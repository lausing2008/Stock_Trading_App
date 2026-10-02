# Production model and capability inventory

Local date: October 1, 2026 (America/Los_Angeles). Production clock: October 2 UTC. Model inventory completed 2026-10-02 02:45:46 UTC. Scope: read-only artifacts, PostgreSQL schema introspection in read-only transactions, Redis GET/TTL. No model resweep, retraining, flag change, broker action, email, recovery-key deletion or deployment.

## Results

### M14: artifact inventory executed

| Population | Artifacts |
|---|---:|
| Total readable artifacts | 1,313 |
| Suppressed | 1,218 |
| Unsuppressed | 95 |
| Explicitly invalid evaluation | 98 |
| Unknown evaluation validity | 39 |
| Explicitly invalid and unsuppressed | 0 |
| Resweep would unsuppress explicitly invalid | 0 |
| Any flag changes proposed by deployed rule | 0 |
| Failed artifact reads | 0 |
| Detected file changes during read | 0 |

Known-validity coverage: 97.03%; this is metadata coverage, not predictive accuracy. All 98 explicitly invalid artifacts are suppressed. Six of the 95 unsuppressed artifacts have unknown validity:

- `lightgbm/MU.joblib`
- `lightgbm/NET.joblib`
- `random_forest/BRK.A.joblib`
- `xgboost/1879.HK.joblib`
- `xgboost/5.HK.joblib`
- `xgboost/BRK.A.joblib`

Unknown does not establish invalidity or justify automatic suppression. Determine whether serving consumers select these legacy filenames, assess their evaluation provenance, then replace through valid training/promotion if needed. Directory counts are not counts of active signal contributors: legacy and horizon-specific files may coexist.

The inventory used the deployed pure `build_inventory` and AST-extracted deployed `_compute_oos_suppression`, not a second implementation or the mutating resweep. Existing trusted model bundles were read with joblib; artifact hashes are recorded. Reads were sequential, not a globally atomic filesystem snapshot; per-file before/after metadata detected no changes. No training module was imported to launch work.

### M24: schema checks executed from the backend fleet

Eleven backend containers successfully evaluated all five matrix capabilities as `ready`: market-data, ml-prediction, signal-engine, decision-engine, event-intelligence, news-intelligence, ranking-engine, research-engine, strategy-engine, technical-analysis and portfolio-optimizer. Their capability-check source hashes matched one another.

API gateway could not execute this database probe because `sqlalchemy` is absent. Record **not evaluated from that service**, not “all 12 ready,” and not automatically a deployment defect: a gateway may delegate database-owned capabilities. Map responsibilities before adding dependencies solely for a health report.

Actual schema evidence supports:

- `notification_outbox.event_id`: single-column unique constraint.
- `portfolio_exposure_reservations.intent_id`: single-column unique constraint.
- `paper_trades.broker_client_order_id`: single-column unique index for non-null IDs. This enforces uniqueness for assigned IDs, not presence on every intent.
- Submission/lease columns inspected by the current matrix exist.
- Supplemental query confirms `options_income_equity_curve.mark_evidence` exists. The current five-capability matrix does not check it. The field belongs to the equity-curve table, not the positions table.

**Scope qualification:** these are schema-structure results. They do not prove write permissions, broker credentials, provider connectivity, actual enqueue/drain behavior, consumer enforcement or sandbox crash-boundary correctness. Eleven callers observing the same shared schema are not eleven independent trading lifecycle tests. The matrix's prose saying work “is blocked” is a declared consequence, not evidence that every caller enforces a gate.

### M02: not expired at this snapshot

The supplied claim that October 6 had passed is inconsistent with the production clock. Both markers still existed during the initial market-data probe:

| Portfolio | Stored streak | Remaining TTL |
|---|---:|---:|
| 2 | 4 | 427,593 seconds (~4.95 days) |
| 5 | 10 | 471,071 seconds (~5.45 days) |

TTL is an observation, not a guarantee against later renewal. This inventory did not recheck holdings/pending orders or establish who originally consumed the grant. M02 remains a separate decision; no marker was removed. M21 digest remains unsent and unchanged.

## Two tool-quality gaps found while inspecting the inventory

1. **Uniqueness check is too permissive.** `_unique_constraint` accepts any unique constraint/index whose column list contains the requested column. A unique `(event_id, other_column)` does not establish unique `event_id`; an arbitrary partial index may exclude relevant rows. Validate exact key columns and intended predicate/null semantics. The inspected production outbox/reservation constraints are correct single-column ones, so this is a latent false-ready defect, not evidence those production constraints are missing.
2. **Unreadable model artifacts inflate reported validity coverage.** `build_inventory` increments total and records failed reads but skips unknown-validity accounting; coverage is `(total-unknown)/total`. A local one-unreadable-artifact probe returned coverage 1.0 and invariants true. This does not affect today's fleet result because every artifact read succeeded. Report unreadable separately, exclude it from known-valid numerator, and distinguish invariant status from inventory completeness. Keep unknown and malformed validity metadata explicit.

## Recommended next steps

1. Correct the two inventory false-assurance cases with behavioral fixtures; extend mark-evidence prerequisites and document gateway delegation. Keep schema readiness distinct from operation-level permission/connectivity and caller enforcement.
2. Map the six unsuppressed unknown-validity artifacts to actual inference selection before changing policy. Preserve the current metadata snapshot as the baseline.
3. Complete M25 sandbox evidence: durable intent/client ID sent to broker, compare-and-set claimant, acceptance/crash reconciliation, reservation retention and no fallback simulated fill for an unknown result. Flag remains a separate activation decision.
4. Prepare M20 activation evidence and watermark/cutover procedure without flipping the flag. Preserve unsent/unknown provenance instead of claiming watermark suppression proves delivery.
5. Do not relabel event attribution as an outbox problem. Event-linked news resolution helps persistent event handling but does not by itself prove a retrospective article was matched to the right earnings release. M19/M23 need explicit event/source association tests.
6. Correct the blanket “waiting on the world” classification: some outcomes need time, but M19 provider mapping/event identity and instrumentation gaps can still require engineering. M17 remains dependent on the simpler comparison and missing margin features; removal of the incorrect M20 dependency did not remove those real prerequisites.

## Artifacts

- [Model inventory results](evidence/2026-10-01-production-model-inventory.json), including per-file hashes and validity state.
- [Capability results across twelve attempted services](evidence/2026-10-01-production-capability-inventory.json).
- [Supplemental mark-evidence schema](evidence/2026-10-01-production-mark-schema.json).
- [Model probe](evidence/2026-10-01-model-inventory-probe.py) and [capability probe](evidence/2026-10-01-capability-inventory-probe.py). The latter was extended after the fleet pass to include the actual equity-curve table for the supplemental check.

This closes the first read-only measurement step for M14 and M24. It does not close all model-validity gaps, full capability readiness, M25 activation, M20 rollout or profitability validation.
