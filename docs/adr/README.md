# Architecture decisions: the estate's primitives

Each ADR is one rule the Estate Manager enforces and may improve. To change one, write a new ADR that supersedes it
(set the old one's status to `superseded by NNNN`); never edit a decision silently. Shaan's own words (`.agents/source/`)
outrank every ADR.

| # | Decision | Status |
|---|---|---|
| [0001](0001-one-map-for-every-machine.md) | One map for every machine | accepted |
| [0002](0002-github-is-the-sync-plane.md) | GitHub is the sync plane | accepted; big files refined by 0014 |
| [0003](0003-one-place-per-repo.md) | Every repo has exactly one place | accepted; intake-house name exception in 0014 |
| [0004](0004-nothing-deleted-without-proof.md) | Nothing is deleted without proof, and every change leaves a line | accepted |
| [0005](0005-no-compat-links.md) | No compatibility links; moves repoint their consumers | accepted |
| [0006](0006-worktrees-in-data.md) | Worktrees live in _data/worktrees/<repo>/<lane> | accepted |
| [0007](0007-halo-never-copied.md) | HALO code is never copied; the HALO block maps to the HALO VPS | accepted |
| [0008](0008-secrets-never-leave.md) | Secrets never leave unscanned; keys live in the credentials store | accepted |
| [0009](0009-other-agents-work-is-theirs.md) | Other agents' work is theirs | accepted |
| [0010](0010-houses-and-blocks.md) | Loose work gets a house; a client is a block | accepted |
| [0011](0011-retire-flow.md) | Taking a project off a machine | accepted |
| [0012](0012-measure-inside-the-buildings.md) | Clean means the census reads zero | accepted |
| [0013](0013-the-estate-manager.md) | The Estate Manager owns the estate | accepted |
| [0014](0014-big-client-files.md) | Big client files go to GitHub whole: unzipped, split, or in an intake house | accepted |
| [0015](0015-the-hidden-estate.md) | The hidden estate: one agent folder per repo, harness folders hold config only, tool state never tracked | accepted |
| [0016](0016-the-building-code-and-the-operations-centre.md) | The building code and the operations centre: every repo scored, the whole city on one live page | accepted |
| [0017](0017-law-and-land-titles.md) | Law and land titles: every building has an owner; routine fixes are written law the estate applies itself | accepted |
| [0018](0018-the-estate-model.md) | The estate model: a territory of islands, compounds and buildings, one register, documents that keep themselves | accepted (estate); proposals to other owners |
