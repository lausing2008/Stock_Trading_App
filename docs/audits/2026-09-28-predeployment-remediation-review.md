# September 28 review of the pending follow-up remediation

**Recommendation: hold the combined release.** The fixes contain substantial improvements, but “all ten closed” is not supported by the current code. This review identifies eight actionable findings: seven P1 correctness/security/release issues and one P2 meta-model validation issue. Several are incomplete original fixes; others are defects introduced or exposed by the remediation. These are sandbox/paper-account and platform-integrity risks, not a claim that real money has been lost.

Reviewed local commit: `0e72d3a927a2e0bd50fde37173164abb2a2b599c`. Review date: September 28, 2026, America/Los_Angeles. Compared the implementation with the [September 24 follow-up audit](2026-09-24-fix-verification-and-followup-audit.md) and [remediation report](2026-09-24-followup-audit-remediation.md). This is a focused verification of that release and its adjacent execution paths, not a new exhaustive audit of every subsystem.

No application fixes, deployment, model promotion, production mutation, or trading actions were performed. `CLAUDE.md` and earlier reports were left unchanged.

## Evidence and verification

The [offline reproduction script](evidence/2026-09-28-predeployment-reproductions.py) executes actual functions or AST blocks against controlled inputs. It also uses real SQLAlchemy and the repository's actual price-table model. The [evidence file](evidence/2026-09-28-predeployment-evidence.json) records outputs, source hashes, production query text/results, and verification results.

| Independently run | Result |
|---|---:|
| market-data suite | 4,378 passed; 1 skipped |
| ml-prediction suite | 360 passed |
| news-intelligence suite | 129 passed |
| portfolio-optimizer suite | 83 passed |
| signal-engine suite | 549 passed; 1 skipped |
| Frontend | 304 passed across 16 files |
| Frontend TypeScript check | Exit 0 |
| Suite runner with no arguments | Exit 2, as required |
| Seven offline probe groups | All expected defect reproductions observed |

That is **5,499 backend and 304 frontend tests passing**, alongside the remaining defects below. I did not rerun all twelve backend suites, reproduce the seventy-mutation campaign, visually inspect the UI in a browser, or independently recount the 1,297 production model artifacts. Those broader claims remain author-reported. No production model artifacts were deserialized.

The probe assertions deliberately establish the defect; a successful audit probe is not a regression-test pass for the application. After remediation, replace those expectations with the intended invariant. The concurrency probe controls an interleaving around the actual settlement function; it is not a real two-process PostgreSQL race test. The authentication probe exercises dependency wiring with a controlled decoded token; it is not an HTTP penetration test.

## Disposition of the original ten findings

| Original | What improved | Current assessment |
|---|---|---|
| R01 | Outcome feature inputs and target now match the base path; admission filter added | Partial: availability still describes the old outcome rather than the new target; deduplication corrupts the date mapping. PR-03. |
| R02 | Training suppresses invalid evaluations and can retain a valid incumbent | Partial: maintenance can reverse suppression; legacy incumbent validity is not established. PR-02. |
| R03 | Session boundary, training-label purge, separate early stopping, and same-holdout rescoring added | Partial: another label boundary remains unpurged; fallback still uses incomparable AUC. PR-08. |
| R04 | Post-close corroboration added in `4260d33`, despite its omission from the user's summary table | Partial: a later bar or nearby quote still does not establish the expiry close. PR-04. |
| R05 | Overwrite path now guarded; publication time separated; compare-and-swap added | Narrow overwrite/race defects addressed. Event-linked resolution remains explicitly open in the remediation report. |
| R06 | Entry path gets a database lock, unique intent key, and lease check | Partial: settlement is outside that protocol; lease uncertainty persists. PR-05 and PR-07. |
| R07 | Model-admin routes re-read account state; revocation markers and service scopes added | Disabled/demoted model-admin protection improved. Reset-token revocation is inconsistent across validators. PR-06. |
| R08 | Mark grades and durable evidence added | Partial, with a new SQL failure in the historical/missing-live branch. PR-01 and PR-07. |
| R09 | Disabled meta member no longer runs or contributes by default; shadow mode distinguished | Reviewed fix supported by source and the green signal-engine suite. No new defect established for this finding. |
| R10 | Sleeve and returned portfolio metrics computed independently | Reviewed fix supported by source and the green optimizer suite. No new defect established for this finding. |

“Implemented,” “unit-tested,” “deployed,” and “verified in operation” should remain separate statuses. The report's own acknowledgment of unresolved event linkage is incompatible with an unqualified claim that every underlying risk is closed.

## PR-01 — P1: the new underlying-price fallback cannot query the deployed schema

**Source:** `services/market-data/src/services/options_income_engine.py:1070`, particularly the SQL at 1089; `shared/db/models.py:146`.

`_underlying_price_as_of()` queries `prices.symbol` and `timeframe = '1d'`. The table has `stock_id`, not `symbol`; its PostgreSQL enum stores `D1`, not `1d`. Both facts were confirmed in the local model and through read-only production schema queries.

Today's live-price success path bypasses this SQL, explaining why ordinary snapshot tests can pass. Historical snapshots and current snapshots with a missing live price reach it and fail before either fallback can run. Against the real ORM table on SQLite, the extracted function raises `no such column: symbol`. PostgreSQL has the same missing-column defect; fixing that alone still leaves the enum literal wrong.

There is a second defect in the same function: after an empty historical query, it falls back to today's `live[symbol]` unconditionally. The reproduction asks for September 25 and receives a controlled September 28 live price of 120, labelled `live`. The “historical snapshot uses its own date” contract therefore remains false even after correcting SQL.

**Solution:** use the ORM join from `Price.stock_id` to `Stock.id` and `TimeFrame.D1`; return the selected bar's timestamp/session with its price. Use current live data only for a current valuation. Historical missing data should produce an explicit unavailable/estimated mark, never a future quote. Roll back failed snapshot transactions and surface a failed/partial step status instead of returning an unconditional `ok: true` after logging the error.

**Acceptance:** exercise current missing-live, historical available-close, and historical missing-close paths against a migrated PostgreSQL schema. Verify no query names a nonexistent column or invalid enum value and no historical mark uses data observed later than the valuation date.

## PR-02 — P1: suppression maintenance can re-enable an invalid model

**Source:** `services/ml-prediction/src/training/trainer.py:1180`, `:1221`, `:1283`, and `:2127` (`resweep_oos_suppression`).

The training path now sets suppression for invalid evaluation/embargo conditions. The resweep still calls only the older `_compute_oos_suppression()` checks: CV AUC, dead recall, and overfit gap. It ignores `evaluation_valid`, embargo shortfall, and threshold-reporting validity.

An actual resweep dry run on a controlled artifact with `evaluation_valid=False`, suppression already true, CV AUC 0.65, recall 0.60, precision 0.70, and gap 0.02 proposes **true → false**. With writes enabled, that maintenance operation would erase the new restriction. Inference relies on the persisted suppression flag, so this is an execution-path bypass of R02, not just inconsistent logging.

The incumbent-retention branch also treats a missing legacy `evaluation_valid` field as false. That does not establish that an old incumbent was invalid; it simply cannot recognize an otherwise valid older artifact as protected.

**Solution:** define one versioned eligibility policy used by training, resweep, publication, and inference. Separate `valid`, `invalid`, and `unknown` for older metadata. Keep restriction reasons in the artifact. Reconstruct legacy validity only from sufficient stored evidence; otherwise quarantine/retrain or explicitly retain the incumbent under a documented transition policy. Do not let missing metadata silently mean either “safe” or “replaceable.”

**Acceptance:** run a candidate through train/save → resweep → load/predict and show invalid evaluation remains suppressed at every step. Add legacy-incumbent cases. Produce a read-only fleet impact report using the complete shared policy before changing existing artifacts.

The reported “only four additional suppressions” is useful evidence about the measured row-floor rule, but I did not independently verify that census. It does not establish the impact of every embargo condition, the legacy transition, or the maintenance path above.

## PR-03 — P1: outcome availability is still earlier than the target becomes knowable

**Source:** `services/ml-prediction/src/training/trainer.py:490`, `:534`, `:735`, `:852`, and `:975`.

R01 correctly changed augmented labels to the base forward-return target. Availability still comes from the old outcome's exit/evaluation dates and `signal_date + timedelta(days=horizon)`. The target is defined over forward price rows; calendar days are not trading rows, and an early outcome exit is not the end of that new target.

The reproduction uses a SWING signal on September 14, an outcome evaluated on September 24, and a ten-trading-row target ending September 28. The shipped availability block returns September 24. A September 25 training cutoff admits it, although the target requires the September 28 bar. Matching feature inputs did not remove this leakage path.

A separate alignment problem affects the same gate. When overlapping base rows are removed, the code resets `X.index`. Later it derives row dates using `df.ts.iloc[X.index]`. Those new contiguous indices no longer identify the original price rows. Executing both blocks maps a retained September 18 example row to September 14. This can incorrectly exclude usable outcomes and misreport split dates; the direction and extent depend on which rows were removed.

**Solution:** carry immutable row identity and aligned `feature_time`, `label_end_time`, and `label_available_at` through feature construction, deduplication, filtering, and splitting. Compute the target endpoint from the actual forward bar used to create that label. Availability must also account for when that bar was observable, rather than relying solely on its session date. Filter all aligned arrays with the same mask; resetting positional indices must not reconstruct timestamps from an unrelated frame.

**Acceptance:** include weekend/holiday spans, early-exit outcomes, delayed bars, and overlap removal. Assert that every fitted label was available before its evaluation boundary and that each retained row keeps its original timestamp. Check the whole training pipeline, not only `_load_outcome_features()` in isolation.

## PR-04 — P1: settlement corroboration does not establish a final expiry price

**Source:** `services/market-data/src/services/options_income_engine.py:531`, `:534`, and `:590`.

Two acceptance branches are insufficient:

1. If a later daily bar exists, `_corroborate_settlement_close()` accepts the older expiry bar immediately. A Monday bar can exist while Friday's incomplete bar was never refreshed. Later ingestion is not evidence of a successful correction to an earlier row.
2. For the latest session, a live quote within 0.5% accepts the stored price. The quote is a bare float, with no proof it represents the expiry session's finalized close. Even if it were the correct final close, tolerance can accept a price on the wrong side of the strike.

The probes reproduce both branches. For a 100-strike put, stored 99.90 and corroborating 100.10 are accepted as close enough. The actual paper-settlement math marks the stored-price case assigned and the hypothetical final-price case unassigned, with different P&L and released cash. This illustrates the platform's own expiry model; it is not a claim that real-world exercise decisions can always be inferred from a closing-price comparison.

**Solution:** require finalized data for the exact expected expiry session, with source, observation time, revision/finality status, and the price actually used. If unavailable, keep settlement pending with a reason. Do not replace this contract with a tighter tolerance or the existence of a later session. Corrections should create a traceable adjustment rather than silently rewriting historical cash.

**Acceptance:** leave a stale Friday bar in place, insert Monday's bar, and confirm settlement remains pending. Test strike-crossing discrepancies, after-hours movement, delayed refresh, and exact-session finalization. Test cash and position stage together.

## PR-05 — P1: the database/lease protocol does not cover settlement

**Source:** `services/market-data/src/services/options_income_engine.py:661`, `:773`, `:801`, `:1304`, and `:1385`.

`open_income_positions()` takes the portfolio row lock and checks its lease. `settle_expired_positions()` reads open positions, changes portfolio cash and position state, and commits without that lock or a lease parameter. The outer worker checks the lease only before entering settlement; it cannot stop a settlement that loses ownership during price lookup or other work.

The controlled interleaving executes the actual settlement function with a stale cash balance of 1,000, a concurrent committed debit of 900, and a collateral release of 500. It writes 1,500 instead of the consistent 600. This proves the function does not refresh or serialize that balance under the supplied interleaving; a real PostgreSQL concurrency test is still required to verify the complete fix.

The renewal loop has no local expiration deadline. Repeated Redis exceptions leave `is_held()` true indefinitely until a successful renewal response proves otherwise. Expiration of the remote TTL does not set the local lost flag. The entry intent index protects duplicate entries; it cannot make settlement cash updates idempotent or serialize all portfolio writers.

**Solution:** use one transaction protocol for every portfolio cash writer. Lock and refresh the portfolio before reading mutable positions; conditionally transition each open position once, with an idempotent settlement event. Keep the balance and position/event changes atomic. Enforce a monotonic lease-validity deadline based on the last confirmed renewal, and check ownership before every mutating commit. Database correctness must survive lease loss and worker death independently of Redis.

**Acceptance:** use separate PostgreSQL sessions/processes with barriers to pause settlement while another worker attempts settlement/entry. Inject renewal failure past TTL and terminate a worker. Verify one settlement event, one cash release, no lost debit, and rejection of further work after ownership uncertainty. Sequential SQLite intent tests cannot establish these properties.

## PR-06 — P1: password-reset revocation is bypassed by another authentication dependency

**Source:** `shared/common/jwt_auth.py:129`; `services/market-data/src/api/auth.py:184`, `:302`, `:503`; `services/api-gateway/src/api/proxy.py:180`.

Reset handlers write a per-user revocation marker, and the shared validator checks it. Market-data's separate `get_current_user()` verifies the JWT, checks the JTI blacklist, and reads the active account, but never checks that marker. Its `get_admin_user()` depends on this validator. The gateway's older blacklist check does not close the gap.

A still-active administrator whose password was reset can therefore retain an old token accepted by this route family, even while the shared validator rejects it. The controlled dependency probe demonstrates exactly that disagreement. Disabling/deleting the account is caught by the database read; the defect concerns reset/revocation while the account remains active. This is separate from the explicitly deferred self-service password-change behavior.

There is also a same-second boundary: `int(iat) < revoked_at` accepts a token issued in the same second as revocation, including one minted before the reset within that second.

**Solution:** centralize token/session validation across route families, retaining live role/account checks for privileged operations. Prefer an account token version or session epoch over ambiguous second-resolution ordering. If using timestamp revocation temporarily, define and test the equality case and issue replacement credentials consistently. Account recovery should invalidate existing sessions through every affected entry point, consistent with [OWASP session-management guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

**Acceptance:** mint a token, reset the password, and call representative shared-auth, market-data, and gateway routes over HTTP. All must reject the old session. Include reset and issue within one second, role demotion, legacy tokens, and intended service-token scope behavior.

## PR-07 — P1: required migrations depend on admin seeding and have a broken recovery branch

**Source:** `shared/db/session.py:764`, especially `:770`, `:801`, and `:820`.

The new `mark_evidence` column and intent index are created inside `_seed_admin()`. That function returns before executing DDL when bcrypt is unavailable or no admin password is configured. Existing databases do not gain new columns through `create_all()`, so these are mandatory schema changes attached to an optional authentication setup path.

If unique-index creation fails, the handler calls `conn.rollback()` inside `with engine.begin()`, then continues issuing service-user queries on the same connection/context. Real SQLAlchemy raises `InvalidRequestError` for that next operation. The preceding transaction's work is also rolled back. This contradicts the comment claiming index failure only warns and startup continues. The [SQLAlchemy transaction documentation](https://docs.sqlalchemy.org/en/20/core/connections.html#connect-and-begin-once-from-the-engine) describes this restriction explicitly; the offline probe confirms it.

This is conditional on configuration/index failure; I did not observe those failure conditions occurring in production. Production's missing new column/index is expected for an undeployed release and is recorded as a deployment prerequisite, not itself an incident.

**Solution:** move these changes into versioned migrations independent of credentials and run them once before compatible application startup. Preflight duplicate intents, resolve any conflicts explicitly, then require the index. Use a separate transaction/savepoint where recovery is intended. Do not declare the trading worker ready if its required schema or concurrency constraint is absent.

**Acceptance:** migrate an existing database with an empty admin password, migrate twice, and simulate duplicate intents/index failure. Verify a clear migration failure or intentional recovery, no use of a closed transaction, and worker readiness blocked until the complete schema contract holds.

## PR-08 — P2 while meta blending is disabled: promotion evidence remains compromised

**Source:** `services/ml-prediction/src/training/meta_trainer.py:381`, `:420`, `:497`, `:550`, and `:564`.

The new training-to-validation label purge is useful. The later split of validation into early-stop and final-promotion slices only separates signal dates. It does not purge early-stop labels whose availability extends into the final slice. Those labels influence the selected boosting round count. The actual split-block probe retains such an overlapping label, so separate arrays alone do not guarantee an untouched promotion period.

When the incumbent cannot be scored on the final slice, the code labels its saved score `historical_incomparable` but still passes that value into the promotion comparison. This repeats the old decision rule on precisely the failure path that requires a different policy.

Finally, `auc_se_day_clustered = 1 / sqrt(number_of_final_sessions)` is a sample-count heuristic. It does not use outcome/prediction variability or estimate a day-clustered AUC standard error. Naming it as such overstates the evidence.

**Solution:** purge by actual label availability at both boundaries, including early-stop → final. Define a separate, explicit schema-migration/cold-start policy when the incumbent is unscorable; do not compare incompatible historical AUCs. Rename the sample-count heuristic or estimate uncertainty with an appropriate time-block/session resampling method and document its assumptions. Retain the meta-blend disablement until feature-contract and promotion-evidence work is complete.

**Acceptance:** overlapping early-stop labels are excluded; an unscorable incumbent cannot silently invoke the historical-AUC gate; changing labels/predictions can change any reported AUC uncertainty estimate. Test minimum effective session counts after purging, not just raw rows. Time-ordered splitting with a gap is a useful baseline, but this platform must additionally track actual label availability; see [scikit-learn's TimeSeriesSplit contract](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html).

## Remaining observations and product consequences

**R05 is still an unresolved risk event problem.** The CAS and overwrite fixes are worthwhile. A newer unrelated positive story can still clear an unresolved material-negative flag, as the remediation report explicitly acknowledges. Store event identity, resolution/supersession links, and expiry separately from a ticker's latest sentiment. Until then, independent positive sentiment should not itself certify that a material event was resolved.

**A mark grade is not yet a complete valuation-quality contract.** New evidence helps explain stale quotes, but `approximate_marks` depends on option-mark quality, not underlying quality. A fresh option quote with an entry-price fallback underlying can avoid the approximate count. The fallback close query also does not return underlying age. API fields expose this evidence, but the frontend has no functional consumer of these new fields; their mention in `improvements.tsx` is documentation. Older rows with null evidence become `is_approximate: false`, conflating unknown provenance with verified quality. Use a three-state quality result, retain underlying timestamps, and display estimated/unknown equity before using it to compare strategies.

**Passing mutation tests does not establish these untested contracts.** The author's correction from 97 to 70 and disclosure of zero-test mistakes are useful process improvements. This round still finds paths bypassing shared policies and SQL that mock-heavy suites never send to the real schema. Prioritize a small number of integration tests for time alignment, schema compatibility, transaction concurrency, and authentication consistency. Require nonzero collected tests, but judge the oracle as well as its count.

**No new threshold or return recommendation is justified by this release.** Leakage, stale/mixed-time valuation, and settlement errors affect the evidence used to choose thresholds. Establish trustworthy labels and cash/equity accounting first, then remeasure stock and option strategies on untouched periods and prospective paper outcomes, including costs and rejected/expired alerts. A higher measured win rate on compromised evidence would not demonstrate an improvement.

## Production observations and proposed release sequence

Read-only database snapshot at **2026-09-28 13:43:19 UTC / 09:43:19 New York / 06:43:19 Los Angeles**:

| Observation | Interpretation |
|---|---|
| `prices` has `stock_id`, no `symbol`; enum includes `D1`, not `1d` | Confirms PR-01 against the deployment schema. |
| 9 open positions, all cash-secured puts; expiry range September 25–October 30 | At least one September 25 expiry remains open. Inspect exact-session finality and the scheduled settlement cycle; this morning snapshot alone does not prove a missed deadline or a stuck engine. |
| `mark_evidence` column absent | Pending migration prerequisite. |
| `uq_options_income_intent` index absent | Pending concurrency prerequisite. |

These observations describe the existing deployment, not execution of the pending fixes. I did not verify production source drift, current runtime feature flags, or the cause of the open September 25 position(s) in this pass. Do not attribute their state to code that has not been deployed.

Recommended sequence after fixes are reviewed:

1. Resolve PR-01–PR-07 and run the integration checks described above. PR-08 must block renewed reliance on meta promotion/blending; keep the disabled/shadow distinction explicit.
2. Apply and verify versioned schema migrations, including index validity and duplicate preflight. Keep trading workers from running the new code against an incomplete schema.
3. Build every service that packages changed shared code. Verify actual container source/schema compatibility after rollout, rather than treating a matching Git checkout as image verification.
4. In the sandbox, verify exact-session settlement and cash reconciliation for the September 25 positions, then verify current and historical equity snapshots. Exercise repeated runs and concurrent-worker failure cases without creating unintended entries.
5. Generate a read-only artifact eligibility inventory from the unified policy. Stage any resweep/retraining deliberately, with before/after suppression reasons and an incumbent-preservation policy.
6. Verify old-token rejection through all authentication paths, then inspect the portfolio and options-income cards in a browser, including unknown/stale valuation labels.
7. Record deployment commit, migration version, test evidence, runtime checks, and deferred items separately. Do not reopen meta blending or move beyond sandbox trading merely because the unit suites pass.

This report proposes that sequence; it does not authorize or perform deployment.
