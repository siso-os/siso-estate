#!/usr/bin/env python3
"""File loose downloads into the estate, by rule, keeping the last few days where they are.

  file-intake.py plan [--src ~/Downloads] [--days 7]    what would go where
  file-intake.py run  [--src ~/Downloads] [--days 7] [--only RULE]   --only files one rule's items (e.g. credentials, any age)

Rules (first match wins; name, case-insensitive). Everything goes to a dated intake folder inside its home, never into a
repo's tracked tree; each move is one rename and a line in machines/<machine>/intake.jsonl (src, dst, rule, bytes).
Installers are re-downloadable, so they go to the archive; nothing is deleted.
"""
import argparse, json, os, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import legend  # a partner's home moves with the legend, so intake follows it

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAMP = time.strftime("%Y-%m-%d")
RULES = [  # (rule, regex on the name, destination folder)
    ("study", r"eieae|ralo3|umcdsk|umcdsn|^(00-)?section-[1-4]|pebblepad|^epq|module[-_ ]handbook|resit|completing your course|award-data",
     f"personal/team-entrepreneurship/_intake/downloads-{STAMP}"),
    ("legal", r"karolina|aarti|form e|applicant|respondent|court|schedule of deficiencies|s25|solicitor|barrister|divorce|evidence[-_ ]pack|disclosure|hearing|nsr_|sisodia[ _]v[ _]sisodia|wildcat|raphael|cross[-_ ]?exam|counsel|lawyer|asset schedule|ps final|offer[-_ ]response|credibility|dad_pack|open[-_ ]reply|annex to ps",
     f"personal/legal/_intake/downloads-{STAMP}"),
    ("credentials", r"secret[-_ ]?key|credential|password|\.env$|api[-_ ]?key|recovery[-_ ]?codes|\.pem$|\.p12$|logins?\b|access[-_ ]handover|takeover[-_ ]pack|private[-_ ]handover|backup[-_ ]codes|\.conf$|\.ovpn$|ssh[-_ ]access|kaggle",
     f".credentials/_intake/downloads-{STAMP}"),
    ("client-fahmy", r"^byk[-_]|bykonz|fahmy|mygumm|kling|night and day|dayparty|day party|dj ",
     f"{legend.home('fahmy')}/_intake/downloads-{STAMP}"),
    ("family", r"^dad[-_]|save-the-date|pretash|nayan|recovered_audio", f"personal/_intake/downloads-{STAMP}"),
    ("client-halo", r"halo|camron|kellman", f"{legend.home('halo')}/crm/_intake/downloads-{STAMP}"),
    ("client-alj", r"^alex[-_]|alj[-_]ofm", f"{legend.home('halo')}/inspiration/alj-ofm/_intake/downloads-{STAMP}"),
    ("agent-stack", r"siso-agent-(stack|base)", f"_inbox/downloads-{STAMP}/agent-stack-packs"),
    ("client-lumelle", r"lumelle", f"SISO_Agency/clients/lumelle/_intake/downloads-{STAMP}"),
    ("installer", r"\.(dmg|pkg|exe|msi|iso)$", f"_archive/{STAMP}-downloads-installers"),
    ("whatsapp", r"whatsapp", f"_inbox/downloads-{STAMP}/whatsapp"),
    ("images", r"\.(png|jpe?g|webp|avif|gif|heic)$", f"_inbox/downloads-{STAMP}/images"),
    ("video-audio", r"\.(mp4|mov|m4a|mp3|opus|wav|webm|vtt)$", f"_inbox/downloads-{STAMP}/media"),
    ("other", r".", f"_inbox/downloads-{STAMP}/other"),
]


def size(p):
    if os.path.isfile(p) or os.path.islink(p):
        return os.lstat(p).st_size
    return sum(os.lstat(os.path.join(r, f)).st_size for r, _, fs in os.walk(p) for f in fs)


def plan(src, days, only=None):
    cut = time.time() - days * 86400
    out = []
    for n in sorted(os.listdir(src)):
        if n in (".DS_Store", ".localized") or n.startswith(".com.google.Chrome"):
            continue
        p = os.path.join(src, n)
        if os.lstat(p).st_mtime > cut:
            continue
        for rule, rx, dst in RULES:
            if re.search(rx, n, re.I):
                if not only or rule == only:
                    out.append((p, os.path.join(WS, dst, n), rule))
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "run"])
    ap.add_argument("--src", default=os.path.join(HOME, "Downloads"))
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--machine", default=os.environ.get("ESTATE_MACHINE", "laptop"))
    ap.add_argument("--only", choices=[r[0] for r in RULES], help="only this rule's items")
    a = ap.parse_args()
    src = os.path.expanduser(a.src)
    moves = plan(src, a.days, a.only)
    by = {}
    for p, d, rule in moves:
        by.setdefault(rule, [0, 0])
        by[rule][0] += 1
        by[rule][1] += size(p)
    for rule, (n, b) in sorted(by.items(), key=lambda x: -x[1][1]):
        print(f"{rule:15} {n:4} items {b / 1e9:6.2f} GB")
    if a.cmd == "plan":
        for p, d, rule in moves[:400]:
            print(f"  {rule:13} {os.path.basename(p)[:70]}")
        return
    log = open(os.path.join(REPO, "machines", a.machine, "intake.jsonl"), "a")
    done = 0
    for p, d, rule in moves:
        if os.path.lexists(d):
            base, ext = os.path.splitext(d)
            d = f"{base}.{int(os.lstat(p).st_mtime)}{ext}"
        os.makedirs(os.path.dirname(d), exist_ok=True)
        b = size(p)
        os.rename(p, d)
        log.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "src": p, "dst": d, "rule": rule, "bytes": b}) + "\n")
        done += 1
    print(f"filed {done} items")


if __name__ == "__main__":
    main()
