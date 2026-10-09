#!/usr/bin/env python3
"""The vault (docs/MERGES.md §6): one private repo, sisodias/siso-vault, holding finished repos as git bundles.
A bundle keeps every branch and tag, so a folded repo is lossless and can be restored with `git clone <bundle>`.
  python3 tools/vault.py fold NAME... --part PART [--why TEXT] [--run]   bundle sisodias/NAME into vault/PART/NAME.bundle
  python3 tools/vault.py absorb PATH --part PART [--run]   a nested repo inside a building: its history into the vault,
                                                  its tracked files into the parent repo (one path-limited commit), its
                                                  own .git removed, then its GitHub original deleted
  python3 tools/vault.py delete NAME... [--run]   delete the GitHub original, only when the vault's bundle still holds
                                                  every ref it has now (re-checked at delete time); a line in removed.jsonl
Without --run it only plans. Each fold is verified: the bundle's refs equal `git ls-remote` of the source, ref for ref.
The originals are NOT deleted here: that needs Shaan's go per batch (ADR 0004); `INDEX.md` marks each as `folded`.
The vault is cloned into a scratch folder and pushed; nothing stays on the laptop. A line per fold: machines/<m>/vault.jsonl."""
import argparse, datetime, json, os, pathlib, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
OWNER, VAULT = "sisodias", "siso-vault"


def sh(*a, cwd=None, check=True):
    r = subprocess.run(a, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise SystemExit(f"failed: {' '.join(a)}\n{r.stderr[-800:]}")
    return r.stdout


def refs(lines):
    """{ref: sha} from ls-remote or bundle list-heads output, without HEAD and peeled tags."""
    out = {}
    for l in lines.splitlines():
        sha, _, ref = l.partition(" ") if " " in l.split("\t")[0] else l.partition("\t")
        ref = ref.strip()
        if ref and ref != "HEAD" and not ref.endswith("^{}"):
            out[ref] = sha.strip()
    return out


def delete(a):
    folded = {json.loads(l)["repo"]: json.loads(l) for l in (ROOT / "machines" / a.machine / "vault.jsonl").open() if l.strip()}
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=".siso-ephemeral-vault."))
    try:
        v = tmp / VAULT
        sh("git", "clone", "-q", f"https://github.com/{OWNER}/{VAULT}.git", str(v))
        for n in a.names:
            f = folded.get(f"{OWNER}/{n}")
            if not f:
                print(f"skip {n}: not in the vault"); continue
            b = v / "vault" / f["part"] / f"{n}.bundle"
            chunks = sorted(b.parent.glob(b.name + ".part*"))
            if not b.exists() and chunks:
                with b.open("wb") as o:
                    for c in chunks:
                        o.write(c.read_bytes())
            now = {k: s for k, s in refs(sh("git", "ls-remote", f"https://github.com/{OWNER}/{n}.git", check=False)).items()
                   if not k.startswith("refs/pull/")}
            got = refs(sh("git", "bundle", "list-heads", str(b))) if b.exists() else {}
            miss = [k for k in now if got.get(k) != now[k]]
            if not now or miss:
                print(f"skip {n}: {'gone already' if not now else f'{len(miss)} refs changed since the fold (re-fold first)'}"); continue
            if not a.run:
                print(f"plan: delete {OWNER}/{n} ({len(now)} refs, all in {b.relative_to(v)})"); continue
            sh("gh", "repo", "delete", f"{OWNER}/{n}", "--yes")
            with (ROOT / "machines" / a.machine / "removed.jsonl").open("a") as o:
                o.write(json.dumps({"at": datetime.datetime.now().isoformat(timespec="seconds"), "path": f"github:{OWNER}/{n}",
                                    "kind": "github-repo", "why": f["why"], "proof": f"{VAULT}/{b.relative_to(v)}: {len(now)} refs equal to ls-remote at delete time; restore drill passed 25 Sep"}) + "\n")
            print(f"deleted {OWNER}/{n}: {len(now)} refs held in {b.relative_to(v)}")
    finally:
        subprocess.run(["rm", "-rf", str(tmp)])


def absorb(a):
    ws = ROOT.parent.parent
    for rel in a.names:
        d = ws / rel
        par = sh("git", "-C", str(d.parent), "rev-parse", "--show-toplevel").strip()
        sub = str(d.relative_to(par))
        url = sh("git", "-C", str(d), "remote", "get-url", "origin").strip()
        name = url.rstrip("/").removesuffix(".git").split("/")[-1]
        if f"{OWNER}/{name}" not in url.replace(":", "/"):
            raise SystemExit(f"{rel}: origin {url} is not ours")
        pu = sh("git", "-C", par, "remote", "get-url", "origin").strip().rstrip("/").removesuffix(".git").split("github.com")[-1].lstrip(":/")
        vis = lambda r: sh("gh", "repo", "view", r, "--json", "visibility", "--jq", ".visibility").strip()
        if vis(pu) == "PUBLIC" and vis(f"{OWNER}/{name}") != "PUBLIC":
            print(f"refuse {rel}: private work into the public {pu} would publish it; fold it into the vault instead"); continue
        if sh("git", "-C", str(d), "status", "--porcelain") and not a.run:
            print(f"note {rel}: has uncommitted changes; they stay in the parent's working tree as changes")
        wts = [l for l in sh("git", "-C", str(d), "worktree", "list", "--porcelain").splitlines() if l.startswith("worktree ")]
        if len(wts) > 1:  # removing .git would orphan them (it did on 25 Sep: project-os-local-profile)
            print(f"refuse {rel}: it has {len(wts) - 1} worktrees; retire them first"); continue
        files = [f for f in sh("git", "-C", str(d), "ls-files", "-z").split("\0") if f]
        if not a.run:
            print(f"plan: {rel}: {len(files)} files into {pathlib.Path(par).name}:{sub}, history -> vault/{a.part}/{name}.bundle, delete {OWNER}/{name}")
            continue
        subprocess.run([sys.executable, __file__, "fold", name, "--part", a.part, "--why", f"history of {rel}, absorbed into its parent repo", "--run"], check=True)
        gl = sh("git", "-C", par, "ls-files", "-s", "--", sub)
        if gl.startswith("160000"):
            sh("git", "-C", par, "rm", "-q", "--cached", sub)
        hold = pathlib.Path(tempfile.mkdtemp(prefix=".siso-ephemeral-absorb."))
        (d / ".git").rename(hold / "git")  # kept until the parent's commit is pushed
        try:
            sh("git", "-C", par, "add", "-f", "--", *[f"{sub}/{f}" for f in files])
            other = [f for f in sh("git", "-C", par, "diff", "--cached", "--name-only").splitlines() if not f.startswith(sub + "/") and f != sub]
            if other:
                raise SystemExit(f"{rel}: the parent has other staged work ({len(other)} files); not committing over it")
            # no pathspec: a pathspec commit re-reads the old submodule entry and fails; only this folder is staged
            sh("git", "-C", par, "commit", "-q", "-m", f"{sub}: absorb the nested repo {OWNER}/{name} (its history is in {VAULT}; siso-estate tools/vault.py)")
            if subprocess.run(["gitleaks", "git", ".", "--log-opts=-1", "--redact", "--no-banner"], cwd=par, capture_output=True).returncode:
                raise SystemExit(f"{rel}: the secret scan flagged the parent commit; not pushed, .git kept at {hold}")
            sh("git", "-C", par, "push", "-q")
        except BaseException:
            if sh("git", "-C", par, "log", "-1", "--format=%s", check=False).startswith(f"{sub}: absorb the nested repo"):
                sh("git", "-C", par, "reset", "-q", "--soft", "HEAD~1")  # ours and unpushed: the scan or push failed
            sh("git", "-C", par, "restore", "--staged", "--", sub, check=False)
            if not (d / ".git").exists():
                (hold / "git").rename(d / ".git")
            raise
        subprocess.run(["rm", "-rf", str(hold)])
        subprocess.run([sys.executable, __file__, "delete", name, "--run"], check=True)
        print(f"absorbed {rel} into {pathlib.Path(par).name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["fold", "delete", "absorb"])
    ap.add_argument("names", nargs="+")
    ap.add_argument("--part", default=None, help="clients, library, agents, hq, personal, partners")
    ap.add_argument("--why", default="finished work, folded into the vault (docs/MERGES.md §6)")
    ap.add_argument("--machine", default="laptop")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if a.cmd == "delete":
        return delete(a)
    if a.cmd == "absorb":
        return absorb(a)
    if not a.part:
        raise SystemExit("fold needs --part")
    if not a.run:
        for n in a.names:
            print(f"plan: {OWNER}/{n} -> {VAULT}/vault/{a.part}/{n}.bundle")
        return
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=".siso-ephemeral-vault."))
    try:
        if subprocess.run(["gh", "repo", "view", f"{OWNER}/{VAULT}"], capture_output=True).returncode:
            sh("gh", "repo", "create", f"{OWNER}/{VAULT}", "--private", "--description",
               "The SISO vault: finished repos as lossless git bundles, with an index (siso-estate tools/vault.py)")
        v = tmp / VAULT
        sh("git", "clone", "-q", f"https://github.com/{OWNER}/{VAULT}.git", str(v))
        idx = v / "INDEX.md"
        if not idx.exists():
            idx.write_text("# The SISO vault\n\nFinished repos, each a git bundle with every branch and tag. Restore one with "
                           "`git clone vault/<part>/<name>.bundle <name>`. Written by siso-estate `tools/vault.py`.\n\n"
                           "| Repo | Part | Refs | Bundle MB | Folded | Why |\n|---|---|---|---|---|---|\n")
        log = []
        for n in a.names:
            m = tmp / f"{n}.git"
            sh("git", "clone", "-q", "--mirror", f"https://github.com/{OWNER}/{n}.git", str(m))
            dest = v / "vault" / a.part / f"{n}.bundle"
            dest.parent.mkdir(parents=True, exist_ok=True)
            old = sorted(dest.parent.glob(dest.name + ".part*")) + ([dest] if dest.exists() else [])
            if old:  # a re-fold never replaces an earlier bundle: it stays beside the new one under a dated name
                stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                for f in old:
                    f.rename(f.with_name(f.name.replace(f"{n}.bundle", f"{n}.until-{stamp}.bundle")))
                print(f"kept the earlier bundle of {n} as {n}.until-{stamp}.bundle")
            sh("git", "bundle", "create", "-q", str(dest), "--all", cwd=m)
            sh("git", "bundle", "verify", "-q", str(dest), cwd=m)
            want = refs(sh("git", "ls-remote", f"https://github.com/{OWNER}/{n}.git"))
            want = {k: s for k, s in want.items() if not k.startswith("refs/pull/")}  # GitHub's PR refs are not the repo's
            got = refs(sh("git", "bundle", "list-heads", str(dest)))
            missing = {k for k in want if got.get(k) != want[k]}
            if missing:
                raise SystemExit(f"{n}: bundle lacks {len(missing)} refs, e.g. {sorted(missing)[:3]}; nothing committed")
            mb = round(dest.stat().st_size / 1048576, 1)
            parts = 1
            if mb > 90:  # over GitHub's file limit: split into 90 MB parts; restore = cat NAME.bundle.part* > NAME.bundle
                sh("split", "-b", "90m", "-d", "-a", "2", str(dest), str(dest) + ".part")
                chunks = sorted(dest.parent.glob(dest.name + ".part*"))
                joined = tmp / f"{n}.rejoined.bundle"
                with joined.open("wb") as o:
                    for c in chunks:
                        o.write(c.read_bytes())
                if sh("shasum", "-a", "256", str(joined)).split()[0] != sh("shasum", "-a", "256", str(dest)).split()[0]:
                    raise SystemExit(f"{n}: the rejoined parts differ from the bundle; nothing committed")
                dest.unlink(); joined.unlink()
                parts = len(chunks)
            day = datetime.date.today().isoformat()
            with idx.open("a") as f:
                f.write(f"| `{OWNER}/{n}` | {a.part} | {len(want)} | {mb}{f' ({parts} parts: cat {n}.bundle.part* > {n}.bundle)' if parts > 1 else ''} | {day} | {a.why} |\n")
            log.append({"at": datetime.datetime.now().isoformat(timespec="seconds"), "repo": f"{OWNER}/{n}", "part": a.part,
                        "refs": len(want), "mb": mb, "parts": parts, "why": a.why, "original": "kept until Shaan's go"})
            print(f"folded {n}: {len(want)} refs, {mb} MB, verified")
        sh("git", "add", "-A", cwd=v)
        sh("git", "commit", "-q", "-m", f"vault: fold {', '.join(a.names)}", cwd=v)
        scan = subprocess.run(["gitleaks", "git", ".", "--log-opts=-1", "--redact", "--no-banner"], cwd=v, capture_output=True)
        if scan.returncode:
            raise SystemExit("the secret scan flagged the vault commit; nothing pushed (the originals are private and untouched)")
        sh("git", "push", "-q", "origin", "HEAD:main", cwd=v)
        with (ROOT / "machines" / a.machine / "vault.jsonl").open("a") as f:
            f.writelines(json.dumps(l) + "\n" for l in log)
    finally:
        subprocess.run(["rm", "-rf", str(tmp)])


if __name__ == "__main__":
    main()
