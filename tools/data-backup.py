#!/usr/bin/env python3
"""Back up data that lives outside any git repo: encrypted, chunked, to one private GitHub repo per plane.

  data-backup.py plan                 -> machines/<m>/data-plan.json (what each plane includes and skips)
  data-backup.py run PLANE [...]      build, encrypt, push (use `all` for every plane)
  data-backup.py restore PLANE DIR    rebuild a plane's files under DIR (needs the age identity)

Planes are declared in plan/data-planes.json. For each plane:
  - files are gathered under its paths, minus excludes, pruned folders (node_modules, caches, .git) and
    nested git repos (those are backed up by backup.py);
  - large generated datasets (by extension and size) are skipped and listed as `regenerable` in the
    plane's encrypted manifest, so an agent knows what existed and can rebuild it;
  - SQLite databases are copied with `.backup` first, so a live database is captured consistently;
  - everything is tar | zstd | age (recipient in plan/age-recipient.txt) | split into 95 MB chunks;
  - the plane repo (sisodias/siso-data-<plane>, private) gets one orphan commit per run (history is not
    kept, so it never balloons), pushed in batches under GitHub's 2 GB push limit.
The age identity lives at ~/.config/siso/age/estate-backup.key, with a copy on siso-vps at the same path.
Only SUMMARY.md (counts and sizes, no file names) and RESTORE.md are plaintext in the repo.

A plane may also set:
  - "machine": the machine that runs it (default laptop); `run all` and `plan` only take this machine's planes;
  - "root": the folder its paths are relative to (default ~/SISO_Workspace; "/" for a server's /opt data);
  - "encrypt": false for a plain private repo (tar | zstd | split, no age; Shaan 26 Sep: "I don't like this
    encrypted data bullshit just push it to git, split it up, make it private"). Key files stay out of any
    plane either way (exclude them): keys live on their box (ADR 0008).
  - "prune": extra folder names to skip (e.g. a browser profile's caches).
  - "manual": true keeps a one-off plane (e.g. the Hetzner archive) out of `run all`; run it by name.
  - "repo": the plane repo's name when it is not siso-data-<name> (HALO's planes: halo-data-<plane>).
  - "pre_encrypted": the files are already age-encrypted where they were made (HALO's box locks its planes to HALO's
    own public key, plan/halo-age-recipient.txt); used with "encrypt": false, so there is no second layer and no
    gitleaks pass over ciphertext. Restoring needs HALO's identity, not the estate's.
"""
import argparse, json, os, shutil, sqlite3, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
OWNER = "sisodias"
KEY = os.path.join(HOME, ".config", "siso", "age", "estate-backup.key")
WORK = os.path.join(WS, "_data", "backup-repos")
CHUNK = 95 * 1024 * 1024
PUSH_BATCH = 1500 * 1024 * 1024
PRUNE = {"node_modules", ".venv", "venv", "__pycache__", ".next", ".cache", ".emb_cache", ".git",
         "site-packages", ".turbo", ".pytest_cache", ".mypy_cache", ".playwright-cli"}
REGEN_EXT = {".csv", ".duckdb", ".sqlite", ".db", ".zip", ".parquet", ".jsonl", ".gz", ".zst", ".mp4",
             ".mov", ".wav", ".bin", ".pt", ".safetensors", ".npy", ".bundle", ".tar"}
REGEN_MIN = 20 * 1024 * 1024


def sh(*cmd, cwd=None, timeout=3600, input=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=input)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def planes(machine=None):
    pls = json.load(open(os.path.join(REPO, "plan", "data-planes.json")))["planes"]
    return pls if machine is None else [p for p in pls if p.get("machine", "laptop") == machine]


def root_of(plane):
    return plane.get("root", WS)


def gather(plane):
    inc, regen = [], []
    base = root_of(plane)
    prune = PRUNE | set(plane.get("prune", []))
    excl = [os.path.join(base, e) for e in plane.get("exclude", [])]
    keep_big = [os.path.join(base, k) for k in plane.get("keep_large", [])]
    for rel in plane["paths"]:
        root = os.path.join(base, rel)
        if os.path.isfile(root):
            inc.append(root)
            continue
        if os.path.exists(os.path.join(root, ".git")):
            print(f"  {plane['name']}: {rel} is a git repo (backup.py and its own remote hold it); skipped", file=sys.stderr)
            continue
        for dp, dns, fns in os.walk(root):
            if dp != root and os.path.exists(os.path.join(dp, ".git")):
                dns[:] = []
                continue
            dns[:] = [d for d in dns if d not in prune and not os.path.islink(os.path.join(dp, d))
                      and not any(os.path.join(dp, d) == e or os.path.join(dp, d).startswith(e + "/") for e in excl)]
            for f in fns:
                p = os.path.join(dp, f)
                if os.path.islink(p) or any(p.startswith(e + "/") or p == e for e in excl):
                    continue
                if any(f.endswith(x) for x in plane.get("skip_suffixes", [])) or any(x in f for x in plane.get("skip_contains", [])):
                    continue
                try:
                    s = os.path.getsize(p)
                except OSError:
                    continue
                ext = os.path.splitext(f)[1].lower()
                big_ok = any(p.startswith(k + "/") or p == k for k in keep_big)
                if s >= REGEN_MIN and ext in REGEN_EXT and not big_ok:
                    regen.append({"path": os.path.relpath(p, base), "bytes": s, "mtime": int(os.path.getmtime(p))})
                else:
                    inc.append(p)
    return inc, regen


def cmd_plan(machine):
    out = []
    for pl in planes(machine):
        inc, regen = gather(pl)
        size = sum(os.path.getsize(p) for p in inc if os.path.exists(p))
        out.append({"plane": pl["name"], "files": len(inc), "bytes": size, "regenerable_files": len(regen),
                    "regenerable_bytes": sum(r["bytes"] for r in regen)})
        print(f"{pl['name']:<22} include {len(inc):>7} files {size/1e9:6.2f} GB | regenerable {len(regen):>4} files {sum(r['bytes'] for r in regen)/1e9:6.2f} GB")
    json.dump({"machine": machine, "planned_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "planes": out},
              open(os.path.join(REPO, "machines", machine, "data-plan.json"), "w"), indent=1)


def recipient():
    return open(os.path.join(REPO, "plan", "age-recipient.txt")).read().split()[0]


def run_plane(pl, machine):
    name = pl.get("repo", f"siso-data-{pl['name']}")
    owner = pl.get("owner", OWNER)  # HALO planes live in the HALO-AGENCY org (moved 27 Sep)
    repo_dir = os.path.join(WORK, name)
    t0 = time.time()
    inc, regen = gather(pl)
    # skip the upload when nothing in the plane changed since the last push (big planes are GBs)
    import hashlib
    fp = hashlib.sha256(("" if pl.get("encrypt", True) else "plain\n").encode()   # switching a plane to plain re-pushes it
                        + "\n".join(f"{p}\t{os.path.getsize(p)}\t{int(os.path.getmtime(p))}" for p in sorted(inc) if os.path.exists(p)).encode()).hexdigest()
    last = (json.load(open(os.path.join(REPO, "machines", machine, "data-backup.json"))).get("results", {}).get(pl["name"], {})
            if os.path.exists(os.path.join(REPO, "machines", machine, "data-backup.json")) else {})
    # "unchanged" is recorded too, and still means GitHub holds this fingerprint (it used to re-push every other run)
    if last.get("fingerprint") == fp and last.get("status") in ("pushed", "unchanged") and not os.environ.get("ESTATE_FORCE"):
        return dict(last, status="unchanged", fingerprint=fp)
    # every push is an orphan snapshot that replaces the last: a plane whose files have left the disk (moved, vaulted)
    # must never overwrite the only copy with an empty one (agent-base-runs, 26 Sep: 275 files on GitHub, 0 on disk)
    if not inc and last.get("files"):
        return dict(last, status="held", held_reason="no files on disk; GitHub keeps the last snapshot")
    base = root_of(pl)
    if not pl.get("encrypt", True) and not pl.get("pre_encrypted"):
        held = secrets_in(pl, inc)
        if held:
            return {"plane": pl["name"], "status": "held", "held_reason": f"gitleaks: {len(held)} finding(s) in a plain plane; "
                    "exclude those files or keep the plane encrypted", "findings": held[:10], "fingerprint": last.get("fingerprint"),
                    "files": last.get("files", 0)}
    stage = os.path.join(WS, "_data", "estate-stage", pl["name"])
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    try:
        # consistent copies of SQLite databases
        files = []
        for p in inc:
            if p.endswith((".db", ".sqlite")) and pl.get("sqlite_backup"):
                dst = os.path.join(stage, "sqlite", os.path.relpath(p, base))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                try:
                    src = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                    dstc = sqlite3.connect(dst)
                    src.backup(dstc)
                    dstc.close(); src.close()
                    files.append(("sqlite/" + os.path.relpath(p, base), dst))
                    continue
                except Exception:
                    pass
            files.append((os.path.relpath(p, base), p))
        manifest = {"plane": pl["name"], "machine": machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "files": [{"path": r, "bytes": os.path.getsize(a)} for r, a in files if os.path.exists(a)],
                    "regenerable_not_included": regen}
        mpath = os.path.join(stage, "MANIFEST.json")
        json.dump(manifest, open(mpath, "w"), indent=0)
        # tar reads a list of (archive name -> real path) via a staging tree of links would be slow;
        # instead tar with -C WS for normal files and -C stage for the sqlite copies and manifest
        # one list, all relative to WS; the stage prefix is stripped so MANIFEST.json and sqlite/ sit at the
        # archive root (bsdtar reads -T after every -C, so a second -C cannot be used)
        stage_rel = os.path.relpath(stage, base)
        names = [r for r, a in files if not r.startswith("sqlite/")]
        names.append(os.path.join(stage_rel, "MANIFEST.json"))
        if os.path.isdir(os.path.join(stage, "sqlite")):
            names.append(os.path.join(stage_rel, "sqlite"))
        lst = os.path.join(stage, "files.lst")
        open(lst, "wb").write(b"\0".join(r.encode() for r in names) + b"\0")
        chunks_dir = os.path.join(stage, "chunks")
        os.makedirs(chunks_dir)
        gnu = "GNU tar" in sh("tar", "--version")[1]
        strip = [f"--transform=s,^{stage_rel}/,,"] if gnu else ["-s", f",^{stage_rel}/,,"]
        tar_args = ["tar", "-cf", "-", "--null", "-C", base, *strip, "-T", lst]
        enc = pl.get("encrypt", True)
        prefix = "data.tar.zst.age." if enc else "data.tar.zst."
        pipe = (f"{' '.join(map(shq, tar_args))} 2>{shq(os.path.join(stage, 'tar.err'))} | zstd -q -T0 -6 | "
                + (f"age -r {shq(recipient())} | " if enc else "")
                + f"split -b {CHUNK} -a 3 - {shq(os.path.join(chunks_dir, prefix))}")
        r = subprocess.run(["bash", "-o", "pipefail", "-c", pipe], capture_output=True, text=True)
        if r.returncode != 0:
            err = open(os.path.join(stage, "tar.err")).read()[-400:]
            # tar exits 1 when a file changed while read; the archive is still complete for the rest
            if "changed as we read" not in err and "file changed" not in err:
                return {"plane": pl["name"], "status": "error", "error": (r.stderr or err)[-400:]}
        chunks = sorted(os.listdir(chunks_dir))
        total = sum(os.path.getsize(os.path.join(chunks_dir, c)) for c in chunks)
        # the plane repo: create on GitHub if needed, one orphan commit, pushed in batches
        code, _, _ = sh("gh", "repo", "view", f"{owner}/{name}", "--json", "name")
        if code != 0:
            code, _, err = sh("gh", "repo", "create", f"{owner}/{name}", "--private",
                              "--description", ("Encrypted" if enc else "Plain private") + f" backup of {', '.join(pl['paths'])}"[:300] + " (siso-estate data plane)")  # GitHub caps it at 350
            if code != 0:
                return {"plane": pl["name"], "status": "error", "error": "create: " + err[-300:]}
            sh("gh", "api", "-X", "PUT", f"repos/{owner}/{name}/actions/permissions", "-F", "enabled=false")
        if os.path.isdir(repo_dir):
            shutil.rmtree(repo_dir)
        os.makedirs(repo_dir)
        sh("git", "init", "-q", "-b", "main", cwd=repo_dir)
        # encrypted chunks are random bytes: no delta search, no zlib (both only burn CPU before the push)
        for k, v in (("core.bigFileThreshold", "1m"), ("core.compression", "0"), ("pack.window", "0"), ("pack.depth", "0")):
            sh("git", "config", k, v, cwd=repo_dir)
        sh("git", "remote", "add", "origin", f"https://github.com/{owner}/{name}.git", cwd=repo_dir)
        by_top = {}
        for f in manifest["files"]:
            top = "/".join(f["path"].split("/")[:2])
            by_top[top] = by_top.get(top, 0) + f["bytes"]
        label = ("HALO age-encrypted payload (unencrypted transport archive)" if pl.get("pre_encrypted")
                 else "Encrypted" if enc else "Plain (unencrypted, private repo)")
        summary = [f"# {name}", "", label + f" backup of `{', '.join(pl['paths'])}` from the {machine}, taken {manifest['at']}.",
                   f"{len(manifest['files'])} files, {sum(by_top.values())/1e9:.2f} GB before compression; "
                   f"{len(chunks)} {'encrypted ' if enc else ''}chunks, {total/1e9:.2f} GB.",
                   f"{len(regen)} large generated datasets ({sum(x['bytes'] for x in regen)/1e9:.2f} GB) are listed in the "
                   "encrypted MANIFEST.json as `regenerable_not_included`.", "", "| Folder | Size |", "|---|---|"]
        summary += [f"| `{k}` | {v/1e6:.0f} MB |" for k, v in sorted(by_top.items(), key=lambda x: -x[1])[:40]]
        open(os.path.join(repo_dir, "SUMMARY.md"), "w").write("\n".join(summary) + "\n")
        open(os.path.join(repo_dir, "RESTORE.md"), "w").write(
            f"# Restore\n\nFrom the {machine}; paths are relative to `{base}`. Each run's `*.tar.zst.age` is encrypted to HALO's "
            "own age key (identity: ~/.config/siso/age/halo-backup.key on siso-vps; the estate credentials store under "
            "SISO_Agency/partners/halo/age).\n\n```bash\ncd <this repo>\ncat data.tar.zst.* | zstd -d | tar -xf - -C <dir>\n"
            "age -d -i ~/.config/siso/age/halo-backup.key <dir>/.../<plane>.tar.zst.age | zstd -d | tar -xf - -C <out>\n"
            "cd <out> && sha256sum -c FILES.sha256\n```\n" if pl.get("pre_encrypted") else
            f"# Restore\n\nFrom the {machine}; paths are relative to `{base}`.\n\n```bash\ncd <this repo>\ncat data.tar.zst.* | zstd -d | tar -xf - -C <dir>\n```\n\n"
            "Or `estate data restore <plane> <dir>`. SQLite databases come back under `sqlite/` as consistent copies.\n" if not enc else
            "# Restore\n\nNeeds the age identity (`~/.config/siso/age/estate-backup.key`; a copy is on siso-vps at the same path).\n\n"
            "```bash\ncd <this repo>\ncat data.tar.zst.age.* | age -d -i ~/.config/siso/age/estate-backup.key | zstd -d | tar -xf - -C ~/SISO_Workspace\n```\n\n"
            "Or `estate data restore <plane> <dir>`. SQLite databases come back under `sqlite/` as consistent copies.\n")
        sh("git", "add", "SUMMARY.md", "RESTORE.md", cwd=repo_dir)
        sh("git", "commit", "-qm", f"{name}: summary", cwd=repo_dir)
        batch, pushed = 0, 0
        env_first = True
        for c in chunks:
            chunk_path = os.path.join(repo_dir, c)
            shutil.move(os.path.join(chunks_dir, c), chunk_path)
            chunk_size = os.path.getsize(chunk_path)
            code, _, err = sh("git", "add", c, cwd=repo_dir)
            if code != 0:
                return {"plane": pl["name"], "status": "error", "error": "add chunk: " + err[-300:]}
            code, stored_size, err = sh("git", "cat-file", "-s", ":" + c, cwd=repo_dir)
            if code != 0 or stored_size != str(chunk_size):
                return {"plane": pl["name"], "status": "error", "error": "chunk not stored in Git: " + c}
            # The index and blob keep the chunk for commits/pushes. Release only this temporary
            # workfile; retaining it too doubles the plane's disk use. Source files stay intact.
            os.unlink(chunk_path)
            batch += chunk_size
            if batch >= PUSH_BATCH:
                sh("git", "commit", "-qm", f"{name}: chunks up to {c}", cwd=repo_dir)
                code, _, err = sh("git", "push", "-q", "-f" if env_first else "-q", "origin", "HEAD:main", cwd=repo_dir, timeout=7200)
                if code != 0:
                    return {"plane": pl["name"], "status": "error", "error": "push: " + err[-300:]}
                env_first, batch = False, 0
        sh("git", "commit", "-qm", f"{name}: snapshot {manifest['at']}", cwd=repo_dir)
        code, _, err = sh("git", "push", "-q", "-f", "origin", "HEAD:main", cwd=repo_dir, timeout=7200)
        if code != 0:
            return {"plane": pl["name"], "status": "error", "error": "push: " + err[-300:]}
        # the whole local clone is a leftover once GitHub holds this exact commit (the next run re-inits it anyway);
        # its .git keeps every chunk, which on 24 Sep was 8.4 GB and helped fill the disk
        _, head, _ = sh("git", "rev-parse", "HEAD", cwd=repo_dir)
        _, remote, _ = sh("git", "ls-remote", "origin", "refs/heads/main", cwd=repo_dir)
        if not remote or remote.split()[0] != head:
            return {"plane": pl["name"], "status": "error", "error": f"pushed but GitHub main != local {head[:12]}; clone kept"}
        shutil.rmtree(repo_dir, ignore_errors=True)
        return {"plane": pl["name"], "status": "pushed", "fingerprint": fp, "repo": f"{owner}/{name}", "files": len(manifest["files"]),
                "chunks": len(chunks), "encrypted_bytes": total, "regenerable_skipped": len(regen),
                "secs": round(time.time() - t0)}
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def secrets_in(pl, inc):
    """A plain plane is readable by anyone who can read the repo: gitleaks every path first (ADR 0008), on its own
    exit code. Returns [{file, rule}] for findings inside the files the plane would push (values never printed)."""
    base, keep, out = root_of(pl), set(inc), []
    for rel in pl["paths"]:
        path = os.path.join(base, rel)
        if not os.path.exists(path):
            continue
        fd, rep = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            r = subprocess.run(["gitleaks", "dir", path, "--redact", "--no-banner", "--max-target-megabytes", "20",
                                "-r", rep, "--log-level", "error"], capture_output=True, text=True, timeout=3600)
            if r.returncode not in (0, 1):
                return [{"file": rel, "rule": "gitleaks failed: " + r.stderr.strip()[-120:]}]
            for f in (json.load(open(rep)) if r.returncode == 1 and os.path.getsize(rep) else []):
                fp = f.get("File", "")
                fp = fp if os.path.isabs(fp) else os.path.join(path, fp) if os.path.isdir(path) else path
                if fp in keep:
                    out.append({"file": os.path.relpath(fp, base), "rule": f.get("RuleID", "")})
        finally:
            os.unlink(rep)
    return out


def shq(s):
    return "'" + str(s).replace("'", "'\\''") + "'"


def cmd_restore(plane, dest):
    pl = next((p for p in planes() if p["name"] == plane), {})
    name = pl.get("repo", f"siso-data-{plane}")
    owner = pl.get("owner", OWNER)
    tmp = tempfile.mkdtemp(prefix="estate-restore-")
    code, _, err = sh("git", "clone", "-q", "--depth", "1", f"https://github.com/{owner}/{name}.git", tmp, timeout=7200)
    if code != 0:
        sys.exit(err)
    os.makedirs(dest, exist_ok=True)
    if any(f.startswith("data.tar.zst.age.") for f in os.listdir(tmp)):
        pipe = f"cat {shq(tmp)}/data.tar.zst.age.* | age -d -i {shq(KEY)} | zstd -d | tar -xf - -C {shq(dest)}"
    else:
        pipe = f"cat {shq(tmp)}/data.tar.zst.* | zstd -d | tar -xf - -C {shq(dest)}"
    code = subprocess.call(["bash", "-o", "pipefail", "-c", pipe])
    shutil.rmtree(tmp, ignore_errors=True)                 # the clone is a leftover once extracted
    sys.exit(code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run", "restore"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    if a.cmd == "plan":
        return cmd_plan(a.machine)
    if a.cmd == "restore":
        return cmd_restore(a.args[0], a.args[1])
    names = [p["name"] for p in planes(a.machine) if not p.get("manual")] if a.args == ["all"] else a.args
    res_path = os.path.join(REPO, "machines", a.machine, "data-backup.json")
    os.makedirs(os.path.dirname(res_path), exist_ok=True)
    prev = json.load(open(res_path)) if os.path.exists(res_path) else {"results": {}}
    for pl in planes():
        if pl["name"] in names:
            r = run_plane(pl, a.machine)
            r["at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            prev["results"][pl["name"]] = r
            json.dump(prev, open(res_path, "w"), indent=1)
            print(json.dumps(r))


if __name__ == "__main__":
    main()
