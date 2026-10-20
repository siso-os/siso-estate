"""Synthetic Git remotes only: generations, failed replacements, and scan gates."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import backup


class Retention(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix=".siso-ephemeral-retention-")))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.remote = self.root / "remote.git"
        self.git("init", "-q", "-b", "main")
        (self.repo / "source.txt").write_text("base\n")
        self.git("add", "source.txt")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base")
        subprocess.run(["git", "init", "-q", "--bare", str(self.remote)], check=True)
        self.enterContext(patch.object(backup, "WS", str(self.root)))
        self.enterContext(patch.object(backup, "_TIPS", {}))
        self.enterContext(patch.object(backup, "related", return_value=True))
        self.scan = self.enterContext(patch.object(backup, "gitleaks_commits", return_value=([], None)))
        self.real_git = backup.git
        self.pushes = []
        self.mode = None
        self.enterContext(patch.object(backup, "git", side_effect=self.route))

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def route(self, path, *args, **kwargs):
        if "push" in args:
            self.pushes.append(args)
            args = tuple(str(self.remote) if a.startswith("https://github.com/") else a for a in args)
        if args[:1] == ("ls-remote",):
            args = (args[0], str(self.remote), *args[2:])
            if self.mode == "mismatch" and self.pushes:
                return 0, "", ""
        if args[:2] == ("bundle", "create"):
            if self.mode == "bundle-error":
                Path(args[2]).write_bytes(b"incomplete candidate")
                return 1, "", "synthetic failure"
            if self.mode == "timeout":
                Path(args[2]).write_bytes(b"incomplete candidate")
                raise subprocess.TimeoutExpired(["git", "bundle"], 1)
        return self.real_git(path, *args, **kwargs)

    def run_item(self, action="own-private"):
        item = {"path": str(self.repo), "action": action, "kind": "repo",
                "target": "fixture/repo", "slug": "fixture", "live_remotes": []}
        return backup.run_item(item, "fixture", False)

    def test_divergent_wip_generations_remain_on_remote_and_index_is_untouched(self):
        index = (self.repo / ".git/index").read_bytes()
        results = []
        for text in ("first\n", "second\n"):
            (self.repo / "source.txt").write_text(text)
            result = self.run_item()
            self.assertEqual(result["status"], "pushed", result)
            self.assertTrue(result["remote_verified"])
            results.append(result)
            self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertNotEqual(results[0]["refs"], results[1]["refs"])
        for result, text in zip(results, ("first\n", "second\n")):
            spec = next(s for s in result["refs"] if s.endswith("/wip"))
            sha, ref = spec.split(":", 1)
            observed = subprocess.check_output(["git", "-C", str(self.remote), "rev-parse", ref], text=True).strip()
            self.assertEqual(observed, sha)
            self.assertEqual(subprocess.check_output(["git", "-C", str(self.remote), "show", ref + ":source.txt"], text=True), text)
        self.assertTrue(all(not s.startswith("+") for result in results for s in result["refs"]))

    def test_remote_mismatch_is_error_and_never_records_pushed_tips(self):
        self.mode = "mismatch"
        result = self.run_item()
        self.assertEqual(result["status"], "error", result)
        self.assertFalse(result["remote_verified"])
        self.assertNotIn("pushed_tips", result)

    def test_overlay_retains_old_generation_and_failed_replacement_keeps_latest(self):
        first = self.run_item("overlay")
        self.assertEqual(first["status"], "bundled", first)
        latest = Path(first["bundle"])
        original = latest.read_bytes()
        (self.repo / "source.txt").write_text("second\n")
        second = self.run_item("overlay")
        self.assertEqual(second["status"], "bundled", second)
        self.assertEqual(Path(second["retained_bundle"]).read_bytes(), original)
        current = latest.read_bytes()
        for mode in ("bundle-error", "timeout", "oversize"):
            self.mode = mode
            with patch.object(backup, "MAX_BUNDLE", 1 if mode == "oversize" else backup.MAX_BUNDLE):
                result = self.run_item("overlay")
            self.assertEqual(result["status"], "error", result)
            self.assertEqual(latest.read_bytes(), current)
            self.assertFalse(list(latest.parent.glob("*.pending-*")))
            self.assertEqual(self.git("for-each-ref", "refs/estate-tmp"), "")

    def test_scan_execution_failure_holds_all_transport(self):
        self.scan.return_value = (None, "synthetic scanner failure")
        with patch.object(backup, "held_copy", return_value={"status": "error"}) as held:
            result = self.run_item()
        self.assertEqual(result["status"], "held", result)
        held.assert_not_called()
        self.assertEqual(self.pushes, [])


if __name__ == "__main__":
    unittest.main()
