import importlib.util
import fcntl
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


trend = module("storage-trend")
guard = module("storage-guard")
mapper = module("storage-map")


class StorageTrendTest(unittest.TestCase):
    def test_runtime_jsonl_is_daily_idempotent_and_measures_labeled_growth(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "machines", "mini", "storage-trend.jsonl")
            trend.record_daily(path, "mini", 100 * 2**30, {"worktrees": 10 * 2**30}, now=0)
            trend.record_daily(path, "mini", 99 * 2**30, {"worktrees": 22 * 2**30}, cleared_gib=11, now=100)
            row = trend.record_daily(path, "mini", 80 * 2**30, {"worktrees": 30 * 2**30}, now=86401)
            self.assertEqual(len(Path(path).read_text().splitlines()), 2)
            self.assertEqual(row["growers"][0]["path"], "worktrees")
            self.assertEqual(row["change_24h_gib"], -20.0)

    def test_other_job_removing_temp_after_glob_does_not_abort_guard(self):
        rule = {'id': 'fixture', 'paths': ['fixture'], 'keep_newest': 2}
        with patch.object(guard, 'expand', return_value=['already-removed']), patch.object(guard.os, 'lstat', side_effect=FileNotFoundError):
            self.assertEqual(guard.candidates(rule, set(), time.time()), [])

    def test_copied_map_uses_measured_time_instead_of_file_mtime(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'tree.json'
            p.write_text(json.dumps({'t': 100}))
            self.assertTrue(mapper.weekly_due(str(p), now=100 + 8 * 86400))
            self.assertFalse(mapper.weekly_due(str(p), now=100 + 86400))

    def test_concurrent_cleanup_stops_before_opening_or_deleting_files(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / '.local/state/estate'
            state.mkdir(parents=True)
            with (state / 'storage-guard.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                with patch.object(guard, 'HOME', d), patch.object(guard.sys, 'argv', ['storage-guard.py', '--run', '--floor-gib', '0']), patch.object(guard, 'open_paths') as paths, patch.object(guard, 'orphan_automation_chrome') as orphan, patch('sys.stdout', new_callable=io.StringIO):
                    self.assertEqual(guard.main(), 0)
                    paths.assert_not_called()
                    orphan.assert_not_called()

    def test_idle_browser_checks_continue_between_hourly_cleanups(self):
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / '.local/state/estate'
            state.mkdir(parents=True)
            (state / 'storage-guard-last-cleanup').write_text(str(time.time()))
            receipt = Path(d) / 'runtime/guard.jsonl'
            row = {'action': 'TERM', 'pid': 123, 'age_hours': 2, 'cpu': 90}
            with patch.object(guard, 'HOME', d), patch.object(guard, 'RECEIPTS', str(receipt)), patch.object(guard.sys, 'argv', ['storage-guard.py', '--run', '--floor-gib', '0', '--cleanup-interval', '3600']), patch.object(guard, 'open_paths') as paths, patch.object(guard, 'orphan_automation_chrome', return_value=[row]) as orphan, patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(guard.main(), 0)
                orphan.assert_called_once()
                self.assertTrue(orphan.call_args.args[0])
                paths.assert_not_called()
            self.assertEqual(json.loads(receipt.read_text())['pid'], 123)

    def test_recent_cleared_uses_summary_or_tool_gain_once(self):
        with tempfile.NamedTemporaryFile(mode="w") as f:
            f.write(json.dumps({"at": "2026-10-20T10:00:00", "du_kib": 1024}) + "\n")
            f.write(json.dumps({"at": "2026-10-20T10:00:01", "du_kib": 1024, "rule": "item"}) + "\n")
            f.write(json.dumps({"at": "2026-10-20T10:00:02", "free_gain_gib": 2}) + "\n")
            f.flush()
            self.assertAlmostEqual(trend.recent_cleared(f.name, now=time.mktime(time.strptime("2026-10-20T10:01:00", "%Y-%m-%dT%H:%M:%S"))), 2.0)

    def test_daily_line_uses_24h_free_change_top_three_and_cleared(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "trend.json")
            trend.update("mini", 90 * 2**30, now=0, path=path)
            trend.update("mini", 89 * 2**30, now=100, path=path)
            state = trend.update("mini", 75 * 2**30, [
                {"path": "a", "grew_gib": 4.0}, {"path": "b", "grew_gib": 3.0},
                {"path": "c", "grew_gib": 2.0}, {"path": "d", "grew_gib": 1.0}],
                cleared_gib=11.5, now=86401, path=path)
            self.assertEqual(state["change_24h_gib"], -15.0)
            self.assertEqual([g["path"] for g in state["growers"]], ["a", "b", "c"])
            self.assertIn("24h -15.0 GiB", state["line"])
            self.assertIn("cleared 11.5 GiB", state["line"])

    def test_orphan_chrome_requires_parent_one_marker_age_and_low_cpu(self):
        ps = "\n".join([
            "10 1 0.0 01:01:00 /Chrome --user-data-dir=/tmp/puppeteer_dev_chrome_profile",
            "11 1 90.0 02:00:00 /Chrome --remote-debugging-port=9222",
            "12 2 0.0 04:00:00 /Chrome --remote-debugging-port=9223",
            "13 1 0.0 00:30:00 /Chrome --remote-debugging-port=9224",
            "14 1 0.0 02:00:00 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ])
        commands = {"10": "/Chrome --user-data-dir=/tmp/puppeteer_dev_chrome_profile", "11": "/Chrome --remote-debugging-port=9222"}
        def probe(*args, **kwargs):
            if args[:2] == ("ps", "-axo"):
                return ps
            if args[-1] == "comm=": return "/Chrome"
            if args[-1] == "lstart=": return "stable process start"
            if args[-1] == "ppid=,command=": return "1 " + commands[args[2]]
            return ""
        state = {}
        with patch.object(guard, "sh", side_effect=probe), patch.object(guard.os, "kill") as kill:
            first = guard.orphan_automation_chrome(True, now=10000, state=state)
            self.assertEqual([r["action"] for r in first], ["observing idle", "observing idle"])
            kill.assert_not_called()
            rows = guard.orphan_automation_chrome(True, now=13601, state=state)
            self.assertEqual([r["pid"] for r in rows], [10, 11])
            self.assertEqual(kill.call_count, 2)

    def test_active_debugging_client_is_held(self):
        with patch.object(guard, "sh", side_effect=["n127.0.0.1:9222", "10"]):
            self.assertTrue(guard.automation_has_client(10))
        with patch.object(guard, "sh", side_effect=["n127.0.0.1:9222", ""]):
            self.assertFalse(guard.automation_has_client(10))
        with patch.object(guard, "sh", return_value=None):
            self.assertTrue(guard.automation_has_client(10))



if __name__ == "__main__":
    unittest.main()
