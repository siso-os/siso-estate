"""Pinned data blocks: no broad fetch, no success from a checkout alone."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('get_data', TOOLS / 'get-data.py')
get = importlib.util.module_from_spec(spec)
spec.loader.exec_module(get)


class DataBlocks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='.siso-ephemeral-get-data-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.block = 'fixture/01'
        self.sha = 'a' * 40
        self.payload = b'fixture payload'
        digest = hashlib.sha256(self.payload).hexdigest()
        self.manifest = {'version': 1, 'path': 'data.tar.zst', 'size': len(self.payload),
                         'sha256': digest, 'parts': [{'name': 'data.tar.zst.aaa', 'size': len(self.payload), 'sha256': digest}],
                         'locations': ['github:siso-data/siso-data-fixture-01'],
                         'pins': {'github:siso-data/siso-data-fixture-01': {'commit': self.sha}}}
        (self.root / 'data/manifests').mkdir(parents=True)
        (self.root / 'data/manifests/fixture.json').write_text(json.dumps(self.manifest))
        self.row = {'block': self.block, 'commit': self.sha, 'size': len(self.payload),
                    'manifest': 'data/manifests/fixture.json'}
        self.write_index([self.row])
        (self.root / '.gitmodules').write_text('[submodule "data/fixture/01"]\n\tpath = data/fixture/01\n\turl = https://github.com/siso-data/siso-data-fixture-01.git\n')
        self.raw_git('init', '-q')
        self.raw_git('add', 'data/blocks.json', 'data/manifests/fixture.json', '.gitmodules')
        self.raw_git('update-index', '--add', '--cacheinfo', '160000,' + self.sha + ',data/fixture/01')
        self.raw_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')

    def raw_git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], env=get.manifest_tool.git_environment(), stderr=subprocess.DEVNULL, text=True).strip()

    def write_index(self, rows):
        (self.root / 'data/blocks.json').write_text(json.dumps({'version': 1, 'blocks': rows}))

    def test_list_never_initializes_submodules(self):
        with patch.object(get, 'sh', side_effect=AssertionError('network or Git operation')):
            self.assertEqual(get.registry(self.root)[self.block]['size'], len(self.payload))

    def test_unknown_block_rejected_before_git_or_network(self):
        with patch.object(get, 'sh', side_effect=AssertionError('unexpected Git operation')):
            with self.assertRaises(ValueError):
                get.get_block(self.root, 'unregistered/01')

    def test_pin_and_url_must_match_committed_registration(self):
        self.assertEqual(get.registration(self.root, self.block, self.row), self.root / 'data/fixture/01')
        with self.assertRaises(ValueError):
            get.registration(self.root, self.block, dict(self.row, commit='b' * 40))
        self.raw_git('config', 'submodule.data/fixture/01.url', 'https://github.com/elsewhere/repo.git')
        with self.assertRaises(ValueError):
            get.registration(self.root, self.block, self.row)

    def test_traversal_duplicate_and_symlink_rejected(self):
        for rows in ([dict(self.row, block='../01')], [self.row, self.row], [dict(self.row, manifest='../outside.json')]):
            self.write_index(rows)
            with self.assertRaises(ValueError):
                get.registry(self.root)
        self.write_index([self.row])
        (self.root / 'data/fixture').symlink_to(self.root / 'data/manifests', target_is_directory=True)
        with self.assertRaises(ValueError):
            get.registration(self.root, self.block, self.row)

    def test_fetch_is_targeted_and_requires_payload_hashes(self):
        checkout = self.root / 'data/fixture/01'
        checkout.mkdir(parents=True)
        (checkout / 'data.tar.zst.aaa').write_bytes(self.payload)
        calls = []
        def fake_git(root, *args):
            calls.append(args)
            if args == ('rev-parse', '--absolute-git-dir'):
                return str(self.root / '.git')
            if args == ('rev-parse', 'HEAD'):
                return self.sha
            return ''
        with patch.object(get, 'registration', return_value=checkout), patch.object(get, 'git', side_effect=fake_git), \
                patch.object(get.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**12)), \
                patch.object(get.manifest_tool, 'verified_archive', side_effect=AssertionError('unnecessary fallback')):
            result = get.get_block(self.root, self.block)
        self.assertTrue(result['verified'])
        self.assertIn(('-c', 'submodule.recurse=false', '-c', 'protocol.file.allow=never', 'submodule', 'update', '--init', '--depth', '1', '--', 'data/fixture/01'), calls)
        self.assertFalse(any('--recursive' in c for c in calls))

    def test_corrupt_checkout_needs_successful_fallback(self):
        checkout = self.root / 'data/fixture/01'
        checkout.mkdir(parents=True)
        (checkout / 'data.tar.zst.aaa').write_bytes(b'bad')
        def fake_git(root, *args):
            return str(self.root / '.git') if args == ('rev-parse', '--absolute-git-dir') else self.sha if args == ('rev-parse', 'HEAD') else ''
        with patch.object(get, 'registration', return_value=checkout), patch.object(get, 'git', side_effect=fake_git), \
                patch.object(get.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**12)), \
                patch.object(get.manifest_tool, 'verified_archive', side_effect=RuntimeError('corrupt remote')) as fallback:
            with self.assertRaises(RuntimeError):
                get.get_block(self.root, self.block)
            fallback.assert_called_once()

    def test_capacity_hold_precedes_submodule_fetch(self):
        with patch.object(get, 'registration', return_value=self.root / 'data/fixture/01'), \
                patch.object(get, 'git', return_value=str(self.root / '.git')) as git, \
                patch.object(get.shutil, 'disk_usage', return_value=SimpleNamespace(free=99_000_000_000)):
            with self.assertRaises(RuntimeError):
                get.get_block(self.root, self.block)
            self.assertEqual(git.call_count, 1)  # Only locate metadata; no fetch.


if __name__ == '__main__':
    unittest.main()
