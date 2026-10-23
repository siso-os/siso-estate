import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('retire', Path(__file__).resolve().parents[1] / 'tools/worktree-retire.py')
retire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retire)


class BuilderRetire(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.remote, self.repo = self.root / 'remote.git', self.root / 'repo'
        self.wt = self.root / 'worktrees'
        self.lane = self.wt / 'siso' / 'builder'
        self.lane.parent.mkdir(parents=True)
        self.git('init', '--bare', str(self.remote))
        self.git('init', '-b', 'main', str(self.repo))
        self.git('-C', str(self.repo), 'config', 'user.email', 'fixture@example.invalid')
        self.git('-C', str(self.repo), 'config', 'user.name', 'Fixture')
        (self.repo / 'source').write_text('preserve this\n')
        self.git('-C', str(self.repo), 'add', 'source')
        self.git('-C', str(self.repo), 'commit', '-m', 'seed')
        self.git('-C', str(self.repo), 'remote', 'add', 'origin', str(self.remote))
        self.git('-C', str(self.repo), 'push', 'origin', 'main')
        self.git('-C', str(self.repo), 'worktree', 'add', '-b', 'builder', str(self.lane))
        self.patch = patch.object(retire, 'WT', str(self.wt))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def git(self, *args):
        return subprocess.check_output(['git', *args], stderr=subprocess.PIPE, text=True).strip()

    def test_landed_lane_uses_remote_main_and_preserves_fetch_head(self):
        self.git('-C', str(self.repo), 'fetch', 'origin', 'main')
        fetch = self.repo / '.git/FETCH_HEAD'
        before = fetch.read_bytes()
        self.assertEqual(retire.landed_builder_lane(str(self.lane)), ('siso', 'builder'))
        self.assertEqual(fetch.read_bytes(), before)
        self.assertTrue(self.lane.is_dir())  # probe itself never removes source

    def test_untracked_work_stays(self):
        (self.lane / 'unfinished').write_text('unique\n')
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            retire.landed_builder_lane(str(self.lane))

    def test_halo_monorepo_verifies_halo_v2_trunk(self):
        halo = self.wt / 'halo/builder'
        halo.parent.mkdir()
        self.git('-C', str(self.repo), 'worktree', 'move', str(self.lane), str(halo))
        self.git('-C', str(self.repo), 'push', 'origin', 'main:refs/heads/halo/v2')
        self.git('--git-dir=' + str(self.remote), 'update-ref', '-d', 'refs/heads/main')
        self.assertEqual(retire.landed_builder_lane(str(halo)), ('halo', 'builder'))

    def test_unlanded_but_pushed_branch_stays(self):
        (self.lane / 'source').write_text('new commit\n')
        self.git('-C', str(self.lane), 'commit', '-am', 'unfinished')
        self.git('-C', str(self.lane), 'push', 'origin', 'builder')
        with self.assertRaisesRegex(ValueError, 'not freshly verified'):
            retire.landed_builder_lane(str(self.lane))

    def test_standing_and_outside_paths_stay(self):
        for path in (self.wt / 'siso/main', self.repo, self.wt / 'siso/builder/child'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                retire.landed_builder_lane(str(path))


if __name__ == '__main__':
    unittest.main()
