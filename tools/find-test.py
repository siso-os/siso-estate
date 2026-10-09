#!/usr/bin/env python3
"""The dumbest-agent find test (goal check A1): cold Haiku sessions, started in ~, answer fixed where-is questions;
each answer is scored against a fixed path. Target: at least 95% correct, median 3 tool calls or fewer.

  find-test.py [--model claude-haiku-4-5-20251001] [--jobs 2] [--only N,M]  -> machines/<m>/find-test.json

A cold session loads only what any agent loads (~/.claude/CLAUDE.md and the nearest AGENTS.md), so a wrong answer
means the city is not findable, not that the agent was weak. Add a question whenever a real agent gets lost.
"""
import argparse, json, os, re, statistics, subprocess, time
from concurrent.futures import ThreadPoolExecutor

HOME = os.path.expanduser("~")
WS = os.path.join(HOME, "SISO_Workspace")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
Q = [  # (question, the path the answer must name, relative to ~/SISO_Workspace)
    ("Where is Oracle streaming's front door (the one folder to start any Oracle work in)?", "SISO_Agency/partners/halo/oracle"),  # 26 Sep: was "the operator app's front door", which operator-app/ answers truly
    ("Where does a brand-new client's work go?", "SISO_Agency/clients"),
    ("Which checkout of the HALO CRM is the real one?", "SISO_Agency/partners/halo/crm/repo"),
    ("Where should a new git worktree be created?", "_data/worktrees"),
    ("Where are a project's API keys and .env files kept?", ".credentials/projects"),
    ("Where does the SISO Estate Manager live (its house)?", "SISO_Agents/siso-estate"),
    ("Where is the UI component bank (the ranked corpus of components)?", "Great_Library_of_SISO/banks/siso-component-bank"),
    ("Where are the HTML page templates agents compose report pages from (siso-shell)?", "Great_Library_of_SISO/banks/siso-shell"),
    ("Where are the Fahmy / Bykonz client's media files (Drive exports, Loom videos)?", "SISO_Agency/partners/fahmy/_intake"),
    ("Where is Agent Zero's repo?", "SISO_Agents/agent-zero/siso-firstmate"),
    ("Where is the home of every Agent Zero (the top one and the index of each project's)?", "SISO_Agents/agent-zero"),
    ("Where are SISO skills authored (the source, not the installed copies)?", "SISO_Agents/siso-skills-hub"),
    ("Where do retired folders go when something is archived?", "_archive"),
    ("Where is the HALO agency block (everything for Cam Kellman's agency)?", "SISO_Agency/partners/halo"),
    ("Where does Shaan's personal stuff (finance, legal, study) live?", "personal"),
    ("Where is the bank of vetted GitHub repos with reuse scores?", "Great_Library_of_SISO/banks/siso-repo-bank"),
    ("Where does third-party code we only study go?", "_reference"),
    ("Where is the agent stack project (hooks, brain, workers)?", "SISO_Agents/siso-harness-lab"),
    ("Where is the map of the whole laptop (not just the workspace)?", "docs/operations/LAPTOP-MAP.md"),
    ("Where is SISO's Software Factory?", "SISO_Agency/factory"),
    ("Where is the kellman automation code (HALO agency workflows)?", "SISO_Agency/partners/halo/kellman"),
    ("Where is SISO Internal Labs (the Plane-based work hub, its agents, server and Mac app)?", "SISO_Agency/apps/siso-internal-labs"),
    # 26 Sep: to the 40 the goal names (A1), machine questions included; a "~/" answer is home-relative
    ("Where are Claude Code's session transcripts stored on this laptop?", "~/.claude/projects"),
    ("Where does the source of a new SISO launchd job go (the file you edit, not the installed copy)?", "SISO_Agents/siso-estate/plan/launchd"),
    ("Which Claude Code home directory is the live one (its brain, hooks and skills)?", "~/.claude"),
    ("Where are Codex's configuration and brain kept?", "~/.codex"),
    ("Where does a new SISO-owned product (an app SISO itself builds and runs) go?", "SISO_Agency/apps"),
    ("Where does a new partner agency (another agency SISO works with) go?", "SISO_Agency/partners"),
    ("Where are the estate's decisions (its ADRs) kept?", "SISO_Agents/siso-estate/docs/adr"),
    ("Where is every folder move the estate made recorded, one line per move?", "SISO_Agents/siso-estate/machines/laptop/moves.jsonl"),
    ("Where is the College Besties client (a client of HALO)?", "SISO_Agency/partners/halo/clients/collegebesties"),
    ("Where is the MelanoTresses client (a client of Fahmy's agency)?", "SISO_Agency/partners/fahmy/clients/melanotresses"),
    ("The repo bank is checked out twice; which checkout is the real one?", "Great_Library_of_SISO/banks/siso-repo-bank"),
    ("Where is the Oracle operator app (the operator board)?", "SISO_Agency/partners/halo/oracle/operator-app"),
    ("Where are the backups of data that lives outside git (the data planes) declared?", "SISO_Agents/siso-estate/plan/data-planes.json"),
    ("Where is the Great Library's catalog command-line tool?", "Great_Library_of_SISO/bin/gls"),
    ("Where is the one list of every SISO launchd job and its owner?", "SISO_Agents/siso-estate/plan/services.json"),
    ("Where is the registry of ports for local dev servers?", "SISO_Agents/siso-estate/plan/ports.json"),
    ("Where do incoming files go before they are filed?", "_inbox"),
    ("Where do runtime databases, caches and builds that are not source go?", "_data"),
]
FROM = {"home": "~", "root": "SISO_Workspace", "product": "SISO_Workspace/SISO_Agency/apps/siso-internal-labs/siso-internal-labs-server",
        "client": "SISO_Workspace/SISO_Agency/partners/fahmy/bykonzyard"}   # the goal's four start folders


def target(want):
    return os.path.join(HOME, want[2:]) if want.startswith("~/") else os.path.join(WS, want)


def shown(want):
    return want if want.startswith("~/") else "~/SISO_Workspace/" + want
# a container question: an answer naming the right thing inside it is right (the Software Factory IS the repo in factory/)
INSIDE_OK = {"SISO_Agency/clients", ".credentials/projects", "SISO_Agency/partners/fahmy/_intake", "_archive", "SISO_Agency/factory",
             "SISO_Agency/partners/halo/kellman", "Great_Library_of_SISO/banks/siso-repo-bank", "_data/worktrees",
             "Great_Library_of_SISO/banks/siso-shell", "SISO_Agency/apps/siso-internal-labs", "SISO_Agency/apps",
             "SISO_Agency/partners", "SISO_Agency/partners/halo/clients/collegebesties",
             "SISO_Agency/partners/fahmy/clients/melanotresses", "~/.claude/projects", "_data", "_inbox",
             "SISO_Agents/siso-skills-hub", "~/.codex"}   # 26 Sep: registry/skills in the hub and ~/.codex/AGENTS.md are true answers
PROMPT = ("You are a new agent on this laptop with no prior context. Question: {q}\n"
          "Find the answer by looking, then reply with ONLY the absolute path, on one line, nothing else.")


def norm(s):
    """The first path in an answer (agents sometimes echo a sentence around it); a trailing <placeholder> is dropped."""
    m = re.search(r"(~|/Users/)[^\s`'\"),;]*", s)
    p = (m.group(0) if m else s.strip()).replace("~", HOME, 1) if (m and m.group(0).startswith("~")) else (m.group(0) if m else s.strip())
    p = re.sub(r"/<.*$", "", p)
    return p.rstrip("/.")


def ask_codex(q, cwd):
    """Codex's cold answer: codex exec from ~, read-only, its default (cheapest configured) model; tool calls are its
    command executions."""
    r = subprocess.run(["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only", "--json", PROMPT.format(q=q)],
                       cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    text, calls = "", 0
    for l in r.stdout.splitlines():
        try:
            e = json.loads(l)
        except json.JSONDecodeError:
            continue
        it = e.get("item") or {}
        if e.get("type") == "item.completed" and it.get("type") == "command_execution":
            calls += 1
        if e.get("type") == "item.completed" and it.get("type") == "agent_message":
            text = it.get("text", "")
    return text, calls + 1


def ask(i, model, harness="claude", start="home"):
    q, want = Q[i]
    cwd = os.path.join(HOME, FROM[start]) if FROM[start] != "~" else HOME
    t0 = time.time()
    if harness == "codex":
        text, turns = ask_codex(q, cwd)
        if not text.strip():                     # a run that returned no message at all (load, rate limit): once more
            text, turns = ask_codex(q, cwd)
        r = None
    else:
      r = subprocess.run(["claude", "-p", PROMPT.format(q=q), "--model", model, "--output-format", "json", "--max-turns", "15",
                        "--allowedTools", "Bash", "Read", "Glob", "Grep"], cwd=cwd, capture_output=True, text=True, timeout=600)
      try:
        o = json.loads(r.stdout)
        text, turns = o.get("result", ""), o.get("num_turns")
      except json.JSONDecodeError:
        text, turns = (r.stdout or r.stderr)[-300:], None
    line = next((norm(l) for l in text.splitlines() if l.strip().startswith(("/", "~", "`/", "`~"))), norm(text.splitlines()[-1]) if text.strip() else "")
    exact = target(want)
    ok = line == exact or (line.startswith(exact + "/") and want in INSIDE_OK)
    return {"n": i + 1, "q": q, "want": shown(want), "from": start, "answer": line.replace(HOME, "~"), "ok": ok,
            "no_answer": not text.strip(),
            "turns": turns, "tool_calls": (turns - 1) if isinstance(turns, int) else None, "secs": round(time.time() - t0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-haiku-4-5-20251001")
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--harness", choices=["claude", "codex"], default="claude")
    ap.add_argument("--from", dest="start", choices=sorted(FROM), default="home")
    ap.add_argument("--only")
    ap.add_argument("--rescore", action="store_true", help="score the saved answers again (after a key change) without asking")
    a = ap.parse_args()
    p = os.path.join(REPO, "machines", os.environ.get("ESTATE_MACHINE", "laptop"),
                     "find-test" + ("" if a.harness == "claude" else f"-{a.harness}") + ("" if a.start == "home" else f"-{a.start}") + ".json")
    if a.rescore:
        old = json.load(open(p))
        for r in old["results"]:
            ans, want = norm(r.get("raw", r["answer"])), target(Q[r["n"] - 1][1])
            r["ok"] = ans == want or (ans.startswith(want + "/") and Q[r["n"] - 1][1] in INSIDE_OK)
        old["correct"] = sum(r["ok"] for r in old["results"])
        json.dump(old, open(p, "w"), indent=1)
        print(f"rescored: {old['correct']}/{old['asked']}; wrong: " + "; ".join(f"{r['n']} {r['answer']}" for r in old["results"] if not r["ok"]))
        return
    idx = [int(x) - 1 for x in a.only.split(",")] if a.only else list(range(len(Q)))
    with ThreadPoolExecutor(a.jobs) as ex:
        res = sorted(ex.map(lambda i: ask(i, a.model, a.harness, a.start), idx), key=lambda r: r["n"])
    calls = [r["tool_calls"] for r in res if r["tool_calls"] is not None]
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "harness": a.harness, "from": a.start, "model": a.model if a.harness == "claude" else "codex default", "correct": sum(r["ok"] for r in res), "asked": len(res),
           "median_tool_calls": statistics.median(calls) if calls else None,
           "no_answer": sum(r.get("no_answer", False) for r in res),   # harness failures, apart from wrong answers
           "results": res}
    if not a.only:
        json.dump(out, open(p, "w"), indent=1)
    for r in res:
        print(f"{'ok ' if r['ok'] else 'NO '} {r['n']:2} calls={r['tool_calls']} {r['answer'][:70]:70} want {r['want']}")
    print(f"{out['correct']}/{out['asked']} correct ({out['no_answer']} returned no answer at all); median tool calls {out['median_tool_calls']}" + ("" if a.only else f" -> {p}"))


if __name__ == "__main__":
    main()
