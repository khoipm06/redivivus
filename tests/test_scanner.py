import json
import tempfile
import unittest
from pathlib import Path

from redivivus.models import PartitionRole
from redivivus.scanner import SystemScanner


SAMPLE_LSBLK_JSON = {
    "blockdevices": [
        {
            "name": "nvme0n1",
            "type": "disk",
            "pttype": "gpt",
            "size": "1000204886016",
            "children": [
                {
                    "name": "nvme0n1p1",
                    "path": "/dev/nvme0n1p1",
                    "type": "part",
                    "fstype": "vfat",
                    "label": "ESP",
                    "uuid": "A1B2-C3D4",
                    "partuuid": "c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                    "size": "288358400",
                    "mountpoint": "/boot/efi",
                    "partflags": "boot,esp",
                },
                {
                    "name": "nvme0n1p2",
                    "path": "/dev/nvme0n1p2",
                    "type": "part",
                    "fstype": "ntfs",
                    "label": "Windows 11",
                    "uuid": "4E1A2B3C4D5E6F70",
                    "partuuid": "550e8400-e29b-41d4-a716-446655440000",
                    "size": "400000000000",
                    "mountpoint": "/media/windows",
                    "partflags": "",
                },
                {
                    "name": "nvme0n1p3",
                    "path": "/dev/nvme0n1p3",
                    "type": "part",
                    "fstype": "ext4",
                    "label": "LinuxRoot",
                    "uuid": "fedcba98-7654-3210-fedc-ba9876543210",
                    "partuuid": "660e8400-e29b-41d4-a716-446655440000",
                    "size": "200000000000",
                    "mountpoint": "/",
                    "partflags": "",
                },
            ],
        }
    ]
}

SAMPLE_EFIBOOTMGR = """
BootCurrent: 0002
Timeout: 0 seconds
BootOrder: 0002,0001,0000
Boot0000* Windows Boot Manager	HD(1,GPT,c12a7328-f81f-11d2-ba4b-00a0c93ec93b,0x800,0x89000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)WINDOWS.........x...B.C.D.O.B.J.E.C.T...
Boot0001  Linux Boot Manager	HD(1,GPT,c12a7328-f81f-11d2-ba4b-00a0c93ec93b,0x800,0x89000)/File(\\EFI\\systemd\\systemd-bootx64.efi)
Boot0002* Fedora	HD(1,GPT,c12a7328-f81f-11d2-ba4b-00a0c93ec93b,0x800,0x89000)/File(\\EFI\\fedora\\shimx64.efi)
"""


class TestScanner(unittest.TestCase):
    def test_parse_lsblk_json(self):
        scanner = SystemScanner()
        devices = SAMPLE_LSBLK_JSON["blockdevices"]
        partitions = scanner._parse_lsblk_json(devices)

        self.assertEqual(len(partitions), 3)

        esp = partitions[0]
        self.assertEqual(esp.device, "/dev/nvme0n1p1")
        self.assertEqual(esp.disk, "/dev/nvme0n1")
        self.assertEqual(esp.part_num, 1)
        self.assertTrue(esp.is_esp)
        self.assertEqual(esp.role, PartitionRole.ESP)
        self.assertEqual(esp.mount_point, "/boot/efi")

        win = partitions[1]
        self.assertEqual(win.device, "/dev/nvme0n1p2")
        self.assertEqual(win.fstype, "ntfs")
        self.assertEqual(win.role, PartitionRole.WINDOWS_OS)

        linux = partitions[2]
        self.assertEqual(linux.device, "/dev/nvme0n1p3")
        self.assertEqual(linux.role, PartitionRole.LINUX)

    def test_parse_efibootmgr_output(self):
        entries, order = SystemScanner.parse_efibootmgr_output(SAMPLE_EFIBOOTMGR)

        self.assertEqual(order, ["0002", "0001", "0000"])
        self.assertEqual(len(entries), 3)

        win_entry = entries[0]
        self.assertEqual(win_entry.boot_num, "0000")
        self.assertEqual(win_entry.label, "Windows Boot Manager")
        self.assertTrue(win_entry.active)
        self.assertEqual(win_entry.file_path, "\\EFI\\Microsoft\\Boot\\bootmgfw.efi")
        self.assertEqual(win_entry.partition_num, 1)
        self.assertTrue(win_entry.is_windows_manager())

        linux_entry = entries[1]
        self.assertFalse(linux_entry.active)

    def test_parse_efibootmgr_raw_efi_path(self):
        sample_output = (
            "BootCurrent: 0002\n"
            "BootOrder: 0000,0005\n"
            r"Boot0000* Fedora	HD(1,GPT,0000003f-3190-8b95-203c-db017e000000,0x800,0x89800)/\EFI\fedora\shimx64.efi" + "\n"
            r"Boot0005* Windows Boot Manager	HD(1,GPT,0000003f-3190-8b95-203c-db017e000000,0x800,0x89800)/\EFI\Microsoft\Boot\bootmgfw.efi57494e444f575300"
        )
        entries, order = SystemScanner.parse_efibootmgr_output(sample_output)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].file_path, r"\EFI\fedora\shimx64.efi")
        self.assertEqual(entries[1].file_path, r"\EFI\Microsoft\Boot\bootmgfw.efi")
        self.assertTrue(entries[1].is_windows_manager())

    def test_inspect_windows_root(self):
        scanner = SystemScanner()
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create simulated Windows directory tree
            win_dir = tmppath / "Windows"
            sys32_dir = win_dir / "System32"
            config_dir = sys32_dir / "config"
            boot_dir = win_dir / "Boot"
            boot_efi_dir = boot_dir / "EFI"
            boot_pcat_dir = boot_dir / "PCAT"

            boot_efi_dir.mkdir(parents=True)
            boot_pcat_dir.mkdir(parents=True)
            config_dir.mkdir(parents=True)

            (sys32_dir / "ntoskrnl.exe").write_bytes(b"MZ\x90\x00")
            (sys32_dir / "winload.efi").write_bytes(b"MZ\x90\x00")
            (sys32_dir / "winload.exe").write_bytes(b"MZ\x90\x00")
            (boot_efi_dir / "bootmgfw.efi").write_bytes(b"MZ\x90\x00")
            (boot_pcat_dir / "bootmgr").write_bytes(b"\xeb\x58\x90")
            (config_dir / "BCD-Template").write_bytes(b"regf")
            (win_dir / "license.rtf").write_text("{\\rtf1 Windows 11 Pro License Agreement}")

            part = scanner._parse_lsblk_json(SAMPLE_LSBLK_JSON["blockdevices"])[1]
            win_inst = scanner._inspect_windows_root(part, tmppath)

            self.assertIsNotNone(win_inst)
            self.assertEqual(win_inst.product_name, "Windows 11")
            self.assertTrue(win_inst.has_efi_boot_files)
            self.assertTrue(win_inst.has_pcat_boot_files)
            self.assertTrue(win_inst.has_bcd_template)
            self.assertTrue(win_inst.winload_efi_present)
            self.assertTrue(win_inst.winload_exe_present)
            self.assertTrue(win_inst.is_uefi_compatible())
            self.assertTrue(win_inst.is_legacy_compatible())


if __name__ == "__main__":
    unittest.main()
