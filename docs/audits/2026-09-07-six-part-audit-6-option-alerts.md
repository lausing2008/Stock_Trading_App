# Deep Audit Series (2026-09-07): Option Alerts — 6 of 6, SERIES COMPLETE

**Domain:** options-flow, short-squeeze and gamma-unwind alerts — gating, outcome evaluation,
and the Unusual Whales data feeding them.

**Result: 3 confirmed findings, one CRITICAL and user-facing.** The most valuable outcome is
arguably Finding 2, which **refutes** the alarming statistic that framed this domain.

---

## Finding 1 — CRITICAL — 71% of options-flow alerts recommend ALREADY-EXPIRED contracts

**File:** `unusual_whales.py:1210` (and `:1122`); consumed at `scheduler.py:4675-4707`

`get_flow_alerts()` bounds only **how recently the alert fired** (`newer_than = now − 48h`). It
relies on `max_dte=45` to bound the contract's expiry — but **UW computes `max_dte` relative to
the alert's own `created_at`, not relative to today.** An alert created 47 hours ago on a 1-DTE
contract has an expiry that is now two days in the past, and **nothing anywhere compares expiry
against today** (verified: zero such filters exist in the module).

### Verified by me against production

| | |
|---|---|
| Total alerts | 1,552 |
| **Fired on an already-expired contract** | **1,102 (71.0%)** |
| Average staleness | **20.9 days** |
| Worst | **62 days** |

**Still happening**, not historical:

| fired_date | total | expired | valid |
|---|---|---|---|
| 2026-09-03 | 31 | 16 | 15 |
| 2026-09-04 | 36 | 13 | 23 |
| 2026-09-05 | 28 | **14** | 14 |
| 2026-09-06 | 13 | 2 | 11 |

**Real emailed examples:**

| symbol | contract | expiry | fired | stale | premium |
|---|---|---|---|---|---|
| SPCX | SPCX260828C00110000 | 2026-08-28 | 2026-09-02 | 5d | **$8,784,730** |
| TSM | TSM260821P00460000 | 2026-08-21 | 2026-09-02 | 12d | $5,982,888 |

A user received an email presenting an $8.8M-premium call as live, actionable positioning — on a
contract that had expired five days earlier. **Acting on it is impossible; the contract does not
exist.**

This is `AUD-OPTIONSFLOW-STALEALERTS` recurring in a narrower form. That fix correctly closed the
multi-week case, but the docstring at `:1177` — *"a genuinely stale alert can never reach a
caller at all"* — is **false** for anything expiring inside the 48h window.

**Fix direction:** a client-side post-filter `expiry >= date.today()` after
`_parse_flow_alert_rows()`. No server-side parameter can express this.

---

## Finding 2 — HIGH — The 20.6% bearish win rate is a measurement artifact, NOT evidence about the criteria

This answers the question that framed the domain, and the answer is **neither** of the two
hypotheses I posed.

**Direction assignment is CORRECT** (rules out mis-labelling). `_options_flow_alert_direction()`
(`scheduler.py:4513-4526`) implements the standard 4-way ask/bid read; live UW data shows the
split is genuine and unambiguous (0 ties, 0 both-zero), so the `ask >= bid` tie-break is inert.
I had already verified the outcome sign handling separately (bearish wins only on negative
returns, 157/0; bullish only on positive, 420/0).

**The criteria are NOT proven anti-predictive** (rules out a real edge defect). The entire
resolved dataset is **2 entry dates**:

| entry_date | n | symbols | avg 1d | % that rose |
|---|---|---|---|---|
| 2026-09-02 | 1,145 | 49 | +1.95% | **82.6%** |
| 2026-09-03 | 299 | 29 | +2.91% | 68.2% |

And **both directions show nearly identical positive returns**:

| direction | dates | symbols | n | avg 1d |
|---|---|---|---|---|
| bearish | **2** | 46 | 761 | **+2.21%** |
| bullish | **2** | 42 | 683 | **+2.08%** |

What was measured is **one broad two-day rally**, in which anything labelled bearish loses by
construction. "n=819 bearish" is 819 *contracts* across 46 symbols on **2 days** — an effective
independent sample closer to 2 than 819.

**The forward risk:** `_build_options_flow_alert_calibration()` (`scheduler.py:4567`) has a
30-outcome floor and **no time-diversity requirement**. Once 10d outcomes resolve it will emit
`calibrated_win_rate ≈ 0.20` for bearish **into real user emails** as a "measured historical win
rate" — when it measures one rally.

**Recommendation: do NOT retune the direction logic.** Add a distinct-fired-date floor to the
calibration gate and re-measure after ~20+ trading days.

This is the third time in this codebase's audit history that a clustered sample produced a
confident wrong conclusion — see the two retracted findings in the 2026-09-05 cycle.

---

## Finding 3 — MEDIUM — No market-hours gate; alerts fire overnight on a closed market

**File:** `scheduler.py:4586-4632`, registered `:12096-12103`

The job runs every minute, 24/7. `check_short_squeeze_alerts()` (`:3111-3116`) explicitly returns
early when both markets are closed; the options-flow job has no equivalent. Because `newer_than`
is a 48h window, the same UW rows stay eligible all night, and the only thing preventing repeat
email is a 30-minute Redis cooldown that expires ~16 times overnight.

**Evidence:** all 28 alerts on 2026-09-05 fired at `00:01` (~8pm ET, market closed); same on
09-06. It is also pure UW quota waste — a live concern given the documented rate-limit pressure.

---

## Answers to the domain's questions

**Q1 — `SweepsFollowedByFloor` (n=2) is genuinely rare, not dead.** It is UW's *server-side*
classification; this codebase never references it, so nothing local can make it unreachable.

**Q2 — `has_sweep` is always true, and gating on it IS inert.** `scheduler.py:4679` passes
`is_sweep=True`, which UW treats as a hard binary filter. So the column is a constant. No
downstream filter currently gates on it, but one added later would be a silent no-op.

**Q3 — short_squeeze gating is healthy, and the UW gap has CLOSED.** `get_short_interest()` is
now wired into the real-time alert (`scheduler.py:3289-3316`,
`AUD-SQUEEZE3-UWSHORTINTERESTCORROBORATION`) — the prior audit's finding is resolved. Verified
live: **20 symbols currently pass both the ≥15% short-float floor and the 30-day freshness
gate**, so the 14-day firing silence is the ≥3% move + RVOL requirement not being met, not a
dead condition.

**Q4 — Dedup is real and working.** Two layers: a per-`(user, option_chain)` Redis set and a
per-`(user, symbol, direction)` 30-min cooldown. The 762 `RepeatedHits` count is the *recorded
candidate* table (uncapped by design), not emails sent. The cooldown fails open on Redis error —
deliberate and documented.

**Q5 — The evaluator is clean.** Entry is `fired_date + 1` (`:5842`), forward windows measured
from `entry_date`, future targets skipped, 10-day censor grace applied. **0 rows** have
`entry_date <= fired_date`. Returns stored as fractions and compared against a fraction hurdle
(0.005) — no unit mismatch.

**Zero 5d resolution is CORRECT, not a stall** — oldest alert 2026-09-01, today 2026-09-08, so
nothing is yet 8 days old. (Verified before dispatch; the same explanation held in the prior
audit and was re-checked rather than assumed.)

---

## CHECKED AND FOUND CLEAN

`_options_flow_alert_direction()` 4-way logic; `evaluate_options_flow_alert_outcomes()`
(no lookahead, correct bearish-thesis sign, no unit mismatch); falsy-zero sweep (`ask/bid or 0.0`
is guarded by an explicit both-zero check; `total_premium or 0.0` is sort-only);
`_bounded_options_flow_symbols()` caps on both union arms; per-recipient send isolation;
`_record_options_flow_alert_outcome()` existence check + fail-open;
`_build_options_flow_alert_calibration()` returns `None` rather than a fabricated 0.0 below its
floor; UW `newer_than` epoch-seconds handling verified live.

---

## The through-line, across all six domains

| domain | shape of the defect |
|---|---|
| 1 Decision-Making | config that never arrived at the code enforcing it |
| 2 ML Training | work that never completed; quality flags that never refreshed |
| 3 AI Signal | a self-correcting mechanism correcting in the wrong direction |
| 4 Entry Timing | gates that exist, are configured — and cannot fire |
| 5 Paper Trading | an exit that fires when it must not |
| 6 Option Alerts | stale data presented as live; a statistic that measures the sample, not the system |

**Not one of the 17 findings was a crash, an exception, or anything that would appear in an
error log.** Every one was a silent wrong answer: a gate that passed everything, a flag that
never updated, a message that was confidently false. That is the single most important pattern
in this series — this platform's failure mode is not breaking, it is quietly not working.

**Not fixed — reported only, per the agreed protocol.**
