# Data security — laptop and Mac mini

Shaan, 8 Oct ~21:00: "there's too much data security and stuff I think the estate should be working on that".
Owner: ESTATE. What private data sits where, what backs it up, what is exposed, and what is being done. Updated by hand
when a fact changes; the storage maps (`docs/STORAGE-MAP.md`, `docs/STORAGE-MAP-mini.md`) carry the sizes.

## Exposure, worst first (8 Oct 2026)

| # | Exposure | Where | Fix | Who |
|---|---|---|---|---|
| 1 | **The mini's internal disk is not encrypted** (`fdesetup status`: FileVault is Off) while it holds WhatsApp's chats, `personal/` (team-entrepreneurship, trading-for-dad), Fahmy's partner folder, Shaan's Photos library (7.7 GiB) and every Codex chat run there (15 GiB) | Mac mini | Turn FileVault on (System Settings > Privacy & Security > FileVault). After a reboot the mini then waits for a login before agents run | Shaan only (his password and recovery key) |
| 2 | Private data on an agent box: WhatsApp app data, Photos library, `personal/` are on the machine every agent works on | Mac mini | Agents' work does not need them: Photos library to the vault drive (Photos > Settings > change library location) or off; `personal/` belongs on the laptop | Shaan decides; ESTATE moves when told |
| 3 | Chats are kept forever in plain files: Claude (`cleanupPeriodDays` 99999) and Codex sessions, both machines | `~/.claude*/projects`, `~/.codex/sessions` | Older than 14 days: encrypted (age) to GitHub or the vault, then off the disk (`archive-offload.py chats`, built; weekly job written, not loaded) | ESTATE |
| 4 | Bulk moved off the laptop goes encrypted: `_archive` folders as age-encrypted parts in private repos `sisodias/siso-archive-*`; the Codex repair copies as one age file in the mini vault | GitHub, mini vault | Done; key `~/.config/siso/age/estate-backup.key` (laptop only), recipient `plan/age-recipient.txt` | ESTATE |
| 5 | HALO code and data never leave Cam's repo (ADR 0007): offload, banks and vault moves refuse halo/oracle/cam-/kellman/fahmy/bykonz/whatsapp/life/personal names | tools | Done in `archive-offload.py` NEVER and `worktree-retire.py` | ESTATE |

## Where private data sits

| Kind | Laptop | Mini | Backed up |
|---|---|---|---|
| WhatsApp | `~/Library/Group Containers/*WhatsApp*` (11 GiB) | `~/Library/Containers/net.whatsapp.*` and Group Containers | not by the estate (never copied) |
| Life / personal | `~/SISO_Workspace/personal` | `~/SISO_Workspace/personal` (team-entrepreneurship, trading-for-dad, apps, tools) | laptop: data planes `personal*` (encrypted) |
| Fahmy | `SISO_Agency/partners/fahmy` | `SISO_Agency/partners/fahmy` | data plane `fahmy-services` |
| HALO | `SISO_Agency/partners/halo`, oracle worktrees | oracle/halo worktrees (57 GiB), `_data/overnight-2026-10-03/streaming-src` | Cam's repo; `halo-*` planes on the HALO VPS |
| Photos | — | `~/Pictures/Photos Library.photoslibrary` (7.7 GiB) | iCloud (cloudsync present) |
| Chats | Claude 10 GiB, Codex sessions 5 GiB | Codex 15.5 GiB | no (see exposure 3) |
| Keys | `.credentials/projects`, `~/.ssh`, age key | `~/.ssh` | `credentials` plane |

## Vault drive

`/Volumes/SISO-STORAGE-VAULT` on the mini: 4.5 TB, APFS **encrypted** (diskutil: FileVault Yes), 3.4 TB free. The mini's
bulk and the laptop's cold copies go here (Shaan: the mini is for agents' compute; backup only on its external drive).
