#!/usr/bin/env python3
"""Files GitHub refuses (one file over ~95 MB) made fit for a house, losslessly (ADR 0014).

  bigfiles.py plan  FOLDER                     every file over the limit in FOLDER (nested repos and ignored paths
                                               skipped) and what would happen to it: unzip (a zip whose entries all fit)
                                               or split (anything else)
  bigfiles.py unzip ZIP... [--run]             extract next to the zip (refuses to overwrite anything), check every
                                               entry's size and CRC-32 against the zip, then remove the zip
  bigfiles.py split FILE... [--keep] [--run]   write FILE.part-000, -001, ... (90 MB each) and FILE.sha256, check the
                                               joined parts hash to the original, then remove it (--keep leaves it:
                                               for untouched originals; the house must then ignore it)
  bigfiles.py drop  COPY --of ORIGINAL [--run] remove COPY when its sha256 equals ORIGINAL's, or when COPY is a zip
                                               whose every file is already in the folder ORIGINAL (size + CRC-32)

Without --run nothing is written. Every removal is proven just before it happens and gets a line in
machines/<machine>/removed.jsonl (ADR 0004). Joining a split file back: see SPLIT_NOTE below.
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, time, zipfile, zlib

LIMIT = 95 * 1024 * 1024
PART = 90 * 1024 * 1024
MIN_FREE = 5 * 1024 ** 3  # never write the disk below this (a full disk crashed herdr and every agent, 24 Sep)
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
SPLIT_NOTE = """# Files split for GitHub

GitHub refuses any file over 100 MB, so each file listed here is stored as `<name>.part-000`, `<name>.part-001`, ...
plus `<name>.sha256` (siso-estate tools/bigfiles.py, ADR 0014). To get one back, in its folder:

    cat "<name>".part-* > "<name>" && shasum -a 256 -c "<name>".sha256

| File | Parts | Bytes | sha256 |
|---|---|---|---|
"""


def tilde(p):
    p = os.path.abspath(p)
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def room(path, need):
    free = shutil.disk_usage(os.path.dirname(path)).free
    if free - need < MIN_FREE:
        raise SystemExit(f"refused: {tilde(path)} needs {need / 2**30:.1f} GB and only {free / 2**30:.1f} GB is free "
                         f"(keeps {MIN_FREE / 2**30:.0f} GB free for the agents)")


def removed(path, nbytes, why, proof):
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "path": tilde(path), "bytes": nbytes, "why": why, "proof": proof}
    with open(os.path.join(REPO, "machines", os.environ.get("ESTATE_MACHINE", "laptop"), "removed.jsonl"), "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec))


def fits(zpath):
    try:
        with zipfile.ZipFile(zpath) as z:
            return all(i.file_size <= LIMIT for i in z.infolist())
    except zipfile.BadZipFile:
        return False


def big_files(folder):
    """Files over the limit that git would pick up in FOLDER: untracked-not-ignored (or all files when not a repo)."""
    folder = os.path.abspath(folder)
    out = []
    if os.path.isdir(os.path.join(folder, ".git")):
        r = subprocess.run(["git", "-C", folder, "ls-files", "-z", "-co", "--exclude-standard"], capture_output=True, text=True)
        paths = [os.path.join(folder, p) for p in r.stdout.split("\0") if p]
    else:
        paths = []
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if not os.path.exists(os.path.join(root, d, ".git")) and d != ".git"]
            paths += [os.path.join(root, f) for f in files]
    for p in paths:
        if os.path.isfile(p) and not os.path.islink(p) and os.path.getsize(p) > LIMIT:
            out.append(p)
    return sorted(out)


def cmd_plan(folders):
    for folder in folders:
        for p in big_files(folder):
            act = "unzip" if p.lower().endswith(".zip") and fits(p) else "split"
            print(f"{os.path.getsize(p) / 1048576:8.0f} MB  {act:5}  {tilde(p)}")


def cmd_unzip(zips, run):
    for zp in zips:
        zp = os.path.abspath(zp)
        dest = os.path.dirname(zp)
        with zipfile.ZipFile(zp) as z:
            infos = [i for i in z.infolist() if not i.is_dir()]
            if not fits(zp):
                raise SystemExit(f"refused: an entry in {tilde(zp)} is itself over the limit; split the zip instead")
            clash = [i.filename for i in infos if os.path.lexists(os.path.join(dest, i.filename))]
            bad = [i.filename for i in infos if os.path.isabs(i.filename) or ".." in i.filename.split("/")]
            if clash or bad:
                raise SystemExit(f"refused: {tilde(zp)} would overwrite {clash[:3]} or escape its folder {bad[:3]}")
            tops = sorted({i.filename.split("/")[0] for i in infos})
            print(f"{tilde(zp)}: {len(infos)} files -> {dest}/{tops if len(tops) > 1 else tops[0]}")
            if not run:
                continue
            room(zp, sum(i.file_size for i in infos))
            z.extractall(dest)
            for i in infos:  # the proof: every extracted file matches the zip's size and CRC-32
                p = os.path.join(dest, i.filename)
                crc = 0
                with open(p, "rb") as f:
                    for b in iter(lambda: f.read(1 << 20), b""):
                        crc = zlib.crc32(b, crc)
                if os.path.getsize(p) != i.file_size or crc != i.CRC:
                    raise SystemExit(f"stopped: {p} does not match the zip; the zip is kept")
        n = os.path.getsize(zp)
        os.unlink(zp)
        removed(zp, n, "unzipped for GitHub (a zip is a transport wrapper; its files are kept)",
                f"extracted {len(infos)} files into {tilde(dest)}; each file's size and CRC-32 matched the zip entry")


PARTS_RULE = "*.part-[0-9][0-9][0-9] binary"


def mark_binary(folder):
    """Parts are raw bytes: git (and so gitleaks, which reads git's diffs) treats them as binary. A split text file with
    one 90-million-character line (a single-file HTML with inlined images) otherwise stalls the secret scan for hours;
    scan such a file's text before splitting it (ADR 0014)."""
    ga = os.path.join(folder, ".gitattributes")
    have = open(ga).read() if os.path.exists(ga) else ""
    if PARTS_RULE not in have.splitlines():
        open(ga, "a").write(("" if not have or have.endswith("\n") else "\n") + PARTS_RULE + "\n")


def cmd_split(files, keep, run):
    rows = []
    for fp in files:
        fp = os.path.abspath(fp)
        n = os.path.getsize(fp)
        count = (n + PART - 1) // PART
        name = os.path.basename(fp)
        parts = [f"{fp}.part-{i:03d}" for i in range(count)]
        if any(os.path.lexists(p) for p in parts + [fp + ".sha256"]):
            raise SystemExit(f"refused: parts or .sha256 for {tilde(fp)} already exist")
        print(f"{tilde(fp)}: {n / 1048576:.0f} MB -> {count} parts{' (original kept)' if keep else ''}")
        if not run:
            continue
        room(fp, n)
        digest = sha256(fp)
        with open(fp, "rb") as src:
            for p in parts:
                with open(p, "wb") as dst:
                    dst.write(src.read(PART))
        joined = hashlib.sha256()
        for p in parts:
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    joined.update(b)
        if joined.hexdigest() != digest:
            for p in parts:
                os.unlink(p)
            raise SystemExit(f"stopped: joined parts of {tilde(fp)} do not hash to the original; parts removed, original kept")
        open(fp + ".sha256", "w").write(f"{digest}  {name}\n")
        rows.append((fp, count, n, digest))
        if not keep:
            os.unlink(fp)
            removed(fp, n, "split for GitHub (one file over 100 MB); the parts are the same bytes",
                    f"{count} parts; sha256 of the joined parts == original {digest}")
    for fp, count, n, digest in rows:  # one SPLIT-FILES.md per folder says how to join them back
        mark_binary(os.path.dirname(fp))
        note = os.path.join(os.path.dirname(fp), "SPLIT-FILES.md")
        if not os.path.exists(note):
            open(note, "w").write(SPLIT_NOTE)
        open(note, "a").write(f"| `{os.path.basename(fp)}` | {count} | {n} | `{digest}` |\n")


def zip_matches_dir(zpath, folder):
    """True when every file in the zip is already in FOLDER (its entries start with FOLDER's name) with the same
    size and CRC-32: the zip is a copy of the folder."""
    base = os.path.dirname(folder)
    with zipfile.ZipFile(zpath) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        for i in infos:
            p = os.path.join(base, i.filename)
            if not i.filename.startswith(os.path.basename(folder) + "/") or not os.path.isfile(p) or os.path.getsize(p) != i.file_size:
                return False, i.filename
            crc = 0
            with open(p, "rb") as f:
                for b in iter(lambda: f.read(1 << 20), b""):
                    crc = zlib.crc32(b, crc)
            if crc != i.CRC:
                return False, i.filename
    return True, len(infos)


def cmd_drop(copy, original, run):
    copy, original = os.path.abspath(copy), os.path.abspath(original)
    if copy.lower().endswith(".zip") and os.path.isdir(original):
        ok, info = zip_matches_dir(copy, original)
        print(f"{tilde(copy)} {'is a copy of' if ok else 'differs from'} {tilde(original)} ({info})")
        if not ok:
            raise SystemExit(1)
        if run:
            n = os.path.getsize(copy)
            os.unlink(copy)
            removed(copy, n, "zip of a folder that is kept", f"all {info} files in the zip are in {tilde(original)} with the same size and CRC-32")
        return
    if copy == original or not os.path.isfile(original):
        raise SystemExit("refused: ORIGINAL must be another existing file")
    a, b = sha256(copy), sha256(original)
    print(f"{tilde(copy)} {'==' if a == b else '!='} {tilde(original)}")
    if a != b:
        raise SystemExit(1)
    if run:
        n = os.path.getsize(copy)
        os.unlink(copy)
        removed(copy, n, "identical copy", f"sha256 {a} == {tilde(original)}, which stays")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["plan", "unzip", "split", "drop"])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--of")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "plan":
        cmd_plan(a.paths)
    elif a.cmd == "unzip":
        cmd_unzip(a.paths, a.run)
    elif a.cmd == "split":
        cmd_split(a.paths, a.keep, a.run)
    elif a.cmd == "drop":
        if not a.of or len(a.paths) != 1:
            ap.error("drop takes one COPY and --of ORIGINAL")
        cmd_drop(a.paths[0], a.of, a.run)


if __name__ == "__main__":
    main()
