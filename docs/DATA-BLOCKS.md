# Dataset manifests and on-demand blocks

`plan/data-planes.json` configures writes. A `backend` declaration does not establish a backup, create a GitHub organization, or prove its reader permissions. The 39 destinations are provisioned as private repositories under `SISO-DATA`, with `backend_status: provisioned-private`. Organization default access is `none`; seven separate reader teams are attached according to each declared boundary. Provisioning does not establish a remote-verified backup. Legacy `owner` and `repo` values preserve old recovery provenance. Mixed-access planes retain an explicit `upload_hold` until their contents are separated by access boundary. Existing private-copy restrictions remain in force.

Each writable destination must be a private repository in `siso-data`, with the configured reader teams verified. `github_access_teams` names the intended teams, not evidence that they exist. Organization membership, default permission, repository access, complete storage inventory and allocation limits are admission checks. Personal planes use the `shaan-private` boundary; HALO and each client have distinct readers. No fallback write into a code organization is permitted.

## Restore contract

A manifest identifies the archive stream and every part independently:

```json
{
  "version": 1,
  "path": "data.tar.zst.age",
  "size": 123,
  "sha256": "<64 lowercase hex characters>",
  "encrypted": true,
  "parts": [
    {"name": "data.tar.zst.age.aaa", "size": 123, "sha256": "<64 lowercase hex characters>"}
  ],
  "locations": ["github:siso-data/siso-data-example-01", "disk:HOLD:/mounted/example"],
  "pins": {
    "github:siso-data/siso-data-example-01": {"commit": "<exact Git commit>"}
  }
}
```

This is a schema illustration, not an actual backed-up dataset. Release locations append `#<release-tag>` and pin both the release ID and each part's asset ID. The ordered resolver verifies every part and the assembled stream, rejects corrupt or unavailable locations, and tries the next location. A failed attempt must never publish a partial restore. Archive extraction rejects traversal, links and device entries. Disk locations are explicitly selected for the current machine; `mini` is not an implicit SSH operation.

The archive contains source filenames. Public manifest metadata contains only stable archive/part names, hashes and sizes. Encryption policy and secret scanning remain separate requirements; a checksum is not a privacy check. Existing recovery references must remain until a replacement is independently downloaded and verified.

## Block index in a code monorepo

Code remains in the monorepo. Only data uses submodules. Register a block in the code repository's small `ops/data/blocks.json` file:

```json
{
  "version": 1,
  "blocks": [
    {
      "block": "example/01",
      "commit": "<exact Git commit matching the parent gitlink>",
      "size": 123,
      "manifest": "data/manifests/example-01.json"
    }
  ]
}
```

The committed `.gitmodules` entry maps `data/example/01` to its authorized `https://github.com/siso-data/<repository>.git` URL. The manifest lives outside the submodule, so its size is available without fetching data. A normal clone leaves all data submodules uninitialized.

Run the estate-provided `bin/get` from the code repository. `bin/get --list` reads the index without fetching. `bin/get example/01` checks the index, committed gitlink, staged gitlink and submodule URL, then initializes only that path at depth one. It verifies the exact commit and all manifest hashes. If the mounted parts are absent or corrupt, the manifest resolver must obtain a complete verified copy; release payloads are not considered present merely because a submodule checkout succeeded. Downloaded archives are cached below Git metadata, and the success receipt identifies their path. Unregistered paths, URL overrides, path escapes and mismatched pins fail closed.

The local download gate retains 100 GB free after a conservative three-copy staging estimate. The migration-only build exception does not apply to data fetches. No data blocks are registered by this tooling change: a real block requires a verified dataset and the correct code repository access boundary first.

## Storage accounting

`estate data size` inventories the entire storage organization, separating Git storage from Release assets. Decimal TB means bytes divided by 1,000,000,000,000. Missing repositories, inaccessible releases or failed inventory calls must remain explicit; partial inventory is not a complete total and cannot authorize an upload. Warn at 1 TB and reject projected totals reaching 5 TB, including concurrent reservations. Reads and restores remain available at the write limit.

Uploads are serialized through the exact machine/hostname in `plan/github-data-storage.json`. Other hosts fail closed; this does not grant the laptop permission to read another machine's source paths. Before staging, the tool reserves a conservative source/SQLite-WAL/tar upper bound plus local working space, retaining the 100 GB laptop floor. Its shared coordinator ledger keeps remote bytes charged after any attempted write, including a lost or failed response, because GitHub's size metric can lag. This intentionally overcounts until an explicit, evidence-backed reconciliation; reservations must not be cleared merely to admit another upload. A future coordinator handover must preserve that ledger and ensure the old writer has stopped.

Git storage is GitHub's repository-size metric in KiB, converted to bytes, not a measurement of GitHub's physical pack files. The report labels this basis. Release asset sizes come from the separately paginated asset inventory.

Git parts are strictly below 95,000,000 bytes; pushes are below 2,000,000,000 bytes; data repositories stay below 5,000,000,000 bytes. Release assets are at most 2 GiB each. These bounds do not imply unlimited GitHub capacity. No R2 or Git LFS is used.
