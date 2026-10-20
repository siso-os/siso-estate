"""Synthetic Git remotes only: generations, failed replacements, and scan gates."""
import os
import json
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
            with patch.object(backup, "MAX_BUNDLE", 1 if mode == "oversize" else backup.MAX_BUNDLE), \
                    patch.object(backup, "held_copy", return_value={"status": "error", "error": "fixture"}):
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

    def test_unknown_destination_is_encrypted_without_creating_repository(self):
        for state, expected in (("pushed", "data-backed"), ("error", "error")):
            with patch.object(backup, "held_copy", return_value={"status": state}) as held:
                result = self.run_item("new-repo")
            self.assertEqual(result["status"], expected, result)
            self.assertFalse(held.call_args.args[5])
            self.assertEqual(self.pushes, [])
            self.scan.assert_not_called()

    def test_recovery_fixture_is_skipped_but_real_source_is_not(self):
        row = {"path": backup.HOME + "/SISO_Workspace/_data/siso-ci-work/tmp/ccrec-fixture/repo"}
        self.assertEqual(backup.classify(row, {}, {})["action"], "skip")
        self.assertFalse(any(rx.search("~/SISO_Workspace/projects/ccrec-product") for rx, _ in backup.SKIP))

    def test_default_command_cwd_survives_replaced_inherited_directory(self):
        old = os.getcwd()
        vanished = self.root / "vanished"
        vanished.mkdir()
        try:
            os.chdir(vanished)
            vanished.rmdir()
            with patch.object(backup, "REPO", str(self.repo)):
                code, out, _ = backup.sh(["git", "rev-parse", "--show-toplevel"])
            self.assertEqual(code, 0)
            self.assertEqual(out, str(self.repo.resolve()))
        finally:
            os.chdir(old)

    def test_renamed_owned_repository_uses_its_canonical_private_destination(self):
        with patch.object(backup, "_GITHUB_ALIASES", {}), \
                patch.object(backup, "sh", return_value=(0, "HALO-AGENCY/halo\ttrue", "")) as api:
            self.assertEqual(backup.resolve_github("HALO-AGENCY", "halo-streaming", {}), ("HALO-AGENCY/halo", "PRIVATE"))
            self.assertEqual(backup.resolve_github("HALO-AGENCY", "halo-streaming", {}), ("HALO-AGENCY/halo", "PRIVATE"))
            self.assertEqual(api.call_count, 1)
        for response in ((1, "", "not found"), (0, "foreign/private\ttrue", "")):
            with patch.object(backup, "_GITHUB_ALIASES", {}), patch.object(backup, "sh", return_value=response):
                self.assertEqual(backup.resolve_github("HALO-AGENCY", "gone", {}), (None, None))

    def test_resume_keeps_completed_receipts_and_retries_latest_unsafe_result(self):
        records = self.root / "records"
        records.mkdir()
        items = [{"path": p, "action": "own-private"} for p in ("/done", "/retry")]
        (records / "backup-plan.json").write_text(json.dumps({"items": items}))
        rows = [{"path": "/done", "status": "pushed", "remote_verified": True},
                {"path": "/retry", "status": "pushed"},
                {"path": "/retry", "status": "held", "held_copy": {"status": "error"}}]
        (records / "backup.log").write_text("".join(json.dumps(r) + "\n" for r in rows))
        with patch.object(backup, "record_path", side_effect=lambda repo, *parts: str(records / parts[-1])), \
                patch.object(backup, "TIPS_FILE", str(records / "tips.json")), \
                patch.object(backup, "load_tips"), \
                patch.object(backup, "run_item", return_value={"path": "/retry", "status": "pushed"}) as execute:
            self.assertEqual(backup.run("fixture", None, 1, False, resume=True), 0)
        self.assertEqual(execute.call_count, 1)
        summary = json.loads((records / "backup.json").read_text())
        self.assertEqual({r["path"] for r in summary["results"]}, {"/done", "/retry"})

    def test_metadata_is_bounded_but_transport_keeps_its_explicit_budget(self):
        with patch.object(backup, "sh", return_value=(0, "", "")) as shell:
            self.real_git("/fixture", "rev-parse", "HEAD")
            self.assertEqual(shell.call_args.kwargs["timeout"], 30)
            self.assertIn("--no-optional-locks", shell.call_args.args[0])
            self.real_git("/fixture", "push", "origin", "main", timeout=3600)
            self.assertEqual(shell.call_args.kwargs["timeout"], 3600)

    def test_worktree_disposition_is_applied_before_inheriting_its_main_repo(self):
        records = self.root / "disposition"
        records.mkdir()
        row = {"path": str(self.repo), "kind": "worktree"}
        key = os.path.relpath(str(self.repo), backup.HOME)
        (records / "names.json").write_text(json.dumps({key: "SKIP verified synthetic fixture"}))
        with patch.object(backup, "record_path", side_effect=lambda repo, *parts: str(records / parts[-1])), \
                patch.object(backup, "load", return_value=({"repos": [row]}, {})), \
                patch.object(backup, "classify", return_value={"action": "new-repo"}):
            result = backup.plan("fixture")
        self.assertEqual(result["items"][0]["action"], "skip")

    def test_unavailable_index_is_not_treated_as_a_directory_to_copy(self):
        with patch.object(backup, "git", return_value=(128, "", "unavailable")), \
                patch.object(backup.shutil, "copyfile") as copy:
            with self.assertRaisesRegex(RuntimeError, "index metadata unavailable"):
                backup.wip_snapshot(str(self.repo), "fixture")
        copy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
