"""Completed data generations restore independently; partial uploads never become restore candidates."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("data_retention", Path(__file__).parents[1] / "tools/data-backup.py")
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


class Retention(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".siso-ephemeral-data-retention-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ws = self.root / "workspace"
        self.source = self.ws / "input/source.txt"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("first synthetic generation\n")
        self.remote = self.root / "remote.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.url = "https://github.com/siso-data/siso-data-fixture.git"
        self.plane = {"name": "fixture", "root": str(self.ws), "paths": ["input"], "encrypt": False,
                      "backend": "github:siso-data/siso-data-fixture", "owner": "siso-data"}
        self.real_sh = db.sh
        self.calls = []
        self.fail_completion = False
        self.drop_tag_response = False
        stack = self.enterContext(contextlib.ExitStack())
        for name, value in [("WS", str(self.ws)), ("REPO", str(self.root)), ("WORK", str(self.ws / "repos")),
                            ("CHUNK", 40), ("PUSH_BATCH", 80)]:
            stack.enter_context(patch.object(db, name, value))
        stack.enter_context(patch.object(db, "admit_upload", return_value=(True, "fixture")))
        stack.enter_context(patch.object(db, "release_reservation"))
        stack.enter_context(patch.object(db, "secrets_in", return_value=[]))
        stack.enter_context(patch.object(db, "planes", return_value=[self.plane]))
        stack.enter_context(patch.object(db, "sh", side_effect=self.routed))

    def routed(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == "gh":
            return 0, "true" if args[-1] == ".private" else "", ""
        if self.fail_completion and args[:2] == ("git", "commit") and ": snapshot " in args[-1]:
            return 1, "", "simulated completion failure"
        answer = self.real_sh(*(str(self.remote) if x == self.url else x for x in args), **kwargs)
        if self.drop_tag_response and args[:2] == ("git", "push") and ":refs/tags/" in args[-1]:
            self.assertEqual(answer[0], 0)
            return 1, "", "lost successful response"
        return answer

    def snapshot(self):
        result = db.run_plane(self.plane, "laptop")
        self.assertEqual(result["status"], "pushed", result)
        self.assertTrue(result["remote_verified"])
        return result

    def restore(self, ref=None):
        dest = self.root / ("restored-" + str(len(list(self.root.glob("restored-*")))))
        self.assertEqual(db.cmd_restore("fixture", str(dest), "laptop", ref), 0)
        return (dest / "input/source.txt").read_text()

    def test_two_generations_legacy_ref_and_discovery_restore(self):
        first = self.snapshot()
        subprocess.run(["git", "-C", str(self.remote), "update-ref", "refs/heads/main", first["commit"]], check=True)
        self.source.write_text("second synthetic generation, changed length\n")
        second = self.snapshot()
        self.assertNotEqual(first["ref"], second["ref"])
        self.assertEqual(self.restore(first["ref"]), "first synthetic generation\n")
        self.assertEqual(self.restore(second["ref"]), self.source.read_text())
        self.assertEqual(self.restore(), self.source.read_text())
        self.assertEqual(self.real_sh("git", "rev-parse", "main", cwd=str(self.remote))[1], first["commit"])
        for args in self.calls:
            if args[:2] == ("git", "push"):
                self.assertFalse(any(x in ("-f", "--force", "--force-with-lease") or x.startswith("+") for x in args))
                self.assertNotIn(":main", args[-1])
                self.assertNotIn(":refs/heads/main", args[-1])

    def test_partial_upload_has_no_completion_tag(self):
        self.fail_completion = True
        result = db.run_plane(self.plane, "laptop")
        self.assertEqual(result["status"], "error")
        self.assertIn("commit completion", result["error"])
        self.assertEqual(self.real_sh("git", "for-each-ref", "refs/tags/snapshots", cwd=str(self.remote))[1], "")
        self.assertNotEqual(self.real_sh("git", "for-each-ref", "refs/heads/uploads", cwd=str(self.remote))[1], "")
        with self.assertRaises(SystemExit) as raised:
            db.cmd_restore("fixture", str(self.root / "must-not-restore"))
        self.assertNotEqual(raised.exception.code, 0)
        self.assertFalse((self.root / "must-not-restore").exists())

    def test_receipt_never_skips_an_independently_verified_snapshot(self):
        first = self.snapshot()
        receipt = self.root / "machines/laptop/data-backup.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"results": {"fixture": first}}))
        repeated = self.snapshot()
        self.assertNotEqual(repeated["ref"], first["ref"])
        subprocess.run(["git", "-C", str(self.remote), "update-ref", "-d", first["ref"]], check=True)
        second = self.snapshot()
        self.assertNotEqual(first["ref"], second["ref"])

    def test_lost_successful_tag_response_is_reconciled(self):
        self.drop_tag_response = True
        result = self.snapshot()
        self.assertEqual(self.restore(result["ref"]), self.source.read_text())

    def test_unclassified_large_file_holds_before_unchanged_or_upload(self):
        first = self.snapshot()
        receipt = self.root / "machines/laptop/data-backup.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"results": {"fixture": first}}))
        large = self.source.parent / "private-name.db"
        large.write_bytes(b"synthetic database bytes" * 4)
        self.calls.clear()
        with patch.object(db, "REGEN_MIN", 32):
            result = db.run_plane(self.plane, "laptop")
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["unclassified_large_files"], 1)
        self.assertEqual(result["unclassified_large_bytes"], large.stat().st_size)
        self.assertEqual(result["previous_ref"], first["ref"])
        self.assertNotIn("private-name", json.dumps(result))
        self.assertEqual(self.calls, [])
        receipt.write_text(json.dumps({"results": {"fixture": result}}))
        with patch.object(db, "REGEN_MIN", 32):
            repeated = db.run_plane(self.plane, "laptop")
        self.assertEqual(repeated["previous_ref"], first["ref"])
        self.assertEqual(self.calls, [])
        self.assertEqual(self.restore(first["ref"]), self.source.read_text())

    def test_explicit_large_file_inclusion_restores_the_payload(self):
        large = self.source.parent / "required-database.db"
        payload = b"synthetic required data" * 4
        large.write_bytes(payload)
        self.plane["keep_large"] = ["input"]
        with patch.object(db, "REGEN_MIN", 32):
            result = self.snapshot()
        dest = self.root / "restore-included-large-file"
        self.assertEqual(db.cmd_restore("fixture", str(dest), "laptop", result["ref"]), 0)
        self.assertEqual((dest / "input/required-database.db").read_bytes(), payload)

    def test_omission_hold_preserves_emptied_source_protection(self):
        first = self.snapshot()
        receipt = self.root / "machines/laptop/data-backup.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"results": {"fixture": first}}))
        large = self.source.parent / "synthetic.db"
        large.write_bytes(b"synthetic database" * 4)
        with patch.object(db, "REGEN_MIN", 32):
            held = db.run_plane(self.plane, "laptop")
        self.assertEqual(held["status"], "held")
        receipt.write_text(json.dumps({"results": {"fixture": held}}))
        large.unlink()
        self.source.unlink()
        self.calls.clear()
        empty = db.run_plane(self.plane, "laptop")
        self.assertEqual(empty["status"], "held")
        self.assertEqual(empty["files"], first["files"])
        self.assertEqual(empty["previous_ref"], first["ref"])
        self.assertEqual(self.calls, [])

    def test_first_unclassified_source_disappearing_still_holds(self):
        self.source.unlink()
        large = self.source.parent / "synthetic.db"
        large.write_bytes(b"synthetic database" * 4)
        with patch.object(db, "REGEN_MIN", 32):
            held = db.run_plane(self.plane, "laptop")
        receipt = self.root / "machines/laptop/data-backup.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"results": {"fixture": held}}))
        large.unlink()
        self.calls.clear()
        empty = db.run_plane(self.plane, "laptop")
        self.assertEqual(empty["status"], "held")
        self.assertEqual(self.calls, [])

    def test_plan_names_large_files_unclassified_without_private_paths(self):
        large = self.source.parent / "private-name.db"
        large.write_bytes(b"synthetic database" * 4)
        (self.root / "machines/laptop").mkdir(parents=True)
        output = io.StringIO()
        with patch.object(db, "REGEN_MIN", 32), contextlib.redirect_stdout(output):
            db.cmd_plan("laptop")
        plan = json.loads((self.root / "machines/laptop/data-plan.json").read_text())["planes"][0]
        self.assertEqual(plan["status"], "held")
        self.assertEqual(plan["unclassified_large_files"], 1)
        self.assertNotIn("regenerable", output.getvalue())
        self.assertNotIn("private-name", output.getvalue() + json.dumps(plan))

    def test_discovery_failure_never_falls_back(self):
        self.snapshot()
        with patch.object(db, "sh", return_value=(1, "", "network unavailable")):
            with self.assertRaises(SystemExit) as raised:
                db.cmd_restore("fixture", str(self.root / "must-not-restore"))
        self.assertNotEqual(raised.exception.code, 0)
        self.assertNotIn("network unavailable", str(raised.exception.code))
        self.assertFalse((self.root / "must-not-restore").exists())

    def test_legacy_main_requires_an_explicit_reference(self):
        first = self.snapshot()
        subprocess.run(["git", "-C", str(self.remote), "update-ref", "refs/heads/main", first["commit"]], check=True)
        subprocess.run(["git", "-C", str(self.remote), "update-ref", "-d", first["ref"]], check=True)
        with self.assertRaises(SystemExit):
            self.restore()
        self.assertEqual(self.restore("refs/heads/main"), self.source.read_text())

    def test_explicit_upload_ref_is_rejected(self):
        with self.assertRaises(SystemExit) as raised:
            db.cmd_restore("fixture", str(self.root / "must-not-restore"), "laptop", "refs/heads/uploads/laptop/partial")
        self.assertNotEqual(raised.exception.code, 0)
        self.assertFalse((self.root / "must-not-restore").exists())

    def test_unverified_private_destination_is_held(self):
        routed = self.routed
        def not_private(*args, **kwargs):
            if args[0] == "gh" and args[-1] == ".private":
                return 0, "false", ""
            return routed(*args, **kwargs)
        with patch.object(db, "sh", side_effect=not_private):
            result = db.run_plane(self.plane, "laptop")
        self.assertEqual(result["status"], "held")
        self.assertEqual(self.real_sh("git", "for-each-ref", cwd=str(self.remote))[1], "")

    def test_encrypted_generation_restores_with_owned_fixture_key(self):
        key = self.root / "fixture.key"
        subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
        recipient = subprocess.check_output(["age-keygen", "-y", str(key)], text=True).strip()
        self.plane["encrypt"] = True
        with patch.object(db, "recipient", return_value=recipient), patch.object(db, "KEY", str(key)):
            result = self.snapshot()
            self.assertEqual(self.restore(result["ref"]), self.source.read_text())


if __name__ == "__main__":
    unittest.main()
