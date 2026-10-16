import tempfile
import unittest
from pathlib import Path

from redivivus.deployer import BootDeployer
from redivivus.models import BootMode


"""
User Story 4 (US4) - Boot Asset Deployment, Backup & Fallback Protection
As a rescue operator restoring Windows bootability,
I want Redivivus to copy verified boot assets from the Windows drive to the ESP/Boot partition,
automatically create timestamped backups of existing Microsoft boot folders,
and safeguard existing non-Windows fallback loaders (/EFI/Boot/bootx64.efi) from being overwritten.
"""


class TestDeployer(unittest.TestCase):
    def setUp(self):
        self.deployer = BootDeployer()

    def _setup_mock_windows(self, root: Path):
        win_dir = root / "Windows"
        boot_dir = win_dir / "Boot"
        efi_dir = boot_dir / "EFI"
        pcat_dir = boot_dir / "PCAT"
        fonts_dir = efi_dir / "Fonts"
        locale_dir = efi_dir / "en-US"

        fonts_dir.mkdir(parents=True)
        locale_dir.mkdir(parents=True)
        pcat_dir.mkdir(parents=True)

        (efi_dir / "bootmgfw.efi").write_bytes(b"PE_BOOTMGFW")
        (efi_dir / "bootmgr.efi").write_bytes(b"PE_BOOTMGR")
        (efi_dir / "memtest.efi").write_bytes(b"PE_MEMTEST")
        (efi_dir / "boot.sdi").write_bytes(b"SDI_DATA")
        (fonts_dir / "wgl4_boot.ttf").write_bytes(b"TTF_DATA")
        (locale_dir / "bootmgfw.efi.mui").write_bytes(b"MUI_DATA")

        (pcat_dir / "bootmgr").write_bytes(b"PCAT_BOOTMGR")
        (pcat_dir / "boot.sdi").write_bytes(b"SDI_DATA")
        (pcat_dir / "memtest.exe").write_bytes(b"EXE_DATA")

    def test_deploy_uefi_boot_assets(self):
        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_esp:
            win_root = Path(tmp_win)
            esp_root = Path(tmp_esp)

            self._setup_mock_windows(win_root)

            success, actions, errors = self.deployer.deploy_uefi_boot_assets(
                win_root=win_root,
                esp_root=esp_root,
                install_fallback=True,
                dry_run=False,
            )

            self.assertTrue(success)
            self.assertEqual(len(errors), 0)
            self.assertGreater(len(actions), 0)

            # Check deployed files
            ms_boot = esp_root / "EFI" / "Microsoft" / "Boot"
            self.assertTrue((ms_boot / "bootmgfw.efi").exists())
            self.assertEqual((ms_boot / "bootmgfw.efi").read_bytes(), b"PE_BOOTMGFW")
            self.assertTrue((ms_boot / "bootmgr.efi").exists())
            self.assertTrue((ms_boot / "memtest.efi").exists())
            self.assertTrue((ms_boot / "boot.sdi").exists())
            self.assertTrue((ms_boot / "Fonts" / "wgl4_boot.ttf").exists())
            self.assertTrue((ms_boot / "en-US" / "bootmgfw.efi.mui").exists())

            # Check fallback
            fallback = esp_root / "EFI" / "Boot" / "bootx64.efi"
            self.assertTrue(fallback.exists())
            self.assertEqual(fallback.read_bytes(), b"PE_BOOTMGFW")

    def test_deploy_uefi_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_esp:
            win_root = Path(tmp_win)
            esp_root = Path(tmp_esp)

            self._setup_mock_windows(win_root)

            success, actions, errors = self.deployer.deploy_uefi_boot_assets(
                win_root=win_root,
                esp_root=esp_root,
                install_fallback=True,
                dry_run=True,
            )

            self.assertTrue(success)
            self.assertEqual(len(errors), 0)
            self.assertGreater(len(actions), 0)

            # In dry-run, no files should have been created in ESP
            ms_boot = esp_root / "EFI" / "Microsoft" / "Boot"
            self.assertFalse(ms_boot.exists())

    def test_backup_existing_boot_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            target = tmppath / "Microsoft"
            target.mkdir()
            (target / "test.txt").write_text("orig")

            backup_path = self.deployer.backup_existing_boot_dir(target)
            self.assertIsNotNone(backup_path)
            self.assertTrue(backup_path.exists())
            self.assertTrue((backup_path / "test.txt").exists())

    def test_deploy_legacy_boot_assets(self):
        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_boot:
            win_root = Path(tmp_win)
            boot_root = Path(tmp_boot)

            self._setup_mock_windows(win_root)

            success, actions, errors = self.deployer.deploy_legacy_boot_assets(
                win_root=win_root,
                boot_root=boot_root,
                dry_run=False,
            )

            self.assertTrue(success)
            self.assertEqual(len(errors), 0)
            self.assertTrue((boot_root / "bootmgr").exists())
            self.assertEqual((boot_root / "bootmgr").read_bytes(), b"PCAT_BOOTMGR")
            self.assertTrue((boot_root / "Boot" / "boot.sdi").exists())


    def test_deploy_uefi_permission_denied(self):
        with tempfile.TemporaryDirectory() as tmp_win:
            win_root = Path(tmp_win)
            self._setup_mock_windows(win_root)
            # Use an unwritable / non-existent restricted directory
            esp_root = Path("/proc/invalid_esp_dir")
            success, actions, errors = self.deployer.deploy_uefi_boot_assets(
                win_root=win_root,
                esp_root=esp_root,
                install_fallback=True,
                dry_run=False,
            )
            self.assertFalse(success)
            self.assertGreater(len(errors), 0)
            self.assertTrue(any("denied" in e.lower() or "failed" in e.lower() or "error" in e.lower() for e in errors))

    def test_preserve_existing_non_windows_fallback(self):
        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_esp:
            win_root = Path(tmp_win)
            esp_root = Path(tmp_esp)
            self._setup_mock_windows(win_root)

            # Pre-create GRUB fallback loader
            fallback = esp_root / "EFI" / "Boot" / "bootx64.efi"
            fallback.parent.mkdir(parents=True)
            grub_bytes = b"MZ\x90\x00" + b"GNU GRUB version 2.06 by Free Software Foundation"
            fallback.write_bytes(grub_bytes)

            success, actions, errors = self.deployer.deploy_uefi_boot_assets(
                win_root=win_root,
                esp_root=esp_root,
                install_fallback=True,
                force_fallback=False,
                dry_run=False,
            )

            self.assertTrue(success)
            self.assertEqual(len(errors), 0)
            # Ensure file was preserved, not overwritten
            self.assertEqual(fallback.read_bytes(), grub_bytes)
            self.assertTrue(any("Preserved existing non-Windows fallback" in a for a in actions))

    def test_force_fallback_overwrites_non_windows(self):
        with tempfile.TemporaryDirectory() as tmp_win, tempfile.TemporaryDirectory() as tmp_esp:
            win_root = Path(tmp_win)
            esp_root = Path(tmp_esp)
            self._setup_mock_windows(win_root)

            # Pre-create shim fallback loader
            fallback = esp_root / "EFI" / "Boot" / "bootx64.efi"
            fallback.parent.mkdir(parents=True)
            shim_bytes = b"MZ\x90\x00" + b"shimx64 loader by Red Hat"
            fallback.write_bytes(shim_bytes)

            success, actions, errors = self.deployer.deploy_uefi_boot_assets(
                win_root=win_root,
                esp_root=esp_root,
                install_fallback=True,
                force_fallback=True,
                dry_run=False,
            )

            self.assertTrue(success)
            self.assertEqual(len(errors), 0)
            # Ensure file was overwritten with Windows bootmgfw
            self.assertEqual(fallback.read_bytes(), b"PE_BOOTMGFW")
            self.assertTrue(any("Overwriting non-Windows fallback" in a for a in actions))


if __name__ == "__main__":
    unittest.main()
