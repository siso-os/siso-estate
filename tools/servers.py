#!/usr/bin/env python3
"""What runs on each server, read from the laptop over ssh (goal A15): checkouts, services, containers, cron.

  servers.py scan [MACHINE...]    read-only scan -> machines/<m>/servers.json (default: every machine in
                                  plan/machines.json with an "ssh" alias)
  servers.py show [MACHINE...]    one line per checkout and service, with its kind

A client box (halo-vps, bykonz-contabo) is recorded, never mapped: nothing is written there and the private map
never goes on it. Each checkout gets a kind:
  ours          remote under sisodias (its place on the map is `estate where <name>`)
  client        a client's own repo (camronkellman/..., never copied: ADR 0007)
  third-party   someone else's upstream
  release       a deploy copy made from a bundle or a release (no live remote)
  no-remote     a lane or worktree git could not name
  temp          under /tmp: a session's scratch, not a place
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OURS = ("sisodias/", "Lordsisodia/", "lordsisodia/")
CLIENTS = ("camronkellman/",)

SCAN = r'''
G="git -c safe.directory=*"
echo "## host"; hostname; nproc; df -Pk / | tail -1 | awk '{print $2, $3}'
echo "## git"
find / -xdev \( -path /proc -o -path /sys -o -name node_modules -o -name .cache -o -name bundle \) -prune -o -name .git -print 2>/dev/null |
while read g; do d=$(dirname "$g"); printf '%s\t%s\t%s\t%s\n' "$d" "$($G -C "$d" remote get-url origin 2>/dev/null)" "$($G -C "$d" rev-parse --short HEAD 2>/dev/null)" "$($G -C "$d" status --porcelain 2>/dev/null | wc -l)"; done
echo "## services"
for u in $(systemctl list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print $1}'); do
  f=$(systemctl show -p FragmentPath --value "$u"); case "$f" in /etc/systemd/system/*)
  printf '%s\t%s\t%s\tsystem\n' "$u" "$(systemctl show -p ExecStart --value "$u" | grep -o 'path=[^ ;]*' | head -1 | cut -d= -f2)" "$(systemctl show -p WorkingDirectory --value "$u")";; esac; done
for u in $(systemctl --user list-units --type=service --state=running --no-legend --plain 2>/dev/null | awk '{print $1}'); do
  f=$(systemctl --user show -p FragmentPath --value "$u"); case "$f" in /usr/*) ;; *)
  printf '%s\t%s\t%s\tuser\n' "$u" "$(systemctl --user show -p ExecStart --value "$u" | grep -o 'path=[^ ;]*' | head -1 | cut -d= -f2)" "$(systemctl --user show -p WorkingDirectory --value "$u")";; esac; done
echo "## failed"   # SISO's own units and the web server, not boot noise (cloud-init, network-wait)
for u in $(systemctl list-units --type=service --state=failed --no-legend --plain 2>/dev/null | awk '{print $1}'); do
  f=$(systemctl show -p FragmentPath --value "$u"); case "$u:$f" in *:/etc/systemd/system/*|caddy*|nginx*|docker*) printf '%s\tsystem\n' "$u";; esac; done
for u in $(systemctl --user list-units --type=service --state=failed --no-legend --plain 2>/dev/null | awk '{print $1}'); do
  f=$(systemctl --user show -p FragmentPath --value "$u"); case "$f" in /usr/*) ;; *) printf '%s\tuser\n' "$u";; esac; done
echo "## docker"; docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null
echo "## pm2"; command -v pm2 >/dev/null && pm2 jlist 2>/dev/null | python3 -c "import json,sys;[print(p['name'],p['pm2_env'].get('pm_cwd',''),p['pm2_env']['status'],sep='\t') for p in json.load(sys.stdin)]" 2>/dev/null
echo "## cron"; crontab -l 2>/dev/null | grep -v '^#' | grep -v '^$'
echo "## users"; getent passwd | awk -F: '$3>=1000 && $3<65534 && $7 !~ /nologin|false/ {print $1}'
'''


def machines():
    return json.load(open(os.path.join(REPO, "plan", "machines.json")))["machines"]


def kind(path, remote):
    if path.startswith("/tmp/"):
        return "temp"
    r = remote.replace("https://github.com/", "").replace("git@github.com:", "")
    if not remote:
        return "no-remote"
    if r.startswith(OURS):
        return "ours"
    if r.startswith(CLIENTS):
        return "client"
    if remote.startswith("/") or remote.endswith(".bundle"):
        return "release"
    return "third-party"


def parse(out):
    sec, res = None, {"host": [], "git": [], "services": [], "failed": [], "docker": [], "pm2": [], "cron": [], "users": []}
    for line in out.splitlines():
        if line.startswith("## "):
            sec = line[3:].strip()
            continue
        if sec and line.strip():
            res[sec].append(line)
    host = res["host"] + ["", "", ""]
    disk = host[2].split() if host[2] else ["0", "0"]
    checkouts = []
    for l in res["git"]:
        p = (l.split("\t") + ["", "", "", "0"])[:4]
        checkouts.append({"path": p[0], "remote": p[1], "head": p[2], "dirty": int(p[3] or 0), "kind": kind(p[0], p[1])})
    services = [dict(zip(("unit", "program", "cwd", "scope"), (l.split("\t") + [""] * 4)[:4]), health="running")
                for l in res["services"]]
    services += [{"unit": u, "program": "", "cwd": "", "scope": sc, "health": "failed"}
                 for u, sc in ((l.split("\t") + [""])[:2] for l in res["failed"])]
    docker = [dict(zip(("name", "image", "status"), (l.split("\t") + [""] * 3)[:3])) for l in res["docker"]]
    for c in docker:                                   # Up ... (healthy) / (unhealthy) / Exited (n) / Restarting
        st = c["status"]
        c["health"] = ("failed" if st.startswith(("Exited", "Dead")) or "unhealthy" in st or st.startswith("Restarting")
                       else "running")
    pm2 = [dict(zip(("name", "cwd", "status"), (l.split("\t") + [""] * 3)[:3])) for l in res["pm2"]]
    return {"hostname": host[0], "cpus": host[1], "disk_kb": int(disk[0]), "disk_used_kb": int(disk[1]),
            "checkouts": sorted(checkouts, key=lambda c: c["path"]), "services": services, "containers": docker,
            "pm2": pm2, "cron": res["cron"], "login_users": res["users"]}


def scan(name, m):
    alias = m.get("ssh")
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, "bash -s"], input=SCAN,
                           capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return {"machine": name, "status": "unreachable", "error": "timeout", "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if r.returncode != 0 and "## host" not in r.stdout:
        return {"machine": name, "status": "unreachable", "error": r.stderr.strip()[-200:],
                "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    rec = parse(r.stdout)
    rec.update(machine=name, ssh=alias, status="scanned", at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               mapped=m.get("status", "").startswith("on the map"))
    counts = {}
    for c in rec["checkouts"]:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    rec["summary"] = {"checkouts": counts, "services": len(rec["services"]), "containers": len(rec["containers"]),
                      "failed": sum(x.get("health") == "failed" for x in rec["services"] + rec["containers"]),
                      "pm2": len(rec["pm2"]), "cron": len(rec["cron"])}
    return rec


def write(name, rec):
    d = os.path.join(REPO, "machines", name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "servers.json")
    if rec["status"] != "scanned" and os.path.exists(p):   # keep the last good scan; note the miss
        old = json.load(open(p))
        old["last_attempt"] = {"at": rec["at"], "status": rec["status"], "error": rec.get("error", "")}
        rec = old
    with open(p + ".tmp", "w") as f:
        json.dump(rec, f, indent=1)
        f.write("\n")
    os.replace(p + ".tmp", p)


def targets(names):
    ms = machines()
    if names:
        missing = [n for n in names if n not in ms or not ms[n].get("ssh")]
        if missing:
            raise SystemExit(f"no ssh alias in plan/machines.json for: {', '.join(missing)}")
        return {n: ms[n] for n in names}
    return {n: m for n, m in ms.items() if m.get("ssh")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["scan", "show"])
    ap.add_argument("machines", nargs="*")
    a = ap.parse_args()
    bad = 0
    for name, m in targets(a.machines).items():
        if a.cmd == "scan":
            rec = scan(name, m)
            write(name, rec)
            s = rec.get("summary", {})
            print(f"{name}: {rec['status']}" + (f" {s}" if s else f" ({rec.get('error', '')})"))
            bad += rec["status"] != "scanned"
        else:
            p = os.path.join(REPO, "machines", name, "servers.json")
            if not os.path.exists(p):
                print(f"{name}: never scanned")
                continue
            rec = json.load(open(p))
            print(f"== {name} ({rec.get('at')}): {rec.get('summary')}")
            for c in rec.get("checkouts", []):
                print(f"  {c['kind']:<11} {c['path']}  {c['remote']}  {c['head']}" + (f"  dirty {c['dirty']}" if c["dirty"] else ""))
            for s in rec.get("services", []):
                print(f"  service     {s['unit']}  {s['cwd'] or s['program']}")
            for s in rec.get("containers", []) + rec.get("pm2", []):
                print(f"  running     {s['name']}  {s.get('image') or s.get('cwd')}  {s['status']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
