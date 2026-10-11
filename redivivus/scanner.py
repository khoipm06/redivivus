"""
Hardware and disk scanner: discovers block devices, partitions, Windows installations,
EFI System Partitions, and UEFI NVRAM boot entries.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from redivivus.models import (
    BootMode,
    PartitionInfo,
    PartitionRole,
    WindowsInstallation,
    EfiBootEntry,
    ScanReport,
)


class SystemScanner:
    """Discovers partitions, Windows installations, ESPs, and UEFI boot state."""

    ESP_GPT_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"

    def __init__(self, run_cmd_fn=None):
        self._run_cmd = run_cmd_fn or self._default_run_cmd

    @staticmethod
    def _default_run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
        try:
            p = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            return p.returncode, p.stdout, p.stderr
        except FileNotFoundError:
            return 127, "", f"Command not found: {cmd[0]}"
        except Exception as ex:
            return 1, "", str(ex)

    def is_firmware_uefi(self) -> bool:
        """Check if the host Linux environment is booted via UEFI."""
        return os.path.isdir("/sys/firmware/efi")

    def detect_system_boot_mode(self) -> BootMode:
        """Determines the current system firmware boot mode."""
        return BootMode.UEFI if self.is_firmware_uefi() else BootMode.BIOS

    def scan_partitions(self) -> List[PartitionInfo]:
        """Query block devices using lsblk and parse into PartitionInfo models."""
        cmd = [
            "lsblk",
            "-J",
            "-b",
            "-o",
            "NAME,KNAME,PKNAME,PATH,TYPE,FSTYPE,LABEL,UUID,PARTUUID,SIZE,MOUNTPOINT,MOUNTPOINTS,PARTFLAGS,PTTYPE",
        ]
        ret, stdout, stderr = self._run_cmd(cmd)
        if ret != 0 or not stdout.strip():
            # Fallback to standard lsblk line output if JSON fails
            return self._scan_partitions_fallback()

        try:
            data = json.loads(stdout)
            devices = data.get("blockdevices", [])
            return self._parse_lsblk_json(devices)
        except Exception:
            return self._scan_partitions_fallback()

    def _parse_lsblk_json(
        self, devices: List[Dict[str, Any]], parent_disk: Optional[str] = None, parent_pttype: str = "gpt"
    ) -> List[PartitionInfo]:
        results: List[PartitionInfo] = []
        for dev in devices:
            dev_type = dev.get("type", "")
            dev_path = dev.get("path") or f"/dev/{dev.get('name', '')}"
            pttype = dev.get("pttype") or parent_pttype

            # If this is a disk device, recurse into partitions
            if dev_type == "disk":
                disk_path = dev_path
                children = dev.get("children", [])
                if children:
                    results.extend(self._parse_lsblk_json(children, parent_disk=disk_path, parent_pttype=pttype))
                continue

            if dev_type in ("part", "lvm", "crypt"):
                disk = parent_disk or self._infer_parent_disk(dev_path)
                part_num = self._extract_part_num(dev_path)

                fstype = (dev.get("fstype") or "").lower()
                uuid = dev.get("uuid")
                part_uuid = dev.get("partuuid")
                label = dev.get("label") or ""
                size_bytes = int(dev.get("size") or 0)
                size_human = self._format_size(size_bytes)

                # Get mount point
                mountpoints = dev.get("mountpoints") or []
                mount_point = dev.get("mountpoint")
                if not mount_point and mountpoints:
                    mount_point = mountpoints[0]
                is_mounted = bool(mount_point)

                # Check if this is an ESP partition
                partflags = (dev.get("partflags") or "").lower()
                is_esp = self._check_is_esp(fstype, label, partflags, mount_point, part_uuid)
                is_boot_flag = "boot" in partflags or is_esp

                role = PartitionRole.UNKNOWN
                if is_esp:
                    role = PartitionRole.ESP
                elif fstype in ("ntfs", "fuseblk"):
                    role = PartitionRole.WINDOWS_OS if "windows" in label.lower() else PartitionRole.DATA
                elif fstype in ("ext4", "btrfs", "xfs"):
                    role = PartitionRole.LINUX

                p = PartitionInfo(
                    device=dev_path,
                    disk=disk,
                    part_num=part_num,
                    fstype=fstype,
                    uuid=uuid,
                    part_uuid=part_uuid,
                    label=label if label else None,
                    size_bytes=size_bytes,
                    size_human=size_human,
                    mount_point=mount_point,
                    is_mounted=is_mounted,
                    is_esp=is_esp,
                    is_boot_flag=is_boot_flag,
                    pttype=pttype,
                    role=role,
                )
                results.append(p)

                # Recurse children if any (e.g. luks/lvm on top of partition)
                children = dev.get("children", [])
                if children:
                    results.extend(self._parse_lsblk_json(children, parent_disk=disk, parent_pttype=pttype))

        return results

    def _scan_partitions_fallback(self) -> List[PartitionInfo]:
        """Simple fallback parser using blkid when lsblk -J is unavailable."""
        ret, stdout, _ = self._run_cmd(["blkid", "-o", "export"])
        if ret != 0 or not stdout:
            return []

        results: List[PartitionInfo] = []
        current: Dict[str, str] = {}
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                if "DEVNAME" in current:
                    dev_path = current["DEVNAME"]
                    fstype = current.get("TYPE", "").lower()
                    uuid = current.get("UUID")
                    part_uuid = current.get("PARTUUID")
                    label = current.get("LABEL")
                    part_num = self._extract_part_num(dev_path)
                    disk = self._infer_parent_disk(dev_path)
                    is_esp = self._check_is_esp(fstype, label or "", "", None, part_uuid)
                    role = PartitionRole.ESP if is_esp else (
                        PartitionRole.WINDOWS_OS if fstype in ("ntfs", "fuseblk") else PartitionRole.UNKNOWN
                    )
                    results.append(
                        PartitionInfo(
                            device=dev_path,
                            disk=disk,
                            part_num=part_num,
                            fstype=fstype,
                            uuid=uuid,
                            part_uuid=part_uuid,
                            label=label,
                            is_esp=is_esp,
                            role=role,
                        )
                    )
                current = {}
                continue

            if "=" in line:
                k, v = line.split("=", 1)
                current[k] = v

        if "DEVNAME" in current:
            dev_path = current["DEVNAME"]
            fstype = current.get("TYPE", "").lower()
            is_esp = self._check_is_esp(fstype, current.get("LABEL") or "", "", None, current.get("PARTUUID"))
            results.append(
                PartitionInfo(
                    device=dev_path,
                    disk=self._infer_parent_disk(dev_path),
                    part_num=self._extract_part_num(dev_path),
                    fstype=fstype,
                    uuid=current.get("UUID"),
                    part_uuid=current.get("PARTUUID"),
                    label=current.get("LABEL"),
                    is_esp=is_esp,
                    role=PartitionRole.ESP if is_esp else PartitionRole.UNKNOWN,
                )
            )

        return results

    @staticmethod
    def _check_is_esp(
        fstype: str, label: str, partflags: str, mount_point: Optional[str], part_uuid: Optional[str]
    ) -> bool:
        if fstype not in ("vfat", "fat32", "fat16"):
            return False
        label_lower = label.lower()
        if "esp" in partflags or "boot" in partflags:
            return True
        if "esp" in label_lower or "efi" in label_lower:
            return True
        if mount_point and mount_point in ("/boot/efi", "/efi", "/boot/EFI"):
            return True
        return False

    @staticmethod
    def _extract_part_num(dev_path: str) -> int:
        m = re.search(r"(?:p|part)?(\d+)$", dev_path)
        return int(m.group(1)) if m else 1

    @staticmethod
    def _infer_parent_disk(dev_path: str) -> str:
        # e.g. /dev/nvme0n1p2 -> /dev/nvme0n1, /dev/sda1 -> /dev/sda
        m = re.match(r"^(/dev/nvme\d+n\d+|/dev/sd[a-z]+|/dev/vd[a-z]+|/dev/xvd[a-z]+|/dev/mmcblk\d+)", dev_path)
        if m:
            return m.group(1)
        return dev_path

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        if size_bytes <= 0:
            return "0B"
        for unit in ["B", "K", "M", "G", "T"]:
            if size_bytes < 1024.0 or unit == "T":
                return f"{size_bytes:.1f}{unit}" if unit != "B" else f"{size_bytes}B"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f}T"

    def scan_windows_installations(
        self, partitions: List[PartitionInfo], probe_unmounted: bool = True
    ) -> List[WindowsInstallation]:
        """
        Inspect partitions for Windows installations.
        Checks mounted partitions and optionally probes unmounted NTFS partitions.
        """
        installations: List[WindowsInstallation] = []
        for p in partitions:
            if p.fstype not in ("ntfs", "fuseblk"):
                continue

            root_path = None
            temp_mounted = False

            if p.is_mounted and p.mount_point:
                root_path = Path(p.mount_point)
            elif probe_unmounted:
                # If unmounted and we have permissions to mount ro
                temp_dir = self._mount_ro_temp(p.device)
                if temp_dir:
                    root_path = Path(temp_dir)
                    temp_mounted = True

            if not root_path or not root_path.exists():
                continue

            try:
                win_inst = self._inspect_windows_root(p, root_path)
                if win_inst:
                    p.role = PartitionRole.WINDOWS_OS
                    installations.append(win_inst)
            finally:
                if temp_mounted and root_path:
                    self._umount_temp(str(root_path))

        return installations

    def _inspect_windows_root(self, part: PartitionInfo, root_dir: Path) -> Optional[WindowsInstallation]:
        """Case-insensitively inspects a mounted filesystem root for Windows system files."""
        # Find Windows directory
        win_dir = self._find_dir_case_insensitive(root_dir, "Windows")
        if not win_dir:
            return None

        # Check for ntoskrnl.exe
        sys32_dir = self._find_dir_case_insensitive(win_dir, "System32")
        if not sys32_dir:
            return None

        ntoskrnl = self._find_file_case_insensitive(sys32_dir, "ntoskrnl.exe")
        if not ntoskrnl:
            return None

        # Check for EFI boot assets in Windows\Boot\EFI
        boot_dir = self._find_dir_case_insensitive(win_dir, "Boot")
        has_efi_boot_files = False
        has_pcat_boot_files = False
        if boot_dir:
            boot_efi_dir = self._find_dir_case_insensitive(boot_dir, "EFI")
            if boot_efi_dir:
                bootmgfw = self._find_file_case_insensitive(boot_efi_dir, "bootmgfw.efi")
                has_efi_boot_files = bool(bootmgfw)

            boot_pcat_dir = self._find_dir_case_insensitive(boot_dir, "PCAT")
            if boot_pcat_dir:
                pcat_bootmgr = self._find_file_case_insensitive(boot_pcat_dir, "bootmgr")
                has_pcat_boot_files = bool(pcat_bootmgr)

        # Check for BCD-Template in System32\config\BCD-Template
        config_dir = self._find_dir_case_insensitive(sys32_dir, "config")
        has_bcd_template = False
        if config_dir:
            bcd_template = self._find_file_case_insensitive(config_dir, "BCD-Template")
            has_bcd_template = bool(bcd_template)

        # Check winload loaders
        winload_efi = self._find_file_case_insensitive(sys32_dir, "winload.efi")
        winload_exe = self._find_file_case_insensitive(sys32_dir, "winload.exe")

        # Extract product name / build if possible
        product_name, version_build = self._detect_windows_version(win_dir, sys32_dir)

        rel_win_dir = "/" + str(win_dir.relative_to(root_dir)).replace("\\", "/")
        rel_sys32_dir = "/" + str(sys32_dir.relative_to(root_dir)).replace("\\", "/")

        return WindowsInstallation(
            partition=part,
            windows_dir=rel_win_dir,
            system32_dir=rel_sys32_dir,
            product_name=product_name,
            version_build=version_build,
            has_efi_boot_files=has_efi_boot_files,
            has_pcat_boot_files=has_pcat_boot_files,
            has_bcd_template=has_bcd_template,
            winload_efi_present=bool(winload_efi),
            winload_exe_present=bool(winload_exe),
        )

    @staticmethod
    def _find_dir_case_insensitive(parent: Path, name: str) -> Optional[Path]:
        name_lower = name.lower()
        try:
            for item in parent.iterdir():
                if item.is_dir() and item.name.lower() == name_lower:
                    return item
        except (PermissionError, OSError):
            pass
        return None

    @staticmethod
    def _find_file_case_insensitive(parent: Path, name: str) -> Optional[Path]:
        name_lower = name.lower()
        try:
            for item in parent.iterdir():
                if item.is_file() and item.name.lower() == name_lower:
                    return item
        except (PermissionError, OSError):
            pass
        return None

    @staticmethod
    def _detect_windows_version(win_dir: Path, sys32_dir: Path) -> Tuple[Optional[str], Optional[str]]:
        # Heuristic 1: check build number from ntoskrnl or license file
        license_rtf = SystemScanner._find_file_case_insensitive(win_dir, "license.rtf")
        if not license_rtf:
            license_rtf = SystemScanner._find_file_case_insensitive(sys32_dir, "license.rtf")

        product_name = "Windows (NT)"
        version_build = None

        if license_rtf:
            try:
                content = license_rtf.read_text(errors="ignore")[:2000]
                if "Windows 11" in content:
                    product_name = "Windows 11"
                elif "Windows 10" in content:
                    product_name = "Windows 10"
                elif "Windows Server" in content:
                    product_name = "Windows Server"
            except Exception:
                pass

        return product_name, version_build

    def _mount_ro_temp(self, device: str) -> Optional[str]:
        temp_dir = f"/tmp/redivivus_probe_{os.getpid()}_{Path(device).name}"
        os.makedirs(temp_dir, exist_ok=True)
        ret, _, _ = self._run_cmd(["mount", "-o", "ro", device, temp_dir])
        if ret == 0:
            return temp_dir
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass
        return None

    def _umount_temp(self, mount_dir: str):
        self._run_cmd(["umount", mount_dir])
        try:
            os.rmdir(mount_dir)
        except OSError:
            pass

    def scan_nvram_entries(self) -> Tuple[List[EfiBootEntry], List[str]]:
        """Parses efibootmgr output to return boot entries and current boot order."""
        ret, stdout, _ = self._run_cmd(["efibootmgr", "-v"])
        if ret != 0 or not stdout:
            return [], []

        return self.parse_efibootmgr_output(stdout)

    @staticmethod
    def parse_efibootmgr_output(output: str) -> Tuple[List[EfiBootEntry], List[str]]:
        entries: List[EfiBootEntry] = []
        boot_order: List[str] = []

        header_pattern = re.compile(r"^Boot([0-9a-fA-F]{4})(\*?)\s+(.*)$")

        for line in output.splitlines():
            line = line.strip()
            if line.startswith("BootOrder:"):
                order_str = line.split(":", 1)[1].strip()
                boot_order = [x.strip() for x in order_str.split(",") if x.strip()]
                continue

            m = header_pattern.match(line)
            if m:
                boot_num = m.group(1)
                active = m.group(2) == "*"
                rest = m.group(3).strip()

                label = rest
                details = ""

                # efibootmgr typically separates label and device path with a tab (\t)
                if "\t" in rest:
                    label, details = rest.split("\t", 1)
                else:
                    # Otherwise split on device path prefix
                    dev_split = re.split(r"\s+(?=(?:HD|File|VenHw|FvFile|Pci|BBS|USB|VenMsg)\()", rest, maxsplit=1)
                    if len(dev_split) == 2:
                        label, details = dev_split

                label = label.strip()
                details = details.strip()

                # Try to extract file path, e.g. File(\EFI\Microsoft\Boot\bootmgfw.efi) or /\EFI\Microsoft\Boot\bootmgfw.efi
                file_match = re.search(r"File\(([^)]+)\)", details)
                if file_match:
                    file_path = file_match.group(1)
                else:
                    raw_match = re.search(r"[/\\\\]+EFI[/\\\\][^\s()]*?\.(?:efi|EFI)", details)
                    file_path = raw_match.group(0).lstrip("/") if raw_match else None

                # Extract HD partition number e.g. HD(1,GPT,...) or HD(2,MBR,...)
                hd_match = re.search(r"HD\((\d+),", details)
                part_num = int(hd_match.group(1)) if hd_match else None

                entries.append(
                    EfiBootEntry(
                        boot_num=boot_num,
                        label=label,
                        active=active,
                        file_path=file_path,
                        partition_num=part_num,
                    )
                )

        return entries, boot_order

    def run_full_scan(self, probe_unmounted: bool = True) -> ScanReport:
        """Executes a full system scan and returns a complete ScanReport."""
        uefi_firmware = self.is_firmware_uefi()
        boot_mode = self.detect_system_boot_mode()

        partitions = self.scan_partitions()
        windows_installations = self.scan_windows_installations(partitions, probe_unmounted=probe_unmounted)
        esp_partitions = [p for p in partitions if p.is_esp]

        nvram_entries: List[EfiBootEntry] = []
        boot_order: List[str] = []
        if uefi_firmware:
            nvram_entries, boot_order = self.scan_nvram_entries()

        return ScanReport(
            firmware_uefi=uefi_firmware,
            system_boot_mode=boot_mode,
            partitions=partitions,
            windows_installations=windows_installations,
            esp_partitions=esp_partitions,
            nvram_entries=nvram_entries,
            current_boot_order=boot_order,
        )
