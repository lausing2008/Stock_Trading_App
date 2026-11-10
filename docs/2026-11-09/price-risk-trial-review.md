# Price-risk trial review: five sessions against the go-live criteria

Read-only, 2026-11-09 evening, from `price_risk_scans`, `risk_events` and `risk_event_receipts`
on production. Trial sessions: Nov 2, 3, 5, 6 and 9. Nov 4 is excluded because it is missing its
15:40 scan. Every session has all five scans under one release and configuration hash, with
delivery off. Criteria: [design doc, "Go-live criteria"](../features/2026-10-12-market-company-risk-alert-design.md#go-live-criteria-review-item-4--for-enabling-email).
Evidence save: `~/stockai-evidence/2026-11-09/`.

## Verdict

**Not ready to enable email.** The trial met five of the six criteria; **volume fails**, at about
2.5× the target. (A first reading also failed duplicates; that was corrected the same evening,
see criterion 4.)

Separately, the review found **a false crash alert**. A stock split that had not yet been
recorded made SOXX read −69%. With email on, that alert would have been sent. Both problems
need code changes, followed by a fresh shadow period. Thresholds were not changed during the
trial.

## Criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Volume ≤ ~10 events per session for the enrolled account; digests ≤ 6 per day | **Fail** (digests pass) | Events reaching user 1, the only matched account: 127, 20, 208, 26 and 16 (median **26**). Digests: 5, 3, 5, 5 and 5. |
| 2 | Freshness: intraday events use `live_quote` or settled bars | Pass | Every intraday US event used `live_quote`. About 430–440 live quotes per session over about 450 US subject-scans. At most 2 refusals per session (`quote_unusable` or `quote_mismatch`). |
| 3 | Detection delay ≤ ~2 h | Pass, by schedule | All 25 scheduled scans ran. The gaps between slots are at most 2 h (10:05, 12:05, 14:05, 15:40). |
| 4 | No repeated (subject, rule, session, step); no slow decline while a pullback is open | Pass (corrected) | SOXX was recorded twice, as a watched symbol and as a benchmark, on Nov 2, 4 and 5. **Corrected:** benchmark scope is forced to shadow by design (2026-10-24), so it never delivers; only the symbol event could reach a user. My first reading counted benchmark receipts as deliveries. NET's second event on Nov 2 is a correct new episode after a recovery. Slow decline never fired while a pullback was open; it fired 1–2 h *before* one, on six subjects, so one decline produced two alerts. |
| 5 | Re-alert after recovery observed | Pass | 28 events had `new episode after recovery`. Episodes closed per session: 14, 10, 5, 14 and 23. |
| 6 | Not over-suppressed | Pass | All 2,231 `same_episode` suppressions had an earlier event for the same subject and rule within 15 days, so suppression only removed repeats. |

## The false crash alert (blocking)

SOXX had a 3-for-1 split with an ex-date of Nov 5. yfinance's corporate-action record for it was
created at **2026-11-06 00:50 UTC**, after that session. In the Nov 5 10:05 scan, the live
quote of 139.00 was compared with the Nov 4 close of 450.41:
- `session_decline` step 4, **−69.1%**;
- `pullback` step 4, **−72.9%**.

Both were marked `provisional`, and both produced a receipt for user 1. The detector already
treats a *recorded* action it cannot apply as unmeasurable; the comment says "a 2:1 split reads
as a 50% crash". That is why SOXX was correctly excluded on Nov 9. An action not yet recorded on
the ex-date still gets through, because `yfinance` coverage is `response_only` and arrives late.

Proposed guard (not built): before any event is raised, treat a one-session move whose ratio
to the prior close is within about 2% of 1/n (n = 2…10), or any one-session decline beyond about
40% on an ETF or benchmark, as `unmeasurable: possible unrecorded corporate action`. The
alert waits until the corporate-action refresh confirms or rules out a split. The cost is a
delay on a genuine crash of that exact size.

## Where the volume comes from

| Session | Events | Note |
|---|---:|---|
| Nov 2 | 127 | Broad semiconductor sell-off. 31 US symbols newly crossed the session-decline ladder in the 14:05 scan alone. |
| Nov 3 | 20 | |
| Nov 5 | 208 | Market-wide drop (SPY −4.4%, QQQ −6.5%). 65 symbols newly crossed at 10:05, 45 more at 12:05. |
| Nov 6 | 26 | |
| Nov 9 | 16 | |

- **By rule and step:** session decline 264 (step 1: 90), pullback 119, slow decline 12,
  basket 2.
- **Dropping session-decline step 1 (−3%)** gives a median of 18, still above 10.
- **One alert per subject per day** gives a median of 15. Nov 5 is still 90.
- **Most of the volume comes from market-wide days.** One basket-breadth event already said
  "semiconductors are down broadly" on Nov 5, and then 200 per-symbol alerts said it again.
  The change that fits the evidence is a **market-wide gate**: while a benchmark or basket
  breadth event is open, roll individual symbols' session declines into that one alert. A
  symbol still alerts on its own when it falls clearly more than its benchmark. This is a
  design change, so it is proposed here, not built.

## Usefulness

These five sessions can't show whether the alerts help. Returns after an alert move with the
market:
- 1 day after an alert: a median of **+1.1%** above the median watched US stock (n = 167). That
  mostly reflects the rebound after Nov 5.
- 3 days after: **−3.4%** (n = 66), dominated by the same sell-off.

Two market-wide days carry most of the events, so neither figure is evidence of usefulness.

## Built 2026-11-09 (commit `3369e1a5`)

- **Unrecorded-split guard:** any one-session fall of 40% or more is held as
  `possible_unrecorded_corporate_action` until corporate actions are refreshed after that
  session's close. A recorded split takes the existing path. A genuine crash of that size is
  delayed until the nightly refresh (about 00:50 UTC).
- **Market-wide gate:** while a market's median measured session move is −1.5% or worse over
  at least 20 subjects, a stock's session decline alerts only when it is at least a ladder
  step (3%) worse than that median. One `market_breadth` event reports the market move. Calm
  markets are unchanged. Pullback and slow-decline rules are not gated.
- **Benchmark duplicate:** no code change; a probe now asserts a watched benchmark ETF is
  deliverable exactly once.
- **Expected effect on volume:** simulated on the trial with end-of-day medians, Nov 5 falls
  from about 220 events to about 120 and Nov 2 from about 140 to about 105. Ordinary days
  (16–26 events) are unchanged, so **volume is still likely to fail** the ≈10 target. Meeting it
  needs a threshold decision, for example raising a single stock's first session step from 3%,
  which is the user's to make.

## Recommendation (as approved)

1. Keep email **off**.
2. Build the changes above, then run a new five-session shadow period from Tue Nov 10,
   measured with the same criteria and `scripts/measurement_report.py`. The new rules change
   the configuration hash, so the new trial cannot be pooled with this one.
3. Go live only after that period passes, with one account first, as the design states.

## Side notes

- **Sunday's watchlist rotation changed the trial's universe** (Nov 8, 22:00 UTC). It removed
  TQQQ, DIVO, AAOI and AAOX from every watchlist and added others, a net of −3 US subjects
  (114 → 111). The trial rules fix the configuration and release, not the universe. A future
  trial should note any rotation that falls inside it, or freeze the subject list. The
  rotation also dropped and re-added GPN, GRNT and SCHD in the same run. Not investigated
  further.
