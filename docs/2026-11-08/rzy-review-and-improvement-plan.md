# Little RZY — implementation review and improvement plan (2026-11-08)

Written for the platform owner deciding what to do with Little RZY, and for whoever builds the
next version. It answers three questions: is RZY implemented correctly, why does it not work, and
what should be tried next, in what order, and how we will know.

Inputs: the original method (sources at the end), the build spec (`docs/Little RZY Integration.md`),
the engine (`services/market-data/src/services/little_rzy.py`), and today's evaluation run
(`rzy-evaluation-v2-results.md`: 744 trades, 155 stocks, 5m/15m/1h from 2026-08-04, daily from 2023-12).

---

## 1. Bottom line

1. **The code does what the spec says.** I found no rule-logic bug. The losses are not an
   implementation error.
2. **The spec is a thin slice of the original method.** Marci Silfrain's Little RZY is a top-down,
   discretionary method: direction from daily/weekly charts, structure and entry on lower
   timeframes, pullbacks on drying volume, the stock moving with the market. We built only the
   pattern geometry and ran it on every timeframe independently. The parts we left out are the parts
   a trader uses to decide **which** patterns to take.
3. **The mechanics lose in a specific way.** Ninety percent of trades exit on the first close back
   through the trendline, after a median 4 bars, for about −0.15R each. Only 3% reach the
   measured-move target (+3.2R each). 22 big wins cannot pay for 670 small losses and 52 full stops.
   On 5-minute bars, trading costs alone take about 0.09R per trade.
4. **The best lead is the original method's own workflow:** trade intraday structures only in the
   direction of the daily trend, on pullbacks where volume dries up. On data already seen this
   looks promising; in the 14 unseen sessions it did not hold. It must be confirmed on new sessions
   before anything changes.
5. **Confirmation takes time.** Detecting a real +0.15R edge needs about 280 trades in the tested
   group. At today's rate a filtered group collects that in roughly 3 months, unless the universe
   grows.

**Recommendation now:** keep RZY research-only. Consider switching off 5-minute RZY emails (they
are the most cost-hit and most numerous) until a variant passes the gates in section 7. That is the
owner's call; nothing in this plan changes live behaviour on its own.

---

## 2. Is it implemented correctly?

### 2a. Against the build spec: yes

Checked by reading the engine against each spec section, and by replaying the 2026-11-08 snapshot
(the replay reproduced the published 744 baseline trades exactly).

| Spec rule | Engine | Status |
|---|---|---|
| Pivots 7/7, known only after the 7th right bar closes | `_pivots`, confirmed at bar i for pivot i−7 | Correct |
| Equal extrema: earliest wins; outside bar that is both types ignored | strict left / non-strict right; ambiguous skipped | Correct |
| Trend: last two highs and two lows both rising (or both falling) | `_pivots` trend block | Correct |
| A–B–C: impulse ≥ 1.5 ATR at B, retracement 20–80%, C beyond A | `_detect` | Correct |
| Line through A and C in bar-index time | `line()` | Correct |
| Extreme strictly between A and C, earliest on ties; D measured at the extreme's bar | `_detect` | Correct |
| Freeze at C confirmation; replay C→confirmation for invalidation or early target | `_detect` loop | Correct |
| Bollinger context at C: long needs C.low ≤ middle + 0.25 ATR | `_detect` | Correct (spec's choice; see 2b) |
| Trigger: touch ±0.1 ATR, close beyond line, directional candle, beyond prior bar's extreme | `_active` | Correct |
| No-chase: block a long closing above the upper band | `_active` | Correct |
| Stop: beyond min/max(C, trigger bar, line) + 0.25 ATR at trigger | `planning_reference` | Correct |
| Next-open fill; recheck reward/risk with costs; epoch must be unchanged | `_positions` | Correct |
| Exit at target, stop (stop first if both), or next open after a close through the line | `_positions` | Correct |
| Sequence increments once per eligible structure, including Bollinger rejects | `_detect` | Correct |

### 2b. Against the original method: important gaps

| Original method (sources below) | What we built | Gap |
|---|---|---|
| Direction from daily/weekly charts; entries on 5–10 minute charts. "Does intraday structure align with the daily trend?" | Every timeframe trades on its own trend | **Missing the top-down filter** |
| "First and second Little RZY in a fresh trend" are strongest | Trend from the stream's own last two pivots; on 5m it resets constantly | **"Sequence 1" is 80% of trades**: 573 of 718 trades with a known pullback number. The label no longer means a fresh trend |
| Volume expands on the impulse and dries up on the pullback | Not used (volume only for a ≥1-share liquidity check) | **Missing** |
| Trend is stronger when the S&P 500 moves the same way | Not used | **Missing** |
| Early structures forming near the outer band (lower band in an uptrend) are higher probability | Context only requires the pullback to reach near the middle band; outer-band distance is recorded, never used | **Weaker than the source** |
| Avoid stops so tight that a retest of the line stops you out | Any close through the line exits at the next open, and entry is at the line | **Effectively the tightest possible exit** |
| 50% retracement healthy; beyond 61.8–78.6% weakening | Accepts 20–80% | Looser than the source (but see 3: the data does not favour 38–62%) |
| Avoid the volatile New York open (one source); opening-range retests (another) | No time rules | Unclear in the sources; not a priority |
| Discretion: skip "too extended or irregular" structures; parabolic trends are weakest | Every geometric match trades | Partly addressable (slope, extension filters) |

### 2c. Design defects (not bugs) that cost money

| ID | Defect | Evidence (744 baseline trades) |
|---|---|---|
| D1 | Same-timeframe two-pivot trend resets too often, so "fresh trend" and the sequence filter lose meaning | 573 of 718 known-sequence trades are #1 |
| D2 | Entry at the line plus exit on the first close through it acts as a hair-trigger stop | 670 of 744 exits; median 4 bars; 63% of these lose 0–0.5R |
| D3 | One parameter set for all timeframes, counted in bars | 5m pivots confirm in 35 minutes; daily in 7 sessions |
| D4 | Costs dominate 5m: median 0.094R per trade (15m 0.046, 1h 0.024, daily 0.008) | 5m mean −0.175R net vs −0.059R before costs |
| D5 | Universe includes low-priced and thin names | Stocks under ~$72: mean −0.26R, edge −0.12R (the other two price thirds are near zero or better) |
| D6 | Unknown-sequence trades (trend began before the data) behave very differently | 26 trades, edge −0.48R (tiny sample) |

---

## 3. What the data suggests (exploratory, not proof)

"Edge" is the trade's R minus its random-timing twins; "mean R" is the raw result after costs. These splits come from data the evaluation has already seen, cut many ways. Some will be luck. They
are used only to choose **which hypotheses to test next**, never as evidence on their own. Script
and per-trade output: `rzy-review-evidence/features.py`, `features.csv`.

| Feature (intraday trades, split in thirds) | Worst third | Best third | Agrees with the source? |
|---|---|---|---|
| Pullback volume ÷ impulse volume | High (≥0.85): edge −0.077R | Low (≤0.52): +0.029R | **Yes**: volume drying up |
| Daily-trend alignment (primary H3) | Opposed: −0.059R | Aligned: +0.095R | **Yes**: top-down |
| Aligned **and** volume ratio < 1 | — | 168 trades: edge +0.113R, mean +0.081R before costs | Both rules together |
| Planned cost as a share of risk | ≥0.107R: mean R −0.30 | ≤0.052R: mean R −0.05 | Practical, not in source |
| Share price | < $72: mean R −0.26 | $72–257: mean R −0.03 | Practical |
| Retracement depth | 38–62%: edge −0.046R | 20–38% or 62–80%: ≈ 0 to +0.02R | **No**: 50% "healthy" not supported |
| Stop distance in ATR | < 2.3 ATR: mean R −0.24 | > 3.0 ATR: mean R −0.03 | Yes: avoid tight stops |
| Trendline slope (ATR per bar) | Flattest third: mean R −0.24 | Middle: mean R −0.09 | Partly: weak trends do worst |
| Time of day | 12:00–14:00: edge −0.068R (290 trades) | 10:30–12:00: +0.023R | Midday chop is a common trader rule |

How trades end: 22 targets (+3.19R each, median 13.5 bars), 52 stops (−1.29R), 670 line exits
(−0.15R). Only 25% of trades ever get +0.5R ahead and 13% ever reach +1R. **The entries are
the main problem:** most trades never move meaningfully in their favour, so exit tweaks alone
cannot rescue them. The stop/target-only variant, which removes the line exit, was **worse**
(−0.18R edge), confirming that.

---

## 4. Diagnosis, as a trader would put it

- **We are trading every pullback in every trend on every timeframe.** A discretionary RZY trader
  takes few trades: in the direction of the bigger picture, in stocks moving with the market, on
  orderly pullbacks. The mechanical version has no way to say "not this one".
- **The 5-minute chart is the worst place to run it unfiltered.** It has the most noise, the
  smallest structures relative to costs, and the fastest-resetting trend definition. The source
  uses 5–10 minute charts only for *timing* an idea formed on daily/weekly charts.
- **The entry sits on the invalidation line.** Buying the third touch of a rising line and exiting
  on the first close below it means normal noise around the line ends most trades. That is what
  the 90% line-exit rate and 4-bar median show.
- **The measured-move target is a long way off** (median about 9 ATR). That is fine for a
  discretionary swing trader who sits through retests; it is rarely reached with a one-close exit.

---

## 5. Improvement plan

Each change is one predeclared variant, tested against the unchanged baseline with the v2
framework, and judged **only on sessions after this document is committed**. Nothing is tuned
on seen data. A change graduates only through the gates in section 7.

### Phase 0 — measurement fixes (small, do first)

| ID | Work | Why |
|---|---|---|
| P0-1 | Declare VOO as the market-regime proxy (SPY is not stored) in analysis version `RZY_EVALUATION_V2_1` | Regime slice was blank |
| P0-2 | Add per-trade features to `trades.csv`: volume ratio, slope, retracement, stop ATR, planned cost R, price | Every hypothesis below needs them; today they come from a side script |
| P0-3 | Scheduled re-run every 2 weeks (export, evaluate, publish), run off the production box or as a niced child process on a weekend | Forward evidence accumulates automatically; respects the 2026-11-04 memory incident |
| P0-4 | Keep research switches **out of `Config`** (subclass flags or a separate research config) | Adding a `Config` field changes the config hash, which rebuilds every live stream |

### Phase 1 — the original method's top-down filters (highest priority)

| ID | Variant | Definition (fixed now) | Source / data |
|---|---|---|---|
| **V1** | Daily-trend filter | Intraday trade allowed only if the symbol's daily RZY trend (as of the last closed daily bar) equals the trade direction | Source workflow; H3 |
| **V2** | V1 + volume dry-up | V1 and mean pullback volume (B+1..C) < mean impulse volume (A..B) | Source rule; strongest exploratory cell |
| V3 | V1 + market agreement | V1 and VOO's last daily close on the same side of its 50-day average as the trade | Source ("stronger when the S&P moves the same way") |
| V4 | Cost and liquidity floor | Skip setups whose planned round-trip cost exceeds 0.10R, or price < $10 | D4/D5; makes 5m results honest |

Run V1–V4 on all intraday timeframes, but **report 15m and 1h separately from 5m**. If 5m stays
negative after costs under every variant, retire 5m as an *entry* timeframe and keep it only for
timing (Phase 3).

### Phase 2 — trade management (only after Phase 1 shows a positive group)

| ID | Variant | Definition | Rationale |
|---|---|---|---|
| M1 | Line exit with buffer | Exit only on a close beyond the line by ≥ 0.25 ATR, or two consecutive closes beyond it | Source: avoid exits so tight a retest stops you out |
| M2 | Partial at +1R | Take half at +1R, move the rest's stop to breakeven plus costs, run it to the measured target | 13% reach +1R but only 3% the target |
| M3 | Time stop | Close at the next open if not +0.5R after 10 bars | Stalled setups tie up the one position each stream allows |

Do not test M1–M3 on the unfiltered baseline: the problem there is the entries, and exit tweaks
on bad entries only reshuffle losses.

### Phase 3 — a true multi-timeframe RZY (larger build, separate strategy version)

`LITTLE_RZY_TOPDOWN_V1`, built only if Phase 1 confirms the direction filter:

1. **Direction:** daily trend from daily pivots (and optionally weekly), not the entry stream's own.
2. **Structure:** A–B–C found on 1h or 15m, in that direction only.
3. **Timing:** 5m used only to time the entry once price reaches the 15m/1h line, with a 5m
   rejection candle. The stop stays at the 15m/1h structure, so 5m costs become a small share of risk.
4. **Sequence:** counted within the daily trend epoch, so "first/second pullback" means what the
   source means.

This is a new strategy with its own config hash, evaluated from scratch by the same framework.
The current V1 strategy keeps running unchanged alongside it.

### Not recommended

- **Parameter sweeps** (pivot lengths, ATR multiples, retracement bands) on the stored history. With
  744 trades and a 0.9R spread per trade, a sweep will find a "winner" by luck.
- **Adding ML or more indicators to score setups** before a simple filter shows a real edge.
- **The 50%-retracement rule**: the source suggests it, the data does not support it. Leave it out
  unless new evidence appears.
- **Turning on live or paper execution** for RZY.

---

## 6. Engineering tasks

| Order | Task | Files | Size |
|---|---|---|---|
| 1 | VOO regime proxy, new analysis version | `rzy_evaluation_context.py`, preregistration addendum | S |
| 2 | Feature columns per trade (volume ratio, slope, retracement, cost R, price) | `rzy_evaluation.py`, `evaluate_little_rzy.py` | S |
| 3 | Variant filters V1–V4 as **trade-level gates replayed inside the engine** (a research subclass that refuses to trigger), not slices, so blocked trades free the stream for later setups | `rzy_evaluation.py` (new engine subclasses), tests | M |
| 4 | Scheduled re-run and publish every 2 weeks; page shows the variant table | runner, `LittleRzyEvaluation.tsx` | M |
| 5 | Exit variants M1–M3 as engine subclasses (like the existing `LINE_EXIT` switch) | `rzy_evaluation.py`, tests | M |
| 6 | Optional: switch off 5m RZY emails, or relabel them "timing only", if the owner decides | RZY preferences/email | S |
| 7 | Phase 3 strategy, only after Phase 1 passes | new module + its own evaluation | L |

Each engine variant needs a causality test like the v2 truncated-replay proof, and a sabotage
check showing the test fails when the filter peeks at a later bar.

---

## 7. How we will know: test protocol and promotion gates

**Unit of evidence:** trades whose signal bar closes after this document's commit time. Earlier
data is used only to check that the code runs.

**Comparisons:** each variant against the unchanged baseline on the same sessions, with
20 random-timing twins per trade, intervals resampling whole stocks. At most four Phase 1 variants;
judge them with a Holm correction so four tries do not inflate the chance of a fluke.

**Sample size:** the edge per trade has a spread of about 0.9R, so detecting an edge of

| True edge | Trades needed (80% power) |
|---|---|
| +0.20R | about 160 |
| +0.15R | about 280 |
| +0.10R | about 630 |

At the current rate, about 9 baseline trades a day, a filter keeping 30% of trades reaches 160 in
about 2 months and 280 in about 3 months. Adding liquid S&P 500 names to the stored universe
shortens this proportionally, at a data-budget cost.

**Promotion gates (all required, before any alert wording or paper-trading change):**

1. Edge vs random timing has its 95% lower bound above zero on unseen sessions, after the Holm correction.
2. Mean R after costs is above zero, not just better than the twins.
3. At least 150 trades on at least 40 different stocks.
4. Positive in at least two of three calendar months.
5. Worst drawdown in cumulative R no worse than the baseline's.
6. Then 4 weeks of live shadow running with the alert labelled as a candidate, before any default changes.

If no variant passes after about 3 months of new sessions, the honest conclusion is that the
mechanical RZY has no exploitable edge in this universe. It would then remain a charting and
education tool, not a signal.

---

## 8. Sources

- Marci Silfrain, "Measured Move Trend Strategy (Little RZY)", Chart Fanatics:
  https://www.chartfanatics.com/strategies/measured-move-trend-strategy
- Video recap (third-party summary, unverified): https://magica.com/youtube-summarizer/marci-silfrain-reveals-a-simple-yet-powerful-trading-strategy-that-can-predict-market-tops-and-tops-AVVM-FyewLg
- Video recap (AI summary, unverified): https://youtubesummary.com/summary/6KJSeSGC7lo
- John Bollinger, Bollinger Band rules: https://www.bollingerbands.com/bollinger-band-rules
- Build spec: `docs/Little RZY Integration.md`; evaluation: `docs/2026-11-08/rzy-evaluation-v2-results.md`

The original method is discretionary and partly known only through secondhand summaries. Where
the sources disagree (opening hour; outer-band structures as strength or exhaustion), this plan
does not rely on either reading.
