# Little RZY evaluation v2 — cross-stream context, unseen sessions and a results page (2026-11-08)

Extends the v1 framework (`docs/2026-10-19/little-rzy-evaluation-framework.md`) rather than
replacing it: same engine replay, same costs, same 20 random-timing twins per trade, same
by-symbol intervals. Questions and label definitions were fixed **before** the first v2 run in
`docs/2026-11-08/rzy-evaluation-v2-preregistration.md`.

## What v2 adds

| Addition | Where | Why |
|---|---|---|
| Unseen period | `period` label, H2 | Trade rules are unchanged since the v1 snapshot (2026-10-20 14:04:54 UTC); later signals are the only out-of-sample evidence. |
| Higher-timeframe alignment | `htf_daily`, `htf_next`, H3 (primary), H4 | "Can we combine streams?" Tested as slices of baseline trades. |
| Agreement across timeframes | `confluence`, H5 | All other timeframes agreeing vs not. |
| Market regime, time of day, volatility | `market_regime`, `time_of_day`, `volatility` | Descriptive slices. |
| Excursions | `mfe_r`, `mae_r`, `reached_1r_rate`, `target_exit_rate` | Separates entry problems (small favourable move) from exit problems (large favourable move given back). |
| Per-symbol table | `by_symbol` in report.json | Concentration check. |
| Difference intervals | `difference_ci` | Aligned − not aligned, resampling whole symbols. |
| Luck check | `scorecard` | Counts cells tested and compares "better/worse" counts with the ~2.5% per side expected by chance. |
| Stored runs + page | `little_rzy_evaluation_runs`, `GET /stocks/rzy/evaluation`, `LittleRzyEvaluation.tsx` | Results visible on the Little RZY page; run history kept. |

## Causality (the property every label must have)

A label uses only bars whose close is at or before the trade's signal-bar close. Each stream's
trend comes from the live engine replayed in time order, stored as change points. A state lapses
to `unknown` at the end of its segment plus a grace period (intraday 20 h, daily 5 days), or
where the next segment starts; a new segment starts cold. Tests prove the timeline equals a replay
truncated at the query time at many points, including between closes; two deliberate sabotages
(look-ahead by 30 h; no lapse at segment end) each fail a test.

## Running it

```bash
# 1. Export (read-only; on EC2, market closed preferably)
docker cp scripts/evaluate_little_rzy.py stockai-market-data-1:/tmp/
docker exec -e RZY_SOURCE_REVISION=$(git rev-parse HEAD) -w /app stockai-market-data-1 \
  nice -n 15 python /tmp/evaluate_little_rzy.py export /tmp/rzy-snapshot-<date>
# copy the snapshot dir to a workstation, then:
python scripts/evaluate_little_rzy.py evaluate <snapshot_dir> <out_dir>      # ~20-30 min
# 3. Publish the summary (inside market-data; idempotent; refuses a summary edited after its manifest)
docker exec -w /app stockai-market-data-1 python /tmp/evaluate_little_rzy.py publish /tmp/rzy-run
```

Outputs: `report.json` (everything), `summary.json` (what the page shows), `report.md`,
`trades.csv` (one row per trade with every label), `manifest.json` (code and input hashes).

## Limits

- Alignment is measured by slicing baseline trades. A real filter could occasionally let a later
  setup trade that the baseline blocked (one position per stream).
- Intraday history starts 2026-07-21; the unseen period is about 13 sessions when first run.
- Bootstrap by symbol still treats streams of different symbols as independent.
- Fillability and borrow remain research assumptions, as in v1. Live simulated fills stay blocked,
  so the forward journal cannot score trades; re-running v2 on later snapshots is the forward test.
