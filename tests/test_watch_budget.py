import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
TOOLS = pathlib.Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import inventory
spec = importlib.util.spec_from_file_location("estate_watch", TOOLS / "watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)

class WatchBudgetTests(unittest.TestCase):
    def test_quick_describe_preserves_counts_and_never_walks_git(self):
        with tempfile.TemporaryDirectory() as td:
            (pathlib.Path(td)/".git").mkdir()
            with patch.object(inventory, "git", side_effect=["main", "abc"]) as git, patch.object(inventory.subprocess, "run", side_effect=AssertionError("expensive subprocess")):
                row=inventory.describe(td, quick=True, previous={"dirty":4,"untracked":9,"git_kb":88})
            self.assertEqual(row["dirty"],4)
            self.assertEqual(row["head"],"abc")
            self.assertEqual(git.call_count,2)
    def test_git_disables_optional_locks(self):
        with patch.object(inventory.subprocess,"run") as run:
            run.return_value.returncode=0;run.return_value.stdout="main"
            inventory.git("/tmp/repo","rev-parse","HEAD")
            self.assertIn("--no-optional-locks",run.call_args.args[0])
    def test_scope_accepts_directory_refs_and_drops_build_noise(self):
        watch.pending.clear();watch.watched_gitdirs.clear();watch.repo_signatures.clear()
        watch.watched_gitdirs["/repo/.git"]="/repo"
        watch.on_event("/repo/node_modules/x")
        watch.on_event("/repo/.git/objects/ab")
        self.assertFalse(watch.pending)
        with patch.object(watch, "repo_signature", return_value=("changed-head",)):
            watch.on_event("/repo/.git/refs/heads")
        self.assertEqual(set(watch.pending),{"/repo"})
    def test_rotation_retains_bytes_and_archives_old_logs(self):
        import os,time
        with tempfile.TemporaryDirectory() as td, patch.object(watch,"LOG",td+"/watch.jsonl"),patch.object(watch,"LOG_BYTES",3):
            p=pathlib.Path(watch.LOG);p.write_text("abcd")
            watch.rotate_log();rotated=list(p.parent.glob("watch.jsonl.*"))
            self.assertEqual(len(rotated),1);self.assertEqual(rotated[0].read_text(),"abcd")
            os.utime(rotated[0],(0,0));watch.rotate_log(now=8*86400)
            self.assertEqual(len(list((p.parent/"_archive/watch").iterdir())),1)
    def test_worktree_signature_changes_without_git_process(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);dot=root/"actual";dot.mkdir();(dot/"HEAD").write_text("ref: refs/heads/main")
            wt=root/"worktree";wt.mkdir();(wt/".git").write_text("gitdir: ../actual")
            first=watch.repo_signature(str(wt));(dot/"HEAD").write_text("abc")
            self.assertNotEqual(first,watch.repo_signature(str(wt)))
    def test_remote_fetch_does_not_invalidate_each_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);dot=root/".git";(dot/"refs/heads").mkdir(parents=True);(dot/"refs/remotes/origin").mkdir(parents=True)
            (dot/"HEAD").write_text("ref: refs/heads/main");(dot/"refs/heads/main").write_text("abc")
            first=watch.repo_signature(td)
            (dot/"refs/remotes/origin/main").write_text("new-remote")
            self.assertEqual(first,watch.repo_signature(td))
            (dot/"refs/heads/main").write_text("new-local")
            self.assertNotEqual(first,watch.repo_signature(td))
    def test_nightly_probe_budget_caps_git_and_skips_du(self):
        with tempfile.TemporaryDirectory() as td:
            (pathlib.Path(td)/".git").mkdir()
            with patch.object(inventory,"git",return_value="0") as git, patch.object(inventory.subprocess,"run",side_effect=AssertionError("du must not run")):
                inventory.describe(td,git_timeout=2,sizes=False)
            self.assertGreaterEqual(git.call_count,7)
            self.assertTrue(all(c.kwargs["timeout"]==2 for c in git.call_args_list))
    def test_unknown_inventory_counts_are_not_clean_backup_evidence(self):
        import backup
        row={"path":"/fixture-repo","kind":"repo","dirty":0,"untracked":0,"unpushed_commits":None,
             "remotes":{"origin":"https://github.com/sisodias/fixture.git"},
             "github":[{"remote":"origin","owner":"sisodias","repo":"fixture"}]}
        with patch.object(backup,"live_remote_names",return_value=["origin"]):
            result=backup.classify(row,{}, {"fixture":{"name":"fixture","visibility":"PRIVATE"}})
        self.assertEqual(result["action"],"own-private")

if __name__ == "__main__": unittest.main()
