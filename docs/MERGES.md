# Which repos to merge: from about 160 live repos to about 70

Status: proposed 2026-09-25 ~18:40 by the legend session, at Shaan's ask: "what repos are worth merging ... from first
principles ... cut down that repo list to make it easier to understand ... now that you understand ... how they
interlink". It builds on `docs/LEGEND.md` (the estate mirrors the business). Nothing is merged yet. Each family moves
with its keeper, and every item marked **read first** is inferred from names and one-line descriptions, so it needs
reading before it is merged.

## 1. When a thing gets its own repo
A repo is the unit of four things: **history, access, release and keeper.** A thing gets its own repo only when one of
those differs from its neighbour:

1. **Access:** who may see it. Public vs private, a partner's own code (Cam's), or a client handover.
2. **Release:** it ships or runs on its own: a Mac app, a VPS service, a public package.
3. **Upstream:** it is a fork that keeps pulling from its original project.
4. **Keeper:** a different seat works in it every day, and sharing a repo would make their lanes collide.

Anything that serves the same part of the business and changes together is **one repo with folders**. Four rules follow:

- **A backup is not a building.** A folder inside a building is backed up by committing it to that building, or to its
  data plane. It never gets its own repo.
- **Records about a thing live with the thing.** Ledgers, design explorations, reviews and archives go in the
  project's repo, not in sibling repos.
- **Finished work is archived on GitHub, read-only.** It keeps its plot on the map, but it is not a live repo.
- **At 1000× the repo count grows only with partners, clients and deployables.** It never grows with folders,
  backups or records.

## 2. The count (measured 25 Sep)
- 234 sisodias repos on GitHub: 53 archived, 181 not.
- The register counts 162 live or recent repos of ours (it leaves out archived and foreign ones).
- **61 of the 234 are backup or recovery mirrors, or estate "houses"**: 27 archived, 34 not. Descriptions like
  "Backup of ~/…", "Private recovery mirror", "(SISO estate house, 2026-09-24)". The 30 Aug workspace-sync recovery
  made some; the estate's own `backup.py --create-repos` and `houses.py` made others on 23-24 Sep. Some are only
  subfolders of an existing repo:
  - `bykonzyard-client-feedback-2026-09-06` is `bykonzyard/docs/reviews/…`;
  - `actionmodel-project-os-adapted` is `actionmodel/runtime/…`;
  - `space-agent-l1-admin` and `space-agent-l2-admin` are folders under `~/.claude/extensions`.

  "Nothing lost" was kept by turning folders into repos, so the cleanup itself grew the list.

## 3. The merges, by part of the business

### The backups and mirrors: 34 unarchived → 6 live (lowest risk, do first)
**Count, 26 Sep 04:30: 8 of 34 left on GitHub** (the 6 keepers plus 2). Every other mirror below is in `siso-vault`
with its GitHub original deleted (`machines/laptop/removed.jsonl`); the umami pair is already one repo;
`melanotresses-site-dev` went last: folded 04:20 (5 refs, 30.1 MB, verified; its `public-handoff` branch is also in
melanotresses-site as `site-dev/public-handoff`), GitHub original deleted 04:32 by `vault.py delete` (refs re-checked).
Left, on purpose: `siso-reflections` is the live checkout `personal/team-entrepreneurship` with uncommitted edits and
10 local branches, so deleting its GitHub home would end its nightly backup: it keeps its repo until that work is
committed or Shaan retires it. `fahmy-2026-08-intake` (3.2 GB of client media) becomes a data plane: a job of its own.
Six of the 34 became real work and keep their repo (`SISOCRM`, `siso-inbox-chatwoot`, `trading-for-dad`, `kellman`,
`fahmy-2026-08`, one `bykonz-umami`). One becomes a data plane. The other 27 fold into a building or are archived.
- **Folders of a building go back into that building,** then the mirror is archived:

  | Mirror | Goes into |
  |---|---|
  | `bykonzyard-client-feedback-2026-09-06` | bykonzyard-site `docs/reviews/` |
  | `actionmodel-project-os-adapted` | actionmodel-private |
  | `melanotresses-site-dev` | melanotresses-site |
  | `provider-compliance-spec-portal` | the provider-compliance block |
  | `amcon-resource-hub` | business-to-government |
  | `trader-platform-research-site` | the Library work trader-platform-research |
  | `people-graph-recovery-pgpre`, `-pgclean` | the people graph's data plane |
  | `space-agent-l1-admin`, `-l2-admin` | the agent stack's extension, or the vault |

- **Mirrors of finished client work are archived:** the Lumelle four (`lumelle-tracker`, `lumelle-theme`,
  `lumelletesthydro`, `Luminelle-Partnership`), `construction-rc`, `bike-rental-template`, `patchwork-store`,
  `tour-guides`, `thehrworld`, `visa-run-da-nang`, `siso-reflections`, `first-principles`.
- **Mirrors of live work join the live repo:** `siso-app-factory` goes into the software factory (below), and the two
  `bykonz-umami` copies become one fork.
- **Houses that are real work keep their repo:** `fahmy-2026-08` (it becomes Fahmy's hq), `trading-for-dad`, `kellman`.
  `fahmy-2026-08-intake` is client media, so it becomes a data plane.
- **Root cause, fixed in the estate:** `backup.py` and `houses.py` commit a folder inside a building to that building,
  or to its data plane, instead of creating a repo.

### Partners
- **HALO's Oracle: 7 → 3** (keeper STREAMING; on the never-list, so its go first).
  - Keep the two deployables: `oracle-core` (the Mac app, the VPS app, shared) and `oracle-operator` (the operator web
    app).
  - `oracle-streaming`, the project gate, absorbs the three repos that are records about Oracle: `oracle-project`
    (agent tasks, briefs, run evidence), `oracle-uihub` (design reviews) and `oracle-archive`.
  - The old monorepo `sisodias/oracle` is archived when the migration finishes.
- **HALO, the rest.**
  - `alex-dashboard` and `alj-dashboard` become one (**read first**).
  - `halo-buzz` and `_reference/buzz` (the one running on siso-vps) become one adopted fork.
  - `siso-agent-alj_ofm` moves from SISO hq to HALO.
  - `halocrm` is Cam's code and is never merged.
- **Fahmy's agency: 12 → 5** (keeper BYK).
  - `fahmy-2026-08` becomes the partner's hq.
  - `bykonzyard-site` absorbs `bykonz-scout-review`, the client-feedback mirror and `preview-gallery`.
  - `bykonz-admin` keeps its own repo because it deploys on its own (**read first**). So does `bykonzyard-agents`,
    which runs on siso-vps.
  - `bykonz-umami` becomes one fork.
  - `mygumm-site` stays, with `mygumm-commerce` kept as its Medusa fork.

### SISO's clients
- **Action Model: 7 → 2, plus SISO's share.**
  - `actionmodel-private` is the client workspace.
  - `actionist-base` and `actionist-appsdk` are the Actionist product, as one repo (**read first**).
  - SISO's reusable parts leave the client:
    - `actionist-blocks` (the repo-to-block catalog) and `autosaas` go to the factory (`actionist-blocks` was an empty
      GitHub repo: deleted 26 Sep, its README kept in `clients/actionmodel/docs/readmes/`);
    - `actionist-components` (curation over the 21st.dev corpus) goes to the Library's UI bank.

  Shaan: "those frameworks are really important for SISO Agency".
- **MelanoTresses: 3 → 1** (the `-dev` and `-public` copies become branches or deploy targets). It moves under Fahmy.
- **Five-star-hire: 2 → 1, archived.**

### SISO's products and control room
- **SISO Internal Labs: 5 → 3.**
  - The Plane fork `siso-internal-labs` stays because it has an upstream.
  - `siso-internal-labs-mac` stays because it releases on its own.
  - `-server` (infra, runbooks) and `-agents` (the lab's agent records) become one ops repo.
  - The Chatwoot copy inside it goes.
- **Notes and knowledge: 4 → 2** (**read first**).
  - `siso-docs` (the AFFiNE fork) stays a fork.
  - `siso-notes`, `sisonotes-backend` and the embeddable `siso-knowledge` module become one SISO notes repo.
- **CRM: one compound.**
  - `SISOCRM` is the kernel.
  - The Teable fork, which appears twice, becomes one.
  - The Twenty fork and the running instance (`hq/infra/twenty`) sit under it.
- **The client platform absorbs its modules.** `siso-event-ticketing` joins it. `siso-booking-kit` and
  `siso-chat-widget` are adopted forks: keep them only if SISO changed them; otherwise they go to the Library's study
  bank.
- **Agency sites: 2 → 1.** `agency-landing` and `siso-agency-lp` are both landing pages (**read first**).
- `siso-internal` and `siso-lifelock` ("Shaan's personal daily operating system", migrated from the old internal app):
  check whether `siso-internal` absorbed it (**read first**).

### The factory: 7 → 2
- `siso-agency-software-factory` absorbs:
  - `siso-app-factory` (its preservation mirror);
  - `ecommerce-app-factory` (a distillation of it);
  - `actionist-blocks`.
- Its `modules/` copies of the Library's repo bank and of the agency frameworks go: it uses the Library instead.
- `siso-agency-frameworks` stays its own repo only if it is released as a public method. Otherwise it merges.

### The UI knowledge: 4 banks over one corpus → 2
Four repos curate the same 21st.dev corpus:
- `siso-component-bank`: 8,538 components, public;
- `siso-ui-base`: curated picks, principles, palettes, the brief tool;
- `siso-design-system`: "3,580 UI components with visual picker, ranking, curation tags, MCP server";
- `actionist-components`: "curation, working set and taste surface over the 21st.dev corpus".

After the merge:
- the public raw corpus stays `siso-component-bank`;
- one private bank, `siso-ui-base`, absorbs `siso-design-system` and `actionist-components`. Shaan's notes on each
  component are its taste layer;
- `siso-shell`, the page templates, stays.

### The agent side: 27 → 11
- **The public distribution becomes one public monorepo:** `siso-agent-stack` with `packages/`.
  - It takes in hooks, runtime, playbook (and `-public`), integrations, session-intelligence, project-os, and the
    `siso-agent-zero` package.
  - The installer exists only to pin commits across nine repos, so one repo removes its job.
  - The public/private split stays: the live lab stays private.
- **Skills: 3 → 2.** `jev-agent-skills` stays its own repo: it is a public MIT release installed by URL (`v1.3.0`), and
  the hub's `jev-judgment` is already byte-identical to it (§1's release exception; fan-out 26 Sep). The 23 Jul snapshot
  `siso-skills` is archived.
- **Agent Zero: 4 → 1.** `siso-firstmate` absorbs `siso-agent-zero-protocol` (the ledger and handoffs). The pool's
  Agent Zero joins compute.
  **26 Sep: held.** `siso-agent-zero-protocol` is live: launchd `com.siso.usage-tracker` (az-usage collect) and
  `com.siso.az-now` run from its `bin/`, it has two worktrees in `_data/worktrees/`, and uncommitted edits. Its owner
  moves the jobs to firstmate first; then the fold. The pool's Agent Zero is done (below).
- **Compute: 4 → 1.** `siso-compute-pool` absorbs `siso-worker-node` (its field guide), `siso-loop` (the free worker
  tier) and `siso-pool-agent-zero`.
  **Done 26 Sep:** `loop/` (047c46d), `agent-zero/` (9a84975, after the laptop's 3 unpushed commits were merged into
  its main) and `worker-node/` (4e37739, after a recorded Kaggle token was scrubbed from its history with filter-repo),
  each by subtree with history; each original is in `siso-vault` `vault/agents/` and deleted from GitHub; the local
  checkouts are in `_archive/2026-09-26-a6-one-of-each/`.
- **DevSpace: 2 → 1.** `devspace` becomes the adopted fork and takes in `siso-workspace`'s history.
- **Archived:** `siso-os`, `siso-agent-base`, `siso-agent-base-pack`, `first-principles`, `catgpt-gateway`.
  **26 Sep:** `siso-os` done: vaulted (140 refs, 179.7 MB in 2 parts), its ignored files in the data plane
  `siso-data-siso-os`, keys in `.credentials/`, GitHub original deleted, checkout in `_archive/2026-09-26-a6-one-of-each/`.
- **Moves to the Library:** `siso-librarian`, the Library's standing agent's worklog.
- **Stay:** `siso-harness-lab`, `siso-agent-brain` (a service), `siso-estate`, `siso-city` (the map), `siso-project-team`,
  `camofox-browser` (adopted).

### The Library
- **The Erdős work: 5 → 1. Done 26 Sep 04:20 (fan lane merges-products).** `siso-unsolveable-mathematics` absorbed
  `erdos-23`, `erdos-647`, `erdos-742` and `erdos-848` as `targets/erdos-N` by `git subtree add` (history kept; main
  `7a46b1a`). Each original is bundled in the vault (`library/`, verified ref for ref) and archived on GitHub; the four
  local checkouts are gone (lines in `machines/laptop/removed.jsonl`), their placements removed.
- **Finished works are archived read-only and stay catalogued:** book library, connector research, declassified
  records, youtube knowledge engine.
- **Industries** are one idea in four places: `siso-industry-packs` (the public research), `hq/industries` (SISO's
  verticals), Action Model's 17 industries, and `superapp-forge`. The verticals in `hq/industries` are the source; the
  packs are their published form.

### personal
- `polymarket-research` goes into `trading-for-dad` (the third Polymarket folder, `personal/apps/polymarket`, has no
  repo).
- `Apollo-Website` and `siso-reflections` are archived, or join the coursework repo.

## 3a. Checked against the disk (25 Sep, evening)

- **Agreed: the Erdős lanes, 5 → 1.** Each `erdos-N` repo is 3 files and 3 commits (README, AGENTS, CLAUDE); the
  programme repo already holds the campaigns under `docs/campaigns/`.
- **Agreed: the agent packages into `siso-agent-stack`.** Hooks, runtime, playbook, integrations, session-intelligence,
  project-os and siso-agent-zero have 3-10 commits each, and every commit since their extraction is doorkeeping. They are
  public, so the merge changes public URLs: Shaan's go.
- **Corrected: the factory's `modules/` are not duplicate repos.** They are git submodules of the Library's banks
  (`siso-repo-bank`, `siso-agency-frameworks`): the same GitHub repos, checked out twice. The GitHub count does not drop.
  The real fault: both checkouts have **forked**. They carry commits that are not on GitHub main (repo-bank 2 ahead and 3
  behind, agency-frameworks 1 ahead and 4 behind), made by the estate's own fix policy and repoint. The fix: the estate
  never commits inside a submodule checkout, and the two modules are reset to origin once their commits are shown to be
  doorkeeping already on main.
- **Corrected: `siso-design-system` is not a fourth copy of the 21st.dev corpus.** It is SISO's own component system,
  harvested from client apps (Lumelle's tokens, tiered primitives, composites, systems and adapters), plus a legacy
  `library/21st-dev` copy that `siso-ui-base/registry/curated/add.mjs` reads. The legacy copy joins `siso-component-bank`;
  the tiered kit stays its own thing and feeds the factory. ui-base stays the curated taste layer.
- **Reversed (ESTATE, 25 Sep ~19:45): the agent packages stay separate.** `siso-agent-stack` is by design a
  distribution composed from pinned public repos: `stack.manifest.json` lists each component's repository and revision
  and the installer clones them. Folding them into `packages/` means rewriting the installer and breaking public URLs,
  and runtime, playbook and project-os carry live uncommitted work (16, 20, 8 files). The count stays; each is a release unit.
- **Reversed (ESTATE, 25 Sep ~19:45): the design system's `library/21st-dev` stays.** It is byte-identical to
  `siso-component-bank/legacy` (0 diffs, 17,135 files each), but the design system's own `viewer/` (`lib/registry.ts`
  reads `../library/manifest.json` and the files beside it) and `mcp/` read it; `add.mjs` was only one of its readers.
  Removing it saves 86 MB and no repo. Revisit only if the viewer is retired.
- **Raw activity counts were inflated by the estate itself.** The lifecycle labels (active, warm, dormant) already
  ignore the estate's commits (`tools/code.py`), but the register's `commits_14d` and GitHub's `pushedAt` did not: the
  nightly backup pushes every repo daily, so `pushedAt` says nothing about work. Fixed 25 Sep: `commits_14d` now counts
  work commits only.

## 3b. The vault is running (25 Sep)

`sisodias/siso-vault` exists (private). `tools/vault.py fold NAME --part PART --run` mirror-clones a repo, writes it as a
git bundle (every branch and tag), checks the bundle's refs against `git ls-remote` ref for ref, adds a row to
`INDEX.md` and pushes. Restore drill passed (construction-rc: all branches back, HEAD equal to GitHub). Folded: 12 repos
(construction-rc, tour-guides, lumelle-tracker, lumelletesthydro, thehrworld, visa-run-da-nang, five-star-hire-overlay,
amcon-resource-hub, people-graph-recovery-pgpre/-pgclean, first-principles, siso-app-factory). The nightly skips any
repo the vault holds (`tools/backup.py`), so a deleted original is never re-created. **The 12 originals still exist:
deleting them is Shaan's go.** Next folds: bundles over ~90 MB (Luminelle-Partnership, siso-reflections, patchwork-store,
preview-gallery, bike-rental-template, ghostty) need a data plane; the nested mirrors of live buildings
(trader-platform-research-site, provider-compliance-spec-portal, actionmodel-project-os-adapted, workspace-sync,
bykonzyard-client-feedback, space-agent-l1/l2) fold into their parent building instead.

## 4. After the merges: about 70 live repos

| Part | Live repos after |
|---|---|
| SISO hq | 2 |
| Control room | 4 |
| Products (including forks) | about 10 |
| Factory | 2 |
| Direct clients (live) | about 4 |
| HALO | about 10 |
| Fahmy | about 7 |
| Agent side | 11 |
| Library | about 14 |
| personal | about 4 |

Everything else is archived read-only on GitHub, with its plot on the map and a README pointing to where its content
went. Nothing is deleted.

## 5. How a merge runs (the estate's tools, one keeper at a time)
1. **Fold:** `git subtree add --prefix=<folder> <satellite-url> <branch>` into the main repo, so the history comes
   along. Push, then `gh repo archive` the satellite with a README line pointing to its new home. Move the map plot.
2. **Archive** finished work: `gh repo archive`. The plot turns dark.
3. **Never merged:** forks with a live upstream, Cam's code, and anything across a public/private line.
4. **Order:**
   1. the mirrors, which nobody uses, so there is no risk;
   2. the dark families (Lumelle, MelanoTresses, Erdős, the old factories);
   3. the live families with their keepers: Oracle (STREAMING), Fahmy (BYK), the agent side (the agent stack), the
      UI banks (the Library lane).
5. **After each merge:** repoint the paths, update `plan/owners.json`, run `estate register`. The door's legend line
   follows.

## 6. The vault: one repo for finished work (Shaan, 25 Sep ~19:00: "one big archive ... one repo which has all of the sub repos of archives")
Archiving on GitHub keeps each finished repo in the list: 53 today, rising to about 110 after these merges. From first
principles, finished work needs three things: its history kept, a way to find it, and a way to bring it back. None of
those needs a separate repo per project. So:

1. **One private repo, `sisodias/siso-vault`.** Each finished repo is folded in with its history as
   `vault/<part-of-business>/<name>/`:

   ```
   git subtree add --prefix=vault/<part>/<name> <repo-url> <default-branch>
   ```

   `<part>` follows the legend: partners/halo, partners/fahmy, clients, products, factory, agents, library,
   personal, legacy. A repo too large to fold (over about 1 GB of history, or with LFS) goes in as a `git bundle`
   into the matching encrypted data plane, and the vault holds a pointer file to it.
2. **A generated index, `vault/INDEX.md`:** name, what it was, the part of the business, the last commit, the fold
   commit, and the command that brings it back (`git subtree split` or `git clone <bundle>`).
3. **Only after the vault is pushed and checked** (the fold commit is on GitHub, and a fresh clone reproduces the
   repo's tree at its last commit), the original GitHub repo is deleted. The map plot points into the vault. Deleting is
   Shaan's go per batch: the first batch is the 27 backup mirrors.
4. **The laptop's `_archive/` is the vault's local side:** the same `vault/<part>/<name>` layout for folders that were
   never repos. It is already backed up by the data planes.

Expected result: GitHub goes from 234 repos to about 70 live plus 1 vault.

## 7. Other placement fixes found on the same pass
- **`_reference/` dissolves.**
  - The code we study becomes a Library bank: `banks/code-references`, holding the 11 architecture exemplars and the
    study clones.
  - buzz, tokentracker and orca run on siso-vps, so they are adopted where they serve (buzz: HALO, which builds on it;
    tokentracker and orca: the agent side).
  - ghostty, MiroFish and cannaroute go to the study bank or the vault.
- **Forks sit under the product they serve,** not as loose products:
  - Chatwoot, Listmonk, Teable, Twenty, booking-kit and chat-widget sit under the client platform or the CRM compound;
  - the Plane fork sits under Internal Labs;
  - AFFiNE sits under notes.
- **The control room goes to `hq/`:** `siso-internal`, `siso-internal-labs` and the console. Only one of them is the
  UI hub where agents' work lands (still Shaan's call; the default is Internal Labs).
- **SISO's own business agents** (`hq/agents/*`: agency_dash, agency-pm, builder, cto, growth-pm, ...) stay HQ's roster.
  `siso-social-outreach-agents` joins them. The agent side keeps only infrastructure.
- **Industries are one idea in four places,** so `hq/industries/<vertical>/` becomes the source. Each vertical holds
  its pilot partner, its modules and its research pack; `siso-industry-packs` is the published form, and Action Model's
  17 industries and `superapp-forge` feed it. `hq/industries/model_management/upper-echelon-clone` is a study clone,
  so it goes to the Library's code bank.
- **Dark leftovers go to the vault:** `dispo` (a Bangkok cannabis concierge PWA, on no map), `codex-remote`,
  `siso-notes` (if the notes merge proves it is superseded), the 11 GitHub-only plots, and `siso-agent-base` and its
  pack.
- **personal:**
  - `agents/court` goes to `legal/`.
  - `siso-voice` (freeflow) stays personal unless it becomes a product.
  - `math-bounties` gets a private repo.
  - `tools/kaneo-worktrees` goes to `_data/worktrees`.
- **Root cause, fixed in the tools:** `backup.py --create-repos` and `houses.py` stop creating repos for folders inside
  buildings. `estate new` refuses a repo that fails §1 (no difference in access, release, upstream or keeper).
