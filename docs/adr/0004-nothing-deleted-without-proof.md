# 0004. Nothing is deleted without proof, and every change leaves a line

Date: 2026-09-23 · Status: accepted

**Why:** Queue safety rule: "Never delete what isn't backed up, archived with a pointer, or proven rebuildable (clean + upstream holds HEAD)."

**Decision:** A deletion needs one proof: an identical copy stays (sha256), every commit is in a live GitHub branch (`ls-remote` + `merge-base --is-ancestor`), it is archived with a MANIFEST, or it is rebuildable (build output, caches, dependencies). Every move goes to `moves.jsonl`, every removal to `removed.jsonl`, every house/sync/retire to `houses.jsonl`/`retired.jsonl`.

**Consequences:** Proofs are re-run immediately before deleting (a check from an hour ago is not a proof). Anything held is left exactly as it was.

**How it is checked:** Each removed.jsonl line has a `proof`; `estate brief` shows removals since the last session.
