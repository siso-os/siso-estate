# 0010. Loose work gets a house; a client is a block

Date: 2026-09-24 · Status: accepted

**Why:** Shaan, 24 Sep: "which one should be moved over into houses into the city or certain blocks".

**Decision:** A folder of real work with no repo becomes its own private repo (`houses.py house`). A client is a block: `clients/<brand>/manifest.md` is its signpost (tracked by siso-agency) and its houses are repos inside it (`code/` and named repos). The parent repo ignores each nested house by path. Rebuildable output and runtime state are gitignored, not housed.

**Consequences:** Blocks can be taken off a machine house by house; the manifest says where each house is on GitHub and how to bring it back.

**How it is checked:** `estate census`: files in no repo only in live runtime folders; `tools/piles.py` shows every pile with a verdict.
