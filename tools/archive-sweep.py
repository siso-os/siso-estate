#!/usr/bin/env python3
"""The archive law (storage-archive-offload): _archive/ is a waiting room, not a home. Every folder that may leave
(archive-offload.py plan: not HALO, not private) and has sat idle for --idle-hours goes to its own private GitHub repo,
encrypted and verified from a fresh clone, then is released; Claude and Codex transcripts older than 14 days are
packed into _archive first (archive-offload.py chats). `estate pull` / `archive-offload.py restore` brings any back.

Shaan, 16 Oct: "clean it once, make sure it's clean forever ... push what needs to be pushed to GitHub and understand
on the estate how it gets pulled back." Laptop only; one folder at a time; receipts in machines/laptop/archive-offload.jsonl.
"""
import argparse, os, socket, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "archive-offload.py")
ARCHIVE = os.path.expanduser("~/SISO_Workspace/_archive")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--idle-hours", type=float, default=48)
    ap.add_argument("--max", type=int, default=3, help="folders per run")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    machine = os.environ.get("ESTATE_MACHINE") or socket.gethostname().lower()
    os.environ["ESTATE_MACHINE"] = "laptop" if machine.startswith("shaans-macbook") else machine
    go = ["--run"] if a.run else []
    out = subprocess.run([sys.executable, TOOL, "chats", "--days", "14", *go], capture_output=True, text=True, timeout=3600)
    print((out.stdout + out.stderr).strip().splitlines()[-1:] and (out.stdout + out.stderr).strip().splitlines()[-1] or "chats: nothing")
    plan = subprocess.run([sys.executable, TOOL, "plan"], capture_output=True, text=True, timeout=1800).stdout
    now, picks = time.time(), []
    for line in plan.splitlines():
        if not line.startswith("may go"):
            continue
        f = line.split()[-1]
        p = os.path.join(ARCHIVE, f)
        try:
            age_h = (now - os.lstat(p).st_mtime) / 3600
        except OSError:
            continue
        if age_h >= a.idle_hours:
            picks.append(f)
    picks = picks[:a.max]
    if not picks:
        print("archive-sweep: no folder may go yet"); return 0
    print(f"archive-sweep: {'offloading' if a.run else 'would offload'} {len(picks)}: {' '.join(picks)}")
    if a.run:
        r = subprocess.run([sys.executable, TOOL, "run", *picks, "--run"], capture_output=True, text=True, timeout=7200)
        print("\n".join((r.stdout + r.stderr).strip().splitlines()[-len(picks) - 1:]))
        return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
