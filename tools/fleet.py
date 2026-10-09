#!/usr/bin/env python3
"""The fleet: every machine on the map, probed for real, with what it is for (Shaan, 1 Oct: "certain VPSs, we don't
know how to be allocating them, how to be using them for extra compute").

  fleet.py [probe] [--json] [--all]   exec-probe every machine now: free cores, RAM, disk, what runs there, what it is
                                      for -> machines/fleet.json. --all also probes on-demand boxes (Cam's Mac).
  fleet.py pick KIND [--n N] [--json] the box for a job kind (headless-agent, image-run, build, streaming-service,
                                      qwen-pool, interactive) and why; probes again when the record is over 5 min old
  fleet.py pull MACHINE JOB [--from REMOTE_DIR] [--dry-run]
                                      bring a remote job's results back to ~/SISO_Workspace/_data/fleet/<machine>/<job>/
                                      (rsync). A job on a box writes to <results>/<job>/ (plan/machines.json "results",
                                      ~/fleet-out by default); --from takes any other folder there
  fleet.py watch                      probe, and post a console card to Shaan when a machine stops answering (once per
                                      6 h while it stays down) and when it comes back (launchd com.siso.estate-fleet)

The policy is plan/machines.json: each machine's "fleet" block ("probe": the ssh target, "for" / "fallback" / "never":
job kinds, "client": true for a box a client holds) and "job_kinds" (what one job needs). A probe runs one read-only
`sh` over ssh with strict host keys, so a box whose key changed (an IP that went to a stranger) is refused, not trusted.
"Exec" means a command really ran: on 26 Sep - 1 Oct the mini took our key but refused every command, and a TCP check
called it up for five days.
"""
import argparse, concurrent.futures, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PLAN = os.path.join(REPO, "plan", "machines.json")
RECORD = os.path.join(REPO, "machines", "fleet.json")
WS = os.path.expanduser("~/SISO_Workspace")
RESULTS = os.path.join(WS, "_data", "fleet")
REALERT = 6 * 3600
FRESH = 300
FILLING = 0.8

PROBE = r'''
echo "## ok"
OS=$(uname -s); echo "os=$OS"; echo "host=$(hostname)"
if [ "$OS" = Darwin ]; then
  echo "cpus=$(sysctl -n hw.ncpu)"; echo "load=$(sysctl -n vm.loadavg | tr -d '{}' | awk '{print $1,$2,$3}')"
  vm_stat | awk -v ps="$(sysctl -n hw.pagesize)" '/Pages free/{f=$3}/Pages inactive/{i=$3}/Pages speculative/{s=$3}
    END{print "mem_avail_mb=" int((f+i+s)*ps/1048576)}'
  echo "mem_total_mb=$(( $(sysctl -n hw.memsize) / 1048576 ))"
  df -Pk /System/Volumes/Data | tail -1 | awk '{print "disk_total_gb=" int($2/1048576); print "disk_free_gb=" int($4/1048576)}'
  echo "boot=$(sysctl -n kern.boottime | sed 's/^{ sec = \([0-9]*\).*/\1/')"
else
  echo "cpus=$(nproc)"; echo "load=$(cut -d' ' -f1-3 /proc/loadavg)"
  free -m | awk '/^Mem:/{print "mem_total_mb=" $2; print "mem_avail_mb=" $7}'
  df -Pk / | tail -1 | awk '{print "disk_total_gb=" int($2/1048576); print "disk_free_gb=" int($4/1048576)}'
  echo "boot=$(date -d "$(uptime -s)" +%s 2>/dev/null)"
fi
command -v docker >/dev/null 2>&1 && echo "containers=$(docker ps -q 2>/dev/null | wc -l | tr -d ' ')"
U=$(id -un); echo "user=$U"
echo "procs_total=$(ps -A -o pid= | wc -l | tr -d ' ')"; echo "procs_user=$(ps -U "$U" -o pid= | wc -l | tr -d ' ')"
if [ "$OS" = Darwin ]; then echo "procs_limit=$(sysctl -n kern.maxprocperuid)"; echo "procs_max=$(sysctl -n kern.maxproc)"
else echo "procs_limit=$(ulimit -u)"; echo "procs_max=$(cat /proc/sys/kernel/pid_max)"; fi
echo "## user_top"
# name <- parent name: tells an orphan left by a closed ssh session (parent launchd/systemd) from a server's own children
ps -A -o pid=,ppid=,user=,comm= | awk -v u="$U" '{c=$4; for (i=5; i<=NF; i++) c=c" "$i; n=split(c, a, "/"); name[$1]=a[n];
  pp[$1]=$2; us[$1]=$3} END {for (p in name) if (us[p]==u && pp[p]!=2 && p!=2) print name[p] " <- " (pp[p] in name ? name[pp[p]] : "?")}' |
  sort | uniq -c | sort -rn | head -6
echo "## procs"
ps -Ao pcpu=,rss=,comm= | sort -rn | head -8
echo "## agents"
ps -Ao comm= | sed 's|.*/||' | sort | uniq -c | awk '$2 ~ /^(codex|claude|omp|herdr|ollama|llama-server|vllm)$/ {print $2 "=" $1}'
true
'''


def plan():
    return json.load(open(PLAN))


def load(p, default):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return default


def why_failed(err, rc):
    e = err.lower()
    if "exec request failed" in e or "subsystem request failed" in e:
        return "exec-refused", "takes our key but refuses to run any command"
    if "host key verification failed" in e or "identification has changed" in e or "host key is known" in e:
        return "host-key", "host key not the one we know (a changed machine, or a stranger on the IP): not connecting"
    if "permission denied" in e:
        return "key-refused", "refuses our ssh key"
    if any(s in e for s in ("timed out", "no route", "connection refused", "could not resolve", "network is unreachable")):
        return "unreachable", err.strip().splitlines()[-1][:120] if err.strip() else "no answer"
    return "error", (err.strip().splitlines() or [f"exit {rc}"])[-1][:120]


def parse(out):
    sec, kv, procs, agents, user_top = None, {}, [], {}, []
    for line in out.splitlines():
        if line.startswith("## "):
            sec = line[3:].strip()
            continue
        if sec == "ok" and "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
        elif sec == "procs" and line.strip():
            p = line.split(None, 2)
            if len(p) == 3 and p[2] not in ("ps", "sort", "head", "sh"):
                procs.append({"cpu": float(p[0]), "rss_mb": int(p[1]) // 1024, "name": os.path.basename(p[2])})
        elif sec == "agents" and "=" in line:
            k, v = line.split("=", 1)
            agents[k] = int(v)
        elif sec == "user_top" and line.strip():
            n, _, name = line.strip().partition(" ")
            if n.isdigit() and name.split(" <- ")[0] not in ("ps", "sort", "awk", "uniq", "head", "wc"):
                user_top.append([name.strip(), int(n)])
    num = lambda k, f=int: f(kv[k]) if kv.get(k, "").replace(".", "", 1).isdigit() else None
    load = [float(x) for x in kv.get("load", "").split()[:3]] or [None]
    cpus = num("cpus")
    return {"os": kv.get("os"), "hostname": kv.get("host"), "cpus": cpus, "load": load,
            "free_cores": round(max(0.0, cpus - load[0]), 1) if cpus and load[0] is not None else None,
            "mem_total_gb": round(num("mem_total_mb") / 1024, 1) if num("mem_total_mb") else None,
            "mem_avail_gb": round(num("mem_avail_mb") / 1024, 1) if num("mem_avail_mb") is not None else None,
            "disk_free_gb": num("disk_free_gb"), "disk_total_gb": num("disk_total_gb"),
            "up_days": round((time.time() - num("boot")) / 86400, 1) if num("boot") else None,
            "containers": num("containers"), "agents": agents, "top": procs[:4],
            # the mini died of this (26 Sep): an account at its process limit can run nothing new, not even ssh
            "procs": {"user": kv.get("user"), "user_count": num("procs_user"), "user_limit": num("procs_limit"),
                      "total": num("procs_total"), "max": num("procs_max"), "user_top": user_top[:5]}}


def probe_one(name, m):
    f = m.get("fleet", {})
    target = f.get("probe")
    at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if not target:
        return {"machine": name, "state": "not-probed", "detail": f.get("never_connect") or m.get("status", ""), "at": at}
    if target == "local":
        cmd = ["sh", "-s"]
    else:
        cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
               "-o", "RemoteCommand=none", "-o", "RequestTTY=no", "-o", "ControlMaster=no", "-o", "ControlPath=none",
               target, "sh -s"]
    t0 = time.time()
    try:
        r = subprocess.run(cmd, input=PROBE, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return {"machine": name, "state": "timeout", "detail": "no answer in 30 s", "at": at}
    if "## ok" not in r.stdout:
        state, detail = why_failed(r.stderr, r.returncode)
        return {"machine": name, "state": state, "detail": detail, "at": at}
    rec = parse(r.stdout)
    rec.update(machine=name, state="ok", detail=f"exec ok in {time.time() - t0:.1f} s", at=at)
    srv = load(os.path.join(REPO, "machines", name, "servers.json"), {})
    if srv.get("summary"):                        # services from the nightly scan (tools/servers.py), not re-scanned
        rec["services"] = {"count": srv["summary"].get("services", 0), "failed": srv["summary"].get("failed", 0),
                           "scanned": srv.get("at")}
    return rec


def probe_all(include_on_demand=False):
    ms = plan()["machines"]
    todo = {n: m for n, m in ms.items() if include_on_demand or not m.get("fleet", {}).get("on_demand")}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        res = dict(zip(todo, ex.map(lambda nm: probe_one(*nm), todo.items())))
    old = load(RECORD, {}).get("machines", {})
    for n, r in res.items():                      # carry the alert state across probes
        o = old.get(n, {})
        hist = o.get("history", [])               # 24 h of process counts: the climb before a box stops answering
        if r["state"] == "ok" and r.get("procs", {}).get("user_count") is not None:
            pr = r["procs"]
            hist = (hist + [[r["at"][:16], pr["user_count"], pr["total"], pr["user_top"][:3]]])[-96:]
        r["history"] = hist
        for k in ("procs_alerted_at", "cleaned_at", "need_at", "mem_low", "disk_alerted_at", "mem_alerted_at"):
            r[k] = o.get(k)
        if r["state"] == "ok":
            r["last_ok"] = r["at"]
            r["down_since"] = None
            r["alerted_at"] = None
            r["was_down"] = o.get("down_since")
        elif r["state"] != "not-probed":
            r["last_ok"] = o.get("last_ok")
            r["down_since"] = o.get("down_since") or r["at"]
            r["alerted_at"] = o.get("alerted_at")
    for n, o in old.items():                      # an on-demand box not probed this time keeps its last record
        if n not in res and n in ms:
            res[n] = o
    rec = {"_what": "generated by tools/fleet.py (estate fleet): the last exec probe of every machine",
           "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "machines": res}
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    with open(RECORD + ".tmp", "w") as f:
        json.dump(rec, f, indent=1)
        f.write("\n")
    os.replace(RECORD + ".tmp", RECORD)
    return rec


def when(stamp):
    """'26 Sep 05:30 (5.5 days)' from a record's timestamp."""
    t = time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))
    d = (time.time() - t) / 86400
    return time.strftime("%-d %b %H:%M", time.localtime(t)) + (f" ({d:.1f} days)" if d >= 1 else f" ({d * 24:.0f} h)")


def gb(x):
    return "?" if x is None else f"{x:g}"


def show(rec):
    ms = plan()["machines"]
    for n, m in ms.items():
        r = rec["machines"].get(n, {"state": "never probed"})
        f = m.get("fleet", {})
        if r.get("state") == "ok":
            ag = " ".join(f"{k} {v}" for k, v in sorted(r.get("agents", {}).items())) or "no agents"
            top = ", ".join(f"{p['name']} {p['cpu']:.0f}%" for p in r.get("top", [])[:3])
            sv = r.get("services")
            pr = r.get("procs") or {}
            print(f"{n:<15} OK   {gb(r['free_cores'])} of {r['cpus']} cores free (load {r['load'][0]:g}) | "
                  f"RAM {gb(r['mem_avail_gb'])} of {gb(r['mem_total_gb'])} GB free | disk {gb(r['disk_free_gb'])} GB free"
                  f" | up {gb(r.get('up_days'))} d")
            print(f"{'':<15}      runs: {ag}" + (f", {r['containers']} containers" if r.get("containers") else "") +
                  (f", {sv['count']} services ({sv['failed']} failed)" if sv else "") + (f"; busiest: {top}" if top else ""))
            if pr.get("user_count") is not None:
                most = ", ".join(f"{a} {b}" for a, b in pr.get("user_top", [])[:3])
                lim = f" of {pr['user_limit']} allowed" if str(pr.get("user_limit", "")).isdigit() else ""
                print(f"{'':<15}      processes: {pr['user']} has {pr['user_count']}{lim} ({pr['total']} on the box); most: {most}")
        else:
            since = f" since {when(r['down_since'])}" if r.get("down_since") else ""
            print(f"{n:<15} {r.get('state', '?').upper():<4} {r.get('detail', '')}{since}")
        kinds = [k for k in plan().get("job_kinds", {}) if not k.startswith("_")]
        never = "any SISO job" if set(kinds) <= set(f.get("never", [])) else ", ".join(f.get("never", []))
        print(f"{'':<15}      for: {', '.join(f.get('for', [])) or '-'}" +
              (f" | fallback: {', '.join(f['fallback'])}" if f.get("fallback") else "") +
              (f" | never: {never}" if never else "") + (" | client box, read-only" if f.get("client") else ""))


def capacity(r, need, reserve=None):
    """How many more jobs of this kind fit now, from free cores and free RAM, minus what the box keeps back."""
    if r.get("state") != "ok" or r.get("free_cores") is None or r.get("mem_avail_gb") is None:
        return 0
    res = reserve or {}
    cores = r["free_cores"] - res.get("cores", 0)
    ram = r["mem_avail_gb"] - res.get("ram_gb", 0)
    return max(0, int(min(cores / need["cores"], ram / need["ram_gb"])))


def pick(kind, n=1, fresh=True):
    p = plan()
    kinds = p.get("job_kinds", {})
    if kind not in kinds:
        raise SystemExit(f"unknown job kind {kind!r}; kinds: {', '.join(kinds)}")
    need = kinds[kind]
    rec = load(RECORD, {})
    age = time.time() - time.mktime(time.strptime(rec["at"][:19], "%Y-%m-%dT%H:%M:%S")) if rec.get("at") else 1e9
    if fresh and age > FRESH:
        rec = probe_all()
    ranked, refused = [], []
    for key in ("for", "fallback"):
        for name, m in p["machines"].items():
            f = m.get("fleet", {})
            if kind not in f.get(key, []):
                continue
            # off the laptop first: Shaan's own machine is the last fallback for anything headless
            tier = 0 if key == "for" else 2 if f.get("probe") == "local" else 1
            r = rec["machines"].get(name, {})
            fits = capacity(r, need, f.get("reserve")) if need.get("cores") else (1 if r.get("state") == "ok" else 0)
            if r.get("state") != "ok":
                refused.append(f"{name}: {r.get('state', 'never probed')} ({r.get('detail', '')})")
            elif fits < 1:
                kept = f" after keeping {f['reserve']['cores']} cores, {f['reserve']['ram_gb']} GB back" if f.get("reserve") else ""
                refused.append(f"{name}: full now ({gb(r['free_cores'])} cores, {gb(r['mem_avail_gb'])} GB free{kept};"
                               f" one {kind} needs {need.get('cores')} cores, {need.get('ram_gb')} GB)")
            else:
                ranked.append((tier, -fits, name, fits, r))
    ranked.sort()
    out = {"kind": kind, "need": need, "picked": None, "refused": refused, "candidates": []}
    for tier, _, name, fits, r in ranked:
        role = ("its job", "fallback", "last resort: Shaan's own machine")[tier]
        if need.get("cores"):
            why = (f"{role}; {gb(r['free_cores'])} of {r['cpus']} cores and {gb(r['mem_avail_gb'])} GB free: "
                   f"room for {fits} now")
        else:
            why = f"{role} by policy (load {r['load'][0]:g} on {r['cpus']} cores, {gb(r['mem_avail_gb'])} GB free)"
        out["candidates"].append({"machine": name, "fits": fits, "why": why, "tier": ("for", "fallback", "last")[tier]})
    if out["candidates"]:
        out["picked"] = out["candidates"][0]["machine"]
        spread, left = [], n
        for c in out["candidates"]:
            if left <= 0:
                break
            take = min(left, c["fits"])
            spread.append({"machine": c["machine"], "jobs": take})
            left -= take
        out["spread"] = spread
        out["unplaced"] = max(0, left)
    return out


CONSOLE_POST = os.path.expanduser("~/.claude/console/bin/console-post")   # launchd's PATH does not have it


def post(title, body):
    """A console card for Shaan; True only when the console took it (an alert counts once it is delivered)."""
    try:
        r = subprocess.run([CONSOLE_POST, "-a", "ESTATE", "-k", "card", "-t", title, body],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"console-post failed: {e}", file=sys.stderr)
        return False
    if r.returncode != 0:
        print(f"console-post failed: {r.stderr.strip()[-200:]}", file=sys.stderr)
    return r.returncode == 0


def filling(r, share=FILLING):
    """A warning before the account hits its process limit (then nothing new starts, not even ssh: the mini, 26 Sep)."""
    pr = r.get("procs") or {}
    n, lim = pr.get("user_count"), pr.get("user_limit")
    if not (isinstance(n, int) and isinstance(lim, int) and lim > 0 and n >= share * lim):
        return None
    was = next((h[1] for h in r.get("history", []) if h[0] >= time.strftime("%Y-%m-%dT%H:%M", time.localtime(time.time() - 86400))), None)
    most = ", ".join(f"{a} {b}" for a, b in pr.get("user_top", [])[:5])
    return (f"{r['machine']}: {pr['user']} runs {n} of the {lim} processes it is allowed" +
            (f" ({was} a day ago)" if was is not None else "") +
            f". At the limit nothing new can start, not even ssh. Most: {most}.")


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def age_s(st):
    return time.time() - time.mktime(time.strptime(st[:19], "%Y-%m-%dT%H:%M:%S")) if st else 1e12


def need(what, link=None):
    """Top of Shaan's dashboard plus a notification: only for what a human must do (siso-az need)."""
    cmd = [os.path.expanduser("~/.local/bin/siso-az"), "need", what, "--by", "ESTATE", "--project", "fleet"]
    try:
        return subprocess.run(cmd + (["--link", link] if link else []), capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def cleanup(target, cmds):
    """The machine's declared safe cleanups (tool-native cache cleaners), run over ssh; never fails the watch."""
    script = 'PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; ' + "; ".join(f"({c}) >/dev/null 2>&1" for c in cmds)
    try:
        subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=yes",
                        "-o", "RemoteCommand=none", "-o", "RequestTTY=no", target, script], capture_output=True, timeout=900)
    except subprocess.TimeoutExpired:
        pass


def health(name, r, f):
    """Storage, RAM and down-time rules from the machine's "health" block; True when the record changed."""
    h, changed = f.get("health") or {}, False
    if not h:
        return False
    if r.get("state") == "ok":
        disk, d = r.get("disk_free_gb"), h.get("disk_free_gb", {})
        if disk is not None and disk < d.get("clean_below", -1) and h.get("cleanup") and age_s(r.get("cleaned_at")) > REALERT:
            cleanup(f["probe"], h["cleanup"])
            after = probe_one(name, plan()["machines"][name]).get("disk_free_gb")
            r["cleaned_at"], changed = stamp(), True
            post(f"{name}: cleaned caches", f"{name} had {disk} GB free (under {d['clean_below']}); the safe cleanups "
                 f"ran and it has {after} GB free now.")
            disk = after if after is not None else disk
        if disk is not None and disk < d.get("alert_below", -1) and age_s(r.get("disk_alerted_at")) > 86400:
            if need(f"Free disk on the {name}: {disk} GB left after the automatic cache cleanup. Say which of its big "
                    f"folders can move to the vault (estate fleet shows it)."):
                r["disk_alerted_at"], changed = stamp(), True
        mem, low = r.get("mem_avail_gb"), h.get("mem_avail_gb", {}).get("alert_below")
        r["mem_low"] = (r.get("mem_low") or 0) + 1 if mem is not None and low and mem < low else 0
        if r["mem_low"] >= 2 and age_s(r.get("mem_alerted_at")) > REALERT:
            busy = ", ".join(f"{p['name']} {p['rss_mb']} MB" for p in sorted(r.get("top", []), key=lambda p: -p["rss_mb"])[:3])
            if post(f"{name}: RAM low", f"{name} has had under {low} GB free on two probes ({mem} GB now). Biggest: {busy}."):
                r["mem_alerted_at"], changed = stamp(), True
    elif r.get("state") not in (None, "not-probed") and h.get("need") and age_s(r.get("down_since")) > 60 * h.get("need_after_min", 30):
        if age_s(r.get("need_at")) > 86400 and need(h["need"]):
            r["need_at"], changed = stamp(), True
    return changed


def watch():
    rec = probe_all()
    changed = False
    now = time.time()
    recent = lambda stamp: stamp and now - time.mktime(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S")) < REALERT
    for name, r in rec["machines"].items():
        changed |= health(name, r, plan()["machines"].get(name, {}).get("fleet", {}))
        if r.get("state") in ("ok", "not-probed") or r.get("state") is None:
            pr = r.get("procs") or {}
            now_procs = (f" Right now {pr.get('user')} runs {pr.get('user_count')} processes; most: " +
                         ", ".join(f"{a} {b}" for a, b in pr.get("user_top", [])[:5]) + ".") if pr.get("user_count") else ""
            if r.get("was_down") and post(f"{name} is back", f"{name} answers again (down since {when(r['was_down'])})."
                                          f"{now_procs} `estate fleet` shows it."):
                r["was_down"] = None
                changed = True
            full = filling(r)
            if full and not recent(r.get("procs_alerted_at")) and post(f"{name} is filling up", full):
                r["procs_alerted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                changed = True
            continue
        if recent(r.get("alerted_at")):
            continue
        fix = plan()["machines"].get(name, {}).get("fleet", {}).get("when_down", "")
        if post(f"{name} is down", f"{name}: {r['state']}: {r['detail']}, since {when(r['down_since'])}. {fix}".strip()):
            r["alerted_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            changed = True
    if changed:
        with open(RECORD + ".tmp", "w") as f:
            json.dump(rec, f, indent=1)
            f.write("\n")
        os.replace(RECORD + ".tmp", RECORD)
    return sum(r.get("state") not in ("ok", "not-probed") for r in rec["machines"].values())


def pull(machine, job, src=None, dry=False):
    m = plan()["machines"].get(machine)
    if not m or not m.get("fleet", {}).get("probe") or m["fleet"]["probe"] == "local":
        raise SystemExit(f"{machine}: not a remote machine with a probe target in plan/machines.json")
    if m["fleet"].get("client"):
        raise SystemExit(f"{machine} is a client box: results are not pulled from it by this tool")
    src = (src or f"{m['fleet'].get('results', '~/fleet-out')}/{job}").rstrip("/") + "/"
    dst = os.path.join(RESULTS, machine, job)
    os.makedirs(os.path.dirname(dst), exist_ok=True)     # rsync makes the job folder itself, so a failed pull leaves none
    cmd = ["rsync", "-a", "--partial", "-e",
           "ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes -o RemoteCommand=none "
           "-o RequestTTY=no", f"{m['fleet']['probe']}:{src}", dst + "/"]
    if dry:
        cmd[1:1] = ["-n", "-v"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"rsync failed ({r.returncode}): {r.stderr.strip()[-300:]}")
    files = sum(len(fs) for _, _, fs in os.walk(dst)) if os.path.isdir(dst) else 0
    print(f"{machine}:{src} -> {dst}  ({files} files there now{', dry run' if dry else ''})")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", nargs="?", default="probe", choices=["probe", "pick", "pull", "watch", "show"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true", help="also probe on-demand boxes")
    ap.add_argument("--n", type=int, default=1, help="pick: how many jobs to place")
    ap.add_argument("--from", dest="src")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.cmd in ("probe", "show"):
        rec = probe_all(a.all) if a.cmd == "probe" else load(RECORD, {"machines": {}})
        if a.json:
            print(json.dumps(rec, indent=1))
        else:
            show(rec)
        return 1 if any(r.get("state") not in ("ok", "not-probed") for r in rec["machines"].values()) else 0
    if a.cmd == "pick":
        if not a.args:
            raise SystemExit("usage: estate fleet pick KIND [--n N]; kinds: " + ", ".join(plan().get("job_kinds", {})))
        out = pick(a.args[0], a.n)
        if a.json:
            print(json.dumps(out, indent=1))
        elif out["picked"]:
            c = out["candidates"][0]
            print(f"{a.args[0]} -> {c['machine']}: {c['why']}")
            if a.n > 1:
                print("  spread: " + ", ".join(f"{s['machine']} {s['jobs']}" for s in out["spread"]) +
                      (f"; {out['unplaced']} have no room anywhere now" if out["unplaced"] else ""))
            for c in out["candidates"][1:]:
                print(f"  then {c['machine']}: {c['why']}")
            for x in out["refused"]:
                print(f"  not {x}")
        else:
            print(f"{a.args[0]}: no machine has room now")
            for x in out["refused"]:
                print(f"  not {x}")
        return 0 if out["picked"] else 1
    if a.cmd == "pull":
        if len(a.args) != 2:
            raise SystemExit("usage: estate fleet pull MACHINE JOB [--from REMOTE_DIR] [--dry-run]")
        return pull(a.args[0], a.args[1], a.src, a.dry_run)
    if a.cmd == "watch":                          # a down box is reported by its card, not by launchd's exit code
        down = watch()
        print(f"{time.strftime('%F %T')} fleet watch: {down} down")
        return 0


if __name__ == "__main__":
    sys.exit(main())
