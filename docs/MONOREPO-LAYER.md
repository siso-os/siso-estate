# The monorepo layer (17 Oct 2026, rewritten after the full push)

Shaan, 16 Oct: "every single thing on my laptop to be synced to github, mapped out on the estate, and then we can figure
out how it maybe gets moved into its monorepo location." Step one is done (17 Oct): everything is on private GitHub.

## Two repos, two jobs (no second monorepo)
- **`sisodias/siso` is THE code monorepo** (ADR 0021/0022 there, its own owner thread, `bin/land` trunk flow,
  `migration-20261016.json` = one row per import, history kept by `git subtree`). SISO's own code goes there.
- **`sisodias/siso-city` (`~/SISO_Workspace`) is the map**: every repo and data plane at one path, on every machine. It
  carries what must NOT enter the code monorepo, pinned by a lock file.

## What we own (GitHub sisodias, 17 Oct: 339 repos, 53 archived)
| Kind | Live repos | GB | Monorepo? |
|---|---:|---:|---|
| SISO code (agents, tools, apps, products) | ~212 | 65 | YES: imported in waves by the monorepo owner; source repo archived after import |
| Data planes (`siso-data-*`, encrypted) | 25 | 30 | NO: data, `bin/get` blocks once restore proof exists |
| Personal (`personal-*`, WhatsApp, home) | 26 | 18 | NO: private life, never in a code repo |
| Client / agency sites | 14 | 5 | Code that is ours goes to `products/`/packs; client-owned code stays its own repo |
| HALO | 6 | 4 | NO: HALO-AGENCY org at HALO's own monorepo (ADR 0007) |
| Triage / intake / `-mini` snapshots | 3+ | 3.5 | NO: archive; fold into its real home or leave archived |

## What goes in, what stays out (industry practice, with sources in `/tmp/monorepo-research/notes.md`)
- In: code we write and release together. Out: other people's code (`_reference`), client-owned code, data and binaries,
  secrets. Google, Meta and Dagster's hub-and-spoke model all keep one repo for own code and pin or mirror the rest.
- Public modules are exports from the private monorepo (`estate/os-release`, Copybara-style), never edited in the mirror.
- Agents: root `AGENTS.md` holds pointers only; each package has its own; Claude Code's large-codebase guide: launch in
  the package dir, `sparsePaths` / `claudeMdExcludes`, deny generated and vendored dirs.
- Import with history (`git subtree add` or `filter-repo --to-subdirectory-filter` + merge), never a squash copy.

## The estate's part (what this repo builds)
1. **Classify every repo** in `plan/monorepo-plan.json`: `import` (wave n) | `keep-separate` (why) | `archive`. Feed the
   monorepo owner's migration rows from it; the estate never imports itself.
2. **After an import lands**: archive the source repo, `estate pull` the old checkout off every machine, map points at
   the monorepo path (as done for siso-agent-hooks, e0fe2e8).
3. **Machine profiles** (`plan/machines.json`): `core` = the monorepo (sparse) + siso-city; extras per machine.
4. **Lock** (`plan/estate.lock.json`): one row per keep-separate repo (path, remote, commit, pushed); nightly after
   backup; `estate restore --lock <sha>`. The monorepo itself is one row.

## Clearing the laptop (runs now, independent of the monorepo)
Every repo is on GitHub, so a checkout nobody is working in comes off with `houses.py retire --run` (verifies GitHub
first, leaves an empty placeholder, `estate restore` brings it back). Order by size from `docs/STORAGE-MAP.md`:
SAFE COPY first, then cold REBUILDABLE; WORKING checkouts stay.
