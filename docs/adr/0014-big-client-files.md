# 0014. Big client files go to GitHub whole: unzipped, split, or in an intake house

Date: 2026-09-24 · Status: accepted · Refines 0002 (how a file over GitHub's limit is kept) and 0003 (one more name exception)

**Why:** Shaan, 24 Sep: "bulk client files they need to be pushed to github and sync to github github's our main syncing
plane". 0002 said a file over 100 MB is "unzipped or dropped/LFS". Dropping loses client material, and LFS spends a
bandwidth quota on every clone. The Fahmy block (job 14) had nine such files: Drive exports, Loom videos, a 169 MB
single-file HTML, and a client's original handover zip that its own README says to keep as received.

**Decision:**
- A file GitHub refuses (over 95 MB) is made fit losslessly with `tools/bigfiles.py`: a zip whose entries all fit is
  **unzipped** (every file checked against the zip's size and CRC-32, then the zip is removed); anything else is
  **split** into 90 MB parts plus `<name>.sha256`, and the folder's `SPLIT-FILES.md` says how to join them. The original
  is removed once the joined parts hash to it, unless it is an untouched original a README or the client asked to keep
  (`--keep`; the house then ignores it by path). A zip that is a copy of a kept folder, or a byte-identical second copy,
  is dropped with `bigfiles.py drop`. Every removal is proven and logged (0004). Nothing is dropped for being big.
- Parts are raw bytes, so `split` marks them `binary` in the folder's `.gitattributes`: git, and gitleaks reading git's diffs,
  skip them. A split **text** file is scanned on its own first: a single-file HTML with inlined images is one line of ~90
  million characters, which stalled the house's gitleaks for 30 minutes on 6 cores (24 Sep). Strip the `data:` URIs,
  scan the rest (`gitleaks dir`), then split.
- A client's intake (media and files received or downloaded, often GBs) is its own private house
  `sisodias/<block>-intake` at `clients/<block>/_intake/`, so the block's working house stays small enough to clone.
  This is the second name exception beside `code/`.
- A client block's root can itself be a house (`sisodias/<block>`) for its loose working files; the parent repo keeps
  tracking only `manifest.md` (the signpost, 0010), which the house ignores.
- Live service databases inside a block (Postgres, Redis, CMS sqlite and uploads) go in an encrypted data plane, never a
  git house: they hold client PII.
- A house bigger than 1.5 GB is pushed as a chain of commits of at most 1.5 GB each (`houses.py` rebatch), whose last
  tree is exactly the secret-scanned tree: GitHub refuses one push over 2 GB.

**Consequences:** a split file is not playable on GitHub; `cat` its parts to use it. A block house plus its intake
house replace one loose pile; siso-agency ignores `/clients/<block>/*` except `manifest.md`.

**How it is checked:** `bigfiles.py plan <folder>` lists nothing but kept originals; `estate census` loose-files and
too_big_for_backup lines drop by the housed amount; removed.jsonl has a proof line for every unzipped or split file.
