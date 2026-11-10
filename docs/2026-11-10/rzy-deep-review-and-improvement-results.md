# Little RZY — deep review and improvement study (2026-11-10)

Written for the platform owner deciding what to do with Little RZY, and for whoever builds the
next step. It follows `docs/2026-11-08/rzy-review-and-improvement-plan.md`, which proposed
filters and exit rules but could only test them on new sessions, about three months away. This
study tests them now, on data no RZY evaluation had seen.

Tests were fixed in advance in `rzy-improvement-preregistration.md` (commit 074bb731, before
any confirmation trade of a variant existed). Evidence: `rzy-improvement-evidence/`.

**Terms.** *R* is a trade's result divided by its initial risk (entry to stop), after costs.
*Twins* are random-timing trades in the same stock with the same direction, stop, target and
holding time; a trade's *edge* is its R minus its twins' mean R. *Context twins* are drawn only
from bars where the stock was in the same kind of trend. *Design* data was used to look for
ideas; *confirmation* data was untouched until the ideas were fixed.

---

## 1. Bottom line

1. **The live rules have no timing edge on any timeframe.** On 9,000 fresh trades (996 stocks,
   daily bars back to 2005) a Little RZY entry does no better than a random entry in the same
   stock, and every timeframe loses after costs: daily −0.085R per trade, 1h −0.075R,
   15m −0.151R, 5m −0.174R (confirmation data). On daily bars since 2016 it is measurably
   *worse* than random.
2. **No filter or exit rule fixes 5m, 15m or 1h.** Fourteen exit rules and the four filters
   from the 2026-11-08 plan were tried. None produced a positive average. The plan's best lead,
   trading intraday setups only with the daily trend, made 1h results worse on fresh data.
3. **One change improves daily results: long only, with the 200-day trend, and no exit on a
   close through the line.** Against the live daily rules it raised the win rate from 25.8% to
   33.2% and the average from −0.085R to +0.194R per trade over 2016–2026 (+1.08% per trade
   instead of −0.60%). Removing the line exit alone is worth +0.23R per trade.
4. **That improvement is not a tradeable edge, and it did not pass.** Its return is what any
   long entry in a rising stock earned with the same stop and target: context twins made
   +0.114R, so the trigger added +0.08R, which is not distinguishable from zero. All of the gain
   came from 2016–2020. Since 2021 it has averaged −0.075R, no better than the live rules.
5. **Win rate can be raised on demand, but only by giving up return.** A breakeven stop lifts
   the share of non-losing daily trades to 47.8% and cuts the average to +0.056R. Exit rules
   reshape the outcomes; they do not create an edge.

**Recommendation.** Keep Little RZY research-only. Stop presenting 5m and 15m triggers as
entries. Do not promote any variant. The decisions that are the owner's are in section 7.

---

## 2. What was done

| Step | Detail |
|---|---|
| Code review | Engine (`little_rzy.py`), evaluation (`rzy_evaluation.py`, `rzy_evaluation_context.py`), data preparation (`rzy_data.py`), page and email guidance, read against the 2026-11-08 review. |
| Research engine | `scripts/rzy_research_engine.py`: the shipped engine plus entry gates and exit rules, replayed bar by bar. With no options it reproduces the published 2026-11-08 run exactly: 744 trades, identical R and exit reasons. Lives outside the service image; no `Config` field, no live change. |
| Independent data | Yahoo Finance, fetched 2026-11-10 from a workstation: the platform's 159 symbols plus today's S&P 500 and S&P 400 (996 symbols). Daily from 2005 (4.5M bars), hourly from 2023-12 (4.9M), 5-minute for 60 days (4.0M), 15-minute derived (1.3M). |
| Split | Fixed before any result (commit 02591c30). Daily: design 2005–2015, confirmation 2016–2026. Hourly: design to 2025-06, confirmation after. 5m/15m: half the symbols each. Platform symbols inside the earlier snapshot's period are excluded as already seen. |
| Design exploration | Baseline description, 26 trade features, 14 exit rules, 22 filter combinations, a 15-variant daily family. Design trades only. |
| Preregistration | One primary test, eight secondary, two predicted nulls, with pass criteria (commit 074bb731). |
| Confirmation | One run per timeframe on the untouched half; `confirm_report.py` applies the criteria. |
| Tests | 10 tests for the research engine; 8 deliberate breaks each fail a test (`sabotage-results.json`). |
| Reproducibility | The four confirmation runs were repeated after the engine was moved; every trades file is byte-identical. |
| Independent review | A separate reviewer read the research code for look-ahead, accounting and statistics errors. Nothing overturned a result. It showed the breakeven variant's win rate is mostly scratches and that the positive-return interval is marginal; both are stated in section 4. |

Baseline trades on the independent data: 4,551 design, 4,449 confirmation, 673 excluded as seen.

---

## 3. Review findings

| ID | Finding | Evidence | Status |
|---|---|---|---|
| R1 | The engine matches its build spec. No rule-logic defect found on a second full read. | Independent replay reproduces all 744 published trades to the last digit. | Agrees with the 2026-11-08 review |
| R2 | **The market calendar is missing the 2025-01-09 closure** (national day of mourning). `is_us_trading_day` calls it a session, so every daily bar series shows a gap there. | 147 of 159 daily streams in the 2026-11-08 snapshot end at 2025-01-08 and restart on 2025-01-10; `shared/common/market_calendar.py` lists no 2025-01-09. | **New. Not fixed** (shared code; see D5) |
| R3 | Consequence of R2: every live daily RZY stream is built only from bars since 2025-01-10, and the evaluation's daily data is cut into two cold-started pieces. | Same counts. About 460 daily bars remain, enough for the indicators, so today's alerts are barely affected; the evaluation loses the warm-up bars after the cut. | New |
| R4 | The standing evaluation's twins match the stock but not the situation. A filter such as "longs in an uptrend" then looks like skill when it is only the uptrend. | Daily long-trend variant, confirmation: +0.194R per trade, but context twins made +0.114R. | New. Context twins added to the research runner |
| R5 | One corrupt price can swamp a twin average. | One QUBT short (2022-12-01) had twins averaging −17,000,000R across a bad early Yahoo price. The 2026-11-08 run has no such case (largest twin mean 4.75R). | New. Guarded in `confirm_report.py` (3 twin means discarded, no verdict affected) |
| R6 | The page's "Reading the timeframes" advice (take lower-timeframe setups in the higher timeframe's direction) says it is untested. It is now tested and not supported. | 1h confirmation, trades aligned with the daily trend: −0.119R, edge −0.093R, interval below zero (519 trades). | New. Page text unchanged (see D4) |
| R7 | On daily long setups, exiting on the first close through the line costs about 0.23R per trade. On 1h, 15m and 5m the same exit is the better choice. | Daily confirmation: same entries, +0.194R without the line exit, −0.033R with it; difference +0.227R, interval +0.080 to +0.373. 1h design: −0.045R with it, −0.148R without. | New |
| R8 | The 2026-11-08 lead H3 (daily-trend alignment) does not hold on independent data. | Design 1h: aligned +0.004R edge (579 trades). Confirmation: see R6. 5m aligned: −0.239R (293 trades). | Refutes the lead |

---

## 4. Results

### 4a. The live rules on fresh data (confirmation set)

| Timeframe | Trades | Win rate | Mean R | 95% interval | Edge vs twins | 95% interval | Cost per trade |
|---|---|---|---|---|---|---|---|
| 1d (2016–2026) | 1,652 | 25.8% | −0.085 | −0.140 to −0.026 | −0.080 | −0.135 to −0.021 | 0.024R |
| 1h (2025-07 on) | 1,520 | 26.2% | −0.075 | −0.130 to −0.018 | −0.035 | −0.096 to +0.028 | 0.039R |
| 15m (60 days) | 369 | 21.4% | −0.151 | −0.244 to −0.008 | −0.070 | −0.203 to +0.092 | 0.081R |
| 5m (60 days) | 908 | 19.1% | −0.174 | −0.271 to −0.118 | −0.030 | −0.134 to +0.025 | 0.138R |

The design half says the same (1d −0.014R edge on 1,372 trades, 1h +0.005R on 1,813, 5m −0.023R
on 1,023, 15m −0.083R on 343). The sample is twelve times the 2026-11-08 run's, and the intervals
are now tight: an edge of +0.10R would have been seen.

### 4b. What does not help

**Exit rules** (design set, entries unchanged; mean R per trade):

| Exit rule | 1d | 1h | 15m | 5m | Win rate, 1d |
|---|---|---|---|---|---|
| Live (exit after a close through the line) | −0.026 | −0.045 | −0.169 | −0.172 | 27.1% |
| Line exit with a 0.25 / 0.50 ATR buffer | −0.041 / −0.029 | −0.054 / −0.064 | — | — | 27.0% / 27.6% |
| Two consecutive closes through the line | −0.029 | −0.075 | — | — | 29.6% |
| Half off at +1R, stop to breakeven | −0.023 | −0.056 | −0.161 | −0.172 | 33.7% |
| Breakeven stop after +1R | −0.016 | −0.038 | — | — | 31.9% |
| Time stop (10 bars without +0.5R) | −0.024 | −0.042 | −0.167 | −0.171 | 27.3% |
| Target capped at 2R / 1R | −0.028 / −0.024 | −0.074 / −0.067 | — / −0.149 | — / −0.154 | 28.3% / 33.8% |
| Stop or target only (no line exit) | +0.025 | −0.148 | −0.266 | −0.217 | 27.3% |
| Stop or 1R target only | −0.024 | −0.147 | — | — | 50.7% |
| Flat at the session close | n/a | n/a | −0.084 | −0.155 | — |

Win rate moves from 27% to 51% across these rules while the average barely moves. That is what
exits do to entries that carry no information.

**Filters** (confirmation set, live exits; the 2026-11-08 plan's V1–V4):

| Filter | 1h | 15m | 5m |
|---|---|---|---|
| None (live) | −0.075 | −0.151 | −0.174 |
| V1: with the daily trend | **−0.119** (edge below zero) | −0.097 | −0.239 |
| V2: V1 and pullback volume below impulse volume | −0.090 | −0.083 | −0.274 |
| V3: V1 and SPY on the trade's side of its 50-day average | **−0.179** (edge below zero) | +0.015 (58 trades) | −0.202 |
| V4: cost at most 0.10R, price at least $10 | −0.082 | −0.060 | −0.066 |
| Flat at the session close | n/a | −0.054 | −0.169 |

Both predicted nulls held: no intraday variant has a mean R interval above zero. The cost floor
halves the 5m and 15m loss by removing the most expensive trades; it does not make them profitable.

### 4c. The one change that improves daily results

Design-set slices showed three things on daily bars: longs do better than shorts, trades against
the 200-day average do badly (−0.176R), and the hair-trigger line exit throws away what the
with-trend longs earn. Put together as one variant (`lt_off`):

- **Entry:** live rules, long only, and the daily close above its 200-day average at the trigger.
- **Exit:** stop or measured-move target only. No exit on a close through the line.

| Daily rule set, confirmation 2016–2026 | Trades | Win rate | Mean R | 95% interval | Return per trade | Profit factor | Total R | Worst drawdown |
|---|---|---|---|---|---|---|---|---|
| Live rules | 1,652 | 25.8% | −0.085 | −0.140 to −0.026 | −0.60% | 0.75 | −140.7 | 143.7R |
| Long with trend, live exit (`lt_base`) | 817 | 28.3% | −0.033 | −0.119 to +0.057 | — | 0.90 | −26.6 | 44.0R |
| **Long with trend, stop or target (`lt_off`)** | 802 | **33.2%** | **+0.194** | +0.014 to +0.368 | **+1.08%** | 1.27 | +155.8 | 69.6R |
| Same, target capped at 2R (`lt_off_t2`) | 807 | 39.5% | +0.111 | −0.037 to +0.257 | +0.77% | 1.17 | +89.9 | 72.4R |
| Same, breakeven stop after +1R (`lt_off_be1`) | 809 | 47.8%* | +0.056 | −0.079 to +0.205 | +0.49% | 1.11 | +44.9 | 64.5R |
| Long only, no 200-day rule (`l_off`) | 942 | 32.8% | +0.233 | +0.041 to +0.431 | — | 1.33 | +219.9 | 95.7R |

\* 22.5% real winners plus about 25% scratched at breakeven; losers fall from 67% to 51%.

`lt_off` beats the live daily rules by +0.279R per trade (interval +0.127 to +0.445). Winners
average +2.74R (33%), losers −1.07R (67%); the median hold is 20 sessions; about 74 trades a year
across the universe, with a median of 9 open together and at most 25.

### 4d. Why it did not pass

| Check on `lt_off` | Design 2005–2015 | Confirmation 2016–2026 | 2016–2020 | 2021–2026 |
|---|---|---|---|---|
| Trades | 637 | 802 | 362 | 440 |
| Win rate | 37.0% | 33.2% | 41.7% | 26.1% |
| Mean R | +0.371 | +0.194 | +0.522 | **−0.075** |
| Edge vs context twins | +0.275 | +0.080 | +0.412 | **−0.193** |
| 95% interval of that edge | +0.082 to +0.473 | −0.093 to +0.250 | +0.141 to +0.681 | −0.381 to +0.004 |
| Advantage over live rules (R per trade) | +0.397 | +0.279 | +0.574 | +0.036 |

- **Primary test P1 failed.** It needed a positive return *and* a trigger that beats its context.
  The return interval is above zero; the context-edge interval is not.
- **The gain is the trend, not the pattern.** Random long entries in the same uptrends, with the
  same stop and target, made +0.114R. The universe holds only stocks that exist today, which
  flatters every long result here; the comparison with twins is the part that survives that.
- **It has faded.** By year: 2016 +0.56, 2017 +0.86, 2018 −0.24, 2019 +0.35, 2020 +0.74,
  2021 +0.02, 2022 −0.48, 2023 +0.17, 2024 +0.23, 2025 −0.31, 2026 −0.49. In 2025 and 2026 its
  trades were significantly worse than their twins. This matches the 2026-11-08 finding that
  the newest sessions were worse than random.
- **A market filter does not rescue it** (post-hoc, not a predeclared test): with SPY above its
  200-day average the confirmation edge is +0.115R, interval −0.062 to +0.297.

Promotion gates (2026-11-08 plan, section 7) for `lt_off`:

| Gate | Result |
|---|---|
| 1. Edge interval above zero on unseen data | **Fail** (−0.093 to +0.250) |
| 2. Mean R above zero | Marginal pass (+0.014 to +0.368 resampling months; −0.069 to +0.450 resampling years) |
| 3. At least 150 trades on 40 stocks | Pass (802 on 530) |
| 4. Positive in most periods | Pass (7 of 11 years) |
| 5. Drawdown no worse than baseline | Pass (69.6R vs 143.7R) |
| 6. Four weeks of live shadow running | Not started |

### 4e. All predeclared tests

| ID | Claim | Result | Verdict |
|---|---|---|---|
| **P1** | `lt_off`: positive return and a trigger that adds to its context | mean +0.194R (+0.014 to +0.368); context edge +0.080R (−0.093 to +0.250) | **Fail** |
| S1 | `lt_off_be1`: positive mean with a higher win rate | +0.056R (−0.079 to +0.205); 47.8% vs 33.2% | Fail. The higher win rate is scratches: real winners are 22.5% |
| S2 | `lt_off_t2`: positive mean | +0.111R (−0.037 to +0.257) | Fail |
| S3 | The exit is the cause: `lt_off` beats `lt_base` | +0.227R (+0.080 to +0.373) | Pass |
| S4 | `l_off`: positive mean | +0.233R (+0.041 to +0.431) | Pass (edge vs twins +0.100R, not above zero) |
| S5 | `lt_off_seqall`: positive mean with more trades | +0.183R (+0.015 to +0.355); 937 vs 802 | Pass (same caveat) |
| S6 | Baseline still has no edge, all timeframes | section 4a | Confirmed; daily is below zero |
| S7 | Shorts lose when held to stop or target | −0.489R (−0.635 to −0.330), 462 trades | Confirmed |
| S8 | `lt_off` on the platform's own stored daily bars | +0.435R, 22 trades (−0.399 to +1.401) | Too few trades |
| N1 | 1h: no variant positive | best `lt_off` −0.020R | Null held |
| N2 | 5m, 15m: no variant positive | best 15m V3 +0.015R on 58 trades (−0.312 to +0.360) | Null held |

---

## 5. Accuracy, win rate and returns: what each timeframe can and cannot give

| Timeframe | Live rules | Best tested alternative | Honest reading |
|---|---|---|---|
| 5m | 19% wins, −0.17R | Cost floor: 25% wins, −0.07R | Loses under every rule. Costs alone are 0.14R a trade. |
| 15m | 21% wins, −0.15R | Flat at close: 28% wins, −0.05R | Loses under every rule tested. |
| 1h | 26% wins, −0.08R | None better | No filter or exit helps; top-down alignment hurts. |
| 1d | 26% wins, −0.09R | Long with trend, stop or target: 33% wins, +0.19R (2016–2026) | Better than the live rules, but the gain is the trend's, it predates 2021, and it failed the edge test. |

"Accuracy" in the sense of an entry that predicts direction better than chance was not found
anywhere. What can be changed is which trades are taken (drop shorts and counter-trend daily
setups) and how they are held (give daily longs room). Those raise the daily numbers relative to
the live rules; they do not make Little RZY a signal.

---

## 6. What was built

| Item | Where | Notes |
|---|---|---|
| Research engine | `scripts/rzy_research_engine.py` | Gates: long only, higher-timeframe alignment, market regime, volume dry-up, cost, price, 50/200-bar and 200-day sides, momentum, relative strength, stop size, slope, time of day. Exits: buffered line, two closes, off, partial, breakeven, time stop, target cap, flat at close. Context-matched twins. |
| Runner | `scripts/rzy_research.py` | `prepare` (per-symbol files, 15m and weekly derived) and `run` (parallel, cached indicators, design-only or confirmation-only output). All bars of the 996-symbol set replay in about 6 minutes per timeframe. |
| Tests | `services/market-data/tests/test_rzy_research.py` | Default equals the shipped engine; line-exit-off equals the existing stop/target variant; gates enforced; unknown context fails a gate; no look-ahead through context; each exit rule; context twins. |
| Evidence | `docs/2026-11-10/rzy-improvement-evidence/` | Fetch and split scripts, design outputs, confirmation trades and results, sabotage results, data hashes. Raw bars (431 MB) are on the workstation; hashes in `local-data-manifest.json`. |

Nothing in the live service changed: no deploy, no `Config` field, no alert or page change.

---

## 7. Decisions for the owner

| ID | Decision | Recommendation | Evidence |
|---|---|---|---|
| D1 | 5m and 15m trigger emails | Switch them off, or relabel them "timing only, not an entry". | Lose under every rule on 2,643 independent trades; section 4b. |
| D2 | 1h trigger emails | Keep as context, labelled "no measured edge", or switch off. | 3,333 independent trades; no variant positive. |
| D3 | A daily long-trend candidate | Do **not** promote. If wanted, run `lt_off` as a separate shadow strategy (own version and config hash, no emails) and read it after six months. It needs a build in the live service. | Fails gate 1; negative since 2021. |
| D4 | Page and guide text | Replace "combining timeframes has not been tested" with the result, and stop recommending daily-trend alignment as a filter. Frontend-only change. | R6, R8. |
| D5 | Calendar: add 2025-01-09 | Fix in `shared/common/market_calendar.py`. It is shared code, so all 12 services rebuild, and every daily RZY stream rebuilds once with its longer history. Schedule it deliberately. | R2, R3. |
| D6 | Standing evaluation | Add context twins and a twin outlier guard to `rzy_evaluation.py` before the next published run, as a new analysis version. | R4, R5. |
| D7 | The 2026-11-08 forward plan | Drop V1–V3 and the exit variants M1–M3; they have now been tested on more data than three months of new sessions would give. Keep the scheduled re-run to watch the baseline and, if D3 is taken, the shadow candidate. | Sections 4b, 4e. |

None of these was done in this study. The owner approved the recommendations the same day;
section 10 records what was then done.

---

## 8. Limits

- **Survivors only.** The universe is today's index members and platform symbols. Absolute long
  returns are overstated; twin comparisons are not affected in the same way.
- **Yahoo data, not the platform's.** Adjusted daily and hourly bars; hourly half-days lack
  their last half hour. The platform's own daily bars gave only 22 `lt_off` trades (S8).
- **5m and 15m cover 60 days**, the same days in both halves of the split, in a period the
  earlier evaluation had already found poor.
- **Per-trade results, not an account.** One position per stock and timeframe, no portfolio
  limit, no correlation between positions considered.
- **Execution is assumed.** Next-open fills, 2 bp half-spread plus 2 bp slippage, $0.005 a
  share, shorts borrowable at 0.5% a year, stops filled at the stop or a worse open.
- **The design step looked at many cuts.** Only the predeclared confirmation tests count as
  evidence; the design tables in section 4b are descriptive.
- **Open positions are not scored.** Trades still open when the data ends are dropped: 10 for
  `lt_off` on daily bars, 28 on 1h, 2 to 17 for the others (`runs/*/run.json` after the rerun).
- **Twins are drawn from a stock's whole history**, not only the trade's own period. A sample
  check with period-matched twins moved the `lt_off` edge by 0.02–0.04R, not enough to change P1.
- **Known gaps in the research exits**, none of which occurs in or changes the quoted runs: a
  target cap is not applied if the entry bar itself reaches the uncapped target (0 cases); a
  breakeven stop takes effect from the bar after +1R is reached; a partial cannot be booked on
  the entry bar. The indicator cache is keyed by symbol and config only, so a data directory must
  not be refilled with different bars without deleting its `cache/`.
- **Deviation from the preregistration:** twin means beyond ±100R are discarded (R5). Three
  values, all on one QUBT short, none in a long-only variant.

---

## 9. Reproduce

```bash
E=docs/2026-11-10/rzy-improvement-evidence
python $E/fetch_universe.py universe.json platform_symbols.json     # index members
python $E/fetch_bars.py <bars_dir> universe.json 1d,1h,5m           # Yahoo bars (will differ from the hashed set as Yahoo re-adjusts)
python scripts/rzy_research.py prepare <bars_dir> <data_dir> --market <SPY 1d.jsonl.gz>
python scripts/rzy_research.py run <data_dir> <out> --variants baseline,lt_base,lt_off,lt_off_be1,lt_off_t2,l_off,lt_off_seqall,t_off \
    --tfs 1d --split $E/split.py --keep confirm
python $E/confirm_report.py confirm_1d=<out>/trades.csv ... $E/confirm-results.json
```

The confirmation trades themselves are in `rzy-improvement-evidence/runs/` (gzip), so
`confirm_report.py` can be re-run without refetching. The confirmation runs record the research
engine at its first path (`services/market-data/src/services/rzy_research.py`); it was moved to
`scripts/rzy_research_engine.py` afterwards with one import line changed, and the 744-trade
identity check was repeated after the move.

---

## 10. Actions taken after approval (2026-11-10)

| Decision | Done | Detail |
|---|---|---|
| D1: 5m and 15m emails | Yes, live | `EMAIL_TIMEFRAMES = ('1h', '1d')` in `rzy_workflow.py`. 5m and 15m streams are still scanned, recorded and shown; their triggers are counted (`not_emailed_timeframe` in the scan result) and never queued. The morning digest is unchanged. |
| D2: 1h label | Yes, live | 1h and daily pattern emails carry a "No measured edge on this timeframe" line with the study's figures (`rzy_email.EVIDENCE_NOTES`). |
| D3: daily long-trend candidate | Not promoted | No shadow strategy was built. |
| D4: page and guide text | Yes, live | The page, the beginner guide and the settings panel say that combining timeframes was tested and did not help, and that only 1h and daily are emailed. |
| D5: calendar | Prepared, not merged | Branch `fix/calendar-2025-01-09` (bbf66d52) adds the date with two tests. It is shared code, so it waits for a window in which all 12 backend services can be rebuilt together. |
| D6, D7 | Not done | Evaluation changes and the forward plan are unchanged. |

**Deployment (commit 2e93e854, 06:20–06:30 UTC).**

- **Frontend:** `scripts/deploy_frontend.sh`, healthy, no `localhost` URLs in the build. It also
  carried one inert label line from another session's commit 3369e1a5 (`market_breadth`).
- **market-data:** a file-level hotfix, not an image rebuild. `prod` also holds two changes from
  another session that are deliberately not deployed (the RZY open-hold fix 58d77de4, which is
  part of an A/B read, and price-risk changes 3369e1a5). A rebuild from `prod` would have shipped
  them. Instead only this change's hunks were applied to the three files the container was
  running (`rzy_workflow.py`, `rzy_email.py`, `api/little_rzy.py`) and the container restarted:
  healthy, `EMAIL_TIMEFRAMES` reads `('1h', '1d')` in the running code, 15 containers healthy.
- **Caveats.** A container recreation from the current image reverts the hotfix; the next
  market-data build from `prod` includes it permanently. The drift check will report these three
  files, and the two price-risk files, as differing from `prod` until then. Pre-change copies
  and logs: `~/container-logs/prehotfix-2e93e854-*` and `market-data-before-2e93e854-hotfix.log`
  on the server.
- **Side effect on other work:** the 5m alert-latency study (tracker 457/458) used queued 5m
  mail as one of its measures. Detection times are still recorded for every 5m trigger; queue
  and delivery times for 5m no longer exist from this deploy on.
- **Tests:** all backend suites passed (market-data 6,205), frontend 1,011 passed, type check
  clean. A copy-of-database test shows a fresh 5m trigger is recorded and not queued.
