"""
BCD (Boot Configuration Data) management engine for Windows bootloader recovery.
Handles copying, generating, and configuring BCD registry hives on Linux.
"""

import os
import shutil
import struct
import subprocess
import uuid
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

from redivivus.models import BootMode, PartitionInfo
from redivivus.templates.bcd_data import get_uefi_bcd_template, get_pcat_bcd_template


class BcdManager:
    """Manages Windows BCD registry hives."""

    BOOTMGR_GUID = "{9dea862c-5cdd-4e70-acc1-f32b344d4795}"
    WINDOWS_LOADER_GUID = "{b012b84d-c47c-4ed5-b722-c0c42163e569}"
    DEFAULT_LOADER_GUID = WINDOWS_LOADER_GUID
    RESUME_LOADER_GUID = "{0c334284-9a41-4de1-99b3-a7e87e8ff07e}"
    MEMTEST_GUID = "{b2721d73-1db4-4c62-bf78-c548a880142d}"
    LEGACY_LOADER_GUID = "{7619dcc9-fafe-11d9-b411-000476eba25f}"

    # Standard BCD Element Hex IDs
    ELEM_APP_DEVICE = "11000001"
    ELEM_APP_PATH = "12000002"
    ELEM_DESCRIPTION = "12000004"
    ELEM_PREFERRED_LOCALE = "12000005"
    ELEM_INHERIT_OBJECTS = "14000006"
    ELEM_OS_DEVICE = "21000001"
    ELEM_SYSTEM_ROOT = "22000002"
    ELEM_DEFAULT_OBJECT = "23000003"
    ELEM_DISPLAY_ORDER = "24000001"
    ELEM_BOOT_SEQUENCE = "24000002"
    ELEM_TIMEOUT = "25000004"
    ELEM_NX_POLICY = "25000020"
    ELEM_PAE_POLICY = "25000021"

    def __init__(self, run_cmd_fn=None):
        self._run_cmd = run_cmd_fn or self._default_run_cmd

    @staticmethod
    def _default_run_cmd(cmd: List[str], input_str: Optional[str] = None) -> Tuple[int, str, str]:
        try:
            env = os.environ.copy()
            env["HOME"] = "/tmp"
            p = subprocess.run(
                cmd,
                input=input_str,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env=env,
            )
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", f"Command not found: {cmd[0]}"
        except Exception as ex:
            return 1, "", str(ex)

    def is_valid_hive(self, bcd_path: Path) -> bool:
        """Verifies if the given file exists and has the MS Windows Registry 'regf' magic header."""
        p = Path(bcd_path)
        if not p.exists() or p.stat().st_size < 4096:
            return False
        try:
            with open(p, "rb") as f:
                header = f.read(4)
                return header == b"regf"
        except (IOError, PermissionError):
            return False

    @staticmethod
    def construct_partition_device_element(
        part_uuid: Optional[str] = None,
        disk_guid: Optional[str] = None,
        partition_offset: int = 0,
        disk_signature: Any = 0,
        is_gpt: bool = True,
        **kwargs,
    ) -> bytes:
        """
        Constructs an 88-byte (0x58 bytes) Windows BCD HardDisk Partition device element.

        For GPT: 88 bytes:
          offset 0x10=0x06 (type, LE uint32)
          offset 0x18=0x48 (size, LE uint32)
          offset 0x20=16 bytes PARTUUID (little endian GUID format)
          offset 0x38=16 bytes Disk GUID (little endian GUID format)
        For MBR: 88 bytes:
          offset 0x10=0x05 (type, LE uint32)
          offset 0x18=0x48 (size, LE uint32)
          offset 0x20=8 bytes partition start offset (LE uint64)
          offset 0x38=4 bytes disk MBR signature (LE uint32)
        """
        # Disambiguate if positional arguments were passed (e.g. disk_signature as arg 2)
        if isinstance(disk_guid, (int, float)) and not disk_signature:
            disk_signature = int(disk_guid)
            disk_guid = None
        if not disk_guid and isinstance(disk_signature, str) and "-" in disk_signature:
            disk_guid = disk_signature
        if not disk_guid and "disk_uuid" in kwargs:
            disk_guid = kwargs["disk_uuid"]

        data = bytearray(88)
        if is_gpt:
            struct.pack_into("<I", data, 0x10, 0x06)
            struct.pack_into("<I", data, 0x18, 0x48)
            if part_uuid:
                try:
                    data[0x20:0x30] = uuid.UUID(str(part_uuid).strip("{}")).bytes_le
                except (ValueError, AttributeError):
                    pass
            if disk_guid:
                try:
                    data[0x38:0x48] = uuid.UUID(str(disk_guid).strip("{}")).bytes_le
                except (ValueError, AttributeError):
                    pass
        else:
            struct.pack_into("<I", data, 0x10, 0x05)
            struct.pack_into("<I", data, 0x18, 0x48)
            struct.pack_into("<Q", data, 0x20, int(partition_offset))
            sig_val = 0
            if isinstance(disk_signature, int):
                sig_val = disk_signature
            elif isinstance(disk_signature, str):
                try:
                    sig_val = int(
                        disk_signature,
                        16 if disk_signature.startswith(("0x", "0X")) or any(c in disk_signature for c in "abcdefABCDEF") else 10
                    )
                except ValueError:
                    sig_val = 0
            struct.pack_into("<I", data, 0x38, sig_val & 0xFFFFFFFF)
        return bytes(data)

    def deploy_bcd(
        self,
        target_path: Path,
        source_template: Optional[Path] = None,
        boot_mode: BootMode = BootMode.UEFI,
        windows_partition: Optional[PartitionInfo] = None,
        esp_partition: Optional[PartitionInfo] = None,
        description: str = "Windows",
        timeout: int = 10,
    ) -> bool:
        """
        Deploys a BCD hive to the target destination (e.g. ESP/EFI/Microsoft/Boot/BCD or /Boot/BCD).
        Prioritizes the Windows disk's BCD-Template, then falls back to bundled canonical templates.
        """
        target_path = Path(target_path)
        source_template = Path(source_template) if source_template else None
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        except (IOError, OSError, PermissionError):
            return False

        copied = False
        if source_template and source_template.exists() and self.is_valid_hive(source_template):
            try:
                shutil.copy2(source_template, target_path)
                copied = True
            except (IOError, OSError, PermissionError):
                copied = False

        if not copied:
            # Use bundled canonical template
            raw_bytes = get_uefi_bcd_template() if boot_mode == BootMode.UEFI else get_pcat_bcd_template()
            try:
                with open(target_path, "wb") as f:
                    f.write(raw_bytes)
            except (IOError, OSError, PermissionError):
                return False

        # Synthesize complete BCD parameters and device elements
        self.configure_bcd(
            target_path=target_path,
            boot_mode=boot_mode,
            windows_partition=windows_partition,
            esp_partition=esp_partition,
            description=description,
            timeout=timeout,
        )

        return self.is_valid_hive(target_path)

    def configure_bcd(
        self,
        target_path: Path,
        boot_mode: BootMode = BootMode.UEFI,
        windows_partition: Optional[PartitionInfo] = None,
        esp_partition: Optional[PartitionInfo] = None,
        description: str = "Windows",
        timeout: int = 10,
    ) -> bool:
        """
        Customizes BCD entries.
        Uses hivexsh to synthesize a complete BCD hive with bootmgr and Windows OS Loader.
        """
        target_path = Path(target_path)
        hivex_bin = shutil.which("hivexsh")
        if hivex_bin:
            return self._configure_with_hivex(
                target_path=target_path,
                boot_mode=boot_mode,
                windows_partition=windows_partition,
                esp_partition=esp_partition,
                description=description,
                timeout=timeout,
            )

        # Fallback: standalone verification / byte check
        return self.is_valid_hive(target_path)

    def _configure_with_hivex(
        self,
        target_path: Path,
        boot_mode: BootMode = BootMode.UEFI,
        windows_partition: Optional[PartitionInfo] = None,
        esp_partition: Optional[PartitionInfo] = None,
        description: str = "Windows",
        timeout: int = 10,
    ) -> bool:
        """Uses hivexsh to synthesize complete BCD registry objects and elements."""
        # 1. Construct ESP device element
        if esp_partition:
            is_gpt = (esp_partition.pttype.lower() != "dos") if esp_partition.pttype else (boot_mode == BootMode.UEFI)
            esp_elem = self.construct_partition_device_element(
                part_uuid=esp_partition.part_uuid,
                disk_guid=esp_partition.disk_guid,
                partition_offset=getattr(esp_partition, "partition_offset", 0),
                disk_signature=getattr(esp_partition, "disk_signature", 0),
                is_gpt=is_gpt,
            )
        elif windows_partition:
            is_gpt = (windows_partition.pttype.lower() != "dos") if windows_partition.pttype else (boot_mode == BootMode.UEFI)
            esp_elem = self.construct_partition_device_element(
                part_uuid=windows_partition.part_uuid,
                disk_guid=windows_partition.disk_guid,
                partition_offset=getattr(windows_partition, "partition_offset", 0),
                disk_signature=getattr(windows_partition, "disk_signature", 0),
                is_gpt=is_gpt,
            )
        else:
            esp_elem = self.construct_partition_device_element(is_gpt=(boot_mode == BootMode.UEFI))

        # 2. Construct Windows OS partition device element
        if windows_partition:
            is_gpt = (windows_partition.pttype.lower() != "dos") if windows_partition.pttype else (boot_mode == BootMode.UEFI)
            win_elem = self.construct_partition_device_element(
                part_uuid=windows_partition.part_uuid,
                disk_guid=windows_partition.disk_guid,
                partition_offset=getattr(windows_partition, "partition_offset", 0),
                disk_signature=getattr(windows_partition, "disk_signature", 0),
                is_gpt=is_gpt,
            )
        elif esp_partition:
            is_gpt = (esp_partition.pttype.lower() != "dos") if esp_partition.pttype else (boot_mode == BootMode.UEFI)
            win_elem = self.construct_partition_device_element(
                part_uuid=esp_partition.part_uuid,
                disk_guid=esp_partition.disk_guid,
                partition_offset=getattr(esp_partition, "partition_offset", 0),
                disk_signature=getattr(esp_partition, "disk_signature", 0),
                is_gpt=is_gpt,
            )
        else:
            win_elem = self.construct_partition_device_element(is_gpt=(boot_mode == BootMode.UEFI))

        # 3. Query existing objects to avoid 'add: File exists'
        ret, stdout, _ = self._run_cmd(["hivexsh", str(target_path)], input_str="cd \\Objects\nls\n")
        existing_objs = {line.strip() for line in stdout.splitlines() if line.strip().startswith("{")}

        # Query existing elements for bootmgr
        existing_bootmgr_elems = set()
        if self.BOOTMGR_GUID in existing_objs:
            ret, stdout, _ = self._run_cmd(
                ["hivexsh", str(target_path)],
                input_str=f"cd \\Objects\\{self.BOOTMGR_GUID}\\Elements\nls\n",
            )
            existing_bootmgr_elems = {line.strip() for line in stdout.splitlines() if line.strip()}

        # Query existing elements for OS loader
        existing_loader_elems = set()
        if self.WINDOWS_LOADER_GUID in existing_objs:
            ret, stdout, _ = self._run_cmd(
                ["hivexsh", str(target_path)],
                input_str=f"cd \\Objects\\{self.WINDOWS_LOADER_GUID}\\Elements\nls\n",
            )
            existing_loader_elems = {line.strip() for line in stdout.splitlines() if line.strip()}

        cmds: List[str] = []

        def set_elem(elem_id: str, val_line: str, existing_set: set):
            if elem_id not in existing_set:
                cmds.append(f"add {elem_id}")
            cmds.append(f"cd {elem_id}")
            cmds.append("setval 1")
            cmds.append("Element")
            cmds.append(val_line)
            cmds.append("cd ..")

        # 4. Configure {9dea862c-5cdd-4e70-acc1-f32b344d4795} (bootmgr)
        cmds.append("cd \\Objects")
        if self.BOOTMGR_GUID not in existing_objs:
            cmds.append(f"add {self.BOOTMGR_GUID}")
            cmds.append(f"cd {self.BOOTMGR_GUID}")
            cmds.append("add Elements")
        else:
            cmds.append(f"cd {self.BOOTMGR_GUID}")
        cmds.append("cd Elements")

        esp_hex = ",".join(f"{b:02x}" for b in esp_elem)
        bootmgr_path = "\\EFI\\Microsoft\\Boot\\bootmgfw.efi" if boot_mode == BootMode.UEFI else "\\bootmgr"
        multi_sz = (self.WINDOWS_LOADER_GUID + "\x00\x00").encode("utf-16le")
        display_order_hex = ",".join(f"{b:02x}" for b in multi_sz)
        timeout_hex = ",".join(f"{b:02x}" for b in bytes([max(0, min(timeout, 255))]))

        set_elem(self.ELEM_APP_DEVICE, f"hex:3:{esp_hex}", existing_bootmgr_elems)
        set_elem(self.ELEM_APP_PATH, f"string:{bootmgr_path}", existing_bootmgr_elems)
        set_elem(self.ELEM_DESCRIPTION, "string:Windows Boot Manager", existing_bootmgr_elems)
        set_elem(self.ELEM_DEFAULT_OBJECT, f"string:{self.WINDOWS_LOADER_GUID}", existing_bootmgr_elems)
        set_elem(self.ELEM_DISPLAY_ORDER, f"hex:7:{display_order_hex}", existing_bootmgr_elems)
        set_elem(self.ELEM_TIMEOUT, f"hex:3:{timeout_hex}", existing_bootmgr_elems)

        # 5. Configure {b012b84d-c47c-4ed5-b722-c0c42163e569} (Windows OS Loader)
        cmds.append("cd \\Objects")
        if self.WINDOWS_LOADER_GUID not in existing_objs:
            cmds.append(f"add {self.WINDOWS_LOADER_GUID}")
            cmds.append(f"cd {self.WINDOWS_LOADER_GUID}")
            cmds.append("add Elements")
        else:
            cmds.append(f"cd {self.WINDOWS_LOADER_GUID}")
        cmds.append("cd Elements")

        win_hex = ",".join(f"{b:02x}" for b in win_elem)
        app_path = "\\windows\\system32\\winload.efi" if boot_mode == BootMode.UEFI else "\\windows\\system32\\winload.exe"

        set_elem(self.ELEM_APP_DEVICE, f"hex:3:{win_hex}", existing_loader_elems)
        set_elem(self.ELEM_APP_PATH, f"string:{app_path}", existing_loader_elems)
        set_elem(self.ELEM_DESCRIPTION, f"string:{description or 'Windows'}", existing_loader_elems)
        set_elem(self.ELEM_OS_DEVICE, f"hex:3:{win_hex}", existing_loader_elems)
        set_elem(self.ELEM_SYSTEM_ROOT, "string:\\windows", existing_loader_elems)
        set_elem(self.ELEM_DEFAULT_OBJECT, f"string:{self.RESUME_LOADER_GUID}", existing_loader_elems)
        set_elem(self.ELEM_NX_POLICY, "hex:3:00", existing_loader_elems)

        cmds.append("commit")
        script = "\n".join(cmds) + "\n"

        ret, stdout, stderr = self._run_cmd(["hivexsh", "-w", str(target_path)], input_str=script)
        return ret == 0

    def inspect_bcd(self, bcd_path: Path) -> Dict[str, Any]:
        """Reads high-level diagnostic metadata from BCD hive."""
        bcd_path = Path(bcd_path)
        valid = self.is_valid_hive(bcd_path)
        size = bcd_path.stat().st_size if bcd_path.exists() else 0
        has_bootmgr = False
        has_default = False

        if valid:
            try:
                with open(bcd_path, "rb") as f:
                    content = f.read()
                    has_bootmgr = (
                        self.BOOTMGR_GUID.encode("utf-16le") in content
                        or self.BOOTMGR_GUID.encode("ascii") in content
                    )
                    has_default = (
                        self.WINDOWS_LOADER_GUID.encode("utf-16le") in content
                        or self.WINDOWS_LOADER_GUID.encode("ascii") in content
                        or self.DEFAULT_LOADER_GUID.encode("utf-16le") in content
                        or self.DEFAULT_LOADER_GUID.encode("ascii") in content
                        or b"winload" in content
                    )
            except Exception:
                pass

        return {
            "path": str(bcd_path),
            "valid_hive": valid,
            "size_bytes": size,
            "has_bootmgr_guid": has_bootmgr,
            "has_default_guid": has_default,
        }
