#!/usr/bin/env python3
"""Move a vault folder (_archive/<folder>) off the laptop to a private GitHub repo, losslessly, then release it.

  archive-offload.py plan                  every _archive folder: size, and whether it may go (HALO's never does)
  archive-offload.py run FOLDER... [--run] tar (everything: .git, node_modules, dirty work) | zstd | age, split into
                                           95 MB parts, pushed to sisodias/siso-archive-<folder> (private) in batches;
                                           then a FRESH clone is decrypted and every file, link and mode compared with
                                           the hashes taken while reading the source. Only then is the folder released,
                                           leaving _archive/<folder>.OFFLOADED.md (repo, head, counts, restore).

Why not data-backup.py: its planes leave out .git, nested repos and node_modules, and these folders are mostly retired
repos. Why verify from a fresh clone: on 6 Oct the plane producer was found to lose files silently on restore for some
macOS inputs. Restore: `archive-offload.py restore FOLDER DEST` (needs ~/.config/siso/age/estate-backup.key).
GitHub is the sync plane (Shaan, 24 Sep); HALO never leaves Cam's repo (ADR 0007). Receipts: machines/<m>/archive-offload.jsonl.
"""
import argparse, hashlib, io, json, os, re, shutil, socket, stat, subprocess, sys, tarfile, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.expanduser("~/SISO_Workspace")
ARCHIVE = os.path.join(WS, "_archive")
WORK = os.path.join(WS, "_data", "archive-offload-work")
OWNER = "sisodias"
KEY = os.path.expanduser("~/.config/siso/age/estate-backup.key")
RECIPIENT = open(os.path.join(REPO, "plan", "age-recipient.txt")).read().split()[0]
PART = 95 * 1024 * 1024
PUSH_BATCH = 4  # parts per push: ~380 MB (8 Oct: a 1.1 GB push over HTTPS timed out with HTTP 408)
NEVER = re.compile(r"halo|oracle|cam-|kellman|fahmy|bykonz|whatsapp|life|personal", re.I)
MACHINE = "laptop" if socket.gethostname().startswith("shaans-MacBook") else socket.gethostname().split(".")[0]
RECEIPTS = os.path.join(REPO, "machines", MACHINE, "archive-offload.jsonl")
VAULT_HOST = "mini-fast"  # the second copy: a bare mirror on the mini's external USB vault drive, as the image bank does
VAULT_DIR = "/Volumes/SISO-STORAGE-VAULT/SISO-VAULT/SISO_Workspace/_data/backup-repos/archive-offload"


def mirror(full, head):
    """Bare-mirror the GitHub repo onto the mini's vault drive and prove its head; returns the vault path or raises."""
    dest = f"{VAULT_DIR}/{full.split('/')[1]}.git"
    remote = (f"set -e; export PATH=/opt/homebrew/bin:/usr/local/bin:$PATH GIT_TERMINAL_PROMPT=0; "
              f"test -d /Volumes/SISO-STORAGE-VAULT/SISO-VAULT; mkdir -p {VAULT_DIR}; "
              f"if [ -d {dest} ]; then git -C {dest} fetch -q --prune origin; "
              f"else git clone -q --mirror https://github.com/{full}.git {dest}; fi; "
              f"git -C {dest} fsck --connectivity-only --no-progress 1>&2; git -C {dest} rev-parse refs/heads/main")
    got = (sh("ssh", "-o", "BatchMode=yes", "-o", "ServerAliveInterval=30", VAULT_HOST, remote).split() or [""])[-1]
    if got != head:
        raise SystemExit(f"{full}: mini vault mirror head {got[:10]} != {head[:10]}; nothing released")
    return f"{VAULT_HOST}:{dest}"


def sh(*a, cwd=None, check=True):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"failed: {' '.join(a[:4])}...\n{r.stderr[-600:]}")
    return r.stdout


def repo_name(folder):
    return "siso-archive-" + re.sub(r"[^A-Za-z0-9._-]", "-", folder)[:80]


def why_not(folder):
    if NEVER.search(folder):
        return "HALO / private: never leaves (ADR 0007)"
    if os.path.exists(os.path.join(ARCHIVE, folder + ".OFFLOADED.md")):
        return "already offloaded"
    return None


class Hashing(io.RawIOBase):
    """Reads a file into the tar while hashing it, so the source is read once."""
    def __init__(self, f):
        self.f, self.h = f, hashlib.sha256()

    def readable(self):
        return True

    def readinto(self, b):
        n = self.f.readinto(b)
        if n:
            self.h.update(memoryview(b)[:n])
        return n


def entry(ti):
    return {"type": "d" if ti.isdir() else "l" if ti.issym() else "f", "mode": ti.mode & 0o7777,
            "size": ti.size if ti.isfile() else 0, "link": ti.linkname if ti.issym() else ""}


def build(folder, out_dir):
    """Write the encrypted parts; return {relative path: entry with sha256} for every entry read."""
    src = os.path.join(ARCHIVE, folder)
    zst = subprocess.Popen(["taskpolicy", "-b", "zstd", "-q", "-T2", "-10", "-c"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    age = subprocess.Popen(["age", "-r", RECIPIENT], stdin=zst.stdout, stdout=subprocess.PIPE)
    zst.stdout.close()
    import threading
    parts = []

    def splitter():
        i = 0
        while True:
            buf = age.stdout.read(PART)
            if not buf:
                break
            name = f"part-{i:05d}"
            with open(os.path.join(out_dir, name), "wb") as f:
                f.write(buf)
            parts.append((name, hashlib.sha256(buf).hexdigest(), len(buf)))
            i += 1
    t = threading.Thread(target=splitter)
    t.start()
    seen = {}
    with tarfile.open(fileobj=zst.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
        for root, dirs, files in os.walk(src, followlinks=False):
            dirs.sort()
            for name in [None] + sorted(files) + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
                path = root if name is None else os.path.join(root, name)
                rel = os.path.relpath(path, ARCHIVE)
                st = os.lstat(path)
                if not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                    continue  # sockets, fifos: nothing to keep
                ti = tar.gettarinfo(path, arcname=rel)
                e = entry(ti)
                if ti.isfile():
                    with open(path, "rb") as f:
                        hr = Hashing(f)
                        tar.addfile(ti, io.BufferedReader(hr, 1 << 20))
                        e["sha256"] = hr.h.hexdigest()
                else:
                    tar.addfile(ti)
                seen[rel] = e
    zst.stdin.close()
    t.join()
    if zst.wait() or age.wait():
        raise SystemExit("zstd or age failed")
    return seen, parts


def read_back(clone, parts):
    """Decrypt a fresh clone's parts and return {relative path: entry with sha256}."""
    cat = subprocess.Popen(["cat"] + [os.path.join(clone, p) for p, _, _ in parts], stdout=subprocess.PIPE)
    dec = subprocess.Popen(["age", "-d", "-i", KEY], stdin=cat.stdout, stdout=subprocess.PIPE)
    cat.stdout.close()
    unz = subprocess.Popen(["taskpolicy", "-b", "zstd", "-q", "-d", "-c"], stdin=dec.stdout, stdout=subprocess.PIPE)
    dec.stdout.close()
    got = {}
    with tarfile.open(fileobj=unz.stdout, mode="r|") as tar:
        for ti in tar:
            e = entry(ti)
            if ti.isfile():
                h, f = hashlib.sha256(), tar.extractfile(ti)
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
                e["sha256"] = h.hexdigest()
            got[ti.name] = e
    if unz.wait() or dec.wait() or cat.wait():
        raise SystemExit("read-back pipeline failed")
    return got


def push(repo_dir, full, parts, folder, files):
    sh("git", "init", "-q", "-b", "main", cwd=repo_dir)
    with open(os.path.join(repo_dir, "README.md"), "w") as f:
        f.write(f"# {folder}\n\nThe SISO estate vault folder `_archive/{folder}`, moved off the laptop on "
                f"{time.strftime('%Y-%m-%d')}: tar | zstd | age (estate key), split into {len(parts)} parts of 95 MB.\n"
                f"{files} entries. Restore: `python3 SISO_Agents/siso-estate/tools/archive-offload.py restore {folder} DEST`,\n"
                f"or `cat part-* | age -d -i ~/.config/siso/age/estate-backup.key | zstd -d | tar -x`.\n")
    with open(os.path.join(repo_dir, "SHA256SUMS"), "w") as f:
        f.writelines(f"{h}  {p}\n" for p, h, _ in parts)
    if subprocess.run(["gh", "repo", "view", full], capture_output=True).returncode:
        sh("gh", "repo", "create", full, "--private", "--description", f"SISO estate vault: _archive/{folder} (encrypted)")
    sh("git", "remote", "add", "origin", f"https://github.com/{full}.git", cwd=repo_dir)
    sh("git", "config", "http.postBuffer", "524288000", cwd=repo_dir)
    batches = [parts[i:i + PUSH_BATCH] for i in range(0, len(parts), PUSH_BATCH)] or [[]]
    for i, batch in enumerate(batches):
        sh("git", "add", "--", *([p for p, _, _ in batch] + (["README.md", "SHA256SUMS"] if i == 0 else [])), cwd=repo_dir)
        sh("git", "-c", "user.name=SISO Estate", "-c", "user.email=estate@siso.local", "commit", "-q", "-m",
           f"vault parts {i + 1}/{len(batches)}", cwd=repo_dir)
        for name, _, _ in batch:  # git holds it now: the working copy would double the temporary disk use
            os.remove(os.path.join(repo_dir, name))
        for attempt in range(4):
            r = subprocess.run(["git", "push", "-q", "-u", "origin", "main"], cwd=repo_dir, capture_output=True, text=True)
            if r.returncode == 0:
                break
            time.sleep(20 * (attempt + 1))
        else:
            raise SystemExit(f"push failed 4 times: {r.stderr[-400:]}")
    return sh("git", "rev-parse", "HEAD", cwd=repo_dir).strip()


def run(folder, go):
    why = why_not(folder)
    src = os.path.join(ARCHIVE, folder)
    if why or not os.path.isdir(src) or os.path.islink(src):
        print(f"skip {folder}: {why or 'not a folder'}")
        return
    full = f"{OWNER}/{repo_name(folder)}"
    kib = int(sh("du", "-sk", src).split()[0])
    if not go:
        print(f"would offload {folder} ({kib / 1048576:.2f} GiB) -> {full}")
        return
    if subprocess.run(["gh", "repo", "view", full], capture_output=True).returncode == 0 and \
            subprocess.run(["gh", "api", f"repos/{full}/commits?per_page=1"], capture_output=True).returncode == 0:
        raise SystemExit(f"{full} already has commits: check it by hand before anything else")  # an empty one is reused
    # the temporary copy can be as big as the folder: keep 10 GiB free beyond it (8 Oct 18:49: free hit 6.7 GiB, laptop hung)
    if shutil.disk_usage(WS).free < kib * 1024 + 10 * 2**30:
        raise SystemExit(f"hold {folder}: needs {kib / 1048576 + 10:.1f} GiB free, have {shutil.disk_usage(WS).free / 2**30:.1f}")
    os.makedirs(WORK, exist_ok=True)
    t0, free0 = time.time(), shutil.disk_usage(WS).free
    repo_dir = tempfile.mkdtemp(prefix=repo_name(folder) + "-", dir=WORK)
    try:  # the parts are a temporary copy: never leave them behind on the laptop
        seen, parts = build(folder, repo_dir)
        head = push(repo_dir, full, parts, folder, len(seen))
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)
    clone = tempfile.mkdtemp(prefix="verify-", dir=WORK)
    try:
        sh("git", "clone", "-q", f"https://github.com/{full}.git", clone)
        remote_head = sh("git", "rev-parse", "HEAD", cwd=clone).strip()
        sums = dict(reversed(l.split("  ")) for l in open(os.path.join(clone, "SHA256SUMS")).read().splitlines())
        for p, h, _ in parts:
            with open(os.path.join(clone, p), "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != h or sums.get(p) != h:
                    raise SystemExit(f"{folder}: part {p} differs on GitHub; nothing released")
        got = read_back(clone, parts)
    finally:
        shutil.rmtree(clone, ignore_errors=True)
    bad = [k for k in set(seen) | set(got) if seen.get(k) != got.get(k)]
    if remote_head != head or bad:
        raise SystemExit(f"{folder}: NOT released: head {remote_head == head}, {len(bad)} entries differ, e.g. {bad[:3]}")
    vault = None  # GitHub is the store (Shaan, 8 Oct: the mini mirror was "way too much extra"); `mirror-backfill` adds one
    nbytes = sum(n for _, _, n in parts)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "folder": folder, "repo": full, "head": head,
           "parts": len(parts), "cipher_bytes": nbytes, "entries": len(seen),
           "files": sum(1 for e in seen.values() if e["type"] == "f"), "source_du_kib": kib,
           "verified": "fresh clone: every part sha256, every entry type/mode/size/link/sha256 equal", "vault": vault}
    with open(os.path.join(ARCHIVE, folder + ".OFFLOADED.md"), "w") as f:
        f.write(f"# {folder} — on GitHub, not on this laptop\n\nMoved off on {rec['at']} by ESTATE "
                f"(tools/archive-offload.py; Shaan, 8 Oct: the storage owner). Repo: https://github.com/{full} "
                f"(private), head {head}, {len(parts)} encrypted parts, {nbytes / 2**30:.2f} GiB.\n"
                f"{rec['entries']} entries ({rec['files']} files) verified from a fresh clone before release.\n\n"
                f"Restore: `python3 ~/SISO_Workspace/SISO_Agents/siso-estate/tools/archive-offload.py restore {folder} "
                f"~/SISO_Workspace/_archive/` (needs ~/.config/siso/age/estate-backup.key).\n")
    shutil.rmtree(src)
    rec["free_gain_kib"] = (shutil.disk_usage(WS).free - free0) // 1024
    rec["seconds"] = int(time.time() - t0)
    with open(RECEIPTS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"offloaded {folder}: {len(parts)} parts, {rec['entries']} entries verified, free {rec['free_gain_kib'] / 1048576:+.2f} GiB, {rec['seconds']} s")


CHATS = [("claude", "~/.claude/projects"), ("claude-siso", "~/.claude-siso/projects"),
         ("claude-siso-3", "~/.config/claude-siso-3/projects"), ("codex", "~/.codex/sessions")]


PRIVATE = re.compile(r"halo|oracle|kellman|fahmy|bykonz|whatsapp|life|personal", re.I)


def private_chat(path, root):
    """A chat from Life, WhatsApp, Fahmy, personal or HALO work never goes into git, encrypted or not. Claude's folder
    name is the session's working path; a Codex rollout records its cwd on its first line."""
    if PRIVATE.search(os.path.relpath(path, root).split(os.sep)[0]) and "sessions" not in root:
        return True
    if "sessions" in root:
        try:  # only the cwd: the first line also carries the global instructions, which name Life and Fahmy
            with open(path, errors="replace") as f:
                cwd = json.loads(f.readline()).get("payload", {}).get("cwd")
            return not isinstance(cwd, str) or bool(PRIVATE.search(cwd))
        except (OSError, ValueError, AttributeError):
            return True
    return False


def chats(days, go):
    """Chat transcripts (*.jsonl) older than DAYS, held by no process, gathered into _archive/<date>-chats-<days>d and
    offloaded like any vault folder. Never deleted: restore puts them back under the same relative paths. Memory files
    and anything that is not a transcript stay."""
    cutoff, held = time.time() - days * 86400, set()
    out = sh("lsof", "-u", str(os.getuid()), "-Fn", "-w", check=False)
    if not out:
        raise SystemExit("cannot see open files (lsof): touching nothing")
    held = {l[1:] for l in out.splitlines() if l.startswith("n/")}
    folder = f"{time.strftime('%Y-%m-%d')}-chats-older-than-{days}d"
    picked = []
    for label, root in CHATS:
        root = os.path.expanduser(root)
        for d, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [x for x in dirs if x != "memory" and not os.path.islink(os.path.join(d, x))]
            for f in files:
                p = os.path.join(d, f)
                if f.endswith(".jsonl") and not os.path.islink(p) and os.lstat(p).st_mtime < cutoff and p not in held \
                        and not private_chat(p, root):
                    picked.append((p, os.path.join(ARCHIVE, folder, label, os.path.relpath(p, root))))
    gib = sum(os.lstat(p).st_blocks for p, _ in picked) * 512 / 2**30
    print(f"{len(picked)} transcripts older than {days} days, {gib:.2f} GiB -> _archive/{folder}")
    if not go or not (picked or os.path.isdir(os.path.join(ARCHIVE, folder))):
        return
    for src, dst in picked:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.rename(src, dst)
        except FileNotFoundError:  # its harness removed it between the scan and the move (8 Oct: a -private-tmp chat)
            continue
    with open(os.path.join(ARCHIVE, folder, "MANIFEST.md"), "w") as f:
        f.write(f"# {folder}\n\n{len(picked)} chat transcripts (Claude, Codex) not written for {days} days, moved here by "
                f"ESTATE (tools/archive-offload.py chats; brief 8 Oct: archive chats to GitHub encrypted, never delete). "
                f"Each sits under <harness>/<its path inside that harness's projects or sessions folder>; move it back "
                f"there to resume it.\n")
    run(folder, True)


def restore(folder, dest):
    full = f"{OWNER}/{repo_name(folder)}"
    clone = tempfile.mkdtemp(prefix="restore-")
    sh("git", "clone", "-q", f"https://github.com/{full}.git", clone)
    parts = sorted(p for p in os.listdir(clone) if p.startswith("part-"))
    os.makedirs(dest, exist_ok=True)
    subprocess.run(f"cat {' '.join(parts)} | age -d -i '{KEY}' | zstd -d -c | tar -x -C '{dest}'", shell=True, cwd=clone, check=True)
    shutil.rmtree(clone)
    print(f"restored {folder} into {dest}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    r = sub.add_parser("run"); r.add_argument("folders", nargs="+"); r.add_argument("--run", action="store_true")
    s = sub.add_parser("restore"); s.add_argument("folder"); s.add_argument("dest")
    sub.add_parser("mirror-backfill")
    c = sub.add_parser("chats"); c.add_argument("--days", type=int, default=14); c.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "mirror-backfill":
        for line in open(RECEIPTS):
            r = json.loads(line)
            if not r.get("vault"):
                print(r["folder"], "->", mirror(r["repo"], r["head"]))
        return 0
    if a.cmd == "chats":
        return chats(a.days, a.run)
    if a.cmd == "plan":
        for f in sorted(os.listdir(ARCHIVE)):
            p = os.path.join(ARCHIVE, f)
            if os.path.isdir(p) and not os.path.islink(p):
                print(f"{why_not(f) or 'may go':<44} {f}")
    elif a.cmd == "run":
        for f in a.folders:
            run(f, a.run)
    else:
        restore(a.folder, a.dest)


if __name__ == "__main__":
    sys.exit(main())
