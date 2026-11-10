# Little RZY improvement study — predeclared confirmation tests (2026-11-10)

Written and committed **before** any confirmation-set trade of any variant below was produced.
The confirmation run writes only confirmation trades; the design runs wrote only design trades
(`scripts/rzy_research.py --keep`). The baseline's confirmation trades exist on disk from the first
run, but only their counts per set have been read.

## Data (fetched 2026-11-10 03:26–03:40 UTC from Yahoo Finance; hashes in `rzy-improvement-evidence/data-manifest.json`)

| Timeframe | Symbols | Bars | Span |
|---|---|---|---|
| 1d | 993 | 4,547,537 | 2005-01-03 to 2026-11-09 |
| 1h | 984 | 4,852,324 | 2023-12-12 to 2026-11-09 |
| 5m | 985 | 4,021,056 | 2026-08-17 to 2026-11-09 |
| 15m (derived from 5m) | 952 | 1,292,694 | same |

Universe: the platform's 159 stored US symbols plus today's S&P 500 and S&P 400 members (996
symbols). It holds only stocks that exist today, so absolute returns are flattered by survivors;
that is why every test also compares with random-timing twins drawn from the same symbols.

## Split (fixed in `rzy-improvement-evidence/split.py`, commit 02591c30, before any result)

- **Seen**: platform symbols inside the 2026-11-08 snapshot's period. Excluded from both sets.
- **1d**: design = signals before 2016-01-01; confirmation = 2016-01-01 onwards.
- **1h**: design = signals before 2025-07-01; confirmation = afterwards.
- **5m, 15m**: design = symbols whose sha256 is even; confirmation = odd. Same calendar days.

## What the design set showed (exploratory; this is what is being tested, not evidence)

1. Baseline, about 4,500 design trades: no edge on any timeframe (1d −0.014R, 1h +0.005R,
   5m −0.023R, 15m −0.083R vs twins; every interval spans zero).
2. No exit rule and no filter gave 5m, 15m or 1h a positive mean R. Daily-trend alignment (the
   earlier H3 lead) showed nothing on 1h (579 aligned trades: edge +0.004R).
3. Daily bars only: **long** setups in a stock above its 200-day average, exited **only at the
   stop or the measured-move target** (no exit on a close through the line), made +0.371R per
   trade (637 trades, win 37.0%), +0.275R better than twins drawn from the same context. With
   the live line exit the same entries made +0.050R. Shorts under the same exit lost 0.38R.

## Variants (definitions frozen in `scripts/rzy_research.py` at this commit)

| Name | Entries | Exit |
|---|---|---|
| `baseline` | live rules | live rules |
| `lt_base` | long only; daily close above its 200-day average at the trigger | live rules |
| **`lt_off`** | as `lt_base` | stop or measured-move target only |
| `lt_off_be1` | as `lt_base` | as `lt_off`, stop moved to breakeven plus costs once +1R is reached |
| `lt_off_t2` | as `lt_base` | as `lt_off`, target capped at 2R |
| `l_off` | long only | as `lt_off` |
| `lt_off_seqall` | as `lt_base`, every pullback in a trend (sequence filter off) | as `lt_off` |
| `t_off` | either direction with the 200-day average | as `lt_off` |
| `v1_htf` | higher-timeframe trend equals the trade direction (daily for intraday) | live rules |
| `v2_htf_vol` | `v1_htf` and pullback volume below impulse volume | live rules |
| `v3_htf_regime` | `v1_htf` and SPY on the trade's side of its 50-day average | live rules |
| `v4_cost` | planned cost at most 0.10R and price at least $10 | live rules |
| `x_flat` | live rules | live rules, plus flat at the session's last bar |

## Tests

Edge = trade R minus the mean R of its twins. Two kinds of twin: plain (same symbol, direction,
bracket and holding time, random timing) and **context-matched** (also drawn only where the
stream's own trend equals the trade direction and price is on the same side of its 200-bar
average). Intervals are 95%, resampling whole symbols and, separately, whole calendar months;
the wider one is used.

| ID | Role | Set | Claim | Passes when |
|---|---|---|---|---|
| **P1** | **primary** | 1d confirmation | `lt_off` has a positive return and its trigger adds to its context | mean R interval above 0 **and** context-matched edge interval above 0 |
| S1 | secondary | 1d confirmation | `lt_off_be1` keeps a positive mean R with a higher win rate than `lt_off` | mean R interval above 0; win rate higher |
| S2 | secondary | 1d confirmation | `lt_off_t2` positive mean R | mean R interval above 0 |
| S3 | secondary | 1d confirmation | the exit is the cause: `lt_off` beats `lt_base` | difference in mean R interval above 0 (months resampled) |
| S4 | secondary | 1d confirmation | `l_off` positive mean R | mean R interval above 0 |
| S5 | secondary | 1d confirmation | `lt_off_seqall` positive mean R with more trades than `lt_off` | mean R interval above 0 |
| S6 | secondary | 1d, 1h, 5m, 15m confirmation | baseline still has no edge | plain edge interval spans or is below 0 |
| S7 | secondary | 1d confirmation | shorts lose under `t_off` | reported (short trades' mean R) |
| S8 | secondary | platform snapshot, 1d (2023-12 to 2026-11, 159 symbols) | `lt_off` positive on the platform's own stored bars | reported; small sample expected |
| N1 | predicted null | 1h confirmation | `lt_off`, `v1_htf`–`v4_cost`: none has a positive mean R interval | reported |
| N2 | predicted null | 5m, 15m confirmation | `v1_htf`–`v4_cost`, `x_flat`: none has a positive mean R interval | reported |

Only P1 decides whether a "daily long-trend" candidate exists. Promotion gates from the
2026-11-08 plan (section 7) are then applied to it: edge interval above zero, mean R above zero,
at least 150 trades on at least 40 stocks, positive in most calendar years (the plan's "two of
three months", adapted to an eleven-year set), and drawdown no worse than the baseline's. Gate 6
(four weeks of live shadow running) cannot be met by a backtest and stays open.

## Limits stated in advance

- Survivors only; 2005–2015 and 2016–2026 are both mostly rising markets. The design year 2008
  lost 0.92R per trade (31 trades).
- Next-open fills with 2 bp half-spread plus 2 bp slippage and $0.005 per share, as in v1/v2.
  Stops fill at the stop or a worse open.
- One position per symbol and timeframe; no portfolio limit, so results are per trade, not an
  account return. Many positions are open together in a broad rally.
- Yahoo adjusted prices, not the platform's stored bars (S8 covers those).
- The 2026-11-08 plan said V1–V4 would be judged on sessions after 2026-11-08. N1/N2 are an
  additional test on other symbols and earlier dates, not a replacement for that forward read.
