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


class TestModels(unittest.TestCase):
    def test_partition_info_serialization(self):
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
        )
        d = p.to_dict()
        self.assertEqual(d["device"], "/dev/sda1")
        self.assertEqual(d["fstype"], "vfat")
        self.assertTrue(d["is_esp"])
        self.assertEqual(d["role"], "ESP")

    def test_windows_installation_compatibility(self):
        p = PartitionInfo(
            device="/dev/nvme0n1p2",
            disk="/dev/nvme0n1",
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
        entry = EfiBootEntry(
            boot_num="0001",
            label="Windows Boot Manager",
            active=True,
            file_path="\\EFI\\Microsoft\\Boot\\bootmgfw.efi",
            disk_device="/dev/nvme0n1",
            partition_num=1,
        )
        self.assertTrue(entry.is_windows_manager())
        d = entry.to_dict()
        self.assertTrue(d["is_windows"])

        non_win = EfiBootEntry(
            boot_num="0002",
            label="Fedora Linux",
            active=True,
            file_path="\\EFI\\fedora\\shimx64.efi",
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
