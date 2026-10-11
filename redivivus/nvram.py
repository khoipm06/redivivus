"""
UEFI NVRAM boot manager: inspects, creates, and reorders UEFI boot entries via efibootmgr.
"""

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from redivivus.models import EfiBootEntry, PartitionInfo
from redivivus.scanner import SystemScanner


class NvramManager:
    """Manages UEFI NVRAM firmware boot entries using efibootmgr."""

    DEFAULT_LABEL = "Windows Boot Manager"
    DEFAULT_LOADER_PATH = "\\EFI\\Microsoft\\Boot\\bootmgfw.efi"

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

    def get_entries(self) -> Tuple[List[EfiBootEntry], List[str]]:
        """Reads current EFI boot entries and boot order."""
        ret, stdout, stderr = self._run_cmd(["efibootmgr", "-v"])
        if ret != 0:
            return [], []
        return SystemScanner.parse_efibootmgr_output(stdout)

    def find_windows_entry(self, entries: List[EfiBootEntry]) -> Optional[EfiBootEntry]:
        """Finds any existing Windows Boot Manager entry in NVRAM."""
        for entry in entries:
            if entry.is_windows_manager():
                return entry
        return None

    def create_or_update_windows_entry(
        self,
        esp_partition: PartitionInfo,
        label: str = DEFAULT_LABEL,
        loader_path: str = DEFAULT_LOADER_PATH,
        set_first: bool = True,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Creates or updates a Windows Boot Manager NVRAM entry.
        Returns: (success, actions_taken, errors)
        """
        actions: List[str] = []
        errors: List[str] = []

        entries, current_order = self.get_entries()
        existing_win = self.find_windows_entry(entries)

        target_disk = esp_partition.disk
        target_part_num = esp_partition.part_num

        if existing_win:
            boot_num = existing_win.boot_num
            actions.append(f"Found existing Windows boot entry: Boot{boot_num} ({existing_win.label})")

            # Activate entry if inactive
            if not existing_win.active:
                activate_cmd = ["efibootmgr", "-a", "-b", boot_num]
                actions.append(f"Activate Boot{boot_num}: {' '.join(activate_cmd)}")
                if not dry_run:
                    ret, stdout, stderr = self._run_cmd(activate_cmd)
                    if ret != 0:
                        errors.append(f"Failed to activate Boot{boot_num}: {stderr.strip()}")

            # Check if boot order needs adjustment
            if set_first and (not current_order or current_order[0] != boot_num):
                new_order = [boot_num] + [x for x in current_order if x != boot_num]
                order_cmd = ["efibootmgr", "-o", ",".join(new_order)]
                actions.append(f"Set boot order (Windows first): {' '.join(order_cmd)}")
                if not dry_run:
                    ret, stdout, stderr = self._run_cmd(order_cmd)
                    if ret != 0:
                        errors.append(f"Failed to set boot order: {stderr.strip()}")

            return len(errors) == 0, actions, errors

        # No entry exists: create new entry
        create_cmd = [
            "efibootmgr",
            "-c",
            "-d",
            target_disk,
            "-p",
            str(target_part_num),
            "-L",
            label,
            "-l",
            loader_path,
        ]
        actions.append(f"Create NVRAM entry: {' '.join(create_cmd)}")

        if not dry_run:
            ret, stdout, stderr = self._run_cmd(create_cmd)
            if ret != 0:
                errors.append(f"efibootmgr failed: {stderr.strip() or stdout.strip()}")
                return False, actions, errors

            # Re-read entries to verify creation
            updated_entries, updated_order = self.get_entries()
            new_win = self.find_windows_entry(updated_entries)
            if new_win:
                actions.append(f"Successfully verified created entry Boot{new_win.boot_num}")
                if set_first and updated_order and updated_order[0] != new_win.boot_num:
                    new_order = [new_win.boot_num] + [x for x in updated_order if x != new_win.boot_num]
                    order_cmd = ["efibootmgr", "-o", ",".join(new_order)]
                    actions.append(f"Set boot order (Windows first): {' '.join(order_cmd)}")
                    self._run_cmd(order_cmd)

        return len(errors) == 0, actions, errors
