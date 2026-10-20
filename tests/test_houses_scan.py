"""Scanner errors cannot create a house or trigger secret parking."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import houses


class Scan(unittest.TestCase):
    def test_report_is_used_only_after_scanner_finishes(self):
        for code in (0, 1, 2):
            def scan(*args, **kwargs):
                Path(args[args.index("--report-path") + 1]).write_text(json.dumps([{ "File": "fixture.txt" }]))
                return code, "", "synthetic error"
            with self.subTest(code=code), patch.object(houses, "sh", side_effect=scan):
                if code == 2:
                    with self.assertRaises(SystemExit):
                        houses.flagged_files("/synthetic")
                else:
                    self.assertEqual(houses.flagged_files("/synthetic"), ["fixture.txt"])

    def test_finding_exit_with_empty_report_cannot_become_clean(self):
        def scan(*args, **kwargs):
            Path(args[args.index("--report-path") + 1]).write_text("[]")
            return 1, "", ""
        with patch.object(houses, "sh", side_effect=scan), self.assertRaises(SystemExit):
            houses.flagged_files("/synthetic")

    def test_scanner_failure_before_house_never_parks_or_creates_remote(self):
        with tempfile.TemporaryDirectory(prefix=".siso-ephemeral-house-scan-") as root:
            with patch.object(houses, "sh", return_value=(1, "", "")) as shell, \
                    patch.object(houses, "git", return_value=(0, "", "")), \
                    patch.object(houses, "save_keys", return_value=[]), \
                    patch.object(houses, "repos_under", return_value=[]), \
                    patch.object(houses, "room"), \
                    patch.object(houses, "flagged_files", side_effect=SystemExit("scan failed")), \
                    patch.object(houses, "park_flagged") as park:
                with self.assertRaises(SystemExit):
                    houses.house(root, "fixture", "synthetic", park=True)
                park.assert_not_called()
                self.assertEqual(shell.call_count, 1)  # existence read only, no create/push


if __name__ == "__main__":
    unittest.main()
