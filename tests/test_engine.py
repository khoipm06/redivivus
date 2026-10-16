import tempfile
import unittest
from pathlib import Path

from redivivus.bcd import BcdManager
from redivivus.deployer import BootDeployer
from redivivus.engine import RecoveryEngine
from redivivus.models import BootMode, PartitionInfo, PartitionRole, WindowsInstallation
from redivivus.nvram import NvramManager


"""
User Story 6 (US6) - End-to-End Recovery Orchestration & Safety Verification
As an administrator recovering Windows bootability,
I want the recovery engine to plan, verify, and orchestrate bootloader restoration,
respecting dry-run simulation mode, dual-boot boot priorities, and permission boundaries.
"""


class TestEngine(unittest.TestCase):
    def setUp(self):
        # Mock NVRAM manager to prevent running actual efibootmgr
        def mock_cmd(cmd):
            return 0, "BootOrder: 0000\nBoot0000* Windows Boot Manager\n", ""

        self.nvram = NvramManager(run_cmd_fn=mock_cmd)
        self.engine = RecoveryEngine(nvram_manager=self.nvram)

    def _setup_mock_windows(self, root: Path):
        win_dir = root / "Windows"
        sys32_dir = win_dir / "System32"
        config_dir = sys32_dir / "config"
        boot_dir = win_dir / "Boot"
        efi_dir = boot_dir / "EFI"
        pcat_dir = boot_dir / "PCAT"

        efi_dir.mkdir(parents=True)
        pcat_dir.mkdir(parents=True)
        config_dir.mkdir(parents=True)

        (efi_dir / "bootmgfw.efi").write_bytes(b"PE_BOOTMGFW_DATA")
        (efi_dir / "bootmgr.efi").write_bytes(b"PE_BOOTMGR_DATA")
        (efi_dir / "boot.sdi").write_bytes(b"SDI_DATA")
        (pcat_dir / "bootmgr").write_bytes(b"PCAT_BOOTMGR")
        (pcat_dir / "boot.sdi").write_bytes(b"SDI_DATA")

    def test_build_plan_uefi(self):
        """US6-AC1: Build structured execution plan for UEFI recovery with verified actions."""
        win_part = PartitionInfo(
            device="/dev/sda2",
            disk="/dev/sda",
            part_num=2,
            fstype="ntfs",
            role=PartitionRole.WINDOWS_OS,
        )
        esp_part = PartitionInfo(
            device="/dev/sda1",
            disk="/dev/sda",
            part_num=1,
            fstype="vfat",
            role=PartitionRole.ESP,
        )
        win = WindowsInstallation(partition=win_part, product_name="Windows 11")

        plan = self.engine.build_plan(
            windows=win,
            esp=esp_part,
            boot_mode=BootMode.UEFI,
            install_fallback=True,
            update_nvram=True,
            backup_esp=True,
        )

        self.assertEqual(plan.boot_mode, BootMode.UEFI)
        self.assertTrue(plan.backup_esp)
        self.assertTrue(plan.install_fallback_bootx64)
        self.assertTrue(plan.update_nvram)
        self.assertGreater(len(plan.actions), 4)

    def test_execute_recovery_uefi_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_esp:
            win_root = Path(tmp_win)
            esp_root = Path(tmp_esp)

            self._setup_mock_windows(win_root)

            win_part = PartitionInfo(
                device="/dev/sda2",
                disk="/dev/sda",
                part_num=2,
                fstype="ntfs",
                role=PartitionRole.WINDOWS_OS,
            )
            esp_part = PartitionInfo(
                device="/dev/sda1",
                disk="/dev/sda",
                part_num=1,
                fstype="vfat",
                mount_point=str(esp_root),
                is_mounted=True,
                role=PartitionRole.ESP,
            )
            win = WindowsInstallation(partition=win_part, product_name="Windows 11")

            plan = self.engine.build_plan(
                windows=win,
                esp=esp_part,
                boot_mode=BootMode.UEFI,
                install_fallback=True,
                update_nvram=True,
                backup_esp=True,
            )

            result = self.engine.execute_recovery(plan, win_root=win_root, esp_root=esp_root)

            self.assertTrue(result.success)
            self.assertEqual(len(result.errors), 0)
            self.assertGreater(len(result.verification_checks), 0)
            self.assertTrue(all(c.passed for c in result.verification_checks if c.severity == "ERROR"))

            # Verify physical files on ESP
            bootmgfw = esp_root / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"
            bcd = esp_root / "EFI" / "Microsoft" / "Boot" / "BCD"
            fallback = esp_root / "EFI" / "Boot" / "bootx64.efi"

            self.assertTrue(bootmgfw.exists())
            self.assertTrue(bcd.exists())
            self.assertTrue(fallback.exists())

    def test_execute_recovery_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_esp:
            win_root = Path(tmp_win)
            esp_root = Path(tmp_esp)

            self._setup_mock_windows(win_root)

            win_part = PartitionInfo(
                device="/dev/sda2",
                disk="/dev/sda",
                part_num=2,
                fstype="ntfs",
            )
            esp_part = PartitionInfo(
                device="/dev/sda1",
                disk="/dev/sda",
                part_num=1,
                fstype="vfat",
            )
            win = WindowsInstallation(partition=win_part)

            plan = self.engine.build_plan(
                windows=win,
                esp=esp_part,
                boot_mode=BootMode.UEFI,
                dry_run=True,
            )

            result = self.engine.execute_recovery(plan, win_root=win_root, esp_root=esp_root)
            self.assertTrue(result.success)
            self.assertTrue(any("Dry-run" in m for m in result.messages))
            # No files created
            self.assertFalse((esp_root / "EFI").exists())

    def test_execute_recovery_permission_denied_handling(self):
        with tempfile.TemporaryDirectory() as tmp_win:
            win_root = Path(tmp_win)
            self._setup_mock_windows(win_root)

            win_part = PartitionInfo(
                device="/dev/sda2",
                disk="/dev/sda",
                part_num=2,
                fstype="ntfs",
            )
            esp_part = PartitionInfo(
                device="/dev/sda1",
                disk="/dev/sda",
                part_num=1,
                fstype="vfat",
            )
            win = WindowsInstallation(partition=win_part)

            plan = self.engine.build_plan(
                windows=win,
                esp=esp_part,
                boot_mode=BootMode.UEFI,
                dry_run=False,
            )

            # Point esp_root to a non-existent unwritable directory
            esp_root = Path("/proc/protected_esp")
            result = self.engine.execute_recovery(plan, win_root=win_root, esp_root=esp_root)
            self.assertFalse(result.success)
            self.assertGreater(len(result.errors), 0)

    def test_build_plan_set_first_default_false(self):
        """US5-AC2: Default recovery plan preserves dual-boot order with set_first=False."""
        win_part = PartitionInfo(
            device="/dev/sda2",
            disk="/dev/sda",
            part_num=2,
            fstype="ntfs",
        )
        esp_part = PartitionInfo(
            device="/dev/sda1",
            disk="/dev/sda",
            part_num=1,
            fstype="vfat",
        )
        win = WindowsInstallation(partition=win_part)

        plan = self.engine.build_plan(
            windows=win,
            esp=esp_part,
            boot_mode=BootMode.UEFI,
        )

        self.assertFalse(plan.set_first)
        self.assertFalse(plan.force_fallback)
        nvram_actions = [a for a in plan.actions if a.category == "NVRAM"]
        self.assertEqual(len(nvram_actions), 1)
        self.assertIn("preserving existing boot order", nvram_actions[0].description)

    def test_build_plan_set_first_true_and_force_fallback(self):
        """US5-AC2 & US4-AC3: Explicit flags override dual-boot safety defaults."""
        win_part = PartitionInfo(
            device="/dev/sda2",
            disk="/dev/sda",
            part_num=2,
            fstype="ntfs",
        )
        esp_part = PartitionInfo(
            device="/dev/sda1",
            disk="/dev/sda",
            part_num=1,
            fstype="vfat",
        )
        win = WindowsInstallation(partition=win_part)

        plan = self.engine.build_plan(
            windows=win,
            esp=esp_part,
            boot_mode=BootMode.UEFI,
            set_first=True,
            force_fallback=True,
        )

        self.assertTrue(plan.set_first)
        self.assertTrue(plan.force_fallback)
        nvram_actions = [a for a in plan.actions if a.category == "NVRAM"]
        self.assertIn("prioritize", nvram_actions[0].description)
        fb_actions = [a for a in plan.actions if a.category == "Fallback"]
        self.assertIn("force overwrite", fb_actions[0].description)

    def test_execute_recovery_passes_set_first_to_nvram(self):
        recorded_calls = []

        def mock_create(esp_partition, label=None, loader_path=None, set_first=False, dry_run=False):
            recorded_calls.append({"set_first": set_first})
            return True, ["NVRAM updated"], []

        self.nvram.create_or_update_windows_entry = mock_create

        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_esp:
            win_root = Path(tmp_win)
            esp_root = Path(tmp_esp)
            self._setup_mock_windows(win_root)

            win_part = PartitionInfo(device="/dev/sda2", disk="/dev/sda", part_num=2, fstype="ntfs")
            esp_part = PartitionInfo(device="/dev/sda1", disk="/dev/sda", part_num=1, fstype="vfat")
            win = WindowsInstallation(partition=win_part)

            plan = self.engine.build_plan(
                windows=win,
                esp=esp_part,
                boot_mode=BootMode.UEFI,
                set_first=False,
            )

            result = self.engine.execute_recovery(plan, win_root=win_root, esp_root=esp_root)
            self.assertTrue(result.success)
            self.assertEqual(len(recorded_calls), 1)
            self.assertFalse(recorded_calls[0]["set_first"])


    def test_execute_recovery_case_insensitive_template_lookup(self):
        """Verify BCD-Template is found case-insensitively even on lowercase directory trees."""
        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_esp:
            win_root = Path(tmp_win)
            esp_root = Path(tmp_esp)

            # Lowercase directory layout
            cfg_dir = win_root / "windows" / "system32" / "config"
            efi_dir = win_root / "windows" / "boot" / "efi"
            cfg_dir.mkdir(parents=True)
            efi_dir.mkdir(parents=True)

            (efi_dir / "bootmgfw.efi").write_bytes(b"PE_BOOTMGFW")
            from redivivus.templates.bcd_data import get_uefi_bcd_template
            (cfg_dir / "bcd-template").write_bytes(get_uefi_bcd_template())

            win_part = PartitionInfo(
                device="/dev/sda2",
                disk="/dev/sda",
                part_num=2,
                fstype="ntfs",
                part_uuid="22222222-2222-2222-2222-222222222222",
                disk_guid="33333333-3333-3333-3333-333333333333",
                pttype="gpt",
            )
            esp_part = PartitionInfo(
                device="/dev/sda1",
                disk="/dev/sda",
                part_num=1,
                fstype="vfat",
                part_uuid="11111111-1111-1111-1111-111111111111",
                disk_guid="33333333-3333-3333-3333-333333333333",
                pttype="gpt",
            )
            win = WindowsInstallation(
                partition=win_part,
                windows_dir="/windows",
                system32_dir="/windows/system32",
            )

            plan = self.engine.build_plan(
                windows=win,
                esp=esp_part,
                boot_mode=BootMode.UEFI,
                install_fallback=False,
                update_nvram=False,
            )

            result = self.engine.execute_recovery(plan, win_root=win_root, esp_root=esp_root)
            self.assertTrue(result.success)
            self.assertEqual(len(result.errors), 0)
            target_bcd = esp_root / "EFI" / "Microsoft" / "Boot" / "BCD"
            self.assertTrue(target_bcd.exists())
            self.assertTrue(self.engine.bcd.is_valid_hive(target_bcd))


if __name__ == "__main__":
    unittest.main()
