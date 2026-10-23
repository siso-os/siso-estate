# Monorepo source disposition — 21 October 2026

Task t-0712 is a source-disposition and design-document check. It does not certify runtime cutover. The ledger has **88 import records for 81 distinct repository remotes**: **75 sources have imported code**, **one has partially preserved source**, **four remain held**, and **one is excluded**. At record level, 80 are imported (11 pending integration), one is partial, five are held and two are excluded. Multiple destinations and sanitized replacements explain the difference. No row or original hold was dropped. The original aggregate is retained as historical evidence; per-source decisions are in `.agents/migration-20261016.json`.

## Remaining source decisions

| Source | Disposition | Reason |
|---|---|---|
| siso-component-bank | code-only-source-preserved | Two source files held for security review; external index and runtime adoption remain unverified |
| siso-repo-bank | hold-security-scan-coverage | 872.483s scan near870s scanner deadline; four observed benign findings do not prove complete scan;176MB tracked data also held |
| siso-ui-code | hold-security-provenance-storage | 1.410GB upstream component mirror; bounded complete security scan pending, licenses/provenance and tracked data unresolved |
| siso-design-system | hold-capacity-native-integration | Full pinned source history security PASS; current storage preflight HOLD; exact-source native and integration gates remain |
| siso-industry-packs | hold-security-privacy | Six opaque or possible credential/private-content findings remain unwaived |
| siso-firstmate | excluded-private-upstream-fork | Upstream private firstmate fork; verbatim private memos and transcript index; excluded scope |
| siso-compute-pool | excluded-private | Full source history includes4 actual raw runtime transcripts; excluded without rewriting source history |
| siso-harness-lab | hold-confirmed-private-history | CONFIRMED private content: audit contains25dated/session-attributed verbatim user corrections from117Claude+371subagent transcripts; audit blobdd9f20c07e3267976eda60cdad8c0cef7a4e591c lines223-251; independent of controlled-test excerpt |

The additional `siso-stargate-corpus-data` entry has no remote and is held as bulk data pending classification. It is not counted as a source repository. Privacy, complete scan coverage, provenance and native-integration gates remain in force. The historical capacity findings were not refreshed into an allocation pass by this review.

## Design

[Design PR #197](https://github.com/sisodias/siso/pull/197) closed without merging. Its design is nevertheless present on main as [ADR 0030](../../../decisions/0030-monorepo-layout-20261021.md), with a later rebased module map. The [execution-status receipt](../../../decisions/0030-layout-execution-status.md) supersedes the preliminary “not landed” header: it records layout landing `410523801d26f095f79237d3d012aca6e509e4d9`, 45 passing affected commands and path adoption on both machines. The design blob and review commit are recorded in the migration ledger. Those existing execution receipts were inspected, not rerun, during this source-disposition task. The remaining import, data and product-integration holds above are not waived.

## Check

The completion check parses all 88 records, groups the 81 distinct remote identities and an explicit import/partial/hold/exclusion decision for each record and each source, recomputes counts, retains all prior rows and source pins, and verifies the design ADR exists in the reviewed main tree. It does not run product builds, change a service, or waive any scanner.
