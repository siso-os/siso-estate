# siso-estate: skills, commands and layout

Moved here from AGENTS.md on 26 Sep 2026, word for word, so the door stays under 40 lines (A8).

## Skills (`.claude/skills/`, loaded when you work in this repo)
| Skill | Use it to |
|---|---|
| `estate-boot` | start and end a manager session |
| `estate-census` | measure the city before claiming anything |
| `estate-clean-downloads` | file Downloads, Desktop, Documents and home-folder strays |
| `estate-move` | move a folder or repo without breaking consumers, worktrees or symlinks |
| `estate-house-and-retire` | give loose work a repo, sync a project to GitHub, take one off a machine |
| `estate-backup-and-secrets` | read the nightly, handle held repos, keys and scan false positives |
| `estate-machines` | put a machine on the map, record a client box, map a block to a VPS |
| `estate-delete-and-archive` | delete only with proof; keep the vault readable |

## Commands
| Command | What it does |
|---|---|
| `estate where <words>` / `estate path <words>` | find anything; `estate map` prints the city map |
| `estate brief [--mark]` | the boot packet (`tools/brief.py`), including the inbox other agents file into |
| `estate report "<what, where>"` | any agent files a mess it found into `.agents/INBOX.md` (the `estate-keeper` skill, in every harness, tells them to) |
| `estate census` | count the city (`tools/census.py` -> `machines/<m>/census.json`); nightly too |
| `estate inventory` · `estate doctor` | re-scan every repo · layout checks (also: stale doors or laptop map, hidden-folder findings) |
| `estate refresh [--light]` | regenerate every map from the disk: district doors (`tools/doors.py`, self-committing), `docs/operations/LAPTOP-MAP.md`, hidden folders, the umbrella; hourly by launchd `com.siso.estate-refresh`, full in the nightly |
| `estate code [words]` · `estate city` | the building code (`plan/building-code.json`, ADR 0016): every live repo scored, or one repo's certificate · the operations centre, live at http://127.0.0.1:8895/ (launchd `com.siso.estate-city`, rebuilt every 2 minutes) |
| `estate run <project> [-- CMD]` · `estate stop <project>` · `estate running` | start any project on its kept port (plan/ports.json; new ones get 6100-6199), with its URL, log (`_data/logs/run/`) and the agent that started it in `machines/<m>/running.json`; stop the whole process group (`tools/run.py`, goal-2050 P5) |
| `python3 tools/watch.py status` | the nervous system: launchd `com.siso.estate-watch` listens to FSEvents and keeps `machines/<m>/repos.json` and the register current within a minute of any new, moved, deleted or committed repo; each change and its lag in `machines/<m>/watch.jsonl` (goal-2050 P3) |
| `python3 tools/mcp_server.py` | the estate as an MCP server (stdio, no dependencies): `estate_where`, `estate_path`, `estate_owner`, `estate_running`, the same code as the CLI; install lines in its docstring (the agent stack owns harness config; goal-2050 P3, reform R3) |
| `estate fix [--run]` · `estate births` | the law (ADR 0017): routine fixes in any lane's repo under `plan/fix-policies.json`, receipts in `machines/<m>/fixes.jsonl` · where agents create things and whether they ask the map |
| `estate dots` · `python3 tools/untrack-state.py [--run]` | every hidden folder against ADR 0015 · stop tracking tool runtime state, one path-limited commit per repo |
| `estate backup plan\|run` · `estate data plan\|run <plane>` | disk-only work to private GitHub · encrypted data planes |
| `estate fleet [--all]` · `estate fleet pick <job-kind> [--n N]` · `estate fleet pull <machine> <job>` | every machine exec-probed now (cores, RAM, disk, what runs, what it is for; plan/machines.json) · the box for a job kind, off the laptop first · a remote job's results to `_data/fleet/<machine>/<job>/`. launchd `com.siso.estate-fleet` probes every 15 min and posts a console card when a box stops answering |
| `estate move SRC DST --no-link --why ...` | move with a manifest line, worktree repair |
| `estate umbrella build\|snapshot --push` · `estate restore --only <path> --run` | the map · bring a repo back |
| `python3 tools/houses.py keys\|house\|sync\|retire` | houses, sync, retire (ADR 0010, 0011) |
| `python3 tools/bigfiles.py plan\|unzip\|split\|drop` | files over GitHub's limit made fit, losslessly (ADR 0014) |
| `python3 tools/projects.py` · `tools/piles.py` | every project's state · loose piles by folder |
| `python3 tools/file-intake.py plan\|run` | file Downloads/Desktop/Documents |
| `python3 tools/reference-index.py drop` · `tools/archive-normalize.py` · `tools/archive-history.py` | third-party clones · the vault · harness history |
| `estate-bootstrap <machine>` · `estate-manager` | put a machine on the map · spin up the Estate Manager |

## Layout
| Path | What it is |
|---|---|
| `bin/` | `estate`, `estate-nightly` (launchd/cron 03:30), `estate-bootstrap`, `estate-manager` |
| `tools/` | the scripts behind the commands |
| `docs/adr/` | the primitives, one decision per file |
| `.claude/skills/` | the Estate Manager's skills |
| `machines/<machine>/` | generated records: repos, backups, census, projects, piles, and one jsonl line per move, removal, house, retirement |
| `machines/github/` | our GitHub repo list |
| `plan/` | goal, placements (`github-placements.json`), machines registry, data planes, report builders, coordination log |
| `.agents/` | handoff, queue, Shaan's words, project memory |
