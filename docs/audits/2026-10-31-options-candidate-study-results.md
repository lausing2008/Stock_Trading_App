# Options candidate outcome study, Part A: results (October 31, 2026)

Research only. Nothing here changes a live default, ranking or recommendation. Design and rules: [the study design](../features/2026-10-29-options-candidate-outcome-study-design.md). Evidence: [the evidence folder](evidence/2026-10-31-options-candidate-study-run/).

## Bottom line

- **The research lead did not pick better contracts than the preset or a random shortlist candidate.** For calls bought short-term, it beat the preset consistently, including in the held-out quarter, by about 1 point of premium per trade. The lead's entry spread was about 2.4 points narrower, and that gain is no larger than the difference, so the evidence fits **cheaper execution, not better selection**. For the other three strategies, lead versus preset is within ±0.2 points of capital. Even the differences that are significant in development do not hold in the held-out quarter, and they track the lead's different strike rather than quote quality.
- **None of the three choices earned anything reliable in the held-out quarter (July–September 2026).** For every strategy, horizon and exit, the held-out interval for each arm includes zero, apart from one random-arm protective-put cell.
- **The strategies that hold the stock trailed the stock alone over the same entries** (development period). For example, a 28-day covered call made +1.3% against +5.8% for the stock. Buying at the ask, selling at the bid and capping the upside cost more than the premium earned in a mostly rising market. This is context from the same entries, not a test of whether to hold options at all.
- **Long-call averages are driven by a few large winners.** The development mean of +15% to +46% sits beside a negative median everywhere, and a held-out mean of −5% to −20%.

Nothing here supports promoting the research lead as a "best pick", or changing any default. Per the design's gates, this report is for review first.

## What was run

- **Run:** October 31, 2026, finished about 16:01 UTC (Saturday; market closed). Inside `stockai-market-data-1`, read-only transactions, against the deployed builder. The deployed `options_strategies.py`, `uw_option_chain.py` and `options_contract.py` hashes match the tested code; see `provenance.json`.
- **Universe:** 152 archived symbols. Entries on the first archived session of each ISO week from 2024-08-16 to 2026-10-19; **4,333 symbol-weeks**.
- **Trades:** **285,702** trade rows. Each covers 4 strategies × 4 horizons × 3 arms (lead, preset, random) × 2 exits (end of holding window, at expiry) where resolvable.
- **Periods, frozen in code before the run:** development to 2026-06-30, **held-out 2026-07-01 to 2026-09-30**, then post-freeze entries (October). The held-out figures in this report were read for the first time in this run.
- **Fills:** buy at the archived ask, sell at the archived bid, on entry and exit.
- **Return:** ROC is return on capital: premium for a long call, stock plus premium for a protective put, stock for a covered call, strike for a cash-secured put. Figures below are percentage points of that capital per trade.
- **GROWTH (14 days)** gives identical lead and preset results to SWING (14 days) and is omitted; only its random draws differ, because the seed includes the horizon.
- **Reproduction:** a secondary analysis from the raw trade rows (`secondary-analysis.py`) reproduces the summary's group counts and means exactly; checked on held-out long call, SHORT, holding exit, lead: n = 756, mean −4.94%.

## 1. Month by month (shown first, per the reporting rules)

**Lead minus preset, paired on the same symbol and entry, end-of-holding-window exit, mean difference in points of capital.** The last column counts months above and below zero. The final four columns (2026-07 to 2026-10) are held-out and post-freeze.

| Strategy, horizon | 24-08…26-06 range (development) | 26-07 | 26-08 | 26-09 | 26-10 | Months +/− |
|---|---|---:|---:|---:|---:|---|
| Long call, SHORT (7 d) | −1.2 to +2.2 | +0.4 | +1.1 | +1.2 | +0.9 | **22 / 5** |
| Long call, SWING (14 d) | −6.3 to +4.0 | +0.8 | +1.7 | +0.9 | +1.9 | 20 / 7 |
| Long call, LONG (28 d) | −9.3 to +6.5 | +0.1 | +3.5 | −0.0 | · | 17 / 9 |
| Cash-secured put, LONG | −0.5 to +0.6 | +0.2 | +0.0 | −0.1 | · | 21 / 5 |
| Cash-secured put, SHORT | −0.1 to +0.3 | +0.0 | +0.0 | −0.1 | −0.2 | 19 / 8 |
| Cash-secured put, SWING | −0.3 to +0.3 | −0.0 | −0.0 | −0.0 | −0.2 | 17 / 10 |
| Covered call, LONG | −0.6 to +0.4 | −0.1 | +0.0 | +0.1 | · | 13 / 13 |
| Covered call, SHORT | −0.3 to +0.2 | +0.0 | +0.0 | +0.0 | +0.0 | 15 / 12 |
| Covered call, SWING | −0.3 to +0.2 | −0.0 | +0.0 | −0.1 | +0.2 | 12 / 15 |
| Protective put, LONG | −1.1 to +0.5 | −0.1 | +0.0 | +0.1 | · | 6 / 20 |
| Protective put, SHORT | −0.2 to +0.1 | +0.0 | +0.0 | −0.1 | +0.1 | 11 / 16 |
| Protective put, SWING | −0.4 to +0.3 | +0.0 | +0.0 | −0.1 | +0.1 | 6 / 21 |

The full monthly table, with every arm and the stock's own return, is in `secondary.json` (`monthly_holding`, `monthly_lead_minus_preset_holding`).

**What the arms earned, by month, for short-term long calls.** The arms move almost exactly together, and with the stock. The arm chosen matters far less than the month: from −29% to +45% per trade.

| Month | Lead | Preset | Random | Stock | Month | Lead | Preset | Random | Stock |
|---|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 2024-08 | −19.2 | −18.7 | −18.3 | −0.7 | 2025-09 | +38.3 | +39.7 | +38.6 | +4.3 |
| 2024-09 | +12.6 | +13.2 | +11.3 | +2.2 | 2025-10 | +4.7 | +0.1 | +4.0 | +1.3 |
| 2024-10 | −6.3 | −7.4 | −8.2 | +0.4 | 2025-11 | −15.6 | −14.3 | −16.7 | −0.6 |
| 2024-11 | +15.7 | +20.0 | +15.1 | +2.8 | 2025-12 | −4.2 | −0.3 | −7.0 | +1.2 |
| 2024-12 | +2.1 | +0.8 | +4.6 | +1.1 | 2026-01 | +8.5 | +14.1 | +6.7 | +2.4 |
| 2025-01 | −0.5 | −3.6 | −1.7 | −0.2 | 2026-02 | −3.2 | −4.4 | −3.8 | +1.4 |
| 2025-02 | −20.0 | −20.5 | −22.8 | −2.6 | 2026-03 | −12.9 | −15.6 | −14.9 | −0.1 |
| 2025-03 | −28.8 | −30.7 | −28.3 | −4.0 | 2026-04 | +45.1 | +46.6 | +45.3 | +6.1 |
| 2025-04 | +25.1 | +21.0 | +24.5 | +4.6 | 2026-05 | +36.6 | +34.0 | +36.6 | +5.4 |
| 2025-05 | +27.0 | +23.5 | +25.7 | +4.4 | 2026-06 | −15.6 | −14.8 | −15.9 | −1.0 |
| 2025-06 | +17.9 | +16.8 | +17.5 | +2.7 | **2026-07** | −15.8 | −13.8 | −15.6 | −1.4 |
| 2025-07 | +0.6 | −0.8 | −1.6 | +0.8 | **2026-08** | −4.1 | −5.5 | −5.6 | +1.1 |
| 2025-08 | −1.8 | −3.8 | −4.1 | +0.4 | **2026-09** | −1.9 | +0.4 | −3.1 | +1.7 |
| | | | | | 2026-10 | −29.7 | −30.7 | −30.3 | −4.1 |

These per-month arm means are not paired: the preset is skipped when its spread exceeds 20%, so its sample differs. Use the paired table above for lead versus preset.

## 2. Paired comparisons, with intervals clustered by entry date

End of holding window, all entries including earnings windows. Mean paired difference in points of capital, with a 95% interval from resampling entry dates (2,000 resamples). **Bold** marks intervals that exclude zero.

| Strategy, horizon | Lead − preset: development (99 dates) | Lead − preset: held-out (15–17 dates) | Lead − random: development | Lead − random: held-out |
|---|---|---|---|---|
| Long call, SHORT | **+0.59 [+0.13, +1.06]** | **+1.00 [+0.29, +1.74]** | **+0.83 [+0.16, +1.44]** | **+0.95 [+0.19, +1.91]** |
| Long call, SWING | +0.32 [−0.54, +1.19] | **+1.05 [+0.26, +1.93]** | −0.17 [−1.09, +0.70] | +0.54 [−0.39, +1.38] |
| Long call, LONG | −0.13 [−2.02, +1.50] | +1.05 [−0.18, +2.15] | −0.87 [−2.82, +0.84] | −0.01 [−1.28, +1.19] |
| Cash-secured put, SHORT | **+0.06 [+0.02, +0.09]** | −0.05 [−0.17, +0.06] | **+0.06 [+0.03, +0.10]** | +0.03 [−0.08, +0.12] |
| Cash-secured put, SWING | **+0.07 [+0.02, +0.12]** | −0.02 [−0.18, +0.07] | **+0.06 [+0.01, +0.11]** | +0.05 [−0.07, +0.14] |
| Cash-secured put, LONG | **+0.13 [+0.06, +0.20]** | +0.01 [−0.08, +0.10] | **+0.15 [+0.08, +0.21]** | +0.05 [−0.06, +0.16] |
| Covered call, SHORT | −0.03 [−0.08, +0.01] | +0.03 [−0.03, +0.10] | +0.01 [−0.03, +0.05] | +0.01 [−0.05, +0.07] |
| Covered call, SWING | **−0.06 [−0.11, −0.00]** | −0.04 [−0.11, +0.03] | −0.05 [−0.11, +0.01] | −0.04 [−0.12, +0.04] |
| Covered call, LONG | −0.05 [−0.11, +0.02] | +0.03 [−0.10, +0.14] | −0.02 [−0.10, +0.06] | +0.01 [−0.10, +0.13] |
| Protective put, SHORT | −0.04 [−0.08, +0.00] | −0.04 [−0.10, +0.03] | −0.03 [−0.08, +0.01] | +0.02 [−0.05, +0.09] |
| Protective put, SWING | **−0.09 [−0.15, −0.03]** | −0.03 [−0.08, +0.04] | −0.03 [−0.09, +0.03] | +0.01 [−0.05, +0.08] |
| Protective put, LONG | **−0.16 [−0.25, −0.07]** | +0.02 [−0.10, +0.12] | −0.04 [−0.12, +0.06] | −0.05 [−0.22, +0.07] |

The expiry exit and the excluding-earnings variants are in `summary.json` (`paired`). They do not change the reading. Post-freeze (October) rows have only 4–7 entry dates, too few for a date-clustered interval to mean much, so they are not interpreted here.

**Only short-term long calls improved in the held-out quarter, against both the preset and random.** The lead and the preset are the same contract in about 25% of entries; the paired means include those zero differences. Over entries where they differ (`spread-decomposition.json`):

| Strategy (SHORT, holding exit) | Pairs | Lead − preset ROC | Entry spread, lead − preset | Strike gap (% of spot) | Different expiry |
|---|---:|---:|---:|---:|---:|
| Long call, development | 1,677 | +0.78 (median +1.00) | **−2.47 pts** | −0.98% (deeper in the money) | 0% |
| Long call, held-out | 429 | +1.36 (median +0.95) | **−2.38 pts** | −0.37% | 0% |
| Cash-secured put, development | 1,483 | +0.08 | −1.96 pts | +1.61% (higher strike) | 0% |
| Protective put, development | 1,450 | −0.05 | −1.90 pts | +1.33% (higher strike) | 0% |
| Covered call, development | 1,672 | −0.04 | −2.46 pts | −0.97% (lower strike) | 0% |

**Reading.** The lead is the narrowest quoted spread, so a round trip at ask-in and bid-out is cheaper by roughly its spread difference: about 2.4 points of premium for long calls. The long call's gain over the preset (about 1 point) is not larger than that saved cost. It is also partly offset by the lead choosing a slightly deeper in-the-money strike. For the other strategies, that saving is measured against stock or strike capital, where it is a few hundredths of a point. What remains tracks exposure: a higher put strike buys more protection, or takes more risk for a cash-secured put. That paid in development and gave nothing in the held-out quarter. This is a rough split, not a model, and it does not establish that spread savings are the whole effect. It does show the data contain no evidence of selection skill beyond them.

## 3. Arms against the stock alone

Same entries, end of holding window, development period, mean ROC per trade:

| Strategy, horizon | Lead | Preset | Random | Stock alone | Median lead | Win rate lead |
|---|---:|---:|---:|---:|---:|---:|
| Covered call, LONG | +1.27 | +1.31 | +1.29 | +5.76 | +2.06 | 75.5% |
| Protective put, LONG | +3.09 | +3.46 | +3.10 | +5.54 | −0.28 | 48.1% |
| Cash-secured put, LONG | +1.26 | +1.27 | +1.11 | +5.53 | +2.10 | 72.4% |
| Long call, LONG | +43.34 | +41.72 | +44.21 | +5.76 | −12.50 | 46.9% |
| Long call, SHORT | +5.20 | +4.72 | +4.34 | +1.47 | −4.55 | 46.6% |

Held-out quarter, same layout: covered call LONG +0.08 / +0.71 / +0.07 against stock +0.32; long call SHORT −4.94 / −4.07 / −5.94 against +0.97. Every row is in `secondary.json` (`arms`), with intervals, medians, win rates and worst trades.

A cash-secured put does not own the stock, so its stock column is context, not a like-for-like alternative. The covered call's high win rate with a lower mean than the stock is exactly the case the reporting rules call worse, not better.

## 4. Exclusions and unresolved trades (counters)

| Counter | Count | Meaning |
|---|---:|---|
| Entry sessions | 4,333 | symbol-weeks attempted |
| No qualifying lead (and no random) | 7,302 | arm-entries where no shortlisted contract passed the lead rules |
| Preset spread above 20% | 14,448 | preset not entered |
| Preset without a two-sided quote | 600 | preset not entered |
| Preset with non-positive maximum profit | 12 | preset not entered |
| Entry price basis incompatible / unverifiable | 329 / 22 | stored close disagreed with the chain's put–call parity, or could not be checked; entry skipped |
| Exit stock price basis incompatible | 288 | exit unresolved |
| Expiry price basis incompatible | 436 | expiry exit unresolved |
| Holding exit with no quote | 957 | exit unresolved, not guessed |
| Holding window reaching expiry | 453 | marked at expiry instead |
| Still open at archive end (holding / expiry) | 10,752 / 24,356 | mostly recent entries; not counted as outcomes |

## What this does not show

These are the design's stated limits, all unchanged:
- **Real execution:** real fills, size or price impact, and whether an archived end-of-day quote was executable.
- **Early assignment,** including calls before an ex-dividend date.
- **Intraday paths:** only end-of-day prices are used.
- **Survivorship:** symbols outside the 152 captured.
- **Inputs:** stop/target-based anchors; every trade used the builder's at-the-money fallback.
- **Sample:** 152 symbols in a period that was mostly rising until mid-2026, with one held-out quarter (13 weeks).

**Trade file.** `trades.jsonl` (180 MB, SHA-256 in `trades.jsonl.sha256`) is not committed. A copy is in the session scratchpad. The container copy at `/tmp/ocs_run` disappears on the next container recreation.

## Suggested next steps (not done)

1. **Do not promote the lead as "best".** If the desk keeps it, describe it as the cheapest quote to trade, which is what this evidence supports.
2. Optionally add a **spread-cost column** to the desk's candidate table: round-trip cost at the quoted spread. It states the one effect found here directly, with no ranking claim.
3. **Part B** (deployed with the next market-data release) gives the forward record. Re-run this comparison on it after several months. Any ranking change still needs its own held-out and forward evidence, per R14.
