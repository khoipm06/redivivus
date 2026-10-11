"""
Bootloader assets deployer: transfers Windows boot files, fonts, and EFI applications
from Windows OS partition to the EFI System Partition (or active legacy partition).
"""

import os
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from redivivus.models import BootMode, PartitionInfo, WindowsInstallation


class BootDeployer:
    """Deploys EFI and legacy boot files, fonts, and resources."""

    UEFI_CRITICAL_FILES = [
        "bootmgfw.efi",
        "bootmgr.efi",
        "memtest.efi",
        "boot.sdi",
    ]

    LEGACY_CRITICAL_FILES = [
        "bootmgr",
        "boot.sdi",
        "memtest.exe",
    ]

    def __init__(self, run_cmd_fn=None):
        self._run_cmd = run_cmd_fn

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

    def backup_existing_boot_dir(self, target_boot_dir: Path, backup_parent: Optional[Path] = None) -> Optional[Path]:
        """Creates a timestamped backup of existing boot directory before modification."""
        if not target_boot_dir.exists():
            return None

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        if backup_parent:
            backup_path = backup_parent / f"{target_boot_dir.name}_backup_{timestamp}"
        else:
            backup_path = target_boot_dir.parent / f"{target_boot_dir.name}_backup_{timestamp}"

        try:
            if backup_parent:
                backup_parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target_boot_dir, backup_path, symlinks=True)
            return backup_path
        except (IOError, OSError, PermissionError):
            return None

    def locate_windows_boot_source(self, win_root: Path, boot_mode: BootMode = BootMode.UEFI) -> Optional[Path]:
        """Locates the C:\\Windows\\Boot\\EFI or C:\\Windows\\Boot\\PCAT directory."""
        win_dir = self._find_dir_case_insensitive(win_root, "Windows")
        if not win_dir:
            return None

        boot_dir = self._find_dir_case_insensitive(win_dir, "Boot")
        if not boot_dir:
            return None

        sub_folder = "EFI" if boot_mode == BootMode.UEFI else "PCAT"
        return self._find_dir_case_insensitive(boot_dir, sub_folder)

    def deploy_uefi_boot_assets(
        self,
        win_root: Path,
        esp_root: Path,
        install_fallback: bool = True,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Deploys UEFI boot files from Windows\\Boot\\EFI to ESP/EFI/Microsoft/Boot and ESP/EFI/Boot.
        Returns: (success, actions_taken, errors)
        """
        actions: List[str] = []
        errors: List[str] = []

        src_efi_dir = self.locate_windows_boot_source(win_root, BootMode.UEFI)
        if not src_efi_dir or not src_efi_dir.exists():
            msg = f"Could not find Windows Boot EFI source in {win_root}"
            errors.append(msg)
            return False, actions, errors

        dest_ms_boot = esp_root / "EFI" / "Microsoft" / "Boot"
        dest_fallback_boot = esp_root / "EFI" / "Boot"

        if not dry_run:
            try:
                dest_ms_boot.mkdir(parents=True, exist_ok=True)
                if install_fallback:
                    dest_fallback_boot.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as pe:
                errors.append(f"Permission denied creating directories in ESP: {pe}. Root privileges required.")
                return False, actions, errors

        # 1. Copy critical EFI files
        for filename in self.UEFI_CRITICAL_FILES:
            src_file = self._find_file_case_insensitive(src_efi_dir, filename)
            if src_file:
                target_file = dest_ms_boot / filename
                actions.append(f"Copy {src_file.name} -> {target_file}")
                if not dry_run:
                    try:
                        shutil.copy2(src_file, target_file)
                    except Exception as ex:
                        errors.append(f"Failed to copy {src_file.name}: {ex}")

        # 2. Copy Fonts folder if present
        src_fonts = self._find_dir_case_insensitive(src_efi_dir, "Fonts")
        if src_fonts and src_fonts.exists():
            target_fonts = dest_ms_boot / "Fonts"
            actions.append(f"Copy directory {src_fonts.name} -> {target_fonts}")
            if not dry_run:
                try:
                    if target_fonts.exists():
                        shutil.rmtree(target_fonts)
                    shutil.copytree(src_fonts, target_fonts)
                except Exception as ex:
                    errors.append(f"Failed to copy Fonts directory: {ex}")

        # 3. Copy locale folders (e.g. en-US, ja-JP) containing .mui files
        try:
            for item in src_efi_dir.iterdir():
                if item.is_dir() and item.name.lower() != "fonts":
                    target_locale = dest_ms_boot / item.name
                    actions.append(f"Copy locale directory {item.name} -> {target_locale}")
                    if not dry_run:
                        try:
                            if target_locale.exists():
                                shutil.rmtree(target_locale)
                            shutil.copytree(item, target_locale)
                        except Exception as ex:
                            errors.append(f"Failed to copy locale {item.name}: {ex}")
        except Exception as ex:
            errors.append(f"Error reading locales from {src_efi_dir}: {ex}")

        # 4. Install standard UEFI fallback loader (\EFI\Boot\bootx64.efi)
        if install_fallback:
            bootmgfw = self._find_file_case_insensitive(dest_ms_boot if not dry_run else src_efi_dir, "bootmgfw.efi")
            if bootmgfw and bootmgfw.exists():
                fallback_target = dest_fallback_boot / "bootx64.efi"
                actions.append(f"Deploy UEFI fallback loader: {bootmgfw.name} -> {fallback_target}")
                if not dry_run:
                    try:
                        shutil.copy2(bootmgfw, fallback_target)
                    except Exception as ex:
                        errors.append(f"Failed to deploy fallback loader: {ex}")

        success = len(errors) == 0
        return success, actions, errors

    def deploy_legacy_boot_assets(
        self,
        win_root: Path,
        boot_root: Path,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str], List[str]]:
        """
        Deploys legacy BIOS boot files from Windows\\Boot\\PCAT to active partition.
        Copies \\bootmgr to root and supporting files to \\Boot.
        """
        actions: List[str] = []
        errors: List[str] = []

        src_pcat_dir = self.locate_windows_boot_source(win_root, BootMode.BIOS)
        if not src_pcat_dir or not src_pcat_dir.exists():
            msg = f"Could not find Windows Boot PCAT source in {win_root}"
            errors.append(msg)
            return False, actions, errors

        dest_boot_dir = boot_root / "Boot"

        if not dry_run:
            try:
                dest_boot_dir.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as pe:
                errors.append(f"Permission denied creating boot directory: {pe}. Root privileges required.")
                return False, actions, errors

        # 1. Copy bootmgr to filesystem root
        bootmgr_src = self._find_file_case_insensitive(src_pcat_dir, "bootmgr")
        if bootmgr_src:
            bootmgr_target = boot_root / "bootmgr"
            actions.append(f"Copy {bootmgr_src.name} -> {bootmgr_target}")
            if not dry_run:
                try:
                    shutil.copy2(bootmgr_src, bootmgr_target)
                except Exception as ex:
                    errors.append(f"Failed to copy bootmgr: {ex}")

        # 2. Copy supporting PCAT files into \\Boot
        for filename in ["boot.sdi", "memtest.exe"]:
            src_file = self._find_file_case_insensitive(src_pcat_dir, filename)
            if src_file:
                target_file = dest_boot_dir / filename
                actions.append(f"Copy {src_file.name} -> {target_file}")
                if not dry_run:
                    try:
                        shutil.copy2(src_file, target_file)
                    except Exception as ex:
                        errors.append(f"Failed to copy {src_file.name}: {ex}")

        # 3. Copy Fonts folder if present
        src_fonts = self._find_dir_case_insensitive(src_pcat_dir, "Fonts")
        if src_fonts and src_fonts.exists():
            target_fonts = dest_boot_dir / "Fonts"
            actions.append(f"Copy directory {src_fonts.name} -> {target_fonts}")
            if not dry_run:
                try:
                    if target_fonts.exists():
                        shutil.rmtree(target_fonts)
                    shutil.copytree(src_fonts, target_fonts)
                except Exception as ex:
                    errors.append(f"Failed to copy Fonts directory: {ex}")

        success = len(errors) == 0
        return success, actions, errors
