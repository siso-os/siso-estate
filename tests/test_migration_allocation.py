"""Allocation measurement must never accept partial du totals."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
import json
import tempfile
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / ".agents/runs/storage-20261016-astra/migration-build-exception.py"
spec = importlib.util.spec_from_file_location("migration_monitor", SCRIPT)
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)

class Allocation(unittest.TestCase):
    def test_transient_retry_requires_complete_totals(self):
        paths = [monitor.ROOT / row[0] for row in monitor.JOBS.values()] + [monitor.OUT]
        missing = SimpleNamespace(returncode=1, stdout="", stderr="du: fixture: No such file or directory\n")
        complete = SimpleNamespace(returncode=0, stdout="".join("1\t"+str(p)+"\n" for p in paths), stderr="")
        with patch.object(monitor.subprocess, "run", side_effect=[missing, complete]), patch.object(monitor.time, "sleep"), patch.object(Path, "exists", return_value=True):
            self.assertEqual(monitor.allocated(), 4096)

    def test_measurement_failures_hold(self):
        missing = SimpleNamespace(returncode=1, stdout="", stderr="du: fixture: No such file or directory\n")
        denied = SimpleNamespace(returncode=1, stdout="", stderr="du: fixture: Permission denied\n")
        partial = SimpleNamespace(returncode=0, stdout="1\tpartial\n", stderr="")
        for responses in ([missing] * 3, [denied], [partial]):
            with self.subTest(responses=responses), patch.object(monitor.subprocess, "run", side_effect=responses), patch.object(monitor.time, "sleep"), patch.object(Path, "exists", return_value=True):
                with self.assertRaises(RuntimeError):
                    monitor.allocated()

    def test_measurement_failure_before_child_and_after_exit(self):
        with tempfile.TemporaryDirectory(prefix=".siso-ephemeral-monitor-") as directory:
            out = Path(directory)
            (out / "baseline.json").write_text('{"allocated_bytes":0}')
            with patch.object(monitor, "OUT", out), patch.object(monitor.sys, "argv", ["monitor", "workspace"]), patch.object(monitor, "free", return_value=100_000_000_000), patch.object(monitor, "allocated", side_effect=RuntimeError("unavailable")), patch.object(monitor.subprocess, "Popen") as launch:
                with self.assertRaises(RuntimeError):
                    monitor.main()
                launch.assert_not_called()
            child = SimpleNamespace(pid=123, poll=lambda: 0, returncode=0)
            with patch.object(monitor, "OUT", out), patch.object(monitor.sys, "argv", ["monitor", "workspace"]), patch.object(monitor, "free", return_value=100_000_000_000), patch.object(monitor, "allocated", side_effect=[0, RuntimeError("unavailable")]), patch.object(monitor.subprocess, "Popen", return_value=child):
                self.assertEqual(monitor.main(), 75)
            receipt = json.loads((out / "workspace.json").read_text())
            self.assertEqual(receipt["state"], "HOLD-measurement-unavailable")
            self.assertIsNone(receipt["measured_build_bytes"])

if __name__ == "__main__":
    unittest.main()
