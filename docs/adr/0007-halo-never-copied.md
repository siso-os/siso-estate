# 0007. HALO code is never copied; the HALO block maps to the HALO VPS

Date: 2026-09-24 · Status: accepted

**Why:** Shaan: HALO code lives only on camronkellman/halocrm. 24 Sep: "we need a halo agency folder ... this will be the part which maps over to the halo vps".

**Decision:** Nothing under a HALO path is pushed to sisodias, bundled into the map, or synced (`backup.py` NEVER_COPY, `houses.py` HALO refusal). `SISO_Agency/clients/halo/` is the HALO agency block; its repos map to paths on `halo-vps`. The private map itself is never checked out on a box a client holds root on. Moves on HALO ground wait for the HALO lead's go (CRM seat).

**Consequences:** Client data exports (cam-laptop-*, convex-export-*) are never committed anywhere.

**How it is checked:** grep backup.json/houses.jsonl for halocrm: no pushed refs; `gh repo list sisodias` has no HALO code copy.

## Amendment, 2026-09-27: HALO's databases are backed up; HALO is one project repo

**Why:** Shaan, 27 Sep: "the database is saved in this halo project you know like remember the infrastructure where you
have like one project repo and everything's git moduled ... just solve it" (verbatim in `.agents/source/2026-09-27-halo-one-project.md`).
Until then `halo_live`, the CRM, had no scheduled backup and nothing left the box.

**Decision:**
- HALO's databases may be backed up **encrypted** into private sisodias data repos: all six (halo_live,
  halo_oracle_convex, twenty, halo_affine, plane, buzz) plus Oracle's Convex zips, nightly, into
  `sisodias/siso-data-halo-databases` (7 daily and 4 weekly copies). This is client data, not Cam's code, and it goes only
  into an age-encrypted plane (ADR 0014). **Cam's code still never leaves camronkellman/halocrm.**
- The pipe runs from the client box, which only drops files. halo-vps dumps into a root-only outbox
  (`halo-estate-dump`), and `oracle-offbox-backup` copies it to siso-vps as user `halo-backup`. That key is
  `restrict` + `rrsync -wo -no-del -munge`, and its `.ssh` is root-owned, so it can only add files to one folder.
  siso-vps verifies the checksums, keeps the retention set in a root-only store, and pushes the plane. HALO never holds
  a GitHub credential or the age identity. Sources: `tools/halo/`.
- `sisodias/halo` is the HALO project repo. It pins every HALO repo as a submodule at its path. A pin to Cam's halocrm
  is **a URL and a commit, not a copy**: no object of his leaves his repo. The data repos are pinned with
  `update = none` and `branch = main`, because each push is an orphan commit and a pinned SHA can vanish.

**How it is checked:** `machines/vps-siso/halo-ingest.json` (last night's run), `machines/vps-siso/halo-restore-proof.json`
(decrypt, `sha256sum -c`, `pg_restore --list` on every dump, `unzip -l` on the Convex zip); `git -C
SISO_Agency/partners/halo submodule status`; halocrm has no pushed refs in backup.json/houses.jsonl (unchanged).

## Amendment, 2026-10-03: HALO's GitHub is the HALO-AGENCY org

**Why:** On 27 Sep the HALO-GITHUB lane moved HALO's repos into the HALO-AGENCY org, the CRM included
(`HALO-AGENCY/halocrm`, described on GitHub as "moved from camronkellman/halocrm on 27 Sep 2026"). `backup.py` and
`code.py` still knew only sisodias, so every HALO-AGENCY checkout read as third-party: its disk-only work left as
plaintext overlay bundles in the map, and the building code skipped it. A0, 3 Oct: "Never push to HALO-AGENCY main or open
PRs there; backup refs only."

**Decision:**
- The CRM's code lives only on `HALO-AGENCY/halocrm`. Both it and the old camronkellman/halocrm are in `NEVER_COPY`, and
  the HALO paths stay skipped: no copy, no bundle, no backup refs. Its lanes push their own work.
- HALO-AGENCY is an owned account in `backup.py` (`OWNED`). A checkout whose remote is a private HALO-AGENCY repo gets its
  disk-only work pushed to `refs/backup/<machine>/<slug>/*` **in that same repo**. That is a custom ref, so it never
  touches main, opens no PR and starts no CI. A secret-scan hit still holds it, and `held_copy` keeps it age-encrypted
  in `sisodias/siso-held-backups`, never in HALO's org. A public or archived HALO-AGENCY repo stays an overlay bundle.
- In `code.py` HALO-AGENCY is a client owner (`plan/building-code.json`): client rules apply, house rules do not, and
  `fix.py`, which fixes only our own repos, never commits into HALO's checkouts.

**How it is checked:** `backup.py plan` shows HALO-AGENCY checkouts as `own-private`/`clean` with target `HALO-AGENCY/<repo>`,
and every halocrm path as `skip`. `tests/test_backup_owned.py` covers this.
