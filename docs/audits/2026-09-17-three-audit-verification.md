# Verification of three 2026-09-17 audit documents

**Reviewed:** 2026-09-17. **Method:** re-derive every checkable claim against production or
source before recording it — the same discipline as
[2026-09-17-audit-review-and-t400-fixes.md](2026-09-17-audit-review-and-t400-fixes.md),
[2026-08-20](2026-08-20-external-audit-doc-review.md) and
[2026-08-22](2026-08-22-external-doc-reviews-and-actions.md), where raw data held up but
analysis conclusions had gone stale.

**Subjects:**

| Document | Scope |
|---|---|
| [system audit and trading roadmap](2026-09-17-system-audit-and-trading-roadmap.md) | A01–A16, already reviewed in the T400 pass |
| [production verification audit](2026-09-17-production-verification-audit.md) | read-only production checks, A17, feedback review |
| [September cohort audit](2026-09-17-september-cohort-audit-and-recommendations.md) | entry-cohort P&L, signal cohorts, C01–C04 |

## Verdict: all three hold up. 18 of 18 checkable claims were correct.

Not one number needed correcting. That is unusual, and worth stating plainly because the value
of these reviews comes from being willing to report the opposite.

| Claim | Source | Verification |
|---|---|---|
| A17 — settlement counter clobbered by a tuple | prod-verify §4 | **Reproduced.** TypeError on the first successful settlement |
| C01 — 2 fixes due, 0 snapshots | cohort §5 | `fix_records` = 2 rows, `fix_snapshots` = 0 |
| C01 — `take_fix_snapshot` drops `since` | cohort §5 | confirmed at `fix_effectiveness.py:168` |
| C01 — non-`ai_signal` domains rejected | cohort §5 | `HTTP 400` at line 165–166 |
| C04 — SSNLF stale, 137/138 US + 42/42 HK fresh | cohort §5 | exact match; SSNLF head `2025-11-07` |
| Paper trades: 128 total / 108 US / 20 HK | cohort §2 | exact |
| US entry cohorts Jun–Sep | cohort §3.1 | all four rows exact, incl. Sep `12/8/4/1/−1498.10` |
| SNOW −986.48, DELL −325.63 = 87.6% | cohort §3.3 | exact |
| Sep 1–8 vs Sep 9+ split (9 / 3 entries) | cohort §3.2 | exact, incl. `−1572.17` and `+74.07` |
| US SHORT BUY 38.68% n=1559 → 25.86% n=174 | cohort §4.2 | exact |
| US SWING SELL 40.00% n=100 → 64.41% n=59 | cohort §4.2 | exact |
| Options: 6 open CSPs, 0 closed, portfolio 2 | prod-verify §3 | exact, incl. full config |
| Equity 66,467 + 186,600 − 3,087 = 249,980 | prod-verify §3 | exact |
| AMD entered ITM at −1.34%, 9 DTE | prod-verify §7.4 | exact; TQQQ 1.11%/9d, PLTR 6.24%/9d |
| TSLA/META/NVDA cushions 10.13/10.11/10.95% | prod-verify §7.4 | exact, 15/29/43 DTE |
| Shared drift: 10 services × exactly 2 files | prod-verify §7.2 | exact; market-data + api-gateway clean |
| A16 — duplicate `aud-c2-calibrator-leakage` | prod-verify §7.3 | 1753 ids, 1752 unique |
| A11 — confidence = `abs(fused − 0.5) * 200` | prod-verify §7.3 | `signals.py:2714` |

The cohort audit's methodology is notably disciplined: it separates entry cohort from exit
month, refuses to add USD and HKD, declines to claim significance over four to six correlated
signal dates, and explicitly says one closed trade cannot support "100% win rate after the
fixes". Those are the distinctions that usually go missing.

## The one disagreement — and they are right, not me

The production audit disputes the T400 review's explanation of three timed-out test suites. The
T400 pass attributed them to that audit's three-at-a-time concurrent harness. The production
audit points out it ran them **one at a time** and still hit its 45-second limit.

Re-run sequentially here: `api-gateway` 2.1 s, `decision-engine` 2.4 s, `event-intelligence`
3.8 s. So both observations stand and the cause is environmental and **undiagnosed**. The T400
claim that concurrency explained it was a guess stated with more confidence than the evidence
carried; the audit was right to refuse it. Nothing here is proof of a production problem.

## A17 — fixed, because it was mine and it had a deadline

`settle_expired_positions` initialised `settled = 0` as its return counter and then reused the
same name for `_settlement_close()`'s `(price, session_date)` tuple, so `settled += 1` raised
`TypeError: can only concatenate tuple (not "int") to tuple` on the **first position that
actually settled**. Introduced by the T400 A05 fix on 2026-09-16. The first options expiry is
**2026-09-25**.

Three defects in one line, all reproduced before fixing:

1. TypeError on every successful settlement.
2. The counter reset each iteration, so a multi-position batch miscounted even without the raise.
3. The missing-price path returned `None` from a function declared `-> int`.

**The rollback matters more than the rename.** The exception fires *after* `portfolio.current_cash`
is credited and `pos.stage` is set to `"closed"`. `run_options_income_step` catches it and only
logs, then proceeds to `_snapshot_income_equity_curve`, whose `session.commit()` is
**unconditional** — so the partial mutation this function reported as *failed* would have been
committed by a later function. The fix wraps the batch and rolls back, making settlement
all-or-nothing.

### Why the existing test did not catch it

`test_t398_options_income_engine.py` asserted `"if settled is None:" in body` — a check on a
local **variable name**. It passed against code that raised on every run, then failed when the
variable was correctly renamed. Failing for a rename while passing for a real defect is exactly
backwards.

That file states the policy in its own docstring: the DB-facing functions are "thin glue…
nothing meaningful to assert against a MagicMock session". A17 is the counterexample — a ~40-line
fake session drives the real function end to end. Seven behavioural tests were added and
**sabotage-verified**: reintroducing the name collision fails 6 of 7; removing the rollback fails
exactly 1.

One source-text assertion was deliberately kept — that settlement calls `_settlement_close` and
never the lenient `_chain_as_of_on_or_before` window — because it pins a *choice of function*
rather than a variable name. That is where the line belongs.

## Two findings the audits did not have

**A18 — CI never runs on the deployed branch.** All three documents mention the `main`/`dev`
workflow triggers in passing, as "a separate release-coverage issue". Quantified:
`git rev-list --count main..prod` = **607**. CI triggers on push/PR to `[main, dev]`;
CLAUDE.md's deploy pattern is commit to `prod` → push → pull on EC2. So no deployed commit has
ever had `make test` run against it by CI. Tier 383 repaired `make test` so it *can* fail; this
is why that repair still reaches nobody.

**A19 — the options equity curve mixes two accounting bases.** Found while checking the audit's
own (correct) equity arithmetic. The 09-17 row reconciles to a 3,087 short liability. The 09-16
row is `178,567 + 73,100 = 251,667` — implied liability **exactly zero**, with three open
positions. The A04 fix landed 2026-09-17 in `0459097`, so 09-16 is a pre-fix row. The apparent
−1,687 move between the only two points on the curve conflates a real change with a change of
definition. Two rows exist; this is cheap to correct now and gets permanently harder.

## What was fixed, and what was deliberately not

**Fixed:** A17 (+ 7 behavioural tests, sabotage-verified) and A16 (the duplicate tracker id —
two *different* findings, tier 10 early-stopping leakage and tier 12 out-of-fold leakage, shared
one id and therefore one checkbox; the tier-12 entry is now `aud-c2-calibrator-leakage-oof`).

**Not fixed, recorded as Tier 384 action items:** C01–C04, A18, A19, A10, A15 (shared drift),
A07, A11, and the source-text-test sweep. These are either design changes, production data
mutations, or a deployment window — none of them a patch to slip in alongside a regression fix.
A01–A03 remain correctly deferred; the production audit's qualification is fair, though: "only
bites with a live broker linked" is too narrow, because the same reconciliation errors affect an
authorised sandbox workflow, and that correctness is testable now.

## The transferable lesson

The T400 pass fixed A05 by replacing a lenient lookup with an exact-session one, and introduced
A17 in the same line. The test that nominally guarded that function kept passing, because it was
asserting on the *text* of the fix rather than the *behaviour* of the function.

`docs/incidents/ci-failure-masking.md` already records the runner half of this: a failure must be
able to reach you. A17 is the assertion half: **the test must be capable of distinguishing the
defect from the fix.** Both are answered the same cheap way — sabotage the code and confirm the
test goes red. It took under a minute here and caught both halves immediately.
