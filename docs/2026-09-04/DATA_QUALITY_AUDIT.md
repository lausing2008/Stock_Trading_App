# DATA QUALITY AUDIT — PHASE A

**Date:** 2026-09-04
**Scope:** Phase A of `AI Stock Trading Platform — Independent Trading Audit Prompt (REVISED 2026-09-04).md`
**Method:** parallel code-trace investigations across signal-engine, ml-prediction, decision-engine, market-data, and data providers — every finding below independently verified against the live repository and/or live production state before being recorded here. Nothing in this report is copied unverified from a subagent.

---

# EXECUTIVE SUMMARY

**This audit found the likely root cause of the confidence-inversion finding reported in
`SYSTEM_CAPABILITY_ASSESSMENT_2026-09-04.md`.**

A single defect — **every intraday price/TA feature is computed off a live, continuously-mutating "today" bar, not a settled close** — is not confined to one place. It was traced through three independent, functionally distinct pipelines:

1. **signal-engine** — the entire TA feature stack (RSI, MACD, SMA, VWAP, `last_price`) and every downstream confidence/fusion computation.
2. **decision-engine's own hard-reject gate (T196)** — ironically, a "hardening" fix that reintroduced the exact defect it was built to close.
3. **ml-prediction's live inference path** — while its own *training* pipeline was already, separately, correctly fixed against this exact defect class.

This is not an edge case. It is a **continuously active condition for the entire tracked universe, every ~5-minute refresh cycle, every trading day, for the full operational history of the platform.** If confidence/fused_prob partly reflects contaminated intraday features, that is a structural, sufficient explanation for why higher confidence predicts *worse* outcomes.

A second, independent, high-actionability finding: **Unusual Whales rate-limiting (22,031 events/48h) has a single, quantified, structural cause** — one uncached, 1-minute, effectively-unbounded function — not distributed noise.

A third: **Polygon and Alpha Vantage, despite looking like real fallback diversification in code, are dead code paths in the live deployment** — both API keys are blank in production, confirmed directly against the live EC2 `.env` and Redis (no admin-set runtime key either). yfinance is the *de facto* sole data source today, not one of three.

---

# PART 1 — POINT-IN-TIME CORRECTNESS (§A.2)

## 1.1 Root cause — the "today bar" problem

**Mechanism, confirmed via direct code read:**

`services/market-data/src/services/ingestion.py::ingest_symbol()` fetches yfinance `1d` bars with `end = date.today() + 1 day`, so **today's still-open trading session is included as a normal daily row.** `shared/db/models.py::Price` has **no `is_final`/`is_settled` column of any kind** — nothing in the schema distinguishes a genuinely closed EOD bar from one still being updated. This row is `ON CONFLICT DO UPDATE` upserted into the same `(stock_id, ts=today, timeframe=D1)` key roughly every 5 minutes throughout the trading day, so **today's "close" value keeps changing as the live price moves** — while its timestamp (`ts = today`) looks identical to a real, final close to every consumer.

| | |
|---|---|
| **SIGNAL_TIME** | Any of ~77 US refresh cycles/day (9:25am open burst → 5-min intervals → 4:15pm close burst) |
| **DATA_AVAILABLE_TIME** | Any ~5-min ingestion cycle, same trading day |
| **DATA_SOURCE** | `prices` table, `timeframe=D1`, via `GET /stocks/{symbol}/prices` |
| **DATA_TIMESTAMP** | Labeled `ts=today` — indistinguishable from a settled close |
| **EXECUTION_TIME** | Any consumer reading "today's daily bar" as if it were final |

## 1.2 Finding — signal-engine: the entire feature stack (CONFIRMED, CRITICAL)

**File:** `services/signal-engine/src/generators/signals.py`

`_fetch_prices()` reads the same contaminated `1d` endpoint with no session-aware filtering. Every TA feature in `_ta_score()` — SMA50/SMA200, RSI, MACD, Bollinger %B, VWAP, ADX, OBV, volume_z, Supertrend, stochastic RSI — reads `.iloc[-1]` off this same live-updating `df`, as does `reasons["last_price"]` (line ~2766).

**Compounding factor:** the signal's own `ts` is bumped to `NOW()` on every recompute (`services/signal-engine/src/api/routes.py`, `ON CONFLICT ... DO UPDATE SET ts = NOW()`), so a signal computed mid-day during a live move looks perfectly "fresh" to every staleness gate (`max_signal_age_hours`) — even though its inputs already silently encode that move. **This is the generalized version of the previously-confirmed SNOW gap-filter bug** — not limited to `last_price`, but contaminating BUY/SELL/HOLD calls, confidence, and every reasons field.

**Verified clean, for contrast** (confirms the codebase is capable of doing this correctly):
- `pullback_recovery()` (`signals.py`) explicitly slices `close.iloc[:-1]` before its rolling max, with its own comment: *"avoid look-ahead on today's bar."*
- `_sr_context()`'s 52-week high/low also explicitly excludes today's bar.
- Both are the exception, not the rule, in the same file.

**Confidence: CONFIRMED via full trace** (cron schedule → ingestion → DB row → HTTP endpoint → signal-engine consumption, no session-aware filtering at any hop).

## 1.3 Finding — decision-engine's T196 gate reintroduces the defect it was built to fix (CONFIRMED, CRITICAL)

**Files:** `services/market-data/src/services/paper_trading_engine.py` (`_scan_for_entries()`, `_sig_ref_prices` block) + `services/decision-engine/src/api/core/hard_rejects.py`

**Independently re-verified by me, exact code:**

```python
# paper_trading_engine.py — T196: Batch-fetch daily close at signal date
_ref_close = session.execute(
    select(Price.close)
    .where(
        Price.stock_id == _sk.id,
        Price.timeframe == TimeFrame.D1,
        func.date(Price.ts) <= _sig_date,
    )
    .order_by(Price.ts.desc())
    .limit(1)
).scalar()
```

No exclusion of a same-day, still-forming bar when `_sig_date == today` (the common case — most candidates are evaluated the same day their signal fired).

```python
# hard_rejects.py — the comparison this feeds
_ref_price = cfg.get("sig_ref_price")
_drift_pct = (live_price / float(_ref_price) - 1) * 100
if _drift_pct > _max_drift:
    return f"Price drifted {_drift_pct:.1f}% ... chasing blocked (T196)"
```

**The irony, confirmed by reading `hard_rejects.py`'s own comment verbatim:** T196 is explicitly framed as *more robust* than the earlier T171 gap-filter *because* it uses a "freshly re-derived daily-close reference price" instead of the "frozen `reasons['last_price']` snapshot." **That reasoning is backwards.** A frozen snapshot captured once at signal-generation time is *harder* to corrupt intraday than a value "freshly re-derived" from a table that is itself being live-mutated at query time. On any day a BUY candidate is evaluated the same day its signal fired, `_ref_close` and `live_price` can both be reading the same still-forming bar moments apart — collapsing the "3% drift / chasing" gate into a comparison of a moving target against itself.

**This is a gate specifically designed to catch price-chasing, built on the exact input contamination that makes chasing hardest to detect.**

**Confidence: CONFIRMED via direct code trace, independently re-verified** (I read both files myself, not just the subagent's citation).

## 1.4 Finding — ml-prediction: training hardened, live inference is not (CONFIRMED, CRITICAL)

**File:** `services/ml-prediction/src/training/trainer.py`

**Training is correctly fixed — independently re-verified by me:**

```python
# train_model(), line ~524
# Exclude any bar timestamped today — partially-observed intraday bars skew
# rolling features (SMA, ATR, z-scores) even though their label is dropped.
today = date.today()
df = df[pd.to_datetime(df["ts"]).dt.date < today].copy()
```

The identical pattern also appears in `validate_walkforward()` and `_load_outcome_features()` (tagged `T237-ML4`). All three carry a **general** comment about the defect class — not SNOW-specific, not narrowly scoped.

**Live inference is not — independently re-verified by me:**

```python
# predict_latest(), line 962 — no today-bar filter at all
df = _load_prices(symbol, lookback_days=400)
```

**This means the team already discovered and fixed this exact defect once — but only patched the path they happened to be looking at (training), leaving the higher-blast-radius path untouched.** `predict_latest()` runs continuously, on every symbol, all day, and its output (`ml_prob`) feeds directly into the same fused confidence score already shown to be inverted.

**Positive finding, also verified:** because `signal_date` values consumed by `meta_trainer.py` are sourced only from already-*closed* `signal_outcomes` rows, that specific path's exposure is a rare theoretical edge case, not an active daily contamination. The **stored historical training data itself is largely NOT contaminated** by this specific defect — but the **live decisions that generated those outcomes in the first place** were made against contaminated real-time inputs, for the entire history of intraday-generated signals.

**Confidence: CONFIRMED via direct code trace, independently re-verified** for both the fixed and unfixed sites.

## 1.5 Finding — a latent landmine in premarket digest code (SUSPECTED, MEDIUM)

`services/market-data/src/services/scheduler.py::_fetch_premarket_gappers()` computes "prior close" via `row_number() ... rn == 1` — but the pattern it claims to mirror (`routes.py::_latest_prices_from_db()`) correctly uses `rn == 2` specifically to skip a live same-day row when one exists. The comment claiming these match is inaccurate.

**Not currently exploitable**: this function's sole call site is a pre-market digest that runs before today's bar would exist in the table. It becomes live the moment this function is ever called intraday, or scheduling shifts. **Confidence: confirmed the off-by-one exists; unverified whether production scheduling ever overlaps market hours.**

## 1.6 Verified clean — alert paths using live_prices (POSITIVE CONTROL)

`check_volume_anomalies()`, `check_short_squeeze_alerts()`, `check_gamma_unwind_alerts()` all read `stockai:live_prices` — a Redis cache written by `routes.py::_fetch_live_bulk()`, which I independently confirmed does a genuinely separate, live 2-day `yf.download()` (`price = closes.iloc[-1]`, `prev_close = closes.iloc[-2]`), never touching the contaminated `prices` DB table at all. **This path is correctly insulated by construction, not by luck.** Confirms the codebase is capable of getting this right; the defect is inconsistent, not universal.

## 1.7 Scale of exposure

Not a handful of edge cases. Confirmed as a **systemic, continuously-active condition**:
- **Symbols:** effectively the entire active tracked universe, unscoped.
- **Cadence:** every ~5-min ingestion cycle, ~78 cycles/day for US equities, every trading day.
- **Consumers touched per cycle:** signal-engine's full recompute, decision-engine's T196 gate (fires on essentially every BUY-candidate evaluation during market hours), and any live `predict_latest()` call in the same window.
- **No regression test anywhere asserts "today's partial bar must be excluded"** — not for T196, not for the premarket-gapper function. `test_price_drift_config_wiring.py` tests only config-threshold wiring, never the underlying settlement correctness.
- **signal-engine itself has zero comments anywhere acknowledging this problem** (confirmed via grep) — in sharp contrast to ml-prediction's training pipeline, where it was clearly recognized once.

## 1.8 Recommended fix (not implemented — Phase A is diagnostic only)

The correct fix already exists in this codebase, at three sites (`train_model`, `validate_walkforward`, `_load_outcome_features`). The remediation is to **port the identical guard** to:
1. `predict_latest()` (ml-prediction) — highest priority, continuous live exposure.
2. `paper_trading_engine.py`'s `_sig_ref_prices` block (T196) — second priority, directly gates real trade entries.
3. `signal-engine`'s `_fetch_prices()` / `_ta_score()` — the deepest fix, requires either (a) a `bar_is_final` flag on `Price` rows so consumers can filter, or (b) `_fetch_prices()` itself only serving bars through the prior settled close during market hours, with a separately-labeled live-quote field for anything that genuinely needs the current price.
4. `_fetch_premarket_gappers()` — low priority given current non-exposure, but cheap to fix defensively (change `rn == 1` to `rn == 2`, matching the established correct pattern).

**This should not be implemented yet.** Per the audit's own operating rules (§G.3), Phase A is diagnostic. Confirm this is understood as the leading hypothesis, then proceed to Phase B (re-run confidence calibration) to test whether fixing this actually resolves the inversion before treating it as settled.

---

# PART 2 — DATA PROVIDER AUDIT (§A.3)

## 2.1 Unusual Whales rate-limiting — root cause confirmed, quantified (CONFIRMED, HIGH ACTIONABILITY)

**22,031 real 429 responses in 48h is structurally explained by one function**, not distributed noise across many callers.

**Mechanism, independently re-verified by me:**
- `check_options_flow_alerts()` registered on a genuine **1-minute** interval (`scheduler.py`, `id="options_flow_alert_check"`, confirmed directly).
- Each tick iterates `_bounded_options_flow_symbols()` — top-20 US symbols by K-Score **unioned with every distinct symbol any user has an active `PriceAlert` on**, uncapped on the PriceAlert side.
- For every symbol, calls `get_flow_alerts()`, which is **"Deliberately NOT cached"** — confirmed directly in `unusual_whales.py`'s own docstring.

**Quantified:** this one function alone makes `N × 1,440` real requests/day (N = bounded symbol-set size). At N=20 (the K-Score floor alone, zero PriceAlert symbols), that's **28,800 req/day** — ~96% of the platform's own assumed 30,000/day budget, from one function, before any other UW-calling code path adds anything. Any real PriceAlert usage pushes this over budget on its own.

**Contrast — a sibling job done correctly:** `check_dark_pool_alerts()` walks the same symbol set but calls `get_dark_pool_prints()`, which **is** cached 15 minutes — negligible incremental load. This is the template fix.

**Recommended remediation (not implemented):** give `get_flow_alerts()` a short (60–90s) cache — matching `get_nope()`'s own established precedent for "acceptably fresh for a per-minute UW read" — and/or hard-cap the bounded symbol union the same way `_OPTIONS_FLOW_TOP_K=20` already caps the K-Score side.

## 2.2 Polygon/Alpha Vantage — architecturally present, operationally dead (CONFIRMED, live-verified)

The adapter registry genuinely prioritizes them correctly (`_PRIORITY = ["polygon", "alpha_vantage", "yfinance"]`), and both self-register at import — so code inspection alone suggests real diversification.

**I independently verified this is not the case in the live deployment:**
```
$ grep POLYGON_API_KEY .env .env.example .env.production.example    → all blank
$ ssh EC2, grep POLYGON_API_KEY .env                                  → blank
$ ssh EC2, redis-cli --scan '*polygon*' / '*alpha*'                  → no runtime key set
```

Both adapters raise `RuntimeError` immediately on a blank key, silently caught by ingestion's generic `except Exception`, falling through to yfinance every time. **yfinance is the de facto 100% primary data source in production today** — not one of three, despite the code's own framing.

**Recommended action (not implemented):** either activate a real Polygon key (even a low tier meaningfully diversifies the single point of failure), or explicitly document that yfinance is the sole live source, since the current state silently contradicts the codebase's own architecture comments.

## 2.3 Analyst-action feed — real gap, does not currently gate a decision (CONFIRMED, LOW urgency)

`analyst_actions` (individual rating changes) is 100% yfinance-sourced with no disclosure that this feed lags dedicated services (Finnhub/Benzinga typically post within minutes; yfinance's scrape is commonly hours-to-a-day behind). **However**, independently traced: the actual trading gate in `research-engine/scoring.py` uses the aggregate mean consensus target, not individual timestamped action rows — so this lag affects display/historical-accuracy scoring, not a live trading decision. **No new provider justified on this basis**, per the audit's own constraint against recommending paid sources without a specific, cited gap.

## 2.4 Earnings-timing gap — already found and already fixed (CONFIRMED, no action needed)

`check_early_earnings_news_alerts()` contains a dated admission that yfinance can lag real earnings announcements by hours — already closed via a separate real-time news-sourced early-warning path (PR Newswire/BusinessWire/SEC EDGAR/Alpaca). Cited for completeness only.

## 2.5 New provider recommendation

**No new paid data source is recommended.** Every candidate (SEC EDGAR, FRED — both already integrated; FMP/Finnhub/Benzinga — no gate-level gap found; Estimize — no consuming feature exists) was checked against a specific, traced gap and none qualified under the audit's own "cite a specific degraded decision path" constraint.

---

# PART 3 — RANKED FINDINGS

| # | Finding | Severity | Confidence | Action |
|---|---|---|---|---|
| 1 | Live "today" bar contaminates signal-engine's entire feature stack | **Critical** | Confirmed, re-verified | Leading hypothesis for confidence inversion — test in Phase B |
| 2 | Decision-engine T196 gate reintroduces the same defect it was built to fix | **Critical** | Confirmed, re-verified | Port ml-prediction's own existing fix pattern |
| 3 | ml-prediction `predict_latest()` lacks the today-bar guard `train_model()` already has | **Critical** | Confirmed, re-verified | Port the existing 1-line fix from `train_model()` |
| 4 | UW rate-limiting (22,031/48h) traced to one uncached 1-min function | High (actionable) | Confirmed, re-verified | Add 60-90s cache to `get_flow_alerts()`, cap symbol union |
| 5 | Polygon/Alpha Vantage keys blank in production — yfinance is sole live source | Medium | Confirmed, live-verified | Activate a real key or document the true state |
| 6 | `_fetch_premarket_gappers()` off-by-one, not currently exploitable | Medium (latent) | Confirmed code defect; live exposure unverified | Cheap defensive fix (`rn==1` → `rn==2`) |
| 7 | Analyst-action feed lag undisclosed | Low | Confirmed; blast radius limited | Docstring note only |
| 8 | `tuner.py`/`feature_ablation.py` missing explicit today-bar filter | Low (currently inert) | Confirmed absent; currently masked by label-NaN filtering | Add defensively to prevent future reactivation |

---

# PART 4 — WHAT THIS MEANS FOR THE REST OF THE AUDIT

Per the audit's own operating rules (§G.1: label `UNMEASURABLE` rather than substitute a plausible number; §H.1: complete each phase before the next):

**Do not yet conclude the confidence inversion is fully explained.** This is the leading, well-evidenced hypothesis — not a proven cause. **Phase B must specifically test it**: after conceptually removing same-day-computed signals from the confidence-calibration sample (or, once fixed, re-measuring post-fix), does the inversion persist, weaken, or disappear? That comparison is the actual test, and it has not yet been run.

**Recommended immediate next step:** proceed to Phase B (§B.3, confidence calibration) with this specific question added: *does the inversion hold when restricted to signals whose `ts` is NOT the same calendar day as their features' most recent bar (i.e., signals evaluated the day after generation, when the reference bar has genuinely settled)?* If the inversion weakens or vanishes in that subset, this finding is confirmed as the primary cause. If it persists identically, a second, independent cause exists and must be found separately.
