import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('orphan_offload', Path(__file__).resolve().parents[1] / 'tools/worktree-orphans.py')
orphans = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orphans)


class OrphanOffload(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'repo/lane'
        self.path.mkdir(parents=True)
        (self.path / '.git').write_text('gitdir: /missing/metadata\n')
        (self.path / 'source.txt').write_text('preserve this')

    def test_unreadable_metadata_is_detected_even_when_gitdir_exists(self):
        metadata = self.root / 'broken'; metadata.mkdir()
        (self.path / '.git').write_text('gitdir: ' + str(metadata))
        with patch.object(orphans, 'WT', self.root):
            self.assertEqual(list(orphans.orphans()), [self.path])

    def test_regenerable_output_is_excluded_but_authored_files_are_fingerprinted(self):
        before = orphans.fingerprint(self.path)
        (self.path / 'node_modules').mkdir()
        (self.path / 'node_modules/package.js').write_text('regenerable')
        after = orphans.fingerprint(self.path)
        self.assertEqual(before['source.txt'], after['source.txt'])
        self.assertFalse(any('node_modules' in key for key in after))

    def test_missing_r2_retains_original_without_publishing(self):
        with patch.object(orphans, 'WT', self.root), patch.object(orphans.wip, 'open_paths', return_value=set()), patch.object(orphans.wip, 'seat_cwds', return_value=[]), patch.object(orphans.wip, 'receipt'), patch.object(orphans, 'r2_ready', return_value=False), patch.object(orphans.archive, 'build') as build, patch('sys.argv', ['worktree-orphans.py', '--run', '--idle-hours', '0']):
            self.assertEqual(orphans.main(), 1)
        build.assert_not_called()
        self.assertTrue(self.path.exists())

    def test_r2_checksum_mismatch_fails_before_release(self):
        with patch.object(orphans.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'wrong', b'')):
            with self.assertRaisesRegex(RuntimeError, 'R2 readback checksum mismatch'):
                orphans.r2_copy(self.root, [('part-00000', 'different', 5)], 'fixture:bucket/key')
        self.assertTrue(self.path.exists())

    def test_local_or_non_r2_transport_cannot_satisfy_second_copy_gate(self):
        for backend in [{'type': 'local'}, {'type': 's3', 'endpoint': 'https://example.com'},
                        {'type': 's3', 'endpoint': 'http://fixture.r2.cloudflarestorage.com'}]:
            with patch.dict(os.environ, {'ESTATE_R2_REMOTE': 'fixture:bucket'}), patch.object(orphans.shutil, 'which', return_value='/fixture/rclone'), patch.object(orphans.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps({'fixture': backend}), '')):
                self.assertFalse(orphans.r2_ready())


if __name__ == '__main__':
    unittest.main()
