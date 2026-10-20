import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('vault_release', Path(__file__).resolve().parents[1] / 'tools/to-vault.py')
vault = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vault)


class VaultRelease(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'; self.source.mkdir()
        (self.source / 'authored.txt').write_text('must be preserved')
        self.destination = self.root / 'destination'

    def test_real_checksum_copy_preserves_then_releases(self):
        with patch.object(vault, 'held', return_value=[]):
            self.assertTrue(vault.checked_transfer(str(self.source), str(self.destination)))
        self.assertFalse(self.source.exists())
        self.assertEqual((self.destination / 'authored.txt').read_text(), 'must be preserved')

    def test_empty_output_with_failed_second_rsync_never_releases(self):
        original = vault.run
        def call(*args, **kw):
            if '-acni' in args:
                return subprocess.CompletedProcess([], 23, '', '')
            return original(*args, **kw)
        with patch.object(vault, 'held', return_value=[]), patch.object(vault, 'run', side_effect=call):
            self.assertFalse(vault.checked_transfer(str(self.source), str(self.destination)))
        self.assertTrue((self.source / 'authored.txt').exists())

    def test_new_writer_after_copy_keeps_newer_source(self):
        def changed(_):
            (self.source / 'authored.txt').write_text('newer work')
            return []
        with patch.object(vault, 'held', side_effect=changed):
            self.assertFalse(vault.checked_transfer(str(self.source), str(self.destination)))
        self.assertEqual((self.source / 'authored.txt').read_text(), 'newer work')

    def test_registered_runner_is_never_selected_from_retired_manifest(self):
        (self.source / 'MANIFEST.md').write_text('ARCHIVED_UNREGISTERED runner=siso-mini-halo-1')
        with patch.object(vault.glob, 'glob', return_value=[str(self.source)]), patch.object(vault, 'run', return_value=subprocess.CompletedProcess([], 0, 'siso-mini-halo-1\n', '')):
            self.assertEqual(vault.retired_services(), [])

    def test_running_pnpm_protects_all_store_generations(self):
        with patch.object(vault, 'run', return_value=subprocess.CompletedProcess([], 0, '123\n', '')):
            self.assertEqual(vault.pnpm_generations(), [])

    def test_git_objects_borrowed_from_outside_the_transfer_root_are_held(self):
        info = self.source / '.git/objects/info'; info.mkdir(parents=True)
        (info / 'alternates').write_text(str(self.root / 'outside-objects') + '\n')
        self.assertIn('Git object store outside the transfer root', vault.git_links(str(self.source)))

    def test_zero_idle_hours_does_not_use_bsd_find_negative_zero(self):
        with patch.object(vault, 'HOT_HOURS', 0), patch.object(vault, 'run', side_effect=AssertionError('no age gate')):
            self.assertIsNone(vault.newest(str(self.source)))

    def test_existing_complete_copy_can_be_verified_without_overwriting(self):
        import shutil
        shutil.copytree(self.source, self.destination)
        with patch.object(vault, 'held', return_value=[]):
            self.assertTrue(vault.checked_transfer(str(self.source), str(self.destination), verify_existing=True))
        self.assertFalse(self.source.exists())
        self.assertEqual((self.destination / 'authored.txt').read_text(), 'must be preserved')

    def test_existing_copy_with_extra_evidence_is_never_changed_or_used_for_release(self):
        import shutil
        shutil.copytree(self.source, self.destination)
        (self.destination / 'older-evidence.txt').write_text('keep this')
        with patch.object(vault, 'held', return_value=[]):
            self.assertFalse(vault.checked_transfer(str(self.source), str(self.destination), verify_existing=True))
        self.assertTrue(self.source.exists())
        self.assertEqual((self.destination / 'older-evidence.txt').read_text(), 'keep this')

    def test_pushed_snapshot_mismatch_prevents_vault_release(self):
        def git(*args):
            return subprocess.run(['git', '-C', str(self.source), *args], check=True, capture_output=True, text=True).stdout.strip()
        remote = self.root / 'remote.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(remote)], check=True)
        git('init', '-q', '-b', 'main')
        git('config', 'user.name', 'Fixture'); git('config', 'user.email', 'fixture@example.invalid')
        git('add', '.'); git('commit', '-qm', 'published')
        git('remote', 'add', 'origin', str(remote)); git('push', '-q', 'origin', 'main')
        sha = git('rev-parse', 'HEAD')
        self.assertTrue(vault.snapshot_matches(str(self.source), sha))
        (self.source / 'authored.txt').write_text('newer unpublished work')
        with patch.object(vault, 'held', return_value=[]):
            self.assertFalse(vault.checked_transfer(str(self.source), str(self.destination), snapshot=sha))
        self.assertEqual((self.source / 'authored.txt').read_text(), 'newer unpublished work')


if __name__ == '__main__':
    unittest.main()
