import unittest
from redivivus.models import (
    BootMode,
    PartitionInfo,
    PartitionRole,
    WindowsInstallation,
    EfiBootEntry,
    ScanReport,
    RecoveryPlan,
    ActionItem,
    RecoveryResult,
    VerificationCheck,
)


"""
User Story 1 & 6 (US1, US6) - Data Models for Multi-Architecture Storage & Boot Entries
Verify serialization, disk GUID/signature properties, Windows installation compatibility flags,
and EFI boot manager entry detection across generic storage hardware.
"""


class TestModels(unittest.TestCase):
    def test_partition_info_serialization(self):
        """US1-AC1: PartitionInfo serialization preserves 64-bit offsets, disk GUIDs, and roles."""
        from redivivus.models import DiskInfo
        p = PartitionInfo(
            device="/dev/sda1",
            disk="/dev/sda",
            part_num=1,
            fstype="vfat",
            uuid="1234-ABCD",
            part_uuid="1111-2222-3333-4444",
            label="ESP",
            size_bytes=536870912,
            size_human="512M",
            mount_point="/boot/efi",
            is_mounted=True,
            is_esp=True,
            is_boot_flag=True,
            pttype="gpt",
            role=PartitionRole.ESP,
            disk_guid="a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
            disk_signature=0,
            partition_offset=1048576,
        )
        d = p.to_dict()
        self.assertEqual(d["device"], "/dev/sda1")
        self.assertEqual(d["fstype"], "vfat")
        self.assertTrue(d["is_esp"])
        self.assertEqual(d["role"], "ESP")
        self.assertEqual(d["disk_guid"], "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
        self.assertEqual(d["disk_signature"], 0)
        self.assertEqual(d["partition_offset"], 1048576)

        disk = DiskInfo(device="/dev/sda", pttype="gpt", disk_guid="a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")
        self.assertEqual(disk.device, "/dev/sda")
        self.assertEqual(disk.disk_guid, "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d")

    def test_windows_installation_compatibility(self):
        """US1-AC4: Detect UEFI vs Legacy BIOS compatibility based on boot files present."""
        p = PartitionInfo(
            device="/dev/sda2",
            disk="/dev/sda",
            part_num=2,
            fstype="ntfs",
            role=PartitionRole.WINDOWS_OS,
        )
        win = WindowsInstallation(
            partition=p,
            product_name="Windows 11 Pro",
            has_efi_boot_files=True,
            winload_efi_present=True,
            has_bcd_template=True,
        )
        self.assertTrue(win.is_uefi_compatible())
        self.assertFalse(win.is_legacy_compatible())

        win_legacy = WindowsInstallation(
            partition=p,
            product_name="Windows 10",
            has_pcat_boot_files=True,
            winload_exe_present=True,
        )
        self.assertTrue(win_legacy.is_legacy_compatible())

    def test_efi_boot_entry(self):
        """US5-AC1: Identify Windows Boot Manager entries vs Linux boot entries in NVRAM."""
        entry = EfiBootEntry(
            boot_num="0001",
            label="Windows Boot Manager",
            active=True,
            file_path="\\EFI\\Microsoft\\Boot\\bootmgfw.efi",
            disk_device="/dev/sda",
            partition_num=1,
        )
        self.assertTrue(entry.is_windows_manager())
        d = entry.to_dict()
        self.assertTrue(d["is_windows"])

        non_win = EfiBootEntry(
            boot_num="0002",
            label="Linux Bootloader",
            active=True,
            file_path="\\EFI\\linux\\shimx64.efi",
        )
        self.assertFalse(non_win.is_windows_manager())

    def test_recovery_result(self):
        res = RecoveryResult(
            success=True,
            messages=["Bootloader restored successfully."],
            verification_checks=[
                VerificationCheck(
                    name="ESP bootmgfw.efi presence",
                    passed=True,
                    details="File exists and is executable PE32+",
                )
            ],
        )
        d = res.to_dict()
        self.assertTrue(d["success"])
        self.assertEqual(len(d["verification_checks"]), 1)
        self.assertTrue(d["verification_checks"][0]["passed"])


if __name__ == "__main__":
    unittest.main()
