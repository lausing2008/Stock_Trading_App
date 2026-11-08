# Little RZY evaluation v2 — first results (2026-11-08)

Run `2026-11-08-16fe5a81-all` (code 16fe5a81, snapshot exported 2026-11-08 22:25 UTC, bars sha256
`cb264cdc…`), published as run `d2031de6…` and shown on the Little RZY page. Questions were fixed
beforehand in `rzy-evaluation-v2-preregistration.md`. Full output: `rzy-eval-runs/2026-11-08-16fe5a81-all/`.

## Answers

| ID | Question | Result | Verdict |
|---|---|---|---|
| **H3 (primary)** | Intraday trades aligned with the daily trend vs not aligned | +0.095R vs −0.067R edge; difference **+0.163R**, 95% CI +0.026 to +0.312 (212 vs 474 trades) | First group better |
| H1 | Baseline edge, all data | −0.017R, CI −0.080 to +0.047 (744 trades) | No measurable edge |
| H2 | Baseline edge, unseen sessions (after 2026-10-20) | **−0.189R**, CI −0.276 to −0.098 (125 trades, 86 symbols) | Below zero |
| H4 | Same as H3, next higher timeframe | −0.001R difference, CI −0.144 to +0.147 | No measurable difference |
| H5 | All other timeframes agree vs the rest | +0.327R difference, only 24 trades | Insufficient sample |

## How to read them (checks made before publishing)

1. **The live rules still show no edge**, and on the 14 sessions no evaluation had seen they were
   worse than random timing. That is not one bad day: the unseen mean R was negative on 13 of 14
   days and in every timeframe, direction and alignment group; trades spread over 86 symbols.
   Unseen win rate 9.6%; 115 of 125 exits were the close-through-the-line exit.
2. **H3 passed on all data, but it did not hold in the unseen sessions.** By month (aligned vs not,
   edge R): Aug +0.157 vs +0.010, Sep +0.092 vs −0.004, Oct +0.096 vs −0.146, Nov −0.211 vs −0.307
   (11 vs 29 trades). Unseen only: aligned −0.235 (33) vs not −0.172 (92). It holds for longs and
   shorts separately. Read it as a **lead to confirm on later sessions**, not a rule change. Even
   aligned trades lost money in absolute terms (mean −0.038R): the edge is relative to random timing.
3. **Entries look like the weak point, not exits.** Average best move during a trade +0.48R; only
   12.8% ever reached +1R; 3.0% exited at the target. Trades rarely move far in their favour, so
   holding them longer (research_stop_target_only: −0.178R edge, worse than control) does not help.
4. **Luck check:** 49 slice cells with ≥30 trades; 0 better than control, 4 worse; about 1.2 per
   side expected by chance. Two of the worse cells are the unseen period; two are rule variants
   (line_stop, stop/target only).

## Deviations and data notes

- **Market regime is unavailable**: SPY is not in the stored universe, so every trade is `unknown`.
  The predeclared symbol is kept; VOO (same index, present) should be declared as the regime proxy
  **before** the next run, as a new analysis version.
- 5m history now starts 2026-08-04 (stored 5m bars are retained 90 days), so the "seen" period is
  not identical to v1's (which began 2026-07-21). v1's figures are not directly comparable.
- Execution assumptions as in v1 (fillable at next open, 0.5%/yr borrow).

## Next

- Re-run on a later snapshot (export → evaluate → publish) once ~4 more weeks of sessions exist;
  H3 is the question to watch, judged on sessions after 2026-11-08 only.
- Investigate why recent sessions are worse than random before any live use; no rule change is
  justified by this run.
