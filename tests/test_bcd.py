import tempfile
import unittest
from pathlib import Path

from redivivus.bcd import BcdManager
from redivivus.models import BootMode


class TestBcd(unittest.TestCase):
    def setUp(self):
        self.mgr = BcdManager()

    def test_is_valid_hive(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            invalid_file = tmppath / "invalid.dat"
            invalid_file.write_bytes(b"hello world")
            self.assertFalse(self.mgr.is_valid_hive(invalid_file))

            # Nonexistent file
            self.assertFalse(self.mgr.is_valid_hive(tmppath / "missing.dat"))

    def test_deploy_uefi_bcd_from_bundled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            target = tmppath / "EFI" / "Microsoft" / "Boot" / "BCD"

            ok = self.mgr.deploy_bcd(target, boot_mode=BootMode.UEFI)
            self.assertTrue(ok)
            self.assertTrue(target.exists())
            self.assertTrue(self.mgr.is_valid_hive(target))

            info = self.mgr.inspect_bcd(target)
            self.assertTrue(info["valid_hive"])
            self.assertGreater(info["size_bytes"], 8192)

    def test_deploy_pcat_bcd_from_bundled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            target = tmppath / "Boot" / "BCD"

            ok = self.mgr.deploy_bcd(target, boot_mode=BootMode.BIOS)
            self.assertTrue(ok)
            self.assertTrue(target.exists())
            self.assertTrue(self.mgr.is_valid_hive(target))

    def test_deploy_from_source_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            # Create a source template by deploying one first
            src = tmppath / "src_bcd"
            self.mgr.deploy_bcd(src, boot_mode=BootMode.UEFI)

            dest = tmppath / "dest_bcd"
            ok = self.mgr.deploy_bcd(dest, source_template=src, boot_mode=BootMode.UEFI)
            self.assertTrue(ok)
            self.assertTrue(self.mgr.is_valid_hive(dest))


if __name__ == "__main__":
    unittest.main()
