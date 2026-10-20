# Little RZY evaluation framework — 2026-10-19

How `LITTLE_RZY_INSPIRED_V1` is tested on real stored bars, and what a result does and does not mean. Code: `services/market-data/src/services/rzy_evaluation.py` (pure), `scripts/evaluate_little_rzy.py` (read-only production runner), tests in `services/market-data/tests/test_rzy_evaluation.py`.

## Questions it answers

1. After costs, do RZY trades make money, measured in R (net P&L per share ÷ initial risk)?
2. Is that better than **the same trade taken at a random time** on the same stock? This separates the pattern's timing from direction, volatility-scaled brackets and drift.
3. Does it hold **month by month**, by timeframe, by direction, and by setup number, rather than only in a pooled total?
4. What do the spec's five rule variants change (§11)?

## Rules of the test

| Concern | Rule |
|---|---|
| Same logic as live | Every bar is fed in time order to the production `Engine`; no separate backtest implementation. |
| Look-ahead | Pivots confirm 7 bars late; entries fill at the **next open**; nothing reads a later bar. Tests prove a control cannot see past its holding window. |
| Intrabar order | Stop before target when one bar touches both; gaps through a stop fill at the worse open. |
| Costs | $0.005/share commission each side, half of a 2 bp spread and 2 bp slippage each side; shorts pay borrow over elapsed time. |
| Missing data | Gaps are never bridged: each contiguous segment gets a fresh engine; segments under 60 bars are skipped. |
| Execution inputs | Stored prices have no verified provider delay or borrow data, so the evaluation **assumes** fills are possible and borrow is 0.5%/yr. These assumptions are written into every report. Long-only results are reported separately so they do not depend on the borrow assumption. |
| Universe | Every active, non-delisted US stock with stored bars; no hand-picked list. Delisted names are absent (survivorship bias, noted). |
| Tuning | None. Defaults only; every variant is reported; no best-variant selection. |

## Control (the comparison that matters)

For each real trade, 20 random bars are drawn from the same stream segment. Each control enters at the next open, in the same direction, with the stop and target at the **same ATR multiples** as the real trade, the same costs, and a time exit after the real trade's holding length. Edge = real R − mean control R, with a bootstrap 95% interval.

A positive mean R alone can be drift (e.g. longs in a rising market). Only an edge interval entirely above zero says the pattern's timing added something.

## Reporting

- Trades, win rate (Wilson 95%), mean and median R (bootstrap 95%), profit factor, average win/loss, mean % return, total R, max drawdown in cumulative R, average holding bars, exit mix (target / stop / line invalidation), cancellations by reason, unresolved trades at segment end.
- Slices: overall, long-only, timeframe, direction, timeframe × direction, **timeframe × month**, setup number (1, 2, 3, 4+, unknown).
- Verdicts: fewer than 30 trades → *insufficient sample*; edge interval spans 0 → *no measurable edge*; otherwise *better* or *worse than control*.

## Known limits

- 5m/15m/1h history is about three months (stored 5m bars begin 2026-07-20); daily covers about 3.5 years. Short windows are dominated by one or two market regimes; read by month.
- Trades within a stream are sequential, but streams overlap in time; bootstrap intervals treat trades as independent and are therefore optimistic.
- Six variants and four timeframes are many comparisons; an isolated "better than control" cell is weak evidence.
- Results describe the past under stated assumptions; they are not a forecast and not verified execution.

## Self-checks (tests)

R reconciles with the engine's net P&L; control arithmetic, stop-first, gap fill and time exit are exact; a control cannot see beyond its window; on pure random walks the framework reports **no** edge (false-positive check). Reversing stop/target order or removing the holding cap makes the tests fail.
