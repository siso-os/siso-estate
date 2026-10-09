#!/usr/bin/env python3
"""Move old harness history into the vault, as the agent stack's retention rule says
(SISO_Agents/siso-harness-lab/docs/HISTORY-RETENTION.md).

  archive-history.py plan  [--days 15] [--jobs-days 7]     what would move, and how many bytes
  archive-history.py run   [--days 15] [--jobs-days 7] [--only claude|codex|jobs]

What moves (older than the line, by the newest mtime of everything that belongs together):
  claude  ~/.claude/projects/<project>/<session>.jsonl plus its sidecar folder <session>/ (subagents, tool-results)
  codex   ~/.codex/sessions/YYYY/MM/DD/*.jsonl
  jobs    ~/.claude/jobs/<id>/ not updated for --jobs-days and not held open by a process
Never moved: memory/ folders, symlinks, anything a running process has open.

Each file: gzip -9 into VAULT/<class>/<same relative path>.gz, gzip -t, sha256 of the decompressed stream must equal
the original's, a manifest line (VAULT/MANIFEST.jsonl), and only then is the original removed. An ARCHIVE.md in each
touched source folder names the vault. Restore one file: gunzip -c VAULT/<class>/<path>.gz > <original path>.
"""
import argparse, gzip, hashlib, json, os, shutil, subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor

HOME = os.path.expanduser("~")
VAULT = os.path.join(HOME, "SISO_Workspace", "_archive", "2026-09-23-harness-history")
ROOTS = {
    "claude": os.path.join(HOME, ".claude", "projects"),
    "codex": os.path.join(HOME, ".codex", "sessions"),
    "jobs": os.path.join(HOME, ".claude", "jobs"),
}


def open_files():
    r = subprocess.run(["lsof", "-Fn"], capture_output=True, text=True, timeout=180)
    return {l[1:] for l in r.stdout.splitlines() if l.startswith("n/")}


def walk_files(top):
    out = []
    for root, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d)) and d != "memory"]
        for f in files:
            p = os.path.join(root, f)
            if not os.path.islink(p):
                out.append(p)
    return out


def newest(paths):
    return max((os.lstat(p).st_mtime for p in paths), default=0)


def units(kind, line_days, jobs_days):
    """Yield (unit_name, [files]) whose newest file is older than the line."""
    now = time.time()
    root = ROOTS[kind]
    if not os.path.isdir(root):
        return
    if kind == "claude":
        cut = now - line_days * 86400
        for proj in sorted(os.listdir(root)):
            pdir = os.path.join(root, proj)
            if os.path.islink(pdir) or not os.path.isdir(pdir):
                continue
            for name in sorted(os.listdir(pdir)):
                if not name.endswith(".jsonl"):
                    continue
                t = os.path.join(pdir, name)
                if os.path.islink(t):
                    continue
                files = [t]
                side = os.path.join(pdir, name[:-6])
                if os.path.isdir(side) and not os.path.islink(side):
                    files += walk_files(side)
                if newest(files) < cut:
                    yield f"{proj}/{name}", files
    elif kind == "codex":
        cut = now - line_days * 86400
        for p in sorted(walk_files(root)):
            if p.endswith(".jsonl") and os.lstat(p).st_mtime < cut:
                yield os.path.relpath(p, root), [p]
    elif kind == "jobs":
        cut = now - jobs_days * 86400
        for jid in sorted(os.listdir(root)):
            jdir = os.path.join(root, jid)
            if os.path.islink(jdir) or not os.path.isdir(jdir):
                continue
            files = walk_files(jdir)
            if files and newest(files + [jdir]) < cut:
                yield jid, files


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sha_gz(p):
    h = hashlib.sha256()
    with gzip.open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def archive_one(args):
    kind, src = args
    rel = os.path.relpath(src, ROOTS[kind])
    dst = os.path.join(VAULT, kind, rel + ".gz")
    try:
        st = os.lstat(src)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        want = sha_file(src)
        tmp = dst + ".part"
        with open(src, "rb") as fi, gzip.open(tmp, "wb", compresslevel=9) as fo:
            shutil.copyfileobj(fi, fo, 1 << 20)
        if subprocess.run(["gzip", "-t", tmp]).returncode != 0:
            raise RuntimeError("gzip -t failed")
        if sha_gz(tmp) != want:
            raise RuntimeError("sha256 mismatch after gzip")
        if os.lstat(src).st_mtime != st.st_mtime or os.lstat(src).st_size != st.st_size:
            raise RuntimeError("changed while archiving")
        os.replace(tmp, dst)
        os.utime(dst, (st.st_atime, st.st_mtime))
        rec = {"src": src, "vault": dst, "bytes": st.st_size, "gz_bytes": os.path.getsize(dst),
               "sha256": want, "mtime": int(st.st_mtime)}
        os.unlink(src)
        return rec, None
    except Exception as e:
        for p in (dst + ".part",):
            if os.path.exists(p):
                os.unlink(p)
        return None, f"{src}: {e}"


def prune_empty(top, root):
    """Remove folders emptied by the move, bottom-up, never the root itself."""
    for d, dirs, files in os.walk(top, topdown=False):
        if d != root and not os.path.islink(d) and not os.listdir(d):
            os.rmdir(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run"])
    ap.add_argument("--days", type=int, default=15)
    ap.add_argument("--jobs-days", type=int, default=7)
    ap.add_argument("--only", choices=list(ROOTS))
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    kinds = [a.only] if a.only else ["claude", "codex"]  # jobs only when asked: ~/.claude is a git repo
    held = open_files()
    plan, skipped = [], []
    for kind in kinds:
        for name, files in units(kind, a.days, a.jobs_days):
            if any(f in held for f in files):
                skipped.append(f"{kind}:{name} (open)")
                continue
            plan.append((kind, name, files))
    total = sum(os.lstat(f).st_size for _, _, fs in plan for f in fs)
    by = {}
    for kind, _, fs in plan:
        by.setdefault(kind, [0, 0])
        by[kind][0] += len(fs)
        by[kind][1] += sum(os.lstat(f).st_size for f in fs)
    for k, (n, b) in by.items():
        print(f"{k}: {n} files, {b / 1e9:.2f} GB")
    print(f"total {total / 1e9:.2f} GB; skipped {len(skipped)} held open")
    if a.cmd == "plan":
        return
    os.makedirs(VAULT, exist_ok=True)
    jobs = [(kind, f) for kind, _, fs in plan for f in fs]
    done = errs = 0
    gz = 0
    with open(os.path.join(VAULT, "MANIFEST.jsonl"), "a") as man, ProcessPoolExecutor(a.workers) as ex:
        for rec, err in ex.map(archive_one, jobs, chunksize=4):
            if rec:
                man.write(json.dumps(rec) + "\n")
                done += 1
                gz += rec["gz_bytes"]
            else:
                errs += 1
                print("ERROR", err, file=sys.stderr)
    for kind in kinds:
        root = ROOTS[kind]
        if kind == "claude":
            for d in {os.path.dirname(f) for k, _, fs in plan if k == "claude" for f in fs[:1]}:
                write_note(os.path.join(d, "ARCHIVE.md"), os.path.join(VAULT, "claude", os.path.relpath(d, root)))
        else:
            write_note(os.path.join(root, "ARCHIVE.md"), os.path.join(VAULT, kind))
        prune_empty(root, root)
    print(f"archived {done} files ({total / 1e9:.2f} GB -> {gz / 1e9:.2f} GB gz), {errs} errors")
    sys.exit(1 if errs else 0)


def write_note(path, vault):
    text = (f"# Archived history\n\nSessions older than the retention line were moved, gzipped, to\n`{vault}`\n"
            f"(manifest: `{os.path.join(VAULT, 'MANIFEST.jsonl')}`; rule: "
            f"SISO_Agents/siso-harness-lab/docs/HISTORY-RETENTION.md).\n\n"
            f"Restore one: `gunzip -c <vault>/<name>.gz > <this folder>/<name>`, then resume as normal.\n")
    if os.path.isdir(os.path.dirname(path)) and not os.path.exists(path):
        open(path, "w").write(text)


if __name__ == "__main__":
    main()
