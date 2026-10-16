#!/usr/bin/env python3
"""Fetch one registered, pinned data submodule and verify its complete manifest."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import data_manifest as manifest_tool

BLOCK = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[0-9]{2,}")
SHA = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})")
RESERVE = 100_000_000_000


def sh(*args, cwd=None, timeout=3600):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                            env=manifest_tool.git_environment(), timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def git(root, *args):
    code, out, _ = sh('git', *args, cwd=str(root))
    if code:
        raise RuntimeError('Git data-block operation failed')
    return out


def within(root, relative):
    path = Path(relative)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('path escapes code repository')
    result = root / path
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('symlink in data-block path')
    return result


def registry(root):
    index = within(root, 'data/blocks.json')
    if not index.exists():
        return {}
    document = json.loads(index.read_text())
    if document.get('version') != 1 or not isinstance(document.get('blocks'), list):
        raise ValueError('invalid data-block index')
    entries = {}
    for row in document['blocks']:
        name = row['block']
        if not BLOCK.fullmatch(name) or name in entries or not SHA.fullmatch(row['commit']):
            raise ValueError('invalid or duplicate data-block pin')
        if type(row['size']) is not int or row['size'] < 0:
            raise ValueError('invalid declared block size')
        within(root, row['manifest'])
        entries[name] = row
    return entries


def registration(root, block, row):
    path = 'data/' + block
    within(root, path)
    modules = within(root, '.gitmodules')
    # Never let an uncommitted URL override turn this into an arbitrary fetch.
    if modules.read_text().strip() != git(root, 'show', 'HEAD:.gitmodules'):
        raise ValueError('data submodule definitions must match committed HEAD')
    output = git(root, 'config', '-f', str(modules), '--get-regexp', r'^submodule\..*\.path$')
    keys = [line.split(None, 1)[0][:-5] for line in output.splitlines()
            if len(line.split(None, 1)) == 2 and line.split(None, 1)[1] == path]
    if len(keys) != 1:
        raise ValueError('block is not a unique registered data submodule')
    key = keys[0]
    url = git(root, 'config', '-f', str(modules), '--get', key + '.url')
    if not re.fullmatch(r'https://github\.com/siso-data/[A-Za-z0-9_.-]+\.git', url):
        raise ValueError('data submodule must use the isolated storage organization')
    code, local_url, _ = sh('git', 'config', '--local', '--get', key + '.url', cwd=str(root))
    if code not in (0, 1) or (code == 0 and local_url != url):
        raise ValueError('local submodule URL overrides registered storage')
    tree = git(root, 'ls-tree', 'HEAD', '--', path).split()
    if tree != ['160000', 'commit', row['commit'], path]:
        raise ValueError('registered pin differs from committed gitlink')
    index = git(root, 'ls-files', '--stage', '--', path).split()
    if index != ['160000', row['commit'], '0', path]:
        raise ValueError('staged data gitlink differs from registered pin')
    return within(root, path)


def get_block(root, block):
    entries = registry(root)
    if block not in entries:
        raise ValueError('unregistered data block')
    row = entries[block]
    checkout = registration(root, block, row)
    manifest = json.loads(within(root, row['manifest']).read_text())
    manifest_tool.validate(manifest)
    if manifest['size'] != row['size']:
        raise ValueError('index and manifest sizes differ')
    cache = Path(git(root, 'rev-parse', '--absolute-git-dir')) / 'estate-data' / block
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / 'fetch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # Snapshot, fallback parts, and assembled archive may coexist. This is a
        # data allocation, never covered by the temporary migration build waiver.
        if shutil.disk_usage(root).free - 3 * row['size'] < RESERVE:
            raise RuntimeError('data fetch held by 100 GB local free-space reserve')
        git(root, '-c', 'submodule.recurse=false', '-c', 'protocol.file.allow=never',
            'submodule', 'update', '--init', '--depth', '1', '--', 'data/' + block)
        if git(checkout, 'rev-parse', 'HEAD') != row['commit']:
            raise ValueError('checked-out data revision differs from pin')
        try:
            manifest_tool.verify_parts(manifest, checkout)
            location = 'submodule:' + row['commit']
            archive = None
        except (OSError, ValueError, RuntimeError):
            archive = cache / manifest['path']
            location = manifest_tool.verified_archive(manifest, archive, sh,
                                                      disk_machine=os.environ.get('ESTATE_DISK_LOCATION'))
        return {'block': block, 'commit': row['commit'], 'size': row['size'],
                'verified': True, 'location': location,
                'archive': str(archive) if archive else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('block', nargs='?')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args()
    if args.list == bool(args.block):
        parser.error('choose one block or --list')
    try:
        root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel')).resolve()
        if args.list:
            print(json.dumps({'blocks': [{'block': name, 'size': row['size'], 'commit': row['commit']}
                                         for name, row in registry(root).items()]}, indent=2))
        else:
            print(json.dumps(get_block(root, args.block), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
        print('Data block fetch/verification failed; no verified-success receipt issued.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
