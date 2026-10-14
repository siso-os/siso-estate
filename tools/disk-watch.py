#!/usr/bin/env python3
"""Every disk the estate keeps, read each hour: free space, a floor, and days until full.

  disk-watch.py            read all four disks, print free / floor / days-until-full (read-only, writes nothing)
  disk-watch.py --record   also append one line to machines/disk-watch.jsonl and, when a disk is under its floor or
                           forecast to fill within 3 days, update the console card `estate-disk-floor` (one card, same id)

Run hourly by the laptop's storage-guard. The forecast is a least-squares line through the last 72 h of readings;
spikes from a build that is still running look like growth, so a forecast alone is reported, never acted on.
Shaan, 14 Oct: "you can help manage the space on my laptop that Halo VPS and like just my laptop and the Mac Mini".
"""
import argparse, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOG = os.path.join(REPO, "machines", "disk-watch.jsonl")
# machine: (ssh alias or None for this laptop, filesystem, floor GiB)
DISKS = {"laptop": (None, "/System/Volumes/Data", 25), "mini": ("mac-mini-herdr", "/System/Volumes/Data", 25),
         "halo-vps": ("halo-vps", "/", 20), "vps-siso": ("siso-vps", "/", 10)}
SOON_DAYS = 3


def df(alias, fs):
    cmd = ["df", "-k", fs] if not alias else \
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", alias, "df -k " + fs]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40).stdout.splitlines()
        f = out[1].split()
        return {"size_gib": round(int(f[1]) / 1048576, 1), "free_gib": round(int(f[3]) / 1048576, 1)}
    except (IndexError, ValueError, OSError, subprocess.TimeoutExpired):
        return None  # BLOCKED: could not observe


def days_to_full(m, now_free, now):
    pts = []
    if os.path.exists(LOG):
        for l in open(LOG):
            try:
                r = json.loads(l)
            except ValueError:
                continue
            if now - r["t"] <= 72 * 3600 and r.get(m):
                pts.append((r["t"], r[m]["free_gib"]))
    pts.append((now, now_free))
    if len(pts) < 6 or pts[-1][0] - pts[0][0] < 12 * 3600:
        return None
    n = len(pts); mx = sum(t for t, _ in pts) / n; my = sum(g for _, g in pts) / n
    var = sum((t - mx) ** 2 for t, _ in pts)
    slope = sum((t - mx) * (g - my) for t, g in pts) / var if var else 0  # GiB per second
    return round(now_free / (-slope * 86400), 1) if slope < 0 else None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    now, line, bad = time.time(), {}, []
    for m, (alias, fs, floor) in DISKS.items():
        r = df(alias, fs)
        if not r:
            print("%-9s BLOCKED: could not read df" % m)
            continue
        r["days_to_full"] = days_to_full(m, r["free_gib"], now)
        line[m] = r
        why = ("under the %d GiB floor" % floor if r["free_gib"] < floor else
               "full in %.1f days at the 72 h rate" % r["days_to_full"] if r["days_to_full"] is not None and r["days_to_full"] < SOON_DAYS else "")
        if why:
            bad.append("%s %.1f GiB free: %s" % (m, r["free_gib"], why))
        print("%-9s %6.1f of %6.1f GiB free  floor %2d  %s%s" % (m, r["free_gib"], r["size_gib"], floor,
              "days-to-full %s" % r["days_to_full"] if r["days_to_full"] is not None else "not filling", ("  <- " + why) if why else ""))
    if a.record and line:
        with open(LOG, "a") as f:
            f.write(json.dumps(dict(t=int(now), at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), **line)) + "\n")
        post = os.path.expanduser("~/.claude/console/bin/console-post")
        if os.path.exists(post):
            title = "Disk under the floor" if bad else "Disks above the floor"
            body = ("; ".join(bad) + ". The hourly rules already ran on the Macs: what fills it now is someone's data. "
                    "Run the estate-space-sweep skill. Map: docs/STORAGE-MAP.md.") if bad else \
                   "; ".join("%s %.0f GiB free" % (m, r["free_gib"]) for m, r in line.items()) + " (%s)." % time.strftime("%d %b %H:%M")
            subprocess.run([post, "-a", "ESTATE", "-k", "status", "-i", "estate-disk-floor", "-t", title, body],
                           capture_output=True, timeout=30)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
