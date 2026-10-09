#!/usr/bin/env python3
"""Apply a move plan (plan/moves/*.json) one move at a time through move.py.

  apply-moves.py plan/moves/wave-a-root.json [--dry-run] [--only SUBSTR]

A move is deferred (not failed) while:
  - a backup run still has checkouts under SRC that it has not reached (machines/<m>/backup.log), or
  - a process has its working directory inside SRC.
Already-applied moves (SRC is a link to DST) are skipped, so the plan can be re-run until done.
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
WS = os.path.expanduser("~/SISO_Workspace")


def pending_backup(src, machine):
    plan_p = os.path.join(REPO, "machines", machine, "backup-plan.json")
    log_p = os.path.join(REPO, "machines", machine, "backup.log")
    running = subprocess.run(["pgrep", "-f", "tools/backup.py run"], capture_output=True).returncode == 0
    if not running or not os.path.exists(plan_p):
        return 0
    done = set()
    if os.path.exists(log_p):
        for line in open(log_p):
            try:
                done.add(json.loads(line)["path"])
            except Exception:
                pass
    items = json.load(open(plan_p))["items"]
    return sum(1 for i in items if (i["path"] == src or i["path"].startswith(src + "/")) and i["path"] not in done)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    plan = json.load(open(a.plan if os.path.isabs(a.plan) else os.path.join(REPO, a.plan)))
    base = plan.get("base", WS)
    tally = {"moved": 0, "done-before": 0, "deferred": 0, "failed": 0}
    for mv in plan["moves"]:
        src_rel, dst_rel, why = mv[:3]
        link = mv[3] if len(mv) > 3 else "link"
        if a.only and a.only not in src_rel:
            continue
        if link == "done":
            tally["done-before"] += 1
            continue
        src, dst = os.path.join(base, src_rel), os.path.join(base, dst_rel)
        if os.path.islink(src) and os.path.realpath(src) == os.path.realpath(dst):
            tally["done-before"] += 1
            continue
        if not os.path.lexists(src) and os.path.exists(dst):
            tally["done-before"] += 1
            continue
        n = pending_backup(src, a.machine)
        if n:
            print(f"DEFER  {src_rel}: backup still has {n} checkouts to reach under it")
            tally["deferred"] += 1
            continue
        cmd = [sys.executable, os.path.join(HERE, "move.py"), src, dst, "--why", why, "--machine", a.machine]
        if link == "nolink":
            cmd.append("--no-link")
        else:
            # a process inside keeps its directory (the inode moves with it) and the compat link
            # keeps its old absolute paths working, so a live server does not block a linked move
            cmd.append("--force-cwd")
        if a.dry_run:
            cmd.append("--dry-run")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            print(f"{'PLAN ' if a.dry_run else 'MOVED'}  {src_rel} -> {dst_rel}")
            tally["moved"] += 1
        elif "processes are working inside" in (r.stderr or ""):
            print(f"DEFER  {src_rel}: {r.stderr.strip().splitlines()[1] if len(r.stderr.strip().splitlines()) > 1 else r.stderr.strip()}")
            tally["deferred"] += 1
        else:
            print(f"FAIL   {src_rel}: {(r.stderr or r.stdout).strip()[-300:]}")
            tally["failed"] += 1
    print(json.dumps(tally))


if __name__ == "__main__":
    main()
