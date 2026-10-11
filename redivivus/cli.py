"""
Command Line Interface (CLI) for Redivivus: Universal Windows Boot Recovery.
"""

import argparse
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path
from typing import Any, List, Optional

from redivivus import __version__
from redivivus.engine import RecoveryEngine
from redivivus.models import BootMode, PartitionInfo, ScanReport, WindowsInstallation
from redivivus.scanner import SystemScanner

DEFAULT_TABLE_WIDTH = 100


def get_terminal_table_width(default: int = DEFAULT_TABLE_WIDTH, min_width: int = 80, max_width: int = 100) -> int:
    """Returns standardized table width based on terminal size, clamped to [min_width, max_width]."""
    try:
        cols = shutil.get_terminal_size((default, 24)).columns
        return max(min_width, min(cols, max_width))
    except (AttributeError, OSError, ValueError):
        return default


def compute_column_widths(
    headers: List[str],
    rows: List[List[str]],
    target_width: int,
) -> List[int]:
    """Computes column widths so that the total rendered table width exactly matches target_width."""
    n = len(headers)
    if n == 0:
        return []

    # Border overhead: left (1), right (1), (n-1) separators (1 each),
    # plus 2 spaces padding per column (2*n). Total = 1 + 1 + (n - 1) + 2n = 3n + 1.
    border_overhead = 3 * n + 1
    min_content_needed = 3 * n
    target_width = max(target_width, border_overhead + min_content_needed)
    budget = target_width - border_overhead

    natural = []
    min_widths = []
    for i in range(n):
        h_len = len(str(headers[i]))
        max_c = max([len(str(r[i])) for r in rows if i < len(r)], default=0)
        nat = max(h_len, max_c, 3)
        natural.append(nat)
        # Protect compact columns (<= 15 chars) so identifiers do not wrap prematurely
        if nat <= 15:
            min_widths.append(nat)
        else:
            min_widths.append(max(3, min(h_len, 12)))

    # If sum of min_widths exceeds budget, fall back to header length / 3
    if sum(min_widths) > budget:
        min_widths = [max(3, min(len(str(headers[i])), 10)) for i in range(n)]

    total_natural = sum(natural)
    if total_natural <= budget:
        extra = budget - total_natural
        widths = list(natural)
        if extra > 0:
            weights = [max(1, w) for w in natural]
            total_weight = sum(weights)
            floats = [(extra * w) / total_weight for w in weights]
            ints = [int(f) for f in floats]
            remainder = extra - sum(ints)
            remainders = sorted(range(n), key=lambda idx: floats[idx] - ints[idx], reverse=True)
            for idx in remainders[:remainder]:
                ints[idx] += 1
            for idx in range(n):
                widths[idx] += ints[idx]
        return widths
    else:
        min_sum = sum(min_widths)
        if budget <= min_sum:
            return min_widths
        flexible_diffs = [max(0, natural[i] - min_widths[i]) for i in range(n)]
        total_flexible = sum(flexible_diffs)
        avail = budget - min_sum
        widths = list(min_widths)
        if total_flexible > 0:
            floats = [(avail * diff) / total_flexible for diff in flexible_diffs]
            ints = [int(f) for f in floats]
            remainder = avail - sum(ints)
            remainders = sorted(range(n), key=lambda idx: floats[idx] - ints[idx], reverse=True)
            for idx in remainders[:remainder]:
                ints[idx] += 1
            for idx in range(n):
                widths[idx] += ints[idx]
        else:
            for i in range(avail):
                widths[i % n] += 1
        return widths


def format_cli_table(
    headers: List[str],
    rows: List[List[str]],
    alignments: Optional[List[str]] = None,
    target_width: Optional[int] = None,
) -> str:
    """Renders a clean terminal CLI box table with standardized total width.

    Columns automatically stretch or wrap so that every table rendered
    with the same target_width has the exact same total character width.
    """
    if not headers:
        return ""

    n = len(headers)
    if target_width is None:
        target_width = get_terminal_table_width()

    widths = compute_column_widths(headers, rows, target_width)

    if not alignments:
        alignments = ["left"] * n
    elif len(alignments) < n:
        alignments = list(alignments) + ["left"] * (n - len(alignments))

    def align_cell(text: str, width: int, align: str) -> str:
        s = str(text)
        if len(s) > width:
            s = s[:width]
        if align == "center":
            return s.center(width)
        elif align == "right":
            return s.rjust(width)
        return s.ljust(width)

    def wrap_cell(val: Any, width: int) -> List[str]:
        text = str(val) if val is not None else ""
        if not text:
            return [""]
        all_lines: List[str] = []
        for line in text.split("\n"):
            wrapped = textwrap.wrap(line, width=width, break_long_words=True, break_on_hyphens=False)
            all_lines.extend(wrapped or [""])
        return all_lines or [""]

    top_border = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    sep_border = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    bot_border = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"

    out: List[str] = [top_border]

    # Render header
    hdr_wrapped = [wrap_cell(h, widths[i]) for i, h in enumerate(headers)]
    hdr_h = max(len(c) for c in hdr_wrapped)
    for v in range(hdr_h):
        line = "│ " + " │ ".join(
            align_cell(hdr_wrapped[i][v] if v < len(hdr_wrapped[i]) else "", widths[i], alignments[i])
            for i in range(n)
        ) + " │"
        out.append(line)
    out.append(sep_border)

    # Render rows
    for row in rows:
        row_wrapped = [wrap_cell(row[i] if i < len(row) else "", widths[i]) for i in range(n)]
        row_h = max((len(c) for c in row_wrapped), default=1)
        for v in range(row_h):
            line = "│ " + " │ ".join(
                align_cell(row_wrapped[i][v] if v < len(row_wrapped[i]) else "", widths[i], alignments[i])
                for i in range(n)
            ) + " │"
            out.append(line)

    out.append(bot_border)
    return "\n".join(out)


def format_markdown_table(
    headers: List[str],
    rows: List[List[str]],
    alignments: Optional[List[str]] = None,
) -> str:
    """Renders a clean, balanced GitHub-flavored Markdown table.
    
    alignments: Optional list containing 'left', 'center', or 'right' per column.
    """
    if not headers:
        return ""
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(val)))

    # Ensure minimum column width of 3 for nice markdown separators
    col_widths = [max(w, 3) for w in col_widths]

    if not alignments:
        alignments = ["left"] * len(headers)
    elif len(alignments) < len(headers):
        alignments = alignments + ["left"] * (len(headers) - len(alignments))

    def align_cell(text: str, width: int, align: str) -> str:
        s = str(text)
        if align == "center":
            return s.center(width)
        elif align == "right":
            return s.rjust(width)
        return s.ljust(width)

    def sep_cell(width: int, align: str) -> str:
        if align == "center":
            return ":" + "-" * (width - 2) + ":" if width >= 4 else ":-:"
        elif align == "right":
            return "-" * (width - 1) + ":"
        return ":" + "-" * (width - 1)

    header_line = "| " + " | ".join(align_cell(h, col_widths[i], alignments[i]) for i, h in enumerate(headers)) + " |"
    sep_line = "| " + " | ".join(sep_cell(col_widths[i], alignments[i]) for i in range(len(headers))) + " |"
    body_lines = [
        "| " + " | ".join(align_cell(val, col_widths[i], alignments[i]) for i, val in enumerate(row)) + " |"
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

    table_width = get_terminal_table_width()
    banner = "=" * table_width

    print(f"\n{banner}")
    print(f"  Redivivus Hardware & Boot Diagnostic Report (v{__version__})")
    print(f"{banner}\n")
    print(f"  • Firmware Environment : {'UEFI' if report.firmware_uefi else 'BIOS / Legacy'}")
    print(f"  • Detected Boot Mode   : {report.system_boot_mode.value}")
    print(f"  • Total Partitions     : {len(report.partitions)}\n")

    # 1. Windows Installations Table
    print("[ 1. Detected Windows Installations ]")
    if report.windows_installations:
        win_headers = ["Device", "Product", "UEFI", "Legacy", "BCD-Template", "Mount"]
        win_aligns = ["left", "left", "center", "center", "center", "left"]
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
        print(format_cli_table(win_headers, win_rows, win_aligns, target_width=table_width))
    else:
        print("  (No Windows installations detected on accessible block devices.)")
    print()

    # 2. EFI System Partitions (ESP)
    print("[ 2. Detected EFI System Partitions (ESP) ]")
    if report.esp_partitions:
        esp_headers = ["Device", "Disk", "Part #", "FSType", "Label", "Size", "Mount"]
        esp_aligns = ["left", "left", "center", "center", "left", "right", "left"]
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
        print(format_cli_table(esp_headers, esp_rows, esp_aligns, target_width=table_width))
    else:
        print("  (No EFI System Partitions (FAT32/ESP) found.)")
    print()

    # 3. UEFI NVRAM Boot Entries
    if report.firmware_uefi:
        print("[ 3. UEFI NVRAM Boot Entries ]")
        if report.nvram_entries:
            nvram_headers = ["Boot #", "Active", "Label", "File Path", "Is Windows"]
            nvram_aligns = ["left", "center", "left", "left", "center"]
            nvram_rows = []
            for entry in report.nvram_entries:
                nvram_rows.append([
                    f"Boot{entry.boot_num}",
                    "*" if entry.active else "No",
                    entry.label,
                    entry.file_path or "(N/A)",
                    "Yes" if entry.is_windows_manager() else "No",
                ])
            print(format_cli_table(nvram_headers, nvram_rows, nvram_aligns, target_width=table_width))
            if report.current_boot_order:
                print(f"\n  • Boot Order: {' -> '.join(report.current_boot_order)}")
        else:
            print("  (No NVRAM entries reported or efibootmgr not accessible.)")
    print()

    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Performs an in-depth diagnosis of Windows bootability."""
    scanner = SystemScanner()
    report = scanner.run_full_scan(probe_unmounted=True)

    table_width = get_terminal_table_width()
    banner = "=" * table_width

    print(f"\n{banner}")
    print("  Redivivus Bootability Diagnosis")
    print(f"{banner}\n")

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
            recommendations.append("Run 'redivivus recover' to register and prioritize Windows in UEFI NVRAM.")

    if issues:
        print("[ Detected Issues ]")
        for iss in issues:
            print(f"  [!] {iss}")
        print("\n[ Recommendations ]")
        for rec in recommendations:
            print(f"  [*] {rec}")
    else:
        print("  [OK] Diagnosis completed: System partitions appear healthy for Windows boot recovery.")

    return 0 if not issues else 1


def cmd_recover(args: argparse.Namespace) -> int:
    """Executes automated or customized bootloader recovery."""
    table_width = get_terminal_table_width()
    banner = "=" * table_width

    print(f"\n{banner}")
    print("  Redivivus Windows Bootloader Recovery")
    print(f"{banner}\n")

    # Root privilege check: recovery modifies boot partitions and firmware NVRAM
    if not args.dry_run and hasattr(os, "geteuid") and os.geteuid() != 0:
        print("Error: Root privileges required for bootloader recovery. Please run with 'sudo redivivus recover' (or use --dry-run to preview).")
        return 1

    scanner = SystemScanner()
    engine = RecoveryEngine()

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

    # 3. Mount checks & preparation (only mounted if actually executing changes)
    win_mount = windows_target.partition.mount_point
    temp_mounted_win = False
    temp_mounted_esp = False

    if not win_mount and not args.dry_run:
        temp_win_dir = f"/tmp/redivivus_win_{os.getpid()}"
        os.makedirs(temp_win_dir, exist_ok=True)
        ret, _, _ = scanner._run_cmd(["mount", "-o", "ro", windows_target.partition.device, temp_win_dir])
        if ret != 0:
            print(f"Error: Cannot mount Windows partition {windows_target.partition.device}. Run with sudo.")
            return 1
        win_mount = temp_win_dir
        temp_mounted_win = True

    esp_mount = esp_target.mount_point if esp_target else None
    if esp_target and not esp_mount and not args.dry_run:
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

        print("\n[ Planned Recovery Actions ]")
        action_headers = ["Category", "Action Description", "Target / Command"]
        action_aligns = ["center", "left", "left"]
        action_rows = [
            [a.category, a.description, a.command or a.target_path or ""]
            for a in plan.actions
        ]
        print(format_cli_table(action_headers, action_rows, action_aligns, target_width=table_width))
        print()

        if args.dry_run:
            print("Dry-run preview complete. Exiting without making changes.")
            return 0

        if not args.yes:
            reply = input("Proceed with recovery execution? [y/N]: ").strip().lower()
            if reply not in ("y", "yes"):
                print("Recovery cancelled by user.")
                return 0

        if not win_mount:
            print("Error: Windows partition could not be mounted. Cannot proceed.")
            return 1

        # 5. Execute recovery
        result = engine.execute_recovery(
            plan=plan,
            win_root=Path(win_mount),
            esp_root=Path(esp_mount) if esp_mount else None,
        )

        print("\n[ Execution Results ]")
        for msg in result.messages:
            print(f"  [+] {msg}")
        for err in result.errors:
            print(f"  [!] ERROR: {err}")

        print("\n[ Integrity Verification ]")
        check_headers = ["Check", "Status", "Details"]
        check_aligns = ["left", "center", "left"]
        check_rows = [
            [c.name, "PASS" if c.passed else f"FAIL ({c.severity})", c.details]
            for c in result.verification_checks
        ]
        print(format_cli_table(check_headers, check_rows, check_aligns, target_width=table_width))
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
