#!/usr/bin/env python3
"""umbrella.take_own_pins settles a rebase stopped only on gitlinks (two machines pinned the same repo) with this
machine's pin, and leaves a real file conflict alone (the caller aborts it).

  python3 -m unittest tests/test_umbrella_pins.py -v
"""
import os, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import umbrella

A, B, C = "a" * 40, "b" * 40, "c" * 40


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                          capture_output=True, text=True)


def pin(cwd, sha, msg, extra=None):
    git(cwd, "update-index", "--add", "--cacheinfo", f"160000,{sha},repo")
    if extra:
        with open(os.path.join(cwd, "f.txt"), "w") as f:
            f.write(extra)
        git(cwd, "add", "f.txt")
    git(cwd, "commit", "-qm", msg)


class TakeOwnPins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix=".siso-ephemeral-umbrella.")
        t = self.tmp.name
        self.origin, self.ws, other = (os.path.join(t, n) for n in ("origin.git", "ws", "other"))
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", self.origin])
        subprocess.run(["git", "clone", "-q", self.origin, other], capture_output=True)
        git(other, "checkout", "-qb", "main")
        pin(other, A, "base", "base\n")
        git(other, "push", "-q", "origin", "main")
        subprocess.run(["git", "clone", "-q", self.origin, self.ws], capture_output=True)
        self.other = other
        umbrella.WS = self.ws

    def tearDown(self):
        self.tmp.cleanup()

    def rebase(self):
        r = git(self.ws, "pull", "-q", "--rebase", "-X", "theirs", "origin", "main").returncode
        return umbrella.take_own_pins(r)

    def test_gitlink_conflict_takes_this_machines_pin(self):
        pin(self.other, B, "mini snapshot"); git(self.other, "push", "-q", "origin", "main")
        pin(self.ws, C, "laptop snapshot")
        self.assertEqual(self.rebase(), 0)
        self.assertFalse(os.path.isdir(os.path.join(self.ws, ".git", "rebase-merge")))
        self.assertEqual(git(self.ws, "rev-parse", "HEAD:repo").stdout.strip(), C)
        self.assertEqual(git(self.ws, "log", "--format=%s", "-2").stdout.split(), ["laptop", "snapshot", "mini", "snapshot"])

    def test_file_conflict_is_left_for_the_abort(self):
        pin(self.other, B, "mini", "mini\n"); git(self.other, "push", "-q", "origin", "main")
        git(self.ws, "rm", "-q", "f.txt"); git(self.ws, "commit", "-qm", "laptop removes f")
        r = self.rebase()
        if r != 0:
            self.assertEqual(git(self.ws, "rebase", "--abort").returncode, 0)
        self.assertNotEqual(r, 0)


if __name__ == "__main__":
    unittest.main()
