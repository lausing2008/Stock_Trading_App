# September 28 post-deployment remediation verification

**The deployment claims are substantially confirmed. Two important correctness findings remain reproducible: settlement can approve a stale value even after a successful refresh, and label availability is still estimated rather than derived from the target bar.** Do not mark the complete prior audit closed or use these changes as evidence to enable meta blending or advance beyond sandbox trading.

Reviewed commit: `b5332a3`, including implementation commit `e77f7a2`. This follows the [pre-deployment review](2026-09-28-predeployment-remediation-review.md) and checks the claims in the [remediation report](2026-09-28-predeployment-audit-remediation.md). Earlier evidence is retained as a dated snapshot; its “schema absent” observations were correct before deployment and are not current-state assertions.

Only new audit/evidence files were added. No application changes, production writes, deployment, pruning, or trading actions were performed. `CLAUDE.md` was not modified.

## Independently confirmed

Read-only production checks at **23:45–23:48 UTC on September 28** confirmed:

| Claim | Verification |
|---|---|
| Required schema deployed | `mark_evidence` exists; `uq_options_income_intent` exists and is valid, ready, and unique. |
| Historical lookup works | Deployed helper returns AAPL 337.02 for September 23, and approximately 319.70 for August 29. The second result correctly comes from the August 28 bar. Separate SQL reads agree. |
| Same-second revocation | Deployed function rejects old, equal-second, and missing-issue-time tokens; accepts a later token and exempts a service principal. Checked with process-local controlled inputs, without changing Redis or issuing tokens. |
| Deployment source drift | Repository checker compared both service and shared Python source: **12 checked, 0 drifted/errored**. |
| Meta blend disabled | Redis flag absent; the deployed implementation enables blending only for the explicit value `"1"`. |
| September 25 positions | All three September 25 cash-secured puts are now closed. Current open inventory: seven puts and one covered call. This verifies state, not historical settlement-price correctness or strategy profitability. |
| Disk pressure | Root filesystem is now 60% used. The reported 58% was from another observation; pruning history and exact recovered amounts were not independently audited. |

Local full suites for the two changed services passed: **market-data 4,395 passed / 1 skipped; ml-prediction 373 passed**, totaling **4,768 passing tests**. This pass did not rerun all twelve suites, frontend tests/typecheck, the mutation campaign, or browser rendering. HTTP 401-versus-500 behavior remains author-reported; the production authentication checks above exercised the deployed function, not full HTTP routes.

Evidence: [recorded results and hashes](evidence/2026-09-28-postdeployment-evidence.json), [new executable probes](evidence/2026-09-28-postdeployment-reproductions.py). The old reproduction file remains tied to its old commit. Its failure on renamed functions or shifted line-number blocks is not proof of remediation; the new probes extract current functions by name and test behavior.

## Disposition against the original eight findings

| Finding | Assessment now |
|---|---|
| PR-01: invalid underlying query / historical live fallback | Specific defects fixed. Real-schema offline checks and deployed historical reads pass. |
| PR-02: resweep undoing suppression | Specific bypass fixed: resweep supplies `evaluation_valid` to the shared rule. Older artifacts with unknown validity and incumbent transition policy remain separate limitations. |
| PR-03: label availability / dedup dates | Date alignment fixed by carrying and masking `X_row_dates`. Target availability remains incomplete; see PV-02. |
| PR-04: settlement finality | Still open. Re-fetching helps the ordinary case but does not make the current read sequence safe; see PV-01. |
| PR-05: settlement outside row lock / lease loss | Settlement now locks and refreshes the portfolio before reading positions. Lease-expiry and pre-commit ownership issues remain; see residual work below. |
| PR-06: local validator / equal-second revocation | Specific defects fixed. Local validator uses the shared marker check, and equality now revokes. |
| PR-07: optional migration placement / rollback misuse | Specific placement and transaction defects fixed; production schema verified. Required-schema failures still only warn, so readiness enforcement remains incomplete. |
| PR-08: meta promotion evidence | Early-stop label purge added. Incomparable historical-AUC fallback and incorrectly named uncertainty statistic remain unchanged. |

There were eight numbered findings and seven probe groups in the earlier audit; those counts do not represent interchangeable units of closure. Some findings each contained multiple related defects.

## PV-01 — P1: settlement can use the old close even when ingestion refreshes it correctly

**Source:** `services/market-data/src/services/options_income_engine.py:534`, `:590`, `:603`; ingestion at `services/market-data/src/services/ingestion.py:235`, `:346`, `:452`.

Claude is right about a fact my first review did not sufficiently account for: incremental daily ingestion requests a trailing window and upserts existing closes. The exact start expression is `head.date() - 7 days + 1 day`. With a complete successful provider response, this normally refreshes the earlier bar while inserting a successor. That reduces the ordinary stale-bar risk and deserves explicit credit.

It does **not** establish the stronger claim that the settlement value currently in memory is final. The code reads the expiry close in one session, then opens another session to corroborate it. The reproduction executes both actual functions against the repository's real Stock/Price tables on SQLite, with this controlled ordering:

1. Settlement reads Friday's stored close, **99.90**.
2. A separate transaction atomically updates Friday to **100.10** and inserts Monday's bar. This deliberately satisfies the claimed successful-refresh premise.
3. Corroboration sees Monday's bar and returns `superseded_by_later_session`.
4. Settlement returns **99.90**, the value read before the refresh. The database now holds **100.10**.

For the platform's 100-strike put example, its own settlement function marks 99.90 assigned and 100.10 unassigned, with different cash release and P&L. No actual production settlement error is asserted. This is a deterministic read-order reproduction, not a PostgreSQL concurrency/load test. The risk is consistent with PostgreSQL's documented ability for successive reads under Read Committed to observe different committed snapshots; using a separate corroboration session adds another independent snapshot. [PostgreSQL transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html).

There is also no ingestion requirement that every requested historical session actually appear in the validated response. The adapter loop accepts a nonempty frame and upserts its returned rows. Requesting a trailing window therefore does not prove that one particular expiry row was present and rewritten. The new coupling test inspects the lookback expression and upsert source; it neither simulates missing historical rows nor this interleaving. Its numeric check allows a window of at least two days rather than enforcing the stated seven-day setting.

The prior latest-session concern also remains: a float within 0.5% accepts the stored close, without preserving the quote's session or finalization evidence. The provider currently fetches the latest non-null daily close from a two-day download; that is more specific than an arbitrary tick, but the helper discards its date. A nearby price can still lie across the strike.

**Solution:** return the price and its exact-session finalization/revision evidence together. Read them consistently and persist the evidence used for settlement. A later-row test must not approve an earlier price captured before refresh. Require per-session refresh coverage rather than infer it from the requested window. Keep uncertain settlement pending. A single consistent read fixes this particular race; it does not by itself prove that a returned provider bar was finalized.

**Acceptance:** reproduce the interleaving on PostgreSQL with two connections; settlement must use 100.10 or defer, never approve 99.90 using the new successor. Also test a valid response containing Monday but omitting Friday, and a discrepancy crossing the strike. Verify the resulting cash event as well as the returned price.

## PV-02 — P1: business-day padding is not the actual forward-label endpoint

**Source:** `services/ml-prediction/src/training/trainer.py:432`, and its use in `_load_outcome_features()`.

The calendar-day bug is improved, but `label_end_date()` now uses generic pandas business days plus `max(1, bars // 10)` calendar days. Its docstring claims this can only err late. That is not guaranteed for holidays, missing data, or trading suspensions. The target remains defined by forward rows in the actual price frame.

The new probe uses a controlled daily series with September 16–18 absent:

| Item | Date |
|---|---|
| Signal | September 14 |
| Helper estimate for ten future bars | September 29 |
| Training cutoff | September 30 |
| Actual tenth future bar | October 1 |

If the outcome exit/evaluation timestamps do not move availability later, this row remains admissible before its target is knowable. This is a supported-input counterexample, not a claim that this exact sparse series or leak was observed in production. The base forward-row target already supplies the information needed to avoid estimating.

**Solution:** create `label_end_time` from the actual timestamp at the forward-shifted target row, in the same computation as the target. Carry that value through outcome selection, deduplication, and split filtering. Add observation/ingestion availability where available. If the target row is absent, drop that label; do not manufacture its availability with a larger fixed allowance.

**Acceptance:** align target price, target timestamp, and availability on complete and sparse histories. Test weekends, exchange holidays, and suspended symbols. Assert every fitted label is observable before its evaluation boundary. Preserve the new lockstep date-mask fix.

## Residual work that should stay explicitly open

**Meta promotion — P2 while blending remains disabled.** The new early-stop purge addresses one real defect. `meta_trainer.py:572` still falls back to historical AUC when same-holdout rescoring fails and uses it in the promotion decision. At `:519`, `1 / sqrt(number_of_days)` is still labelled `auc_se_day_clustered`; it is a sample-count heuristic, not an estimated AUC standard error. Define an explicit unscorable-incumbent policy and either rename the heuristic or implement an uncertainty estimate based on predictions/outcomes. Keep blending disabled pending the full feature and evaluation contract.

**Lease ownership and transaction protocol.** The new settlement portfolio lock is valuable. However, settlement still has no lease parameter/check immediately before commit, and `IncomeLease.is_held()` remains true through repeated renewal exceptions without a local expiry deadline. The database lock improves serialization but does not establish the claim that work aborts on lease loss. Enforce a monotonic deadline and ownership policy for all mutating paths, and verify lock behavior on PostgreSQL. Do not confuse a serialized stale worker with an aborted stale worker.

**Schema readiness.** `_apply_isolated_ddl()` correctly separates transactions and runs independently of admin seeding. It catches both mandatory-DDL failures and continues startup. Production is healthy on this point today because both objects were verified. Future startup should refuse to enable the affected worker if its required column/index is missing, while allowing unrelated services to remain available.

**Unknown legacy model validity.** Explicit `False` now survives the resweep. Missing metadata remains a deliberate legacy-policy gap; it is not proof that older evaluations are valid. The earlier four-of-130 measurement cannot by itself establish the impact of every new availability rule. Keep an artifact policy/version and report unknown versus valid separately.

**Previously acknowledged items:** event-linked news resolution, self-service password-change session revocation, and valuation-quality display remain open. Browser verification of `/improvements` and the affected portfolio cards was not completed in this review. A built bundle proves inclusion, not rendering.

## Recommended next work

1. Fix and integration-test PV-01 before trusting the next expiry's automatic settlement; review the three newly closed positions' exact prices and cash events without silently rewriting them. Their closed status alone does not prove the flaw occurred or did not occur.
2. Replace the availability estimate with the actual target-bar endpoint, then assess affected model artifacts before relying on their validation metrics.
3. Close the listed lease, schema-readiness, and meta-evidence gaps under their existing audit IDs instead of marking them closed with the narrower fixes.
4. Keep operating claims separate: source drift is zero, schema exists, and tests pass; correctness of every timing, failure, and concurrency path has not thereby been established.

The release is materially better than the version first audited. The specific dispute over settlement is not resolved by the trailing-window explanation, and the new reproducible counterexample holds even when that refresh works exactly as intended.
