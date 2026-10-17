import importlib.util
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('home_backup', Path(__file__).parents[1] / 'tools/home_backup.py')
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class CoverageTests(unittest.TestCase):
    def test_boundary_hold_quarantines_excluded_and_linked_files(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(os.path.realpath(tmp))
            (home / 'secret').mkdir()
            (home / 'good.txt').write_text('x')
            (home / 'secret/k').write_text('y')
            os.symlink(home / 'good.txt', home / 'link.txt')
            db = mod.open_state(home / 'run')
            plane = {'name': 'p', 'root': str(home), 'paths': ['.'], 'exclude': ['secret']}
            batch = [str(home / 'good.txt'), str(home / 'secret/k'), str(home / 'link.txt')]
            with patch.object(mod.backup, 'WS', str(home / 'ws')):
                bad = mod.quarantine_unsnapshottable(db, plane, batch, {}, mod.BOUNDARY_HOLD)
            self.assertEqual(sorted(bad), sorted([batch[1], batch[2]]))

    def test_snapshot_hold_quarantines_only_unopenable_files(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(os.path.realpath(tmp))
            good, fifo = home / 'good.txt', home / 'pipe'
            good.write_text('x')
            os.mkfifo(fifo)
            db = mod.open_state(home / 'run')
            bad = mod.quarantine_unsnapshottable(db, {'root': str(home)}, [str(good), str(fifo), str(home / 'gone')],
                                                 {str(fifo): 0, str(home / 'gone'): 0})
            self.assertEqual(sorted(bad), sorted([str(fifo), str(home / 'gone')]))
            rows = dict(db.execute('SELECT path,status FROM coverage').fetchall())
            self.assertEqual(rows, {str(fifo): 'unreadable-or-vanished', str(home / 'gone'): 'unreadable-or-vanished'})

    def test_refetch_relative_path_and_cached_verified_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp).resolve()
            run = home / 'run'
            (run / 'refetch').mkdir(parents=True)
            source_root = home / '.npm/_cacache'
            source_root.mkdir(parents=True)
            (source_root / 'tarball').write_bytes(b'package')
            manifest = {'source_root': str(source_root), 'entries': [
                {'source_verification_status': 'verified_public', 'covered_file':
                 {'path': 'tarball', 'size': 7, 'sha512': hashlib.sha512(b'package').hexdigest()}}]}
            path = run / 'refetch/npm-refetch-manifest-source-verified.json'
            path.write_text(json.dumps(manifest))
            (run / 'refetch-upload.json').write_text(json.dumps({
                'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'result': {'status': 'pushed', 'restore_verified': True, 'repo': 'siso-data/test', 'commit': 'abc', 'ref': 'ref'}}))
            db = mod.open_state(run)
            with patch.object(mod, 'HOME', home), patch.object(mod.backup, 'run_streamed') as upload:
                mod.upload_refetch_manifest(run, db, {})
                upload.assert_not_called()
            row = db.execute('SELECT path,status,source_bytes FROM coverage').fetchone()
            self.assertEqual(tuple(row), (str(source_root / 'tarball'), 'verified-refetch', 7))
            db.close()

    def test_due_failed_source_retries_but_verified_source_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            db = mod.open_state(run)
            for name, status, due in [('due', 'source-changed-or-unreadable', 0),
                                      ('later', 'unreadable-or-vanished', time.time() + 300),
                                      ('done', 'verified-bytes', 0)]:
                path = run / name
                path.write_bytes(b'x')
                db.execute('INSERT INTO coverage(path,status) VALUES (?,?)', (str(path), status))
                db.execute('INSERT INTO retries VALUES (?,?,?)', (str(path), 1, due))
            self.assertEqual([Path(p).name for p, _ in mod.retry_candidates(db)], ['due'])
            db.close()

    def test_fragment_groups_do_not_count_until_descriptor_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            run = home / 'run'
            run.mkdir()
            (run / 'scope.json').write_text('{"halo_prefixes": []}')
            source = home / 'source'
            source.write_bytes(b'abcdef')
            db = mod.open_state(run)
            calls = []
            def fake_factory(group, *args, **kwargs):
                class Factory:
                    last_state = {'descriptor': dict(group, fragments=[dict(f, sha256='a'*64) for f in group['fragments']])}
                return Factory()
            def transport(*args, **kwargs):
                calls.append(kwargs)
                if 'archive_factory' not in kwargs:
                    return {'status': 'held'}
                return {'status': 'pushed', 'restore_verified': True, 'repo': 'siso-data/test', 'commit': 'abc', 'ref': 'ref'}
            with patch.object(mod, 'HOME', home), patch.object(mod.backup, 'recipient', return_value='recipient'), \
                 patch.object(mod.fragments, 'archive_factory', side_effect=fake_factory), \
                 patch.object(mod.backup, 'run_streamed', side_effect=transport):
                with self.assertRaisesRegex(RuntimeError, 'descriptor verification'):
                    mod.upload_large_file(run, db, {}, source)
                self.assertEqual(db.execute('SELECT count(*) FROM coverage').fetchone()[0], 0)
                # Verified fragment receipt is resumed, not uploaded again.
                calls.clear()
                with self.assertRaises(RuntimeError):
                    mod.upload_large_file(run, db, {}, source)
                self.assertEqual(len(calls), 1)
            db.close()

    def test_failed_publication_never_counts_callback_as_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            db = mod.open_state(run)
            row = {'path': 'a', 'size': 3, 'sha256': 'abc', 'source_stat': {'file': {'size': 3}}}
            with self.assertRaisesRegex(RuntimeError, 'publication not remotely verified'):
                mod.record_batch(db, run, {}, [str(mod.HOME / 'a')], 'failed', {'status': 'held'}, [row])
            self.assertEqual(db.execute('SELECT count(*) FROM coverage').fetchone()[0], 0)
            good = {'status': 'pushed', 'restore_verified': True, 'repo': 'siso-data/test', 'commit': 'abc'}
            mod.record_batch(db, run, {}, [str(mod.HOME / 'a')], 'done', good, [row])
            self.assertEqual(db.execute('SELECT source_bytes FROM coverage').fetchone()[0], 3)
            db.close()

    def test_candidate_pagination_preserves_pending_large_and_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            run = home / 'run'
            run.mkdir()
            protected = home / 'protected'
            protected.mkdir()
            (run / 'scope.json').write_text(json.dumps({'halo_prefixes': [str(protected)]}))
            extra = home / 'extra-protected'
            extra.mkdir()
            (run / 'git-protected-prefixes.json').write_text(json.dumps({
                'schema': 'home-git-protected-prefixes.v1', 'protected_prefixes': [str(extra)]}))
            names = ['a', 'b', 'large', 'protected/private', 'extra-protected/private']
            inv = sqlite3.connect(':memory:')
            inv.row_factory = sqlite3.Row
            inv.execute('CREATE TABLE entries(path TEXT PRIMARY KEY,size INTEGER,kind TEXT,exclusion TEXT)')
            for name in names:
                p = home / name
                p.write_bytes(b'abcdef' if name == 'large' else b'x')
                inv.execute('INSERT INTO entries VALUES (?, ?, ?, NULL)', (str(p), p.stat().st_size, 'file'))
            db = mod.open_state(run)
            with patch.object(mod, 'HOME', home), patch.object(mod, 'MAX_FILE', 4):
                got = list(mod.candidates(inv, db, run, '', str(home / 'a')))
            self.assertEqual([Path(p).name for p, _ in got], ['b'])
            self.assertEqual(db.execute('SELECT status FROM coverage WHERE path=?', (str(home / 'large'),)).fetchone()[0], 'large-file-pending')
            db.close()
            inv.close()

    def test_parallel_partition_is_complete_and_disjoint_including_root_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            run = home / 'run'
            run.mkdir()
            with sqlite3.connect(run / 'inventory-all.sqlite') as db:
                db.execute('CREATE TABLE directory_summary(directory TEXT,depth INTEGER,allocated_bytes INTEGER)')
                for rel, depth, size in [('.config', 1, 20), ('Library', 1, 90),
                                         ('Library/Caches', 2, 50), ('Library/Support', 2, 40),
                                         ('SISO_Workspace', 1, 100), ('SISO_Workspace/Agency', 2, 100)]:
                    db.execute('INSERT INTO directory_summary VALUES (?,?,?)', (str(home / rel), depth, size))
            with patch.object(mod, 'HOME', home):
                groups = mod.parallel_lanes(run, 3)
                for rel in ['.config/a', 'Library/Caches/a', 'Library/Support/a',
                            'Library/top.db', 'SISO_Workspace/Agency/a',
                            'SISO_Workspace/top.json', '.profile']:
                    self.assertEqual(sum(mod.lane_contains(str(home / rel), g) for g in groups), 1, rel)

    def test_root_file_lane_never_selects_descendants(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            run = home / 'run'
            run.mkdir()
            (run / 'scope.json').write_text('{"halo_prefixes": []}')
            (home / 'top').write_text('a')
            (run / 'nested').write_text('b')
            inventory = sqlite3.connect(':memory:')
            inventory.row_factory = sqlite3.Row
            inventory.execute('CREATE TABLE entries(path TEXT PRIMARY KEY,size INTEGER,kind TEXT,exclusion TEXT)')
            for p in [home / 'top', run / 'nested']:
                inventory.execute('INSERT INTO entries VALUES (?,1,\'file\',NULL)', (str(p),))
            db = mod.open_state(run)
            with patch.object(mod, 'HOME', home):
                result = list(mod.candidates(inventory, db, run, '@files', None))
            self.assertEqual(result, [(str(home / 'top'), 1)])
            db.close()
            inventory.close()

    def test_retry_is_owned_by_exactly_one_parallel_lane(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / 'a').mkdir()
            (home / 'a/file').write_bytes(b'x')
            db = mod.open_state(home / 'run')
            db.execute('INSERT INTO coverage(path,status) VALUES (?,?)', (str(home / 'a/file'), 'source-changed-or-unreadable'))
            db.execute('INSERT INTO retries VALUES (?,1,0)', (str(home / 'a/file'),))
            with patch.object(mod, 'HOME', home):
                self.assertEqual(len(list(mod.retry_candidates(db, ['a']))), 1)
                self.assertEqual(list(mod.retry_candidates(db, ['b', '@files'])), [])
            db.close()

    def test_exact_member_requires_verified_manifest_and_unchanged_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            run = home / 'run'
            (run / 'refetch-trees').mkdir(parents=True)
            (run / 'scope.json').write_text('{"halo_prefixes": []}')
            source = home / 'package.js'
            source.write_bytes(b'known')
            identity = mod.backup.safe_source.identity(source.stat())
            manifest = {'schema': 'siso-estate.exact-refetch-members.v1', 'done': True, 'covered_files': [{'absolute_path': str(source), 'size': 5,
                'sha256': hashlib.sha256(b'known').hexdigest(), 'source_stat': identity,
                'source_url': 'https://registry.npmjs.org/example/-/example-1.0.0.tgz',
                'archive_member': 'package/package.js', 'source_integrity': 'sha512-example'}]}
            path = run / 'refetch-trees/exact-members.json'
            path.write_text(json.dumps(manifest))
            db = mod.open_state(run)
            unfinished = dict(manifest, done=False)
            path.write_text(json.dumps(unfinished))
            with self.assertRaisesRegex(ValueError, 'unfinished'):
                mod.upload_refetch_members(run, db, {})
            path.write_text(json.dumps(manifest))
            with patch.object(mod, 'HOME', home), patch.object(mod, 'upload_with_capacity', return_value={'status': 'held'}):
                with self.assertRaisesRegex(RuntimeError, 'publication incomplete'):
                    mod.upload_refetch_members(run, db, {})
            self.assertEqual(db.execute('SELECT count(*) FROM coverage').fetchone()[0], 0)
            (run / 'refetch-trees/exact-members-upload.json').write_text(json.dumps({
                'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                'result': {'status': 'pushed', 'restore_verified': True, 'repo': 'siso-data/test', 'commit': 'abc', 'ref': 'ref'}}))
            source.write_bytes(b'changed')
            with patch.object(mod, 'HOME', home):
                self.assertEqual(mod.upload_refetch_members(run, db, {})['added_files'], 0)
            db.close()

    def test_incomplete_inventory_retries_without_crediting_unpublished_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            got, calls = [], []
            def transport(*args, **kwargs):
                calls.append(1)
                kwargs['on_verified_files']([{'attempt': len(calls)}])
                if len(calls) == 1:
                    return {'status': 'held', 'held_reason': 'organization storage inventory incomplete; upload denied'}
                return {'status': 'pushed', 'restore_verified': True}
            with patch.object(mod.backup, 'run_streamed', side_effect=transport), patch.object(mod.time, 'sleep') as sleep:
                result = mod.upload_with_capacity(run, {}, [], 'run', 'hash', on_verified_files=got.extend)
            self.assertEqual(result['status'], 'pushed')
            self.assertEqual(got, [{'attempt': 2}])
            sleep.assert_called_once_with(60)
            with patch.object(mod.backup, 'run_streamed', return_value={'status': 'held', 'held_reason': 'storage reader boundary differs from declared teams'}) as upload, patch.object(mod.time, 'sleep') as sleep:
                result = mod.upload_with_capacity(run, {}, [], 'run', 'hash')
            self.assertEqual(result['status'], 'held')
            upload.assert_called_once()
            sleep.assert_not_called()

    def test_later_worker_failure_stops_surviving_lane_without_waiting_for_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            def batches(run, max_batches, lanes, number, count):
                if number == 1:
                    raise RuntimeError('bounded fixture failure')
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not (run / 'stop-after-batch').exists():
                    time.sleep(0.01)
                self.assertTrue((run / 'stop-after-batch').exists())
            with patch.object(mod, 'parallel_lanes', return_value=[['a'], ['b']]), patch.object(mod, 'import_legal'), patch.object(mod, 'run_batches', side_effect=batches):
                with self.assertRaisesRegex(RuntimeError, 'bounded fixture failure'):
                    mod.run_parallel(run, 2)
            self.assertEqual(json.loads((run / 'worker-0-exit.json').read_text())['status'], 'completed')
            self.assertEqual(json.loads((run / 'worker-1-exit.json').read_text())['status'], 'failed')
            self.assertEqual(json.loads((run / 'parallel-failure.json').read_text())['status'], 'failed')

    def test_transport_retry_discards_unpublished_verification_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            got = []
            def transport(*args, **kwargs):
                kwargs['on_verified_files']([{'unpublished': True}])
                return {'status': 'held', 'held_reason': 'source failed'}
            with patch.object(mod.backup, 'run_streamed', side_effect=transport):
                mod.upload_with_capacity(run, {}, [], 'run', 'hash', on_verified_files=got.extend)
            self.assertEqual(got, [])


if __name__ == '__main__':
    unittest.main()
