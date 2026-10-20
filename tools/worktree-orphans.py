#!/usr/bin/env python3
"""Preserve unreadable linked worktrees as encrypted evidence; retire only after GitHub, vault and R2 restore proof."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.parse import urlparse


def load(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


archive = load('archive-offload')
wip = load('worktree-wip')
WT = Path.home() / 'SISO_Workspace/_data/worktrees'
VAULT = Path('/Volumes/SISO-STORAGE-VAULT')
REBUILD = {'node_modules', '.next', 'dist', 'build', '.turbo', 'target'}


def orphans():
    for path in sorted(WT.glob('*/*')):
        if (path / '.git').is_file() and not path.is_symlink() and not wip.PRIVATE.search(str(path)) and not wip.NO_REMOVE.search(str(path)):
            state = wip.git(str(path), 'status', '--porcelain')
            if state.returncode:
                yield path


def fingerprint(path):
    seen = {}
    for base, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if d not in REBUILD]
        for p in [Path(base)] + [Path(base) / n for n in files] + [Path(base) / n for n in dirs if (Path(base) / n).is_symlink()]:
            s = p.lstat()
            seen[str(p.relative_to(path))] = (s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode)
    return seen


def r2_ready():
    remote = os.environ.get('ESTATE_R2_REMOTE', '')
    if ':' not in remote or not remote.split(':', 1)[1] or not shutil.which('rclone'):
        return False
    try:
        result = subprocess.run(['rclone', 'config', 'dump'], capture_output=True, text=True, timeout=20)
        config = json.loads(result.stdout).get(remote.split(':', 1)[0], {})
        endpoint = config.get('endpoint', '')
        parsed = urlparse(endpoint if '://' in endpoint else 'https://' + endpoint)
        return (result.returncode == 0 and config.get('type') == 's3' and parsed.scheme == 'https' and
                bool(parsed.hostname and parsed.hostname.endswith('.r2.cloudflarestorage.com')))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


def r2_copy(clone, parts, remote):
    for name, digest, _ in parts:
        dest = remote.rstrip('/') + '/' + name
        subprocess.run(['rclone', 'copyto', str(clone / name), dest], check=True, capture_output=True)
        result = subprocess.run(['rclone', 'cat', dest], check=True, capture_output=True)
        if hashlib.sha256(result.stdout).hexdigest() != digest:
            raise RuntimeError('R2 readback checksum mismatch; sources retained')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--idle-hours', type=float, default=24)
    parser.add_argument('--only', nargs='+')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--preserve-only', action='store_true', help='preserve to GitHub and vault even if R2 access is unavailable; never release')
    args = parser.parse_args()
    held = wip.open_paths()
    roots, snapshots = [], {}
    cutoff = (time.time() - args.idle_hours * 3600) * 1e9
    for path in orphans():
        if args.only and str(path.relative_to(WT)) not in args.only:
            continue
        if wip.inside(held, str(path)) or wip.inside(wip.seat_cwds(), str(path)):
            continue
        snap = fingerprint(path)
        if any(s[2] > cutoff for s in snap.values()):
            continue
        roots.append(path); snapshots[path] = snap
    print('worktree-orphans: %d unreadable idle trees; R2 %s' % (len(roots), 'configured' if r2_ready() else 'unavailable; sources held'), flush=True)
    if not roots or not args.run:
        return 0
    if not r2_ready() and not args.preserve_only:
        wip.receipt(rule='storage-worktree-orphans', result='blocked: R2 access unavailable; sources retained', trees=len(roots))
        return 1
    if not os.path.ismount(VAULT) or not Path(archive.KEY).is_file():
        raise RuntimeError('external vault or estate identity unavailable')
    identity = subprocess.run(['age-keygen', '-y', archive.KEY], capture_output=True, text=True, check=True)
    if identity.stdout.strip() != archive.RECIPIENT:
        raise RuntimeError('estate decrypt identity mismatch')
    target = VAULT / 'cold-storage/mini/orphan-evidence' / str(time.time_ns())
    target.mkdir(parents=True, mode=0o700)
    wrapper = target / 'publish'; wrapper.mkdir()
    archive.ARCHIVE = str(WT.parent)
    seen, parts = archive.build(WT.name, str(wrapper), exclude=REBUILD, roots=roots)
    full = 'sisodias/siso-evidence-mini-orphans-20261021'
    head = archive.push(str(wrapper), full, parts, 'unreadable-mini-worktrees', len(seen))
    clone = target / 'verified'
    archive.sh('git', 'clone', '-q', 'https://github.com/' + full + '.git', str(clone))
    if archive.sh('git', 'rev-parse', 'HEAD', cwd=clone).strip() != head:
        raise RuntimeError('fresh clone head differs')
    for name, digest, _ in parts:
        if hashlib.sha256((clone / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError('fresh clone part checksum differs')
    if archive.read_back(str(clone), parts) != seen:
        raise RuntimeError('decrypted tar listing/content differs')
    remote = os.environ.get('ESTATE_R2_REMOTE', '').rstrip('/') + '/mini-orphans/' + head
    if r2_ready():
        r2_copy(clone, parts, remote)
    receipt = {'rule': 'storage-worktree-orphans', 'head': head, 'repo': full, 'vault': str(target),
               'entries': len(seen), 'trees': len(roots), 'parts': parts,
               'verified': 'fresh GitHub clone decrypted/listed: every file SHA256, type, mode, link equal',
               'r2': remote if r2_ready() else 'BLOCKED: account R2 not enabled; no configured R2 transport', 'released': []}
    (target / 'MANIFEST.json').write_text(json.dumps(receipt))
    if not args.preserve_only and r2_ready():
        for path in roots:
            if wip.inside(wip.open_paths(), str(path)) or fingerprint(path) != snapshots[path]:
                continue
            quarantine = path.with_name(path.name + '.estate-retiring-' + str(os.getpid()))
            if quarantine.exists():
                continue
            path.rename(quarantine)
            try:
                if wip.inside(wip.open_paths(), str(quarantine)) or fingerprint(quarantine) != snapshots[path]:
                    if not path.exists(): quarantine.rename(path)
                    continue
                shutil.rmtree(quarantine)
                receipt['released'].append(str(path))
            except BaseException:
                if quarantine.exists() and not path.exists(): quarantine.rename(path)
                raise
    (target / 'MANIFEST.json').write_text(json.dumps(receipt))
    wip.receipt(**receipt)
    print('worktree-orphans: %d trees, %d entries restored; %d released; %s' % (len(roots), len(seen), len(receipt['released']), receipt['r2']), flush=True)
    return 0 if r2_ready() else 1


if __name__ == '__main__':
    raise SystemExit(main())
