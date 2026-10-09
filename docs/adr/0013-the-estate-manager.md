# 0013. The Estate Manager owns the estate

Date: 2026-09-24 · Status: accepted

**Why:** Shaan, 24 Sep: "we should have a folder for a SISO estates manager, and the main agent zero knows that this guy exists ... he'd know exactly what the last state was".

**Decision:** The Estate Manager is a persistent seat whose house is this repo (`SISO_Agents/siso-estate`, sisodias/siso-estate). It boots with `estate brief`, works the queue, checks drift against these ADRs and improves them (a new or superseding ADR, never a silent change), and ends with `estate brief --mark` plus a State section in `.agents/HANDOFF.md`. Its skills are in `.claude/skills/`. It is Agent Zero's estate domain owner. Spin it up with `estate-manager`.

**Consequences:** Same seat on every machine; each machine's records are under `machines/<m>/`.

**How it is checked:** A cold manager can answer "what changed since last time" from `estate brief` alone.
