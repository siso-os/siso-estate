"""Interrupted archive uploads resume by verified content; missing identities never upload."""
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools/archive-offload.py'
spec = importlib.util.spec_from_file_location('archive_recovery', SOURCE)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)

class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'archive/fixture'
        self.source.mkdir(parents=True)
        (self.source / 'authored.txt').write_text('preserve me\n')
        self.key = self.root / 'identity'
        self.key.write_text('fixture only')
        for name, value in [('ARCHIVE', str(self.root / 'archive')), ('WORK', str(self.root / 'work')),
                            ('WS', str(self.root)), ('KEY', str(self.key)),
                            ('RECEIPTS', str(self.root / 'runtime/receipt.jsonl'))]:
            ctx = patch.object(archive, name, value)
            ctx.start(); self.addCleanup(ctx.stop)

    def test_missing_identity_never_uploads(self):
        with patch.object(archive, 'KEY', str(self.root / 'absent')), patch.object(archive, 'sh', return_value='1 path'), patch.object(archive.subprocess, 'run') as call:
            with self.assertRaisesRegex(SystemExit, 'identity missing'):
                archive.run('fixture', True)
            call.assert_not_called()
        self.assertTrue(self.source.exists())

    def resume(self, change_source=False):
        seen = archive.source_manifest('fixture')
        cipher = b'fixture encrypted payload'
        digest = hashlib.sha256(cipher).hexdigest()
        def sh(*args, cwd=None, **kw):
            if args[0] == 'du': return '1 path'
            if args[:2] == ('git', 'clone'):
                clone = Path(args[-1]); clone.mkdir(exist_ok=True)
                (clone / 'part-00000').write_bytes(cipher)
                (clone / 'SHA256SUMS').write_text(digest + '  part-00000\n')
                return ''
            if args[:2] == ('git', 'rev-parse'): return 'abc123'
            raise AssertionError(args)
        def read_back(*args):
            if change_source:
                (self.source / 'authored.txt').write_text('a newer edit\n')
            return seen
        with patch.object(archive, 'sh', side_effect=sh), patch.object(archive, 'push') as push, patch.object(archive, 'read_back', side_effect=read_back), patch.object(archive.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, archive.RECIPIENT, '')), patch('sys.stdout', new_callable=io.StringIO):
            archive.run('fixture', True)
            push.assert_not_called()

    def test_existing_upload_is_verified_then_released(self):
        self.resume()
        self.assertFalse(self.source.exists())
        self.assertTrue((self.root / 'archive/fixture.OFFLOADED.md').exists())
        self.assertTrue((self.root / 'runtime/receipt.jsonl').exists())

    def test_newer_source_keeps_its_only_copy(self):
        with self.assertRaisesRegex(SystemExit, 'source changed'):
            self.resume(change_source=True)
        self.assertEqual((self.source / 'authored.txt').read_text(), 'a newer edit\n')

    def test_stale_remote_snapshot_is_refreshed_before_a_fresh_verification(self):
        seen = archive.source_manifest('fixture')
        cipher = b'cipher'; digest = hashlib.sha256(cipher).hexdigest()
        def sh(*args, cwd=None, **kwargs):
            if args[0] == 'du': return '1 path'
            if args[:2] == ('git', 'clone'):
                p = Path(args[-1]); (p / 'part-00000').write_bytes(cipher)
                (p / 'SHA256SUMS').write_text(digest + '  part-00000\n'); return ''
            if args[:2] == ('git', 'rev-parse'): return 'fixture-head'
            raise AssertionError(args)
        with patch.object(archive, 'sh', side_effect=sh), patch.object(archive.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, archive.RECIPIENT, '')), patch.object(archive, 'read_back', side_effect=[{}, seen]), patch.object(archive, 'build', return_value=(seen, [('part-00000', digest, 6)])), patch.object(archive, 'push') as pushed, patch('sys.stdout', new_callable=io.StringIO):
            archive.run('fixture', True)
        pushed.assert_called_once()
        self.assertFalse(self.source.exists())

    def test_archive_parts_are_under_95_megabytes(self):
        self.assertLess(archive.PART, 95_000_000)

if __name__ == '__main__':
    unittest.main()
