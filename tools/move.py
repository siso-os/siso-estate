#!/usr/bin/env python3
"""Move one folder to its planned home without breaking anything that used the old path.

  move.py SRC DST --why "reason" [--no-link] [--dry-run]

Steps, in order; any failed precondition stops the move before anything changes:
  1. SRC exists, DST does not, both on the same disk; no process has its working directory inside SRC.
  2. Relative symlinks inside SRC that point outside it are recorded (they would break when moved).
  3. A manifest line goes to machines/<machine>/moves.jsonl: src, dst, git HEAD and dirty count, why.
  4. mv SRC DST (one rename, atomic on one disk).
  5. Those relative symlinks are rewritten as absolute links to the same target.
  6. Git worktrees whose main repo or checkout moved are repaired (`git worktree repair`).
  7. A compat symlink SRC -> DST keeps every old consumer working (unless --no-link).
  8. Claude session history folders for SRC get a sibling link under the new path's name.
Undo: estate undo (reads moves.jsonl), or by hand: rm SRC && mv DST SRC.
"""
import argparse, json, os, re, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")


def sh(*cmd, cwd=None, timeout=300):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def cwd_holders(src):
    """PIDs whose current directory is inside src."""
    code, out, _ = sh("lsof", "-a", "-d", "cwd", "-Fpcn", timeout=60)
    hits, pid, cmd = [], None, None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = line[1:]
        elif line.startswith("c"):
            cmd = line[1:]
        elif line.startswith("n"):
            path = line[1:]
            if path == src or path.startswith(src + "/"):
                hits.append(f"{pid} {cmd} {path}")
    return hits


def outward_relative_links(src):
    links = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules", ".git")]
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            if os.path.islink(p):
                t = os.readlink(p)
                if not os.path.isabs(t):
                    absolute = os.path.normpath(os.path.join(dirpath, t))
                    if not (absolute == src or absolute.startswith(src + "/")):
                        links.append((os.path.relpath(p, src), absolute))
    return links


def claude_key(path):
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def git_state(path):
    if not os.path.exists(os.path.join(path, ".git")):
        return None
    _, head, _ = sh("git", "-C", path, "rev-parse", "--short", "HEAD")
    _, st, _ = sh("git", "-C", path, "status", "--porcelain", "-uno")
    return {"head": head, "dirty": len(st.splitlines()) if st else 0}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--why", required=True)
    ap.add_argument("--no-link", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--force-cwd", action="store_true", help="move even if a process sits inside (it keeps its inode)")
    a = ap.parse_args()
    src = os.path.abspath(os.path.expanduser(a.src))
    dst = os.path.abspath(os.path.expanduser(a.dst))
    if os.path.islink(src):
        sys.exit(f"SRC is already a link: {src} -> {os.readlink(src)}")
    if not os.path.exists(src):
        sys.exit(f"SRC missing: {src}")
    if os.path.lexists(dst):
        sys.exit(f"DST exists: {dst}")
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.stat(src).st_dev != os.stat(os.path.dirname(dst)).st_dev:
        sys.exit("SRC and DST are on different disks; refusing (a copy is not a move)")
    holders = cwd_holders(src)
    if holders and not a.force_cwd:
        sys.exit("processes are working inside SRC:\n  " + "\n  ".join(holders[:10]))
    rel_links = outward_relative_links(src) if os.path.isdir(src) else []
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "src": src, "dst": dst, "why": a.why,
           "git": git_state(src), "outward_links_fixed": len(rel_links), "compat_link": not a.no_link,
           "cwd_holders": holders[:10]}
    if a.dry_run:
        print(json.dumps(rec, indent=1))
        return
    # worktrees that belong to repos inside SRC, or checkouts inside SRC that belong to repos elsewhere
    wt_mains = []
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = [d for d in dirnames if d not in ("node_modules",)]
        if ".git" in dirnames and os.path.isdir(os.path.join(dirpath, ".git", "worktrees")):
            wt_mains.append(os.path.relpath(dirpath, src))
        if ".git" in filenames:
            wt_mains.append(os.path.relpath(dirpath, src))
        if ".git" in dirnames:
            dirnames.remove(".git")
    os.rename(src, dst)
    for relp, target in rel_links:
        p = os.path.join(dst, relp)
        os.unlink(p)
        os.symlink(target, p)
    repaired = 0
    for rel in wt_mains:
        p = os.path.normpath(os.path.join(dst, rel))
        code, _, _ = sh("git", "-C", p, "worktree", "repair")
        repaired += code == 0
    if not a.no_link:
        os.symlink(dst, src)
    proj = os.path.join(HOME, ".claude", "projects")
    old_key, new_key, linked = claude_key(src), claude_key(dst), []
    if os.path.isdir(proj):
        for name in os.listdir(proj):
            if name == old_key or name.startswith(old_key + "-"):
                new_name = new_key + name[len(old_key):]
                np = os.path.join(proj, new_name)
                if not os.path.lexists(np):
                    os.symlink(os.path.join(proj, name), np)
                    linked.append(new_name)
    rec.update({"worktree_repairs": repaired, "claude_history_links": len(linked)})
    with open(os.path.join(REPO, "machines", a.machine, "moves.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))


if __name__ == "__main__":
    main()
