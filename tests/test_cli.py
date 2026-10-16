import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from redivivus.cli import (
    build_parser,
    format_cli_table,
    format_markdown_table,
    get_terminal_table_width,
    main,
)
from redivivus.models import (
    BootMode,
    EfiBootEntry,
    PartitionInfo,
    PartitionRole,
    ScanReport,
    WindowsInstallation,
)
from redivivus.scanner import SystemScanner


"""
User Story 6 (US6) - CLI Interface, Pretty Printing & Safety Guards
Verify command-line parsing, terminal table formatting, JSON serialization,
and root privilege enforcement for non-destructive operations.
"""


def _create_mock_scan_report(has_windows: bool = True, has_esp: bool = True, uefi: bool = True) -> ScanReport:
    """Creates a deterministic synthetic scan report representing universal hardware."""
    partitions = []
    esp_partitions = []
    win_installations = []
    nvram_entries = []

    if has_esp:
        esp = PartitionInfo(
            device="/dev/sda1",
            disk="/dev/sda",
            part_num=1,
            fstype="vfat",
            label="ESP",
            uuid="A1B2-C3D4",
            part_uuid="c12a7328-f81f-11d2-ba4b-00a0c93ec93b",
            size_bytes=536870912,
            size_human="512.0M",
            mount_point="/boot/efi",
            is_mounted=True,
            is_esp=True,
            pttype="gpt",
            role=PartitionRole.ESP,
            disk_guid="a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
            partition_offset=1048576,
        )
        partitions.append(esp)
        esp_partitions.append(esp)

    if has_windows:
        win_part = PartitionInfo(
            device="/dev/sda2",
            disk="/dev/sda",
            part_num=2,
            fstype="ntfs",
            label="Windows 11",
            uuid="4E1A2B3C4D5E6F70",
            part_uuid="550e8400-e29b-41d4-a716-446655440000",
            size_bytes=107374182400,
            size_human="100.0G",
            mount_point="/mnt/windows",
            is_mounted=True,
            pttype="gpt",
            role=PartitionRole.WINDOWS_OS,
            disk_guid="a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
            partition_offset=1074790400,
        )
        partitions.append(win_part)
        win_inst = WindowsInstallation(
            partition=win_part,
            product_name="Windows 11",
            has_efi_boot_files=True,
            has_pcat_boot_files=True,
            has_bcd_template=True,
            winload_efi_present=True,
            winload_exe_present=True,
        )
        win_installations.append(win_inst)

    if uefi:
        nvram_entries = [
            EfiBootEntry(
                boot_num="0001",
                label="Linux Boot Manager",
                active=True,
                file_path=r"\EFI\systemd\systemd-bootx64.efi",
            ),
            EfiBootEntry(
                boot_num="0002",
                label="Windows Boot Manager",
                active=True,
                file_path=r"\EFI\Microsoft\Boot\bootmgfw.efi",
            ),
        ]

    return ScanReport(
        firmware_uefi=uefi,
        system_boot_mode=BootMode.UEFI if uefi else BootMode.BIOS,
        partitions=partitions,
        windows_installations=win_installations,
        esp_partitions=esp_partitions,
        nvram_entries=nvram_entries,
        current_boot_order=["0001", "0002"] if uefi else [],
    )


class TestCli(unittest.TestCase):
    def test_format_markdown_table(self):
        """US6-AC2: Format standard Markdown pipe table for reports and logs."""
        headers = ["Col1", "Col2"]
        rows = [["ValA", "ValB"], ["LongValA", "B"]]
        table = format_markdown_table(headers, rows)
        self.assertIn("| Col1", table)
        self.assertIn("| ValA", table)
        self.assertIn("| LongValA", table)

    def test_format_cli_table_basic(self):
        """US6-AC2: Format Unicode box-drawing tables with exact column borders."""
        headers = ["Col1", "Col2"]
        rows = [["ValA", "ValB"], ["LongValA", "B"]]
        target_width = 80
        table = format_cli_table(headers, rows, target_width=target_width)
        lines = table.split("\n")
        self.assertTrue(lines[0].startswith("┌"))
        self.assertTrue(lines[-1].startswith("└"))
        for line in lines:
            self.assertEqual(len(line), target_width)
        self.assertIn("ValA", table)
        self.assertIn("LongValA", table)

    def test_format_cli_table_uniform_total_width(self):
        """US6-AC2: Ensure consistent terminal table width across disparate schemas."""
        # Multiple tables with completely different column counts and content
        table1_headers = ["Device", "Product", "UEFI", "Legacy", "BCD-Template", "Mount"]
        table1_rows = [["/dev/sda2", "Windows (NT)", "Yes", "Yes", "Found", "/mnt/win"]]

        table2_headers = ["Category", "Action Description", "Target / Command"]
        table2_rows = [
            ["NVRAM", "Register and prioritize Windows entry", "efibootmgr -c -d /dev/sda -p 1 -L Windows -l \\bootmgfw.efi"]
        ]

        table3_headers = ["Check", "Status", "Details"]
        table3_rows = [["Windows Boot Manager Binary", "PASS", "Present at /boot/efi (150000 bytes)"]]

        for target_w in [80, 96, 100]:
            t1 = format_cli_table(table1_headers, table1_rows, target_width=target_w)
            t2 = format_cli_table(table2_headers, table2_rows, target_width=target_w)
            t3 = format_cli_table(table3_headers, table3_rows, target_width=target_w)

            # Verify every line in all 3 tables has the exact same total width
            for line in t1.split("\n"):
                self.assertEqual(len(line), target_w)
            for line in t2.split("\n"):
                self.assertEqual(len(line), target_w)
            for line in t3.split("\n"):
                self.assertEqual(len(line), target_w)

    def test_format_cli_table_multiline_wrapping(self):
        headers = ["Col1", "Col2"]
        long_text = "This is a very long string that should wrap gracefully across multiple visual lines inside the cell."
        rows = [["Short", long_text]]
        target_width = 50
        table = format_cli_table(headers, rows, target_width=target_width)
        lines = table.split("\n")
        # Should wrap into multiple lines, but every single line must strictly equal target_width
        self.assertGreater(len(lines), 4)
        for line in lines:
            self.assertEqual(len(line), target_width)

    def test_get_terminal_table_width(self):
        w = get_terminal_table_width()
        self.assertGreaterEqual(w, 80)
        self.assertLessEqual(w, 100)

    def test_parser_options(self):
        parser = build_parser()

        args = parser.parse_args(["scan", "--json"])
        self.assertEqual(args.command, "scan")
        self.assertTrue(args.json)

        args_rec = parser.parse_args([
            "recover",
            "--win",
            "/dev/sda2",
            "--esp",
            "/dev/sda1",
            "--mode",
            "UEFI",
            "--dry-run",
            "--force-fallback",
            "--set-first",
            "-y",
        ])
        self.assertEqual(args_rec.command, "recover")
        self.assertEqual(args_rec.win, "/dev/sda2")
        self.assertEqual(args_rec.esp, "/dev/sda1")
        self.assertTrue(args_rec.dry_run)
        self.assertTrue(args_rec.force_fallback)
        self.assertTrue(args_rec.set_first)
        self.assertTrue(args_rec.yes)

        args_def = parser.parse_args(["recover"])
        self.assertFalse(args_def.force_fallback)
        self.assertFalse(args_def.set_first)

    @patch.object(SystemScanner, "run_full_scan", return_value=_create_mock_scan_report())
    def test_cli_scan_json(self, _mock_scan):
        """US6-AC2: CLI scan --json outputs valid JSON structure from universal scan report."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["scan", "--json"])
        self.assertEqual(ret, 0)
        output = buf.getvalue()
        data = json.loads(output)
        self.assertTrue(data["firmware_uefi"])
        self.assertEqual(len(data["partitions"]), 2)
        self.assertEqual(data["partitions"][0]["device"], "/dev/sda1")
        self.assertEqual(data["partitions"][1]["device"], "/dev/sda2")

    @patch.object(SystemScanner, "run_full_scan", return_value=_create_mock_scan_report())
    def test_cli_scan_cli_format(self, _mock_scan):
        """US6-AC2: CLI scan prints clean terminal tables with box borders on universal mock."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["scan"])
        self.assertEqual(ret, 0)
        output = buf.getvalue()
        # Verify no markdown H1/H2 headers or markdown separator bars
        self.assertNotIn("# Redivivus Hardware", output)
        self.assertNotIn("## 1.", output)
        self.assertNotIn("| :---", output)
        # Verify pure CLI banners and box characters
        self.assertIn("Redivivus Hardware & Boot Diagnostic Report", output)
        self.assertIn("┌", output)
        self.assertIn("└", output)
        self.assertIn("/dev/sda2", output)
        self.assertIn("/dev/sda1", output)

    @patch.object(SystemScanner, "run_full_scan", return_value=_create_mock_scan_report())
    def test_cli_diagnose_healthy(self, _mock_scan):
        """US6-AC3: CLI diagnose returns 0 when all boot components are healthy."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["diagnose"])
        self.assertEqual(ret, 0)
        self.assertIn("Redivivus", buf.getvalue())
        self.assertIn("Diagnosis completed", buf.getvalue())
        self.assertNotIn("# Redivivus Bootability", buf.getvalue())

    @patch.object(SystemScanner, "run_full_scan", return_value=_create_mock_scan_report(has_windows=False))
    def test_cli_diagnose_missing_windows(self, _mock_scan):
        """US6-AC3: CLI diagnose detects missing Windows and exits with error code 1."""
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["diagnose"])
        self.assertEqual(ret, 1)
        self.assertIn("No Windows installation was detected", buf.getvalue())

    @patch.object(SystemScanner, "run_full_scan", return_value=_create_mock_scan_report())
    def test_cli_recover_permission_check(self, _mock_scan):
        """US6-AC4: Non-root user running recover without dry-run is blocked."""
        with patch("os.geteuid", return_value=1000):
            with patch("sys.stdin.isatty", return_value=False):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ret = main(["recover"])
                self.assertEqual(ret, 1)
                output = buf.getvalue()
                self.assertIn("Root privileges are required", output)
                self.assertIn("sudo", output)

    @patch.object(SystemScanner, "run_full_scan", return_value=_create_mock_scan_report())
    def test_cli_recover_dry_run_non_root(self, _mock_scan):
        """US6-AC4: Non-root user running recover --dry-run previews plan safely."""
        with patch("os.geteuid", return_value=1000):
            buf = io.StringIO()
            with redirect_stdout(buf):
                ret = main(["recover", "--dry-run"])
            output = buf.getvalue()
            self.assertIn("Dry-run preview complete", output)
            self.assertEqual(ret, 0)

    @patch.object(SystemScanner, "run_full_scan", return_value=_create_mock_scan_report(has_windows=False))
    def test_cli_recover_no_windows_detected(self, _mock_scan):
        """US6-AC4: Recovery aborts gracefully with 1 if no Windows installation exists."""
        with patch("os.geteuid", return_value=0):
            buf = io.StringIO()
            with redirect_stdout(buf):
                ret = main(["recover", "--dry-run"])
            output = buf.getvalue()
            self.assertIn("No Windows installations detected", output)
            self.assertEqual(ret, 1)


if __name__ == "__main__":
    unittest.main()
