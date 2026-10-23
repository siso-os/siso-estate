# A04 / t-0660 — one startup retirement; load-reduction check remains blocked

21 October 2026. Builder: dtask-t-0660 on the Mac mini. Runtime observations are from Shaan's laptop. This is a partial completion receipt, not evidence that all of A04 is done.

## Verified change

- User LaunchAgent files: **67 → 66**; only `com.siso.host-SCOUT` removed. **Zero running processes stopped.**
- SCOUT was already off in the service manifest, unloaded (`launchctl print` exit 113), had no named host record and no SCOUT host process. The mini had no installed SCOUT plist. Its replacement is the SISO Code seat/thread arrangement documented in the existing manifest.
- Installed plist archived with byte equality and SHA-256 proof in `retirement.json`; source plist archived with its own checksum under `../../_archive/2026-10-21-t-0660/`. Source and installed copies differ and are both preserved. The manifest row is removed so the installer cannot recreate the retired job.
- Existing A04 receipts remain partial: watcher d4081b4c571 (82 → 3 processes/change), 13 worktree removals, and cutover-5 remaining runtime findings. None proves this task's native load-reduction done-check.

## Native measurement, without a causal improvement claim

Both files contain four macOS `top -l 4 -s 5 -n 0` observations. Discard the first CPU observation; the following three measure intervals. Native Codex executable count stayed at eight throughout both windows. Exact-basename counts are process counts, not a census of active seats; Claude binaries with versioned filenames are not counted as `claude`. No prompts, environment values, transcripts or private database contents were captured.

| Measure | Before (2026-10-21T20:50:58.219123+07:00) | After (2026-10-21T20:52:28.920757+07:00) |
|---|---|---|
| Mean CPU busy, three measured intervals | 61.28% | 96.15% |
| Native codex processes, start/end | 8/8 | 8/8 |
| Node processes, start/end | 37/38 | 50/49 |
| Physical memory, final top observation | 15G used; 93M unused; 2369M compressed | 14G used; 1291M unused; 4142M compressed |

**The workload changed. These windows are not a comparable-load before/after benchmark.** SCOUT was unloaded beforehand, so its current process CPU/RSS contribution was already zero. Removing its startup file prevents future activation; it cannot explain a present load reduction. The lower free memory in one sample and increased CPU in the other must not be attributed to this retirement.

## Consumer reconciliation and boundaries

- `laptop-health servers`, invoked using `/opt/homebrew/bin/python3`, observed HALO board :5173 (370 MB, no clients in that snapshot) and SISO preview :3078 (18 MB, active Claude starter). Neither is proven unused. `strays` returned `no strays`.
- EFFICIENCY manifest says off, but launchd and host metadata prove live runner PID 57791 / child 57854, state idle. Preserve it: an idle agent is not an abandoned server. Other loaded/off manifest discrepancies are retained in the snapshot, not used as retirement authority.
- `health-watch --once` every 60s and `laptop-health watch --once` every 600s serve distinct health/wake and diagnostic requirements. The two caffeinate jobs use different sleep assertions. Neither pair is an established duplicate.
- The table covers all 67 user LaunchAgent plists observed before the change. A declared requirement or configured program explains retention; it does not establish live traffic or prove that an unexamined consumer is absent. System daemons and private database contents were not inspected.

| Label | Observed state | Consumer / decision evidence |
|---|---|---|
| `com.google.GoogleUpdater.wake` | loaded, no current PID | RETAIN: Google application updater; vendor managed. |
| `com.google.keystone.agent` | unloaded | RETAIN: Empty vendor plist; no executable or loaded job; no runtime saving. Preserve vendor tombstone. |
| `com.google.keystone.xpcservice` | unloaded | RETAIN: Empty vendor plist; no executable or loaded job; no runtime saving. Preserve vendor tombstone. |
| `com.kunchenguid.no-mistakes.daemon.f90e6871` | running | RETAIN: Installed no-mistakes application daemon; no evidence its user has retired it. |
| `com.siso.a0-autoplace` | loaded, no current PID | RETAIN: Places unsorted Agent Base agents under their project and owner every 60 seconds |
| `com.siso.a0-review` | loaded, no current PID | RETAIN: Agent Zero's daily self-review by one headless Haiku, 07:30 |
| `com.siso.a0-sweep` | loaded, no current PID | RETAIN: Archives finished helper threads in SISO Code's tree, hourly |
| `com.siso.a0-upgrade-haiku` | loaded, no current PID | RETAIN: a0-review on 1800-second schedule; Agent Zero improvement consumer. |
| `com.siso.ab-remote` | running | RETAIN: Agent Base remote server for the SISO Internal app |
| `com.siso.agency-inbox-alert` | loaded, no current PID | RETAIN: Counts new agency inbox messages (keys only, never contents) and tells A0 when it rises, every 900 seconds |
| `com.siso.agent-base-state` | loaded, no current PID | RETAIN: Backs up redacted Agent Base state every 1800 seconds |
| `com.siso.agent-base.edge` | running | RETAIN: the local Agent Base edge server for the SISO Internal Mac app (~/.local/share/siso-agent-base-local/edge.mjs) |
| `com.siso.agent-base.front` | running | RETAIN: Agent Base front server (~/.local/share/agent-base/front.mjs) |
| `com.siso.agent-base.node.5411` | unloaded | RETAIN: Agent Base node server on port 5411 |
| `com.siso.agent-base.node.5412` | running | RETAIN: Agent Base node server on port 5412 (not loaded 19 Oct) |
| `com.siso.agent-base.tty` | running | RETAIN: the local Agent Base terminal bridge for the SISO Internal Mac app |
| `com.siso.agent-base.voice` | running | RETAIN: Agent Base Voice dictation app |
| `com.siso.caffeinate` | running | RETAIN: Keep the laptop awake while infrastructure agents work (/usr/bin/caffeinate -ims) |
| `com.siso.claude-autoupdate` | loaded, no current PID | RETAIN: keeps Claude Code updated (live tree agents/harness) |
| `com.siso.client-platform-mission-control` | running | RETAIN: SISO client platform mission control on :4177 |
| `com.siso.code-idle-install` | loaded, no current PID | RETAIN: queue-idle-install.py --run every 60s; SISO Code idle deployment queue. |
| `com.siso.code` | running | RETAIN: SISO Code (the T3 Code fork) server on 127.0.0.1:5490 |
| `com.siso.cutover.caffeinate` | running | RETAIN: caffeinate -dims; display-sleep assertion differs from infrastructure -ims. Display requirement unresolved: retain. |
| `com.siso.db-archive-20261021` | loaded, no current PID | RETAIN: SISO Code database archive scheduled 22 October 02:00; task still pending, retain. |
| `com.siso.disk-keeper` | loaded, no current PID | RETAIN: Installed versioned disk-keeper-daily laptop release; storage protection. |
| `com.siso.estate-chat-offload` | loaded, no current PID | RETAIN: Weekly: chat transcripts older than 14 days to encrypted private GitHub (tools/archive-offload.py chats) |
| `com.siso.estate-city` | running | RETAIN: the estate operations centre on :8895 (tools/city.py serve) |
| `com.siso.estate-console` | running | RETAIN: Live SISO console at :8891, HTTP 200; ports.json and surfaces.json declare this surface. |
| `com.siso.estate-disk-floor` | loaded, no current PID | RETAIN: The laptop's free-space floor and the folders that grew, every 300 seconds, no tokens |
| `com.siso.estate-fleet` | loaded, no current PID | RETAIN: every 15 min: exec-probe every machine (tools/fleet.py watch -> machines/fleet.json); a box that stops answering posts a console card to Shaan (again every 6 h while down) and another when it is back |
| `com.siso.estate-mini-codex-vault` | loaded, no current PID | RETAIN: Hourly: over ssh, moves the mini's finished Codex rollouts (idle >1 h, not open) to its encrypted vault drive, verified (tools/codex-sessions-to-vault.sh; 17 GiB of rollouts on 8 Oct). Runs from the laptop because launchd on the mini may not write the external drive |
| `com.siso.estate-nightly` | loaded, no current PID | RETAIN: the nightly: inventory, backup, census, register, fixes (bin/estate-nightly, 03:30) |
| `com.siso.estate-refresh` | loaded, no current PID | RETAIN: hourly map refresh and umbrella snapshot (tools/refresh.py --light) |
| `com.siso.estate-storage-guard` | loaded, no current PID | RETAIN: Hourly: clears rebuildable storage leaks under plan/fix-policies.json; laptop floor 100 GiB and receipts in machines/laptop/storage-guard.jsonl; Mini uses the machine-specific source with a 60 GiB floor |
| `com.siso.estate-storage-map` | loaded, no current PID | RETAIN: Weekly, Sunday 04:10 on laptop and Mini (Shaan, 8 Oct: daily was "a bit harsh"): maps every gigabyte in one throttled du pass (tools/storage-map.py) and writes each machine's docs/STORAGE-MAP.md, storage-tree.json, and storage-history.jsonl |
| `com.siso.estate-watch` | running | RETAIN: the nervous system: FSEvents on ~/SISO_Workspace keep repos.json and the register current within a minute (tools/watch.py serve, P3) |
| `com.siso.guest-gateway-budget` | running | RETAIN: Guest gateway budget server |
| `com.siso.guest-gateway-relay` | running | RETAIN: Guest gateway OAuth relay |
| `com.siso.guest-gateway` | running | RETAIN: Guest gateway service |
| `com.siso.halo.cam-laptop-sync` | loaded, no current PID | RETAIN: syncs Cam's laptop-side HALO CRM tooling (HALO_Agency/crm/tools/cam-laptop-sync.sh) |
| `com.siso.health-watch` | loaded, no current PID | RETAIN: Samples machine health every 60 seconds and records sustained health alerts |
| `com.siso.host-EFFICIENCY` | running | RETAIN: Keeps the EFFICIENCY Codex SDK host alive, working in the monorepo agents/harness (not loaded since 9 Oct) |
| `com.siso.host-SCOUT` | unloaded | RETIRED: documented supersession; no launchd job, host record or process. |
| `com.siso.jarvis-distill` | loaded, no current PID | RETAIN: Jarvis nightly distill (.agents/jarvis/bin/jarvis-distill) |
| `com.siso.kill-contacts-hogs` | loaded, no current PID | RETAIN: stops macOS Contacts helpers that spin CPU (~/bin/kill-contacts-hogs.sh) |
| `com.siso.laptop-health-watch` | loaded, no current PID | RETAIN: laptop-health watch pass every 10 minutes (SISO_Agents/laptop-health) |
| `com.siso.library-reader` | loaded, no current PID | RETAIN: rebuilds the library reader site hourly from the live tree (library/reader/bin/rebuild.sh) |
| `com.siso.live` | loaded, no current PID | RETAIN: advances the live tree (_data/worktrees/siso/live) to landed main every 5 min; runtimes run from it (siso decisions/0028) |
| `com.siso.login-failover` | loaded, no current PID | RETAIN: Moves a seat to the other Claude login when its usage runs out, every 120 seconds |
| `com.siso.machine-tunnel.halo` | running | RETAIN: Configured HALO SSH tunnel; client dependency, retain. |
| `com.siso.mini-code-forward` | running | RETAIN: Configured SSH forwarding to mini Code; remote access consumer. |
| `com.siso.music-playlist-import` | loaded, no current PID | RETAIN: imports YouTube playlists into the SISO Music library, from the live tree apps/music |
| `com.siso.music` | running | RETAIN: SISO Music server on 127.0.0.1:5493, from the live tree apps/music |
| `com.siso.oss-sync` | loaded, no current PID | RETAIN: 04:15 nightly: laptop dispatches public siso-os exports to the Mini through heavy (live tree ops/estate/os-release) |
| `com.siso.property-sync` | loaded, no current PID | RETAIN: syncs the UK house search to the Mac Mini (personal/goals/uk-house-search); fails while the Mini is offline |
| `com.siso.renewals` | loaded, no current PID | RETAIN: Agent Zero renewals checks at 10:00 and 18:00 |
| `com.siso.roles-cycle` | loaded, no current PID | RETAIN: Runs the standing infrastructure owners in their mode, every 600 seconds (not loaded 19 Oct) |
| `com.siso.serena-daemon` | running | RETAIN: Serena code-navigation daemon |
| `com.siso.serena-lazy-prepare` | loaded, no current PID | RETAIN: Serena --prepare every 21600 seconds; code navigation preparation. |
| `com.siso.serena-project-server` | running | RETAIN: Serena project server on :24225 |
| `com.siso.sol-sunset` | loaded, no current PID | RETAIN: sol-sunset check every 900 seconds; dated model transition watchdog. |
| `com.siso.ui-hub-nightly` | loaded, no current PID | RETAIN: UI hub nightly: census, fingerprints, inventory, uihub checks, reader hand-offs (siso-ui-hub/bin/nightly, 04:15; commits locally, never pushes) |
| `com.siso.voice-sync` | running | RETAIN: voice notes sync (siso-agent-zero/extensions/voice) |
| `com.siso.workspace-node` | running | RETAIN: the SISO workspace node (~/.siso-workspace runtime, siso-node.py) |
| `homebrew.mxcl.postgresql@15` | running | RETAIN: Installed PostgreSQL data service; database consumers not audited; no retirement justified. |
| `io.github.codex-router` | running | RETAIN: Installed Codex router start.mjs; agent routing, retain. |
| `sh.brew.beszel-agent` | running | RETAIN: Installed Beszel monitoring agent; monitoring consumer. |

## Collision and preservation checks

Checked open SISO worktrees on both machines and the wave3 pids file. Prior t-0660 (47753/52679), estate-sweep (19781), and cutover-5 (53632) processes were absent. The cutover source lane had no uncommitted changes in the two owned service paths. The mini lane is fresh `dtask-t-0660`; no shared live source was edited. Free disk remained above the brief's 10 GiB floor during edits.

## Validation

- `heavy --class quick -- python3 -B -m unittest tests.test_services` from Estate core: **4 tests passed** (exit 0). Existing fixture ResourceWarnings do not change the result.
- Archive/source comparison: byte-for-byte identical to the prior Git blob; plist parses; the only manifest change is removal of SCOUT. `git diff --check` passes.
- Laptop post-retirement: original file absent; archived SHA-256 matches; `launchctl print` still returns 113 for SCOUT.

## Remaining two-line specification

1. Identify a remaining loaded service whose consumer is explicitly retired (in particular resolve whether cutover.caffeinate must still prevent display sleep); preserve active agents, HALO consumers and Code.
2. Supply a stable seat/workload interval for matched native CPU/RAM sampling; success must distinguish prevented startup from measurable current resource reduction.

Until those consumer/workload facts are established, A04 remains blocked; do not mark it done from the plist count alone.
