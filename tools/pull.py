#!/usr/bin/env python3
"""estate pull: the one door back. Finds where a path's master copy lives and brings it back (dry run unless --run).

  pull.py TARGET [--to DIR] [--run]

TARGET is a path or a name. It is looked up, in order, in: the places of plan/storage-map.json (their home, declared or
derived: tools/ledger.py), the repos retired to GitHub (machines/<m>/retired.jsonl), then the archives on the Mac mini
vault (/Volumes/SISO-STORAGE-VAULT/cold-storage/laptop, matched by name).
  github:<owner/repo>  git clone into the mapped path, through `estate restore --only` (with --to: a plain clone into DIR)
  vault:<archive>      scp the archive from the mini, check its sha256 against the mini's, unpack with zstd + tar
  plane:<name>         prints the data-backup restore command (needs the age identity; never run here)
  rebuild:<command>    prints the command
Vault archives unpack into DIR (default ~/SISO_Workspace/_data/vault-restore/<archive>), never over live files.
"""
import argparse, glob, hashlib, json, os, shlex, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ledger

REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
MINI, VAULT = "mac-mini-herdr", "/Volumes/SISO-STORAGE-VAULT/cold-storage/laptop"
MACHINE = os.environ.get("ESTATE_MACHINE", "laptop")


def jl(path):
    return [json.loads(l) for l in open(path) if l.strip()] if os.path.exists(path) else []


def find_place(target, places):
    """Places matching TARGET: exact path, else path ending in /TARGET, else substring. Longest path first."""
    t = os.path.expanduser(target).rstrip("/")
    ex = [p for p in places if os.path.expanduser(p["path"]).rstrip("/") == t]
    if ex:
        return ex
    end = [p for p in places if os.path.expanduser(p["path"]).rstrip("/").endswith("/" + t.lstrip("/"))]
    return end or [p for p in places if target in p["path"]]


def find_retired(target, machine=MACHINE):
    """{path (relative to ~/SISO_Workspace), url} for repos retired to GitHub whose path contains TARGET."""
    out = {}
    t = os.path.relpath(os.path.expanduser(target), WS) if os.path.expanduser(target).startswith(WS) else target
    for e in jl(os.path.join(REPO, "machines", machine, "retired.jsonl")):
        if e.get("act") != "retire":
            continue
        for r in e.get("repos", []):
            if r.get("url") and t in r.get("path", "") and "github.com" in r["url"]:
                out[r["path"]] = r["url"]
    return out


def mini(cmd, timeout=60):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", MINI, cmd], capture_output=True, text=True, timeout=timeout)


def vault_archives(words):
    """Archive names on the mini vault containing every word."""
    r = mini(f"ls -1 {VAULT}")
    if r.returncode:
        raise SystemExit(f"cannot list the mini vault: {r.stderr.strip()[-200:]}")
    ws = [w.lower() for w in words]
    return [n for n in r.stdout.split() if n.endswith(".tar.zst") and all(w in n.lower() for w in ws)]


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def pull_github(slug_or_url, rel, to, go):
    url = slug_or_url if slug_or_url.startswith("http") else f"https://github.com/{slug_or_url}.git"
    if to:
        cmd = ["git", "clone", "-q", url, to]
        print("  $", " ".join(cmd))
        if go:
            if os.path.exists(to) and os.listdir(to):
                return fail(f"{to} is not empty; left alone")
            r = subprocess.run(cmd, capture_output=True, text=True)
            print("  " + ("cloned into " + to if r.returncode == 0 else "FAIL " + r.stderr.strip()[-300:]))
            return r.returncode
        return 0
    cmd = [sys.executable, os.path.join(HERE, "restore.py"), "--only", rel] + (["--run"] if go else [])
    print("  $ estate restore --only", rel, "--run" if go else "")
    return subprocess.run(cmd).returncode


def pull_vault(words, to, go):
    names = vault_archives(words)
    if not names:
        return fail(f"no archive on the mini vault matches {' '.join(words)}")
    if len(names) > 1:
        print("  several archives match; name one more precisely:")
        print("\n".join("   " + n for n in names))
        return 2
    name = names[0]
    dest = to or os.path.join(WS, "_data", "vault-restore", name[:-len(".tar.zst")])
    print(f"  archive {MINI}:{VAULT}/{name}\n  unpacks into {dest}")
    if not go:
        return 0
    if os.path.exists(dest) and os.listdir(dest):
        return fail(f"{dest} is not empty; left alone")
    os.makedirs(dest, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".pull-", dir=os.path.dirname(os.path.abspath(dest)))
    try:
        local = os.path.join(tmp, name)
        r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{MINI}:{VAULT}/{name}", local])
        if r.returncode:
            return fail("scp from the mini failed")
        theirs = mini(f"shasum -a 256 '{VAULT}/{name}' | cut -c1-64").stdout.strip()
        mine = sha256(local)
        if mine != theirs:
            return fail(f"sha256 differs: local {mine[:12]} vs mini {theirs[:12]}; nothing unpacked")
        print(f"  sha256 equal on both sides: {mine[:16]}...")
        r = subprocess.run(f"zstd -dc {shlex.quote(local)} | tar -C {shlex.quote(dest)} -xf -", shell=True, capture_output=True, text=True)
        if r.returncode:
            return fail("unpack failed: " + r.stderr.strip()[-200:])
        n = sum(len(f) for _, _, f in os.walk(dest))
        print(f"  unpacked {n} file(s) into {dest}")
        return 0
    finally:
        for f in glob.glob(os.path.join(tmp, "*")):
            os.remove(f)
        os.rmdir(tmp)


def fail(msg):
    print("  FAIL", msg)
    return 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("target")
    ap.add_argument("--to", help="restore into this directory instead of the original place")
    ap.add_argument("--run", action="store_true", help="act (default: show the plan)")
    a = ap.parse_args(argv)
    to = os.path.abspath(os.path.expanduser(a.to)) if a.to else None
    m = json.load(open(os.path.join(REPO, "plan", "storage-map.json")))
    rp = os.path.join(REPO, "machines", MACHINE, "repos.json")
    repos = ledger.repo_index(json.load(open(rp)) if os.path.exists(rp) else {})
    print(f"pull {a.target}" + ("" if a.run else "  (dry run; add --run to do it)"))
    hits = find_place(a.target, m["places"])
    for pl in hits[:1] if len(hits) == 1 else []:
        full = os.path.expanduser(pl["path"]).rstrip("/")
        home, pending, why = ledger.home_of(pl, repos.get(full))
        print(f"  place {pl['path']}: home {home or 'none'} ({why})")
        kind, _, arg = (home or "").partition(":")
        if os.path.exists(os.path.join(full, ".git")) and not to:
            print("  already here (a live checkout is never touched)")
            return 0
        if kind == "github" and arg != "pending":
            return pull_github(arg, os.path.relpath(full, WS), to, a.run)
        if kind == "vault":
            return pull_vault([os.path.basename(full)] if arg.startswith("cold-storage") else [os.path.basename(arg).replace(".tar.zst", "")], to, a.run)
        if kind == "plane":
            print(f"  estate data restore {arg} {to or '<dir>'}   # needs the age identity; files come back under <dir>")
            return 0
        if kind == "rebuild":
            print(f"  {arg if arg != 'regenerable' else 'it regenerates itself when its tool runs (' + (pl.get('writer') or '') + ')'}")
            return 0
        print("  no copy elsewhere is recorded for this place: " + comes_back_msg(home, pending))
        return 1
    if len(hits) > 1:
        print("  several places match; use a longer path:")
        print("\n".join("   " + p["path"] for p in hits[:12]))
        return 2
    ret = find_retired(a.target)
    if len(ret) == 1:
        rel, url = next(iter(ret.items()))
        print(f"  retired to GitHub: {rel} <- {url}")
        return pull_github(url, rel, to, a.run)
    if len(ret) > 1:
        print("  several retired repos match; use a longer path:")
        print("\n".join(f"   {k}" for k in sorted(ret)[:12]))
        return 2
    return pull_vault([os.path.basename(a.target.rstrip("/"))], to, a.run)


def comes_back_msg(home, pending):
    return "github push pending" if pending and home else "no home declared (set home in plan/storage-map.json)"


if __name__ == "__main__":
    sys.exit(main())
