# Little RZY evaluation rerun — corrected controls and stop/target-only research variant

Run 2026-10-20, code `07c8e54f` (clean), input snapshot exported 2026-10-20 14:04 UTC. Supersedes the headline in [the first results](../2026-10-19/little-rzy-evaluation-results.md), whose controls had two defects found by the [audit addendum](little-rzy-audit-addendum.md) (RZA-09 borrow after early exit, RZA-10 shortened control windows). Live defaults are unchanged; the config hash is still `8ceb8cf780c3eb981983`.

## What was done, in the requested order

1. **Preserved** the first run (report, trades, code hashes, manifest; input bars were not saved at the time, and the manifest says so): [`rzy-eval-runs/2026-10-20T0709Z-cfb40e0b/`](rzy-eval-runs/2026-10-20T0709Z-cfb40e0b/) and EC2 `/home/ec2-user/rzy-eval-runs/`.
2. **Fixed both control defects** with regressions that fail when reverted; reran the unchanged baseline from a saved, hashed input snapshot.
3. **Ran `research_stop_target_only`** (open positions exit only at stop or target; entry rules unchanged) as a separately named variant beside the original exit rule. It is an evaluation-only engine class switch, not a configuration option.
4. **Reported** chronological development vs held-out periods (last 30% of each timeframe's span), costs, gross R, holding time, drawdown and unresolved trades, by timeframe and direction.

Also added, because one random-walk check cannot show a future positive result is reliable: edge intervals bootstrapped **by symbol** (verdicts use these), a **minimum detectable edge**, and a planted-edge test proving the statistic detects a known 0.6R edge, finds none at 0, and widens under within-symbol dependence.

## Reproducibility

| Item | Location | SHA-256 |
|---|---|---|
| Input snapshot (26.5 MB, not in git) | EC2 `/home/ec2-user/rzy-eval-runs/2026-10-20T1414Z-snapshot/`, local `~/Documents/MyProjects/rzy-eval-data/2026-10-20T1414Z-snapshot/` | `6cef50d0…b012a` |
| Report, trades, manifest | [`rzy-eval-runs/2026-10-20-07c8e54f-all/`](rzy-eval-runs/2026-10-20-07c8e54f-all/) and EC2 | in `manifest.json` |

Reproduce: `python scripts/evaluate_little_rzy.py evaluate <snapshot_dir> <out_dir>` at `07c8e54f`. The evaluator refuses a snapshot whose hash does not match. A baseline-only run produced a byte-identical baseline section.

Coverage: 5m 149 symbols / 705,827 bars, 15m 148 / 230,921, 1h 143 / 59,501 (Jul 21 – Oct 20 2026); daily 152 / 103,287 (Dec 2023 – Oct 2026). Held-out starts: intraday 2026-09-22/23, daily 2025-12-14.

## 1. Corrected baseline: conclusion unchanged

| | First run (defective controls) | Corrected |
|---|---:|---:|
| Trades | 693 | 692 |
| Mean R | −0.092 | −0.093 |
| Control mean R | −0.091 | −0.094 |
| Edge vs control | −0.001 (by-trade CI −0.068, +0.067) | **+0.001 (by-symbol CI −0.066, +0.073)** |
| Minimum detectable edge | — | 0.098R |

The one-trade difference comes from the newer input snapshot (today's bars and provider restatements), not from the control fix, which changes only control values. Gross mean R is **+0.001**: before costs the baseline is break-even, and costs average **0.094R** per trade. Median holding is 4 bars; 620 of 692 exits are line closes, 46 stops, 26 targets. Unresolved at segment ends: 3.

| Baseline slice | Trades | Mean R | Cost R | Edge (by-symbol 95% CI) | Verdict |
|---|---:|---:|---:|---|---|
| 5m long | 220 | −0.128 | 0.117 | +0.031 (−0.075, +0.132) | no edge |
| 5m short | 219 | −0.133 | 0.118 | −0.007 (−0.122, +0.125) | no edge |
| 15m long | 88 | +0.023 | 0.070 | +0.056 (−0.164, +0.286) | no edge |
| 15m short | 87 | −0.085 | 0.069 | −0.051 (−0.289, +0.188) | no edge |
| 1d long / short | 30 / 20 | −0.038 / +0.156 | ~0.01 | | no edge / insufficient |
| 1h long / short | 13 / 15 | −0.363 / +0.070 | | | insufficient |

**Held out (last 30%)**: 5m −0.137R (edge −0.020, CI −0.168..+0.122); 15m −0.145R (edge −0.096, CI −0.287..+0.120); daily and hourly held-out samples are under 30. The 15m development period was +0.028R; it did not carry into the held-out period.

## 2. Stop/target-only research variant: worse, not better

| | Baseline (line exit) | `research_stop_target_only` |
|---|---:|---:|
| Trades | 692 | 670 |
| Target exits | 26 (3.8%) | **172 (25.7%)** |
| Stop exits | 46 | 498 |
| Win rate | 24.9% | 25.7% |
| Mean R (net) | −0.093 | **−0.200** |
| Gross mean R | +0.001 | −0.103 |
| Edge vs control | +0.001 (−0.066, +0.073) | −0.079 (−0.236, +0.076) |
| Max drawdown (cumulative R) | 74.8 | **174.9** |
| Median holding (bars) | 4 | 25 |
| Unresolved at segment end | 3 | 16 |

By slice: 15m long −0.344R and short −0.327R (both held-out and development negative); 5m long −0.231R, short −0.095R; daily slightly positive (+0.167 long, +0.087 short) but n < 30 and below its own controls for longs.

Removing the line exit lets targets be reached far more often, but the extra time in the trade loses more than it gains: the line exit was cutting losers early, not cutting winners. **This is evidence against the hypothesis that the line exit caused the poor baseline.** It remains a research result only; nothing about live defaults changes.

## 3. Other variants (corrected controls)

None produced a by-symbol edge interval above zero. Unrestricted sequence was the least negative (−0.076R, edge +0.022, CI −0.045..+0.091). The spec's line-only stop is worst: −0.275R, costs 0.267R per trade, drawdown 286.8R.

## Conclusion

On every timeframe, direction, period and variant tested, Little RZY entries show **no edge over random timing**, with a minimum detectable edge of about 0.1R overall (larger per slice). Costs alone take the baseline from break-even to −0.09R. The stop/target-only exit is materially worse. These results support caution about the entries; they do not identify a rule change that would fix them.

## Boundaries

- Execution and borrow inputs are research assumptions (fills possible; 0.5%/yr borrow); not verified live execution.
- Survivors-only universe; about three months of intraday history; daily samples are small.
- Seven variants and many slices were examined; isolated favourable cells are expected by chance and none survived the by-symbol interval.
- Matched controls use the real trade's observed holding length within the same segment: a retrospective diagnostic, not a tradable rule.
- The held-out split is descriptive; nothing was fitted on the development period.

## Still open from the audits

RZA-01 to RZA-08 (generation identity of events, derived-frame freshness, backtest pruning in `rzy_backtest.py`, replay gap/eligibility validation, short cancellation mail, stale-native fallback, chart cancellation history) are **not** addressed by this evaluation work. Exact pivot confirmation disclosure and paper-ledger provenance also remain.
