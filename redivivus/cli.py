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
from redivivus.colors import (
    bold,
    bold_cyan,
    bold_green,
    bold_red,
    bold_white,
    bold_yellow,
    cyan,
    dim,
    format_status_badge,
    green,
    red,
    strip_ansi,
    visible_len,
    yellow,
)
from redivivus.engine import RecoveryEngine
from redivivus.models import BootMode, PartitionInfo, ScanReport, WindowsInstallation
from redivivus.scanner import SystemScanner

DEFAULT_TABLE_WIDTH = 100


def is_root() -> bool:
    """Checks if current process has root / UID 0 privileges."""
    return getattr(os, "geteuid", lambda: 0)() == 0


def elevate_privileges() -> bool:
    """Attempts to re-execute the current process with root privileges via sudo or pkexec."""
    sudo_bin = shutil.which("sudo") or shutil.which("pkexec")
    if not sudo_bin:
        print(bold_red("\n[!] Error: Neither 'sudo' nor 'pkexec' found in system PATH."))
        print("    Please run the recovery command as root:")
        print(f"    sudo {sys.executable} -m redivivus " + " ".join(sys.argv[1:]))
        return False

    orig_args = sys.argv[1:]
    if sys.argv[0].endswith("__main__.py") or (not os.path.isabs(sys.argv[0]) and "redivivus" in sys.argv[0]):
        re_exec = [sudo_bin, sys.executable, "-m", "redivivus"] + orig_args
    elif sys.argv[0].endswith(".py"):
        re_exec = [sudo_bin, sys.executable, sys.argv[0]] + orig_args
    else:
        re_exec = [sudo_bin, sys.argv[0]] + orig_args

    print(cyan(f"\n[*] Elevating privileges with {os.path.basename(sudo_bin)}..."))
    try:
        os.execvp(sudo_bin, re_exec)
    except OSError as e:
        print(bold_red(f"\n[!] Failed to elevate privileges via {sudo_bin}: {e}"))
        print("    Please re-run the command directly with root privileges.")
        return False
    return True


def ensure_root_privileges(args: argparse.Namespace) -> bool:
    """Ensures root privileges are present, prompting to elevate or auto-elevating if -y."""
    if is_root():
        return True

    if getattr(args, "yes", False):
        return elevate_privileges()

    if not sys.stdin.isatty():
        print(bold_red("\n[!] Error: Root privileges are required to perform bootloader recovery."))
        print("    Please re-run with 'sudo' or specify '-y' to elevate automatically.")
        return False

    print(bold_yellow("\n[!] Root privileges are required to modify EFI bootloader files."))
    try:
        reply = input("Elevate privileges using sudo? [Y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nOperation cancelled.")
        return False

    if reply in ("", "y", "yes"):
        return elevate_privileges()

    print(bold_red("\nRecovery cancelled: Root privileges are required to write EFI files."))
    return False


def get_terminal_table_width(default: int = DEFAULT_TABLE_WIDTH, min_width: int = 80, max_width: int = 100) -> int:
    """Returns standardized table width based on terminal size, clamped to [min_width, max_width]."""
    try:
        cols = shutil.get_terminal_size((default, 24)).columns
        return max(min_width, min(cols, max_width))
    except (AttributeError, OSError, ValueError):
        return default


def compute_column_widths(
    headers: List[str],
    rows: List[List[Any]],
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
        h_len = visible_len(headers[i])
        max_c = max([visible_len(str(r[i])) for r in rows if i < len(r)], default=0)
        nat = max(h_len, max_c, 3)
        natural.append(nat)
        # Protect compact columns (<= 15 chars) so identifiers do not wrap prematurely
        if nat <= 15:
            min_widths.append(nat)
        else:
            min_widths.append(max(3, min(h_len, 12)))

    # If sum of min_widths exceeds budget, fall back to header length / 3
    if sum(min_widths) > budget:
        min_widths = [max(3, min(visible_len(headers[i]), 10)) for i in range(n)]

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
    rows: List[List[Any]],
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
        v_len = visible_len(text)
        if v_len > width:
            text = text[:width]
            v_len = visible_len(text)
        pad = max(0, width - v_len)
        if align == "center":
            left = pad // 2
            right = pad - left
            return " " * left + text + " " * right
        elif align == "right":
            return " " * pad + text
        return text + " " * pad

    def wrap_cell(val: Any, width: int, is_header: bool = False) -> List[str]:
        text = str(val) if val is not None else ""
        if not text:
            return [""]
        if not is_header:
            badge = format_status_badge(text)
            if visible_len(badge) <= width:
                return [badge]
        plain = strip_ansi(text)
        all_lines: List[str] = []
        for line in plain.split("\n"):
            wrapped = textwrap.wrap(line, width=width, break_long_words=True, break_on_hyphens=False)
            all_lines.extend(wrapped or [""])
        return all_lines or [""]

    top_border = dim("┌─" + "─┬─".join("─" * w for w in widths) + "─┐")
    sep_border = dim("├─" + "─┼─".join("─" * w for w in widths) + "─┤")
    bot_border = dim("└─" + "─┴─".join("─" * w for w in widths) + "─┘")
    pipe = dim("│")

    out: List[str] = [top_border]

    # Render header
    hdr_wrapped = [wrap_cell(h, widths[i], is_header=True) for i, h in enumerate(headers)]
    hdr_h = max(len(c) for c in hdr_wrapped)
    for v in range(hdr_h):
        line = f"{pipe} " + f" {pipe} ".join(
            align_cell(bold_cyan(hdr_wrapped[i][v]) if v < len(hdr_wrapped[i]) else "", widths[i], alignments[i])
            for i in range(n)
        ) + f" {pipe}"
        out.append(line)
    out.append(sep_border)

    # Render rows
    for row in rows:
        row_wrapped = [wrap_cell(row[i] if i < len(row) else "", widths[i]) for i in range(n)]
        row_h = max((len(c) for c in row_wrapped), default=1)
        for v in range(row_h):
            line = f"{pipe} " + f" {pipe} ".join(
                align_cell(row_wrapped[i][v] if v < len(row_wrapped[i]) else "", widths[i], alignments[i])
                for i in range(n)
            ) + f" {pipe}"
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

    print(bold_cyan(f"\n{banner}"))
    print(bold_cyan(f"  Redivivus Hardware & Boot Diagnostic Report (v{__version__})"))
    print(bold_cyan(f"{banner}\n"))
    print(f"  {bold('• Firmware Environment')} : {cyan('UEFI') if report.firmware_uefi else yellow('BIOS / Legacy')}")
    print(f"  {bold('• Detected Boot Mode')}   : {cyan(report.system_boot_mode.value)}")
    print(f"  {bold('• Total Partitions')}     : {len(report.partitions)}\n")

    # 1. Windows Installations Table
    print(bold_cyan("[ 1. Detected Windows Installations ]"))
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
        print(dim("  (No Windows installations detected on accessible block devices.)"))
    print()

    # 2. EFI System Partitions (ESP)
    print(bold_cyan("[ 2. Detected EFI System Partitions (ESP) ]"))
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
        print(dim("  (No EFI System Partitions (FAT32/ESP) found.)"))
    print()

    # 3. UEFI NVRAM Boot Entries
    if report.firmware_uefi:
        print(bold_cyan("[ 3. UEFI NVRAM Boot Entries ]"))
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
                print(f"\n  {bold('• Boot Order')}: {cyan(' -> '.join(report.current_boot_order))}")
        else:
            print(dim("  (No NVRAM entries reported or efibootmgr not accessible.)"))
        print()

    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    """Performs an in-depth diagnosis of Windows bootability."""
    scanner = SystemScanner()
    report = scanner.run_full_scan(probe_unmounted=True)

    table_width = get_terminal_table_width()
    banner = "=" * table_width

    print(bold_cyan(f"\n{banner}"))
    print(bold_cyan("  Redivivus Bootability Diagnosis"))
    print(bold_cyan(f"{banner}\n"))

    issues: List[str] = []
    warnings: List[str] = []
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
            warnings.append("Warning: No 'Windows Boot Manager' NVRAM entry found in UEFI firmware.")
            recommendations.append("Run 'redivivus recover' to register and prioritize Windows in UEFI NVRAM.")

    if not shutil.which("hivexsh"):
        warnings.append("Warning: 'hivexsh' tool is not found in PATH.")
        recommendations.append("Install 'hivex' (e.g. 'sudo dnf install hivex' or 'sudo apt install libhivex-bin') for advanced BCD editing.")

    if report.firmware_uefi and not shutil.which("efibootmgr"):
        warnings.append("Warning: 'efibootmgr' tool is not found in PATH.")
        recommendations.append("Install 'efibootmgr' to allow updating UEFI NVRAM boot entries.")

    if issues:
        print(bold_red("[ Detected Issues ]"))
        for iss in issues:
            print(f"  {bold_red('[!]')} {iss}")

    if warnings:
        print(bold_yellow("[ System Warnings ]"))
        for w in warnings:
            print(f"  {bold_yellow('[?]')} {w}")

    if recommendations:
        print(bold_yellow("\n[ Recommendations ]"))
        for rec in recommendations:
            print(f"  {bold_yellow('[*]')} {rec}")

    if not issues:
        print(bold_green("  [+] Diagnosis completed: System partitions appear healthy for Windows boot recovery."))

    print()
    return 0 if not issues else 1


def cmd_recover(args: argparse.Namespace) -> int:
    """Executes automated or customized bootloader recovery."""
    table_width = get_terminal_table_width()
    banner = "=" * table_width

    print(bold_cyan(f"\n{banner}"))
    print(bold_cyan("  Redivivus Windows Bootloader Recovery"))
    print(bold_cyan(f"{banner}\n"))

    # Root privilege check: recovery modifies boot partitions and firmware NVRAM
    if not args.dry_run and not is_root():
        if not ensure_root_privileges(args):
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
            print(bold_red(f"Error: Specified Windows partition '{args.win}' not found or invalid."))
            return 1
    else:
        if not report.windows_installations:
            print(bold_red("Error: No Windows installations detected on this machine."))
            return 1
        elif len(report.windows_installations) == 1:
            windows_target = report.windows_installations[0]
            print(f"  {bold('Auto-selected Windows installation')}: {windows_target.partition.device} ({windows_target.product_name})")
        else:
            print(bold_yellow("Multiple Windows installations detected. Please select via `--win <device>`:"))
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
                print(bold_red(f"Error: Specified ESP partition '{args.esp}' not found or invalid."))
                return 1
        else:
            if not report.esp_partitions:
                print(bold_red("Error: No EFI System Partition (ESP) found. Cannot proceed with UEFI recovery."))
                return 1
            # Prefer ESP on the same disk as Windows, or the first mounted ESP
            same_disk_esp = [e for e in report.esp_partitions if e.disk == windows_target.partition.disk]
            esp_target = same_disk_esp[0] if same_disk_esp else report.esp_partitions[0]
            print(f"  {bold('Auto-selected ESP partition')}:        {esp_target.device} (on {esp_target.disk})")

    # 3. Mount checks & preparation (only mounted if actually executing changes)
    win_mount = windows_target.partition.mount_point
    temp_mounted_win = False
    temp_mounted_esp = False

    if not win_mount and not args.dry_run:
        temp_win_dir = f"/tmp/redivivus_win_{os.getpid()}"
        os.makedirs(temp_win_dir, exist_ok=True)
        mount_opts = "rw" if boot_mode == BootMode.BIOS else "ro"
        ret, _, _ = scanner._run_cmd(["mount", "-o", mount_opts, windows_target.partition.device, temp_win_dir])
        if ret != 0:
            print(bold_red(f"Error: Cannot mount Windows partition {windows_target.partition.device}. Run with sudo."))
            return 1
        win_mount = temp_win_dir
        temp_mounted_win = True

    esp_mount = esp_target.mount_point if esp_target else None
    if esp_target and not esp_mount and not args.dry_run:
        temp_esp_dir = f"/tmp/redivivus_esp_{os.getpid()}"
        os.makedirs(temp_esp_dir, exist_ok=True)
        ret, _, _ = scanner._run_cmd(["mount", esp_target.device, temp_esp_dir])
        if ret != 0:
            print(bold_red(f"Error: Cannot mount ESP partition {esp_target.device}. Run with sudo."))
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
            force_fallback=getattr(args, "force_fallback", False),
            update_nvram=not args.no_nvram,
            set_first=getattr(args, "set_first", False),
            backup_esp=not args.no_backup,
            dry_run=args.dry_run,
        )

        print(bold_cyan("\n[ Planned Recovery Actions ]"))
        action_headers = ["Category", "Action Description", "Target / Command"]
        action_aligns = ["center", "left", "left"]
        action_rows = [
            [a.category, a.description, a.command or a.target_path or ""]
            for a in plan.actions
        ]
        print(format_cli_table(action_headers, action_rows, action_aligns, target_width=table_width))
        print()

        if args.dry_run:
            print(green("Dry-run preview complete. Exiting without making changes."))
            return 0

        if not args.yes:
            reply = input("Proceed with recovery execution? [y/N]: ").strip().lower()
            if reply not in ("y", "yes"):
                print(yellow("Recovery cancelled by user."))
                return 0

        if not win_mount:
            print(bold_red("Error: Windows partition could not be mounted. Cannot proceed."))
            return 1

        # 5. Execute recovery
        try:
            result = engine.execute_recovery(
                plan=plan,
                win_root=Path(win_mount),
                esp_root=Path(esp_mount) if esp_mount else None,
            )
        except PermissionError as pe:
            print(bold_red(f"\n[!] Permission denied during recovery: {pe}"))
            print("    Please run with root/sudo privileges.")
            return 1
        except Exception as ex:
            print(bold_red(f"\n[!] Unexpected error during recovery execution: {ex}"))
            return 1

        print(bold_cyan("\n[ Execution Results ]"))
        for msg in result.messages:
            print(f"  {bold_green('[+]')} {msg}")
        for err in result.errors:
            print(f"  {bold_red('[!] ERROR:')} {err}")

        print(bold_cyan("\n[ Integrity Verification ]"))
        check_headers = ["Check", "Status", "Details"]
        check_aligns = ["left", "center", "left"]
        check_rows = [
            [c.name, "PASS" if c.passed else f"FAIL ({c.severity})", c.details]
            for c in result.verification_checks
        ]
        print(format_cli_table(check_headers, check_rows, check_aligns, target_width=table_width))
        print()

        if result.success:
            print(bold_green("[SUCCESS] Windows bootloader restored successfully!"))
            print("You may now reboot your system into Windows.\n")
            return 0
        else:
            print(bold_red("[FAILURE] Boot recovery encountered errors. Check logs above.\n"))
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
    p_rec.add_argument("--win", type=str, help="Target Windows partition device (e.g. /dev/sda2)")
    p_rec.add_argument("--esp", type=str, help="Target EFI System Partition (e.g. /dev/sda1)")
    p_rec.add_argument("--mode", choices=["AUTO", "UEFI", "BIOS"], default="AUTO", help="Boot mode")
    p_rec.add_argument("--dry-run", action="store_true", help="Preview plan without modifying disk")
    p_rec.add_argument("--no-fallback", action="store_true", help="Skip deploying fallback bootx64.efi")
    p_rec.add_argument("--force-fallback", action="store_true", help="Force overwrite of existing non-Windows fallback bootloader (e.g. GRUB/shim)")
    p_rec.add_argument("--no-nvram", action="store_true", help="Skip modifying UEFI NVRAM boot entries")
    p_rec.add_argument("--set-first", action="store_true", help="Prioritize Windows in UEFI NVRAM boot order (default: False to preserve GRUB)")
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
