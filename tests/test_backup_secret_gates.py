"""A backup may leave only after its exact source passes the secret scanner."""
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools"))
import backup


class ScannerReport(unittest.TestCase):
    def scan(self, code, report):
        def scanner(args, **kwargs):
            path = args[args.index("--report-path") + 1]
            pathlib.Path(path).write_text(report)
            return code, "", "synthetic diagnostic must not enter a receipt"
        with patch.object(backup, "sh", side_effect=scanner):
            return backup.gitleaks_commits("/synthetic-repo", "-1 HEAD")

    def test_invalid_and_inconsistent_reports_are_errors(self):
        for code, report in [(0, ""), (0, "{broken"), (0, "{}"), (3, "[]"),
                             (3, '[{"RuleID":"fixture","Commit":"abc"}]'),
                             (0, '[{"File":"a.txt","RuleID":"fixture","Commit":"abc"}]')]:
            with self.subTest(code=code, report=report):
                findings, error = self.scan(code, report)
                self.assertIsNone(findings)
                self.assertTrue(error)

    def test_scanner_failure_does_not_return_its_raw_diagnostic(self):
        findings, error = self.scan(2, "[]")
        self.assertIsNone(findings)
        self.assertTrue(error)
        self.assertNotIn("synthetic diagnostic", error)

    def test_valid_clean_and_finding_reports(self):
        self.assertEqual(self.scan(0, "[]"), ([], None))
        findings, error = self.scan(3, '[{"File":"a.txt","RuleID":"fixture","Commit":"1234567890abcdef"}]')
        self.assertIsNone(error)
        self.assertEqual(findings, [{"file": "a.txt", "rule": "fixture", "commit": "1234567890"}])

    def test_launch_error_and_timeout_return_sanitized_errors(self):
        for failure in (FileNotFoundError("synthetic diagnostic"),
                        subprocess.TimeoutExpired(["synthetic diagnostic"], 1)):
            with self.subTest(kind=type(failure).__name__), patch.object(backup, "sh", side_effect=failure):
                findings, error = backup.gitleaks_commits("/synthetic-repo", "-1 HEAD")
                self.assertIsNone(findings)
                self.assertTrue(error)
                self.assertNotIn("synthetic diagnostic", error)


class BackupSecretBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".siso-ephemeral-secret-gate-")
        self.addCleanup(self.temp.cleanup)
        self.repo = pathlib.Path(self.temp.name, "source")
        self.remote = pathlib.Path(self.temp.name, "remote.git")
        self.repo.mkdir()
        self.git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "source.txt").write_text("base\n")
        (self.repo / ".gitleaks.toml").write_text(
            "title = 'synthetic fixture'\n[[rules]]\nid = 'synthetic-only'\n"
            "description = 'noncredential regression sentinel'\n"
            "regex = '''ASTRA_SYNTHETIC_SECRET_[A-Z]+'''\n")
        self.git(self.repo, "add", "source.txt", ".gitleaks.toml")
        self.git(self.repo, "commit", "-qm", "base")
        self.git(self.repo, "init", "-q", "--bare", str(self.remote))
        self.git(self.repo, "remote", "add", "origin", str(self.remote))
        self.git(self.repo, "push", "-q", "origin", "main")
        (self.repo / "source.txt").write_text("draft\n")
        self.item = {"path": str(self.repo), "kind": "worktree", "action": "own-private",
                     "target": "sisodias/fixture", "slug": "fixture", "live_remotes": ["origin"]}
        self.real_git = backup.git
        self.pushes = []

    def git(self, root, *args):
        result = subprocess.run(["git", "-C", str(root), "-c", "user.name=fixture",
                                 "-c", "user.email=fixture@example.invalid", *args],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def routed_git(self, root, *args, **kwargs):
        if "push" in args:
            self.pushes.append(args)
        routed = [str(self.remote) if a == "https://github.com/sisodias/fixture.git" else a for a in args]
        return self.real_git(root, *routed, **kwargs)

    def run_backup(self):
        with patch.object(backup, "git", side_effect=self.routed_git), \
             patch.object(backup, "related", return_value=True):
            return backup.run_item(self.item, "fixture", False)

    def assert_no_transport(self, result):
        self.assertEqual(result["status"], "held", result)
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.git(self.remote, "for-each-ref", "refs/backup"), "")

    def test_commit_scan_error_stops_before_wip_or_encrypted_fallback(self):
        self.item["kind"] = "repo"
        head = self.git(self.repo, "rev-parse", "HEAD")
        for error in ("unavailable", "timeout"):
            with self.subTest(error=error), \
                 patch.object(backup, "unpushed_branches", return_value=[("main", head)]), \
                 patch.object(backup, "gitleaks_commits", return_value=(None, error)), \
                 patch.object(backup, "wip_snapshot", wraps=backup.wip_snapshot) as snapshot, \
                 patch.object(backup, "held_copy", return_value={"status": "synthetic-only"}) as fallback:
                self.pushes.clear()
                self.assert_no_transport(self.run_backup())
                snapshot.assert_not_called()
                fallback.assert_not_called()

    def test_wip_scan_errors_are_held_without_push(self):
        for error in ("unavailable", "timeout"):
            with self.subTest(error=error), patch.object(backup, "gitleaks_commits", return_value=(None, error)):
                self.pushes.clear()
                self.assert_no_transport(self.run_backup())

    def test_staged_flagged_content_cannot_escape_through_filtered_rebuild(self):
        (self.repo / "flagged.txt").write_text("ASTRA_SYNTHETIC_SECRET_SENTINEL\n")
        self.git(self.repo, "add", "flagged.txt")
        index = (self.repo / ".git/index").read_bytes()
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.assert_no_transport(self.run_backup())
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual((self.repo / "flagged.txt").read_text(), "ASTRA_SYNTHETIC_SECRET_SENTINEL\n")

    def test_filtered_snapshot_scanner_error_stops_transport(self):
        (self.repo / "flagged.txt").write_text("ASTRA_SYNTHETIC_SECRET_SENTINEL\n")
        scans = [([{"file": "flagged.txt", "rule": "synthetic-only", "commit": "fixture"}], None),
                 (None, "invalid-report")]
        with patch.object(backup, "gitleaks_commits", side_effect=scans):
            self.assert_no_transport(self.run_backup())

    def test_filtered_snapshot_itself_passes_a_real_scan_before_push(self):
        (self.repo / "flagged.txt").write_text("ASTRA_SYNTHETIC_SECRET_SENTINEL\n")
        real_scan = backup.gitleaks_commits
        passed = set()
        def observed_scan(path, opts):
            findings, error = real_scan(path, opts)
            if findings == [] and error is None:
                passed.add(opts.split()[-1])
            return findings, error
        with patch.object(backup, "gitleaks_commits", side_effect=observed_scan):
            result = self.run_backup()
        self.assertEqual(result["status"], "pushed", result)
        sha, ref = result["refs"][0].split(":", 1)
        self.assertIn(sha, passed, "the uploaded snapshot did not itself pass a scan")
        self.assertNotIn("flagged.txt", self.git(self.remote, "ls-tree", "-r", "--name-only", ref).splitlines())
        self.assertEqual(self.git(self.remote, "show", ref + ":source.txt"), "draft")
        self.assertTrue((self.repo / "flagged.txt").exists())


if __name__ == "__main__":
    unittest.main()
