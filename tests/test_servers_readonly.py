"""The remote Git census must leave checkout metadata unchanged."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import servers


class ReadonlyGitScan(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "fixture repo"
        self.repo.mkdir()
        self.env = os.environ | {
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_OPTIONAL_LOCKS": "1", "SCANNER_FIXTURE_REPO": str(self.repo),
        }
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        (self.repo / "tracked").write_text("original\n")
        self.git("add", "tracked")
        self.git("commit", "-qm", "fixture")
        self.git("remote", "add", "origin", "https://example.invalid/fixture.git")
        tools = self.root / "bin"
        tools.mkdir()
        find = tools / "find"
        find.write_text('#!/bin/sh\nprintf \'%s\\n\' "$SCANNER_FIXTURE_REPO/.git"\n')
        find.chmod(0o755)
        self.env["PATH"] = str(tools) + os.pathsep + self.env["PATH"]

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.repo), *args], env=self.env, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()

    def metadata(self):
        git = self.repo / ".git"
        s = git.stat()
        return (s.st_mtime_ns, s.st_ctime_ns,
                hashlib.sha256((git / "index").read_bytes()).hexdigest(),
                (git / "HEAD").read_bytes(), (git / "config").read_bytes())

    def scan_git(self):
        # Run the actual remote Git section, with find limited to this fixture.
        prefix = servers.SCAN.split('echo "## host"', 1)[0]
        body = servers.SCAN.split('echo "## git"\n', 1)[1].split('echo "## services"', 1)[0]
        output = subprocess.check_output(["bash", "-c", prefix + body], env=self.env, text=True)
        return output.strip().split("\t")

    def test_clean_checkout_keeps_git_metadata(self):
        head = self.git("rev-parse", "--short", "HEAD")
        before = self.metadata()
        row = self.scan_git()
        self.assertEqual(row[:3], [str(self.repo), "https://example.invalid/fixture.git", head])
        self.assertEqual(int(row[-1]), 0)
        self.assertEqual(self.metadata(), before)

    def test_dirty_count_without_index_refresh(self):
        (self.repo / "tracked").write_text("changed\n")
        (self.repo / "untracked").write_text("new\n")
        before = self.metadata()
        row = self.scan_git()
        self.assertEqual(int(row[-1]), 2)
        self.assertEqual(self.metadata(), before)


if __name__ == "__main__":
    unittest.main()
