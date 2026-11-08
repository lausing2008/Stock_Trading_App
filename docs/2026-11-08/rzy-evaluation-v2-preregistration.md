# Little RZY evaluation v2 — predeclared questions (2026-11-08)

Written and committed **before** v2 was run on any data. The definitions below are fixed in code
(`services/market-data/src/services/rzy_evaluation_context.py`); a later change to them is a new,
separately named analysis, never an edit of this one.

## Why v2

v1 (2026-10-19/20) replayed every stream on its own and compared each trade with random-timing
twins. Its answer: no measurable edge (baseline 692 trades, edge +0.001R, 95% CI by symbol −0.066
to +0.073). v1 could not answer three questions:

1. Does the pattern hold on sessions **no earlier evaluation saw**?
2. Do trades taken **with the higher-timeframe trend** do better ("combining streams")?
3. Where do trades go between entry and exit (could different exits help, or is the entry the problem)?

## The unseen period

The trade rules have not changed since the v1 snapshot was exported (2026-10-20 14:04:54 UTC):
later commits to `little_rzy.py` changed event identity and the live fill-eligibility check only,
and research bars bypass that check. Trades whose signal bar closed **after
2026-10-20T14:04:54Z** were therefore invisible to every evaluation that shaped the current rules.
This is the only genuinely out-of-sample evidence available. It is short (about 13 sessions).

## Labels (all causal: computed only from bars closed at or before the trade's signal-bar close)

| Label | Definition |
|---|---|
| Trend of a stream | The engine's own pivot trend (last two pivot highs and lows both higher = long, both lower = short, else neutral). `unknown` until the stream has two confirmed pivot highs and two lows, when the stream has no bar in its current segment yet, or when its last closed bar is older than its grace (intraday 20 h, daily 5 days). |
| `htf_daily` | Daily stream trend vs the trade direction: `aligned`, `opposed`, `neutral`, `unknown`. For intraday trades only. |
| `htf_next` | Same, using the next higher timeframe (5m→15m, 15m→1h, 1h→1d). |
| `confluence` | Across the symbol's other three timeframes: `all_aligned`, `majority_aligned`, `minority_aligned` (0 or 1 aligned), or `incomplete` if any is unknown. |
| `market_regime` | SPY's last closed daily close vs its 50-day simple average: `spy_above_50d` / `spy_below_50d` / `unknown`. |
| `time_of_day` | Signal-bar close in New York time: 09:30–10:30, 10:30–12:00, 12:00–14:00, 14:00–15:00, 15:00–16:00 (intraday only). |
| `volatility` | ATR ÷ close at the signal bar, split into thirds **within each timeframe** (descriptive; thresholds come from the sample itself). |
| `period` | `seen_by_v1` or `unseen_after_2026-10-20`. |

## Hypotheses

Edge = trade R − mean R of its 20 random-timing twins. Intervals are 95% bootstraps that resample
whole **symbols**. Groups with fewer than 30 trades are reported as insufficient.

| ID | Question | Test | Role |
|---|---|---|---|
| **H3** | Do intraday trades aligned with the **daily** trend have a larger edge than intraday trades that are not aligned (opposed or neutral)? | Difference in mean edge, aligned − not aligned; unknown excluded | **Primary** |
| H1 | Is the baseline edge above zero on all data? | Edge CI | Secondary (repeat of v1 on more data) |
| H2 | Is the baseline edge above zero in the unseen period? | Edge CI | Secondary |
| H4 | Same as H3 with the next higher timeframe | Difference | Secondary |
| H5 | Do trades whose other three timeframes all agree beat the rest (incomplete excluded)? | Difference | Secondary |

Only H3 is the primary question. Every other slice (regime, time of day, volatility, symbol,
month, sequence) is descriptive. The scorecard counts how many slice cells were tested and how
many would be expected to look "better than control" by chance alone (2.5% each side per cell).

## What a result can and cannot mean

- A supported H3 would say only that the alignment filter selected better historical trades. The
  filter must then be confirmed on later sessions before any rule change, and filtering is
  approximated by slicing the baseline trades (one position per stream means a real filter could
  occasionally let a different later setup trade).
- Most intraday history (5m from 2026-07-21) is one market period; results are read by month.
- Execution inputs (fillability, borrow) remain research assumptions, as in v1.
- No variant is selected for live use by this analysis.
