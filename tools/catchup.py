#!/usr/bin/env python3
"""Bring a shared checkout up to its GitHub branch without stashing, resetting or losing anyone's uncommitted work.

  catchup.py                 plan: what each changed file would get
  catchup.py --run           do it (backups first), then move the branch and the index
  catchup.py --theirs PATH   take GitHub's version of PATH even though it was edited here (repeatable; backed up)
  catchup.py --repo DIR      another checkout (default: this repo)

Why (19 Oct 2026): the nightly pulled with `--rebase --autostash` (a stash of other agents' work) and fell back to a
rebase pull that refuses over a dirty tree, so from 16 Oct every night committed here and never pushed: laptop main
ended 16 commits ahead and 94 behind GitHub. This replaces both pulls.

The target is origin/<branch> when this branch is behind it, or a merge of the two (`git merge-tree`, committed only
when it has no conflict) when they diverged; the caller pushes. Each file that differs between HEAD and the target:
  - not edited here: the target's version;
  - edited here: a 3-way merge of (here, HEAD, target); when the lines clash, append-only records merge by union
    (*.jsonl, *.log, *.md: both sides appended), JSON merges by key and by "name" in lists (here wins only where GitHub
    left a value alone), anything else holds the run;
  - untracked here and added by the target: nothing when identical, else held (or --theirs).
Then the index is set to the target and the branch moved; working-tree edits stay as uncommitted changes on top.
Exit 0 caught up (or already current), 1 held (nothing changed), 2 could not start.
"""
import datetime, json, os, re, shutil, subprocess, sys

UNION = ('.jsonl', '.log', '.md')


def git(repo, *a, **k):
    return subprocess.run(['git', '-C', repo, *a], capture_output=True, **k)


def blob(repo, ref, p):
    r = git(repo, 'show', f'{ref}:{p}')
    return r.stdout if r.returncode == 0 else None


def merge_json(b, o, t):
    """3-way merge: t (GitHub) is the frame, o's changes against b are applied where t left the value alone."""
    if o == t or o == b:
        return t
    if t == b:
        return o
    if isinstance(o, dict) and isinstance(t, dict):
        b = b if isinstance(b, dict) else {}
        out = dict(t)
        for k in o:
            out[k] = merge_json(b.get(k), o[k], t.get(k))
        for k in list(out):
            if k in b and k not in o and t.get(k) == b[k]:
                del out[k]
        return out
    if isinstance(o, list) and isinstance(t, list) and all(isinstance(x, dict) and 'name' in x for x in o + t):
        bn = {x['name']: x for x in (b or []) if isinstance(x, dict) and 'name' in x}
        on = {x['name']: x for x in o}
        out = [merge_json(bn.get(x['name']), on[x['name']], x) if x['name'] in on else x for x in t]
        return out + [x for x in o if x['name'] not in {y['name'] for y in t}]
    return t


def dump_like(value, raw):
    m = re.search(rb'\n( +)"', raw)
    text = json.dumps(value, indent=len(m.group(1)) if m else None, ensure_ascii=not re.search(rb'[\x80-\xff]', raw),
                      separators=None if m else (',', ':'))
    return (text + ('\n' if raw.endswith(b'\n') else '')).encode()


def three_way(path, here, base, there, tmp):
    for name, data in (('here', here), ('base', base), ('there', there)):
        open(os.path.join(tmp, name), 'wb').write(data)
    args = [os.path.join(tmp, n) for n in ('here', 'base', 'there')]
    r = subprocess.run(['git', 'merge-file', '-p', *args], capture_output=True)
    if r.returncode == 0:
        return r.stdout, 'merged'
    if path.endswith(UNION):
        out = subprocess.run(['git', 'merge-file', '--union', '-p', *args], capture_output=True).stdout
        if os.path.basename(path) == 'MEMORY.md':   # an index: one line per memory file
            seen, lines = set(), []
            for line in out.decode().splitlines(keepends=True):
                m = re.match(r'- \[[^\]]*\]\(([^)]+)\)', line)
                if m and m.group(1) in seen:
                    continue
                seen.add(m.group(1)) if m else None
                lines.append(line)
            out = ''.join(lines).encode()
        return out, 'union'
    if path.endswith('.json'):
        try:
            return dump_like(merge_json(json.loads(base), json.loads(here), json.loads(there)), there), 'json'
        except ValueError:
            pass
    return None, 'conflict'


def main(argv):
    repo, run, theirs = os.path.dirname(os.path.dirname(os.path.abspath(__file__))), False, set()
    args = iter(argv)
    for a in args:
        if a == '--run':
            run = True
        elif a == '--theirs':
            theirs.add(next(args))
        elif a == '--repo':
            repo = os.path.abspath(next(args))
        else:
            print(__doc__.strip()); return 0 if a in ('-h', '--help') else 2
    branch = git(repo, 'symbolic-ref', '--short', 'HEAD', text=True).stdout.strip()
    if not branch:
        print('catchup: detached HEAD; nothing to move', file=sys.stderr); return 2
    if not git(repo, 'diff', '--cached', '--quiet').returncode == 0:
        print('catchup: something is staged; commit or unstage it first (catchup never touches a staged index)', file=sys.stderr)
        return 2
    if git(repo, 'fetch', '-q', 'origin', branch).returncode:
        print('catchup: fetch failed', file=sys.stderr); return 2
    head = git(repo, 'rev-parse', 'HEAD', text=True).stdout.strip()
    up = git(repo, 'rev-parse', f'origin/{branch}', text=True).stdout.strip()
    if git(repo, 'merge-base', '--is-ancestor', up, head).returncode == 0:
        print(f'catchup: {branch} already has origin/{branch}'); return 0
    if git(repo, 'merge-base', '--is-ancestor', head, up).returncode == 0:
        target, how = up, 'fast-forward'
    else:
        mt = git(repo, 'merge-tree', '--write-tree', head, up, text=True)
        if mt.returncode:
            print(f'catchup: {branch} and origin/{branch} diverged and their commits conflict:')
            print('\n'.join('  ' + l for l in mt.stdout.splitlines()[1:] if l.startswith('CONFLICT')))
            return 1
        ahead = git(repo, 'rev-list', '--count', f'{up}..{head}', text=True).stdout.strip()
        behind = git(repo, 'rev-list', '--count', f'{head}..{up}', text=True).stdout.strip()
        msg = f'Merge origin/{branch} into this checkout ({ahead} local, {behind} remote; tools/catchup.py)'
        target = git(repo, 'commit-tree', mt.stdout.split()[0], '-p', head, '-p', up, '-m', msg, text=True).stdout.strip()
        how = f'merge {target[:10]} ({ahead} local, {behind} remote; push it)'
    changed = [l.split('\t', 1) for l in git(repo, 'diff', '--name-status', '--no-renames', head, target, text=True).stdout.splitlines()]
    edited = set(git(repo, 'diff', '--name-only', 'HEAD', text=True).stdout.splitlines())
    tmp = os.path.join(os.environ.get('TMPDIR', '/tmp'), f'estate-catchup-{os.getpid()}')
    os.makedirs(tmp, exist_ok=True)
    writes, removes, held, counts = {}, [], [], {}
    for _, p in changed:
        full = os.path.join(repo, p)
        h, t = blob(repo, head, p), blob(repo, target, p)
        w = open(full, 'rb').read() if os.path.isfile(full) else None
        if p in theirs:
            kind = 'theirs'; (writes.__setitem__(p, t) if t is not None else removes.append(p))
        elif w == t:
            kind = 'same'
        elif (h is not None and p not in edited) or (h is None and w is None):
            kind = 'take'; (writes.__setitem__(p, t) if t is not None else removes.append(p))
        elif h is None:
            kind = 'held'; held.append(f'{p}: untracked here, GitHub added a different one (--theirs {p} to take it)')
        elif w is None or t is None:
            kind = 'held'; held.append(f'{p}: deleted on one side, edited on the other')
        else:
            out, kind = three_way(p, w, h, t, tmp)
            if out is None:
                held.append(f'{p}: edits clash (--theirs {p} to take GitHub\'s; the local copy is backed up)')
            else:
                writes[p] = out
        counts[kind] = counts.get(kind, 0) + 1
    shutil.rmtree(tmp, ignore_errors=True)
    print(f'catchup {branch}: {how}; ' + ', '.join(f'{k} {v}' for k, v in sorted(counts.items())))
    for line in held:
        print('  held ' + line)
    if held or not run:
        return 1 if held else 0
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backups = os.path.expanduser(f'~/.local/state/siso-estate/catchup/{stamp}')
    def keep(p):
        src = os.path.join(repo, p)
        if os.path.exists(src):
            dst = os.path.join(backups, p)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    for p, data in writes.items():
        keep(p)
        full = os.path.join(repo, p)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        mode = int(git(repo, 'ls-tree', target, '--', p, text=True).stdout.split()[0], 8) & 0o777
        part = os.path.join(os.path.dirname(full), f'.{os.path.basename(full)}.catchup')
        open(part, 'wb').write(data)
        os.chmod(part, mode)
        os.replace(part, full)
    for p in removes:
        keep(p)
        if os.path.exists(os.path.join(repo, p)):
            os.remove(os.path.join(repo, p))
    if git(repo, 'read-tree', target).returncode or git(repo, 'update-ref', f'refs/heads/{branch}', target, head).returncode:
        print(f'catchup: files written but the branch did not move; backups in {backups}', file=sys.stderr); return 2
    print(f'catchup: {branch} at {target[:10]}; {len(writes)} written, {len(removes)} removed; backups in {backups}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
