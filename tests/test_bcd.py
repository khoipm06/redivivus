import base64
import shutil
import struct
import subprocess
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from redivivus.bcd import BcdManager
from redivivus.models import BootMode, PartitionInfo


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

            # Short file (< 4096 bytes)
            short_file = tmppath / "short.dat"
            short_file.write_bytes(b"regf" + b"\x00" * 100)
            self.assertFalse(self.mgr.is_valid_hive(short_file))

    def test_construct_partition_device_element_gpt(self):
        part_uuid_str = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
        disk_guid_str = "be6bface-727b-46b3-b29f-8e5e2f65f90e"
        elem = self.mgr.construct_partition_device_element(
            part_uuid=part_uuid_str,
            disk_guid=disk_guid_str,
            is_gpt=True,
        )
        self.assertEqual(len(elem), 88)
        # Type flags at 0x10 (0x06: Qualified Partition Device)
        dev_type = struct.unpack_from("<I", elem, 0x10)[0]
        self.assertEqual(dev_type, 6)
        # Payload size at 0x18 (0x48 = 72 bytes)
        size = struct.unpack_from("<I", elem, 0x18)[0]
        self.assertEqual(size, 0x48)

        # Verify Partition GUID endianness (Windows mixed-endian: time_low, mid, hi reversed)
        expected_part_le = uuid.UUID(part_uuid_str).bytes_le
        self.assertEqual(elem[0x20:0x30], expected_part_le)
        self.assertEqual(elem[0x20:0x24], bytes.fromhex("28732ac1"))
        self.assertEqual(elem[0x24:0x26], bytes.fromhex("1ff8"))
        self.assertEqual(elem[0x26:0x28], bytes.fromhex("d211"))
        self.assertEqual(elem[0x28:0x30], bytes.fromhex("ba4b00a0c93ec93b"))

        # Verify Disk GUID endianness
        expected_disk_le = uuid.UUID(disk_guid_str).bytes_le
        self.assertEqual(elem[0x38:0x48], expected_disk_le)
        self.assertEqual(elem[0x38:0x3c], bytes.fromhex("cefa6bbe"))

        # Verify unused regions are zeroed
        self.assertEqual(elem[0x00:0x10], bytes(16))
        self.assertEqual(elem[0x30:0x38], bytes(8))
        self.assertEqual(elem[0x48:0x58], bytes(16))

        # Verify positional and kwargs compatibility
        elem_pos = self.mgr.construct_partition_device_element(
            part_uuid_str,
            disk_guid_str,
            0,
            0,
            True,
        )
        self.assertEqual(elem_pos, elem)

    def test_construct_partition_device_element_mbr(self):
        offset = 368050176  # 0x15f00000
        signature = 0xb3eba8f1
        elem = self.mgr.construct_partition_device_element(
            partition_offset=offset,
            disk_signature=signature,
            is_gpt=False,
        )
        self.assertEqual(len(elem), 88)
        # Type flag at 0x10
        dev_type = struct.unpack_from("<I", elem, 0x10)[0]
        self.assertIn(dev_type, (5, 6))
        # Size at 0x18
        size = struct.unpack_from("<I", elem, 0x18)[0]
        self.assertEqual(size, 0x48)
        # Partition offset at 0x20 (LE uint64)
        part_offset = struct.unpack_from("<Q", elem, 0x20)[0]
        self.assertEqual(part_offset, offset)
        # Disk signature at 0x38 (LE uint32)
        disk_sig = struct.unpack_from("<I", elem, 0x38)[0]
        self.assertEqual(disk_sig, signature)

        # Test string hex disk signature
        elem_str_sig = self.mgr.construct_partition_device_element(
            partition_offset=offset,
            disk_signature="0xb3eba8f1",
            is_gpt=False,
        )
        self.assertEqual(elem_str_sig, elem)

    def test_deploy_uefi_bcd_populated_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            target = tmppath / "EFI" / "Microsoft" / "Boot" / "BCD"

            esp = PartitionInfo(
                device="/dev/nvme0n1p1",
                disk="/dev/nvme0n1",
                part_num=1,
                fstype="vfat",
                part_uuid="c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
                disk_guid="be6bface-727b-46b3-b29f-8e5e2f65f90e",
                pttype="gpt",
            )
            win = PartitionInfo(
                device="/dev/nvme0n1p2",
                disk="/dev/nvme0n1",
                part_num=2,
                fstype="ntfs",
                part_uuid="550e8400-e29b-41d4-a716-446655440000",
                disk_guid="be6bface-727b-46b3-b29f-8e5e2f65f90e",
                pttype="gpt",
            )

            ok = self.mgr.deploy_bcd(
                target_path=target,
                boot_mode=BootMode.UEFI,
                windows_partition=win,
                esp_partition=esp,
                description="Windows 11 Pro Test",
                timeout=15,
            )
            self.assertTrue(ok)
            self.assertTrue(target.exists())
            self.assertTrue(self.mgr.is_valid_hive(target))

            info = self.mgr.inspect_bcd(target)
            self.assertTrue(info["valid_hive"])
            self.assertGreater(info["size_bytes"], 8192)
            self.assertTrue(info["has_bootmgr_guid"])
            self.assertTrue(info["has_default_guid"])

            if shutil.which("hivexml"):
                p = subprocess.run(["hivexml", str(target)], stdout=subprocess.PIPE, text=True)
                self.assertEqual(p.returncode, 0)
                root = ET.fromstring(p.stdout)

                # 1. Verify Boot Manager object
                bootmgr_node = root.find(f".//node[@name='{BcdManager.BOOTMGR_GUID}']/node[@name='Elements']")
                self.assertIsNotNone(bootmgr_node)
                bm_elems = {el.get("name"): el for el in bootmgr_node.findall("./node")}

                # Default object must point to Windows loader GUID
                def_obj = bm_elems["23000003"].find("./value").attrib.get("value")
                self.assertEqual(def_obj, BcdManager.WINDOWS_LOADER_GUID)

                # Display order must be REG_MULTI_SZ containing loader GUID
                disp_val = bm_elems["24000001"].find("./value")
                disp_strings = [s.text for s in disp_val.findall("./string") if s.text]
                self.assertIn(BcdManager.WINDOWS_LOADER_GUID, disp_strings)

                # Bootmgr path
                bm_path = bm_elems["12000002"].find("./value").attrib.get("value")
                self.assertEqual(bm_path, r"\EFI\Microsoft\Boot\bootmgfw.efi")

                # Bootmgr application device element
                esp_dev_bytes = base64.b64decode(bm_elems["11000001"].find("./value").attrib.get("value"))
                self.assertEqual(len(esp_dev_bytes), 88)
                self.assertEqual(esp_dev_bytes[0x20:0x30], uuid.UUID(esp.part_uuid).bytes_le)
                self.assertEqual(esp_dev_bytes[0x38:0x48], uuid.UUID(esp.disk_guid).bytes_le)

                # 2. Verify Windows OS Loader object
                loader_node = root.find(f".//node[@name='{BcdManager.WINDOWS_LOADER_GUID}']/node[@name='Elements']")
                self.assertIsNotNone(loader_node)
                ld_elems = {el.get("name"): el for el in loader_node.findall("./node")}

                # Winload.efi application path
                app_path = ld_elems["12000002"].find("./value").attrib.get("value")
                self.assertEqual(app_path, r"\windows\system32\winload.efi")

                # System root
                sys_root = ld_elems["22000002"].find("./value").attrib.get("value")
                self.assertEqual(sys_root, r"\windows")

                # Custom description
                desc = ld_elems["12000004"].find("./value").attrib.get("value")
                self.assertEqual(desc, "Windows 11 Pro Test")

                # Application device (11000001)
                win_app_dev = base64.b64decode(ld_elems["11000001"].find("./value").attrib.get("value"))
                self.assertEqual(len(win_app_dev), 88)
                self.assertEqual(win_app_dev[0x20:0x30], uuid.UUID(win.part_uuid).bytes_le)
                self.assertEqual(win_app_dev[0x38:0x48], uuid.UUID(win.disk_guid).bytes_le)

                # OS device (21000001)
                win_os_dev = base64.b64decode(ld_elems["21000001"].find("./value").attrib.get("value"))
                self.assertEqual(len(win_os_dev), 88)
                self.assertEqual(win_os_dev[0x20:0x30], uuid.UUID(win.part_uuid).bytes_le)
                self.assertEqual(win_os_dev[0x38:0x48], uuid.UUID(win.disk_guid).bytes_le)

    def test_deploy_pcat_bios_bcd_populated_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            target = tmppath / "Boot" / "BCD"

            win = PartitionInfo(
                device="/dev/sda1",
                disk="/dev/sda",
                part_num=1,
                fstype="ntfs",
                partition_offset=1048576,
                disk_signature=0xdeadbeef,
                pttype="dos",
            )

            ok = self.mgr.deploy_bcd(
                target_path=target,
                boot_mode=BootMode.BIOS,
                windows_partition=win,
                description="Windows 10 BIOS MBR",
                timeout=5,
            )
            self.assertTrue(ok)
            self.assertTrue(target.exists())
            self.assertTrue(self.mgr.is_valid_hive(target))

            if shutil.which("hivexml"):
                p = subprocess.run(["hivexml", str(target)], stdout=subprocess.PIPE, text=True)
                self.assertEqual(p.returncode, 0)
                root = ET.fromstring(p.stdout)

                # Bootmgr
                bootmgr_node = root.find(f".//node[@name='{BcdManager.BOOTMGR_GUID}']/node[@name='Elements']")
                self.assertIsNotNone(bootmgr_node)
                bm_elems = {el.get("name"): el for el in bootmgr_node.findall("./node")}
                bm_path = bm_elems["12000002"].find("./value").attrib.get("value")
                self.assertEqual(bm_path, r"\bootmgr")

                # Loader
                loader_node = root.find(f".//node[@name='{BcdManager.WINDOWS_LOADER_GUID}']/node[@name='Elements']")
                self.assertIsNotNone(loader_node)
                ld_elems = {el.get("name"): el for el in loader_node.findall("./node")}

                # Winload.exe path
                app_path = ld_elems["12000002"].find("./value").attrib.get("value")
                self.assertEqual(app_path, r"\windows\system32\winload.exe")

                # System root
                sys_root = ld_elems["22000002"].find("./value").attrib.get("value")
                self.assertEqual(sys_root, r"\windows")

                # MBR device element
                dev_bytes = base64.b64decode(ld_elems["11000001"].find("./value").attrib.get("value"))
                self.assertEqual(len(dev_bytes), 88)
                self.assertEqual(struct.unpack_from("<Q", dev_bytes, 0x20)[0], 1048576)
                self.assertEqual(struct.unpack_from("<I", dev_bytes, 0x38)[0], 0xdeadbeef)

    def test_configure_bcd_direct_update(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            target = tmppath / "BCD"

            # Deploy base hive
            self.mgr.deploy_bcd(target, boot_mode=BootMode.UEFI)

            # Reconfigure directly with configure_bcd
            win = PartitionInfo(
                device="/dev/nvme0n1p2",
                disk="/dev/nvme0n1",
                part_num=2,
                fstype="ntfs",
                part_uuid="77777777-7777-7777-7777-777777777777",
                disk_guid="88888888-8888-8888-8888-888888888888",
                pttype="gpt",
            )
            ok = self.mgr.configure_bcd(
                target_path=target,
                boot_mode=BootMode.UEFI,
                windows_partition=win,
                description="Reconfigured System",
                timeout=20,
            )
            self.assertTrue(ok)

            if shutil.which("hivexml"):
                p = subprocess.run(["hivexml", str(target)], stdout=subprocess.PIPE, text=True)
                root = ET.fromstring(p.stdout)
                loader_node = root.find(f".//node[@name='{BcdManager.WINDOWS_LOADER_GUID}']/node[@name='Elements']")
                ld_elems = {el.get("name"): el for el in loader_node.findall("./node")}
                desc = ld_elems["12000004"].find("./value").attrib.get("value")
                self.assertEqual(desc, "Reconfigured System")

                dev_bytes = base64.b64decode(ld_elems["11000001"].find("./value").attrib.get("value"))
                self.assertEqual(dev_bytes[0x20:0x30], uuid.UUID(win.part_uuid).bytes_le)

    def test_deploy_from_source_template(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            src = tmppath / "src_bcd"
            self.mgr.deploy_bcd(src, boot_mode=BootMode.UEFI)

            dest = tmppath / "dest_bcd"
            ok = self.mgr.deploy_bcd(dest, source_template=src, boot_mode=BootMode.UEFI)
            self.assertTrue(ok)
            self.assertTrue(self.mgr.is_valid_hive(dest))


if __name__ == "__main__":
    unittest.main()
