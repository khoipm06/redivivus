"""
Command Line Interface (CLI) for Redivivus: Universal Windows Boot Recovery.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from redivivus import __version__
from redivivus.engine import RecoveryEngine
from redivivus.models import BootMode, PartitionInfo, ScanReport, WindowsInstallation
from redivivus.scanner import SystemScanner


def format_markdown_table(headers: List[str], rows: List[List[str]]) -> str:
    """Renders a clean GitHub-flavored Markdown table."""
    if not headers:
        return ""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(val)))

    header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join("-" * col_widths[i] for i in range(len(headers))) + " |"
    body_lines = [
        "| " + " | ".join(str(val).ljust(col_widths[i]) for i, val in enumerate(row)) + " |"
        for row in rows
    ]
    return "\n".join([header_line, sep_line] + body_lines)


def cmd_scan(args: argparse.Namespace) -> int:
    """Executes system and partition scan."""
    scanner = SystemScanner()
    report = scanner.run_full_scan(probe_unmounted=not args.no_probe)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
        return 0

    print(f"\n# Redivivus Hardware & Boot Diagnostic Report (v{__version__})\n")
    print(f"- **Firmware Environment**: `{'UEFI' if report.firmware_uefi else 'BIOS / Legacy'}`")
    print(f"- **Detected Boot Mode**: `{report.system_boot_mode.value}`")
    print(f"- **Total Partitions Discovered**: {len(report.partitions)}\n")

    # 1. Windows Installations Table
    print("## 1. Detected Windows Installations")
    if report.windows_installations:
        win_headers = ["Device", "Product", "UEFI Capable", "Legacy Capable", "BCD-Template", "Mount"]
        win_rows = []
        for win in report.windows_installations:
            win_rows.append([
                win.partition.device,
                win.product_name or "Windows (NT)",
                "Yes" if win.is_uefi_compatible() else "No",
                "Yes" if win.is_legacy_compatible() else "No",
                "Found" if win.has_bcd_template else "Missing",
                win.partition.mount_point or "Unmounted",
            ])
        print(format_markdown_table(win_headers, win_rows))
    else:
        print("*No Windows installations detected on accessible block devices.*")
    print()

    # 2. EFI System Partitions (ESP)
    print("## 2. Detected EFI System Partitions (ESP)")
    if report.esp_partitions:
        esp_headers = ["Device", "Disk", "Part #", "FSType", "Label", "Size", "Mount"]
        esp_rows = []
        for esp in report.esp_partitions:
            esp_rows.append([
                esp.device,
                esp.disk,
                str(esp.part_num),
                esp.fstype,
                esp.label or "(None)",
                esp.size_human or f"{esp.size_bytes}B",
                esp.mount_point or "Unmounted",
            ])
        print(format_markdown_table(esp_headers, esp_rows))
    else:
        print("*No EFI System Partitions (FAT32/ESP) found.*")
    print()

    # 3. UEFI NVRAM Boot Entries
    if report.firmware_uefi:
        print("## 3. UEFI NVRAM Boot Entries")
        if report.nvram_entries:
            nvram_headers = ["Boot #", "Active", "Label", "File Path", "Is Windows"]
            nvram_rows = []
            for entry in report.nvram_entries:
                nvram_rows.append([
                    f"Boot{entry.boot_num}",
                    "*" if entry.active else "No",
                    entry.label,
                    entry.file_path or "(N/A)",
                    "Yes" if entry.is_windows_manager() else "No",
                ])
            print(format_markdown_table(nvram_headers, nvram_rows))
            if report.current_boot_order:
                print(f"\n**Boot Order**: `{' -> '.join(report.current_boot_order)}`")
        else:
            print("*No NVRAM entries reported or efibootmgr not accessible.*")
        print()

    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Performs an in-depth diagnosis of Windows bootability."""
    scanner = SystemScanner()
    report = scanner.run_full_scan(probe_unmounted=True)

    print(f"\n# Redivivus Bootability Diagnosis\n")

    issues: List[str] = []
    recommendations: List[str] = []

    if not report.windows_installations:
        issues.append("Fatal: No Windows installation was detected on any storage device.")
        recommendations.append("Ensure your Windows drive is attached and unlocked (e.g. BitLocker/cryptsetup if encrypted).")

    if report.firmware_uefi:
        if not report.esp_partitions:
            issues.append("Critical: System is booted in UEFI mode, but no FAT32 EFI System Partition (ESP) exists.")
            recommendations.append("Create or designate an ESP partition formatted with FAT32 (type c12a7328-f81f-11d2-ba4b-00a0c93ec93b).")

        has_win_nvram = any(n.is_windows_manager() for n in report.nvram_entries)
        if not has_win_nvram:
            issues.append("Warning: No 'Windows Boot Manager' NVRAM entry found in UEFI firmware.")
            recommendations.append("Run `redivivus recover` to register and prioritize Windows in UEFI NVRAM.")

    if issues:
        print("### Detected Issues")
        for iss in issues:
            print(f"- {iss}")
        print("\n### Recommendations")
        for rec in recommendations:
            print(f"- {rec}")
    else:
        print("Diagnosis completed: System partitions appear healthy for Windows boot recovery.")

    return 0 if not issues else 1


def cmd_recover(args: argparse.Namespace) -> int:
    """Executes automated or customized bootloader recovery."""
    scanner = SystemScanner()
    engine = RecoveryEngine()

    print(f"\n=== Redivivus Windows Bootloader Recovery ===")

    report = scanner.run_full_scan(probe_unmounted=True)

    # 1. Determine Windows target
    windows_target: Optional[WindowsInstallation] = None
    if args.win:
        for w in report.windows_installations:
            if w.partition.device == args.win:
                windows_target = w
                break
        if not windows_target:
            print(f"Error: Specified Windows partition '{args.win}' not found or invalid.")
            return 1
    else:
        if not report.windows_installations:
            print("Error: No Windows installations detected on this machine.")
            return 1
        elif len(report.windows_installations) == 1:
            windows_target = report.windows_installations[0]
            print(f"Auto-selected Windows installation: {windows_target.partition.device} ({windows_target.product_name})")
        else:
            print("Multiple Windows installations detected. Please select via `--win <device>`:")
            for w in report.windows_installations:
                print(f"  - {w.partition.device}: {w.product_name}")
            return 1

    # 2. Determine Boot Mode and ESP target
    boot_mode = BootMode.UEFI if (args.mode == "UEFI" or (args.mode == "AUTO" and report.firmware_uefi)) else BootMode.BIOS
    esp_target: Optional[PartitionInfo] = None

    if boot_mode == BootMode.UEFI:
        if args.esp:
            for e in report.esp_partitions:
                if e.device == args.esp:
                    esp_target = e
                    break
            if not esp_target:
                print(f"Error: Specified ESP partition '{args.esp}' not found or invalid.")
                return 1
        else:
            if not report.esp_partitions:
                print("Error: No EFI System Partition (ESP) found. Cannot proceed with UEFI recovery.")
                return 1
            # Prefer ESP on the same disk as Windows, or the first mounted ESP
            same_disk_esp = [e for e in report.esp_partitions if e.disk == windows_target.partition.disk]
            esp_target = same_disk_esp[0] if same_disk_esp else report.esp_partitions[0]
            print(f"Auto-selected ESP partition: {esp_target.device} (on {esp_target.disk})")

    # 3. Mount checks & preparation
    win_mount = windows_target.partition.mount_point
    temp_mounted_win = False
    temp_mounted_esp = False

    if not win_mount:
        temp_win_dir = f"/tmp/redivivus_win_{os.getpid()}"
        os.makedirs(temp_win_dir, exist_ok=True)
        ret, _, _ = scanner._run_cmd(["mount", "-o", "ro", windows_target.partition.device, temp_win_dir])
        if ret != 0:
            print(f"Error: Cannot mount Windows partition {windows_target.partition.device}. Run with sudo.")
            return 1
        win_mount = temp_win_dir
        temp_mounted_win = True

    esp_mount = esp_target.mount_point if esp_target else None
    if esp_target and not esp_mount:
        temp_esp_dir = f"/tmp/redivivus_esp_{os.getpid()}"
        os.makedirs(temp_esp_dir, exist_ok=True)
        ret, _, _ = scanner._run_cmd(["mount", esp_target.device, temp_esp_dir])
        if ret != 0:
            print(f"Error: Cannot mount ESP partition {esp_target.device}. Run with sudo.")
            if temp_mounted_win:
                scanner._run_cmd(["umount", win_mount])
            return 1
        esp_mount = temp_esp_dir
        temp_mounted_esp = True

    try:
        # 4. Generate Recovery Plan
        plan = engine.build_plan(
            windows=windows_target,
            esp=esp_target,
            boot_mode=boot_mode,
            install_fallback=not args.no_fallback,
            update_nvram=not args.no_nvram,
            backup_esp=not args.no_backup,
            dry_run=args.dry_run,
        )

        print("\n## Planned Recovery Actions")
        action_headers = ["Category", "Action Description", "Target / Command"]
        action_rows = [
            [a.category, a.description, a.command or a.target_path or ""]
            for a in plan.actions
        ]
        print(format_markdown_table(action_headers, action_rows))
        print()

        if args.dry_run:
            print("Dry-run preview complete. Exiting without making changes.")
            return 0

        if not args.yes:
            reply = input("Proceed with recovery execution? [y/N]: ").strip().lower()
            if reply not in ("y", "yes"):
                print("Recovery cancelled by user.")
                return 0

        # 5. Execute recovery
        result = engine.execute_recovery(
            plan=plan,
            win_root=Path(win_mount),
            esp_root=Path(esp_mount) if esp_mount else None,
        )

        print("\n## Execution Results")
        for msg in result.messages:
            print(f"  [+] {msg}")
        for err in result.errors:
            print(f"  [!] ERROR: {err}")

        print("\n## Integrity Verification")
        check_headers = ["Check", "Status", "Details"]
        check_rows = [
            [c.name, "PASS" if c.passed else f"FAIL ({c.severity})", c.details]
            for c in result.verification_checks
        ]
        print(format_markdown_table(check_headers, check_rows))
        print()

        if result.success:
            print("[SUCCESS] Windows bootloader restored successfully!")
            print("You may now reboot your system into Windows.")
            return 0
        else:
            print("[FAILURE] Boot recovery encountered errors. Check logs above.")
            return 1

    finally:
        if temp_mounted_win:
            scanner._run_cmd(["umount", win_mount])
            try:
                os.rmdir(win_mount)
            except OSError:
                pass
        if temp_mounted_esp and esp_mount:
            scanner._run_cmd(["umount", esp_mount])
            try:
                os.rmdir(esp_mount)
            except OSError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="redivivus",
        description="Redivivus: Universal Windows Bootloader Recovery Tool from Linux without WinPE.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # scan
    p_scan = subparsers.add_parser("scan", help="Scan storage devices for Windows and ESP partitions")
    p_scan.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p_scan.add_argument("--no-probe", action="store_true", help="Skip probing unmounted partitions")

    # diagnose
    p_diag = subparsers.add_parser("diagnose", help="Diagnose Windows bootability and firmware health")

    # recover
    p_rec = subparsers.add_parser("recover", help="Recover and rebuild Windows bootloader")
    p_rec.add_argument("--win", type=str, help="Target Windows partition device (e.g. /dev/nvme0n1p2)")
    p_rec.add_argument("--esp", type=str, help="Target EFI System Partition (e.g. /dev/nvme0n1p1)")
    p_rec.add_argument("--mode", choices=["AUTO", "UEFI", "BIOS"], default="AUTO", help="Boot mode")
    p_rec.add_argument("--dry-run", action="store_true", help="Preview plan without modifying disk")
    p_rec.add_argument("--no-fallback", action="store_true", help="Skip deploying fallback bootx64.efi")
    p_rec.add_argument("--no-nvram", action="store_true", help="Skip modifying UEFI NVRAM boot entries")
    p_rec.add_argument("--no-backup", action="store_true", help="Skip creating safety pre-flight backup")
    p_rec.add_argument("-y", "--yes", action="store_true", help="Auto-confirm all prompts")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    if args.command == "scan":
        return cmd_scan(args)
    elif args.command == "diagnose":
        return cmd_diagnose(args)
    elif args.command == "recover":
        return cmd_recover(args)

    return 0
