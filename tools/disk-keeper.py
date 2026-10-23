#!/usr/bin/env python3
"""Bounded daily disk inventory, guarded expiry, Git ledger and read-only latest JSON.

Default: latest receipt. --dry-run measures without mutation; --run enforces and records.
All sizes use decimal GB; incomplete measurements remain explicit unknowns.
"""
import argparse
import datetime as dt
import fcntl
import fnmatch
import glob
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import socket
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
CORE = HERE.parent
ROOT = CORE.parents[2]
POLICY = CORE / 'plan/disk-classes.json'
GB = 10**9


def read(path, default):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, sort_keys=True) + '\n')
    tmp.replace(path)


def call(args, cwd=None, timeout=30):
    return subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def git(path, *args):
    r = call(['git', '-C', path, *args])
    if r.returncode:
        raise RuntimeError('git ' + args[0] + ' failed (output withheld)')
    return r.stdout.strip()


def module(name):
    path = ROOT / 'agents/harness/ci/retention.py' if name == 'ci-retention' else HERE / (name + '.py')
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), path)
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    return obj


def match(path, pattern):
    # A class applies to its descendants. '**' crosses directories; '*' is one component.
    a, b = Path(path).parts, Path(pattern).parts
    def rec(i, j):
        if j == len(b):
            return True
        if b[j] == '**':
            return rec(i, j + 1) or (i < len(a) and rec(i + 1, j))
        return i < len(a) and fnmatch.fnmatchcase(a[i], b[j]) and rec(i + 1, j + 1)
    return rec(0, 0)


def classify(path, policy):
    for rule in policy['rules']:
        if any(match(path, g) for g in rule['globs']):
            return rule
    return dict(id='unclassified', **{'class': 'unclassified'}, owner='estate', restore='unresolved')


def canonical(path, home):
    return not path.is_symlink() and path.resolve() == path and path.is_relative_to(home)


def discover(home, policy):
    """Only immediate children of declared roots; bounded repo walk for node_modules markers."""
    paths = set()
    errors = []
    deadline = time.monotonic() + policy['discovery_budget_seconds']
    for parent in [home] + [Path(p) for pat in policy['scan_roots'] for p in glob.glob(str(home / pat))]:
        if not canonical(parent, home) or not parent.is_dir():
            continue
        try:
            paths.update(p for p in parent.iterdir() if canonical(p, home))
        except OSError:
            errors.append(str(parent.relative_to(home)) + ': unreadable')
    # Do not run du on home, workspace, Library or other expanded containers.
    parents = {p.parent for p in paths}
    paths.difference_update(parents)
    # Known large module dependencies must not depend on a slow Git manifest walk.
    for pattern in policy.get('dependency_roots', []):
        for name in glob.glob(str(home / pattern)):
            dep = Path(name)
            if canonical(dep, home) and dep.is_dir() and not classify(str(dep.relative_to(home)), policy).get('protected'):
                paths.add(dep)
    # Expiry roots must be discoverable even when their parent is not a source checkout.
    for rule in policy['rules']:
        if rule.get('action') == 'rebuildable':
            for pattern in rule['globs']:
                if '**' not in pattern:
                    paths.update(Path(p) for p in glob.glob(str(home / pattern))
                                 if canonical(Path(p), home))
    def has_git(p):
        try:
            return (p / '.git').exists()
        except OSError:  # root-owned or TCC-denied directories must not abort the whole keeper
            errors.append(str(p.relative_to(home)) + ': git marker unreadable')
            return False
    lanes = sorted(p for p in paths if not classify(str(p.relative_to(home)), policy).get('protected') and has_git(p))
    visited = 0
    for lane in lanes:
        if time.monotonic() >= deadline:
            errors.append('dependency discovery budget exhausted')
            break
        try:
            result = call(['git', '-C', lane, 'ls-files', '-z', '--', 'package.json', '**/package.json'],
                          timeout=max(.1, min(5, deadline-time.monotonic())))
            if result.returncode:
                errors.append(str(lane.relative_to(home)) + ': package inventory unavailable')
                continue
            for name in result.stdout.split('\0'):
                if not name:
                    continue
                visited += 1
                if visited > policy['discovery_max_dirs']:
                    errors.append('dependency discovery budget exhausted')
                    return sorted(paths), errors
                dep = lane / Path(name).parent / 'node_modules'
                if dep.is_dir() and canonical(dep, home) and not classify(str(dep.relative_to(home)), policy).get('protected'):
                    paths.add(dep)
        except (OSError, subprocess.SubprocessError):
            errors.append(str(lane.relative_to(home)) + ': package inventory unavailable')
    return sorted(paths), errors


def measure(paths, home, policy):
    deadline = time.monotonic() + policy['measure_budget_seconds']
    sizes, errors = {}, []
    # Known-reclaimable caches first; unknown roots last so they cannot exhaust the budget.
    def priority(path):
        rule = classify(str(path.relative_to(home)), policy)
        if rule['class'] == 'unclassified':
            rank = 5  # unknown trees are never candidates; they only use budget left over
        elif rule.get('action') == 'rebuildable':
            rank = 0  # cheap, known-reclaimable caches first, so exhaustion still plans them
        else:
            rank = {'source':2,'unique':2,'runtime':3,'rebuildable':3,'personal':4}[rule['class']]
        return (rank, str(path))
    ordered = sorted(paths, key=priority)
    for index, path in enumerate(ordered):
        rel = str(path.relative_to(home))
        remaining = deadline - time.monotonic()
        if remaining <= 0 or index >= policy['max_paths']:
            errors.append('measurement budget exhausted; %d paths unknown' % (len(ordered) - index))
            break
        if path.is_file():
            try:
                sizes[rel] = path.stat().st_blocks * 512
            except OSError:
                errors.append(rel + ': stat unavailable')
            continue
        if os.path.ismount(str(path)):
            errors.append(rel + ': mount skipped (external volume or vault)')
            continue
        cmd = ['nice', '-n', '19', 'du', '-sk', '-x', str(path)]
        if sys.platform == 'darwin':
            cmd = ['taskpolicy', '-b', *cmd]
        try:
            limit = policy.get('protected_path_timeout_seconds',policy['path_timeout_seconds']) if classify(rel,policy).get('protected') else policy['path_timeout_seconds']
            result = call(cmd, timeout=min(remaining, limit))
            if result.returncode:
                if path.exists():
                    errors.append(rel + ': du failed')
            else:
                sizes[rel] = int(result.stdout.split()[0]) * 1024
        except (subprocess.TimeoutExpired, ValueError, IndexError, OSError):
            errors.append(rel + ': du unavailable or timed out')
    return sizes, errors


def attributed(sizes, policy):
    totals = {c: 0 for c in ('source', 'rebuildable', 'runtime', 'unique', 'personal', 'unclassified')}
    for name, size in sizes.items():
        descendants = [p for p in sizes if p.startswith(name + '/')]
        children = [p for p in descendants if not any(p.startswith(q + '/') for q in descendants if q != p)]
        totals[classify(name, policy)['class']] += max(0, size - sum(sizes[p] for p in children))
    return totals


def release_totals(rows, sizes, policy):
    roots = {r['path']: r['bytes'] for r in rows}
    measured = {p:v for p,v in sizes.items() if any(p == r or p.startswith(r + '/') for r in roots)}
    for p,v in roots.items():
        measured.setdefault(p,v)
    return attributed(measured,policy)


def newest(path, max_entries=500000):
    """Fail closed on oversized inventories; nested edits count, keeper markers do not."""
    latest = 0
    count = 0
    if not path.is_dir():
        return path.stat().st_mtime
    for base, dirs, files in os.walk(path, followlinks=False):
        latest = max(latest, Path(base).stat().st_mtime)
        for name in dirs + files:
            if name == '.metadata_never_index':
                continue
            count += 1
            if count > max_entries:
                return None
            latest = max(latest, (Path(base) / name).lstat().st_mtime)
    return latest


def growth_alarms(home, policy):
    # Stat only: never open a live database, browser profile or private app state.
    rows = []
    for watch in policy.get('growth_watches', []):
        path = home / watch['path']
        if canonical(path, home) and path.is_file():
            size = path.stat().st_size
            if size > watch['cap_gb'] * GB:
                rows.append(dict(id=watch['id'], owner=watch['owner'], path=watch['path'],
                                 gb=round(size/GB, 3), cap_gb=watch['cap_gb'], action='measure only; owner growth alarm'))
    return rows


def ci_cleanup(home, policy, machine, sizes, busy, dry_run):
    """Reuse the CI owner's 48-hour, worker-lock and fresh remote-custody gates."""
    root = home / 'SISO_Workspace/_data/siso-ci-work'
    if not canonical(root, home) or not root.is_dir():
        return []
    retention = module('ci-retention')
    preservation = module('worktree-wip')
    measured = {}
    deadline = time.monotonic() + 90
    def size(path):
        rel = str(Path(path).relative_to(home))
        measured[rel] = sizes[rel] if rel in sizes else retention.tree_bytes(path)
        return measured[rel]
    # Always measure before removal, so reported recovery is tied to removed paths.
    def safe(path):
        if time.monotonic() >= deadline or any(p == str(path) or p.startswith(str(path) + '/') for p in busy):
            return False
        if not retention.preserved_clone(path):
            return False
        # The source owner also guards stashes, ignored payloads and outside linked worktrees.
        result = preservation.process(str(path), False, True, busy, preservation.seat_cwds())
        return result.get('result') == 'would retire verified standalone clone'
    floor = policy['machines'][machine]['free_floor_gb'] * GB / retention.GIB
    planned = retention.prune(root, floor_gib=floor, margin_gib=0, days=2, dry_run=True,
                             pnpm=False, size_fn=size, safe_fn=safe, log=lambda *a: None)
    if not dry_run:
        selected = {str(Path(p).relative_to(home)) for p in planned}
        suffix = '.estate-retiring-' + str(os.getpid())
        removed = retention.prune(root, floor_gib=floor, margin_gib=0, days=2, dry_run=False,
                                 pnpm=False, safe_fn=lambda p: str(Path(p).relative_to(home)).removesuffix(suffix) in selected and safe(p),
                                 log=lambda *a: None)
        planned = removed
    return [dict(path=str(Path(p).relative_to(home)), bytes=measured[str(Path(p).relative_to(home))],
                 **{'class':'source'}, action='ci-retire') for p in planned]


def select(rule, size, last_used, now, preserved_at=None):
    """Pure eligibility policy. Activity/remote/preservation gates run again at mutation."""
    ttl = rule.get('ttl_hours')
    if rule.get('protected') or not ttl or last_used is None:
        return 'hold'
    over_cap = size > rule.get('cap_gb', float('inf')) * GB
    cutoff = 24 * 3600 if rule.get('action') == 'rebuildable' and over_cap else ttl * 3600
    if now - last_used < cutoff:
        return 'hold'
    if rule.get('action') == 'rebuildable':
        return 'expire'
    if rule.get('action') == 'worktree':
        return 'retire' if preserved_at is not None and now - preserved_at >= ttl * 3600 else 'preserve'
    return 'hold'


def fingerprint(path):
    digest = hashlib.sha256((git(path, 'rev-parse', 'HEAD') + '\n' + git(path, 'status', '--porcelain', '--untracked-files=all')).encode())
    changed = git(path, 'ls-files', '--modified', '--others', '--exclude-standard', '-z')
    for name in sorted(set(changed.split('\0')) - {''}):
        file = path / name
        digest.update(name.encode())
        if file.is_symlink():
            digest.update(os.readlink(file).encode())
        elif file.is_file():
            stat = file.stat()
            digest.update(str((stat.st_size, stat.st_mtime_ns)).encode())
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024*1024), b''):
                    digest.update(chunk)
    return digest.hexdigest()


def lane_used(path):
    admin = Path(git(path, 'rev-parse', '--absolute-git-dir'))
    if (admin / 'locked').exists():
        return None
    # Authored edits under a directory matter too; preservation resets on a later diff.
    points = [p.stat().st_mtime for p in (path / '.git', admin / 'HEAD', admin / 'logs/HEAD', admin / 'index') if p.exists()]
    return max(points) if points else None


def candidates(home, sizes, policy, state, busy):
    rows = []
    now = time.time()
    for rel, size in sorted(sizes.items()):
        rule = classify(rel, policy)
        path = home / rel
        if rule.get('action') not in ('worktree', 'rebuildable') or rule.get('protected') or path.name in policy['protected_lane_names']:
            continue
        # Never retire nested dependency roots in a worktree selected for preservation.
        if any(part.lower() in ('halo', 'halocrm', 'fahmy', 'life', 'lifelock', 'whatsapp') for part in path.parts):
            continue
        try:
            if not canonical(path, home) or any(b == str(path) or b.startswith(str(path) + '/') for b in busy):
                continue
            if path.name == 'node_modules' and any(b == str(path.parent) or b.startswith(str(path.parent) + '/') for b in busy):
                continue
            if rule['id'] == 'ci-scratch' and (not (path / '.siso-ci-generated').is_file() or (path / '.siso-ci-failed').exists()):
                continue
            if rule['action'] == 'worktree':
                if not (path / '.git').is_file():
                    continue
                used = lane_used(path)
                if select(rule, size, used, now) == 'hold':
                    continue
                fp = fingerprint(path)
                saved = state.get(rel, {})
                preserved = saved.get('at') if saved.get('fingerprint') == fp else None
                action = select(rule, size, used, now, preserved)
            else:
                fp = None
                used = newest(path)
                action = select(rule, size, used, now)
            if action != 'hold':
                rows.append(dict(path=rel, **{'class':rule['class']}, bytes=size, action=action, fingerprint=fp))
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            print('candidate skipped: %s: %s' % (rel, error), file=sys.stderr)
            continue
    return rows


def mark(paths, home, policy):
    count = 0
    roots = set(paths)
    roots.update(p.parent for p in paths if p.parent.parent.name == 'worktrees')
    for path in sorted(roots):
        rel = path.relative_to(home)
        if classify(str(rel), policy).get('protected'):
            continue
        if path.is_dir() and (path.name == 'node_modules' or path.parent.name == 'worktrees' or path.parent.parent.name == 'worktrees') and canonical(path, home):
            marker = path / '.metadata_never_index'
            if not os.path.lexists(marker):
                marker.touch(exist_ok=False)
                count += 1
    return count


def runtime_plan(home, busy, now, policy=None):
    """Only the codex-run producer's closed per-run artifacts; native stores never enter."""
    root = home / '.local/state/codex-run/runs'
    rule = classify('.local/state/codex-run', policy or read(POLICY, {}))
    ttl = rule['ttl_hours']
    if not canonical(root, home) or not root.is_dir():
        return []
    rows = []
    for index, path in enumerate(sorted(root.iterdir())):
        if index >= 20000:
            break
        if not path.is_file() or not canonical(path, home):
            continue
        if not path.name.endswith(('.jsonl', '.last.md', '.meta.json', '.brief.md')):
            continue
        if path.stat().st_mtime > now - ttl*3600 or any(p == str(path) for p in busy):
            continue
        rows.append(dict(path=str(path.relative_to(home)), bytes=path.stat().st_size, ttl_hours=ttl))
    return rows


def archive_runtime(rows, home, machine, wip):
    archived = []
    deadline = time.monotonic() + 90
    for row in rows:
        if time.monotonic() >= deadline:
            break
        source = home / row['path']
        target = home / 'SISO_Workspace/_archive/disk-keeper-runtime' / machine / (source.name + '.gz')
        quarantine = source.with_name(source.name + '.disk-archiving-' + str(os.getpid()))
        if not canonical(source, home) or not source.exists() or source.stat().st_mtime > time.time()-row['ttl_hours']*3600:
            continue
        if target.exists() or quarantine.exists() or wip.inside(wip.open_paths(), str(source)):
            continue
        source.rename(quarantine)
        try:
            if wip.inside(wip.open_paths(), str(quarantine)):
                continue
            original = hashlib.sha256()
            target.parent.mkdir(parents=True, exist_ok=True)
            with quarantine.open('rb') as src, gzip.open(target, 'xb') as dst:
                for chunk in iter(lambda: src.read(1024*1024), b''):
                    original.update(chunk)
                    dst.write(chunk)
            restored = hashlib.sha256()
            with gzip.open(target, 'rb') as src:
                for chunk in iter(lambda: src.read(1024*1024), b''):
                    restored.update(chunk)
            if original.digest() != restored.digest() or wip.inside(wip.open_paths(), str(quarantine)):
                continue
            with (target.parent / 'MANIFEST.jsonl').open('a') as out:
                out.write(json.dumps(dict(source=row['path'], archive=target.name, sha256=original.hexdigest(), bytes=row['bytes'])) + '\n')
            quarantine.unlink()  # The closed original has a verified, lossless archived home.
            archived.append(dict(**row, compressed_bytes=target.stat().st_size))
        except (OSError, RuntimeError, subprocess.SubprocessError):
            pass  # The original is restored; an incomplete archive never authorizes removal.
        finally:
            if quarantine.exists() and not source.exists():
                quarantine.rename(source)
    return archived


def enforce(rows, home, policy, state, wip):
    released = {c: 0 for c in ('source', 'rebuildable')}
    results = []
    for row in rows:
        path = home / row['path']
        rule = classify(row['path'], policy)
        if not canonical(path, home):
            continue
        try:
            if rule['action'] == 'worktree':
                # The shared tool rechecks activity, seats, secrets, remote refs and snapshot contents.
                current = fingerprint(path)
                if current != row['fingerprint']:
                    results.append(dict(path=row['path'], result='held: changed since plan'))
                    continue
                result = wip.process(str(path), True, row['action'] == 'retire', wip.open_paths(), wip.seat_cwds())
                wip.receipt(**result)
                if result.get('result') == 'committed':
                    state[row['path']] = dict(at=state.get(row['path'], {}).get('at', time.time())
                                             if state.get(row['path'], {}).get('fingerprint') == current else time.time(), fingerprint=current)
                elif result.get('result') == 'removed':
                    released['source'] += row['bytes']
                    state.pop(row['path'], None)
                results.append(dict(path=row['path'], result=result.get('result', 'held')))
            else:
                # Only declared cache roots. Recheck age and open files after atomic quarantine.
                if wip.inside(wip.open_paths(), str(path)) or wip.inside(wip.seat_cwds(), str(path)):
                    continue
                if rule['id'] == 'ci-scratch' and (not (path / '.siso-ci-generated').is_file() or (path / '.siso-ci-failed').exists()):
                    continue
                if select(rule, row['bytes'], newest(path), time.time()) != 'expire':
                    continue
                if path.name == 'node_modules':
                    parent = path.parent
                    if wip.inside(wip.open_paths(), str(parent)) or wip.inside(wip.seat_cwds(), str(parent)):
                        continue
                    if not (parent / 'package.json').is_file() or not git(parent, 'check-ignore', str(path)):
                        continue
                    if git(parent, 'ls-files', '--', str(path)):
                        continue
                target = path.with_name(path.name + '.disk-expiring-' + str(os.getpid()))
                if target.exists():
                    continue
                # Rename changes the root directory ctime, not its mtime.
                path.rename(target)
                try:
                    if wip.inside(wip.open_paths(), str(target)) or select(rule, row['bytes'], newest(target), time.time()) != 'expire':
                        continue
                    # A cache containing a Git store is unique source, irrespective of its name.
                    if any('.git' in ds or '.git' in fs for _, ds, fs in os.walk(target, followlinks=False)):
                        continue
                    if rule['id'] == 'ci-scratch' and (target / '.siso-ci-failed').exists():
                        continue
                    if target.is_dir():
                        shutil.rmtree(target)
                    elif rule['id'] == 'npm-temp-packs' and target.is_file():
                        target.unlink()
                    else:
                        continue
                    released['rebuildable'] += row['bytes']
                    results.append(dict(path=row['path'], result='expired declared rebuildable'))
                finally:
                    if target.exists() and not path.exists():
                        target.rename(path)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            results.append(dict(path=row['path'], result='held: preservation or activity check unavailable'))
    return released, results


def ledger(summary, directory, origin, machine):
    """Small dedicated Git tree: no source checkout, raw logs, filenames or transcripts."""
    branch = 'estate/disk-ledger-' + machine
    directory.mkdir(parents=True, exist_ok=True)
    if not (directory / '.git').exists():
        git(directory, 'init', '-q', '-b', branch)
        git(directory, 'remote', 'add', 'origin', origin)
        remote = git(directory, 'ls-remote', '--heads', 'origin', 'refs/heads/' + branch)
        if remote:
            git(directory, 'fetch', '-q', 'origin', branch)
            git(directory, 'checkout', '-q', '-B', branch, 'FETCH_HEAD')
    if git(directory, 'status', '--porcelain', '--untracked-files=all'):
        # A failed prior push/commit is recoverable; never replace unknown files.
        known = {line[3:] for line in git(directory, 'status', '--porcelain').splitlines()}
        if known - {'ledger.jsonl'}:
            raise RuntimeError('ledger has unrelated changes')
    file = directory / 'ledger.jsonl'
    rows = [json.loads(l) for l in file.read_text().splitlines()] if file.exists() else []
    rows = [r for r in rows if (r['date'], r['machine']) != (summary['date'], machine)]
    rows.append(summary)
    file.write_text(''.join(json.dumps(r, sort_keys=True) + '\n' for r in rows))
    git(directory, 'add', '--', 'ledger.jsonl')
    if call(['git', '-C', directory, 'diff', '--cached', '--quiet']).returncode:
        git(directory, '-c', 'user.name=Estate disk keeper', '-c', 'user.email=estate@users.noreply.github.com', 'commit', '-qm', 'disk: ' + machine + ' ' + summary['date'])
    sha = git(directory, 'rev-parse', 'HEAD')
    git(directory, 'push', '-q', 'origin', 'HEAD:refs/heads/' + branch)
    if git(directory, 'ls-remote', '--heads', 'origin', 'refs/heads/' + branch).split()[0] != sha:
        raise RuntimeError('ledger remote SHA mismatch')
    return sha


def install(machine, state):
    if sys.platform != 'darwin':
        raise RuntimeError('launchd requires macOS')
    release = read(ROOT / 'disk-keeper-release.json', None)
    target_root = ROOT
    if release is None:
        sha = git(ROOT, 'rev-parse', 'HEAD')
        origin = git(ROOT, 'remote', 'get-url', 'origin')
        target_root = state / 'releases' / sha
        files = ['ops/estate/core/' + p for p in (
            'tools/disk-keeper.py', 'tools/worktree-wip.py', 'tools/estate_state.py',
            'bin/disk-keeper-daily', 'plan/disk-classes.json', 'plan/data-planes.json', 'module.json')]
        files += ['ops/estate/laptop-health/launchd/com.siso.disk-keeper.plist', 'agents/zero/bin/a0_data_home.py',
                  'agents/harness/ci/retention.py']
        files += ['ops/estate/laptop-destinations.json', 'ops/estate/laptop-destinations.rules.json',
                  'ops/estate/laptop-health/health-machines.json']
        for name in files:
            result = call(['git', '-C', ROOT, 'show', sha + ':' + name])
            if result.returncode:
                raise RuntimeError('pinned source missing: ' + name)
            content = result.stdout
            target = target_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_text() != content:
                raise RuntimeError('pinned release changed; install held')
            target.write_text(content)
        write(target_root / 'disk-keeper-release.json', dict(sha=sha, origin=origin))
    runner = target_root / 'ops/estate/core/bin/disk-keeper-daily'
    if not runner.is_file():
        raise RuntimeError('daily runner unavailable')
    template = target_root / 'ops/estate/laptop-health/launchd/com.siso.disk-keeper.plist'
    payload = plistlib.loads(template.read_bytes())
    payload['ProgramArguments'] = ['/bin/sh', str(runner), machine]
    payload['StandardOutPath'] = str(state / 'daily.log')
    payload['StandardErrorPath'] = str(state / 'daily.log')
    payload['EnvironmentVariables']['PATH'] = str(Path.home() / '.local/bin') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
    state.mkdir(parents=True, exist_ok=True)
    destination = Path.home() / 'Library/LaunchAgents/com.siso.disk-keeper.plist'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        archive = state / ('launchd-before-' + str(time.time_ns()) + '.plist')
        shutil.copy2(destination, archive)
        call(['launchctl', 'bootout', 'gui/' + str(os.getuid()), destination])
    destination.write_bytes(plistlib.dumps(payload))
    result = call(['launchctl', 'bootstrap', 'gui/' + str(os.getuid()), destination])
    if result.returncode:
        raise RuntimeError('launchctl bootstrap failed')
    command = Path.home() / '.local/bin/estate-disk'
    command.parent.mkdir(parents=True, exist_ok=True)
    script = '#!/bin/sh\n# disk-keeper installed entrypoint\nexec python3 -B ' + shlex.quote(str(target_root / 'ops/estate/core/tools/disk-keeper.py')) + ' "$@"\n'
    if command.exists() and 'disk-keeper installed entrypoint' not in command.read_text():
        raise RuntimeError('estate-disk is owned by another tool')
    command.write_text(script)
    command.chmod(0o755)
    return str(destination)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true')
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--install', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--machine', choices=('laptop','mini'), default='mini' if 'mini' in socket.gethostname().lower() else 'laptop')
    args = ap.parse_args()
    home = Path.home().resolve()
    state_dir = home / '.local/state/estate/disk-keeper'
    latest = state_dir / 'latest.json'
    if args.install:
        print(install(args.machine, state_dir))
        return 0
    if not args.run and not args.dry_run:
        summary = read(latest, dict(status='unmeasured', machine=args.machine))
        print(json.dumps(summary) if args.json else json.dumps(summary, indent=2))
        return 0 if latest.exists() else 2
    # Measurements must share the existing quick-class allocator, including manual runs.
    heavy = shutil.which('heavy')
    if os.environ.get('DISK_KEEPER_HEAVY') != '1':
        if not heavy:
            raise RuntimeError('heavy unavailable; measurement held')
        os.execvpe(heavy, [heavy, '--class', 'quick', '--', sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]], dict(os.environ, DISK_KEEPER_HEAVY='1'))
    policy = read(POLICY, {})
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'keeper.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('keeper already running')
        free_before = shutil.disk_usage(home).free
        paths, errors = discover(home, policy)
        sizes, measurement_errors = measure(paths, home, policy)
        errors += measurement_errors
        preservation = read(state_dir / 'preserved.json', {})
        wip = module('worktree-wip')
        ci_rows = []
        try:
            busy = wip.open_paths()
            plan = candidates(home, sizes, policy, preservation, busy)
            archives = runtime_plan(home, busy, time.time())
            ci_rows = ci_cleanup(home, policy, args.machine, sizes, busy, True)
            # One owner per removal: a clone/cache selected as a whole includes its children.
            roots = [r['path'] for r in plan + ci_rows if r['action'] in ('expire','retire','ci-retire')]
            plan = [r for r in plan if not any(r['path'].startswith(p + '/') for p in roots)]
        except (RuntimeError, OSError, subprocess.SubprocessError):
            errors.append('open-file inventory unavailable; all expiry held')
            plan = []
            archives = []
        released, results = {'source':0, 'rebuildable':0}, []
        archived = []
        markers = 0
        if args.run:
            released, results = enforce(plan, home, policy, preservation, wip)
            try:
                ci_removed = ci_cleanup(home, policy, args.machine, sizes, wip.open_paths(), False)
            except (RuntimeError, OSError, subprocess.SubprocessError):
                errors.append('CI retention unavailable; clones held')
                ci_removed = []
            released['source'] += sum(r['bytes'] for r in ci_removed)
            results.extend(dict(path=r['path'], bytes=r['bytes'], result='removed') for r in ci_removed)
            archived = archive_runtime(archives, home, args.machine, wip)
            released['runtime'] = sum(r['bytes']-r['compressed_bytes'] for r in archived)
            markers = mark(paths, home, policy)
            write(state_dir / 'preserved.json', preservation)
        remaining_sizes = dict(sizes)
        removed = [r['path'] for r in results if r['result'] in ('removed', 'expired declared rebuildable')]
        actual = release_totals([dict(path=r['path'], bytes=r.get('bytes',sizes.get(r['path'],0)))
                                 for r in results if r['path'] in removed], sizes, policy)
        released.update({c:actual[c] for c in ('source','rebuildable')})
        for rel in sorted(removed, key=lambda p: len(Path(p).parts)):
            amount = remaining_sizes.get(rel, 0)
            for parent in list(remaining_sizes):
                if rel.startswith(parent + '/'):
                    remaining_sizes[parent] = max(0, remaining_sizes[parent] - amount)
                elif parent == rel or parent.startswith(rel + '/'):
                    remaining_sizes.pop(parent, None)
        for row in archived:
            for parent in remaining_sizes:
                if row['path'].startswith(parent + '/'):
                    remaining_sizes[parent] = max(0, remaining_sizes[parent] - row['bytes'])
                if parent == 'SISO_Workspace/_archive':
                    remaining_sizes[parent] += row['compressed_bytes']
        totals = attributed(remaining_sizes, policy)
        today = dt.date.today().isoformat()
        ledger_dir = state_dir / 'ledger'
        history_file = ledger_dir / 'ledger.jsonl'
        history = [json.loads(l) for l in history_file.read_text().splitlines()] if history_file.exists() else []
        yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        previous = next((r for r in reversed(history) if r['date'] == yesterday and r['machine'] == args.machine), None)
        old_sizes = read(state_dir / ('sizes-' + yesterday + '.json'), {})
        common = set(remaining_sizes) & set(old_sizes)
        free = shutil.disk_usage(home).free
        current_common = attributed({p:remaining_sizes[p] for p in common}, policy)
        old_common = attributed({p:old_sizes[p] for p in common}, policy)
        growth = {c: current_common[c] - old_common[c] for c in totals} if previous and common else None
        volume_growth = round(previous['free_gb'] - free/GB, 3) if previous else None
        movers = sorted([dict(path=p, growth_gb=round((v-old_sizes[p])/GB, 3)) for p,v in sizes.items() if p in old_sizes], key=lambda r: abs(r['growth_gb']), reverse=True)[:5]
        unclassified = [dict(path=p, gb=round(v/GB, 3)) for p,v in sizes.items() if v >= policy['unclassified_gb']*GB and classify(p, policy)['class']=='unclassified']
        caps = [dict(path=p, gb=round(v/GB,3), cap_gb=classify(p,policy)['cap_gb']) for p,v in remaining_sizes.items() if v > classify(p,policy).get('cap_gb',float('inf'))*GB]
        free = shutil.disk_usage(home).free
        alerts = []
        watches = growth_alarms(home, policy)
        alerts.extend('%s: %s growth alarm %.3f GB; measure only' % (r['owner'], r['id'], r['gb']) for r in watches)
        if free < policy['machines'][args.machine]['free_floor_gb'] * GB:
            alerts.append('free space below floor')
        if (growth and sum(growth.values()) > policy['growth_alert_gb'] * GB) or (volume_growth is not None and volume_growth > policy['growth_alert_gb']):
            alerts.append('growth over 10 GB/day')
        if unclassified:
            alerts.append('unclassified directories over 1 GB')
        if caps:
            alerts.append('class caps exceeded; owner action required for protected or active paths')
        if errors:
            alerts.append('incomplete inventory')
        summary = dict(date=today, at=dt.datetime.now(dt.timezone.utc).isoformat(), machine=args.machine, dry_run=args.dry_run,
                       free_gb=round(free/GB,3), free_delta_gb=round((free-free_before)/GB,3), released_gb={c:round(v/GB,3) for c,v in released.items()},
                       bytes_by_class=totals, growth_gb_by_class={c:round(v/GB,3) for c,v in growth.items()} if growth else None,
                       growth_coverage=dict(compared_paths=len(common), current_paths=len(remaining_sizes),
                                            complete=bool(previous and previous.get('complete') and not errors and set(old_sizes)==set(remaining_sizes))),
                       volume_growth_gb=volume_growth,
                       top5_movers=movers, unclassified=unclassified, over_caps=caps, complete=not errors, errors=errors,
                       markers_added=markers, alerts=alerts, would_free_gb={c:round(sum(r['bytes'] for r in plan if r['class']==c and r['action'] in ('expire','retire'))/GB,3) for c in released},
                       preservation_candidates=sum(r['action']=='preserve' for r in plan))
        summary['would_archive_runtime_gb'] = round(sum(r['bytes'] for r in archives)/GB, 3)
        summary['archived_runtime_files'] = len(archived)
        summary['growth_alarms'] = watches
        summary['cleanup_plan'] = plan + ci_rows
        summary['before_bytes_by_class'] = attributed(sizes,policy)
        summary['policy_sha256'] = hashlib.sha256(json.dumps(policy,sort_keys=True).encode()).hexdigest()
        predicted = release_totals([r for r in plan + ci_rows if r['action'] in ('expire','retire','ci-retire')], sizes, policy)
        summary['would_free_gb'] = {c:round(predicted[c]/GB,3) for c in ('source','rebuildable','runtime','unique','personal')}
        summary['inventory'] = [dict(path=p, bytes=v, **{'class':classify(p, policy)['class']},
                                     owner=classify(p, policy)['owner']) for p,v in remaining_sizes.items()]
        summary['unmeasured_paths'] = [dict(path=str(p.relative_to(home)), **{'class':classify(str(p.relative_to(home)), policy)['class']})
                                       for p in paths if str(p.relative_to(home)) not in sizes]
        if args.run:
            # Detailed local paths never enter the small Git ledger.
            write(state_dir / ('run-' + today + '.json'), dict(summary=summary, plan=plan, results=results, archived=archived))
            write(state_dir / ('sizes-' + today + '.json'), remaining_sizes)
            if alerts:
                sys.path.insert(0, str(ROOT / 'agents/zero/bin'))
                from a0_data_home import resolve_data_home
                inbox = resolve_data_home() / '.agents/a0/inbox.log'
                inbox.parent.mkdir(parents=True, exist_ok=True)
                with inbox.open('a') as out:
                    out.write(summary['at'] + ' disk-keeper ' + args.machine + ': ' + '; '.join(alerts) + '; free_gb=' + str(summary['free_gb']) + '\n')
                summary['inbox'] = str(inbox)
            try:
                safe = {k:v for k,v in summary.items() if k not in ('errors','unclassified','over_caps','inbox',
                                                                  'growth_alarms','cleanup_plan','inventory','unmeasured_paths')}
                # Movers contain only home-relative directory labels, never file content.
                safe['top5_movers'] = [dict(path=hashlib.sha256(r['path'].encode()).hexdigest()[:12], growth_gb=r['growth_gb']) for r in movers]
                release = read(ROOT / 'disk-keeper-release.json', {})
                origin = release.get('origin') or git(ROOT,'remote','get-url','origin')
                summary['ledger_sha'] = ledger(safe, ledger_dir, origin, args.machine)
            except (RuntimeError, OSError, subprocess.SubprocessError):
                summary['ledger_error'] = 'Git ledger push unavailable; local receipt retained'
            write(latest, summary)
        print(json.dumps(summary) if args.json else json.dumps(summary, indent=2))
        return 1 if errors or summary.get('ledger_error') else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print('disk-keeper: ' + str(exc), file=sys.stderr)
        sys.exit(2)
