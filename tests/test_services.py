#!/usr/bin/env python3
"""services.py (goal A11) against a thrown-together LaunchAgents folder: clean, then each kind of drift.

  python3 -m unittest tests/test_services.py -v
"""
import io, json, os, plistlib, shutil, sys, tempfile, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import services


class Services(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        services.AGENTS = os.path.join(self.tmp, "LaunchAgents")
        services.SOURCES = os.path.join(self.tmp, "launchd")
        services.MANIFEST = os.path.join(self.tmp, "services.json")
        os.makedirs(services.AGENTS)
        self.prog = os.path.join(self.tmp, "job.sh")
        open(self.prog, "w").write("#!/bin/sh\n")
        with open(os.path.join(services.AGENTS, "com.siso.demo.plist"), "wb") as f:
            plistlib.dump({"Label": "com.siso.demo", "ProgramArguments": ["/bin/sh", self.prog]}, f)
        json.dump({"services": []}, open(services.MANIFEST, "w"))
        services.loaded = lambda: {"com.siso.demo": (None, "0")}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_check(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = services.check()
        return rc, out.getvalue()

    def test_unknown_job_then_adopted_is_clean(self):
        rc, out = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("not in the manifest", out)
        with redirect_stdout(io.StringIO()):
            services.adopt("com.siso.demo", "ESTATE", "a demo", True)
        self.assertEqual(self.run_check()[0], 0)

    def test_changed_copy_and_gone_program_are_drift(self):
        with redirect_stdout(io.StringIO()):
            services.adopt("com.siso.demo", "ESTATE", "a demo", True)
        with open(os.path.join(services.AGENTS, "com.siso.demo.plist"), "ab") as f:
            f.write(b"\n")
        rc, out = self.run_check()
        self.assertEqual(rc, 1)
        self.assertIn("differs from its source", out)
        os.unlink(self.prog)
        self.assertIn("its program is gone", self.run_check()[1])

    def test_a_path_with_spaces_is_one_program(self):
        p = {"ProgramArguments": ["/usr/bin/open", "-a", "/Applications/Some App.app"]}
        self.assertEqual(services.program_of(p), "/Applications/Some App.app")
        p = {"ProgramArguments": ["/bin/bash", "-c", "/Users/x/bin/tool --flag || osascript -e 'x'"]}
        self.assertEqual(services.program_of(p), "/Users/x/bin/tool")


if __name__ == "__main__":
    unittest.main()
