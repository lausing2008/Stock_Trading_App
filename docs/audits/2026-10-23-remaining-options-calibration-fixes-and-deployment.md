# Remaining options and calibration work — October 23, 2026

Scope: finish the reviewed R01–R07 follow-up, address the remaining reproducible defects, and deploy an isolated release. No intraday horizon or new profitable parameter set is claimed. The user authorized deployment in this session. Unrelated PriceChart, daily-cross, backup/deploy refactoring and news work must remain outside the release.

## Changes

The approved R01–R07 follow-up is committed as `85af2e04` on top of `d52518bf`. It includes the deadline retry scheduler, estimated moneyness, unresolved-settlement states and valuation caveats, plus the historical review evidence. Original audit evidence is preserved.

### R08 — corrected and versioned descriptive activity score

`chain_intensity_v2` uses exact contract volumes: imbalance points are `40 × abs(calls − puts) / (calls + puts)`. Swapping calls and puts produces identical intensity. Missing, negative/non-finite or zero-total volumes withhold the score. Known event-size points and the combined-volume floor retain explicit measurement coverage. A legacy rounded/capped ratio cannot change the calculation.

Live GEX no longer contributes points to a settled-session score. The separate Market Pressure panel continues showing GEX/NOPE with its own time. Score metadata records session, included expirations, field coverage, method and component set; old and new methods are not comparable. The flow cache version changes so an earlier cached score is not returned as v2. The card labels v2 as “Chain activity”; it remains descriptive, without a predictive-performance claim.

### R09 — supported-product boundary

Known cash-index roots and caret index symbols are refused as current candidates and as automatic 100-share payoff calculations. Historical expiry evidence remains available. Symbol context now reaches scope evaluation in routes, snapshots and cached-response scope updates.

This implements a restriction, not a complete index contract calendar. Index options remain unsupported until contract-class metadata establishes their last-trading instant, settlement and deliverable. Browser clock refreshes preserve a server-side product refusal; unsupported settlement is not relabelled as expired. Existing ordinary equity/ETF expiry calendar tests cover regular, early-close, holiday and DST sessions. The application's symbol/root assumptions are not authoritative exchange contract terms.

### R10 — forward identity ledger, cancellations and sampling quality

Every actual dark-pool provider attempt now records success/empty/failure, count and window-limit saturation in a Redis poll journal. Cache hits are not new polls. The journal retains at most 4,096 observations and 21 days; telemetry errors do not interrupt feed requests. The UI receives explicit unobserved/query-failed/degraded/observed-sample quality, failed polls, saturated windows and missing complete regular-session polling slots after observation began. Weekends and holidays do not count as missing session slots. This measures the application's observed sampling, not complete market coverage or a guaranteed polling SLA for every stock.

A read-only five-row production response check found both `tracking_id` and `canceled` on all five rows; only field names and aggregate counts were returned. The adapter now preserves these fields and the response observation time. An additive ledger keys current state by symbol/provider tracking ID and retains distinct payload versions. Separate IDs with identical trade figures coexist; repeat deliveries are idempotent; later observed corrections replace the current state; cancellations persist as tombstones. Older captures cannot reverse newer observations, even when an intervening response repeated the same state. Conflicting states for one ID at the same capture time are quarantined until a later response resolves them. Canceled and conflicting rows cannot trigger activity.

The baseline now uses only uncanceled, unambiguous identified events from this ledger. It excludes legacy tuple history and unidentified rows, so baseline coverage starts anew and may be insufficient initially. Legacy tables remain untouched for compatibility and rollback. Startup creates the two additive tables before scheduling. Poll telemetry counts unidentified events and degrades the sample status when present.

**Limits:** provider tracking IDs are not proven exchange-wide identities; observation time orders responses, not exchange correction sequence. The ledger retains distinct observed states, not every repeated observation. A correction absent from the bounded feed cannot be recovered, and historical tuple collisions cannot be reconstructed. Unknown cancellation state is excluded from the baseline. This is forward correction handling and explicit sampling coverage, not a complete consolidated tape. PostgreSQL behavior is checked with session-temporary tables and an outer rollback; the probe neither reads nor modifies production event rows.

### R11–R14 — reviewed and contained; full strategy research remains unfinished

The findings remain supported by source: the no-incumbent promotion bypass, row/date split leakage, inconsistent evidence populations and gross-return/hit-rate metrics do not establish better net trading returns.

The registered applying endpoints for ML/TA/conviction calibration, outcome thresholds, sell pillars, style/strategy tuning and the watchdog now return a versioned **research-only** result before executing their legacy mutation bodies. The ML applying helper also refuses writes when invoked directly. There is no deployment environment toggle to silently bypass the boundary. Existing settings are not rewritten or cleared; their existing TTL behaviour is unchanged. Read-only ML diagnostics and confidence research remain available, and tuning status exposes the promotion policy.

The read-only ML sweep now splits whole signal dates, purges training outcomes ending on/after validation begins, requires adequate fired training observations and positive training gross returns, and selects by mean gross return rather than hit rate. Its no-incumbent comparison uses each style's actual default cap. First runs cannot bypass positive validation-return and verified-evidence checks. A zero-fired incumbent has an unknown return, not a measured zero. Public preview states that no candidate would apply, even if a gross diagnostic criterion passes.

Threshold diagnostics use strict production BUY boundaries, effective incumbent thresholds and the shared verified/forward-entry/exit eligibility predicate used for displayed confidence calibration. Watchdog hit-rate fractions are no longer assigned to an EV-percentage field in the retained legacy code.

**Still open:** R12 is corrected for the ML diagnostic path; the other legacy tuners remain blocked rather than represented as validated purged studies. R13's production-write hazard is contained and verified diagnostic eligibility is shared. R14 still requires immutable snapshots of every candidate, policy/input provenance, faithful downstream gates and executable entry/exit/cost replay, followed by independent held-out and forward evidence. Those capabilities cannot be replaced by a cutoff change. No automatic calibration is re-enabled by this release.

## Deployment boundary

Production preflight found 15 healthy containers, 32 GB free, and a clean server checkout at `fddb2e03`. The Oct 22 capture file records `post_close_confirmed:true`, report exit code 0 and capture time `2026-10-23T00:03:57.596515+00:00`. No capture/build process was running at preflight.

Deploy only committed source, excluding the unrelated dirty working tree. Build market-data, signal-engine and frontend with production settings on EC2; never transfer the local `.next` artifact or `.env.local`. Retain the running image IDs before rebuilding. Recreate only those services with `--no-deps` and check health, source hashes and representative authenticated responses. The frontend deployment script now also uses `--no-deps` so it cannot recreate dependencies.

The PostgreSQL temporary-table probe passed: two distinct IDs retained, ten payload versions, one active unambiguous event; corrections, cancellation tombstones and stale-capture protection all passed. Temporary tables disappeared after rollback. Evidence is in [the release evidence folder](evidence/2026-10-23-remaining-fixes-release/README.md). The first container probe failed before any connection because its repository-depth assumption did not fit `/app/tests`; the harness was corrected and the ledger code passed unchanged.

Final suite totals and deployed release identity will be appended after rollout.
