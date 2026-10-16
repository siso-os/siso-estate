import contextlib
import io
import sys
import unittest
from unittest.mock import patch

from tools import refresh


class RefreshModesTest(unittest.TestCase):
    def invoke(self, args, failures=()):
        calls = []

        def fake_run(name, *cmd):
            calls.append((name, cmd))
            return int(len(calls) in failures)

        output = io.StringIO()
        with patch.object(sys, "argv", ["refresh.py", *args]), patch.object(refresh, "run", fake_run):
            with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                refresh.main()
        return calls, raised.exception.code, output.getvalue()

    def test_maps_only_runs_two_mapping_steps(self):
        calls, code, _ = self.invoke(["--maps-only"])
        self.assertEqual(calls, [
            ("doors", ("doors.py", "--commit")),
            ("laptop map", ("laptop-map.py", "--machine", "laptop")),
        ])
        self.assertEqual(code, 0)

    def test_light_and_default_sequences_are_preserved(self):
        light, _, _ = self.invoke(["--light"])
        default, _, _ = self.invoke([])
        self.assertEqual(len(light), 9)
        self.assertEqual([name for name, _ in light], [
            "doors", "laptop map", "servers", "building code", "law", "link repair",
            "law stats", "register", "live pages",
        ])
        self.assertEqual(len(default), 14)
        self.assertEqual([name for name, _ in default], [
            "inventory", "dots", "doors", "laptop map", "servers", "building code", "law",
            "link repair", "law stats", "register", "live pages", "umbrella", "census", "building code",
        ])

    def test_non_laptop_default_skips_laptop_maintenance(self):
        calls, _, _ = self.invoke(["--machine", "mini"])
        self.assertEqual([name for name, _ in calls], ["inventory", "dots", "doors", "laptop map", "umbrella", "census", "building code"])
        self.assertTrue(all("--machine" in cmd for name, cmd in calls if name in ("inventory", "dots", "laptop map", "umbrella", "census")))

    def test_failure_continues_and_returns_failure(self):
        calls, code, _ = self.invoke(["--maps-only"], failures=(1,))
        self.assertEqual(len(calls), 2)
        self.assertEqual(code, 1)

    def test_conflicting_modes_reject_before_running_any_step(self):
        called = []
        with patch.object(sys, "argv", ["refresh.py", "--light", "--maps-only"]), patch.object(
            refresh, "run", lambda *args: called.append(args) or 0
        ), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            refresh.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
