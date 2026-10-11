import io
import json
import unittest
from contextlib import redirect_stdout

from redivivus.cli import build_parser, format_markdown_table, main


class TestCli(unittest.TestCase):
    def test_format_markdown_table(self):
        headers = ["Col1", "Col2"]
        rows = [["ValA", "ValB"], ["LongValA", "B"]]
        table = format_markdown_table(headers, rows)
        self.assertIn("| Col1", table)
        self.assertIn("| ValA", table)
        self.assertIn("| LongValA", table)

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
            "-y",
        ])
        self.assertEqual(args_rec.command, "recover")
        self.assertEqual(args_rec.win, "/dev/sda2")
        self.assertEqual(args_rec.esp, "/dev/sda1")
        self.assertTrue(args_rec.dry_run)
        self.assertTrue(args_rec.yes)

    def test_cli_scan_json(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["scan", "--json"])
        self.assertEqual(ret, 0)
        output = buf.getvalue()
        data = json.loads(output)
        self.assertIn("firmware_uefi", data)
        self.assertIn("partitions", data)

    def test_cli_diagnose(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["diagnose"])
        # Should complete and print report
        self.assertIn(ret, (0, 1))
        self.assertIn("Redivivus", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
