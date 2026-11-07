# I06-F1/F2: snapshot errors and top-ten disclosure

Implemented in isolated branch `fix/i06-snapshot-coverage`, based on `f8a00b5e`. Production deployment is not recorded by this document. The original dirty `prod` checkout is unchanged.

## Behavior and API contract

Both snapshot save routes preserve optimizer 4xx status and `detail`, including structured validation details. Non-JSON errors retain the response text (or an HTTP-status fallback). Connection failures, optimizer 5xx and JSON decode failures remain 502. Rejected requests do not save a snapshot.

Both select the ten largest **positive cost-basis** positions (`shares × avg_cost`), descending, with symbol ascending to break ties. This preserves the existing weighting convention; it is not live market value and does not convert currencies. Smaller books send all eligible holdings. Nonfinite selected-book weights are rejected. The existing position reader still filters nonpositive cost basis; this change does not implement short, cash, FX or derivative accounting.

Save responses and history gain nullable `coverage`. The two tables each gain a nullable JSON column; existing `symbols_json` remains a sorted JSON list. Old snapshots have null metadata: their historical omissions cannot be reconstructed reliably. Same-day upserts replace the metadata with the latest calculation.

| Metadata field | Meaning |
|---|---|
| `weight_basis` | `positive_cost_basis_no_fx_conversion` |
| `requested_symbols` | All eligible positive-cost-basis symbols before the cap |
| `selected_symbols` | Up to ten symbols sent to the optimizer, in rank order |
| `excluded_positions` | Each cap exclusion's symbol, cost basis and percentage of eligible cost basis |
| `selected_weight_pct` | Selected cost basis / eligible cost basis × 100 |
| `optimizer_coverage` | Unmodified optimizer metadata, including missing history and measurement window |
| `covered_weight_pct` | Selected percentage × optimizer covered percentage / 100; null if upstream coverage is unknown |
| `benchmark_basis` | Optimizer benchmark-selection explanation |
| `warnings` | Optimizer warnings plus explicit top-ten exclusion warning |

Example: cost-basis values 1 through 12 total 78. The top ten total 75; the two exclusions are 2 and 1. Selection coverage is 96.15%. If the optimizer covers 80% of the selected weight, combined coverage is approximately 76.92%, not 80% or 100%. The optimizer percentage is already rounded, so combined coverage is approximate. VaR and stress figures still describe the covered, renormalized subset; they are not scaled into an estimate for omitted holdings. This does not establish statistical reliability or better returns.

The API records the metadata, but this change does not add frontend rendering. Consumers must label subset results and inspect coverage before treating them as whole-book risk.

## Validation

Focused validation: **56 passed** (32 snapshot tests plus 24 existing DDL-guard and one-shot-migration tests). Snapshot tests cover both routes' 400/401/403/422/429 and 500 responses, connection failures, non-JSON reasons, no writes on rejection, top-ten ordering and weights, full-book coverage, JSON persistence and history, legacy nulls, ties, overflow-safe totals, unknown upstream coverage, and existing token/upsert behavior.

Full market-data suite on the final implementation: **6,178 passed, 33 skipped, 29 warnings** (424.67 seconds). The first sandboxed run was interrupted after an unrelated intraday API subprocess timed out; the same five API tests passed outside the sandbox, followed by the successful full rerun outside the sandbox. Skipped integrations are not claimed as validated.

The rollback-only [PostgreSQL migration probe](evidence/i06-snapshot-migration.sql) ran successfully against local `stockai-pgtest`: columns added twice idempotently, legacy rows kept null metadata, JSON round-tripped, and existing symbol lists remained unchanged. This tests the DDL and storage, not production startup or scheduler behavior.

## Rollout and rollback

This release includes `risk_snapshots.py`, shared `db/models.py`, and shared `db/session.py`; route-only copying is insufficient. Apply the additive columns before loading the new ORM models. Startup includes idempotent DDL under the existing schema lock/timeout machinery. Old processes remain compatible with the added nullable columns; no other service restart is needed solely to consume snapshot metadata.

Use the host deploy lock and a weekend window before Monday opens. Check remote head, file drift and current ingest/scheduler/paper-trading activity before proceeding. Back up all three running files; preserve unrelated production changes. Use the repository's durable deployment procedure, or explicitly record a temporary copy as a hotfix. Restart **market-data**, then verify health, hashes, scheduler/ingest/paper-trading recovery and API saves/history. Test synthetic books in an isolated process or dedicated test account; do not overwrite a real user's daily snapshots to prove coverage.

Rollback restores the previous route and shared files together and restarts market-data. Leave the nullable columns and metadata in place; old readers ignore them. Do not drop columns or rewrite history during rollback. Deployment and live verification remain separate from local test evidence.
