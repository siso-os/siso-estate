# 0008. Secrets never leave unscanned; keys live in the credentials store

Date: 2026-09-24 · Status: accepted

**Why:** Shaan, 24 Sep: "make sure ... all keys and stuff somehow are saved in SISO credentials ... we need a centralized place for all keys".

**Decision:** gitleaks gates everything that leaves the machine, on its own exit code (never piped before `&&`). A flagged piece of work stays local. Key files (`.env*`, `*.pem`, credentials json, `.npmrc`) and files the scan flags are copied to `~/SISO_Workspace/.credentials/projects/<map path>/` before anything is synced or removed; that folder is backed up encrypted (`sisodias/siso-data-credentials`). Browser profiles (cookies, logins) are never snapshotted. Allowlists are narrow (one rule, one path pattern, one secret shape) and tested both ways.

**Consequences:** Some work cannot go to GitHub until a human rotates or strips its keys; it is listed as held, never forced.

**How it is checked:** `estate brief` lists held items; `.credentials/projects/INDEX.md` names every parked file.
