# 0018. The estate model: a territory of islands, compounds and buildings, one register, and documents that keep themselves

Date: 2026-09-25 · Status: accepted for the estate's own records, tools and vocabulary; the changes it asks of other
owners (the siso-project-os template, the Library's Agent Stack assembly, Agent Zero's project list) are proposals to
them · Builds on 0001–0017

**Why:** Shaan, 25 Sep: "Maybe instead of the island analogy ... the whole land, plot a land, including the water is the
SISO estate"; "this is more about an AI agent being able to understand it and reason"; "I still want the reasoning in
terms of the actual core principles of the estate and first principles"; and "it relies on an agent to go through and
change it, rather than somehow code changing it". The first 17 ADRs set rules one problem at a time. There was no single
model of what the estate is, so every tool and list invented its own:
- five project lists;
- three shapes for a multi-repo project;
- adopted open-source products filed as "foreign";
- docs whose cited paths rot (up to 124 of 784) because only an agent keeps them true.

**Decision:** the estate is modelled as `docs/MODEL.md` says, and `plan/model.json` is the machine-readable contract:
- The estate is a territory of land (where things are kept) and water (how they move). Islands are the top-level
  folders, each with a law and a governor. Districts exist only where they are needed. Compounds are projects (a
  gatehouse repo from the second building on). Buildings are repos on plots. Rooms are worktrees in `_data`. Sites are
  machines that build plots from the one plan.
- A building's postcode is its GitHub name. It never changes; addresses are derived from it.
- Provenance is ours, adopted, client or foreign. Running or changing foreign code adopts it.
- One register holds all of it. Every list, map, page and render is generated from the register, by a generator wired
  to the change it depends on.
- Every document block is generated, checked or a journal (invariant I11).
- The eleven invariants in `plan/model.json` are what the register enforces.

**Consequences:** the next build is the register. Until it exists, the invariants marked "to build" are unchecked, and
this ADR says so rather than claiming them. Estate tools and pages use the model's words. When one of them disagrees
with `docs/MODEL.md`, it is fixed or MODEL.md is superseded by a dated revision. The island spec page
(`plan/city-2050.json`, v2) is superseded where it differs: v2 made islands mean jurisdiction, while v3 makes islands
the top-level folders and carries law on islands and compounds.

**How it is checked:** today by the building code (I3, I6 in part), the backup's NEVER_COPY and gitleaks (I8), and births
(I10, measured only). From stage 1 on, by the register's own doctor, which fails on any invariant it can test.
