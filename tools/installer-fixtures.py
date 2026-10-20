#!/usr/bin/env python3
"""Preserve completed synthetic stack-install test homes on the external vault, then release them.

The producer is agents/stack/tests/install.test.mjs. Only homes certified by its
installation receipt and generated marker qualify. Every byte, including the
intentional dirty.fixture, is encrypted and read back before any release.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

spec = importlib.util.spec_from_file_location('fixture_archive', Path(__file__).with_name('archive-offload.py'))
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
VAULT = Path('/Volumes/SISO-STORAGE-VAULT')


def open_paths():
    result = subprocess.run(['lsof', '-u', str(os.getuid()), '-Fn', '-w'], capture_output=True, text=True, timeout=60)
    if result.returncode or not result.stdout:
        raise RuntimeError('cannot observe open files; nothing released')
    return {os.path.realpath(line[1:]) for line in result.stdout.splitlines() if line.startswith('n/')}


def busy(path, held):
    return any(p == str(path) or p.startswith(str(path) + '/') for p in held)


def certified(path):
    if path.is_symlink() or not path.is_dir() or not path.name.startswith('siso-stack-test-'):
        return False
    try:
        receipt = json.loads((path / '.siso/agent-stack/install-receipt.json').read_text())
        return (receipt.get('schema_version') == 1 and receipt.get('stack_version') and
                Path(receipt['home']).resolve() == path.resolve() and
                'SISO AGENT STACK' in (path / '.codex/AGENTS.md').read_text())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def fingerprint(path):
    result = {}
    for base, dirs, files in os.walk(path, followlinks=False):
        for p in [Path(base)] + [Path(base) / n for n in files] + [Path(base) / n for n in dirs if (Path(base) / n).is_symlink()]:
            s = p.lstat()
            result[str(p.relative_to(path))] = (s.st_ino, s.st_size, s.st_mtime_ns, s.st_mode)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--idle-hours', type=float, default=1)
    parser.add_argument('--max', type=int, default=128, help='bound archive size and memory per run')
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    # Job environments may set TMPDIR to ~/tmp; the installer producer used Darwin's
    # per-user temp directory, so query that directory explicitly.
    temp = Path(subprocess.check_output(['getconf', 'DARWIN_USER_TEMP_DIR'], text=True).strip()).resolve()
    held, picked, snapshots = open_paths(), [], {}
    cutoff = (time.time() - args.idle_hours * 3600) * 1e9
    for path in sorted(temp.glob('siso-stack-test-*')):
        if not certified(path) or busy(path, held):
            continue
        snap = fingerprint(path)
        if any(s[2] > cutoff for s in snap.values()):
            continue
        picked.append(path)
        snapshots[path] = snap
        if len(picked) >= args.max:
            break
    print('installer-fixtures: %d certified idle homes %s' % (len(picked), 'selected' if args.run else 'would archive'), flush=True)
    if not args.run or not picked:
        return 0
    if not os.path.ismount(VAULT) or not Path(archive.KEY).is_file():
        raise RuntimeError('external vault or decrypt identity unavailable; sources retained')
    identity = subprocess.run(['age-keygen', '-y', archive.KEY], capture_output=True, text=True, check=True)
    if identity.stdout.strip() != archive.RECIPIENT:
        raise RuntimeError('estate identity mismatch')
    target = VAULT / 'cold-storage/mini/installer-fixtures' / str(time.time_ns())
    target.mkdir(parents=True, mode=0o700)
    before = shutil.disk_usage(temp).free
    archive.ARCHIVE = str(temp.parent)
    seen, parts = archive.build(temp.name, str(target), roots=picked)
    if archive.read_back(str(target), parts) != seen:
        raise RuntimeError('encrypted restore differs; sources retained')
    receipt = {'at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'rule': 'storage-installer-fixtures',
               'vault': str(target), 'parts': parts, 'entries': len(seen), 'source_paths': list(map(str, picked)),
               'verified': 'decrypted tar: every file SHA256, type, mode and link equal', 'released': []}
    (target / 'MANIFEST.json').write_text(json.dumps(receipt))
    for path in picked:
        if busy(path, open_paths()) or fingerprint(path) != snapshots[path]:
            continue
        quarantine = path.with_name(path.name + '.estate-retiring-' + str(os.getpid()))
        if quarantine.exists():
            continue
        path.rename(quarantine)
        try:
            if busy(quarantine, open_paths()) or fingerprint(quarantine) != snapshots[path]:
                if not path.exists():
                    quarantine.rename(path)
                continue
            shutil.rmtree(quarantine)
            receipt['released'].append(str(path))
        except BaseException:
            if quarantine.exists() and not path.exists():
                quarantine.rename(path)
            raise
    receipt['free_gain_bytes'] = shutil.disk_usage(temp).free - before
    (target / 'MANIFEST.json').write_text(json.dumps(receipt))
    Path(archive.RECEIPTS).parent.mkdir(parents=True, exist_ok=True)
    with open(archive.RECEIPTS, 'a') as f:
        f.write(json.dumps(receipt) + '\n')
    print('installer-fixtures: %d homes restored and released; free gain %+.2f GiB' % (len(receipt['released']), receipt['free_gain_bytes'] / 2**30), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
