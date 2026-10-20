# Harvest preservation tools (code only)

Owned by existing module `siso.estate.core`. Original sources, pins, Git modes,
SHA mapping, allowlist and adaptations are in `import-receipt.json`.
No source payload, source history or new module is imported.

Runtime: Python 3 stdlib, Git, gitleaks with its `git` command, and access to the
configured GitHub origin. Use absolute paths for all inputs, scratch and receipt
directories. Scratch directories must already exist. All plans, requests, reports,
ignore fingerprints and source payloads remain external to this code directory.
Do not run these scripts against live owners merely to test an import.

- `python3 snapshot.py PLAN MACHINE RUN SCRATCH [--push]`: PLAN is a JSON array
  of `{ "path": "/absolute/owner/repo" }`. Repositories need HEAD, an existing
  index and an observed GitHub origin tip available locally. Without `--push`,
  writes only rescue objects and external receipts. `--push` publishes only after
  scan/size/source stability gates. No source ref, owner index or files are written.
- `python3 push-rescues.py RUN`: consumes only `scanned-ready` snapshot receipts
  with scan exit 0 and unchanged index proof; rechecks current HEAD/index before
  publishing the recorded commit. This command really pushes; it has no dry run.
- `python3 sanitize-rescues.py RUN SCRATCH REQUESTS`: REQUESTS is an array with
  `lane`, optional `paths`, `prefixes`, `restore_roots`, `drop_findings`, `reason`.
  Requires the corresponding snapshot receipt, candidate Git objects and remote
  tips. Rebuilds/rescans a candidate without publishing. Historical
  `allow_verified_digest_findings` is explicitly held: its Oracle-specific
  exception is not a portable validation rule.
- `python3 archive-artifacts.py CONFIG SCRATCH OUT`: CONFIG is an array of packs
  with `name`, absolute `repo`, `parent` commit, `files` (`source` absolute regular
  text file, repository-relative `target`, optional `report`, default true), and
  optional `allow_digest_findings`. This command really pushes after its scan and
  index gate; it has no dry run. Supply only trusted task-owned pack targets.

The original `harvest/2026-10-06/` ref namespace and rescue messages are retained
for existing receipt compatibility. These are historical preservation helpers,
not a newly adopted scheduled service. They create Git objects even on a hold;
source and rejected artifacts remain in their original locations. Multi-row
commands communicate holds/errors in per-row JSON receipts and may exit 0;
consumers must inspect `status`, `secret_scan_exit` and `remote_verified`.

Check: `PYTHONDONTWRITEBYTECODE=1 python3 test_synthetic.py`. Uses only temporary
synthetic owner repositories, a fake remote and a fake scanner. Proves scanner
failure prevents push, exclusions preserve source, owner HEAD/index/files stay
unchanged, sanitize/archive fail closed, and a scan-time source mutation is held.
This does not validate real GitHub publication or real artifact archival.

Parent integration: append a code-only snapshot record using the receipt's full
source pin, file-level source/destination blobs, allowlist and exclusions. Record
the actual integrated destination commit; do not assert subtree ancestry or
retire the external source. Reuse the existing module id; no index/ledger/move
writes were made here. Run catalog, layout and dependency gates in a full checkout.
