# I06-F1/F2: snapshot errors and top-ten disclosure

Implemented in `44c8b24e` on isolated branch `fix/i06-snapshot-coverage`, based on `f8a00b5e`, and pushed to remote `prod`. Deployed as a scoped market-data hotfix on **2026-11-08 04:06 UTC** (Saturday November 7, 20:06 Pacific). The original dirty local `prod` checkout is unchanged.

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


## Production deployment evidence

The three target files matched the expected pre-change hashes before deployment, checked again under the host deploy lock. The EC2 checkout was clean and fast-forwarded to `44c8b24e`. Both nullable JSON columns were added under the existing startup schema lock and bounded DDL transaction before replacing the running files. Only market-data was restarted. The lock finished at `2026-11-08T04:06:56Z`, `state=completed`, `exit=0`.

| Running file | SHA-256 after deployment |
|---|---|
| `/app/src/api/risk_snapshots.py` | `4c6f92cda8b469252ae9cbad1308b742a59c5e7b9a01f340d3b1cf8b4a94131e` |
| `/app/shared/db/models.py` | `60d978426a2dea7eceab7aa2817a81ca88e9175271fea65432636031150beee0` |
| `/app/shared/db/session.py` | `20c9c0b6f290e414da120a32e87669d4a378855e432b1a1e1edb3802775de1e3` |

All match the commit. Container health is `running healthy`; HTTP `/health` reports `status=ok` and migrations OK. The existing `option_prospective_capture` capability remains degraded exactly as before deployment; this release does not resolve it.

The [deployed-module probe](evidence/i06-snapshot-live-probe.py) ran in a separate process inside market-data. It used synthetic position books and a session that captures writes without executing them, while the unmodified route functions made real HTTP calls to portfolio-optimizer. It did not call the inbound snapshot HTTP routes or save production snapshots:

- Both save routes selected the largest ten out of twelve synthetic positive-cost-basis holdings, excluding MSFT (2/78) and AAPL (1/78), and prepared the expected coverage field for the database insert.
- Selection coverage was **96.153846%**. The real optimizer returned 80% coverage and 42 common observations; combined coverage was **76.923077%** for both routes.
- Both preserved real optimizer **422**, `Insufficient price history for at least 2 symbols`, for nonexistent symbols, without preparing a write.
- Stress preserved real optimizer **400** and its invalid-scenario reason, without preparing a write.

Production persistence/history round trips were not exercised against users' accounts. Those paths passed local SQLite tests; PostgreSQL JSON and additive migration semantics passed the rollback-only local probe. After restart, logs showed scheduler startup, ingest/refresh job registration, successful recurring jobs including live-price refresh, and paper regime classification. This confirms resumed background activity, not that new market-session ingest or paper entries/exits were exercised during the weekend.

Rollback files and before/after health JSON are on EC2 at `/home/ec2-user/rollback/i06-snapshots-44c8b24e/`: `risk_snapshots.py`, `models.py`, `session.py`, `health-before.json`, `health-after.json`, and `image-before.txt`. Restore all three source files to their matching container paths under the deploy lock, restart market-data, and verify health. Leave the additive columns in place.

**Durability limitation:** this was a `docker cp` hotfix, not an image rebuild. The image remains `sha256:c92f633dad80bb30ce986fe30e249553e4195321a6160bed9f6c3207cffc883a`; container recreation can revert the code. A durable rebuild remains separate work and must reconcile all running source drift first. The changes are committed remotely so they are available for that rebuild.
