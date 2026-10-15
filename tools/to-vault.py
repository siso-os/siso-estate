#!/usr/bin/env python3
"""Move a cold folder off the mini's internal disk onto its encrypted vault drive, verified, with a pointer left behind.

  to-vault.py PATH [PATH ...]          (folders or single files) the plan: size, last write, open files, git links that would break
  to-vault.py PATH [PATH ...] --run    rsync -a to /Volumes/SISO-STORAGE-VAULT/cold-storage/mini/<date>-<name>/, verify
                                       with a checksum pass (rsync -ac --dry-run lists nothing), leave <name>.IN-VAULT.md
                                       beside the old place, a receipt line, then remove the source

Refuses: a folder a process holds, one written in the last 72 h, a git repo whose worktrees live outside it (or a
worktree whose repo lives outside it), and anything when the drive is not mounted. Run it over ssh, never from launchd
(macOS privacy blocks launchd from the external drive). Restore: rsync -a <vault path>/ <old path>/.
Shaan via Agent Zero, 14 Oct: the mini hosts the research seats; 60 GB free on its data disk. Python 3.9.
"""
import argparse, glob, json, os, shutil, subprocess, sys, time

VAULT = "/Volumes/SISO-STORAGE-VAULT"
DEST = os.path.join(VAULT, "cold-storage", "mini")
RECEIPTS = os.environ.get("ESTATE_REMOVED") or os.path.expanduser("~/.local/state/estate-removed.jsonl")
HOT_HOURS = 72


def run(*a, **k):
    return subprocess.run(a, capture_output=True, text=True, **k)


def mounted():
    return (" on %s (" % VAULT) in run("mount").stdout


def held(p):
    out = run("lsof", "-u", str(os.getuid()), "-Fn", "-w").stdout
    return sorted({l[1:] for l in out.splitlines() if l.startswith("n" + p + "/") or l == "n" + p})


def newest(p):
    # a fetch, a remote ref or an estate pin is not work; a commit, checkout or edit is (HEAD, logs/HEAD, index, files)
    skip = []
    for pat in ("*/.git/objects", "*/.git/refs/remotes", "*/.git/refs/estate-kept", "*/.git/logs/refs/remotes"):
        skip += ["-path", pat, "-prune", "-o"]
    hot = run("find", p, *skip, "-type", "f", "-mmin", "-%d" % (HOT_HOURS * 60), "-not", "-name", "FETCH_HEAD",
              "-print", "-quit").stdout.strip()
    return hot


def git_links(p):
    """Worktree links that cross the folder's edge, either way: moving would break them."""
    bad = []
    for g in run("find", p, "-name", ".git", "-not", "-path", "*/node_modules/*").stdout.split():
        if os.path.isfile(g):  # a worktree: its repo must be inside too
            gd = open(g).read().strip().split(": ", 1)[-1]
            gd = os.path.join(os.path.dirname(g), gd)  # relative gitdirs are relative to the .git file
            if not os.path.realpath(gd).startswith(os.path.realpath(p) + "/"):
                bad.append("%s -> %s" % (g, gd))
        elif os.path.isdir(os.path.join(g, "worktrees")):  # a repo: its worktrees must be inside too
            for w in glob.glob(os.path.join(g, "worktrees", "*", "gitdir")):
                wt = os.path.join(os.path.dirname(w), open(w).read().strip())
                if not os.path.realpath(wt).startswith(os.path.realpath(p) + "/"):
                    bad.append("%s has worktree %s" % (g, wt))
    return bad


def kib(p):
    out = run("du", "-sk", p).stdout
    return int(out.split()[0]) if out else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if not mounted():
        print("vault drive not mounted: nothing moved")
        return 1
    free0 = shutil.disk_usage(os.path.expanduser("~")).free
    for p in [os.path.abspath(os.path.expanduser(x)).rstrip("/") for x in a.paths]:
        if os.path.islink(p) or not (os.path.isdir(p) or os.path.isfile(p)):
            print("skip   %s: not a folder or file" % p)
            continue
        isfile = os.path.isfile(p)
        why = []
        h = held(p)
        if h:
            why.append("a process holds %d files (e.g. %s)" % (len(h), h[0]))
        n = newest(p)
        if n:
            why.append("written in the last %d h (%s)" % (HOT_HOURS, n))
        why += [] if isfile else ["git link: " + b for b in git_links(p)]
        size = kib(p)
        if why:
            print("keep   %s  %.1f GiB: %s" % (p, size / 1048576, "; ".join(why)))
            continue
        dest = os.path.join(DEST, "%s-%s-%s" % (time.strftime("%Y-%m-%d"), os.path.basename(os.path.dirname(p)), os.path.basename(p)))
        print("%s %s  %.1f GiB -> %s" % ("move  " if a.run else "would ", p, size / 1048576, dest))
        if not a.run:
            continue
        os.makedirs(dest, exist_ok=True)
        src = p if isfile else p + "/"
        r = run("nice", "-n", "19", "rsync", "-a", src, dest + "/")
        if r.returncode:
            print("  KEPT: copy failed: " + r.stderr.strip()[:200])
            continue
        diff = run("nice", "-n", "19", "rsync", "-ac", "--dry-run", "--out-format=%n", src, dest + "/").stdout.split("\n")
        diff = [d for d in diff if d and not d.endswith("/")]
        if diff:
            print("  KEPT: %d files differ after copy (e.g. %s)" % (len(diff), diff[0]))
            continue
        with open(os.path.join(os.path.dirname(p), os.path.basename(p) + ".IN-VAULT.md"), "w") as f:
            f.write("# %s is in the vault\n\nMoved %s by ESTATE (tools/to-vault.py), checksum-verified, %.1f GiB.\n"
                    "Vault: `%s` (the mini's encrypted SISO-STORAGE-VAULT drive).\nRestore: `rsync -a '%s/' '%s'`\n"
                    % (os.path.basename(p), time.strftime("%Y-%m-%d %H:%M"), size / 1048576, dest, dest, os.path.dirname(p) + "/" if isfile else p + "/"))
        os.remove(p) if isfile else shutil.rmtree(p)
        with open(RECEIPTS, "a") as f:
            f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "what": "moved to the vault drive", "path": p,
                                "bytes": size * 1024, "to": dest, "why": "mini data disk for the research seats",
                                "proof": "rsync -ac --dry-run: 0 files differ", "by": "ESTATE tools/to-vault.py"}) + "\n")
    print("free %+.1f GiB" % ((shutil.disk_usage(os.path.expanduser("~")).free - free0) / 2**30))
    return 0


if __name__ == "__main__":
    sys.exit(main())
