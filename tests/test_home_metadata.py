import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import home_inventory
import home_metadata


class HomeMetadataTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.run = self.base / "run"
        self.run.mkdir()
        self.protected = self.home / "private"
        self.protected.mkdir()
        (self.run / "scope.json").write_text(json.dumps({"halo_prefixes": [str(self.protected)]}))
        self.inventory = self.run / "inventory-all.sqlite"
        self.coverage = self.run / "coverage.sqlite"
        with sqlite3.connect(self.inventory) as db:
            db.executescript(home_inventory.SCHEMA)
            db.execute("INSERT INTO scan_info VALUES ('started_at','2026-10-17T00:00:00+0700')")
            db.execute("INSERT INTO scan_info VALUES ('roots',?)", (str(self.home),))
            entries = [
                (str(self.home / "docs"), None, None, None, 1, "directory", None, None, None, "unassessed", None, None, None, None, None, None, None),
                (str(self.home / "docs" / "alias"), None, None, None, None, "symlink", None, None, "symlink recorded", "unassessed", None, None, None, None, None, str(self.home / "target"), None),
                (str(self.home / "broken"), None, None, None, None, "error", None, None, "directory listing failed", "unassessed", None, None, None, None, None, None, "PermissionError"),
                (str(self.protected / "secret"), 8, 8, None, None, "file", None, None, None, "unassessed", None, None, None, None, None, None, None),
            ]
            for row in entries:
                db.execute("""INSERT INTO entries(path,size,allocated_bytes,mtime_ns,is_directory,kind,nearest_git_root,exclusion,reason,backup_status,github_location,what_it_is,destination_guess,metadata_location,git_kind,link_target,error)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
        with sqlite3.connect(self.coverage) as db:
            db.execute("""CREATE TABLE coverage(path TEXT PRIMARY KEY,status TEXT,source_bytes INTEGER,sha256 TEXT,source_stat TEXT,snapshot_url TEXT,run_id TEXT,note TEXT)""")
            db.execute("INSERT INTO coverage VALUES (?,?,?,?,?,?,?,?)", (
                str(self.home / "docs"), "verified-bytes", 123, "abc123", "{}", "https://example.invalid/tree", "run-7", None))

    def tearDown(self):
        self.temp.cleanup()

    def read_export(self, **kwargs):
        output = self.base / "metadata" / "home.jsonl.gz"
        receipt = home_metadata.export(self.inventory, self.coverage, self.run, output,
                                       home=self.home, free_floor=0, **kwargs)
        with gzip.open(output, "rt", encoding="utf-8") as stream:
            rows = [json.loads(line) for line in stream]
        return output, receipt, rows

    def test_protected_paths_filtered_and_paths_are_relative(self):
        output, receipt, rows = self.read_export()
        text = gzip.open(output, "rt", encoding="utf-8").read()
        self.assertNotIn(str(self.protected), text)
        self.assertNotIn("secret", text)
        self.assertEqual(rows[0]["relative_path_fields"], ["path", "relative_home_path", "nearest_git_root", "metadata_location"])
        self.assertEqual(rows[0]["inventory_scan_info"]["roots"], ["."])
        records = [row for row in rows if row["record"] == "entry"]
        self.assertEqual({row["path"] for row in records}, {"docs", "docs/alias", "broken"})
        self.assertEqual(receipt["filtered_protected_rows"], 1)
        self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(output.parent).st_mode & 0o777, 0o700)

    def test_coverage_join_and_incomplete_error_and_symlink_truth(self):
        _, receipt, rows = self.read_export()
        header = rows[0]
        self.assertFalse(header["inventory_complete_at_export"])
        self.assertTrue(header["coverage_is_partial"])
        self.assertFalse(header["full_reconstruction_claimed"])
        entries = {row["path"]: row for row in rows[1:]}
        self.assertEqual(entries["docs"]["coverage"]["sha256"], "abc123")
        self.assertEqual(entries["docs"]["coverage"]["run_id"], "run-7")
        self.assertEqual(entries["docs/alias"]["link_target"], str(self.home / "target"))
        self.assertEqual(entries["broken"]["error"], "PermissionError")
        self.assertEqual(receipt["error_rows"], 1)

    def test_output_cap_fails_without_replacing_existing_file(self):
        target = self.base / "metadata" / "home.jsonl.gz"
        target.parent.mkdir(mode=0o700)
        target.write_bytes(b"prior")
        with self.assertRaises(OverflowError):
            home_metadata.export(self.inventory, self.coverage, self.run, target,
                                 home=self.home, free_floor=0, output_cap=20)
        self.assertEqual(target.read_bytes(), b"prior")


if __name__ == "__main__":
    unittest.main()
