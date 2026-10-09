#!/usr/bin/env python3
"""backup.held_copy keeps a held checkout's disk-only work off the machine, age-encrypted, and re-uploads only when it
changes; backup.alert makes a held checkout loud (card + A0 inbox line when it is not backed up).

  python3 -m unittest tests/test_backup_held.py -v
"""
import json, os, shutil, subprocess, sys, tempfile, unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import backup


def git(cwd, *a):
    r = subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *a], capture_output=True, text=True)
    return r.stdout.strip()


class HeldCopy(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        subprocess.run(["age-keygen", "-o", f"{self.d}/key"], capture_output=True, check=True)
        pub = subprocess.run(["age-keygen", "-y", f"{self.d}/key"], capture_output=True, text=True).stdout
        open(f"{self.d}/recipients", "w").write(pub)
        self.remote = f"{self.d}/held-remote.git"
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        self._saved = (backup.HELD_URL, backup.HELD_GIT, backup.AGE_RECIPIENTS)
        backup.HELD_URL, backup.HELD_GIT, backup.AGE_RECIPIENTS = self.remote, f"{self.d}/local.git", f"{self.d}/recipients"
        self.addCleanup(lambda: setattr(backup, "HELD_URL", self._saved[0]) or setattr(backup, "HELD_GIT", self._saved[1])
                        or setattr(backup, "AGE_RECIPIENTS", self._saved[2]))
        self.repo = f"{self.d}/repo"
        os.makedirs(self.repo)
        git(self.repo, "init", "-q", "-b", "main")
        open(f"{self.repo}/a.txt", "w").write("token=not-a-real-one\n")
        git(self.repo, "add", "a.txt")
        git(self.repo, "commit", "-qm", "one")
        git(self.repo, "branch", "side")

    def restore(self, ref):
        out = f"{self.d}/out"
        shutil.rmtree(out, ignore_errors=True)
        subprocess.run(["git", "clone", "-q", "--branch", ref, self.remote, out], check=True)
        parts = sorted(p for p in os.listdir(out) if p.startswith("work.bundle.age."))
        enc = b"".join(open(f"{out}/{p}", "rb").read() for p in parts)
        dec = subprocess.run(["age", "-d", "-i", f"{self.d}/key"], input=enc, capture_output=True, check=True).stdout
        open(f"{self.d}/w.bundle", "wb").write(dec)
        return subprocess.run(["git", "bundle", "list-heads", f"{self.d}/w.bundle"], capture_output=True, text=True).stdout

    def test_push_unchanged_then_changed(self):
        r = backup.held_copy(self.repo, "m", "slug", [], None, create=False)
        self.assertEqual(r["status"], "pushed", r)
        heads = self.restore("m/slug")
        self.assertIn("refs/heads/main", heads)
        self.assertIn("refs/heads/side", heads)
        self.assertNotIn(b"token=", open(f"{self.d}/out/work.bundle.age.000", "rb").read())   # ciphertext only
        self.assertEqual(backup.held_copy(self.repo, "m", "slug", [], None, create=False)["status"], "unchanged")
        open(f"{self.repo}/b.txt", "w").write("more\n")
        git(self.repo, "add", "b.txt")
        git(self.repo, "commit", "-qm", "two")
        r2 = backup.held_copy(self.repo, "m", "slug", [], None, create=False)
        self.assertEqual(r2["status"], "pushed", r2)
        self.assertNotEqual(r2["commit"], r["commit"])
        self.assertEqual(git(self.repo, "for-each-ref", "refs/estate-tmp"), "")   # temp refs cleaned up

    def test_nothing_to_keep_when_remote_has_it(self):
        git(self.repo, "remote", "add", "origin", self.remote)
        git(self.repo, "push", "-q", "origin", "main", "side")
        git(self.repo, "fetch", "-q", "origin")
        self.assertEqual(backup.held_copy(self.repo, "m", "s2", ["origin"], None, create=False)["status"], "nothing-to-keep")


class Alert(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        self.m = "test-alert-" + os.path.basename(self.d)
        self.rec = os.path.join(backup.REPO, "machines", self.m)
        os.makedirs(self.rec)
        self.addCleanup(shutil.rmtree, self.rec)
        self.inbox, self.cardfile = f"{self.d}/inbox.log", f"{self.d}/cards"
        open(self.inbox, "w").close()
        post = f"{self.d}/console-post"
        open(post, "w").write(f"#!/bin/sh\necho \"$@\" >> {self.cardfile}\n")
        os.chmod(post, 0o755)
        saved = (backup.A0_INBOX, backup.CONSOLE_POST)
        backup.A0_INBOX, backup.CONSOLE_POST = self.inbox, post
        self.addCleanup(lambda: setattr(backup, "A0_INBOX", saved[0]) or setattr(backup, "CONSOLE_POST", saved[1]))

    def write(self, rows):
        json.dump({"results": rows}, open(os.path.join(self.rec, "backup.json"), "w"))

    def cards(self):
        return open(self.cardfile).read().count("-k card") if os.path.exists(self.cardfile) else 0

    def test_unsafe_is_loud_every_night_safe_once(self):
        safe = {"path": "/x/safe", "status": "held", "leaks_in_commits": [{}], "leak_count": 3,
                "held_copy": {"status": "pushed", "repo": "o/r", "ref": "refs/heads/m/safe"}}
        bad = {"path": "/x/bad", "status": "held", "leaks_in_commits": [{}], "held_copy": {"status": "error", "error": "push: x"}}
        self.write([safe, {"path": "/x/fine", "status": "pushed"}])
        self.assertEqual(backup.alert(self.m), 0)
        self.assertEqual(self.cards(), 1)
        self.assertEqual(open(self.inbox).read(), "")
        self.assertEqual(backup.alert(self.m), 0)            # same held set, safe: quiet
        self.assertEqual(self.cards(), 1)
        self.write([safe, bad])
        self.assertEqual(backup.alert(self.m), 1)
        self.assertIn("BLOCKED backup", open(self.inbox).read())
        self.assertEqual(backup.alert(self.m), 1)            # unsafe repeats until fixed
        self.assertEqual(open(self.inbox).read().count("BLOCKED"), 2)


if __name__ == "__main__":
    unittest.main()
