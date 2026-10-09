#!/usr/bin/env python3
"""List the loose piles inside repos: files in a repo's folder that the repo does not track (`git ls-files -o`,
ignored files excluded), grouped by folder, so each pile can be read and given a verdict.

  piles.py [--machine laptop] [--min-mb 20] [--min-files 300]   -> machines/<m>/piles.json, and a table on stdout

A pile = the untracked files under one folder, up to 3 levels below the repo root (a single file is its own pile when it is
100 MB or more: GitHub refuses those without LFS). Piles under the thresholds are summed per repo as "small". For each
pile: files, bytes, newest change, top extensions, the largest files, and a few sample names. Nothing is changed.
"""
import json, os, subprocess, sys, time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GH_LIMIT = 100 * 1024 * 1024


def arg(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def piles_of(path):
    r = subprocess.run(["git", "-C", path, "ls-files", "-o", "--exclude-standard", "-z"], capture_output=True, timeout=600)
    if r.returncode:
        return path, None
    groups = defaultdict(lambda: {"files": 0, "bytes": 0, "newest": 0, "ext": Counter(), "big": [], "sample": []})
    for f in filter(None, r.stdout.decode(errors="replace").split("\0")):
        if f.endswith("/"):
            continue  # a nested repo; its own files are counted from inside it
        try:
            st = os.lstat(os.path.join(path, f))
        except OSError:
            continue
        parts = f.split("/")
        key = f if st.st_size >= GH_LIMIT else "/".join(parts[:min(3, len(parts) - 1)]) or "(repo root)"
        g = groups[key]
        g["files"] += 1
        g["bytes"] += st.st_size
        g["newest"] = max(g["newest"], st.st_mtime)
        g["ext"][os.path.splitext(f)[1].lower() or "(none)"] += st.st_size
        if st.st_size >= 20 * 1024 * 1024:
            g["big"].append([f, st.st_size])
        if len(g["sample"]) < 6:
            g["sample"].append(f)
    return path, groups


def main():
    machine = arg("--machine", os.environ.get("ESTATE_MACHINE", "laptop"))
    min_b, min_n = arg("--min-mb", 20) * 1024 * 1024, arg("--min-files", 300)
    census = json.load(open(os.path.join(REPO, "machines", machine, "census.json")))
    inv = json.load(open(os.path.join(REPO, "machines", machine, "repos.json")))["repos"]
    live = [r["path"] for r in inv if r["kind"] == "repo" and r.get("untracked") and r["path"].startswith(WS + "/")
            and not any(x in r["path"] for x in ("/_archive/", "/_data/", "/_reference/", "/node_modules/", "/.t/"))]
    out, small = [], defaultdict(lambda: [0, 0])
    with ThreadPoolExecutor(8) as ex:
        for path, groups in ex.map(piles_of, live):
            for k, g in (groups or {}).items():
                rel = os.path.relpath(path, WS)
                if g["bytes"] >= min_b or g["files"] >= min_n:
                    out.append({"repo": rel, "pile": k, "files": g["files"], "bytes": g["bytes"],
                                "newest": time.strftime("%Y-%m-%d", time.localtime(g["newest"])),
                                "ext": [[e, b] for e, b in g["ext"].most_common(4)],
                                "big": sorted(g["big"], key=lambda x: -x[1])[:5], "sample": g["sample"]})
                else:
                    small[rel][0] += g["files"]
                    small[rel][1] += g["bytes"]
    out.sort(key=lambda p: -p["bytes"])
    doc = {"machine": machine, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "census_at": census["at"], "piles": out,
           "small": {k: {"files": v[0], "bytes": v[1]} for k, v in sorted(small.items(), key=lambda kv: -kv[1][1])}}
    dst = os.path.join(REPO, "machines", machine, "piles.json")
    json.dump(doc, open(dst, "w"), indent=1)
    open(dst, "a").write("\n")
    tot = sum(p["bytes"] for p in out)
    print(f"{len(out)} piles, {tot / 1024 ** 3:.2f} GB; {sum(v[1] for v in small.values()) / 1024 ** 3:.2f} GB in small ones -> {dst.replace(HOME, '~')}")
    for p in out:
        print(f"{p['bytes'] / 1024 ** 2:8.0f} MB {p['files']:6} {p['newest']}  {p['repo']} :: {p['pile']}  "
              + ",".join(e for e, _ in p["ext"][:3]))


if __name__ == "__main__":
    main()
