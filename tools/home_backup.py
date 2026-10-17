#!/usr/bin/env python3
"""Resumable encrypted home-file batches using Estate's verified bounded transport.

Private per-file receipts stay in the run directory. A completed remote manifest,
not a successful push alone, is required before coverage is recorded.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

TRANSPORT = threading.local()

SPEC = importlib.util.spec_from_file_location('estate_backup', Path(__file__).with_name('data-backup.py'))
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)
FRAGMENT_SPEC = importlib.util.spec_from_file_location('home_fragments', Path(__file__).with_name('home_fragments.py'))
fragments = importlib.util.module_from_spec(FRAGMENT_SPEC)
FRAGMENT_SPEC.loader.exec_module(fragments)
HOME = Path.home()
BATCH_BYTES = 700_000_000
BATCH_FILES = 8000
MAX_FILE = 256_000_000
PRIORITY = ['SISO_Workspace/personal', 'SISO_Workspace/.credentials', '.config', '.ssh', '.gnupg', '.local/state']


def atomic_json(path, value, indent=None):
    path = Path(path)
    pending = path.with_name(path.name + '.pending-' + uuid.uuid4().hex)
    with pending.open('x') as output:
        os.chmod(pending, 0o600)
        json.dump(value, output, indent=indent)
        output.write('\n')
    os.replace(pending, path)


def open_state(run):
    run.mkdir(parents=True, exist_ok=True)
    os.chmod(run, 0o700)
    db = sqlite3.connect(run / 'coverage.sqlite', timeout=60)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS coverage (
        path TEXT PRIMARY KEY, status TEXT NOT NULL, source_bytes INTEGER,
        sha256 TEXT, source_stat TEXT, snapshot_url TEXT, run_id TEXT, note TEXT);
      CREATE TABLE IF NOT EXISTS cursors (lane TEXT PRIMARY KEY, path TEXT);
      CREATE TABLE IF NOT EXISTS batches (run_id TEXT PRIMARY KEY, status TEXT, receipt TEXT);
      CREATE TABLE IF NOT EXISTS retries (path TEXT PRIMARY KEY, attempts INTEGER, after_time REAL);
    ''')
    return db


def minimal_prefixes(values):
    result = []
    for value in sorted(set(os.path.abspath(v) for v in values)):
        if not any(value == parent or value.startswith(parent + os.sep) for parent in result):
            result.append(value)
    return result


def protected_prefixes(run):
    scope = json.loads((run / 'scope.json').read_text())
    prefixes = scope['halo_prefixes']
    extra = run / 'git-protected-prefixes.json'
    if extra.exists():
        document = json.loads(extra.read_text())
        if isinstance(document, dict) and document.get('schema') == 'home-git-protected-prefixes.v1':
            document = document.get('protected_prefixes')
        if not isinstance(document, list) or any(not isinstance(p, str) or not os.path.isabs(p) for p in document):
            raise ValueError('protected Git prefixes have an unknown schema')
        prefixes += document
    return minimal_prefixes(prefixes)


def make_plane(run, shard):
    name = f'home-laptop-{shard:03}'
    return {'name': name, 'root': str(HOME), 'paths': ['.'],
            'exclude': [os.path.relpath(p, HOME) for p in protected_prefixes(run) if p.startswith(str(HOME) + '/')],
            'encrypt': True, 'access_boundary': 'shaan-private',
            'backend': f'github:siso-data/siso-data-{name}', 'backend_status': 'provisioned-private',
            'github_access_teams': ['shaan-private-data-readers'], 'machine': 'laptop', 'manual': True}


def ensure_plane(run, shard):
    plane = make_plane(run, shard)
    owner, repo, _ = backup.dm.github(plane['backend'], write=True)
    known_path = run / 'known-shards.json'
    try:
        known = json.loads(known_path.read_text())
    except (OSError, ValueError):
        known = {}
    if plane['name'] not in known:
        # REST core quota is 5,000/hour: one existence, identity and reader check per shard per run
        # is cached; rate limits wait for the reset inside gh_rest; "already exists" is not fatal.
        code, output, _ = backup.gh_rest('api', f'repos/{owner}/{repo}', tries=1)
        if code:
            code, output, err = backup.gh_rest('api', 'orgs/siso-data/repos', '-X', 'POST',
                                               '-f', 'name=' + repo, '-F', 'private=true',
                                               '-f', 'description=Encrypted laptop preservation; immutable verified snapshots')
            if code and 'already exists' in (err + output).lower():
                code, output, _ = backup.gh_rest('api', f'repos/{owner}/{repo}')
            if code:
                raise RuntimeError('private home shard creation failed')
        info = json.loads(output)
        if info.get('private') is not True or info.get('full_name', '').lower() != f'{owner}/{repo}'.lower():
            raise RuntimeError('home shard identity or privacy differs')
        code, _, _ = backup.gh_rest('api', f'orgs/siso-data/teams/shaan-private-data-readers/repos/{owner}/{repo}',
                                    '-X', 'PUT', '-f', 'permission=pull')
        if code:
            raise RuntimeError('private home shard reader binding failed')
        known[plane['name']] = time.time()
        atomic_json(known_path, known)
    # The shared transport checks actual teams, direct collaborators and org policy again.
    path = Path(backup.REPO) / 'plan/data-planes.json'
    with (run / 'plane-register.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        document = json.loads(path.read_text())
        old = next((p for p in document['planes'] if p['name'] == plane['name']), None)
        if old is not None and old != plane:
            # Refresh explicit protected prefixes without silently changing any other boundary.
            if {k: v for k, v in old.items() if k != 'exclude'} != {k: v for k, v in plane.items() if k != 'exclude'}:
                raise RuntimeError('registered home plane differs')
            old['exclude'] = plane['exclude']
        elif old is None:
            document['planes'].append(plane)
        atomic_json(path, document, indent=2)
    return plane


def import_legal(run, db):
    receipt = run / 'personal-legal-result.json'
    if not receipt.exists():
        return
    result = json.loads(receipt.read_text())
    if result.get('status') != 'pushed' or not result.get('restore_verified'):
        return
    url = f"https://github.com/{result['repo']}/tree/{result['commit']}"
    for row in json.loads((run / 'personal-legal-files.json').read_text()):
        path = str(Path(backup.WS) / row['path'])
        db.execute('INSERT OR IGNORE INTO coverage VALUES (?,?,?,?,?,?,?,?)',
                   (path, 'verified-bytes', row['size'], row['sha256'], json.dumps(row['source_stat']), url, result['ref'], None))
    db.commit()


def upload_with_capacity(run, plane, paths, run_id, fingerprint, **kwargs):
    callback = kwargs.pop('on_verified_files', None)
    for attempt in range(100):
        captured = []
        try:
            result = backup.run_streamed(plane, 'laptop', paths, run_id + (f'-s{attempt}' if attempt else ''),
                                         fingerprint, backup.STREAM_PROFILE,
                                         max_streams=getattr(TRANSPORT, 'max_streams', 1),
                                         on_verified_files=captured.extend if callback else None, **kwargs)
        except RuntimeError as error:
            if str(error) not in ('streaming load ceiling reached', 'streaming resource monitor failed: load ceiling'):
                raise
            result = {'status': 'held', 'held_reason': str(error)}
        reason = result.get('held_reason', '')
        if result.get('status') == 'held' and (reason.startswith('concurrent ') or 'load ceiling' in reason
                                               or reason == 'another streaming run owns the aggregate staging budget'
                                               or reason == 'organization storage inventory incomplete; upload denied'):
            atomic_json(run / f'worker-{getattr(TRANSPORT, "worker", "serial")}-admission-wait.json',
                        {'reason': reason, 'at': time.time()})
            if (run / 'stop-after-batch').exists():
                return result
            delay = min(300, 60 * 2 ** min(attempt, 3)) if reason == 'organization storage inventory incomplete; upload denied' else 10
            time.sleep(delay)
            continue
        if result.get('status') != 'held' or 'shard capacity' not in result.get('held_reason', ''):
            if callback and result.get('status') == 'pushed' and result.get('restore_verified'):
                callback(captured)
            return result
        shard = int(plane['name'].rsplit('-', 1)[1]) + 1
        refreshed = ensure_plane(run, shard)
        plane.clear()
        plane.update(refreshed)
        atomic_json(run / getattr(TRANSPORT, 'shard_file', 'active-shard.json'), {'shard': shard})
    raise RuntimeError('too many capacity holds')


def candidates(inventory, db, run, lane, after):
    """Keyset pagination avoids sorting millions of rows into a staging file."""
    root_files = lane.endswith('/@files') or lane == '@files'
    selected_lane = lane.removesuffix('/@files') if lane != '@files' else ''
    prefix = str(HOME / selected_lane) if selected_lane else str(HOME)
    low, high = prefix + '/', prefix + '/' + chr(0x10ffff)
    params = [max(after or '', low), high]
    query = "SELECT path,size,kind FROM entries INDEXED BY sqlite_autoindex_entries_1 WHERE path>? AND path<? AND kind IN ('file','git_metadata_file') AND exclusion IS NULL ORDER BY path"
    denied = tuple(p + '/' for p in protected_prefixes(run))
    own = (str(run) + '/', str(Path(backup.WS) / '_data/estate-stream-stage') + '/')
    if root_files:
        # Root-file lanes must not traverse millions of descendant entries.
        direct = [entry.path for entry in os.scandir(prefix)
                  if entry.is_file(follow_symlinks=False) and entry.path > (after or '')]
        if not direct:
            return
        query = ("SELECT path,size,kind FROM entries WHERE path IN (" + ','.join('?' for _ in direct)
                 + ") AND kind IN ('file','git_metadata_file') AND exclusion IS NULL ORDER BY path")
        params = direct
    for row in inventory.execute(query, params):
        path, size = row['path'], row['size']
        if root_files and str(Path(path).parent) != prefix:
            continue
        if path.startswith(denied) or path.startswith(own):
            continue
        existing = db.execute('SELECT status FROM coverage WHERE path=?', (path,)).fetchone()
        if existing:
            continue
        try:
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode):
                raise OSError('not a regular file')
        except OSError:
            db.execute('INSERT OR REPLACE INTO coverage(path,status,source_bytes,note) VALUES (?,?,?,?)',
                       (path, 'unreadable-or-vanished', size, 'rescan required'))
            db.execute('INSERT OR IGNORE INTO retries VALUES (?,?,?)', (path, 1, time.time() + 120))
            continue
        if st.st_size > MAX_FILE:
            db.execute('INSERT OR REPLACE INTO coverage(path,status,source_bytes,note) VALUES (?,?,?,?)',
                       (path, 'large-file-pending', st.st_size, 'bounded fragment preservation required'))
            continue
        yield path, st.st_size


def record_batch(db, run, plane, paths, run_id, result, verified):
    receipt = run / 'batch-receipts' / (run_id + '.json')
    receipt.parent.mkdir(exist_ok=True)
    atomic_json(receipt, {'result': result, 'verified_files': verified})
    if result.get('status') != 'pushed' or not result.get('restore_verified'):
        raise RuntimeError('batch publication not remotely verified')
    if len(verified) != len(paths) or {str(HOME / row['path']) for row in verified} != set(paths):
        raise RuntimeError('completed batch file coverage differs')
    url = f"https://github.com/{result['repo']}/tree/{result['commit']}"
    for row in verified:
        path = str(HOME / row['path'])
        if path not in paths:
            raise RuntimeError('verified batch source differs')
        source_size = row['source_stat']['file']['size']
        db.execute('INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?,?,?)',
                   (path, 'verified-bytes', source_size, row['sha256'], json.dumps(row['source_stat']), url, run_id, None))
        db.execute('DELETE FROM retries WHERE path=?', (path,))
    db.execute('INSERT OR REPLACE INTO batches VALUES (?,?,?)', (run_id, 'verified', str(receipt)))
    db.commit()


def retry_candidates(db, lanes=None):
    rows = db.execute('SELECT c.path FROM coverage c JOIN retries r ON r.path=c.path '
                      'WHERE c.status IN (?,?) AND r.after_time<=? ORDER BY r.after_time LIMIT 100',
                      ('source-changed-or-unreadable', 'unreadable-or-vanished', time.time())).fetchall()
    for row in rows:
        if lanes is not None and not lane_contains(row['path'], lanes):
            continue
        try:
            value = os.lstat(row['path'])
        except OSError:
            continue
        if stat.S_ISREG(value.st_mode) and value.st_size <= MAX_FILE:
            yield row['path'], value.st_size


def summary(db, run):
    values = [dict(row) for row in db.execute('SELECT status,count(*) files,sum(source_bytes) bytes FROM coverage GROUP BY status')]
    result = {'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'coverage': values,
              'floor_bytes': backup.STREAM_FLOOR, 'staging_cap_bytes': backup.STREAM_CAP}
    atomic_json(run / 'backup-progress.json', result)
    return result


def upload_large_file(run, db, plane, path):
    """Resume verified groups; count the source only after its descriptor is remote."""
    path = Path(path)
    relative = str(path.relative_to(HOME))
    if any(str(path) == p or str(path).startswith(p + '/') for p in protected_prefixes(run)):
        raise RuntimeError('large source is protected')
    plan = fragments.plan_file(path)
    plan_key = hashlib.sha256(json.dumps({'path': str(path), 'plan': plan}, sort_keys=True).encode()).hexdigest()
    directory = run / 'large-files' / plan_key
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(directory / 'plan.json', {'path': str(path), 'plan': plan})
    descriptors, locations = [], []
    for number, group in enumerate(fragments.fragment_groups(plan)):
        receipt_path = directory / f'group-{number:06}.json'
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        result = receipt.get('result', {})
        if result.get('status') != 'pushed' or not result.get('restore_verified'):
            factory = fragments.archive_factory(group, path, relative, encrypt=True, recipient=backup.recipient())
            run_id = 'home-fragment-' + plan_key[:16] + '-' + str(number) + '-' + uuid.uuid4().hex[:8]
            result = upload_with_capacity(run, plane, [str(path)], run_id, plan_key, archive_factory=factory,
                                         archive_upper_bytes=fragments.archive_upper_bytes(group))
            if result.get('status') != 'pushed' or not result.get('restore_verified'):
                atomic_json(run / 'backup-hold.json', result)
                raise RuntimeError('large fragment transport held')
            receipt = {'result': result, 'descriptor': factory.last_state['descriptor']}
            atomic_json(receipt_path, receipt)
        descriptors.append(receipt['descriptor'])
        locations.append({'group': number, 'result': result})
    merged = fragments.merge_descriptors(descriptors, expected_plan=plan)
    fd = fragments._open_source(path, plan['source_identity'])
    os.close(fd)
    descriptor_path = directory / 'reconstruction.json'
    document = {'source_path': relative, 'descriptor': merged, 'groups': locations}
    # Preserve mtime and identity across resume when the descriptor is unchanged.
    if not descriptor_path.exists() or json.loads(descriptor_path.read_text()) != document:
        atomic_json(descriptor_path, document)
    fingerprint = hashlib.sha256(descriptor_path.read_bytes()).hexdigest()
    published_path = directory / 'publication.json'
    publication = json.loads(published_path.read_text()) if published_path.exists() else {}
    result = publication.get('result', {})
    if publication.get('sha256') != fingerprint or result.get('status') != 'pushed' or not result.get('restore_verified'):
        verified = []
        run_id = 'home-descriptor-' + plan_key[:16] + '-' + uuid.uuid4().hex[:8]
        result = upload_with_capacity(run, plane, [str(descriptor_path)], run_id, fingerprint,
                                      on_verified_files=verified.extend)
        if result.get('status') != 'pushed' or not result.get('restore_verified') or len(verified) != 1 or verified[0]['sha256'] != fingerprint:
            raise RuntimeError('large reconstruction descriptor verification incomplete')
        atomic_json(published_path, {'sha256': fingerprint, 'result': result})
    url = f"https://github.com/{result['repo']}/tree/{result['commit']}"
    db.execute('INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?,?,?)',
               (str(path), 'verified-fragments', plan['logical_size'], None,
                json.dumps({'file': plan['source_identity'], 'descriptor_sha256': fingerprint}),
                url, result['ref'], 'all sparse extents verified; reconstruction descriptor encrypted and verified'))
    db.commit()


def upload_refetch_manifest(run, db, plane):
    path = run / 'refetch/npm-refetch-manifest-source-verified.json'
    if not path.exists():
        return
    manifest = json.loads(path.read_text())
    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
    receipt_path = run / 'refetch-upload.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if (receipt.get('manifest_sha256') != fingerprint or receipt.get('result', {}).get('status') != 'pushed'
            or not receipt.get('result', {}).get('restore_verified')):
        verified = []
        run_id = 'home-refetch-' + time.strftime('%Y%m%dT%H%M%S')
        result = upload_with_capacity(run, plane, [str(path)], run_id, fingerprint,
                                      on_verified_files=verified.extend)
        if result.get('status') != 'pushed' or not result.get('restore_verified'):
            raise RuntimeError('re-fetch manifest remote verification incomplete')
        if len(verified) != 1 or verified[0]['sha256'] != fingerprint:
            raise RuntimeError('re-fetch manifest changed during capture')
        receipt = {'manifest_sha256': fingerprint, 'result': result}
        atomic_json(receipt_path, receipt)
    result = receipt['result']
    url = f"https://github.com/{result['repo']}/tree/{result['commit']}"
    source_root = Path(manifest['source_root'])
    if source_root != HOME / '.npm/_cacache':
        raise RuntimeError('re-fetch source root differs from verified cache')
    for entry in manifest.get('entries', []):
        if entry.get('source_verification_status') != 'verified_public':
            continue
        file = entry['covered_file']
        relative = Path(file['path'])
        source = source_root / relative
        if relative.is_absolute() or '..' in relative.parts or not source.is_relative_to(source_root):
            raise RuntimeError('re-fetch coverage escaped its exact cache boundary')
        try:
            fd, _, before = backup.safe_source.open_source(source_root, str(relative))
            digest = hashlib.sha512()
            with os.fdopen(fd, 'rb') as handle:
                for block in iter(lambda: handle.read(1_048_576), b''):
                    digest.update(block)
                after = backup.safe_source.identity(os.fstat(handle.fileno()))
        except OSError:
            continue
        if before != after or before['size'] != file['size'] or digest.hexdigest() != file['sha512']:
            continue
        db.execute('INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?,?,?)',
                   (str(source), 'verified-refetch', file['size'], None, json.dumps({'file': before, 'sha512': file['sha512']}),
                    url, result['ref'], 'anonymous source GET hash matched; encrypted re-fetch manifest verified'))
    db.commit()


def upload_refetch_members(run, db, plane):
    """Publish exact package-member recipes encrypted, then credit unchanged source files."""
    path = run / 'refetch-trees/exact-members.json'
    if not path.exists():
        return {'added_files': 0, 'added_bytes': 0}
    payload = path.read_bytes()
    manifest = json.loads(payload)
    if (not isinstance(manifest, dict)
            or manifest.get('schema') != 'siso-estate.exact-refetch-members.v1'
            or manifest.get('done') is not True
            or not isinstance(manifest.get('covered_files'), list)):
        raise ValueError('unknown or unfinished exact member manifest')
    fingerprint = hashlib.sha256(payload).hexdigest()
    receipt_path = run / 'refetch-trees/exact-members-upload.json'
    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
    if (receipt.get('manifest_sha256') != fingerprint or receipt.get('result', {}).get('status') != 'pushed'
            or not receipt.get('result', {}).get('restore_verified')):
        verified = []
        run_id = 'home-members-' + time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
        result = upload_with_capacity(run, plane, [str(path)], run_id, fingerprint,
                                      on_verified_files=verified.extend)
        if (result.get('status') != 'pushed' or not result.get('restore_verified') or len(verified) != 1
                or verified[0]['sha256'] != fingerprint):
            raise RuntimeError('exact member manifest publication incomplete')
        receipt = {'manifest_sha256': fingerprint, 'result': result}
        atomic_json(receipt_path, receipt)
    result = receipt['result']
    url = f"https://github.com/{result['repo']}/tree/{result['commit']}"
    denied = protected_prefixes(run)
    count, total = 0, 0
    for row in manifest['covered_files']:
        source = Path(row['absolute_path'])
        if not source.is_absolute() or not source.is_relative_to(HOME) or '..' in source.parts:
            raise ValueError('exact member source outside home')
        if any(str(source) == p or str(source).startswith(p + '/') for p in denied):
            continue
        if (not isinstance(row.get('sha256'), str) or len(row['sha256']) != 64
                or any(ch not in '0123456789abcdef' for ch in row['sha256'])
                or not row.get('source_url', '').startswith('https://registry.npmjs.org/')
                or not row.get('source_integrity', '').startswith('sha512-')
                or not row.get('archive_member', '').startswith('package/')):
            raise ValueError('exact member reconstruction evidence incomplete')
        existing = db.execute('SELECT status FROM coverage WHERE path=?', (str(source),)).fetchone()
        if existing and existing['status'].startswith('verified-'):
            continue
        try:
            fd, _, before = backup.safe_source.open_source(HOME, str(source.relative_to(HOME)))
            os.close(fd)
        except (OSError, RuntimeError, ValueError):
            continue
        if before != row['source_stat'] or before['size'] != row['size']:
            continue
        db.execute('INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?,?,?)',
                   (str(source), 'verified-refetch', row['size'], row['sha256'], json.dumps({'file': before}),
                    url, result['ref'], 'exact installed member matches verified public archive; encrypted recipe verified'))
        db.execute('DELETE FROM retries WHERE path=?', (str(source),))
        count += 1
        total += row['size']
        if count % 1000 == 0:
            db.commit()
    db.commit()
    value = {'added_files': count, 'added_bytes': total, 'manifest_sha256': fingerprint}
    atomic_json(run / 'refetch-trees/exact-members-coverage.json', value)
    return value


SNAPSHOT_HOLD = 'streamed source identity could not be captured safely'
BOUNDARY_HOLD = ('streaming source escaped declared roots, entered an excluded or protected boundary, '
                 'or used a linked path')


def quarantine_unsnapshottable(db, plane, batch, batch_sizes, reason=SNAPSHOT_HOLD):
    """Probe each file of a held batch; record the ones the guard refuses so the lane keeps going.
    The guards themselves are unchanged: refused files are simply not uploaded."""
    root = backup.root_of(plane)
    bad = []
    for path in batch:
        try:
            if reason == BOUNDARY_HOLD:
                if not backup._stream_source_boundary(plane, [path]):
                    raise ValueError('boundary refused')
            else:
                backup.safe_source.snapshot(Path(root).resolve(strict=True), [os.path.relpath(path, root).replace(os.sep, '/')])
        except (OSError, RuntimeError, ValueError):
            bad.append(path)
            db.execute('INSERT OR REPLACE INTO coverage(path,status,source_bytes,note) VALUES (?,?,?,?)',
                       (path, 'unreadable-or-vanished', batch_sizes.get(path), 'guard refused; rescan required'))
            db.execute('INSERT OR IGNORE INTO retries VALUES (?,?,?)', (path, 1, time.time() + 3600))
    db.commit()
    return bad


def run_batches(run, max_batches, lanes=None, worker=None, max_streams=1):
    TRANSPORT.max_streams = max_streams
    TRANSPORT.worker = worker
    TRANSPORT.shard_file = f'active-shard-worker-{worker}.json' if worker is not None else 'active-shard.json'
    db = open_state(run)
    if worker is None:
        import_legal(run, db)
    inventory_path = run / 'inventory-all.sqlite'
    inventory = sqlite3.connect(inventory_path.resolve().as_uri() + '?mode=ro', uri=True)
    inventory.row_factory = sqlite3.Row
    if not inventory.execute("SELECT 1 FROM scan_info WHERE key='completed_at'").fetchone():
        raise RuntimeError('all-content inventory is still running')
    shard_path = run / TRANSPORT.shard_file
    shard, completed = (json.loads(shard_path.read_text())['shard'] if shard_path.exists() else (1000 * (worker + 1) if worker is not None else 1)), 0
    with (run / (f'home-backup-worker-{worker}.lock' if worker is not None else 'home-backup.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        plane = ensure_plane(run, shard)
        if worker is None or worker == 0:
            upload_refetch_manifest(run, db, plane)
        for lane in ['@retry'] + (lanes if lanes is not None else PRIORITY + ['']):
            cursor = db.execute('SELECT path FROM cursors WHERE lane=?', (lane,)).fetchone()
            iterator = retry_candidates(db, lanes) if lane == '@retry' else candidates(inventory, db, run, lane, cursor['path'] if cursor else '')
            exhausted = False
            while not exhausted:
                if (run / 'stop-after-batch').exists():
                    return
                while os.getloadavg()[0] >= 28:
                    atomic_json(run / f'worker-{worker}-load-wait.json', {'load': os.getloadavg()[0], 'at': time.time()})
                    time.sleep(10)
                    if (run / 'stop-after-batch').exists():
                        return
                batch, batch_sizes, total, last = [], {}, 0, None
                while len(batch) < BATCH_FILES and total < BATCH_BYTES:
                    try:
                        path, size = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    batch.append(path)
                    batch_sizes[path] = size
                    total += size
                    last = path
                    # Live databases are isolated so one changing source cannot invalidate a large batch.
                    if lane == '@retry' or path.endswith(('.db', '.sqlite', '.sqlite3', '-wal', '-shm')):
                        break
                db.commit()
                if not batch:
                    break
                run_id = 'home-' + time.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8]
                verified, producer = [], {}
                def capture_state(state):
                    producer['state'] = state
                print(json.dumps({'phase': 'upload', 'files': len(batch), 'source_bytes': total, 'shard': plane['name'], 'worker': worker}), flush=True)
                try:
                    result = upload_with_capacity(run, plane, batch, run_id, run_id,
                                                 on_producer_state=capture_state, on_verified_files=verified.extend)
                    if result.get('status') == 'held' and (run / 'stop-after-batch').exists():
                        return
                    if result.get('status') == 'held' and result.get('held_reason') in (SNAPSHOT_HOLD, BOUNDARY_HOLD):
                        bad = quarantine_unsnapshottable(db, plane, batch, batch_sizes, result['held_reason'])
                        if bad:
                            atomic_json(run / 'last-batch-error.json', {'run_id': run_id, 'type': 'SnapshotHold',
                                                                      'message': result['held_reason'], 'quarantined': bad})
                            summary(db, run)
                            if lane != '@retry':
                                iterator = candidates(inventory, db, run, lane, cursor['path'] if cursor else '')
                                exhausted = False
                            continue
                    if result.get('status') == 'held':
                        if result.get('status') != 'pushed':
                            atomic_json(run / 'backup-hold.json', result)
                            raise RuntimeError('transport admission held; inspect private receipt')
                    record_batch(db, run, plane, batch, run_id, result, verified)
                except Exception as error:
                    state = producer.get('state', {})
                    failed_path = state.get('current_source') if state.get('source_failure') else None
                    atomic_json(run / 'last-batch-error.json', {'run_id': run_id, 'type': type(error).__name__,
                                                              'message': str(error), 'failed_source': failed_path})
                    if not failed_path:
                        raise
                    # Retry the rest on the next invocation; the cursor does not move past unverified data.
                    db.execute('INSERT OR REPLACE INTO coverage(path,status,source_bytes,note) VALUES (?,?,?,?)',
                               (failed_path, 'source-changed-or-unreadable', batch_sizes.get(failed_path), 'retry from a stable source'))
                    previous = db.execute('SELECT attempts FROM retries WHERE path=?', (failed_path,)).fetchone()
                    attempts = previous['attempts'] + 1 if previous else 1
                    db.execute('INSERT OR REPLACE INTO retries VALUES (?,?,?)',
                               (failed_path, attempts, time.time() + min(3600, 60 * 2 ** min(attempts, 6))))
                    db.commit()
                    summary(db, run)
                    if lane != '@retry':
                        # Re-select the unverified remainder; don't lose earlier files in this failed batch.
                        iterator = candidates(inventory, db, run, lane, cursor['path'] if cursor else '')
                        exhausted = False
                    continue
                if last and lane != '@retry':
                    db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?)', (lane, last))
                    db.commit()
                    cursor = {'path': last}
                completed += 1
                print(json.dumps(summary(db, run)), flush=True)
                if max_batches and completed >= max_batches:
                    return
        summary(db, run)
        for row in db.execute("SELECT path FROM coverage WHERE status='large-file-pending' ORDER BY source_bytes,path").fetchall():
            if lanes is not None and not lane_contains(row['path'], lanes):
                continue
            if (run / 'stop-after-batch').exists():
                return
            upload_large_file(run, db, plane, row['path'])
            completed += 1
            print(json.dumps(summary(db, run)), flush=True)
            if max_batches and completed >= max_batches:
                return


def lane_contains(path, lanes):
    for lane in lanes:
        if lane == '@files' or lane.endswith('/@files'):
            parent = HOME / (lane.removesuffix('/@files') if lane != '@files' else '')
            if Path(path).parent == parent:
                return True
        else:
            root = str(HOME / lane)
            if path.startswith(root + '/'):
                return True
    return False


def parallel_lanes(run, count):
    """Partition directory roots without overlaps; keep home/workspace root files."""
    rows = [('@files', 0)]
    with sqlite3.connect((run / 'inventory-all.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as inv:
        # Depth-2 roots split the two biggest producers; all other top-level trees stay whole.
        split = {'SISO_Workspace', 'Library'}
        for directory, depth, size in inv.execute('SELECT directory,depth,allocated_bytes FROM directory_summary WHERE depth IN (1,2)'):
            rel = Path(directory).relative_to(HOME)
            if depth == 1:
                if str(rel) in split:
                    rows.append((str(rel) + '/@files', 0))
                else:
                    rows.append((str(rel), size))
            elif rel.parts[0] in split:
                rows.append((str(rel), size))
    groups, sizes = [[] for _ in range(count)], [0] * count
    for lane, size in sorted(rows, key=lambda x: (-x[1], x[0])):
        selected = min(range(count), key=lambda n: sizes[n])
        groups[selected].append(lane)
        sizes[selected] += size
    return groups


def run_parallel(run, count, max_batches=0):
    """One coordinator owns the legacy lock; workers have disjoint source roots."""
    with (run / 'home-backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = open_state(run)
        import_legal(run, db)
        db.close()
        groups = parallel_lanes(run, count)
        atomic_json(run / 'parallel-lanes.json', {'workers': groups, 'started_at': time.time()})
        def work(number, lanes):
            atomic_json(run / f'worker-{number}-exit.json', {'status': 'running', 'at': time.time(), 'pid': os.getpid()})
            try:
                run_batches(run, max_batches, lanes, number, count)
                atomic_json(run / f'worker-{number}-exit.json', {'status': 'completed', 'at': time.time()})
            except Exception as error:
                atomic_json(run / f'worker-{number}-exit.json', {'status': 'failed', 'type': type(error).__name__, 'at': time.time()})
                raise
        with ThreadPoolExecutor(max_workers=count) as pool:
            futures = [pool.submit(work, number, lanes) for number, lanes in enumerate(groups)]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as error:
                    # Do not leave a live coordinator silently waiting on surviving lanes.
                    atomic_json(run / 'parallel-failure.json', {'status': 'failed', 'type': type(error).__name__, 'at': time.time()})
                    if not (run / 'stop-after-batch').exists():
                        atomic_json(run / 'stop-after-batch', {'reason': 'parallel worker failed; stop remaining lanes after verified batch', 'at': time.time()})
                    raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run', type=Path)
    parser.add_argument('--max-batches', type=int, default=0)
    parser.add_argument('--parallel', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--refetch-only', action='store_true')
    args = parser.parse_args()
    if args.refetch_only:
        TRANSPORT.max_streams = args.parallel
        TRANSPORT.worker = 'refetch'
        TRANSPORT.shard_file = 'active-shard-refetch.json'
        with (args.run / 'refetch-publication.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            db = open_state(args.run)
            shard_path = args.run / TRANSPORT.shard_file
            shard = json.loads(shard_path.read_text())['shard'] if shard_path.exists() else 4000
            print(json.dumps(upload_refetch_members(args.run, db, ensure_plane(args.run, shard))), flush=True)
            print(json.dumps(summary(db, args.run)), flush=True)
            db.close()
    elif args.parallel > 1:
        run_parallel(args.run, args.parallel, args.max_batches)
    else:
        run_batches(args.run, args.max_batches)
