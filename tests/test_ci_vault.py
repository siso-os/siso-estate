import fcntl
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('ci_vault', Path(__file__).resolve().parents[1] / 'tools/ci-storage-retire.py')
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)


class CiVault(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.clone = self.root / 'clone'; self.clone.mkdir()
        self.lock = self.root / 'worker.lock'
        self.retention = SimpleNamespace(running_commands=lambda: '')

    def test_stash_and_unobserved_git_state_cannot_pass_clean_retirement(self):
        for result in [subprocess.CompletedProcess([], 0, 'unpublished-stash', ''), subprocess.CompletedProcess([], 128, '', 'unknown')]:
            with patch.object(ci.subprocess, 'run', return_value=result):
                self.assertFalse(ci.stash_free(self.clone))

    def test_worker_lease_prevents_any_transfer(self):
        with self.lock.open('a') as lease:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(ci.subprocess, 'run') as run:
                self.assertFalse(ci.vault_clone(self.clone, self.lock, self.retention, 'mini'))
            run.assert_not_called()
        self.assertTrue(self.clone.exists())

    def test_unknown_or_running_owner_is_held(self):
        for commands in [None, str(self.clone)]:
            retention = SimpleNamespace(running_commands=lambda: commands)
            with patch.object(ci.subprocess, 'run') as run:
                self.assertFalse(ci.vault_clone(self.clone, self.lock, retention, 'mini'))
            run.assert_not_called()

    def test_success_keeps_worker_lease_until_verified_transfer_returns(self):
        def transfer(*args, **kwargs):
            with self.lock.open('a') as another:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(another, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.clone.rename(self.root / 'vaulted')
            return subprocess.CompletedProcess([], 0, 'verified', '')
        with patch.object(ci.subprocess, 'run', side_effect=transfer):
            self.assertTrue(ci.vault_clone(self.clone, self.lock, self.retention, 'mini'))
        self.assertTrue((self.root / 'vaulted').exists())


if __name__ == '__main__':
    unittest.main()
