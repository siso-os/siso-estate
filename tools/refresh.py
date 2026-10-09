#!/usr/bin/env python3
"""Refresh every map of the estate from the disk, so no map is ever hand-kept (Shaan, 24 Sep: "make it in a way where
they kind of auto-update").

  refresh.py [--light] [--machine laptop]

Full (nightly, or after big moves): inventory -> hidden folders (dots.py) -> district doors (doors.py --commit) ->
the laptop map (laptop-map.py) -> the umbrella map (umbrella.py build) -> census. --light (hourly): doors and the laptop
map only, from the last full run's records. Each step's failure is printed and the rest still run.
"""
import argparse, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def run(name, *cmd):
    t = time.time()
    r = subprocess.run([sys.executable, os.path.join(HERE, cmd[0]), *cmd[1:]], capture_output=True, text=True)
    out = (r.stdout.strip().splitlines() or [""])[-1][:160]
    print(f"{'ok ' if r.returncode == 0 else 'FAIL'} {name:12} {time.time() - t:5.1f}s  {out}")
    if r.returncode != 0 and r.stderr:
        print("     " + r.stderr.strip().splitlines()[-1][:200])
    return r.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--light", action="store_true")
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    a = ap.parse_args()
    m = ["--machine", a.machine]
    bad = 0
    if not a.light:
        bad += run("inventory", "inventory.py", *m) != 0
        bad += run("dots", "dots.py", *m) != 0
    bad += run("doors", "doors.py", "--commit") != 0
    bad += run("laptop map", "laptop-map.py", *m) != 0
    if a.machine == "laptop":                     # W2: every server's services and health, at most an hour old
        bad += run("servers", "servers.py", "scan") != 0
    if a.machine == "laptop":                     # P8: the law runs every hour, so a routine mess waits an hour at most
        bad += run("building code", "code.py", *m) != 0
        bad += run("law", "fix.py", "--run", *m) != 0
        bad += run("link repair", "links.py", "--repair") != 0
        run("law stats", "fix.py", "--stats", *m)
        # the Library in Agent Base reads these two: Built (the register) and Live (every deployed page), an hour old at most
        bad += run("register", "register.py") != 0
        bad += run("live pages", "surfaces.py") != 0
    if not a.light:
        bad += run("umbrella", "umbrella.py", "build", *m) != 0
        bad += run("census", "census.py", *m) != 0
        bad += run("building code", "code.py", *m) != 0
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
