import unittest
from redivivus.models import PartitionInfo, PartitionRole
from redivivus.nvram import NvramManager


"""
User Story 5 (US5) - UEFI NVRAM Boot Entry Management & Dual-Boot Protection
As a dual-boot user,
I want Redivivus to activate or register Windows Boot Manager in UEFI NVRAM,
while preserving existing boot priority (default set_first=False) so Linux GRUB is not hijacked.
"""

SAMPLE_NVRAM_EXISTING = """
BootCurrent: 0001
BootOrder: 0001,0000
Boot0000  Windows Boot Manager\tHD(1,GPT,c12a7328-f81f-11d2-ba4b-00a0c93ec93b,0x800,0x89000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)
Boot0001* Linux\tHD(2,GPT,660e8400-e29b-41d4-a716-446655440000,0x89800,0x100000)/File(\\EFI\\linux\\shimx64.efi)
"""

SAMPLE_NVRAM_CLEAN = """
BootCurrent: 0001
BootOrder: 0001
Boot0001* Linux\tHD(2,GPT,660e8400-e29b-41d4-a716-446655440000,0x89800,0x100000)/File(\\EFI\\linux\\shimx64.efi)
"""


class TestNvram(unittest.TestCase):
    def test_existing_inactive_windows_entry(self):
        """US5-AC1: Reactivate existing dormant Windows Boot Manager without duplicate entries."""
        commands_run = []

        def mock_run(cmd):
            commands_run.append(" ".join(cmd))
            if cmd == ["efibootmgr", "-v"]:
                return 0, SAMPLE_NVRAM_EXISTING, ""
            return 0, "", ""

        mgr = NvramManager(run_cmd_fn=mock_run)
        esp = PartitionInfo(
            device="/dev/sda1",
            disk="/dev/sda",
            part_num=1,
            fstype="vfat",
            role=PartitionRole.ESP,
        )

        ok, actions, errors = mgr.create_or_update_windows_entry(
            esp_partition=esp,
            set_first=True,
            dry_run=False,
        )

        self.assertTrue(ok)
        self.assertEqual(len(errors), 0)
        # Verify efibootmgr -a -b 0000 and order update were called
        self.assertTrue(any("-a -b 0000" in c for c in commands_run))
        self.assertTrue(any("-o 0000,0001" in c for c in commands_run))

    def test_create_new_entry_dry_run(self):
        commands_run = []

        def mock_run(cmd):
            commands_run.append(" ".join(cmd))
            if cmd == ["efibootmgr", "-v"]:
                return 0, SAMPLE_NVRAM_CLEAN, ""
            return 0, "", ""

        mgr = NvramManager(run_cmd_fn=mock_run)
        esp = PartitionInfo(
            device="/dev/sda1",
            disk="/dev/sda",
            part_num=1,
            fstype="vfat",
            role=PartitionRole.ESP,
        )

        ok, actions, errors = mgr.create_or_update_windows_entry(
            esp_partition=esp,
            set_first=True,
            dry_run=True,
        )

        self.assertTrue(ok)
        self.assertEqual(len(errors), 0)
        self.assertTrue(any("Create NVRAM entry" in a for a in actions))
        # In dry run, only efibootmgr -v should be executed
        self.assertEqual(len([c for c in commands_run if "-c" in c]), 0)


    def test_create_new_entry_preserves_boot_order_when_set_first_false(self):
        """US5-AC2: When creating a brand-new Windows entry with set_first=False, restore Linux as first in BootOrder."""
        commands_run = []

        def mock_run(cmd):
            commands_run.append(" ".join(cmd))
            if cmd == ["efibootmgr", "-v"]:
                # If -c hasn't been called yet: initial order is 0001 (Linux only)
                if not any("-c" in c for c in commands_run):
                    return 0, SAMPLE_NVRAM_CLEAN, ""
                else:
                    # After efibootmgr -c: efibootmgr automatically inserted 0000 at index 0!
                    sample_after_create = (
                        "BootCurrent: 0001\n"
                        "BootOrder: 0000,0001\n"
                        "Boot0000* Windows Boot Manager\tHD(1,GPT,c12a7328-f81f-11d2-ba4b-00a0c93ec93b,0x800,0x89000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)\n"
                        "Boot0001* Linux\tHD(2,GPT,660e8400-e29b-41d4-a716-446655440000,0x89800,0x100000)/File(\\EFI\\linux\\shimx64.efi)\n"
                    )
                    return 0, sample_after_create, ""
            return 0, "", ""

        mgr = NvramManager(run_cmd_fn=mock_run)
        esp = PartitionInfo(
            device="/dev/sda1",
            disk="/dev/sda",
            part_num=1,
            fstype="vfat",
            role=PartitionRole.ESP,
        )

        ok, actions, errors = mgr.create_or_update_windows_entry(
            esp_partition=esp,
            set_first=False,
            dry_run=False,
        )

        self.assertTrue(ok)
        self.assertEqual(len(errors), 0)
        # efibootmgr -c was called
        self.assertTrue(any("-c -d /dev/sda" in c for c in commands_run))
        # Crucially: efibootmgr -o 0001,0000 MUST have been called to push Windows to end and restore Linux first!
        self.assertTrue(any("-o 0001,0000" in c for c in commands_run))
        self.assertTrue(any("Preserve boot order" in a for a in actions))


if __name__ == "__main__":
    unittest.main()
