# What the estate feeds the Library (for LIBRARY and Agent Base)

Owner: ESTATE. Readers: Agent Base's Library space (`services/node/src/library.ts`) and LIBRARY's reader
(`Great_Library_of_SISO/reader/build.mjs`, branch `library-reader`). Coordination is through this file; nobody messages.

| Shelf | File | Made by | Fresh | What is in it (9 Oct 2026) |
|---|---|---|---|---|
| Built | `machines/register.json` (`buildings[]`) | `tools/register.py` | hourly (`tools/refresh.py`) and on any repo added or removed (`tools/watch.py`) | 285 buildings; description from GitHub, else the building's own AGENTS.md "In one line" or README (never for personal/, Life, WhatsApp, Fahmy); 58 still blank (GitHub-only folders with no description) |
| Live | `plan/surfaces.json` (`surfaces[]`) | `tools/surfaces.py` | hourly | 68 pages: all 62 Cloudflare Pages projects on the account (asked live via wrangler, with last deploy), linked to their building by deploy config or folder name (30 linked), plus Agent Base's seed rows for local consoles and apps |
| Works | `great-library-of-siso.pages.dev/catalog.json` | LIBRARY (`Great_Library_of_SISO`, `npm run build && npm run deploy:cloudflare`) | **stale**: generated 2026-09-21, deployed ~2 weeks ago; the local `site/catalog.json` is older still (10 Sep) | 51 Works. Not the estate's: LIBRARY rebuilds and deploys it |

## For LIBRARY's reader
- Projects shelf: read `machines/register.json` as you do; `description` is now filled for 227 of 285, so `b.description` is a usable
  fallback line. `lifecycle` and `provenance` are the estate's verdicts.
- A Live shelf, if you want one: `plan/surfaces.json`; each row's `path` is a register building path, `deployed` the Cloudflare age.
- Asks of LIBRARY (owner's call): rebuild and deploy the catalogue so Works is current; Agent Base shows its `generated_at`.

## Rules
- Never edit these files by hand: change the source (map, GitHub description, the building's door, Cloudflare).
- Private material never enters them: no paths' contents from personal/, Life, WhatsApp or Fahmy beyond the building row itself.
