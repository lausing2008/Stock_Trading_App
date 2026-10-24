# Pending working-tree review — October 23, 2026

> **Fix update:** W01–W04 are now fixed locally and regression-tested. Nothing is staged, committed or deployed. See the completion section below; the initial findings and original reproduction results remain as historical evidence.

Reviewed against committed HEAD `e0831bb9`. **The Git index is empty: no files are staged.** The initial review covered the modified and untracked work visible at review time and added only this report and reproduction evidence. The subsequently authorized fixes change the files recorded in the completion section; no staging, commit or deployment was performed.

**Initial verdict, before the fixes below: the pending work was not ready to call fully resolved.** Deployment verification has two reproducible false-success paths, and the scheduler change breaks four existing signal-engine tests. The news documents also identify work that remains unimplemented. Previously committed options/calibration changes are separate from these pending changes.

## Findings, in priority order

### W01 — P1: an unhealthy container passes deployment verification

`scripts/deploy_remote.sh:75–76` tests Docker's human-readable status with `[[ "$st" == *healthy* ]]`. The string `unhealthy` contains `healthy`. An unhealthy service is therefore reported as OK.

The saved witness runs the actual script with the existing test harness's fake Docker, SSH, disk and Git commands. Returning `Up 1 minute (unhealthy)` produces exit 0, an OK message and a completed lock status. No production command is executed.

Required fix: compare the exact structured health state, require a running container, and test unhealthy, starting, missing and Docker-inspection failure. Inspect a precise container identity rather than relying on a substring name match. This logic was moved from the earlier script; it is an existing defect retained by the refactor, not a newly invented regression.

### W02 — P1: a failed verification still records deployment success

`scripts/deploy_remote.sh:133` catches `verify` failure with a successful warning command, then runs pruning. A successful prune makes the script exit 0. Consequently, `deploy_lock_finish` records completion and the workstation wrapper also returns success.

The witness supplies a missing service. Verification emits its failure warning, but the script exits 0 and records `state=completed ... exit=0`.

Required fix: retain verification's nonzero status through any cleanup and return it to the caller. Add a test covering the script exit, outer wrapper exit and recorded lock status. The proposal explicitly acknowledges this retained discrepancy, but acknowledgement does not make the deployment gate reliable. Repair W01 and W02 together.

Evidence: [executable witness](evidence/2026-10-23-pending-review/deploy-verification-witness.py) and [results](evidence/2026-10-23-pending-review/deploy-verification-results.json).

### W03 — P2: pending scheduler changes break four existing price-window tests

Running `services/signal-engine/tests/test_technical_signal_price_window.py` on the current tree gives **4 failed, 4 passed**. Running the same test on the clean committed checkout gives **8 passed**.

The test extracts the scheduler's price-query/row-preparation statements and executes them with a small namespace. The new query at `scheduler.py:9738` now needs `sym` and `cross_symbols`; subsequent statements also need `daily_rows_by_sym`, `markets_by_sym` and `stock.market`. The old harness supplies none of them. Its first failure is `NameError: sym`.

This is a test-harness regression. The real scheduler defines `sym` in its enclosing loop, so it is **not evidence that production necessarily raises that NameError**.

Required fix: update the harness with the new context and exercise both ordinary alerts (260-bar query) and cross alerts (up to 1,000 rows, while preserving the ordinary 260-row indicator window). Keep assertions for filtering, chronological order and empty history. Do not simply remove the failing tests. Until repaired, the complete current working tree cannot be described as having a clean signal-engine suite.

### W04 — P2, pre-existing: the documented zero-sample news verdict is still unfixed

The pending news documents accurately identify the issue; they do not implement its repair. `services/signal-engine/src/api/analytics.py:1144` substitutes zero for a missing active win rate, and line 1167 assigns a predictive/weak/harmful verdict from that artificial edge. With no active observations and a positive inactive win rate, this can label an unmeasured filter predictive.

Required fix: withhold the comparison and return an explicit insufficient-evidence state when either cohort is empty. Carry that state into the UI and test empty-active, empty-inactive, both-empty and measured comparisons. Small measured cohorts also need honest descriptive wording; replacing missing data with zero is a distinct correctness defect.

This finding is confirmed by current source and the existing measurement document. No new production cohort query was run during this review.

## Other deployment limitation

The local frontend gate in `scripts/deploy.sh:130–140` tests the local working tree, but `run_remote` then pulls whatever `origin/prod` contains. It does not establish that the tested and deployed revisions match. Before treating this as a release-attestation gate, pin the tested commit, require the appropriate clean state, and deploy that exact revision. This is a retained limitation, not introduced by the lock refactor. The completed options release used isolated committed source and explicit source-hash checks instead.

## File and feature status

| Pending files | Review result |
|---|---|
| `.dockerignore` | `.git` exclusion is appropriate; no issue found in this change. |
| `Makefile` | Adds host-script tests to `make test` and preserves aggregate failure reporting. The tests still need W01/W02 coverage. |
| `scripts/deploy.sh`, `scripts/deploy_remote.sh` | Remote work is grouped under a host lock and build pipeline failures propagate. W01/W02 remain blocking; tested-revision pinning remains a limitation. |
| `scripts/deploy_lock.sh` | Refusal, bounded waiting, nesting, descriptor inheritance and cleanup pass the focused suite. No additional defect reproduced. |
| `scripts/pg_backup.sh` | Partial-file publish, dump/gzip status checks, completion marker, space floor and retention pass focused tests. This does not establish database restorability: a real restore drill was not performed. |
| `scripts/download_weekly_backup.sh` | Excluding `.partial` files is appropriate; no new issue found. |
| `scripts/tests/test_host_lock.py` | All **25** current tests pass, but they miss W01/W02. The proposal's count of 23 is stale. |
| `frontend/src/components/PriceChart.tsx`, `PriceChart.commands.test.tsx`, `frontend/src/lib/intradayHistory.ts`, `intradayHistory.test.ts` | Warmup-history and chart-command checks pass; full typecheck passes. No new blocker found in these changes. No interactive browser visual check performed. |
| `services/market-data/src/services/daily_cross.py`, `tests/test_daily_cross.py` | All six helper tests pass, covering forming candles, close buffer, direction, insufficient history, HK/US timing and recursive-seed reproduction. Provider finalization is still inferred from a 15-minute buffer, as documented. |
| `services/market-data/src/services/scheduler.py` | Ordinary alert/email checks pass; integration into the existing price-window regression needs W03 repaired. |
| `docs/audits/2026-10-22-backup-build-lock-proposal.md` | Correctly says local/not deployed and lists the cron-wrapper rollout still to do. Fix verification defects, update the test count, then validate the real backup rollout and restoration separately. |
| `docs/audits/2026-10-22-news-measurement-and-nu-cross.md`, accompanying `measurements.json` | Measurements and limitations are documented. W04 remains present. Historical recorded measurements were not re-queried. |
| `docs/2026-10-22-news-classification-evaluation-plan.md` | A proposal, not an implemented evaluator or proof of classification/trading quality. Frozen evidence, human reference labels and prospective cost-aware evaluation remain future work. |
| `docs/2026-10-23/deployment-verification/README.md`, `production.json` | Correctly distinguish committed deployed code from pending chart/alert/backup work. JSON parses. Its production checks are historical evidence; production was not re-inspected in this review. |

New dependencies are still untracked. A tracked-files-only commit would omit `intradayHistory.ts`, `daily_cross.py`, `deploy_remote.sh`, backup tooling and tests. Include the required implementation and tests together when preparing a future scoped commit.

## Verification performed

| Check | Current result |
|---|---|
| Host lock/backup/deploy suite | 25 passed |
| Daily-cross helper suite | 6 passed |
| Existing alert/dedup/environment/email suites | 85 passed |
| Chart command and intraday-history tests | 12 passed |
| Full frontend TypeScript check | Passed |
| Price-window regression, current tree | **4 failed, 4 passed** |
| Price-window regression, clean committed checkout | 8 passed |
| False deployment-success witness | Both unhealthy and missing-container cases reproduced |
| Changed shell script syntax, tracked diff whitespace | Passed |
| Pending measurement/deployment JSON parsing | Passed |

Full application suites, a production deployment, a real backup and an interactive browser acceptance test were not performed for this review. Focused passing checks do not override the reproduced failures.

The initial recommended fix order has been completed for W01–W04 below. Further work remains separate from these four defects: pinning the tested deployment revision, preparing complete scoped commits, and validating the real backup/cron rollout and restore process. Keep the news evaluation plan distinct from implemented features.


## Completion — local fixes, not deployed

| Finding | Implemented behavior | Regression coverage |
|---|---|---|
| W01 | `deploy_remote.sh` inspects the exact container name and compares `running/healthy`; missing, unhealthy, starting, stopped, no health check and inspection failure cannot pass. Declared services must be running, and failure to read Compose configuration also fails verification. | Both direct remote entry and workstation wrapper are tested. |
| W02 | Verification's failure status survives successful cleanup, propagates to the outer caller and records `state=failed` in the lock status. Cleanup still runs. | Missing/unhealthy cases now exit 1 and record failed status, rather than returning 0. |
| W03 | The SQLite price-window harness supplies the real query's symbol, stock market, cross-symbol set and per-symbol maps. It exercises ordinary and cross paths, retaining unrelated-symbol/timeframe filtering and chronological assertions. | 15 price-window cases pass, including empty history and 1,200 rows; cross evidence retains at most 1,000 rows while ordinary calculations retain 260. |
| W04 | Empty-cohort edge is null, verdict/status explicitly insufficient evidence, and the reason is returned. Measured zero win rate remains a valid measurement. Unknown comparisons sort last. A v2 API cache key prevents reuse of fabricated old edges. | Actual aggregation block is executed for each missing-cohort combination, measured signs/zero and mixed sorting; existing alpha/hurdle/news tests pass. |

`FilterAuditComparison.tsx` renders an unavailable edge and “Insufficient evidence” when either cohort is empty, even if an older backend supplies a numeric edge and predictive verdict. The API type allows null edges. The Filter Audit page explains that measured comparisons are descriptive rather than validated predictions. Existing measured comparison thresholds are unchanged; no classifier, trade setting or calibration promotion behavior changes.

Updated files for these fixes: `scripts/deploy_remote.sh`, `scripts/tests/test_host_lock.py`, `services/signal-engine/tests/test_technical_signal_price_window.py`, `services/signal-engine/src/api/analytics.py`, new `services/signal-engine/tests/test_filter_audit_empty_cohorts.py`, `frontend/src/lib/api.ts`, `frontend/src/pages/signal-accuracy.tsx`, and new `frontend/src/components/FilterAuditComparison.tsx` with its tests. The earlier pending chart, daily-cross, backup and lock implementation stays intact.

Final validation on the current complete working tree:

- Signal-engine full suite: **955 passed, 1 skipped** (35.88 seconds).
- Frontend full suite: **819 passed**, 69 test files; full TypeScript check passed.
- Host script suite: **38 passed**.
- Market-data focused daily-cross/alert/email suites: **91 passed**. The full market-data suite was not rerun for these fixes; its application source was not changed in this round.
- Targeted analytics/price-window group: 52 passed; targeted UI/chart/history group: 19 passed (subsets of the full suite counts, not additional totals).
- Shell syntax and tracked-diff whitespace checks passed. No production build, live deployment, cron edit or restore drill was performed.

Guard-removal checks ran in temporary copies, leaving the real working tree unchanged: restoring the broad healthy-substring comparison fails 4 cases; discarding verification's exit status fails 10; substituting zero for an absent cohort again fails 4 aggregation cases. See [guard-removal results](evidence/2026-10-23-pending-review/guard-removal-results.json), the [portable runner](evidence/2026-10-23-pending-review/check-guard-removal.py), [fixed deployment witness results](evidence/2026-10-23-pending-review/deploy-verification-fixed-results.json) and [validation summary](evidence/2026-10-23-pending-review/fix-validation.json). The original pre-fix witness results are preserved separately.


## Documentation and tracker follow-up

The [consolidated October 23 session record](../2026-10-23/session-index.md) now links deployed work, local repairs, validation and outstanding research/rollout. Improvements tiers **468–473** add 16 status-specific entries. W01–W04 remain in progress because their local fixes are not deployed. Only deployed completed work is marked done; incomplete identity/replay and planned features remain open. Existing archive-capture deployment wording was corrected without claiming a completed scheduled run.

New in-progress tracker entries now initialize with their declared status; saved user choices still override ordinary defaults and deployed done seeds retain precedence. Validation after this follow-up: **820 frontend tests passed**, including **13 tracker visibility/status checks**, and full typecheck passed. Earlier counts above describe the pre-tracker fix phase. The tracker and docs are local, unstaged and uncommitted; no production change was made.
