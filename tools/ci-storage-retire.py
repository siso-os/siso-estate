#!/usr/bin/env python3
"""Retire verified idle CI clones; seal recovery evidence on the vault under their worker lease."""
import argparse
import fcntl
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time


def stash_free(path):
    result = subprocess.run(['git', '-C', str(path), 'stash', 'list', '--format=%H'], capture_output=True, text=True)
    return result.returncode == 0 and not result.stdout.strip()


def vault_clone(clone, lock, retention, machine, idle_hours=24):
    with lock.open('a') as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        commands = retention.running_commands()
        if commands is None or str(clone) in commands:
            return False
        result = subprocess.run([os.sys.executable, str(Path(__file__).with_name('to-vault.py')), str(clone),
                                 '--cold-clones', '--idle-hours', str(idle_hours), '--run'], capture_output=True, text=True,
                                env=dict(os.environ, ESTATE_MACHINE=machine))
        released = result.returncode == 0 and not clone.exists()
        print('ci-retire: vault %s; preservation exit %d' % ('released' if released else 'held', result.returncode), flush=True)
        for line in (result.stdout or '').splitlines()[-2:]:
            print('  ' + line, flush=True)
        return released


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--idle-hours', type=float, default=24)
    ap.add_argument('--vault-max', type=int, default=2, help='bound long verified vault copies per hourly run')
    ap.add_argument('--run', action='store_true')
    args = ap.parse_args()
    if args.idle_hours < 0 or args.vault_max < 0:
        ap.error('idle hours and vault maximum must be nonnegative')
    root = Path(__file__).resolve().parents[3]
    source = root / 'agents/harness/ci/retention.py'
    if not source.is_file():
        print('ci-retire: held; shared CI retention source unavailable')
        return 0
    spec = importlib.util.spec_from_file_location('ci_retention', source)
    retention = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(retention)
    work = Path.home() / 'SISO_Workspace/_data/siso-ci-work'
    if not work.is_dir():
        print('ci-retire: no worker data')
        return 0
    machine = os.environ.get('ESTATE_MACHINE') or ('mini' if 'mini' in socket.gethostname().lower() else 'laptop')
    before = shutil.disk_usage(work).free
    target = 60 if machine == 'mini' else 100
    removed = retention.prune(work, floor_gib=target, margin_gib=0, days=args.idle_hours / 24,
                              dry_run=not args.run, pnpm=True,
                              safe_fn=lambda path: stash_free(path) and retention.preserved_clone(path))
    copied = 0
    if args.run:
        for clone, lock in retention.units(work):
            if copied >= args.vault_max or shutil.disk_usage(work).free >= target * 2**30:
                break
            if not any('/clones/' + owner + '/' in str(clone) for owner in ('sisodias', 'HALO-AGENCY')):
                continue
            if not (clone / '.git').is_dir() or time.time() - retention.last_used(clone) < args.idle_hours * 3600:
                continue
            if vault_clone(clone, lock, retention, machine, args.idle_hours):
                copied += 1
                removed.append(str(clone))
    after = shutil.disk_usage(work).free
    print('ci-retire: %d verified clones; free gain %+.2f GiB' % (len(removed), (after - before) / 2**30), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
