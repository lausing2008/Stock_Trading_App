# Audit: News LLM Call Sites, the HMM, and Actual Predictive Performance (2026-09-22)

**User ask, verbatim:** *"Review and audit on the event intelligence and see if news_classify,
news_sentiment and market_pulse are effective to help market regime and predict the market and
stock trends. And are we using Hidden Markov Model to predict the stock movement? How well on
our prediction? How to improvement?"*

**Method:** every number below was measured against live production (EC2 Postgres, the fitted
HMM pickle in the running container, and the platform's own `filter_audit` endpoint). Nothing
here is inferred from code alone. Two research subagents produced supporting material; every
load-bearing claim they made was independently re-run before being recorded here, and one of
their numbers was found wrong and corrected (see "A correction made during this audit").

---

> ## ⚠ MATERIAL CORRECTION (added same day, after the user challenged the framing)
>
> **The user asked "check only this month on avg returns, it should be better" — and was right.
> The pooled figures below are dominated by June–August and overstate the CURRENT state.**
>
> Benchmark-matched alpha (each outcome vs SPY over its own `entry_date` → `exit_date` window),
> by month:
>
> | month | N | BUY alpha | t | SELL alpha | t | BUY−SELL gap |
> |---|---|---|---|---|---|---|
> | **2026-09** | 588 | **−0.32%** | **−0.99 (ns)** | +0.40% | 1.15 (ns) | **0.72 pp (ns)** |
> | 2026-08 | 5,148 | −1.81% | −14.06 | +1.49% | 5.72 | 3.30 pp |
> | 2026-07 | 4,387 | −2.02% | −13.20 | −0.20% | −0.83 | 1.82 pp |
> | 2026-06 | 2,884 | −6.53% | −24.95 | +1.03% | 4.74 | 7.56 pp |
> | 2026-05 | 122 | −3.13% | −4.14 | +0.79% | 0.79 | 3.92 pp |
>
> **September BUY alpha is statistically indistinguishable from zero (t = −0.99), and the
> BUY/SELL inversion is no longer significant either.** The trajectory is a monotone improvement:
> **−6.53% → −2.02% → −1.81% → −0.32%** over four months. The system is now roughly break-even
> on a benchmark-relative basis, not the badly-inverted engine the pooled numbers imply.
>
> **Three caveats that keep this from being a clean "fixed" verdict:**
> 1. **Lower power.** n=588 vs August's 5,148; the 95% CI on September's alpha spans roughly
>    −0.95% to +0.31%. A real but smaller negative alpha cannot be ruled out.
> 2. **September is censored and horizon-biased.** Only 649 of its BUY outcomes have resolved, and
>    **75.5% are SHORT-horizon** (vs 39% in August) because longer horizons have not matured yet.
>    SHORT is consistently the least-bad horizon. Horizon-matched, September still beats August
>    (SHORT −0.55% vs −1.15%; SWING −2.05% vs −3.11%; GROWTH −2.44% vs −2.81%), so the improvement
>    is real — but the headline number flatters it.
> 3. **The measurement regime changed on 2026-09-02** (`AUD-SIGNAL3-EVALSELECTIONBIAS`). September
>    is the first month measured under the fixed selection logic, so part of the improvement may be
>    *measurement* rather than *system*. This cannot be separated with current data; it resolves
>    naturally as October accumulates.
>
> **What this changes:** the Tier-3 "the ranking must be rebuilt" recommendation is **downgraded**.
> The right read is that something between June and September worked, and the priority is to
> identify what, protect it, and keep going — not to rebuild from scratch. Everything in §3C
> remains a correct description of the **pooled May–September** population; it is no longer a safe
> description of the system as it stands today.

## TL;DR (pooled May–September — read the correction above first)

**THE HEADLINE: the signal engine is inverted, not merely weak.** SELL signals outperform BUY
signals by **3.67 pp** (SELL +1.19%, n=4,838 vs BUY −2.48%, n=13,238), confirmed on two
independent data paths. Selection-adjusted, the ordering is perfectly monotone in the wrong
direction: `SELL +1.89% > WAIT +1.20% > HOLD −0.76% > BUY −2.04%`. Confidence is inverted the same
way. An exhaustive ~1,020-cell search found **no positive-expectancy subset anywhere** — with the
positive-cell count *below* chance expectation. This is a polarity/ranking defect, not a tuning
problem.

Supporting answers to the four questions asked:

1. **The three LLM call sites do not feed market regime at all.** Regime comes purely from
   SPY/QQQ/VIX/VIX9D/IWM/MDY prices plus the HMM overlay. `market_pulse` is display-only;
   `news_sentiment` and `news_classify` affect per-symbol scores, not regime. **The hot-news gate's
   sign is also backwards** — material *negative* news is followed by the *highest* alpha
   (+1.64 pp, n=977), which is exactly when the platform suppresses BUY signals.
2. **There is an HMM, but it does not predict and cannot predict stocks.** It classifies the
   *current* market-wide regime and never uses its own transition matrix; its entire live effect is
   one boolean that fired on **2.3% of days**. It *can* be made predictive — its states do separate
   forward returns (§3B) — but the 700-day lookback is the binding constraint.
3. **Predictions are measurably negative-edge.** Date-matched alpha vs SPY: **−1.076 pp per 6-day
   trade, t = −9.24, n = 4,134**. Live paper trading: **−$8,222 over 123 closed trades**. No
   holding period from 1 to 20 days rescues it.
4. **Two cheap fixes exist that are independent of the rebuild:** flat position sizing (the two
   largest size quartiles lost −$10,188 against a −$8,222 net loss, §3D.4) and pulling GROWTH's
   unreachable 7.8-ATR target in (§3D.3).

---

## 1. Predictive performance — the headline measurement

### 1a. Date-matched alpha (computed directly, not from a stored aggregate)

4,134 SWING BUY signals over 180 days, entry T+1, 6-trading-day hold, each compared to SPY over
**its own matched window** (not an average-vs-average comparison, which is biased because signals
cluster on particular dates):

| Metric | Value |
|---|---|
| Mean signal return | **−1.108%** |
| Mean SPY, same windows | −0.032% |
| **Mean alpha** | **−1.076%** |
| Median alpha | −0.521% |
| Alpha stdev | 7.49 |
| **t-statistic** | **−9.24** |
| Trades beating SPY | 44.8% |

A t-statistic of −9.24 on n=4,134 is not sampling noise. Note the date-matched SPY figure
(−0.032%) differs substantially from an unmatched period average (+0.89%) — the matched version
is the correct one and is what is reported here.

### 1b. Corroboration from three independent sources

- **The platform's own `filter_audit`** (`GET /signals/filter_audit?lookback_days=180&hold_days=6`):
  overall SWING BUY win rate **39.2%** on n=4,136. Unfiltered signals (`filter_count=0`, n=2,462)
  return **−1.55%**; fully filtered improves only to −1.11%. The filters help marginally; they are
  polishing a negative-expectancy entry.
- **`signal_outcomes`** (n=13,238 BUY rows, 2026-05-25 → 2026-09-15): all four styles negative on
  **both mean and median**. SHORT −1.31% (n=3,906), SWING −2.72% (n=3,291), GROWTH −2.89%
  (n=3,713), LONG −3.42% (n=2,328). HK −5.28% (n=2,604) vs US −1.79% (n=10,634).
- **Live paper trading**, 123 closed trades, 2026-06-17 → 2026-09-18:

  | | |
  |---|---|
  | Win rate | **32.5%** |
  | Avg win / avg loss | +$391 / −$288 |
  | Total P&L | **−$8,222** |
  | Expectancy | **−$66.85 / trade** |

  Breakeven at a 1.36 payoff ratio needs ~42.4% wins; actual is 32.5%.

  Exit mix — **61 stop-outs versus 7 target-reaches**:

  | exit_reason | n | total P&L |
  |---|---|---|
  | stop_hit | 61 | **−$16,078** |
  | trailing_stop | 13 | **+$5,748** |
  | target_reached | 7 | +$1,968 |
  | breakeven_stop | 34 | +$303 |
  | momentum_exit | 5 | −$228 |
  | hold_stall_timeout | 2 | +$250 |
  | signal_exit | 1 | −$186 |

### 1c. Confidence is an anti-signal

Independently re-run against `signal_outcomes`:

| confidence ≤ | N | hit rate | avg return |
|---|---|---|---|
| 10 | 174 | 29.9% | −4.19% |
| 20 | 1,235 | 35.8% | −1.86% |
| 30 | 2,160 | 41.3% | −1.61% |
| 40 | 2,381 | 42.9% | −1.67% |
| 50 | 2,316 | 42.7% | −1.81% |
| 60 | 1,882 | 41.5% | −2.73% |
| 70 | 1,338 | 38.9% | −3.14% |
| 80 | 902 | 41.2% | −3.65% |
| 90 | 505 | 36.6% | −5.35% |
| 100 | 227 | 34.8% | −6.39% |
| >100 | 118 | 29.7% | **−11.43%** |

Monotonic degradation across seven consecutive buckets above 40. Hit rate stays flat at ~38–43%
everywhere above 20 — confidence carries **no directional information**, only a reliable negative
relationship with return magnitude. Anything that sizes up or selects on high confidence is
currently harmful.

**Separate data-integrity issue:** 118 rows have `confidence > 100`, which should not be possible.

### 1d. ML layer

- **Dead recall is fixed** — of 1,297 live model artifacts, 354 (27.3%) have `recall==0 and
  precision==0`, and **all 354 are `oos_suppressed=True`** (zero serving live). The prior
  "41/249 models with zero true positives" finding is closed.
- **But 1,170 of 1,297 (90.2%) are suppressed overall.** Median `cv_auc_mean` = **0.539**; 44.2%
  are below 0.52 (coin-flip); median `n_test` = 30. The ML layer is almost entirely pinned at
  0.5 neutral.
- Of 76 promoted `tune_history` rows with realized EV backfilled, **0 have positive realized EV**
  (avg predicted −1.85%, avg realized −1.43%).

---

## 2. The three LLM call sites

### 2a. Verdicts

| Call site | Role | Evidence | Verdict |
|---|---|---|---|
| `market_pulse` | **Display-only** | Zero consumers outside `market-data/src/api/news.py`; only frontend callers (`intelligence.tsx`, `market-pulse-dashboard.tsx`) | Honest card. ~1.9k tokens/week. Keep. |
| `news_sentiment` | Compresses fused score (SWING ×0.75/0.85, GROWTH ×0.80/0.90) at `signal-engine/src/generators/signals.py:2316-2325` | `filter_audit`: fires on 92 of 4,136 (2.2%). Active 35.9% win / −0.76% vs inactive 39.3% / −1.12% | Negligible reach, ambiguous effect. 270k tok/wk. |
| `news_classify` | Sets `stockai:hot_news:{symbol}` → compresses BUY signals at `signals.py:2450-2470` | See below | **87% of all Claude spend; ~71% of output unreachable; sign is backwards** |

**None of the three feeds market regime.** The regime dict is built in
`paper_trading_engine.py:1520-1560` from SPY/QQQ/VIX/VIX9D/IWM/MDY via yfinance, plus the HMM
overlay. The user's question "are these effective to help market regime" is answered structurally:
they do not touch it.

### 2b. The `news_classify` waste is an ordering bug

In `news-intelligence/src/services/storage.py`, `classify_in_batches()` is called **before**
symbols are resolved — every new headline goes to Claude, and only afterwards does the code
discover it maps to nothing:

```
263,457 classifications total → 186,461 (70.8%) have symbol IS NULL
```

| Source | Items | Unusable (NULL symbol) | Wasted |
|---|---|---|---|
| `sec_edgar` | 148,860 | 144,322 | **97.0%** |
| `pr_newswire` | 37,153 | 37,102 | 99.9% |
| `businesswire` | 5,043 | 5,037 | 99.9% |
| `alpaca` | 72,401 | 0 | **0%** |

Alpaca is 0% wasted precisely because it ships **native ticker tags**. The EDGAR CIK and the RSS
headline text are both available *before* the Claude call — `symbol_for_cik()` and
`extract_symbols()` are pure local lookups. The ordering is simply backwards.

### 2c. EDGAR specifically is structurally incapable of being a useful signal source

What the EDGAR poller actually ingests:

| Filing type | Items | On a tracked symbol | Flagged material |
|---|---|---|---|
| **4** (insider transaction paperwork) | 66,491 | 962 (1.4%) | **48** |
| **8-K** (material corporate events) | 37,995 | **169 (0.4%)** | 180 |
| **424B2** (prospectus supplements) | 29,303 | 3,216 | **1** |
| 497K / 485BXT / 497 / 485BPOS / 497J (fund prospectuses) | ~7,400 | **0** | **0** |
| 8-K/A, 424B3, 424B5, 4/A | ~4,750 | ~180 | ~19 |

Three separate problems, each sufficient on its own:

1. **29,303 prospectus supplements were classified to produce exactly ONE material flag.**
2. **~7,400 mutual-fund prospectus filings map to zero tracked symbols and zero material flags** —
   pure, total waste.
3. **The one genuinely useful filing type, 8-K, lands on a tracked symbol only 169 times out of
   37,995 (0.4%)** — because the poller consumes EDGAR's whole-market firehose while the tracked
   universe is ~200 stocks.

Forward-return measurement confirms the emptiness: over 150 days, the only EDGAR-on-tracked-symbol
bucket large enough to measure (n≥25) is `is_material=false, neutral` — n=3,976, avg 6-day forward
return +0.27%, 47.6% up. There is **no EDGAR material bucket on tracked symbols at measurable
scale at all**.

**Answer to "is news_classify from EDGAR not effective?" — correct, and worse than a cost problem.
It is structurally incapable of producing tradeable signal at its current configuration.**

### 2d. THE KEY FINDING — the hot-news gate's sign is backwards

Alpaca-sourced news on tracked symbols, deduplicated, 150 days, 6-trading-day forward return,
benchmarked against SPY over each event's own matched window:

| Material? | Sentiment | N | avg | **median** | **alpha vs SPY** |
|---|---|---|---|---|---|
| yes | **negative** | 977 | +2.13% | +0.76% | **+1.64%** |
| yes | neutral | 274 | +1.85% | +0.54% | +1.29% |
| yes | positive | 1,321 | +1.12% | +0.31% | +0.77% |
| no | **negative** | 424 | +2.69% | +0.95% | **+2.05%** |
| no | positive | 1,048 | +1.06% | +0.25% | +0.84% |
| no | neutral | 1,506 | +1.07% | +0.17% | +0.77% |

The ordering **negative > neutral > positive** holds consistently in *both* the material and
non-material groups, on *both* mean and median, and benchmark-relative. Negative-news alpha is
roughly **2× positive-news alpha**.

This is a textbook short-horizon overreaction/mean-reversion pattern. **And the platform does
exactly the wrong thing with it:** `signals.py:2465-2470` compresses the fused BUY score by
×0.70 (first hour) or ×0.85 (second hour) when `sentiment_label == "negative"` — i.e. it
systematically suppresses signals on the stocks with the *best* subsequent returns, and lets
through those with the worst.

**Caveats, stated honestly:**
- This measures returns after *news events*, not after *news-gated BUY signals*. The gate only
  applies when a BUY signal co-occurs, so the interaction could differ in magnitude.
- News days carry higher volatility; part of the premium is compensation for that risk.
- Measured at a 6-day horizon only.
- Flipping the sign would **reduce a drag, not create edge** — the base signal is still negative.

### 2e. The hot-news gate has never been measured

`hot_news_flag` appears in neither `SUPPRESSION_BOOLEAN` nor `SUPPRESSION_NAMED` in
`signal-engine/src/api/analytics.py`, so the platform's own `filter_audit` has never scored it.
There is also **no news outcome table**, while every other alert family has one
(`squeeze_alert_outcomes`, `prebreakout_alert_outcomes`, `options_flow_alert_outcomes`,
`dark_pool_alert_outcomes`). News is the one gate flying completely blind — which is precisely
how a backwards sign survived.

### 2f. Current steady-state cost (7 days)

| Call site | Calls | Tokens |
|---|---|---|
| `news_classify` | 5,618 | **1.77M** |
| `news_sentiment` | 1,556 | 270k |
| `market_pulse` | 4 | 1.9k |

The 5.44M-tokens-in-one-day `BUG-NEWSCLASSIFY-REPEATCOST` dedup incident is genuinely fixed.

---

## 3. The HMM

**`services/market-data/src/services/hmm_regime.py`** — a 4-state `GaussianHMM` on
(VIX, SPY 5-day return, IWM vs EMA200).

### 3a. It does not predict, and structurally cannot predict stocks

- All three features are **market-wide**. There is no per-stock input, so individual stock
  movement is out of scope by construction.
- `predict_current()` returns `model.predict_proba(X)[-1]` — the posterior over the **current**
  state. **`model.transmat_` is fitted and then never used at inference.** The transition matrix is
  the entire forecasting mechanism of an HMM; without it, this is a labeller, not a forecaster.

### 3b. Its entire live effect is one boolean, on 2.3% of days

`paper_trading_engine.py:5976`:
```python
if live_regime.get("hmm_bear_pressure"):          # P(bear) > 0.50
    regime_size_mult = min(regime_size_mult, 0.70)
```
A 4-state probabilistic model collapsed to a single flag. Measured over the model's own fitted
history (474 observations, 2024-10-29 → 2026-09-21): **the trigger fired on 11 of 474 days = 2.3%.**

### 3c. The fitted model contradicts its own stated purpose

State means, in real units, from the live pickle:

| Label | VIX | SPY 5d | IWM vs EMA200 | Expected duration | Days observed |
|---|---|---|---|---|---|
| bull | 19.45 | +2.77% | **−0.88%** | 7.2 d | 74 (15.6%) |
| neutral | 16.93 | +0.43% | **+10.74%** | **71.9 d** | 217 (45.8%) |
| choppy | 18.88 | −0.56% | +1.61% | 16.6 d | 172 (36.3%) |
| bear | **36.74** | **−4.63%** | −14.99% | 5.5 d | 11 (2.3%) |

- The code comment claims the HMM *"catches early-phase downturns via volatility clustering
  before SMA/VIX thresholds trigger."* **The fitted bear state is VIX 36.74 with SPY −4.63%/5d —
  a full panic, not an early warning.** A VIX-36.7 print trips every rule-based VIX gate already.
  The comment describes behaviour the model does not have.
- **The labels are questionable.** The state called "bull" has small-caps **below** their 200EMA
  and lasts 7.2 days; the state called "neutral" has them **+10.74% above** and lasts 71.9 days.
  Because `_label_states()` ranks primarily on 5-day SPY return (a short, mean-reverting quantity),
  "bull" effectively means *"SPY just popped"* — frequently a bounce inside a downtrend.

### 3d. Model quality and operational issues

- **Overparameterised:** ~51 free parameters (4 states × [3 means + 6 full-covariance terms] +
  transition + initial) fit on 474 observations that are heavily autocorrelated — `spy_5d_ret` is
  a 5-day overlapping window and `iwm_vs_e200` is an EMA ratio, so effective independent sample
  size is far lower than 474.
- **Degenerate posterior:** max state probability exceeds 0.99 on **383 of 474 days (80.8%)**. A
  well-calibrated 4-state regime model should not be near-certain four days in five. The live
  reading at audit time was `choppy 0.9042` with `neutral` and `bear` at **exactly 0.0**.
- **Ephemeral persistence:** `MODEL_PATH = "/tmp/hmm_regime.pkl"`. Verified: container started
  `2026-09-22T03:07:48Z`, pickle written `03:09` — **it refits from scratch on every container
  recreate**, each refit being a fresh 700-day yfinance download plus a single-seed
  (`random_state=42`) EM fit with no multi-restart.
- **Never backtested.** `backtest/position_scaling_gate.py` cites `hmm_regime.py` only as a
  *pattern precedent*; no evaluation of the HMM's own accuracy exists anywhere in the repo.

---

## 3A. Is there a higher-impact news feed from Unusual Whales?

Asked as a follow-up: *"Can we get any news data from UW with more impact?"*

**Answer: not from UW's news endpoint — but there is a far better non-news UW feed sitting
completely unwired.**

### 3A.1 UW's news endpoint is worse than what you already have

`/api/news/headlines` is live on the BASIC tier. Measured against real responses (320 sampled
headline rows):

| Property | Measured |
|---|---|
| `sentiment` | `"neutral"` on **320/320 rows** — the field exists but never varies |
| `tags` | empty on **320/320 rows** |
| Ticker-tagged | **32–42%** (Alpaca is 100%, EDGAR ~3%) |
| `is_major` | `true` on 59% of the feed — not selective |
| Sources | `Tradex` 58–93%, then **PR NewsWire / Business Wire / GlobeNewswire** — the same wires already ingested at 99.9% unusable — plus crypto blogs |

Worse, its ticker tagging is a naive uppercase-token match that produces **real false positives**
on live rows: `['YORK']` on an Africa story (from "New York"), `['FOR','ET']` on a crypto story
(from "for" and "11 A.M. ET"), `['BROS']` on Dutch consumer confidence, `['RBA']` on Reserve Bank
of Australia commentary.

A *wrong* symbol attached to a live BUY-signal compressor is strictly worse than a NULL symbol,
which is at least inert. And because `sentiment` is constant, adopting it would **not** remove the
Claude cost — the LLM would still be needed. It fails on all three counts.

Filtering `sources='Unusual Whales'` returns only congress-trade summaries, already ingested via
`/api/congress/recent-trades`.

### 3A.2 The real answer: `/api/screener/analysts`, unwired

Independently probed live — **HTTP 200, 500 rows**:

| Property | Measured |
|---|---|
| Ticker-tagged | **500/500 = 100%** |
| Fields | `action`, `analyst_name`, `firm`, `recommendation`, `sector`, `target`, `ticker`, `timestamp` |
| Action mix | maintained 348, initiated 72, reiterated 32, **upgraded 22, downgraded 20**, reinstated 4 |
| LLM cost | **zero** — fully structured, nothing to classify |
| Quota cost | ~50 calls/day |
| Currently wired | **No** — zero references in the repo |

Sample row:
```json
{"timestamp":"2026-09-21T18:38:53Z","ticker":"J","action":"maintained","target":"161.0000",
 "sector":"Industrials","recommendation":"hold","analyst_name":"Jamie Cook","firm":"Truist Securities"}
```

Analyst upgrades/downgrades carrying explicit price targets are a substantially better
short-horizon move predictor than wire headlines, arrive pre-structured, and need no LLM at all.
This is the genuine "news data with more impact".

### 3A.3 Correction: the UW quota is NOT at 8%

A standing note in this project claimed UW usage was ~8% of the 120k/day BASIC allowance. **That
is stale.** Measured from the live Redis counters for 2026-09-21:

| Endpoint | Calls |
|---|---|
| `/api/stock/{ticker}/option-chains` | **66,175 (86.7%)** |
| `/api/option-trades/flow-alerts` | 4,993 |
| `/api/darkpool/{symbol}` | 3,755 |
| `/api/stock/{symbol}/gex-levels` | 648 |
| `/api/congress/recent-trades` | 366 |
| others (shorts, greeks, iv-rank, etf flows) | ~375 |
| **TOTAL** | **76,312 / 120,000 = 64%** |

`dq_check:uw_rate_limit_events_48h` also reports **9 rate-limit (429) events in 48h**. Real
headroom is ~44k/day, not ~110k. Adding the analysts feed (~50/day) is trivially affordable, but
any future per-symbol UW polling must be scoped against 44k, and the option-chain job is the
obvious place to look first if headroom is ever needed.

---

## 3B. Can the HMM actually be used for prediction? (measured, not theorised)

The user asked to use the HMM for stock prediction. Before building anything, the gating question
was measured: **do the HMM's states separate FUTURE returns at all?** If not, adding forecasting
machinery would be an expensive way to get nothing.

### 3B.1 Test 1 — the production model as-is (474 obs)

States computed **filtered** (posterior over the sequence ending at *t*, no future peeking —
matching what `predict_current()` does in production), then SPY returns measured strictly *after*
each day:

| state | n | fwd 1d | fwd 5d | fwd 20d |
|---|---|---|---|---|
| bull | 80 | +0.154% | +0.521% | +2.784% |
| neutral | 215 | +0.061% | +0.246% | +0.657% |
| choppy | 168 | +0.021% | +0.178% | +0.846% |
| **bear** | **11** | +0.393% | **+3.568%** | **+10.344%** |
| *unconditional* | | | *+0.347%* | |

The bear state is followed by the **best** forward returns — the opposite of how it is used. But
**n=11**, likely one or two VIX episodes. Not tradeable evidence.

### 3B.2 Test 2 — refit on 20 years (5,206 obs, converged)

`bear` sample goes from **11 → 588**, spanning 2008, 2011, 2015, 2018, 2020 and 2022:

| state | n | VIX mean | fwd 1d | fwd 5d | fwd 20d |
|---|---|---|---|---|---|
| bull | 1,590 | 12.8 | +0.055% | +0.178% | +0.664% |
| neutral | 1,716 | 17.2 | +0.066% | +0.296% | +0.908% |
| choppy | 1,312 | 22.5 | +0.016% | +0.135% | +0.998% |
| **bear** | **588** | **37.1** | +0.058% | **+0.451%** | **+1.699%** |
| *unconditional* | 5,206 | | *+0.049%* | *+0.237%* | *+0.946%* |

**The finding survives the sample expansion.** High-VIX states are followed by ~1.8–1.9× the
unconditional mean return; the lowest-VIX "bull" state has the *worst* 20-day forward return
(+0.664% vs +0.946%). Both directions are consistent with the well-documented volatility risk
premium.

**This means the 700-day lookback is the single binding constraint on the HMM's usefulness.**
VIX/SPY/IWM history goes back decades; the model currently sees under two years and therefore
almost never observes a bear regime (11 days).

### 3B.3 Test 3 — is it a RETURN effect or a VOLATILITY effect?

This determines whether the current gate should be flipped or merely re-justified. 20-day forward
SPY returns, 20-year fit:

| state | n | mean% | **stdev%** | **Sharpe** | worst% |
|---|---|---|---|---|---|
| bull | 1,580 | 0.664 | 3.16 | 0.210 | −31.0 |
| neutral | 1,706 | 0.908 | 3.42 | **0.265** | −18.2 |
| choppy | 1,312 | 0.998 | 5.09 | 0.196 | −29.4 |
| **bear** | **588** | **1.699** | **7.91** | 0.215 | −30.4 |
| ALL | 5,186 | 0.946 | 4.54 | 0.208 | −31.0 |

**Decisive nuance: the bear state has the highest mean return but 2.5× the volatility, so its
Sharpe (0.215) is indistinguishable from the market's (0.208) and worse than `neutral`'s (0.265).**

Therefore:

- **Do NOT flip the bear gate.** Reducing size in the bear state is defensible — not for the
  reason the code gives, but as ordinary **volatility targeting**. To hold risk constant against a
  2.5× volatility increase you would size down ~60%, so the current `min(mult, 0.70)` is if
  anything *under*-scaled.
- **The code's stated rationale is still wrong and should be corrected.** It claims the HMM
  "catches early-phase downturns... before SMA/VIX thresholds trigger." Measured: the bear state is
  a VIX-37 panic that is followed by *above-average* returns. It is a high-volatility marker, not
  a bad-returns predictor.
- **The genuinely unexploited finding is `neutral`** — the best risk-adjusted state (Sharpe 0.265,
  and much the shallowest worst case at −18.2% vs −31%). The platform currently does nothing
  special in it. Sizing *up* in `neutral` is better supported by this data than anything the
  current bear flag does.

### 3B.4 What "HMM for stock prediction" should actually mean here

Three options, with honest risk assessment:

| Option | What it is | Assessment |
|---|---|---|
| **A. Make the existing market HMM forecast** | Use `prob @ transmat_` for next-state probabilities; map states → empirical forward-return **and volatility** distributions; output expected return + expected vol instead of one boolean | **Recommended first step.** Small, uses the already-fitted-but-discarded transition matrix, and §3B.2/3B.3 show the states genuinely separate. Must be paired with the 20-year refit. |
| **B. Regime-conditioned per-stock scoring** | Keep the HMM as a market-wide *conditioning variable*; measure per-state historical performance by style/sector and adjust scoring | Sensible second step; needs enough history per (state × cohort) cell. Uses HMMs for what they're good at. |
| **C. Per-stock HMMs** | Fit an HMM per symbol on its own returns/volume/vol | **Not recommended.** The market-wide model already showed degenerate overconfidence (>0.99 posterior on 80.8% of days) on 474 obs; single-stock series are noisier with less history. High overfit risk for likely-negative return on effort. |

Prerequisites before any of the above ship: refit on 15–20 years (not 700 days), persist the model
outside `/tmp`, multi-restart EM instead of a single `random_state=42`, consider diagonal
covariance or fewer states to cut the ~51 free parameters, and **backtest before trusting** —
none of which exists today.

---

## 3C. Where is the edge? An exhaustive search that found none — and something better

A systematic search for any positive-expectancy subset. Event study rebuilt from raw `signals` +
`prices` (18,438 BUY rows, 2026-05-25 → 09-22), entry T+1, alpha vs SPY (US) / 2800.HK (HK) over
each signal's **own matched window**, significance by **day-clustered** t (overlapping windows make
naive t meaningless) and **symbol-clustered** t for survivors.

### 3C.1 The null result is unusually clean

- **~1,020 cells tested** — 552 univariate across 165 features, 311 deliberate conjunctions, ~157
  hand-run (hold periods, market × hold, sector, day-of-week, regime, confidence × market × hold,
  per-symbol).
- Of 552 univariate cells, **21 had positive mean alpha and only 2 cleared t_day > 1.96 — fewer
  than the ~27.6 expected by chance under a true null.**
- Of 311 conjunction cells, **zero** cleared positive + t_day > 1.96 + t_sym > 1.96.
- Every apparent winner collapsed under symbol clustering: `ml_model='lightgbm'` (+1.36%, t_day
  +2.85) was **4 stocks**, t_sym +0.27; `sector='Financial'` (+0.60%) is a **label-split artifact** —
  its sibling label `Financial Services` is −0.65%, and merged they are −0.02%.

**No subset of the BUY population has positive expectancy.** The positive-cell count being *below*
chance expectation at every stage is itself evidence of the negative.

### 3C.2 The signals are INVERTED, not uninformative — and this is the key diagnosis

Benchmarking BUY days against **the same stock's own unconditional forward alpha** isolates
selection skill from universe drift:

```
unconditional universe (every stock-day):  −0.32%   (US −0.09%, HK −1.07%)
BUY-day alpha:                             −1.82%   →  SELECTION = −2.04%  (t_day −3.27)
```

The engine does not inherit a bad universe. **It actively selects the worst days within it.**
And the ordering across all four signal classes is *perfectly monotone in the wrong direction*,
replicated independently at all four horizons:

```
SELL +1.89%  >  WAIT +1.20%  >  HOLD −0.76%  >  BUY −2.04%      (selection-adjusted)
BUY−SELL spread:  SHORT −3.69% | SWING −3.12% | LONG −3.85% | GROWTH −5.19%
```

**Independently verified on a separate data path** — the stored `signal_outcomes` table, which was
not used in the study above:

| signal_direction | N | avg return | median | % up |
|---|---|---|---|---|
| **SELL** | 4,838 | **+1.19%** | +0.86% | 55.5% |
| **BUY** | 13,238 | **−2.48%** | −0.97% | 43.5% |

A **3.67 pp gap in the wrong direction**, on both mean and median, on large N, confirmed by two
independent methodologies.

**Mechanism (plausible, monotone, N≈17k): the engine chases extended momentum.** Forward alpha
degrades monotonically with prior 20-day return; the top quintile (>+17.6% already) returns
**−4.35%**. It enters after the move is done.

**The critical caveat that prevents a naive fix:** `SELL`/`WAIT` taken as an *absolute* long is
only +0.37% / +0.18% alpha, t_day +0.96 — **not statistically significant**. The information is in
the **ranking**, not in a ready-made inverse strategy. Do not simply flip BUY and SELL.

### 3C.3 Hold period offers no rescue

```
1d −0.27% (ns) | 2d −0.73% | 3d −1.02% | 5d −1.57% | 6d −1.82% | 10d −3.07% | 20d −5.21%
```

Monotonically worse with every additional day. At 1 day it is indistinguishable from zero;
negative thereafter. **There is no holding period at which these signals are right** — the 6-day
evaluation horizon is not the problem.

---

## 3D. Exit structure and position sizing

### 3D.1 A correction to the "61 stop-outs vs 7 targets" premise

`stop_hit` is a fall-through label, not 61 initial-stop breaches. Of the 61: only **32 exited below
their original `stop_loss`**, and **14 exited *above* entry price** (ratcheted stops filling below
entry). GROWTH `stop_hit` median return is −0.10% — breakeven ratchets, not loss-cuts.

### 3D.2 Stops are NOT firing on noise

For all 61 stopped trades, measured forward from the stop:

| window after stop | recovered to breakeven | reached original target | return if held |
|---|---|---|---|
| remaining hold (~27.9 bars) | 70.0% | 13.3% | **−9.62%** |
| +10 bars | 70.5% | 11.5% | −5.74% |
| +20 bars | 72.1% | 18.0% | −7.29% |

70% *touch* breakeven again, which superficially looks like noise-stopping — but holding through
returns **−9.6%**. The stops are firing on genuine downtrends that happen to be volatile.
**Widening stops is the wrong fix**, and a 240-configuration sweep confirms it: wider stops
performed *worse*, and **all 240 configurations were negative**.

### 3D.3 But GROWTH's config sits in the worst cell of the entire grid

Parameters in force (`paper_trading_engine.py:904-912`, `_build_game_plan_for_style` L2851-2882):
SWING `atr_mult 2.0 / stop_pct 0.945 / tp 1.12 / max_hold 20`; GROWTH `3.0 / 0.880 / 1.35 /
max_hold 60`. Measured on live trades: SWING stop 5.3% / target 11.8%; **GROWTH stop 11.1% /
target 34.4%**. Median ATR is 4.42% of price, so **GROWTH's target sits ~7.8 ATR away —
effectively unreachable**, which is why only 7 trades ever reached one. `min_rr_ratio=2.0` (L781,
hardcoded) is satisfied by pushing the *target out*, not by tightening the stop.

Sweep over 4,667 point-in-time entries (Wilder ATR(14) from prior close only, stop probed before
target, 0.2% round-trip cost), mean % per trade:

| config | win% | mean | 1st half | 2nd half |
|---|---|---|---|---|
| Current SWING-like (1.25/2.75 ATR, 20d) | 29.4% | −2.30% | −2.14 | −2.47 |
| **Current GROWTH-like (2.5 ATR, unreachable tgt, 20d)** | 39.2% | **−3.42%** | −3.70 | −3.13 |
| **1.0 ATR stop / 1.0 ATR tgt / 3d** | 43.8% | **−0.82%** | −0.75 | −0.89 |
| Flat 1-day exit | 42.1% | −0.45% | −0.41 | −0.50 |

Paired difference (3d config vs current SWING-like) = **+1.48 pp/trade, t=11.98, n=4,667**, holding
in both sample halves, all four horizons, and the confidence≥60 subset. The surface is **smooth and
monotone**, not a spiky fitted optimum.

Two honest caveats: (a) trailing stops do **not** generalise — all 20 variants underperformed a
plain tight stop + tight target; the live +$442/trade on 13 `trailing_stop` exits is selection, as
trailing only fires on trades that already rose. (b) The simulator does **not** reproduce the live
engine (replaying the 123 live trades gives −203pp vs actual −53pp), so the sweep measures the
**entry population**, not an engine replay.

The breakeven ratchet is **helping** (+0.75 to +1.19 pp at 20-day holds) and should be kept.

### 3D.4 Position size is inversely correlated with outcome — the cheapest available win

Spearman(notional, pct_return) = **−0.253, p=0.005**; Spearman(confidence, notional) = **+0.224,
p=0.013**. Size follows confidence, and confidence is anti-predictive (§1c), so size is
anti-predictive. Independently verified:

| size quartile | n | avg notional | win% | total P&L |
|---|---|---|---|---|
| Q1 smallest | 31 | $2,819 | 41.9% | **+$1,507** |
| Q2 | 31 | $4,740 | 32.3% | +$459 |
| Q3 | 31 | $5,109 | 12.9% | **−$5,580** |
| Q4 largest | 30 | $18,705 | 43.3% | **−$4,608** |

**The two largest quartiles account for −$10,188 against a −$8,222 net loss; the two smallest were
profitable.** Flat position sizing alone, changing nothing else, would have removed most of the
loss.

---

## 4. A correction made during this audit

The first paper-trading P&L figure computed here used `paper_trades.realized_pnl` and produced
**+$7,164 with a 13.0% win rate** — a *positive* result. That was wrong. `realized_pnl` is
documented in `shared/db/models.py:1042` as **P&L from scale-out partial exits only**, "folded
into `pnl` at final close". The giveaway was `avg_loss = exactly $0` (scale-outs only happen on
winners) and `stop_hit` summing *positive*.

`pnl` is the authoritative total-P&L column, and it gives **−$8,222 / 32.5% win rate**.

Recorded because the error is an easy one to repeat, and because the two columns differ in
*sign*, not just magnitude.

---

## 5. Recommendations

Ordered by (evidence strength × cheapness). Items 1–4 are independent of any rebuild and can ship
without waiting on the hard problem in item 5.

### Tier 1 — cheap, well-evidenced, independent of the rebuild

1. **Flat position sizing** (§3D.4). The two largest size quartiles lost −$10,188 against a −$8,222
   net loss; the two smallest were profitable. Size currently follows confidence, and confidence is
   anti-predictive. This is the single cheapest win in this document and requires no model change.
2. **Stop using confidence to size or select** (§1c, §3C.2). Inverted monotonically on n=13,238,
   and inverted *within each market separately*. Recalibrate against realized outcomes. Separately
   fix the 118 rows with `confidence > 100`.
3. **Pull GROWTH's target in and cut its hold** (§3D.3): `max_hold_days` 60 → ~10 and
   `default_tp_pct` 1.35 → ~1.06 (≈1.5 ATR). Its current config is the worst cell of a 240-cell
   grid; the gradient is smooth and monotone in that direction across every subgroup and both
   sample halves. Worth ≈ +1.3–1.5 pp/trade on the sweep population. **Keep the breakeven ratchet**
   — it is measurably helping. **Do not widen stops** — that was tested and is worse.
4. **Flip or remove the hot-news sentiment sign** (§2d) and **instrument `hot_news_flag`** — add it
   to `SUPPRESSION_NAMED` in `analytics.py` plus a news outcome table matching the four that
   already exist. This gate has never been scored, which is how a backwards sign survived.

### Tier 2 — cost and hygiene

5. **Reorder `news_classify` to resolve symbol before calling Claude** (§2b) and **drop the EDGAR
   filing types that cannot produce signal** (§2c) — at minimum the fund-prospectus forms
   (497K/485BXT/497/485BPOS/497J, zero tracked symbols ever) and 424B2 (29,303 classifications → 1
   material flag). Trade-off: symbol-less rows currently populate the market-wide `/news` feed's
   sentiment labels, so decide explicitly whether to keep a display sample.
6. **Wire `/api/screener/analysts`** (§3A.2) — 100% ticker-tagged, structured, zero LLM cost,
   ~50 calls/day. Do **not** adopt UW's news endpoint. Scope any new UW polling against ~44k/day
   real headroom, not 110k (§3A.3).
7. **Suspend HK BUY entries** — −3.93% alpha, −10.44% at 20 days, inversion t = −4.65. Independent
   of any rebuild.

### Tier 3 — the actual problem

8. **[DOWNGRADED by the correction box at the top of this document.]** The rebuild framing below
   describes the **pooled May–September** population. On September data alone, BUY alpha is
   −0.32% (t = −0.99, not significant) and the inversion is no longer significant. **The revised
   priority is diagnostic, not reconstructive: find out what changed between June and September,
   confirm it is a system change rather than the 2026-09-02 measurement-regime change, and
   protect it.** Re-read the rest of this item only if October data reverts to the June–August
   pattern.

   *(Original text, still accurate for the pooled population:)*
   **The entry ranking must be rebuilt; filtering cannot save it** (§3C). Unfiltered −1.55% →
   fully filtered −1.11%; ~1,020 cells searched with no positive subset; no holding period from
   1–20 days is profitable. **But the inversion evidence says the raw features do contain signal**
   — a monotone −3.67 pp BUY/SELL spread is information, pointed the wrong way.
   **Next test, as a hypothesis not a strategy:** fit a model on `reasons` features to predict
   forward alpha, walk-forward by date. If sign-flipped confidence achieves out-of-sample
   AUC > 0.55, root-cause the polarity — check whether `calibrated_win_rate`, the `ml_weight` ramp,
   or the pillar gates enter with inverted sign. **Do not naively flip BUY/SELL**: SELL as an
   absolute long is only +0.37% alpha, t = +0.96, not significant.
   Also add an extension filter as a hard reject (prior-20d return > ~+15%): it won't create edge,
   but it removes the −4.35% worst quintile the engine currently chases.

9. **Decide what the HMM is for** (§3, §3B). Its states *do* separate forward returns, so it is
   salvageable — but refit on 15–20 years first (bear sample 11 → 588 days), persist outside
   `/tmp`, multi-restart EM, and backtest before trusting. **Do not flip the bear gate** — sizing
   down there is defensible as volatility targeting (2.5× vol), just not for the reason the code
   states. The unexploited opportunity is `neutral` (best Sharpe 0.265, shallowest worst case),
   where the platform currently does nothing.

---

## 6. Implementation progress (updated as work lands)

Redesign agreed with the user: Phase 0 (instrumentation) before Phase 1 (risk fixes), because
the reason a backwards news gate survived for months is that nothing scored it.

### Phase 0 — instrumentation

| # | Item | Status | Commit |
|---|---|---|---|
| 0a | Score `hot_news_flag` in `filter_audit` | **DONE, deployed** | `0d2ecc3` |
| 0b | Benchmark-relative (alpha) evaluation | **DONE, deployed** | `e55f216` |
| 0c | News outcome table | **DESCOPED — see below** | — |
| 0d | Durable, scheduled effectiveness tracking | **DONE, deployed** | `8a72385` |

**0d — built on what already existed, not a new table.** The `FixRecord`/`FixSnapshot` mechanism
(from the user's own 2026-09-02 request: *"a dashboard to show the performance after we applied
the fix… see if the fix really works"*) already had a register endpoint, a snapshot endpoint, a
daily scheduled recheck job on `recheck_after_days`, and a UI at `/fix-effectiveness`. The only
gap was WHAT it measured — absolute win rate and return, which §7.1 shows has no power.

Added `_compute_day_clustered_alpha()` and composed it **at the snapshot route**, not inside the
domain metric function, so it is domain-agnostic, leaves each metric function pure and
independently testable, and lands as a new top-level key that leaves the UI's existing
baseline/snapshot key-zip untouched on older records. Fail-soft: it reads the large `prices`
table, and an observability failure must never cost a snapshot its primary metrics.

Verified against real production data at deploy: **day-mean alpha −2.066%, sd 5.633, t_day −3.5,
n_days 91, n_signals 13,238**. That is slightly worse than the −1.967% computed earlier in §7.1,
and the difference is itself confirmation — §7.1 used SPY for every row, while this uses 2800.HK
for HK, and the HK investigation found SPY was *flattering* HK.

**Registered for tracking:** `AUD-REGIME-MARKETBLIND` (FixRecord #4), with the pre-registered
criteria from §7.2 stored in `success_criteria` so they cannot be rewritten later. First snapshot
correctly returned *"no resolved BUY outcomes with a matched benchmark window yet"* — the honest
not-yet-measurable state rather than a misleading zero.

**Deliberately NOT registered: `AUD-CONFSIZE-INVERTED`.** It changes position size, not signals,
so day-clustered signal alpha would measure nothing about it and tracking it here would be
misleading. Its validation is the trade-level counterfactual in §7.2, which is already complete
(**+$1,425**) because position size scales P&L linearly — no forward data required. Per §7.1,
forward trade-level validation of an $11.58/trade effect would need ~24,000 trades and is simply
not available at this data rate; saying so is more useful than manufacturing a metric for it.

**0a** — `hot_news_flag` was absent from both `SUPPRESSION_NAMED` and `SUPPRESSION_BOOLEAN`.
Now scored on `material_negative` only (`material_other` is logged-but-never-applied;
scoring it would dilute the effect). 7 tests, 2 sabotage cycles.

**0b** — `filter_audit` now reports `avg_alpha_pct` / `alpha_win_rate_pct` / `alpha_edge_pct` /
`alpha_verdict` alongside every absolute field, using **per-market benchmarks** (SPY / 2800.HK).
Implemented as a query-time computation against the existing `prices` table rather than a stored
column — this deliberately avoided a `shared/db/models.py` change and its all-12-backend rebuild,
at no loss of capability. 13 tests, 2 sabotage cycles.

*Cross-validation:* the live endpoint reports **−1.13%** alpha on 180d/6d SWING; an
independently-written standalone computation over the same population gave **−1.076%**. Two
implementations built separately agreeing is the strongest available check that the instrument
is right.

*A hole found in the tests themselves:* the first sabotage round pointed HK's benchmark at SPY —
the exact bug 0b exists to fix — and **all 12 tests passed**, because the test hardcoded the
benchmark map into its exec namespace and shadowed the source. Fixed to read the mapping from
source; the re-run then caught it (3 failures). Recorded because a test that cannot fail is the
same failure class as the unmeasured gate this whole audit is about.

**0c descoped:** its purpose was to make the news gate measurable, which 0a+0b now achieve at the
gate level. A per-event outcome table would add event-level granularity, but it needs a
`models.py` change plus an all-12-backend rebuild — disproportionate now that the measurement
question is answered. Revisit only if per-event attribution is actually needed.

### What the instrument found immediately

Re-running `filter_audit` on 180d/6d SWING with alpha verdicts (n=4,136):

| filter | n_active | alpha active | alpha inactive | alpha edge | verdict |
|---|---|---|---|---|---|
| **earnings_warning** | 149 | **+1.66%** | −1.24% | **+2.90** | harmful |
| insufficient_history_warning | 16 | +0.60% | −1.14% | +1.74 | harmful |
| weekly_alignment | 243 | +0.35% | −1.23% | +1.58 | harmful |
| high_vol_compression | 151 | −0.30% | −1.17% | +0.87 | harmful |
| options_flag | 466 | −0.75% | −1.18% | +0.43 | weak |
| news_sentiment_flag | 92 | −0.73% | −1.14% | +0.41 | weak |
| hot_news_flag | 27 | −0.73% | −1.14% | +0.41 | weak |
| weekly_gate_fired | 15 | −1.76% | −1.13% | −0.63 | predictive |

**`earnings_warning` is now the largest harmful filter** — and it is a *genuine* suppressor, not
an informational tag the tool mislabelled: verified at `signals.py:2290-2301`, where
`caution`/`note`/`watch` each apply `fused = 0.5 + (fused - 0.5) * adj_mult`. It suppresses a
cohort that earned **+1.66% alpha** against −1.24% for everything else, on n=149. This is
better-powered than the hot-news finding and was invisible before 0b.

Note also the refinement on `hot_news_flag`: **"harmful" on absolute win-rate but only "weak" on
alpha** (+0.41). The absolute view was partly measuring market drift. The n=977 event study still
supports the flip, but the gate-level evidence is weaker than the absolute numbers suggested —
which is exactly the kind of correction alpha-based evaluation exists to supply.

### Phase 1 — risk fixes

| # | Item | Status | Commit |
|---|---|---|---|
| 1a | Market-scoped regime (`AUD-REGIME-MARKETBLIND`) | **DONE, deployed** | `af74d29` |
| 1b | Confidence-decoupled position sizing (`AUD-CONFSIZE-INVERTED`) | **DONE, deployed** | `327eb47` |
| 1c | GROWTH exit config (target ~1.5 ATR, hold ~10) | **NOT DONE** | — |
| 1d | HK watchlist prune | **NOT DONE — user decision** | — |
| 1e | Pre-earnings compression A/B (`AUD-EARNCOMPRESS-PROXIMITY`) | **DONE, deployed** | `fad1ba7` |

**1a** — `_fetch_market_regime()` hardcoded `market="US"` for every stock; 94.5% of HK BUY
outcomes were tagged `bull` while HK was actually `choppy`. Now scoped by the stock's own
market. `fear_greed` deliberately left US-only (the endpoint takes no market parameter) and that
limitation is documented in the docstring and locked by a test rather than silently faked.
8 tests, 2 sabotage cycles.

**1b — shipped** (`327eb47`). `confidence_sizing_enabled` added to `_DEFAULT_CONFIG`, **default
False**. The band arithmetic is left intact and still evaluated so the US-floor boundary test
stays meaningful and re-enabling is a pure config flip, not a code restore. Only the multiplier
is neutralised. Historical counterfactual, computable because size scales P&L linearly:
**−$8,222 → −$6,798, a +$1,425 improvement (17% of the loss)**, expectancy −$66.85 → −$55.27.
Of 123 closed trades, **89 (72%) had been sized UP at 1.25×, and they carried $7,042 of the
loss**. 10 tests, 2 sabotage cycles.

**1e — shipped as an EXPERIMENT, not a fix** (`fad1ba7`). BUY signals by earnings proximity,
5-day forward return, all cohorts over the same window:

| cohort | n | 5d return | sd | Sharpe |
|---|---|---|---|---|
| **caution** (DTE 0–2) | 77 | **+1.72%** | 8.62 | **0.200** |
| short_imminent_event | 127 | +0.63% | 9.66 | 0.065 |
| note (DTE 3–5) | 43 | +0.32% | 9.34 | 0.034 |
| watch (DTE 6–10) | 104 | −0.14% | 4.79 | −0.029 |
| bull_beater | 3,351 | −0.79% | 6.57 | −0.120 |
| **(none)** | 9,575 | **−1.40%** | 7.96 | −0.176 |

Monotone in proximity; `caution` is best on **both** raw return and Sharpe — and compression is
tightest exactly there (`ec[2]` = 0.60–0.65, a 35–40% haircut toward 0.50).

**The confound that makes this an experiment rather than a conclusion:** those cohorts are
survivorship-filtered. A signal compressed by 0.60 that *still* cleared the BUY threshold was
stronger before compression, biasing the compressed buckets upward. No further querying of
existing data resolves that — only running with compression off and comparing does.

Two properties preserved deliberately, both sabotage-verified: **the cohort is still tagged**
when compression is skipped (`filter_audit` groups by that reason key, so hiding the tag would
make the A/B invisible), and **SA-25's SHORT DTE≤2 guard is not gated** — highest variance of any
cohort (sd 9.66), guarding a coin-flip binary event on a 5-day trade, which is risk control
rather than a return bet. The SA-7 `bull_beater` branch is likewise untouched; it already skips
compression.

**Sizing decision (1b), as originally recorded:** the user likes confidence-driven sizing as a
concept. Resolution —
keep it as the design, but **gate it on the polarity fix**: confidence is currently inverted, so
confidence-weighted sizing amplifies losses. Size on volatility until recalibration demonstrates
positive out-of-sample correlation, then re-enable confidence weighting behind a flag.

**HK (1d):** the root-cause investigation concluded HK is *not* a distinct problem — within-symbol,
US BUY signals underperform their own stocks by −5.07pp/14d versus HK's −5.67pp (t = 0.30, not
significant). The HK headline gap is ~all universe composition: the 42 tracked "HK" names are
~33 Technology, dominated by newly-listed China semis that fell 15–25%. Recommendation is to
**prune the watchlist, not suspend the market** — but the universe is the user's call.

---

### Follow-on work, same session

| Item | Status | Commit |
|---|---|---|
| Post-earnings backfill window parameterised (`AUD-PEARN-COVERAGE`) | **DONE, deployed + run** | `24d269d` |
| Earnings surprise impact by sector — backend (`AUD-EARNSURPRISE-SECTOR`) | **DONE, deployed** | `7f3a5ad` |
| Earnings Impact tab — frontend | **DONE, deployed** | `1a7be3d` |
| Test suite greened (4 red tests) | **DONE** | `6e8bc1c` |
| Reports/Event-Intelligence dedup (`AUD-REPORTSTAB-DEDUP`) | **DONE, deployed** | `12dc9f2` |

**The backfill.** `backfill_post_earnings_returns()` bounded its scan to 45 days, with a comment
calling older unbackfilled rows "a genuine, if rare, gap". Measured: **91% of the table** (74 of
809 populated) — the job only ever fills rows within 45 days of its own run, so everything that
reported before the job existed was never touched. Window is now a parameter (cron keeps its
45-day default); one historical run filled **555 rows**, taking coverage **9.1% → 77.8%**.

*A correction recorded against myself:* I first read `post_earnings_return_1d` as broken because
every bucket averaged ~0.03. It is a **fraction, not a percent** (max 0.3384 = +33.8%), so that
was +3%, not 0.03%. Read correctly it agrees with the 5-day figures.

**What the backfilled data says.** On the full dataset, a >10% EPS beat is followed by **+5.90%
over 5 days** (n=250, 61.2% up) vs **+0.65%** for in-line (n=264). But the tradeable figure is
smaller and the endpoint returns both: `post_earnings_return_5d` baselines off the last close
*before* the report, so it includes the overnight gap, which only a position held **through** the
report captures. Measured from the first close on/after the report — what a post-announcement
alert could actually catch — beats give **+4.19%** vs **−0.05%** in-line.

By sector, only **two clear the 30-beat sample floor**: Industrials (n=89, 50 beats, **+5.55%**
after open, 11.41pp spread) and Technology (n=293, 131 beats, +4.42% after open, but only a
2.50pp spread because tech drifts up on non-beats too at +3.07%). The floor is load-bearing —
`(unclassified)` shows a **+47.41pp spread on two beats** and Energy +8.56pp on two; both would
otherwise top the table. The UI segregates sub-floor rows under an explicit "not ranked and not
actionable" divider rather than letting them sort to the top.

**The suite greening.** market-data had 4 red tests, signal-engine's ratchet was red. A
chronically red suite is how a real regression hides (three incidents on that theme in
`ci-failure-masking.md`), so none of the session's work was safe while the baseline was red.
Three causes, none a code defect: one was **mine** (a source-text assertion pinning a number,
caught by this repo's own AUD-T401 ratchet — replaced with a value test, and verified by
sabotaging the source to `> 0.5 - 99999`, which leaves the substring intact so the old assertion
would have passed); two were tests anchored to the machine's **local** date while the code
correctly used `_today_et()`, which only agree when the developer's timezone matches US Eastern.

**An index that turned out not to be needed.** Two cohort-breakdown queries timed out, and the
obvious next step was an expression index on the `reasons` JSON extraction. Then a trivial
`pg_stat_activity` query also timed out — which no index explains. The real cause was I/O
starvation from a concurrent frontend build (§7.5). On a quiet instance the same query returned
in seconds. **No index was added**, and the non-finding is recorded here so it is not
re-attempted.

---

## 7. Measurement plan — how to tell whether any of this worked

Written BEFORE the results are known, so the success criteria cannot be rationalised afterwards.

### 7.1 The central finding: measure at the SIGNAL level, not the trade level

Power analysis on the real distributions:

| layer | unit | mean | sd | rate | to detect full closure to zero |
|---|---|---|---|---|---|
| Paper trades | P&L per trade | −$66.85 | $640.01 | 1.31/day | **~719 trades ≈ 550 trading days** |
| Signals (day-clustered) | day-mean alpha | −1.967% | 5.152% | 144 signals/day over 91 days | **~52 trading days** |

Trade-level P&L is the intuitive thing to watch and it is **the wrong instrument**: at 1.31
trades/day with a $640 standard deviation, detecting that expectancy reached breakeven takes
roughly **1.5 years**. Detecting the sizing fix's own +$11.58/trade effect would take ~24,000
trades — infeasible by two orders of magnitude.

Day-clustered signal alpha is ~10× more efficient. Clustering by day (not treating 144 same-day
signals as independent) is required — within-day signals are heavily correlated, and naive
per-signal t-statistics overstate significance badly.

**Detectable effect sizes at the day level** (80% power, α=0.05, n_days ≈ 208/d²):

| improvement to detect | trading days needed |
|---|---|
| 2.0 pp | ~52 (≈10 weeks) |
| 1.5 pp | ~92 |
| 1.0 pp | ~208 (≈10 months) |

So: closing the whole −1.97pp gap is detectable in about ten weeks. Anything subtler than ~1.5pp
is not practically measurable on this data rate, which is itself a design constraint — prefer
few large changes over many small ones, because small ones cannot be validated.

### 7.2 Pre-registered criteria, per change

| change | primary metric | success | failure | earliest read |
|---|---|---|---|---|
| **Confidence sizing off** (`327eb47`) | already measured counterfactually | — | — | **done: +$1,425 (17% of loss), expectancy −66.85 → −55.27** |
| | forward: no `Size 1.25×` notes on new entries | 0 occurrences | any occurrence | next entry |
| | forward: Spearman(notional, pct_return) | ≥ −0.10 | still ≤ −0.20 | ~60 trades |
| **HK regime scoped** (`af74d29`) | HK `market_regime` tag mix | `bull` share < 60% | still > 90% | ~10 trading days |
| | HK day-clustered alpha | improves ≥ 1.5pp | unchanged/worse | ~90 trading days (HK volume is lower) |
| **Gates instrumented** (`0d2ecc3`, `e55f216`) | `alpha_edge_pct` per filter, tracked over time | trend visible | — | continuous |

Note the asymmetry: the sizing change is **already validated** on historical data because position
size scales P&L linearly, so the counterfactual is computable. The HK regime change is **not**
counterfactually computable — it alters which signals fire and at what score — so it genuinely
requires forward data.

**Caveat on the sizing counterfactual:** it is first-order. Position size interacts with finite
capital (a smaller position leaves more for the next trade) and with `min_position_value` gates,
so the true effect is approximately, not exactly, +$1,425.

### 7.3 The queries

```bash
# 1. Counterfactual / actual sizing effect (re-runnable as trades accumulate)
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
WITH t AS (SELECT pnl, CASE WHEN entry_decision_notes::text LIKE '%Size 1.25%' THEN 1.25
  WHEN entry_decision_notes::text LIKE '%Size 0.75%' THEN 0.75 ELSE 1.00 END AS mult
  FROM paper_trades WHERE exit_time IS NOT NULL AND pnl IS NOT NULL)
SELECT count(*), round(sum(pnl)::numeric,0) actual, round(sum(pnl/mult)::numeric,0) flat FROM t;"

# 2. Day-clustered alpha (THE headline metric) — add a date filter to compare periods
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
WITH spy AS (SELECT p.ts::date d, p.close FROM prices p JOIN stocks s ON s.id=p.stock_id
  WHERE s.symbol='SPY' AND p.timeframe='D1' AND p.ts >= '2026-04-01'),
per_day AS (SELECT o.signal_date::date d, avg(o.pct_return-(se.close-sb.close)/sb.close) a
  FROM signal_outcomes o JOIN spy sb ON sb.d=o.entry_date::date
  JOIN spy se ON se.d=o.exit_date::date WHERE o.signal_direction='BUY' GROUP BY 1)
SELECT count(*) n_days, round((100*avg(a))::numeric,3) mean_alpha_pct,
       round((avg(a)/(stddev(a)/sqrt(count(*))))::numeric,2) t_day FROM per_day;"

# 3. HK regime tag mix — should shift away from 'bull'
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT s.market, o.market_regime, count(*) FROM signal_outcomes o JOIN stocks s ON s.id=o.stock_id
WHERE o.signal_direction='BUY' AND o.signal_date >= '2026-09-22' GROUP BY 1,2 ORDER BY 1,3 DESC;"

# 4. Filter verdicts on alpha (watch alpha_edge_pct move)
docker exec stockai-signal-engine-1 python3 -c "
import httpx; d=httpx.get('http://localhost:8005/signals/filter_audit',
  params={'lookback_days':180,'hold_days':6},timeout=240).json()
print(d['overall_avg_alpha_pct'], d['n_with_alpha'])
[print(f['filter'], f['alpha_edge_pct'], f['alpha_verdict']) for f in d['by_filter_name']]"
```

### 7.4 Check-back schedule

Two dated checkpoints, derived from §7.1's power analysis. Recorded here because the calendar
integration's auth token was expired when they were set — re-create them in Google Calendar when
it is re-authorised, but this file is the source of truth either way.

**2026-10-06 — "did the fixes take EFFECT?" (~10 trading days).** Deliberately not a performance
check; far too early for that. Three pass/fail checks:

| # | check | PASS | FAIL |
|---|---|---|---|
| 1 | HK `market_regime` tag mix | `bull` share < 60% | still > 90% → the fix never took effect |
| 2 | New entries carrying a `Size 1.25×` note | zero | any occurrence |
| 3 | FixRecord #4 accumulating snapshots | ≥1 real snapshot | none |

"No resolved BUY outcomes yet" is a fine result at this date. **Do not judge alpha.**

**2026-12-04 — "did the fixes WORK?" (~52 trading days).** The point a 2.0pp alpha shift becomes
detectable at 80% power.

- **Baseline to beat** (recorded 2026-09-22, stored in FixRecord #4's `success_criteria` so it
  cannot be rewritten afterwards): day-clustered BUY alpha **−2.066%**, sd 5.633, **t_day −3.5**,
  n_days 91, n_signals 13,238.
- **SUCCESS:** alpha improves ≥ 1.5pp. **FAILURE:** unchanged or worse with |t_day| ≥ 2 after 92+
  trading days. **|t_day| < 2 means NOT YET MEASURABLE — never read it as failure.**
- Also review: the `/fix-effectiveness` dashboard, `filter_audit`'s alpha verdicts (watch
  `earnings_warning` at +2.90pp harmful and `hot_news_flag` at +0.41 weak), and re-run the sizing
  counterfactual as trades accumulate.

**Parked for that date, deliberately:**
- `earnings_warning` polarity — NOT changed on 2026-09-22 because its own input (`market_regime`)
  changed the same day, and the pooled n=149 hides three warning levels across regimes and beat
  rates. Needs the per-cohort breakdown first (§7.5).
- The post-earnings drift signal — >10% EPS beat gives **+4.19% over 5 days after open** (n=250)
  versus −0.05% in-line; Industrials strongest at +5.55% (n=89, 50 beats).

### 7.5 An operational note: don't measure during a frontend build

Both attempts to break `earnings_warning` down by cohort timed out, and so did a trivial
`pg_stat_activity` query — while a `DOCKER_BUILDKIT=0 docker build` of the frontend was running on
the same instance. That is not a query-planning problem; it is the I/O starvation already
documented in `docs/incidents/ebs-io-credit-exhaustion.md`, where a frontend rebuild made the whole
instance unreachable for ~50 minutes. Diagnose from local files and wait for the build, rather than
adding load to a box that is already saturated.

### 7.6 What this implies for the remaining work

Running query 2 manually every few weeks is exactly the failure mode that let a backwards news
gate survive for months. **Phase 0d (scheduled `filter_audit` persistence) is therefore the
highest-value remaining item** — it converts this plan from "remember to check" into "it tells
you", and it is the only remaining Phase 0 item.

Second implication: because nothing below ~1.5pp is measurable at this data rate, **resist
shipping many small unvalidated changes**. Each one consumes the same ten-week measurement window
while making attribution harder. Prefer one substantial change per window.

---

## What to check if this looks wrong

```bash
# Re-run the platform's own filter effectiveness audit:
docker exec stockai-signal-engine-1 python3 -c "
import httpx; print(httpx.get('http://localhost:8005/signals/filter_audit',
  params={'lookback_days':180,'hold_days':6}, timeout=180).json()['overall_win_rate_pct'])"

# Authoritative paper-trading P&L (note: pnl, NOT realized_pnl):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT count(*), round((100.0*count(*) FILTER (WHERE pnl>0)/count(*))::numeric,1) win_pct,
       round(sum(pnl)::numeric,0) total FROM paper_trades
WHERE exit_time IS NOT NULL AND pnl IS NOT NULL;"

# Confidence inversion:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT width_bucket(confidence,0,100,10)*10 conf_upto, count(*) n,
  round((100*avg(pct_return))::numeric,2) avg_ret FROM signal_outcomes
WHERE signal_direction='BUY' GROUP BY 1 ORDER BY 1;"

# Live HMM state + how degenerate the posterior is:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from src.services.hmm_regime import predict_current; print(predict_current())"

# Classification waste by source:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "
SELECT source, count(*), count(*) FILTER (WHERE symbol IS NULL) unusable
FROM realtime_news_items GROUP BY 1 ORDER BY 2 DESC;"
```

If the hot-news finding (§2d) ever reverses, check first whether the Alpaca news population
changed composition — the result depends on Alpaca being the ticker-tagged source covering
tracked symbols, and it is the only source with 0% NULL-symbol rows.
