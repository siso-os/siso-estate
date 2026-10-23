# Daily disk keeper

`estate disk --json` reads the latest local receipt without measuring. `estate disk
--dry-run --json` measures and selects without moving, committing, pushing or deleting
anything. `estate disk --run --json` enforces and records; `estate disk --install`
installs a retained, SHA-pinned copy as a daily 04:30 launchd job and exposes
`estate-disk --json`, even before the main estate launcher adopts the source. The
job never depends on a builder checkout that may be retired.

`plan/disk-classes.json` owns both machines' globs, owner, restore source, TTL and
caps. Repository home decisions remain in `../laptop-destinations.rules.json` and
`../laptop-destinations.json`; SSH aliases remain in laptop-health/health-machines.json.
Only allowlisted rebuildable roots expire. Caps on these roots shorten expiry to
24 idle hours; active or protected paths exceeding caps raise an alert. Other caps
are measurement and escalation limits, never permission to discard unknown data.

Worktree expiry reuses worktree-wip.py. A fingerprint of HEAD and dirty content is
recorded only after verified remote preservation. The 48-hour clock starts there;
changed content restarts it. Source retirement still requires the shared tool's
fresh remote, secrets, seat, open-file, snapshot and quarantine gates. Locked,
standing and private lanes remain held. codex-run already retires its completed
landed lane. Comparison baselines follow the keeper's 48-hour preservation policy;
the optional immediate baseline-cleanup hook is retained separately at
`preserve/disk-keeper-2-baseline-hooks-20261021`. Legacy clean-slate/backup piles are measured and flagged: names alone do
not establish complete GitHub custody or make their unique evidence disposable.

No full-home find: expand only declared immediate directories, discover dependency
roots from tracked package manifests with a count/time limit, then bound each `du -sk -x` and the total budget.
All measurements run under `heavy --class quick`, nice 19 and macOS background IO.
Incomplete scans retain explicit errors; growth compares only matching measured
paths and reports its coverage. The volume free-space change independently alerts
on growth, so an inaccessible application directory cannot hide a filling disk. Directory
sizes can overlap for nested inventory roots; class attribution subtracts them.
Spotlight markers are added on real runs, without refreshing existing markers.
Native sessions, projects, credentials/configs and Shaan's personal files are
measure-only. SISO Code userdata is protected; its statev2.sqlite watch uses only
filesystem stat and tags the SISO Code/AGENT-BASE owner above 5 GB. Unclassified
directories >=1 decimal GB alert on the same run.

Known large dependency roots are discovered before the bounded Git-manifest walk.
CI clone expiry also reuses the worktree owner's stash, ignored-payload and outside
linked-worktree guards. CI scratch requires its producer marker; failed runs stay.
Exit 1 retains a valid partial measurement receipt (for example TCC denial or a
budget limit); inspect coverage explicitly. Exit 2 is a fatal failure.

`~/.local/state/estate/disk-keeper/latest.json` is the Running panel contract:
`free_gb`, `bytes_by_class`, `growth_gb_by_class` (null until a complete yesterday
sample exists), `top5_movers`, `unclassified`, `over_caps`, `complete`, `errors`,
`released_gb` (measured directory estimates), and `free_delta_gb` (observed volume
change; includes concurrent writers). GB means 1,000,000,000 bytes.

One dated summary per machine lives in a small separate Git tree under `ledger/`,
pushed without force to `sisodias/siso:estate/disk-ledger-laptop|mini`. It contains
class totals and hashed mover labels, never session contents or private filenames.
A failed push retains the local commit for the next run. Detailed local receipts
stay outside source Git. Daily output rotates at 10 MiB into a compressed archive;
closed codex-run artifacts older than 30 days are losslessly compressed into
`_archive/disk-keeper-runtime/<machine>/` with SHA-256 readback and a manifest. A
90-second archive budget bounds each daily run. Native sessions/projects are
measure-only and never enter the archive selector.
Low free space (60 GB laptop, 100 GB Mini), growth >10 GB/day, caps, unknown large
directories and incomplete scans append one line to the selected Agent Zero data
home's `.agents/a0/inbox.log`, resolved by its existing compatibility resolver.
