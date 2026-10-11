"""
Core orchestrator: generates recovery plans, coordinates safety backups,
deploys boot assets, patches BCD, updates NVRAM, and verifies recovery integrity.
"""

import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

from redivivus.bcd import BcdManager
from redivivus.deployer import BootDeployer
from redivivus.models import (
    ActionItem,
    BootMode,
    PartitionInfo,
    RecoveryPlan,
    RecoveryResult,
    VerificationCheck,
    WindowsInstallation,
)
from redivivus.nvram import NvramManager


class RecoveryEngine:
    """Orchestrates end-to-end Windows bootloader recovery."""

    def __init__(
        self,
        bcd_manager: Optional[BcdManager] = None,
        deployer: Optional[BootDeployer] = None,
        nvram_manager: Optional[NvramManager] = None,
    ):
        self.bcd = bcd_manager or BcdManager()
        self.deployer = deployer or BootDeployer()
        self.nvram = nvram_manager or NvramManager()

    def build_plan(
        self,
        windows: WindowsInstallation,
        esp: Optional[PartitionInfo] = None,
        boot_mode: BootMode = BootMode.UEFI,
        install_fallback: bool = True,
        update_nvram: bool = True,
        backup_esp: bool = True,
        dry_run: bool = False,
    ) -> RecoveryPlan:
        """Constructs an atomic, step-by-step recovery plan."""
        actions: List[ActionItem] = []

        if boot_mode == BootMode.UEFI:
            if not esp:
                raise ValueError("An EFI System Partition (ESP) is required for UEFI recovery.")

            # 1. Backup action
            if backup_esp:
                actions.append(
                    ActionItem(
                        category="Backup",
                        description=f"Create timestamped backup of existing EFI/Microsoft directory on {esp.device}",
                        target_path=f"{esp.mount_point or esp.device}/EFI/Microsoft",
                    )
                )

            # 2. Deploy boot files
            actions.append(
                ActionItem(
                    category="Deploy",
                    description=f"Deploy Windows UEFI boot binaries (bootmgfw.efi, bootmgr.efi, boot.sdi) from {windows.partition.device}",
                    target_path=f"{esp.mount_point or esp.device}/EFI/Microsoft/Boot/",
                )
            )

            # 3. Fonts and locales
            actions.append(
                ActionItem(
                    category="Deploy",
                    description="Deploy boot fonts and localized MUI resource directories",
                    target_path=f"{esp.mount_point or esp.device}/EFI/Microsoft/Boot/Fonts/",
                )
            )

            # 4. BCD Hive
            actions.append(
                ActionItem(
                    category="BCD",
                    description=f"Deploy and configure Windows BCD registry hive (targeting OS on {windows.partition.device})",
                    target_path=f"{esp.mount_point or esp.device}/EFI/Microsoft/Boot/BCD",
                )
            )

            # 5. Fallback loader
            if install_fallback:
                actions.append(
                    ActionItem(
                        category="Fallback",
                        description="Install standard UEFI fallback loader (EFI/Boot/bootx64.efi)",
                        target_path=f"{esp.mount_point or esp.device}/EFI/Boot/bootx64.efi",
                    )
                )

            # 6. NVRAM
            if update_nvram:
                actions.append(
                    ActionItem(
                        category="NVRAM",
                        description=f"Register and prioritize 'Windows Boot Manager' entry via efibootmgr on {esp.disk} part {esp.part_num}",
                        command=f"efibootmgr -c -d {esp.disk} -p {esp.part_num} -L 'Windows Boot Manager' -l '\\EFI\\Microsoft\\Boot\\bootmgfw.efi'",
                    )
                )

        else:
            # 1. Active boot flag on partition (MBR practice)
            disk = windows.partition.disk
            part_num = windows.partition.part_num
            if windows.partition.pttype == "dos" and not windows.partition.is_boot_flag:
                actions.append(
                    ActionItem(
                        category="MBR",
                        description=f"Set bootable/active flag on MBR partition {windows.partition.device}",
                        command=f"parted -s {disk} set {part_num} boot on",
                    )
                )

            # 2. Deploy bootmgr to root
            actions.append(
                ActionItem(
                    category="Deploy",
                    description=f"Deploy bootmgr to filesystem root of {windows.partition.device}",
                    target_path=f"{windows.partition.mount_point or windows.partition.device}/bootmgr",
                )
            )

            # 3. Deploy PCAT supporting assets
            actions.append(
                ActionItem(
                    category="Deploy",
                    description="Deploy PCAT supporting boot assets (boot.sdi, memtest.exe) to \\Boot",
                    target_path=f"{windows.partition.mount_point or windows.partition.device}/Boot/",
                )
            )

            # 4. Deploy PCAT BCD
            actions.append(
                ActionItem(
                    category="BCD",
                    description="Deploy and configure PCAT BCD registry hive",
                    target_path=f"{windows.partition.mount_point or windows.partition.device}/Boot/BCD",
                )
            )

        return RecoveryPlan(
            windows_target=windows,
            esp_target=esp,
            boot_mode=boot_mode,
            actions=actions,
            backup_esp=backup_esp,
            install_fallback_bootx64=install_fallback,
            update_nvram=update_nvram,
            dry_run=dry_run,
        )

    def execute_recovery(
        self,
        plan: RecoveryPlan,
        win_root: Path,
        esp_root: Optional[Path] = None,
    ) -> RecoveryResult:
        """Executes the recovery plan."""
        messages: List[str] = []
        errors: List[str] = []
        backup_path_str: Optional[str] = None
        checks: List[VerificationCheck] = []

        if plan.dry_run:
            messages.append("Dry-run mode active. No disk modifications were executed.")
            return RecoveryResult(
                success=True,
                plan=plan,
                messages=messages,
                errors=[],
                backup_dir=None,
                verification_checks=[],
            )

        if plan.boot_mode == BootMode.UEFI:
            if not esp_root or not esp_root.exists():
                return RecoveryResult(
                    success=False,
                    plan=plan,
                    messages=[],
                    errors=["ESP root path does not exist or is not mounted."],
                )

            target_ms_boot = esp_root / "EFI" / "Microsoft" / "Boot"

            # Step 1: Backup existing boot directory
            if plan.backup_esp and target_ms_boot.parent.exists():
                backed_up = self.deployer.backup_existing_boot_dir(target_ms_boot.parent)
                if backed_up:
                    backup_path_str = str(backed_up)
                    messages.append(f"Saved pre-flight backup to {backup_path_str}")

            # Step 2: Deploy UEFI boot assets
            deploy_ok, actions, deploy_errs = self.deployer.deploy_uefi_boot_assets(
                win_root=win_root,
                esp_root=esp_root,
                install_fallback=plan.install_fallback_bootx64,
                dry_run=False,
            )
            messages.extend(actions)
            errors.extend(deploy_errs)

            # Step 3: Deploy and configure BCD hive
            target_bcd = target_ms_boot / "BCD"
            src_template = win_root / "Windows" / "System32" / "config" / "BCD-Template"
            if not src_template.exists():
                # Check case-insensitively
                found = self.deployer._find_file_case_insensitive(win_root / "Windows" / "System32" / "config", "BCD-Template")
                if found:
                    src_template = found

            bcd_ok = self.bcd.deploy_bcd(
                target_path=target_bcd,
                source_template=src_template if src_template.exists() else None,
                boot_mode=BootMode.UEFI,
                windows_partition=plan.windows_target.partition,
                description=plan.windows_target.product_name or "Windows",
            )

            if bcd_ok:
                messages.append(f"BCD hive successfully created at {target_bcd}")
            else:
                errors.append(f"Failed to generate valid BCD hive at {target_bcd}")

            # Step 4: NVRAM registration
            if plan.update_nvram and plan.esp_target:
                nvram_ok, nvram_acts, nvram_errs = self.nvram.create_or_update_windows_entry(
                    esp_partition=plan.esp_target,
                    set_first=True,
                    dry_run=False,
                )
                messages.extend(nvram_acts)
                if not nvram_ok:
                    # NVRAM failure is treated as warning if fallback loader is installed
                    warning_note = "NVRAM update failed; fallback loader will handle boot if NVRAM is inaccessible."
                    messages.append(warning_note)
                    errors.extend(nvram_errs)

            # Step 5: Post-recovery verification
            checks = self.verify_uefi_recovery(esp_root, plan.install_fallback_bootx64)

        else:
            # Legacy PCAT recovery
            deploy_ok, actions, deploy_errs = self.deployer.deploy_legacy_boot_assets(
                win_root=win_root,
                boot_root=esp_root or win_root,
                dry_run=False,
            )
            messages.extend(actions)
            errors.extend(deploy_errs)

            target_bcd = (esp_root or win_root) / "Boot" / "BCD"
            bcd_ok = self.bcd.deploy_bcd(
                target_path=target_bcd,
                boot_mode=BootMode.BIOS,
                windows_partition=plan.windows_target.partition,
            )
            if bcd_ok:
                messages.append(f"Legacy BCD hive created at {target_bcd}")
            else:
                errors.append(f"Failed to generate legacy BCD hive at {target_bcd}")

            checks = self.verify_legacy_recovery(esp_root or win_root)

        # Success is determined by critical files passing verification
        critical_passed = all(c.passed for c in checks if c.severity == "ERROR")
        success = critical_passed and len(errors) == 0

        return RecoveryResult(
            success=success,
            plan=plan,
            messages=messages,
            errors=errors,
            backup_dir=backup_path_str,
            verification_checks=checks,
        )

    def verify_uefi_recovery(self, esp_root: Path, check_fallback: bool = True) -> List[VerificationCheck]:
        """Runs post-recovery integrity checks on the ESP."""
        checks: List[VerificationCheck] = []

        bootmgfw = esp_root / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"
        if bootmgfw.exists() and bootmgfw.stat().st_size > 0:
            checks.append(
                VerificationCheck(
                    name="Windows Boot Manager EFI Binary",
                    passed=True,
                    details=f"Present at {bootmgfw} ({bootmgfw.stat().st_size} bytes)",
                )
            )
        else:
            checks.append(
                VerificationCheck(
                    name="Windows Boot Manager EFI Binary",
                    passed=False,
                    details="bootmgfw.efi is missing or empty in EFI/Microsoft/Boot/",
                    severity="ERROR",
                )
            )

        bcd_file = esp_root / "EFI" / "Microsoft" / "Boot" / "BCD"
        if self.bcd.is_valid_hive(bcd_file):
            checks.append(
                VerificationCheck(
                    name="BCD Registry Hive",
                    passed=True,
                    details=f"Valid regf hive at {bcd_file} ({bcd_file.stat().st_size} bytes)",
                )
            )
        else:
            checks.append(
                VerificationCheck(
                    name="BCD Registry Hive",
                    passed=False,
                    details="BCD hive is missing or corrupt in EFI/Microsoft/Boot/",
                    severity="ERROR",
                )
            )

        if check_fallback:
            fallback = esp_root / "EFI" / "Boot" / "bootx64.efi"
            if fallback.exists() and fallback.stat().st_size > 0:
                checks.append(
                    VerificationCheck(
                        name="UEFI Fallback Loader (bootx64.efi)",
                        passed=True,
                        details=f"Present at {fallback}",
                        severity="INFO",
                    )
                )
            else:
                checks.append(
                    VerificationCheck(
                        name="UEFI Fallback Loader (bootx64.efi)",
                        passed=False,
                        details="Fallback loader is missing in EFI/Boot/",
                        severity="WARNING",
                    )
                )

        return checks

    def verify_legacy_recovery(self, boot_root: Path) -> List[VerificationCheck]:
        """Runs post-recovery integrity checks on active legacy partition."""
        checks: List[VerificationCheck] = []

        bootmgr = boot_root / "bootmgr"
        if bootmgr.exists() and bootmgr.stat().st_size > 1024:
            checks.append(
                VerificationCheck(
                    name="Legacy bootmgr Executable",
                    passed=True,
                    details=f"Present at {bootmgr}",
                )
            )
        else:
            checks.append(
                VerificationCheck(
                    name="Legacy bootmgr Executable",
                    passed=False,
                    details="bootmgr is missing at partition root",
                    severity="ERROR",
                )
            )

        bcd_file = boot_root / "Boot" / "BCD"
        if self.bcd.is_valid_hive(bcd_file):
            checks.append(
                VerificationCheck(
                    name="Legacy BCD Registry Hive",
                    passed=True,
                    details=f"Valid regf hive at {bcd_file}",
                )
            )
        else:
            checks.append(
                VerificationCheck(
                    name="Legacy BCD Registry Hive",
                    passed=False,
                    details="BCD hive is missing in /Boot/",
                    severity="ERROR",
                )
            )

        return checks
