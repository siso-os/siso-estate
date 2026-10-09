#!/usr/bin/env python3
"""data-backup.py: a plane's machine, root and prune; and the guard that keeps an emptied plane's last snapshot.

  python3 -m unittest tests/test_data_backup.py -v
"""
import importlib.util, io, json, os, shutil, subprocess, sys, tarfile, tempfile, unittest
from unittest.mock import patch

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
spec = importlib.util.spec_from_file_location("data_backup", os.path.join(TOOLS, "data-backup.py"))
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


class DataBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.srv = os.path.join(self.tmp, "srv")
        prof = os.path.join(self.srv, "opt", "app", "data", "sessions", "Default")
        os.makedirs(os.path.join(prof, "Code Cache"))
        open(os.path.join(prof, "Local State"), "w").write("keep")
        open(os.path.join(prof, "Code Cache", "x.js"), "w").write("cache")
        open(os.path.join(self.srv, "opt", "app", "data", "api-key"), "w").write("secret")
        self.plane = {"name": "t", "machine": "vps", "root": self.srv, "encrypt": False,
                      "paths": ["opt/app/data"], "exclude": ["opt/app/data/api-key"], "prune": ["Code Cache"]}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_root_prune_exclude(self):
        inc, _ = db.gather(self.plane)
        rel = sorted(os.path.relpath(p, self.srv) for p in inc)
        self.assertEqual(rel, ["opt/app/data/sessions/Default/Local State"])

    def test_machine_filter(self):
        planes = [{"name": "a", "paths": []}, {"name": "b", "machine": "vps", "paths": []}]
        orig = db.json.load
        db.json.load = lambda f: {"planes": planes}
        try:
            self.assertEqual([p["name"] for p in db.planes("laptop")], ["a"])
            self.assertEqual([p["name"] for p in db.planes("vps")], ["b"])
            self.assertEqual(len(db.planes()), 2)
        finally:
            db.json.load = orig

    def test_emptied_plane_is_held(self):
        shutil.rmtree(os.path.join(self.srv, "opt"))
        os.makedirs(os.path.join(self.srv, "opt", "app", "data"))
        m = os.path.join(self.tmp, "machines", "vps")
        os.makedirs(m)
        json.dump({"results": {"t": {"status": "pushed", "files": 275, "fingerprint": "old"}}},
                  open(os.path.join(m, "data-backup.json"), "w"))
        orig = db.REPO
        db.REPO = self.tmp
        try:
            r = db.run_plane(self.plane, "vps")
        finally:
            db.REPO = orig
        self.assertEqual(r["status"], "held")
        self.assertEqual(r["files"], 275)

    def test_plain_plane_with_a_secret_is_held(self):
        leak = os.path.join(self.srv, "opt", "app", "data", "sessions", "Default", "config.env")
        open(leak, "w").write("GITHUB_TOKEN=ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8" + "\n")
        inc, _ = db.gather(self.plane)
        found = db.secrets_in(self.plane, inc)
        self.assertEqual([f["file"] for f in found], ["opt/app/data/sessions/Default/config.env"])
        os.unlink(leak)
        self.assertEqual(db.secrets_in(self.plane, db.gather(self.plane)[0]), [])

    def run_local_snapshot(self, fail_add=False):
        ws = os.path.join(self.tmp, "workspace")
        os.makedirs(os.path.join(ws, "input"))
        payload = os.urandom(32768)
        source = os.path.join(ws, "input", "payload.bin")
        with open(source, "wb") as f:
            f.write(payload)
        remote = os.path.join(self.tmp, "remote.git")
        subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
        real_sh = db.sh
        released = []

        def local_sh(*args, **kwargs):
            if args[0] == "gh":
                return 0, "", ""
            if args[:4] == ("git", "remote", "add", "origin"):
                args = (*args[:4], remote)
            if args[:2] == ("git", "commit"):
                cwd = kwargs["cwd"]
                _, files, _ = real_sh("git", "ls-files", cwd=cwd)
                for name in files.splitlines():
                    if name.startswith("data.tar.zst."):
                        self.assertFalse(os.path.exists(os.path.join(cwd, name)))
                        self.assertEqual(real_sh("git", "cat-file", "-t", ":" + name, cwd=cwd)[:2], (0, "blob"))
                        released.append(name)
            if fail_add and args[:2] == ("git", "add") and args[2].startswith("data.tar.zst."):
                return 1, "", "simulated full disk"
            return real_sh(*args, **kwargs)

        plane = {"name": "chunk-test", "root": ws, "paths": ["input"], "encrypt": False}
        with patch.object(db, "WS", ws), patch.object(db, "REPO", self.tmp), \
                patch.object(db, "WORK", os.path.join(ws, "backup-repos")), \
                patch.object(db, "CHUNK", 4096), patch.object(db, "PUSH_BATCH", 8192), \
                patch.object(db, "secrets_in", return_value=[]), patch.object(db, "sh", side_effect=local_sh):
            result = db.run_plane(plane, "laptop")
        with open(source, "rb") as f:
            self.assertEqual(f.read(), payload)
        return result, remote, payload, released, ws

    def test_released_chunk_workfiles_restore_from_remote(self):
        result, remote, payload, released, ws = self.run_local_snapshot()
        self.assertEqual(result["status"], "pushed")
        self.assertGreater(len(set(released)), 1)
        names = subprocess.check_output(["git", "-C", remote, "ls-tree", "--name-only", "main"], text=True).splitlines()
        packed = b"".join(subprocess.check_output(["git", "-C", remote, "show", "main:" + name])
                          for name in sorted(names) if name.startswith("data.tar.zst."))
        archive = subprocess.run(["zstd", "-q", "-d", "-c"], input=packed, capture_output=True, check=True).stdout
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            self.assertEqual(tar.extractfile("input/payload.bin").read(), payload)
        self.assertFalse(os.path.exists(os.path.join(ws, "backup-repos", "siso-data-chunk-test")))

    def test_add_failure_keeps_chunk_workfile_and_source(self):
        result, remote, payload, released, ws = self.run_local_snapshot(fail_add=True)
        self.assertEqual(result["status"], "error")
        self.assertIn("add chunk", result["error"])
        repo = os.path.join(ws, "backup-repos", "siso-data-chunk-test")
        self.assertTrue(any(name.startswith("data.tar.zst.") for name in os.listdir(repo)))
        self.assertFalse(subprocess.run(["git", "-C", remote, "show-ref"], capture_output=True).stdout)


if __name__ == "__main__":
    unittest.main()
