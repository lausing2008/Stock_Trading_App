# Little RZY evaluation results — run 2026-10-20 07:09 UTC

> **Superseded** by the [corrected rerun](../2026-10-20/little-rzy-evaluation-rerun.md): this run's controls accrued short borrow after early exits (RZA-09) and accepted shortened windows (RZA-10). The conclusion (no edge) is unchanged; use the rerun's figures. Raw outputs preserved in [`rzy-eval-runs/2026-10-20T0709Z-cfb40e0b/`](../2026-10-20/rzy-eval-runs/2026-10-20T0709Z-cfb40e0b/).

First real-data run of the [evaluation framework](little-rzy-evaluation-framework.md) for `LITTLE_RZY_INSPIRED_V1` (config `Config().hash` defaults, no tuning). Read-only against production stored bars; runtime 18 min. Raw outputs were `report.json` and `trades.csv` (kept on EC2 under `/tmp/rzy-eval/`, not committed).

## Coverage

| Timeframe | Symbols | Segments | Bars | Window |
|---|---:|---:|---:|---|
| 5m | 149 | 375 | 704,786 | 2026-07-21 → 2026-10-19 |
| 15m (derived) | 148 | 243 | 230,624 | same |
| 1h (derived) | 143 | 165 | 59,494 | same |
| 1d | 152 | 296 | 103,287 | 2023-12-26 → 2026-10-19 |

Execution inputs are research assumptions (fills possible; shorts borrowable at 0.5%/yr). Costs: $0.005/share per side, half of a 2 bp spread and 2 bp slippage per side.

## Headline

**No measurable edge.** 693 baseline trades, mean **−0.092R** (95% CI −0.159 to −0.023), win rate 25%, profit factor 0.71. Matched random-timing controls averaged −0.091R, so the edge is **−0.001R** (95% CI −0.068 to +0.067). The pattern's timing added nothing beyond direction, volatility-scaled brackets and costs. Long-only is the same (−0.091R, edge −0.002R).

| Slice (baseline) | Trades | Mean R | Control R | Edge (95% CI) | Verdict |
|---|---:|---:|---:|---|---|
| 5m long | 220 | −0.128 | −0.138 | +0.010 (−0.079, +0.114) | no edge |
| 5m short | 219 | −0.134 | −0.130 | −0.004 (−0.119, +0.120) | no edge |
| 15m long | 88 | +0.023 | −0.040 | +0.064 (−0.150, +0.301) | no edge |
| 15m short | 88 | −0.077 | −0.031 | −0.046 (−0.268, +0.197) | no edge |
| 1h long / short | 13 / 15 | −0.363 / +0.070 | | | insufficient |
| 1d long | 30 | −0.038 | +0.112 | −0.150 (−0.399, +0.156) | no edge |
| 1d short | 20 | +0.156 | +0.002 | | insufficient |
| Setup #1 | 539 | −0.100 | −0.090 | −0.009 | no edge |
| Setup #2 | 130 | +0.020 | −0.105 | +0.125 (−0.046, +0.328) | no edge |
| Unknown sequence | 24 | −0.530 | | | insufficient |

## Why: the exit rule ends trades almost immediately

- Exits: **621 of 693 (90%) on a close back through the structure line**, 46 at the stop, **26 (3.8%) at the target**.
- Planned targets are about 3.3R (5m), 3.5R (15m), 2.8R (1h/1d), but the median holding time is **4 bars**.
- Entries sit right on the line by design (the trigger requires a touch), so ordinary noise closes back through it on the next few bars, and the measured-move target is rarely reachable.
- Cost drag is largest where stops are tightest: on 5m the median risk is 0.82% of price and costs are **0.117R per trade**; gross mean R is −0.013, so costs turn break-even into −0.131R. On 15m costs are 0.069R (gross +0.042R → net −0.027R); on 1d they are 0.011R.

## Month by month (baseline)

| Month | 5m mean R (n) | 5m edge | 15m mean R (n) | 15m edge |
|---|---|---:|---|---:|
| 2026-07 | +0.012 (48) | +0.142 | +0.038 (15) | +0.115 |
| 2026-08 | −0.170 (154) | −0.038 | +0.135 (55) | +0.179 |
| 2026-09 | −0.130 (133) | −0.010 | −0.055 (66) | −0.031 |
| 2026-10 | −0.138 (104) | +0.017 | −0.227 (40) | −0.200 |

The 15m sign flips between months; no month is consistent enough to read as a regime effect.

## The five §11 comparisons (overall, all timeframes)

| Variant | Trades | Mean R | Edge vs control | Note |
|---|---:|---:|---:|---|
| Baseline | 693 | −0.092 | −0.001 | |
| Bollinger context off | 717 | −0.097 | −0.009 | no difference |
| No-chase filter off | 716 | −0.096 | −0.013 | no difference |
| Unrestricted sequence | 738 | −0.082 | −0.003 | no difference |
| Partial / runner exit | 683 | −0.101 | +0.007 | no difference |
| Line-only stop (spec's original) | 925 | −0.273 | +0.007 | **worse**: 3× the drawdown (286.8R vs 74.8R), tighter stops amplify costs |

None of the filters changes the outcome; the structural stop is clearly better than the line-only stop.

## Other counts

- Entry cancellations at the next open (gap past target/stop or R/R after the actual fill < 1.5): 77.
- Trades still open at a segment end (excluded): 2.

## What this does and does not establish

- It establishes that, as specified and with these costs, the default strategy did **not** beat random timing over Jul–Oct 2026 intraday and Dec 2023–Oct 2026 daily, on this universe.
- It does not establish that the measured-move idea has no value: the exit on any close through the line dominates every result, so the target logic is barely tested. That is the one diagnostic worth running next (stop/target exits without the line-close exit), as a **separately named research variant**, judged on the same control and month-by-month, not a change to live defaults.
- Intervals assume independent trades and six variants were compared; treat small differences as noise.
- Daily samples (n = 50) and 1h samples (n = 28) are too small for conclusions.

## Recommendation

Keep Little RZY as a research overlay and watch alert; do not rely on its entries. Highest-value next step: run the no-line-exit diagnostic variant (and 15m only, where costs are smallest relative to risk), then re-run monthly as history accumulates.
