# Market & Stock Intelligence Engine — fit-gap analysis

Against `Improvements/Market Intelligence/Market and Stock Intelligence.md` (52 sections,
2,431 lines), audited 2026-10-07 against the running production system.

**Scope of this document.** The specification's §1 asks for this analysis and then says "begin
implementation". This document is the analysis and the architecture only. **No code in this
commit implements any gap below.** Every status is evidence-backed: a claim of IMPLEMENTED
names the file or the table and, where possible, the measured row count.

**Headline:** of the 40 capability sections assessed, **17 are implemented**, **17 are partial**
and **6 are not implemented** — 40, which reconciles. Separately, **5 data constraints** block
specific sections; they are *not* added to those totals, because a blocker is a property of an
input and cuts across several capabilities at once (the absent VIX series constrains §6 and §7;
the absent bar timeframes constrain §17, §18 and §19).

*(Corrected 2026-10-07 after review: the first version listed 17 sections under a "16 partial"
heading and folded the five blockers into the capability totals, double-counting them.)*
The platform is much further along than the spec assumes on provenance, options, earnings and
risk — and further behind on market-level macro/liquidity and on the two things the spec calls
"high-priority" and "especially important": estimate revisions (§14) and the memory/HBM module
(§11). Both are blocked on data, not on code.

---

## 1. Existing capabilities

| § | Capability | Status | Evidence |
|---|---|---|---|
| 3 | Data provenance | **IMPLEMENTED, and stronger than specified** | Every intelligence-report field carries a statement class (observed / calculated / interpreted / conditional / model output), and an empty field states why it is empty rather than rendering a dash. `intelligence_reports` are versioned immutable snapshots; `issuer_documents` holds source bytes and facts hashes separately. Quality & Value evaluations freeze their **input values** with a SHA-256 digest beside the references, so a later change to a source row is detectable rather than invisible |
| 12 | Company fundamentals | **IMPLEMENTED** | `financial_statements` 1,424 rows / 156 symbols; `fundamentals`, `fundamentals_snapshot`. Business-performance findings with growth, margin and cash generation, each carrying its counterevidence |
| 13 | Earnings intelligence | **IMPLEMENTED** | `earnings_events` with actual / estimate / surprise; pre- and post-earnings report types where the pre-report is **frozen before the release** and the post-report scores against it; coverage discovery finds quarters that were never ingested |
| 16 | Valuation (partial scope) | **IMPLEMENTED for the gates** | `issuer_assessments` carries versioned valuation assessments with scenarios, horizons, discounting, dilution treatment and sensitivity; `cape_readings` 84 rows |
| 20 | Options intelligence | **IMPLEMENTED** | Unusual Whales integration; `option_chain_history`, `gex_snapshots`, `options_flow_snapshots`, `dark_pool_prints`; IV / IV-rank with a real year of daily IV per request; call walls and dealer positioning |
| 21 | Short interest | **IMPLEMENTED** | `short_percent_of_float`, `short_ratio`, `squeeze_watches`, `squeeze_alert_outcomes`. Notably the platform has **measured** its squeeze alert as anti-predictive (13.3% win, n=15) rather than assuming it works |
| 23 | Catalyst calendar | **IMPLEMENTED** | `economic_events` 521 rows out to 2027-12-08; `fda_catalysts`, `political_events`, `catalyst_scores`; earnings calendar with consensus and beat-rate history |
| 27 | Trade setup engine | **IMPLEMENTED** | Direction screen (`direction_screen.py`, `/quality-value/setups`) classifying breakout / breakdown / watch / inside-range over 189 active listings; `sr_watches`, `trade_plans` |
| 28 | Position sizing | **IMPLEMENTED** | ATR-based sizing; `sizer.py` composes regime, breadth and VIX multipliers; concentration caps enforced by **atomic reservation under a row lock**, not a pre-loop snapshot |
| 29 | Stock vs options | **IMPLEMENTED** | Options Game Plan: all four legs plus collar and verticals, recommendation keyed to position then IV regime; `options_game_plan_snapshots`; a short's ROI denominator is capital, never premium |
| 30 | Portfolio correlation | **IMPLEMENTED** | Correlation-aware pre-entry scoring; `portfolio_risk_metrics`, `portfolio_exposure_reservations` |
| 36 | Historical snapshots | **IMPLEMENTED** | `signal_outcome_horizons` **24,128 rows across 4 horizons**; `signals`, `signal_outcomes`; every intelligence report versioned and immutable |
| 37 | Performance measurement | **IMPLEMENTED** | Win rate, alpha, expectancy measured per direction × horizon — and the platform has recorded that pooling BUY with SELL was wrong because they point opposite ways |
| 40 | Risk guardrails | **IMPLEMENTED** | Sector/concentration caps, min R:R, portfolio-level entry gates, daily-loss and drawdown controls |
| 43 | Caching / rate limiting | **IMPLEMENTED** | UW daily budget accounting; **LLM budget with atomic admission control** in one PostgreSQL authority — reserves an upper bound before the call rather than measuring after; provider-degradation control probe distinguishes an outage from an invalid symbol |
| 46 | Security | **IMPLEMENTED** | Secrets in EC2 env only; `.env` gitignored; no path from LLM output to a broker order |
| 47 | Trading safety architecture | **IMPLEMENTED** | Paper trading is the live path; the broker route is disabled and its blocker is recorded (`submit_pending` has no production caller) |

---

## 2. Weak capabilities — partially implemented

| § | Capability | What exists | What is missing |
|---|---|---|---|
| 4 | Market regime | A regime classifier (`BULL` / `NEUTRAL` / `CHOPPY` / `BEAR`) with market-data as the single source of truth after five independent copies were consolidated. Market Outlook reports carry 1/5/20/63-bar returns | **The spec's 5 states × 3 horizons, classified independently.** Today one label covers all horizons — which is precisely what §4 says not to do |
| 5 | Breadth | `/market_breadth` endpoint, a breadth adapter in reports, HK breadth | % above 20/50/200 DMA; 52-week high/low counts; RSP/SPY, QQEW/QQQ, IWM/SPY, SOXX/QQQ ratios; **divergence detection** |
| 6 | Volatility regime | VIX participates in position sizing (`vix_mult`) | **No VIX series is stored.** No VIX9D, VVIX, term structure or SKEW. No `LOW/NORMAL/ELEVATED/EXTREME` or `RISK_ON/RISK_OFF` state |
| 7 | Macro | `cross_asset_readings` (57 rows, current to 2026-10-06) carrying 2Y, 10Y, 2s10s, DXY, HY spread; `economic_events`; Fed Watch deriving FOMC odds from CBOT futures | CPI / PCE / GDP / unemployment / NFP / claims / ISM / retail sales as **series**. No `growth_trend`, `inflation_trend`, `fed_stance`, `macro_equity_effect` classification |
| 9 | Sector rotation | `sector_rotation_snapshots` (96 rows); sector leadership in Market Outlook | Leaders / improving / neutral / weakening / laggards ranking; `RISK_ON_ROTATION` vs `DEFENSIVE_ROTATION` label |
| 14 | **Estimate revisions** | `estimate_snapshots` 324 rows / 164 symbols, captured daily and append-only, under the metric names `analyst_target_price` and `forward_pe` | **Forward EPS and revenue consensus history.** The gap is the missing input, *not* a substitution — see the correction below |
| 17 | Technical engine | RSI, MACD, ADX, ATR, OBV, VWAP, volume profile, FVG, support/resistance, 52-week levels | **Only `M5` and `D1` bars are stored.** `W1` and `H1` exist in the `TimeFrame` enum but were never ingested, and 4H does not exist at all |
| 18 | Multi-timeframe | Independent horizon resolution for outcomes (5-day outcomes without waiting for 14–28 day primaries) | The spec's W/D/4H/1H composition into `WAIT_FOR_PULLBACK_CONFIRMATION` — blocked by the bar-timeframe gap above |
| 19 | Relative strength | Industry peer basket excluding the subject ("13 covered semiconductor peers"); RS vs benchmark in places | Systematic 5/20/63-day RS vs SPY, QQQ, sector ETF and peer basket with `OUTPERFORMING` × `IMPROVING` states |
| 22 | News intelligence | news-intelligence service; classification scope fixed (1,074 → 284 in-scope); enforced token budget; deferred-retry worker | The spec's 12 **categories**; impact duration; **"likely priced in?"**; comparing actual price reaction against expected |
| 24 | **Evidence engine** | `Finding` (headline / supports / contradicts / invalidated_by) and `issuer_assessments` where every claim carries its counterevidence and its source accession | The spec's **13 named buckets**, each with `direction`, `strength`, `confidence`, `evidence`, `contradictions`, `timestamp`. Today evidence is per-report and per-gate, not per-bucket |
| 25 | Confidence model | Confidence exists on signals | It is **measured meaningless** — bands are flat at 36.5–43.9% over n=19,256. No data-completeness or freshness-driven confidence |
| 26 | Scenario engine | Bear/base/bull with horizons, discounting and sensitivity for CRDO | A general engine: required conditions, catalysts, technical confirmation, invalidation, target zone |
| 31 | Stress testing | `stress_test_results` table exists — **1 row** | The named scenarios (QQQ −5/−10%, SOXX −10/−20%, VIX spike, 10Y +50bp, AI-capex slowdown, DRAM deterioration) |
| 33 | Dashboard | Many dashboards: Market Pulse, Intelligence Reports, Quality & Value, Options Flow, Events | A single Market Intelligence dashboard and a Stock Intelligence card layout per §33 |
| 35 | Alert engine | Extensive alerting with per-type preferences, HMAC unsubscribe, a transactional outbox with `(user, symbol, episode, transition, channel)` identity | `INFO` / `WATCH` / `IMPORTANT` / `CRITICAL` severity; regime-change and RS-breakdown alert types |
| 38 | Walk-forward | `services/market-data/src/backtest/purged_walk_forward.py` exists; `backtests` 10 rows; look-ahead and fee/slippage tests with 8 engine sabotages caught | Train/validation/test split reporting with in-sample vs out-of-sample degradation |

---

## 3. Missing capabilities — not implemented

| § | Capability | Why it matters | Blocked? |
|---|---|---|---|
| 8 | **Liquidity engine** | Fed balance sheet, TGA, reverse repo, money supply, financial conditions | **Yes — no provider.** FRED would supply all of it |
| 10 | **Semiconductor subgroup taxonomy** | The spec insists semis are not homogeneous: AI accelerator / memory / foundry / networking / optical / equipment / legacy. The platform's peer basket is industry-level ("Semiconductors"), which pools NVDA with AMAT | No — this is a mapping table and is cheap |
| 11 | **Memory / HBM intelligence** | DRAM/NAND/HBM pricing, capacity, qualification, `MEMORY_CYCLE` state | **Yes — no provider for contract pricing.** The spec's own rule applies: "Never infer memory pricing without actual data" |
| 15 | **Company status engine** | Structured events (`CUSTOMER_WIN`, product launch, contract, insider) with importance and horizon | Partly — `insider_transactions` and `sec_filings` exist as raw inputs; the structured event layer does not |
| 39 | **AI learning loop** | Store predictions → analyse failures → propose change → backtest → validate out-of-sample → promote | No — the inputs exist (24,128 outcome rows, `tune_history`, model suppression). The loop does not |
| 42 | **`/api/intelligence/*` surface** | The spec's route names | No — existing routes are equivalent under different prefixes (`/intel/*`, `/quality-value/*`). This is a naming decision, not a gap |

---

## 4. Data gaps — what cannot currently be obtained

These are the real constraints. Five of them, and they govern what is worth building next.

1. **Historical analyst earnings estimates.** Measured 2026-10-07: `earnings_events.eps_estimate`
   holds 676 rows and **every one is for an event that has already reported**; all 129 scheduled
   events have none, and `revenue_estimate` is populated in 0 of 845. `fundamentals_snapshot.eps_estimate`
   is populated in **0 of 2,476 rows**. So the platform has no forward EPS or revenue consensus
   anywhere, and §14 — which the spec calls high-priority — cannot be built from current sources.
   **Required:** a provider with dated consensus history.

   **A correction to the first version of this document.** It stated that capturing analyst
   price targets *was* the substitution §14 forbids. That was wrong, and alleging a substitution
   requires naming a consumer that reads those metrics as EPS revisions. Checked, and there is
   none: the capture stores them under `analyst_target_price` and `forward_pe` with their own
   caveats; `earnings_revisions_driver()` still returns an explicit refusal naming what it would
   need; `forward_pe` is consumed only as a **valuation** input; and `EstimateSnapshot` has no
   consumer outside its own store. The gap is a missing input, and the capture is the right
   thing to be doing with the forward-looking data that does exist.
2. **Memory contract pricing (DRAM/NAND/HBM).** No source. TrendForce/DRAMeXchange are paid.
3. **Macro series.** `economic_events` carries release *dates*, not *values* or *consensus*.
   `macro_expectations` has **0 rows** — the capture exists and correctly refuses to record an
   expectation at or after publication, so nothing has been captured yet. **Required:** FRED
   (free) for values; a consensus source for expectations.
4. **Intraday and weekly bars.** Only `M5` and `D1` are stored. §17/§18 need 1H, 4H and W1.
   1H and W1 are derivable from existing data; 4H needs a resampling convention.
5. **VIX and the volatility complex.** No VIX series is stored at all, let alone VIX9D/VVIX/SKEW.

---

## 5. Technical debt relevant to this work

- **Five regime classifiers were consolidated to one**, but the surviving vocabulary
  (`BULL`/`NEUTRAL`/`CHOPPY`/`BEAR`) is not the spec's 5-state scale and is single-horizon.
  Changing it touches paper trading, which gates real decisions.
- **Confidence is measured meaningless** and still rendered. §25 would require either
  recalibrating it or removing it; leaving it is the worst option.
- **`fiscal_quarter` is derived from the calendar month** and is wrong for any non-calendar
  financial year. Already documented as never-match-on-it; §13 must not reintroduce it.
- **Statement retrieval is uniformly ~1 month old** (`max(fetched_at)` 2026-09-07) even where
  the reported year is current. §12 freshness policy needs a refresh job, not more fields.
- **`stress_test_results` has 1 row** — the table exists without the engine behind it.

---

## 6. What this platform already does that the specification does not ask for

Worth recording so it is not discarded in a rewrite:

- **Admission control for spend.** An atomic reservation of an upper bound before an LLM call,
  in a single PostgreSQL authority. The spec's §43 asks only for caching and rate limiting.
- **Frozen inputs with digests.** §36 asks to store inputs; this stores their *values* with a
  tamper-detectable digest, so premises moving on is distinguishable from the record being edited.
- **Claim-class permission.** An interpretation layer that refuses to emit a claim class it has
  no evidence standard for — enumerated, not a banned-word list.
- **Measured negative results.** Several shipped features have been measured and found
  anti-predictive or meaningless, and that is recorded rather than quietly dropped.

---

## 7. Status summary, in the format §52 requires

**IMPLEMENTED — 17:** §3, §12, §13, §16(gates), §20, §21, §23, §27, §28, §29, §30, §36, §37,
§40, §43, §46, §47

**PARTIALLY IMPLEMENTED — 17:** §4, §5, §6, §7, §9, §14, §17, §18, §19, §22, §24, §25, §26,
§31, §33, §35, §38

**NOT IMPLEMENTED — 6:** §8, §10, §11, §15, §39, §42

**Total 40.**

**DATA CONSTRAINTS — 5, counted separately.** Each cuts across several sections, so adding them
to the totals above would double-count:

| Constraint | Sections it blocks |
|---|---|
| No historical analyst **earnings** consensus | §14 |
| No memory contract pricing (DRAM/NAND/HBM) | §11 |
| No macro series *values* or pre-release consensus | §7, §8 |
| No 1H / 4H / W1 bars (only `M5`, `D1` stored) | §17, §18, §19 |
| No VIX complex stored | §6, §7 |

### What "implemented" does and does not assert

A populated table or a working adapter establishes **availability**, not conformance to the
section's full text. These are three separate questions and the first version of this document
collapsed them:

| Axis | Question | Example — §27 direction screen |
|---|---|---|
| **Implementation** | Does the code path exist and run? | Yes — `/quality-value/setups`, 189 listings scanned |
| **Coverage** | Over how much of the intended population, with what inputs? | US 147 + HK 42; daily completed sessions only; ETFs not yet excluded |
| **Software correctness** | Does the rule do what it says, on the paths exercised? | Partly — unit tests over the classifier and the adapter; **not** every path (gaps, halts, corporate actions, HK lunch break) |
| **Predictive performance** | Do its outputs lead anywhere useful? | **Unmeasured** — and a stored prediction would not by itself change that. Skill needs resolved observations, an appropriate benchmark and enough of them to separate a result from noise |

**The last two are different questions and an earlier version of this document ran them
together as "behavioural verification".** A correctly implemented rule can have poor investment
results, and an outcome study showing good results verifies neither the paths it never
exercised nor the cases it never encountered. They fail independently and are recorded
independently:

- §21 short interest has **predictive performance measured** — and the result was negative
  (squeeze alert 13.3% win, n=15, anti-predictive). That says nothing about whether its code is
  correct on every path.
- §38 walk-forward has **software correctness evidenced** — look-ahead and fee/slippage tests
  with 8 engine sabotages caught — and no out-of-sample performance reported.

Every row marked IMPLEMENTED should be read as *implementation* established, *coverage* stated
where known, and **both** verification axes absent unless named.
