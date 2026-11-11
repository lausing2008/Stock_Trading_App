# Call Finder replay: does the half-move rule pick better calls? (November 11, 2026)

Research only. Nothing live was changed by this report. Design: [Call Finder](../features/2026-11-10-options-tab-call-finder-design.md). Method: [candidate outcome study](../features/2026-10-29-options-candidate-outcome-study-design.md), version 2. Evidence: [the evidence folder](evidence/2026-11-11-call-finder-replay/).

## Bottom line

- **The Call Finder's strike rule does not pick better contracts. It picks less leverage.** Against the aggressive alternative, the at-the-money call in the same expiry, and the desk's preset, the finder's monthly lead moves **opposite to the stock**: correlation −0.86, −0.77 and −0.85.
  - It trails by about 8 points per trade in months the stock rose more than 1%.
  - It leads by about 3–6 points in months the stock fell.
  - It is ahead in only about half the months: 13, 14 and 15 of 28.
- **The development and held-out results flip for the same reason.** In the mostly rising development period, the higher-leverage choices did better. In the flat held-out quarter (July–September 2026), the finder did better. That is market direction, not skill.
- **One consistent advantage: short holds, and it is cheaper trading.** For the 7-day hold the finder beat the preset by +1.7 points per trade in development and +2.8 held-out (intervals exclude zero). It led in **25 of 27 months**, including 11 of 12 rising months, and the lead does not track the stock (correlation 0.19). The two picks have almost the same expiry (median 33 and 32 days) and strike (+0.5% and 0% from the price). The difference is the quoted spread: **3.3% against 4.9%**. That saving roughly equals the edge, the same cost effect the October study found for the research lead.
- **What this means for the card:**
  - The finder is useful as a disciplined, liquid, lower-leverage choice.
  - It does not forecast which contract will earn most.
  - Its "research candidate" label stays.
  - The card should say plainly that it trails the aggressive pick in rallies and holds up better in flat or falling markets (proposed below).

## What was run

- **Run:** November 11, 2026, about 02:15–04:35 ET, read-only, inside `stockai-market-data-1` against the deployed code.
  - The deployed `option_call_finder.py`, `option_pricing.py` and `options_strategies.py` were hash-checked equal to the tested files.
  - `provenance.json` records the source hashes.
- **Size:** study v2, 154 symbols, **353,336** trade rows, entries on the first archived session of each ISO week, 2024-08 to 2026-10.
- **New choices, long calls only** (all at each holding period, 7, 14 and 28 days, from the real `option_call_finder` on that session's chain only):

| Choice | What it is |
|---|---|
| `finder` | The half-move pick |
| `finder_aggressive` | The highest return at the full target |
| `finder_atm` | The eligible contract nearest the money in the **same expiry**, which isolates the strike rule from the expiry rule |

- **Assumptions:**
  - The target is the finder's default, one implied standard deviation up.
  - No historical IV rank, events or 200-day average were available, so the finder's warnings do not apply.
- **Fills and exits:** unchanged from v1. Buy at the archived ask, sell at the bid. Holding exit on the first session on or after the holding period; expiry exit at intrinsic value.
- **Not available:** on 4,355 symbol-horizon-sessions the finder had no pick (no expiry in its window or nothing passed its checks). These are counted, not guessed.
- **Periods:** the same frozen split as v1. Development to 2026-06-30; held-out 2026-07-01 to 2026-09-30.

## Paired results (holding exit, points of premium per trade, 95% intervals resampling entry dates)

| Finder minus | Hold | Development | Held-out |
|---|---|---|---|
| Aggressive alternative | 7 d | −1.20 [−2.33, −0.14] | +0.99 [−0.58, +2.48] |
| | 14 d | −5.12 [−8.65, −1.87] | +3.04 [−0.43, +6.46] |
| | 28 d | −10.12 [−14.44, −5.98] | +4.15 [+1.36, +6.52] |
| At-the-money, same expiry | 7 d | +0.66 [+0.02, +1.36] | +1.17 [+0.34, +1.98] |
| | 14 d | −2.14 [−3.90, −0.63] | +2.61 [+0.97, +4.32] |
| | 28 d | −2.29 [−3.72, −0.93] | +2.38 [+1.00, +3.61] |
| Desk preset | 7 d | **+1.68 [+1.01, +2.37]** | **+2.83 [+1.61, +3.84]** |
| | 14 d | −1.55 [−3.68, +0.52] | +4.35 [+1.35, +7.46] |
| | 28 d | −14.83 [−22.75, −7.06] | +1.49 [−10.60, +11.45] |

The expiry-exit rows, the excluding-earnings variant and the random-pick comparisons are in `summary.json`. They tell the same story. The 7-day row against the preset is the only one that is positive in both periods; it is in bold.

## Month by month (the reading that decides it)

| Finder minus | Months ahead | Correlation with the stock's move | Mean when stocks rose >1% (16–17 months) | Mean when stocks fell (5 months) |
|---|---|---|---|---|
| Aggressive alternative | 13 of 28 | −0.86 | −7.99 | +2.83 |
| At-the-money, same expiry | 14 of 28 | −0.77 | −1.89 | +1.55 |
| Desk preset | 15 of 28 | −0.85 | −7.86 | +5.60 |
| Desk preset, 7-day hold only | 25 of 27 | +0.19 | +2.03 (11 of 12 months ahead) | — |

All holding periods are pooled in the first three rows. Scripts: `monthly-exposure-analysis.py` and `short-hold-analysis.py`.

## Limits

Unchanged from the study's design:
- end-of-day quotes, with no real fills, size or price impact;
- no early assignment or intraday paths;
- 154 captured symbols only;
- the held-out quarter is 13 weeks and flat, so it cannot separate skill from exposure on its own; the monthly reading above is what does.

The LEAPS pick is **not** tested here: a 180-day hold gives too few independent windows in a 26-month archive.

## Proposed (not done)

1. **Card wording.** Under the near-term pick, add: "Lower leverage than the aggressive alternative: in the replay it trailed in rising months and held up better in flat or falling ones. Its one consistent edge was a narrower spread." The Call Finder stays a research candidate.
2. **Spread first for short holds.** For holds of a week or less, rank by spread among contracts within a narrow delta band. This is the one effect measured in both periods and in nearly every month. It needs its own replay arm before any change.
3. **A forward record.** The desk record began on November 11. Re-run this comparison on served picks after a few months.
