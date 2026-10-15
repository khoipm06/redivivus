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


class TestCli(unittest.TestCase):
    def test_format_markdown_table(self):
        headers = ["Col1", "Col2"]
        rows = [["ValA", "ValB"], ["LongValA", "B"]]
        table = format_markdown_table(headers, rows)
        self.assertIn("| Col1", table)
        self.assertIn("| ValA", table)
        self.assertIn("| LongValA", table)

    def test_format_cli_table_basic(self):
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
        # Multiple tables with completely different column counts and content
        table1_headers = ["Device", "Product", "UEFI", "Legacy", "BCD-Template", "Mount"]
        table1_rows = [["/dev/nvme0n1p2", "Windows (NT)", "Yes", "Yes", "Found", "/mnt/win"]]

        table2_headers = ["Category", "Action Description", "Target / Command"]
        table2_rows = [
            ["NVRAM", "Register and prioritize Windows entry", "efibootmgr -c -d /dev/nvme0n1 -p 1 -L Windows -l \\bootmgfw.efi"]
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

    def test_cli_scan_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["scan", "--json"])
        self.assertEqual(ret, 0)
        output = buf.getvalue()
        data = json.loads(output)
        self.assertIn("firmware_uefi", data)
        self.assertIn("partitions", data)

    def test_cli_scan_cli_format(self):
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

    def test_cli_diagnose(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["diagnose"])
        # Should complete and print report
        self.assertIn(ret, (0, 1))
        self.assertIn("Redivivus", buf.getvalue())
        # Verify no markdown H1/H2
        self.assertNotIn("# Redivivus Bootability", buf.getvalue())

    def test_cli_recover_permission_check(self):
        # Non-root user running recover without dry-run should prompt or fail gracefully
        with patch("os.geteuid", return_value=1000):
            with patch("sys.stdin.isatty", return_value=False):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    ret = main(["recover"])
                self.assertEqual(ret, 1)
                output = buf.getvalue()
                self.assertIn("Root privileges are required", output)
                self.assertIn("sudo", output)

    def test_cli_recover_dry_run_non_root(self):
        # Non-root user running recover WITH --dry-run should succeed
        with patch("os.geteuid", return_value=1000):
            buf = io.StringIO()
            with redirect_stdout(buf):
                ret = main(["recover", "--dry-run"])
            output = buf.getvalue()
            self.assertIn("Dry-run preview complete", output)
            self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
