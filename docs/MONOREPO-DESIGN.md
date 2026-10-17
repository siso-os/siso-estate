# SISO monorepo: first-principles, agent-first design (17 Oct 2026)

Shaan, 17 Oct: "redesign it from first principles as if we didn't have any repos to start with", "built to scale ...
shit might get moved around, reorganized, rebuilt", "designed beautifully so that agents can interact with it,
self-improve it, self-iterate it". Evidence: 286 repos read (`plan/monorepo-repo-reads.jsonl`), 10 research slices
(`/tmp/mono-deep/r1..r10-*`, ~375 sourced rows), Anthropic notes, our own usage data and 2,122-line lessons ledger.
This builds on ADR 0021/0022 in sisodias/siso (roots kept); it adds what they lack: stable ids, seats, enforcement.

## 0. What the evidence says (one line each)
- Anthropic, Google, Meta: one repo for own code, partition by owner and job, small working copy, fetch on demand.
  Anthropic left git (Scion) at 30k merges/day; we are ~4 orders below that: stock git is right for years.
- Microsoft: virtual filesystems lost to stock git (blobless clone + cone sparse + maintenance; `scalar` is in git).
- OpenAI (harness engineering), Stripe, Vercel: a ~100-line root map, short per-directory AGENTS.md loaded on
  traversal, a generated compressed index (Vercel: 53% -> 100% task pass, 40 KB -> 8 KB). Rules live as checks.
- Catalogs (Backstage, Cortex, Port's sunset): identity must be a stable id, never the path. Moves break path keys.
- Nx/moon: per-module manifest with owners + tags; untagged modules depend on nothing; CODEOWNERS is generated.
- Claude Code docs: CLAUDE.md is "context, not enforced configuration"; hooks enforce. Our ledger agrees: ~1,300 of
  1,641 lessons are mistakes a check or structure could have stopped; prose rules were the recurring failure.
- Usage: 16 of 269 repos had any agent session in the last 8 days. The working set is small; the rest is cold.
- GitHub limits: repo ideally < 1 GB, file < 100 MiB; LFS is metered. git-crypt leaks and cannot rotate.

## 1. The repo set (who may read decides the split, never the product)
| Repo | Holds | Why separate |
|---|---|---|
| `sisodias/siso` | ALL SISO-own code, seats, rules, knowledge text, data manifests | one access boundary |
| `sisodias/siso-data-<family>-<set>-NN` | data blocks, ~1 GB each (5 GB hard), files <= 64 MiB | size; encryption per family |
| `HALO-AGENCY/halo` | HALO's monorepo (Cam's code never copied) | client boundary, ADR 0007 |
| client- / partner-owned repos (Fahmy, kikas, ...) | their code | they own it |
| real forks (`siso-code` T3, listmonk, chat-widget, ...) | upstream + our commits | upstream history; a `kind: fork` pointer module in siso |
| `sisodias/siso-city` | the machine map (`~/SISO_Workspace`), lock of every repo above | where things sit on each box |
| archived repos | history of everything merged | `gh repo unarchive` brings any back |
Public code: `siso-os/*` are exports from paths in `sisodias/siso` (Copybara pattern), never edited there.

## 2. The tree (fixed kinds are folders; everything else is tags)
```
AGENTS.md        router, <= 100 lines; bottom half generated: one line per module (id, owner, purpose, check)
decisions/       ADRs (0021, 0022, + 0023 ids/seats, 0024 data blocks)
seats/<seat>/    a persistent agent's home (section 4)
agents/          the agent system: zero (A0 runtime), harness (brain, hooks, stack, routing), work (tasks),
                 team, skills/{hub,playbook}, adapters
estate/          city hall: estate tool, laptop-health, os-release (exports), guest-gateway
packages/        shared code (@siso/ui, business-os, site-kit, booking, voice, cms, edge-auth, ...)
products/        what we ship: os (siso-internal + business-os app + life module), ui-hub, siso-voice,
                 siso-browser, siso-music, sites, labs, bench, lawbook, project-base, client-platform
factory/         client brief -> app: line, generator, method, app-factory
industries/<x>/  pack + clients/<brand> (brand and data only; client code stays in their repo)
library/         knowledge system code, catalog, registry, works, small curated text; big corpora = data blocks
hq/              the agency: direction, brand, site, ops docs
data/            manifests only: blocks.json pins every block by repo + sha; `bin/get <block>` mounts it
bin/             new, move, check, land, work, get, index
.siso/           tags.json (closed vocabulary), moves.jsonl (append-only), baseline.json (ratchet), generated/
```
Dependency direction (checked): products -> packages -> nothing; agents/estate/factory -> packages; nothing -> seats.
A kind folder over ~150 children splits by its first tag (Sapling: fan-out is a scale axis); ids do not change.

## 3. The module: one `module.json` per unit of ownership
```json
{ "schema": 1, "id": "siso.pkg.ui", "kind": "package", "name": "@siso/ui",
  "purpose": "Shared React components for every SISO product.", "owner": "ui-hub",
  "tags": ["domain:ui", "layer:package", "scope:public"], "depends": ["siso.pkg.tokens"],
  "visibility": ["layer:product", "layer:factory"], "data": ["library/ui-bank"],
  "checks": { "test": "pnpm -F @siso/ui test", "lint": "pnpm -F @siso/ui lint" },
  "lifecycle": "production", "exports": "siso-os/siso-ui" }
```
- `id` is set once and never reused (Cortex CID); paths are not stored, the tree is the path; `.siso/moves.jsonl`
  maps every old path to the id, so a stale path anywhere resolves (`siso where <old path>`).
- Kinds (closed): package, product, service, tool, agent, skill, pack, knowledge, data-manifest, docs, fork.
- References are ids everywhere: docs link `siso:<id>`, seats own ids, data names blocks; npm/Python names = ids.
- Default-private: a module may be used only by modules matching its `visibility` (Bazel/Nx default-deny).
- README head and per-module AGENTS.md are GENERATED from module.json (purpose, owner, run, check, deps) with a
  hand-written body below a marker; the 286 drifting READMEs get rewritten by this generator, not by hand.

## 4. Seats: each persistent agent has a home folder, not a repo
A repo per agent re-creates the 286-repo sprawl and splits a seat from the code it changes in one commit. A folder
gives the same persistence, atomic changes, and one place for every seat to be read by A0 and the screens.
```
seats/estate/
  seat.json     { id, name, role (one line), owns: [module ids], reads: [ids], route: "builder|reader|owner",
                  tools: [allowlist], loop: "LOOP.md", host: "t3" }        # host is a field: it changes each era
  AGENTS.md     the role; generated block lists owned modules and their checks
  HANDOFF.md    where the work stands (newest State first)
  memory/       one file per fact + MEMORY.md index (facts about this seat's job)
  source/       Shaan's verbatim words to this seat (work only; personal words stay in the personal data family)
  LOOP.md       the standing task
```
- `bin/work <seat>` = worktree `_data/worktrees/siso/<seat>`, blobless clone + cone sparse of: root files,
  `seats/<seat>`, owned modules and their dependency closure (Meta's in-repo sparse profiles, generated not edited).
  It also writes `.claude/agents/<seat>.md` and Codex config from seat.json, then launches in the seat folder.
- One writer per module: a module's owner seat lands its changes; any seat may PR into it (ADR 0021 trunk flow).
- Runtime state (queue, mailbox, dispatch log, transcripts) is data under `_data/`, never git (R8, R1).
- Routing by role (`route`), resolved through Agent Zero's routing.json; seats never name a model.
- Replaces: t3-seats.json + registry.json + owners.json (3 registries) with one generated `.siso/generated/seats.json`.

## 5. How it improves itself (the ladder, the ratchet, the gardener)
- **Memory has one home: the thing it is about.** A fact about code -> `<module>/.agents/memory/`; about a seat's job
  -> `seats/<s>/memory/`; nothing else. Global LEDGER becomes intake only (`.siso/lessons/inbox.jsonl`).
- **Ladder: correction -> lesson -> rule -> check -> hook.** Lesson file frontmatter: id, about (module id), evidence
  (commit/date), status, enforced_by. A lesson that recurs, or that a check can express, is promoted; a check ships
  only if it fails on the recorded incident and passes on the fix (replay before promote). Check errors cite the
  lesson id and link the rule. Superseded lessons get `valid_to`; retirement moves files to `_archive/`.
- **Ratchet gate.** `bin/check` maps changed paths -> modules -> reverse deps, runs only those checks, and fails only
  on reds not in `.siso/baseline.json` (no new reds; an old red never blocks six seats overnight again).
- **Gardener.** A nightly job (the ESTATE seat's loop) regenerates index/READMEs/CODEOWNERS/cones, grades every module
  (owner, purpose, checks pass, AGENTS.md size, tests, last touched), proposes promotions from the lesson inbox, and
  opens small fixes. Grades show on the map page; a module's grade only goes up (ratchet).
- **Reorganisation is cheap by design.** `bin/move <id> <path>` = git mv + moves.jsonl line + regenerate. Because
  nothing references paths, a smarter agent can re-lay the whole tree in one change and every seat follows on rebase.

## 6. Scale axes, each with a budget check (Sapling's list, our numbers)
| Axis | Budget | Mechanism |
|---|---|---|
| code repo size | < 1 GB packed | no tracked file > 1 MB without an allowlist; big = data block |
| files / fan-out | < 150 children per kind folder | split by first tag; ids unchanged |
| working copy | per seat cone | blobless + cone sparse; `scalar register` maintenance |
| merges/day | thousands | `bin/land` serialises, regenerates generated files on rebase, retries |
| seats | 24 -> hundreds | seat = folder + generated cone; registry generated |
| rules | unbounded | each rule is a check with an id; AGENTS.md stays <= 100 lines |
| data | GBs -> TBs | add numbered blocks; warn 1 TB, stop GitHub data at 5 TB (estate guard) |

## 7. Data architecture
- Families (one key each, keys in `.credentials/projects`): `library` (plain: transcripts, UI/repo banks, corpora),
  `people` (enc), `personal` (enc, Shaan only: life, lifelock data, WhatsApp, memories), `runtime` (enc: service DBs,
  agent state, run evidence), `client-<x>` (enc, per client; HALO keeps its own), `credentials` (enc), `archive`.
- Physical: repos `siso-data-<family>-<set>-NN`, target 1 GB (5 GB stop), files <= 64 MiB, immutable once sealed;
  new data = a new block, never a rewrite. Manifests in `data/<family>/<set>.json` (file id, path, size, sha256,
  block). Encrypted families use age per family key on whole files, after the secret scan; git-crypt is not used.
- Code and data meet by id: a module's `data: ["library/ui-bank"]` lets `bin/work` offer the blocks a seat needs.
- v2 later, once GitHub's terms for release-asset bulk storage are checked: content-defined chunks packed into 64 MiB
  release assets (Hugging Face Xet design), dedupe for the library family.

## 8. The merge (286 repos -> 1 code repo + data blocks + few separate)
| Module (path) | Absorbs | Archive |
|---|---|---|
| agents/zero | siso-agent-zero, siso-firstmate code (last, it is live) | siso-agent-zero-protocol (same thing) |
| agents/harness | siso-harness-lab + agent-hooks, agent-stack, agent-brain, agent-runtime | blackbox4, memories_v2, agent-base-sources |
| agents/work, team, adapters | project-os, project-team, agent-integrations | project-team-2, social-outreach x2 |
| agents/skills/{hub,playbook} | skills-hub, agent-playbook, jev-agent-skills | |
| estate/* | siso-estate, laptop-health, os-release, guest-gateway | codex-remote (after a security read) |
| packages/ui + products/ui-hub + library/banks/ui | siso-ui-hub, siso-ui-base, component-bank index, design-system | siso-ui-library, ui-shoot, ui-comps, actionist-components |
| products/os | siso-internal, siso-business-os, SISOCRM, siso-notes(+backend), task0001, operator-walk, siso-lifelock + siso-life code | siso-monorepo, siso-core-clean |
| products/siso-voice | siso-voice, freeflow | |
| hq | siso-agency, landing, sites, scratch | bdbt, demo-tool, 6 proof* |
| factory/* | software-factory, client-pack, frameworks, app-factory | ecommerce-app-factory (after fold) |
| industries/<x>/pack | bike-rental-template, restaurant-app | |
| library/* | great-library-of-siso, knowledge, foundry, people-graph code, repo-bank code, siso-knowledge, erdos | youtube-knowledge-engine |
| data blocks | transcripts (3 sets), UI/repo banks, people-graph-data, personal, runtime planes | |
Stays separate: HALO, client-owned (9), Fahmy, real forks (siso-code, listmonk, chat-widget; Plane/chatwoot only if
SISO-FORK.md shows real changes), compute-pool (own deploy), siso-city.
Flags before moving: encrypt siso-data-vps-services (WhatsApp DB, plain today); declare whatsapp-media, home-keepers,
held-backups in data-planes.json; scan c9fce36 (aws-creds files); stargate-library is PUBLIC.

## 9. Laptop
Always: root, seats/ (all, small), agents/, estate/, packages/, bin/, decisions/, hq/. Per seat cone: products/*,
factory/, industries/, library/. Data: only blocks a working seat asks for. Today's hot set (usage, 8 days):
harness, estate, siso-code (fork, own repo), ui-hub, agents/zero, people-graph.

## 10. Build order (each step leaves main green)
1. Spine: ADR 0023 (ids + seats) and 0024 (data); `module.json` schema + `bin/new|move|index|check`;
   module.json for the 62 imported modules; generated root index, README heads, CODEOWNERS.
2. Seats: `seats/` for the 24 seats from t3-seats.json; one generated registry; `bin/work <seat>` cones.
3. Gates: ratchet baseline, affected checks, boundary lint; lessons inbox + first replayed promotions.
4. Merge waves B and C per section 8 (bin/oss subdir support first); archive each source repo after cutover.
5. Data: families + age keys; encrypt vps-services; pin blocks in data/blocks.json; gardener nightly.

## Build log
- 17 Oct, step 1 DONE: sisodias/siso db11fc04. ADR 0023 (module ids, owners, layers) and 0024 (data blocks);
  `bin/siso where|owner|move|new|index`; 67 module.json; `.siso/{seats,tags}.json`, `moves.jsonl` (42 import rows);
  generated index in AGENTS.md; `bin/siso index --check` in `bin/check`. A full check needs `pnpm install` first
  (library imports siso-shell); a fresh clone without it fails library on main too.
- 17 Oct, step 2 DONE: sisodias/siso afd36a19. seats/<seat>/ for 19 SISO seats (seat.json + AGENTS.md with a
  generated owns block); `.siso/generated/seats.json` registry; `bin/siso work <seat>` cone worktrees (UI-HUB 274 MB
  of 909 MB). HALO and personal seats live elsewhere.
- 17 Oct, step 3 DONE: sisodias/siso eabef70a. `bin/siso affected` (uses derived from pnpm workspace members),
  `bin/check --affected` (quick loop; land stays full while Actions is off), `.siso/baseline.json` ratchet (empty).
  Real graph: 5 products use @siso/ui; no wrong-way edges (3 packages share the name @siso/tokens; only one is a member).
- Next: step 4. `bin/oss` subdir support (unblocks 10 held wave-A repos), then waves B/C and the merges; seats move
  HANDOFF/memory/source in; t3-seats.json generated from `.siso/generated/seats.json`.
