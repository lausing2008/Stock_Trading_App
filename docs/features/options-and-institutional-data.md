## Feature Reference: T230-DATA-OPTIONS-CHAIN — Full Strike/Expiry Options Chain (Built 2026-07-22)

**Correction to this tracker item's original claim**: it said a full options chain "requires
Polygon.io options snapshots API (paid tier)." Checking the actual code found this false —
`GET /stocks/{symbol}/options-flow` (`services/market-data/src/api/routes.py`) already calls
yfinance's `t.option_chain(exp)` and fetches the FULL calls/puts DataFrames (strike, bid, ask,
last price, volume, open interest, implied volatility, in-the-money flag) for the nearest 4
expiries — then throws almost all of it away down to a top-3-per-side "unusual activity"
summary. No new or paid data source was needed; the data was already being fetched and
discarded.

**New endpoint**: `GET /stocks/{symbol}/options-chain?expiry=<date>` — a SECOND, independent
fetch (not a shared cache with `options-flow`, since a different `expiry` param means a
genuinely different yfinance call) returning every strike for ONE expiration, both sides,
unfiltered. Defaults to the nearest listed expiry when `expiry` is omitted; always returns
the full list of available expiries too, so the frontend can build an expiry picker without a
second round-trip. Redis-cached 15 min (`options_chain:{symbol}:{expiry}`), matching
`options-flow`'s own `_OPTIONS_TTL` cadence.

**New pure function**: `_options_chain_rows(df)` — flattens one side (calls or puts) of a
yfinance chain DataFrame into a plain list of dicts, sorted by strike ascending. Pulled out to
module level (not an inline closure inside the route handler) specifically so it's
independently unit-testable without a real yfinance/HTTP call — the only real logic in the
new endpoint worth testing directly. `df.fillna(0)` before conversion is load-bearing: a
thinly-traded contract's `NaN` bid/ask/volume would otherwise either crash the endpoint
(`ValueError: cannot convert float NaN to integer`) or — worse — leak a bare `NaN` into the
JSON response, which browser `JSON.parse` rejects, matching the exact `Infinity`-in-JSON bug
class already fixed once this session for `updown_vol_ratio`.

**Frontend**: `frontend/src/lib/api.ts` gained `getOptionsChain()` + `OptionsChain`/
`OptionsChainRow` types. `frontend/src/pages/stock/[symbol].tsx` gained a new, collapsed-by-
default "Options Chain" section directly below the existing Options Flow summary — opens on
click (the full chain is a heavier fetch than the flow summary's top-3-per-side rows, so it's
opt-in rather than always-fetched), with an expiry-picker row of buttons and a side-by-side
calls-left/strike-center/puts-right table matching the classic broker options-chain layout.
Strikes are merged from the union of calls' and puts' own strike sets (a strike missing on one
side, e.g. a call with no matching put row in a thin market, renders `—` on that side rather
than being silently dropped from the table).

**Tests**: `services/market-data/tests/test_options_chain.py` (8 cases) — `routes.py` can't be
imported directly in this test environment (its import chain needs `common.config`/`db`,
neither for-real-installed here), so `_options_chain_rows()`'s real source is extracted via
`exec()` and run against a REAL pandas DataFrame (not mocked), matching this repo's established
source-text-extraction technique. Covers strike-ascending sort, field mapping + IV-to-percent
conversion, `NaN`→`0` degradation (not a crash, not a JSON-breaking leaked `NaN`), empty-
DataFrame handling, and that `itm`/`volume`/`oi` are plain Python `bool`/`int` (not
`numpy.bool_`/`numpy.int64`, which `json.dumps()` also chokes on).

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: removing the
`sort_values("strike")` call (caught by the sort-order test); removing the `df.fillna(0)` call
(caught by the NaN test with a real `ValueError`, not a generic assertion failure). Full
444-test market-data suite (up from 436) and 89-test frontend vitest suite (unaffected) green;
frontend typecheck and a full `next build` both clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/AAPL/options-chain' | python3 -m json.tool | head -30
```
If a symbol with real listed options returns `available: false, reason: "fetch_error"`,
check `docker logs stockai-market-data-1 --since 10m | grep options_chain` for the underlying
yfinance error — the same rate-limit/fetch fragility documented elsewhere for options-flow
applies here too, since it's the identical underlying yfinance call.

---


## Feature Reference: TIER82-FMP-ANALYST-ESTIMATES — analyst_pt_upside ML Feature (Built 2026-08-18, No FMP Dependency)

**The original ask, and why it was closed differently than proposed**: this tracker item
proposed a Financial Modeling Prep (FMP) integration for analyst EPS-estimate revisions and
price targets — but no `FMP_API_KEY` exists in this environment, and both requested features
turned out to be derivable from data this app ALREADY had, closing the real gap at $0
marginal cost rather than waiting on a new vendor integration.

**`eps_revision_direction`** was reintroduced separately under `T237-ML2b` (see that entry) —
a rolling delta on the already-point-in-time-correct `recommendation_mean` snapshot history,
thresholded the same way `signals.py`'s own live `T220-F` signal does.

**`analyst_pt_upside` (this session)**: reuses yfinance's own `target_price` field, already
available on the SAME `upgrades_downgrades`/fundamentals fetch this app already makes for
every stock — no new external call, no new vendor.

**Implementation**:
1. `shared/db/models.py` — added `target_price: Mapped[float | None]` to both `Fundamental`
   AND `FundamentalsSnapshot` (an EXISTING-table column addition — 2 manual `ALTER TABLE`s
   run against production, per this repo's own `create_all()`-gap invariant; a brand-new
   table would need none, but these are both existing, already-populated tables).
2. `services/market-data/src/api/routes.py`'s `get_fundamentals()` — extended the
   `upgrades_downgrades` DataFrame capture to also read `currentPriceTarget`/
   `priorPriceTarget`, treating a literal `0.00` as `None` (yfinance's own "no target on this
   action" sentinel — a real, false-zero risk if left un-guarded). `target_price` added to
   both the `.values()` insert and the `on_conflict_do_update(set_=...)` update clause of the
   existing `Fundamental` upsert.
3. `services/market-data/src/services/scheduler.py`'s `_snapshot_fundamentals()` — the
   existing weekly job's raw SQL INSERT extended to also carry `target_price` through
   (both the column list and the `SELECT ... latest.target_price` clause, plus the `latest`
   subquery's own SELECT).
4. `services/ml-prediction/src/features/builder.py` — added `"analyst_pt_upside"` to
   `FUNDAMENTAL_COLUMNS`; new PIT-safe join block using `pd.merge_asof(direction="backward")`
   against the sorted `fund_snapshots` DataFrame — a training row can only ever see a snapshot
   at or before its OWN date, never a future one. `analyst_pt_upside = (target_price / close -
   1) * 100`, computed via a safe-placeholder-divisor pattern (`np.where(_valid, c.values,
   1.0)` before dividing) to avoid `np.where`'s eager-both-branches-evaluated
   `RuntimeWarning: divide by zero` even on rows the mask excludes. `NaN` when no snapshot
   exists yet or `close <= 0`.
5. `services/ml-prediction/src/training/trainer.py`'s `_load_fund_snapshots()` — extended to
   SELECT and return `target_price` alongside the fields it already fetched.

**A real test-fixture bug self-caught before shipping**: the first test fixture used a
perfectly flat `close` series (`flat_close=100.0` for every bar) — this produced ZERO
surviving rows in `build_features()`'s output regardless of window size, since a flat series
starves the required technical-indicator computations (RSI/ATR/etc. need real variance to
produce a non-degenerate value), which in turn starves the row mask entirely. Fixed by
renaming the fixture parameter to `pinned_end_close` — real random-walk noise throughout, but
shifted (`close = close - close[-1] + pinned_end_close`) so the FINAL bar lands on a known,
deterministic value for assertions, while every earlier bar has genuine variance.

**Tests**: `services/ml-prediction/tests/test_analyst_pt_upside.py` (new, 10 cases) — the
PIT-safe merge_asof join (a training row must never see a LATER snapshot's target price),
the `0.00`-sentinel-is-None treatment, the safe-divisor pattern's correctness at `close <= 0`,
and no-snapshot-available degrading to `NaN` rather than a crash.

**A SECOND, closely-related feature also shipped this same session using the same underlying
yfinance data, distinct from this ML-feature ask** — see the "wsz-analyst-accuracy-weighting"
section below: a new `AnalystPriceTarget` table tracking each FIRM's own individual calls and
whether they were later achieved, feeding a per-firm accuracy-WEIGHTED consensus shown on the
stock detail page. The two features share a data source (yfinance's `upgrades_downgrades`) but
serve genuinely different purposes — one is an ML training feature, the other a user-facing
consensus display — and were built as two independent pieces of work, not one shared
implementation.

**Deployed and live-verified.** Full ml-prediction test suite green at build time.

**What to check if this looks wrong**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d fundamentals" | grep target_price
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d fundamentals_snapshot" | grep target_price
docker exec stockai-ml-prediction-1 grep -n "analyst_pt_upside" /app/src/features/builder.py
```

---


## Feature Reference: wsz-analyst-accuracy-weighting — Per-Firm Analyst Accuracy Tracking + Weighted Consensus (Built 2026-08-18)

**The ask**: weight analyst price targets by each firm's own historical accuracy, rather than
treating every analyst's call as equally reliable in a simple mean.

**New `AnalystPriceTarget` model** (`shared/db/models.py`) — a brand-new table (so
`create_all()` handles it automatically, zero manual migration needed, unlike the
`target_price` COLUMN addition in the TIER82 section above): `(id, stock_id, symbol, firm,
grade_date, action, to_grade, current_price_target, prior_price_target,
outcome_evaluated_at, target_achieved, max_price_in_window, created_at)`, with
`UniqueConstraint("stock_id", "firm", "grade_date")` (one row per firm per rating action per
stock — a firm re-rating the same stock on a different day is a genuinely new row) and
`Index("ix_analyst_price_target_firm_evaluated", "firm", "outcome_evaluated_at")` (supports
the per-firm accuracy aggregation query efficiently).

**A real bug found and fixed live, mid-deployment**: `shared/db/__init__.py` re-exports
models by an EXPLICIT name list, not a wildcard import — `AnalystPriceTarget` was defined in
`models.py` but never added to either the import list or `__all__` in `__init__.py`. This
caused a real `ImportError: cannot import name 'AnalystPriceTarget' from 'db'` crash-loop on
`stockai-market-data-1` during EC2 deployment, caught live (not in local testing, since the
local test environment stubs `db` wholesale and never actually imports the real module this
way). Fixed by adding the name to both lists; committed as its own dedicated fix commit and
proactively synced to ALL 11 backend containers' `shared/db/` (not just market-data), per
this repo's own documented "shared/db/ staleness across containers" recurring-issue class.

**Ingestion**: `services/market-data/src/api/routes.py`'s `get_fundamentals()` extended to
also capture `currentPriceTarget`/`priorPriceTarget` from yfinance's `upgrades_downgrades`
DataFrame (the same fetch the TIER82 `analyst_pt_upside` feature reuses — see that section —
but persisted into a DIFFERENT table here, since this feature needs per-FIRM history, not a
single latest-consensus value). A literal `0.00` is treated as `None` (yfinance's own
"no target on this action" sentinel). New, INDEPENDENT persist block for `AnalystPriceTarget`
rows (its own try/except, isolated from the `Fundamental` persist path so a failure in one
never blocks the other), using `on_conflict_do_nothing(constraint="uq_analyst_price_target_
stock_firm_date")`.

**Scoring — `_evaluate_analyst_target_outcomes()`** (`services/market-data/src/services/
scheduler.py`, new daily job `analyst_target_outcomes_daily`, `CronTrigger(hour=6, minute=45,
timezone="UTC")`): scores an `AnalystPriceTarget` row once `grade_date <= today - 365 days`
(`_ANALYST_TARGET_OUTCOME_WINDOW_DAYS`) AND real `Price` rows exist for that window — scoring
BEFORE the window has fully elapsed would be a false negative (the target simply hasn't had
time to be hit yet), so the wait is load-bearing, not arbitrary. `target_achieved = (max_high
>= lo) or (min_low <= hi)` where `lo`/`hi` are ±10% (`_ANALYST_TARGET_TOLERANCE_PCT`) of
`current_price_target` — symmetric, so both an upside AND a downside target get a fair
evaluation. Bounded to `.limit(2000)` rows per run to keep the daily job's own cost bounded.

**Consensus — `_compute_weighted_analyst_consensus(session, symbol)`**: computes both a
simple mean AND an accuracy-weighted mean over the trailing `_ANALYST_CONSENSUS_LOOKBACK_DAYS`
(90) days of targets, deduped to the MOST RECENT target per firm (a firm's older, superseded
call shouldn't count alongside its newer one). Firm accuracy is computed ACROSS ALL symbols
that firm has ever covered (a firm's track record is a property of the firm, not of any one
stock) — `_ANALYST_ACCURACY_MIN_SAMPLES = 5`: a firm with fewer than 5 scored calls gets
EQUAL weight (1.0), not its own noisy raw accuracy, avoiding overfitting a firm's weight to a
tiny, statistically meaningless sample. New `GET /stocks/{symbol}/analyst-consensus` route.

**A real bug self-caught via `func` resolving to a stale MagicMock stub during test-writing**:
the test extraction technique initially did `from sqlalchemy import func as sa_func` INSIDE
the lazily-called extraction function — since this runs AFTER the test file's own stub-pop-
and-restore sequence, it grabbed the STILL-STUBBED `sqlalchemy.func` (a `MagicMock`), causing
a real `sqlalchemy.exc.ArgumentError`. Fixed by moving `func` into the SAME top-level `from
sqlalchemy import create_engine, func, select` line that runs BEFORE the stub restoration,
capturing the genuinely real object.

**"Still passes after sabotage" caught and fixed during adversarial verification**: the first
version of the min-samples-floor test used a "ThinFirm" fixture with 2 scored targets, BOTH
`target_achieved=True` (100% accuracy) — coincidentally IDENTICAL to the equal-weight
fallback value of `1.0`, so removing the min-samples floor produced no detectable change and
the test kept passing regardless of whether the guard existed. Fixed by changing the fixture
to 0% accuracy (both targets `target_achieved=False`), a value that genuinely differs from
`1.0` — re-verified the sabotage now correctly swings `weighted_mean` and is caught.

**Frontend**: new `AnalystConsensusPanel({ symbol })` component (self-contained `useSWR`
fetch) on the stock detail page, right after the existing "Recent Analyst Actions" block.

**Live-verified against real production data** (2026-08-18): `GET /stocks/AAPL/
analyst-consensus` returned 17 real firms (TD Cowen $400, Goldman Sachs $360, etc.),
`simple_mean: 327.04`, `weighted_mean: 327.04` (correctly equal, since no firm has scored
history yet — the daily evaluation job hasn't had 365 days to run against any of these rows).
Manually verified the scoring logic itself by inserting/deleting a synthetic old test row and
confirming `target_achieved=true` computed correctly against real AAPL price history (a real
max high of $258.226 vs. a $150 test target).

**What to check if this looks wrong**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "SELECT COUNT(*) FROM analyst_price_targets;"
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/AAPL/analyst-consensus'
docker logs stockai-market-data-1 --since 24h | grep analyst_target_outcomes
```

---


## Feature Reference: IF-04 Phase 1 — Cross-Asset Signals (Yield Curve + Credit Spread + Dollar Index) (Built 2026-08-19)

**Closes the first, cheapest slice of IF-04** (see the Tier 289 review above) — extends the
already-configured FRED sync in `event-intelligence` with real cross-asset macro data, rather
than building a new integration from scratch.

**What it is**: a new daily-synced `CrossAssetReading` table (one row per calendar day, all
continuous numeric fields — a genuinely different SHAPE from `EconomicEvent`'s row-per-release-
event structure) storing 5 FRED series: `DGS10`/`DGS2` (10Y/2Y treasury yields), `T10Y2Y` (the
2s10s spread — the standard yield-curve-inversion signal), `BAMLH0A0HYM2` (high-yield credit
OAS spread), `DTWEXBGS` (broad trade-weighted dollar index). All 5 series IDs were verified LIVE
against the real FRED API before being hardcoded (2026-08-19) — not guessed:
```
DGS10 4.72%  DGS2 4.19%  T10Y2Y +0.52%  BAMLH0A0HYM2 2.70%  DTWEXBGS 118.9
```

**How it works**: `sync_cross_asset()` (`services/event-intelligence/src/services/economic.py`)
reuses the exact same per-series-isolated fetch pattern `sync_fred()` already established — a
failure on one series never blocks the others. Each observation upserts into ONE row per
`as_of` date via `on_conflict_do_update(index_elements=["as_of"], set_={column: value})` —
critically, 5 separate series calls correctly accumulate into a single day's row (each updating
only its OWN column), rather than 5 series each creating/colliding on their own row. Scheduled
daily at 06:20 UTC (`job_sync_cross_asset`, right after the existing `sync_fred_release_dates`
job) plus a startup seed task, matching that job's own established convention.

`get_latest_cross_asset_reading()` adds a rule-based `RISK_ON`/`RISK_OFF`/`NEUTRAL` classification
— explicitly stated in its own docstring as unvalidated macro CONTEXT, not a trading signal
(no walk-forward backtest of these specific thresholds has been run), matching the same honesty
convention already established for CAPE and options-flow sentiment elsewhere in this app. A
2s10s inversion or a wide HY spread (>5%) scores toward RISK_OFF; a steep curve or tight spread
(<3.5%) scores toward RISK_ON; a genuine tie between the two reads NEUTRAL rather than silently
picking a side.

**API**: `GET /events/cross-asset` (the latest reading + classification), `POST /events/sync/
cross-asset` (manual trigger). **Frontend**: a new "🌐 CROSS-ASSET SIGNALS" card on
`intelligence.tsx`'s Overview tab, right after the existing Market Pulse card.

**Deliberately deferred, not built in this pass**: gold/oil (yfinance-sourced, not FRED) and VIX
term structure — adding them here would introduce a new cross-service dependency
(`event-intelligence` has no yfinance dependency today) into what was scoped as the cheapest,
FRED-only slice. A natural follow-on, not silently dropped.

**A real, self-caught test bug during development** (the exact "still passes after sabotage"
red flag this repo's testing discipline treats as a finding in its own right): the first test
run against a genuinely correct implementation still reported `synced: 0` — traced to the test's
OWN stub-pop-and-restore sequence importing `economic.py` AFTER restoring `sqlalchemy.dialects.
postgresql`'s stub, meaning `economic.py`'s own `from sqlalchemy.dialects.postgresql import
insert as pg_insert` silently bound to a `MagicMock`, not the real function — every upsert
silently no-op'd. Fixed by capturing the real `insert` function BEFORE the stub restore (as
`_real_pg_insert`) and patching `economic.pg_insert` with it directly in each test, alongside
`economic.CrossAssetReading` (same root cause — a module-level import resolved against the
wrong module state at exactly the moment `economic.py` itself was imported).

**A second, genuine "still passes after sabotage" finding, investigated rather than dismissed**:
removing the explicit `obs["value"] in (".", "")` skip-guard for FRED's own missing-observation
sentinel did NOT make the dedicated test fail — because `float(".")` already raises
`ValueError`, caught by the surrounding `try/except Exception: continue`, producing the
IDENTICAL final observable outcome either way. The guard is real defensive code (clearer
intent, avoids an internal exception) but not distinguishable from the crash-and-catch path at
the level this test checks — the test's own docstring was corrected to state this precisely
rather than over-claim what it proves.

**Tests**: `services/event-intelligence/tests/test_cross_asset.py` (14 cases) — the multi-series
upsert-into-one-row property, per-series failure isolation, the FRED "." sentinel never
fabricating a 0.0, and the full RISK_ON/RISK_OFF/NEUTRAL classification matrix (inverted+wide,
steep+tight, mixed-signals-read-neutral, a middling reading producing no direction bias, and
using the MOST RECENT `as_of` row, not the first inserted). 3 adversarial sabotage cycles, 2
caught correctly (the upsert conflict-action swap, the classification direction flip) and 1
correctly identified as testing a redundant-but-harmless guard (documented above). Full
270-test event-intelligence suite green; 132-test frontend suite + full `next build` green;
pyflakes clean on all touched files.

**What to check if this looks wrong**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT * FROM cross_asset_readings ORDER BY as_of DESC LIMIT 5;"
docker exec stockai-event-intelligence-1 curl -s 'http://localhost:8010/events/cross-asset' \
  -H "Authorization: Bearer <token>"
docker logs stockai-event-intelligence-1 --since 24h | grep 'sync_cross_asset\|cross_asset'
```

---


## Feature Reference: IF-05 Phase 1 — Real Max Pain Calculation (Built 2026-08-19)

**Closes the cheapest half of IF-05** (see the Tier 289 review above) — the deterministic
strike-minimization calculation, deliberately BEFORE true GEX (which needs a dealer-
positioning ASSUMPTION, not just a measurement, and was correctly scoped out of this pass).

**What it is**: `compute_max_pain(calls, puts)` (`services/market-data/src/api/routes.py`,
right next to `_options_chain_rows()` it reuses) — for every strike listed on EITHER side of
an options chain, computes the total intrinsic-value payout option HOLDERS would receive if
the underlying expired exactly at that strike:
```
call_value(S) = sum(call_OI[K] * max(0, S - K))   # ITM calls at hypothetical expiry price S
put_value(S)  = sum(put_OI[K]  * max(0, K - S))   # ITM puts  at hypothetical expiry price S
```
Max pain = the strike `S` minimizing `call_value(S) + put_value(S)` — the price point where
option WRITERS (typically viewed as "the market") owe the least, hence "pain" for holders at
any other expiry price.

**Needs only strike + open interest** — both already fetched by the existing `GET /{symbol}/
options-chain` endpoint (`T230-DATA-OPTIONS-CHAIN`) — zero new data source, zero implied
volatility, zero Black-Scholes, zero dealer-positioning assumption. Returns `None` (not a
fabricated strike) when an expiry has zero real open interest on either side — a genuinely
common case for a thin or newly-listed expiry.

**Genuinely different from, and complementary to, `check_gamma_unwind_alerts()`'s own OI-
concentration proxy** (`services/market-data/src/services/scheduler.py`) — that one flags a
lopsided position clustered near the current price as elevated hedge-unwind risk; this one
computes an actual expiry-day price target from the FULL open-interest distribution across
every strike.

**Frontend**: a new "Max Pain" + "Put/Call OI Ratio" readout on the stock detail page's
options-chain panel, right above the existing OI-by-strike bar chart — framed explicitly as
"measured from open interest alone — not a prediction of where price will land," matching this
app's established options/squeeze-alert honesty convention (the same discipline already
applied to the gamma-unwind alert's own "NOT a real GEX calculation" disclaimer).

**Tests**: `services/market-data/tests/test_max_pain.py` (8 cases) using the established
source-text-extraction technique (`routes.py` can't be imported directly in this test
environment) — including a fully hand-computed 3-strike chain (verified the exact arithmetic
BY HAND before trusting the test, not just checking directional behavior — a 3rd, middle
strike correctly wins on total payout, not just OI concentration) and a case confirming a
strike listed on only ONE side of the chain is still a real candidate (the union of both
sides' strikes, not just one side's). Adversarially verified 3 sabotage cycles, all caught and
reverted: swapping the call/put payout-direction formulas, restricting candidate strikes to
calls-only (losing the either-side-counts property), and removing the zero-OI guard (which
would have fabricated a strike from a chain with zero real interest).

**Deliberately deferred, not built in this pass**: true GEX (per-contract Black-Scholes gamma
× OI × 100 × spot², summed with a dealer net-positioning sign) — the missing piece is a real
ASSUMPTION about which side of every trade dealers are on, not a measurement this app can make,
matching this codebase's own established discipline of stating an unvalidated assumption
honestly rather than presenting it as a known fact.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/AAPL/options-chain' \
  | python3 -c "import sys, json; d = json.load(sys.stdin); print(d.get('max_pain'))"
```
If `max_pain` is always `null` for a symbol with real, actively-traded options, check the
`calls`/`puts` arrays' own `oi` fields first — a `null` max pain with real non-zero OI present
would be a genuine regression; a `null` alongside genuinely thin/zero OI on both sides is
correct, expected behavior.

---


## Feature Reference: IF-13 (partial) — Portfolio-Level Volatility-Targeting Size Multiplier (2026-08-19)

**Closes the volatility-targeting half of Tier 289's `IF-13-REGIME-AWARE-SIZING` finding** —
the Kelly-consumption half (`GET /paper-portfolio/kelly` is computed but never consumed by
real sizing) remains deliberately open, not attempted in the same pass.

**Confirmed as a genuinely distinct signal before building anything**: `regime_size_mult`
(`paper_trading_engine.py`) already stacks 4 dampeners via `min()` — base regime state,
pre-regime early warning, market breadth (IWM/MDY vs 200EMA), HMM bear pressure — plus a
continuous VIX gradient. None of these is THIS portfolio's own realized return volatility,
which can diverge from all of them: a concentrated, correlated book can realize far MORE
volatility than a calm VIX implies, or a well-diversified one can realize far LESS.

**Design risk flagged and resolved via `AskUserQuestion`** before writing code: stacking a 5th
correlated volatility dampener onto live trading capital is a real risk worth a deliberate
choice, not a default. Presented shadow/log-only vs. live/immediate vs. skip — user explicitly
chose **"Build it live, applied immediately."**

**Implementation**: new `_compute_portfolio_vol_targeting_mult(session, portfolio_id)` in
`paper_trading_engine.py`, placed right next to the sibling `_compute_portfolio_drawdown()`
(T286-DRAWDOWN-ALERT) it's structurally closest to. Reuses `_portfolio_risk_metrics()`'s own
established annualized-volatility formula (`paper_portfolio.py`) rather than re-deriving a
second, possibly-drifting one: daily returns from `PaperEquityCurve.equity` (ordered by date)
→ sample variance → `sqrt(252)` annualization. Reuses that same function's own
`_MIN_SHARPE_DAYS = 20` sample floor (renamed `_VOL_TARGET_MIN_SAMPLE_DAYS` locally) — fewer
than 20 equity-curve points fails open to a neutral `1.0`, since annualizing a shorter window
produces a meaningless estimate. `vol_mult = 0.15 (target annual vol) / realized_vol`, clamped
to `[0.5, 1.5]` — matches the design doc's own bounds exactly. A zero-variance (perfectly flat)
equity curve also fails open to `1.0` rather than a divide-by-zero crash.

**Composition — a genuine multiply, not a `min()`, and this distinction is load-bearing**:
`regime_size_mult`'s own pre-existing comment (`T234-PT-SIZING-MULT-STACK`) states its 4
internal dampeners compose via `min()` specifically to avoid multiple downward-pressure
signals compounding multiplicatively. But vol-targeting must be able to move sizing **UP**
when realized volatility is comfortably below target — a `min()` composition would silently
discard any upward adjustment, defeating half the feature. Applied as `regime_size_mult =
round(regime_size_mult * _vol_mult, 3)`, its own final step in the composition chain, right
after the VIX gradient block. Gated behind a new `vol_targeting_enabled` cfg flag (default
`True`). A neutral (`== 1.0`) multiplier is never logged, to avoid a log line on every scan
cycle for every portfolio when nothing changed.

**Tests**: `services/market-data/tests/test_vol_targeting.py` (11 cases), matching
`test_drawdown_alert.py`'s established real-sqlalchemy-via-stub-pop-and-restore technique
exactly (the sibling function it sits next to already uses this pattern) — a real in-memory
SQLite engine + the real `PaperEquityCurve` model, with the function itself extracted via
`exec()` from its own real source text (`paper_trading_engine.py`'s own module-level
`from sqlalchemy import ...` would otherwise resolve against conftest's stubbed `sqlalchemy` if
that module gets imported elsewhere in the same pytest session). Covers both fail-open paths
(sample floor, zero variance), both clamp directions (high realized vol → sizes down to the
0.5 floor; low realized vol → sizes up to the 1.5 ceiling), a near-target realized-vol case
landing near-neutral, per-portfolio isolation, and 3 source-text regression checks on the
`_scan_for_entries()` wiring (the multiply-not-`min()` property, the admin toggle gate, and the
neutral-multiplier log-suppression).

**A real, self-caught test gap during adversarial verification** (matching this repo's own
"still passes after sabotage is itself a finding" discipline): the first version of
`test_fewer_than_min_sample_days_returns_neutral_1_0` used a **constant** return sequence
(`[0.01] * 10`). Lowering the real sample floor from 20 to 2 (the sabotage) did NOT make this
test fail — because a constant return produces exactly zero variance, which trips the
*separate* zero-variance fail-open guard regardless of sample size, masking the sabotage
entirely. Investigated per this repo's standing discipline rather than shrugged off; fixed by
switching the fixture to genuinely-varying (non-constant) returns, which isolates the
sample-floor guard specifically — re-verified the sabotage is now correctly caught.

**Adversarial verification** — 3 sabotage/revert cycles, all caught and reverted (confirmed
byte-identical via `md5sum` before moving on): removing the `[0.5, 1.5]` clamp entirely (caught
by both clamp-direction tests, which would otherwise have seen an unbounded multiplier);
loosening the sample floor to `< 2` (caught by the corrected fixture above); and — the most
important one — swapping the composition from a genuine multiply to a `min()` (caught by the
dedicated multiply-not-`min()` wiring test, confirming this distinction is actually enforced by
a test, not just asserted in a comment).

Full 1810-test market-data suite green; `pyflakes` clean on both touched files (all 3
pre-existing warnings in `paper_trading_engine.py` confirmed via `git stash` to predate this
change — one warning's line number shifted by exactly the ~51 lines of new code added above it,
nothing new introduced).

**Not built this pass, documented not silently dropped**: the Kelly-consumption half of the
original IF-13 finding (`GET /paper-portfolio/kelly` computes quarter-Kelly + a
`recommended_risk_pct` but nothing consumes it for real sizing — it remains advisory-only,
matching the tracker's own original framing that this is a deliberate, separately-scoped
decision, not an oversight).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_compute_portfolio_vol_targeting_mult\|vol_targeting_enabled" /app/src/services/paper_trading_engine.py
docker logs stockai-market-data-1 --since 1h | grep 'paper.vol_targeting_size_adjusted'
```
A portfolio with fewer than 20 real `PaperEquityCurve` rows will always show a neutral `1.0`
(no log line at all) — this is correct, expected fail-open behavior, not a bug; check
`SELECT COUNT(*) FROM paper_equity_curve WHERE portfolio_id = <id>` directly before assuming
the multiplier itself is broken.

---


## Feature Reference: IF-06 (partial) — Size-Aware Entry/Exit Slippage (2026-08-19)

**Closes the size-aware-slippage half of Tier 289's `IF-06-SMART-ORDER-EXECUTION` finding** —
limit-order support and the full TWAP/VWAP/iceberg design remain deliberately unbuilt, matching
this item's own original scoping (execution algorithms aren't justified at current ~$100k
paper-portfolio sizes against liquid US large-caps).

**Found 5 real call sites, not the 3 originally cited**: a repo-wide grep for
`entry_slippage_pct` in `paper_trading_engine.py` turned up 5 places reading the flat 10bps
constant, not the 3 the original Tier 289 review cited — the real entry
(`_open_paper_trade()`), the final exit (`_monitor_positions()`), BOTH levels of T232-PT6's
two-tier partial scale-out, and T286-PYRAMID-TIERS' pyramid scale-in ADD path. All 5 now route
through a new pure function.

**`_size_aware_slippage_pct(shares, avg_daily_volume, base_slippage_pct)`**
(`paper_trading_engine.py`, placed right before the sibling pure function
`_slipped_position_value()` it's structurally closest to) — uses the standard simplified
square-root market-impact approximation: `base_slippage_pct * (1 + K * sqrt(participation_
rate))` where `participation_rate = shares / avg_daily_volume` and `K = 2.0`
(`_SIZE_AWARE_SLIPPAGE_IMPACT_K`). Impact growing with the SQUARE ROOT of size (not linearly)
is a well-established simplification reflecting that per-trade impact cost grows sub-linearly
with size. **Fails open to the unmodified `base_slippage_pct` — never lower** — whenever
`avg_daily_volume` is missing/non-positive or `shares` is non-positive, meaning the size-aware
model can only ever be as-or-more conservative than the flat constant it replaces, never less
conservative.

**`_avg_daily_volume_for(symbol)`** — reads the ALREADY-EXISTING `stockai:avg_volume` Redis
cache (`refresh_avg_volume_cache()` in `routes.py`, a real 20-day mean-share-volume-per-symbol
cache already refreshed 4-hourly for this app's RVOL/screener/volume-anomaly features) — no new
data source, no new ingestion job. Fails open to `None` on any Redis/parse error, matching
every other lookup in this file that reads this same key.

**Wiring — 5 call sites, each gated behind a new `size_aware_slippage_enabled` cfg flag
(default `True`)**:
1. **Entry** (`_open_paper_trade()`) — uses the real, already-computed `shares` for this
   candidate.
2. **Final exit** (`_monitor_positions()`) — uses `trade.shares` (the full remaining position
   being closed).
3/4. **Both partial scale-out levels** (T232-PT6, `_monitor_positions()`) — a real, deliberate
   distinction: each level's lookup uses THAT level's own tranche share count
   (`partial_shares`, 33% or 50% of the remaining position), **never** the full remaining
   `trade.shares` — a 33%-of-position sale must not be size-adjusted as if the whole position
   were being sold at once.
5. **Scale-in ADD** (T286-PYRAMID-TIERS, `_scan_for_entries()`) — the add-on share count is
   approximated pre-slippage (`_si_add_value / _si_live`), a small, explicitly-documented
   circularity (the exact add-on share count itself depends on slippage, which depends on
   shares) judged negligible at this scale rather than worth an iterative solve.

**Tests**: `services/market-data/tests/test_size_aware_slippage.py` (19 cases) — the pure
function's fail-open guards (missing/zero/negative `avg_daily_volume`, non-positive `shares`),
a hand-verified exact-formula check (not just directional behavior), monotonicity in
participation rate, a thinner stock producing higher slippage than a more liquid one for the
identical share count, the never-lower-than-base invariant, `_avg_daily_volume_for()`'s Redis
fail-open matrix (missing symbol, empty cache, connection error, malformed JSON — patched via
`sys.modules["common.redis_client"].get_redis` directly, matching this repo's own documented
gotcha that a freshly re-imported name against a `MagicMock`-stubbed parent package silently
misses the real call site), and source-text regression checks confirming all 5 sites are wired
AND correctly fall back to the plain flat constant when the toggle is off.

**Adversarial verification** — 4 sabotage/revert cycles, all caught and reverted (confirmed
byte-identical via `md5sum` before moving on):
1. Removing the fail-open guard entirely — caught with a REAL crash (`TypeError: type complex
   doesn't define __round__ method`), from taking the square root of a negative participation
   rate when `avg_daily_volume` is negative — not just a wrong value, a genuine exception.
2. Changing the impact exponent from `0.5` (sqrt) to `1.0` (linear) — caught by the dedicated
   hand-computed formula test.
3. Removing the `size_aware_slippage_enabled` toggle gate at the entry site — caught by 2 tests.
4. Swapping a partial scale-out level's tranche share count for the full `trade.shares` — the
   exact regression class the dedicated per-tranche test targets — caught correctly.

Full 1829-test market-data suite green (up from 1810); `pyflakes` clean on the touched file
(all 3 pre-existing warnings confirmed via `git stash` to predate this change — one warning's
line number shifted by exactly the ~54 lines of new code added above it).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_size_aware_slippage_pct\|_avg_daily_volume_for\|size_aware_slippage_enabled" /app/src/services/paper_trading_engine.py

# Spot-check the avg-volume cache and a real computed slippage value directly:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from src.services.paper_trading_engine import _avg_daily_volume_for, _size_aware_slippage_pct
adv = _avg_daily_volume_for('AAPL')
print('avg daily volume:', adv)
print('slippage for 1000 shares:', _size_aware_slippage_pct(1000, adv, 0.001))
"
```
If a real trade's slippage looks unexpectedly identical to the flat 10bps regardless of size,
first confirm `stockai:avg_volume` actually has a real entry for that symbol —
`docker exec stockai-redis-1 redis-cli get stockai:avg_volume | python3 -m json.tool | grep
<SYMBOL>` — a missing cache entry (not yet refreshed, or a symbol outside this app's tracked
universe) correctly falls back to the flat base, which is fail-open working as designed, not a
bug.

---


## Feature Reference: IF-01 (phases 1-3 + persistence) — Historical VaR/CVaR + Stress Testing (2026-08-19)

**Closes the "never persisted, stress testing entirely absent" half of Tier 289's `IF-01-
VAR-STRESS-TESTING` finding** — the ENTIRE pre-existing VaR implementation was 3 lines
(`port_vol = float(port_rets.std())`, `var_95_pct = port_vol * 1.645 * 100`), request-scoped
and discarded on every call, with zero stress-testing capability of any kind.

**Historical VaR/CVaR — `compute_var_cvar()`** (`services/portfolio-optimizer/src/api/risk.py`)
— computes EMPIRICAL-PERCENTILE VaR (not just the pre-existing parametric/normal-distribution
assumption) plus CVaR (Conditional VaR / Expected Shortfall — the average of the tail BEYOND
the VaR threshold, a materially more informative "how bad does it get" figure than a single
VaR point), at both 95%/99% confidence and 1-day/10-day horizons. **Kept ALONGSIDE the
pre-existing parametric `var_95_pct`, not replacing it** — a large divergence between the two
is itself a useful signal that the return distribution is meaningfully non-normal. 10-day
scaling uses the standard `sqrt(time)` convention (the same simplification this app's own
CAGR/Sharpe annualization already makes elsewhere). Fails safe to `None` (never a fabricated
number) below a 20-sample floor.

**5 predefined stress scenarios — `STRESS_SCENARIOS` + `run_stress_test()`** (same file) — 2008
GFC (-46%), COVID-19 (-34%), 2022 rate-hike selloff (-25%), 2010 flash crash (-9%), 1973-74
stagflation proxy (-48%), each a real, dated historical benchmark-index move. Per-position
impact = `beta * scenario_move` — an explicitly-stated **beta-scaled proxy**, not a claim that
a specific stock actually moved this way historically (a full historical replay would need
per-symbol price history reaching back to 2008, which this app doesn't have for most of its
tracked universe). New `GET /portfolio-risk/stress-test` and `GET /portfolio-risk/
stress-test/scenarios` endpoints; `portfolio_risk()` itself gained a `historical_var` field.

**Persistence — the other half of this item's headline finding, and the harder architectural
problem**: portfolio-optimizer has **no DB access of its own** (confirmed via its own module
docstring — a pure HTTP-consumer service). New `services/market-data/src/api/
risk_snapshots.py` calls portfolio-optimizer's risk endpoints over HTTP (the SAME established
cross-service compute-then-persist pattern already used for `OptionsFlowSnapshot`/
`SectorRotationSnapshot`) using a user's REAL `UserPosition` holdings (weighted by
`shares * avg_cost` — the same cost-basis convention `positions.py`'s own `PositionOut`
already surfaces), then writes into two new tables:
- `PortfolioRiskMetric` — one row per `(user_id, as_of)`, real upsert (running twice the same
  day updates in place, never duplicates).
- `StressTestResult` — one row per `(user_id, as_of, scenario)` — a user can run multiple
  scenarios against the same day's holdings.

Both are brand-new tables (`create_all()`-friendly — no manual `ALTER TABLE` needed, per this
repo's own standing `create_all()`-gap invariant). New endpoints: `POST /risk-snapshots/var`,
`GET /risk-snapshots/var/history`, `POST /risk-snapshots/stress-test`, `GET /risk-snapshots/
stress-test/history` — the last two finally answering the question this whole tracker item
existed to close: "what has my VaR actually looked like over time."

**Scoped PER USER, not per-`PaperPortfolio`** — deliberate: `portfolio.tsx`'s real call site
passes an arbitrary comma-separated symbol/weight list built from `UserPosition` (a user's own
manually-tracked holdings), not a `PaperPortfolio` (which already has its own, separate
Sharpe/Sortino/CAGR/max-drawdown risk metrics via `_portfolio_risk_metrics()` in
`paper_portfolio.py` — genuinely different figures from VaR/CVaR, not a duplicate).

### A real, unrelated bug found and fixed along the way

While wiring the new `risk-snapshots` proxy route into api-gateway's `_ROUTES` table, this
repo's own pre-existing `test_every_backend_router_prefix_has_a_gateway_route` test (built
specifically to catch exactly this class of bug) failed — not just for the new route, but for
**T286-CONDITIONAL-ORDER's own `/conditional-orders` router, which had NEVER been added to the
proxy table since that feature shipped**. Every real request to `/conditional-orders/...` had
been silently 404ing at the gateway (`_upstream()` has no default fallback) since that feature
was deployed. Confirmed via `git stash` on `proxy.py` that the test fails WITHOUT this fix and
passes WITH it. Fixed both gaps in the same edit — `"conditional-orders"` and
`"risk-snapshots"` both now map to `_settings.market_data_url`.

### A real, self-caught test gotcha (matching this repo's own documented Redis-connection-
### pooling gotcha, generalized to sqlalchemy itself)

`test_risk_snapshots.py` needs a real DB session (the established stub-pop/`create_all()`/
stub-restore technique), but a NAIVE version — re-importing `from sqlalchemy import select`
INSIDE a function called AFTER the stub restore — silently re-resolves to the STUBBED mock
again, not the real module, because `sys.modules["sqlalchemy"]` has already been restored to
the mock by that point. This produced a real `sqlalchemy.exc.ArgumentError: Executable SQL or
text() construct expected, got <MagicMock ...>` the first time these tests ran. Fixed by
capturing `select`/`Session`/`pg_insert` ONCE, at the top of the file, BEFORE the stub restore,
and reusing those captured references everywhere downstream instead of re-importing.

### Two more self-caught test gaps during adversarial verification, both fixed before shipping

1. `test_stress_test_endpoint_computes_a_real_result_end_to_end`'s first version reused an
   existing sibling test's fake benchmark fixture (`np.linspace(100, 110, 90)`, a deterministic
   price ramp) — this produces essentially ZERO return variance (`std ~3e-5`), sending
   `_beta()`'s `cov/var` computation into an absurd 650x-amplified beta against real noisy
   stock returns, and the test's own `assert result["portfolio_impact_pct"] < 0` failed with
   `650.47 < 0`. Not a bug in `run_stress_test()`/`_beta()` — real production benchmark data
   always has genuine variance. Fixed with a realistic noisy-benchmark fixture and a
   bounds-based assertion (the sign/scaling correctness is already covered by the dedicated
   hand-computed unit tests) rather than assuming a specific sign from random independent noise.
2. `test_cvar_is_at_least_as_severe_as_var_at_the_same_confidence_and_horizon` used a plain
   `>=` comparison — sabotaging CVaR to just re-report the VaR value (`cvar_1d = var_1d`
   instead of averaging the tail) still passed every test, since `cvar == var` still satisfies
   `>=`. Investigated per this repo's own "still passes after sabotage is itself a finding"
   discipline; fixed by adding a dedicated fat-tail fixture (a handful of much-worse-than-
   typical days) and asserting STRICT inequality (`>`) specifically for that case, while
   keeping a separate, weaker `>=` test for the genuinely-degenerate equal case.

**Tests**: `services/portfolio-optimizer/tests/test_var_stress_test.py` (27 cases — sample-
floor fail-open, the strict/weak CVaR-severity pair above, 99%>=95% ordering, sqrt-time 10-day
scaling hand-verified, all 5 scenarios present, hand-computed beta-scaling cases, missing-beta
fallback, endpoint-level wiring for both new routes) plus `services/market-data/tests/
test_risk_snapshots.py` (9 cases — symbol/weight building from real `UserPosition` rows,
zero-value-position exclusion, per-user isolation, the 2-symbol floor, real persisted rows,
and the same-day upsert-not-duplicate property). Adversarially verified 3 more sabotage/revert
cycles on `risk_snapshots.py` (the value-guard, the upsert conflict target, the 2-symbol
floor) and 3 on `risk.py` (the sample floor, the CVaR tail-mean, the unknown-scenario-key
guard) — all caught correctly and reverted, confirmed byte-identical via `md5sum` before
moving on.

Full 55-test portfolio-optimizer suite (up from 8), 1838-test market-data suite (up from
1829), and 40-test api-gateway suite (unchanged count, but now correctly passing the
route-registration test) all green; `pyflakes` clean on every touched file.

**Deliberately deferred, not silently dropped**: a scheduled DAILY post-close job (phase 4 —
snapshots are currently USER-TRIGGERED on-demand, not automatic) and a full frontend risk
dashboard (phase 5, showing the VaR/stress-test history charts these new endpoints now make
possible). Both are real, separately-scoped follow-ups once the calculation+persistence layer
itself has been live long enough to validate.

**What to check if this looks wrong**:
```bash
# Confirm the api-gateway route fix landed (both new AND the pre-existing conditional-orders gap):
docker exec stockai-api-gateway-1 grep -n '"conditional-orders"\|"risk-snapshots"' /app/src/api/proxy.py

# Live-check the new VaR/stress-test computation directly:
docker exec stockai-portfolio-optimizer-1 curl -s 'http://localhost:8007/portfolio-risk/risk?symbols=AAPL,MSFT'
docker exec stockai-portfolio-optimizer-1 curl -s 'http://localhost:8007/portfolio-risk/stress-test?symbols=AAPL,MSFT&scenario=covid_2020'

# Confirm a real snapshot persists (needs a real user JWT with >=2 real UserPosition rows):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT user_id, as_of, var_95_1d_pct, cvar_95_1d_pct FROM portfolio_risk_metrics ORDER BY as_of DESC LIMIT 10;"
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT user_id, as_of, scenario, portfolio_impact_pct FROM stress_test_results ORDER BY as_of DESC LIMIT 10;"
```

---


## Feature Reference: IF-02 (option a) — Entry-Lag Signal Age Decay (2026-08-19)

**Closes the day-grained interim half of Tier 289's `IF-02-ALPHA-DECAY-TRACKING` finding** —
the forward-looking hourly recording mechanism (option b, this item's own "real answer") is
deliberately deferred, matching the item's own explicit "(a) only as a clearly-labeled interim
read" framing.

**The name-collision this item's own review warned about, avoided by design**: the pre-existing
`GET /signals/alpha_decay` holds `entry_date`/`entry_price` FIXED and varies the EXIT day to
find the optimal hold period — it answers "how long should I hold?". This new endpoint,
deliberately named `GET /signals/signal_age_decay` (not a variant of "alpha_decay"), holds each
row's own already-resolved `pct_return` fixed and groups by **entry lag** —
`(entry_date - signal_date).days` — answering the genuinely inverse question: "how much edge
is lost by acting N days late?"

**Re-verified the feasibility constraint against CURRENT production data before building
anything** (not assumed from the original review): `lag=0` (8 rows), `lag=1` (6,263), `lag=2`
(135), `lag=3` (1,188), `lag=4` (396), `lag>=5` (3 total) — confirms the T+1 convention still
dominates and the day-grained (not hourly) scope is still the right call.

**Implementation** (`services/signal-engine/src/api/outcomes.py`) — genuinely SIMPLER to build
than `alpha_decay()`: no `Price` join needed at all, since `pct_return` is already stored
per-row. Buckets `[0, 1, 2, 3, 4]`, each needing `>= _SIGNAL_AGE_MIN_N = 5` resolved outcomes to
report a real average (matching `alpha_decay()`'s own established `AUD261-ALPHADECAY-
CHERRYPICKS-MAX` min-sample discipline in the same file). A negative lag (entry recorded before
the signal — a real, rare data anomaly) is explicitly excluded, never fabricated into a bucket.
A real `lag >= 5` row is counted in a separate `overflow_n` rather than silently dropped or
given its own misleadingly-precise bucket at that sample size. The response includes an
explicit `note` field stating the day-grained-only limitation AND the confound (entry lag is
not randomly assigned — it can correlate with WHY entry was delayed) directly in the API
response, not buried in a code comment, so any future consumer sees the caveat without reading
this tracker entry.

### A real regression caught in a SIBLING test, not this new one

`test_alpha_decay_no_profitable_hold.py`'s own `_extract_alpha_decay()` bounds its source-text
extraction with a hardcoded end-marker string
(`'\n@router.get("/information_coefficient")'`). Inserting the new `signal_age_decay()`
function BETWEEN `alpha_decay()` and `information_coefficient()` in the real file made that
hardcoded boundary silently swallow the new function's own `@router.get(...)` decorator too —
producing a real `NameError: name 'router' is not defined` in all 5 of that sibling test
file's tests once the new function was added. Caught by running the FULL signal-engine test
suite (not just the new test file) before considering this change done — exactly the
discipline this repo's own testing convention calls for. Fixed by updating the sibling test's
end-marker to the new function's own decorator string.

**Tests**: `services/signal-engine/tests/test_signal_age_decay.py` (7 cases) — entry-lag
grouping (proven distinct from a fixed-exit-day grouping), the min-n eligibility floor, the
negative-lag exclusion, the `overflow_n` bucket for real `lag>=5` rows, fastest/slowest-eligible-
lag selection, and BUY-only scoping (a `SELL` row must never leak into the curve).

**A real, self-caught "still passes after sabotage" gap** (matching this repo's own testing
discipline): the first version of the negative-lag test only asserted `lag0["n"]` stayed
unpolluted after removing the `if lag < 0: continue` guard — but this passed EVEN WITH the
guard removed, because a negative lag already fails the `lag in lag_returns` dict-membership
check (keys are `[0,1,2,3,4]`) and falls into `overflow_n` instead, never touching `lag0` at
all either way. Investigated per this repo's own standing discipline rather than accepted as a
coincidence; fixed by asserting `overflow_n` stays exactly `0` too — this correctly distinguishes
"genuinely excluded" from "accidentally miscounted as a real `lag>=5` row" — re-verified the
sabotage is now caught.

**Adversarial verification** — 3 sabotage/revert cycles on the new function itself (the
negative-lag guard, the min-n eligibility floor, the BUY-only direction filter), all caught
correctly and reverted, confirmed byte-identical via `md5sum` before moving on.

Full signal-engine suite green (340 passed, excluding the 2 pre-existing, unrelated failure
groups already documented elsewhere in this file — `test_signal_generator.py`'s `_decide`
import-collection error and 4 `test_analyst_momentum.py` failures, both reconfirmed via `git
stash` to predate this change). `pyflakes` clean (the sole warning, an unused `httpx` import,
confirmed pre-existing via `git stash`).

**Not built this pass, documented not silently dropped**: the forward-looking mechanism
(option b, this item's own "real answer") — recording intraday price at fixed hourly offsets
after each signal fires. A genuinely different, larger piece of work (new persistence, a
scheduled recording job, months to accumulate a usable sample) than the day-grained interim
read shipped here.

**What to check if this looks wrong**:
```bash
docker exec stockai-signal-engine-1 curl -s 'http://localhost:8005/signals/signal_age_decay?horizon=SWING' \
  -H "Authorization: Bearer <token>" | python3 -m json.tool
```
If every lag bucket shows `eligible: false`, check the real per-lag sample counts against
production directly before assuming the endpoint is broken — a young/thin data window can
legitimately fail to clear the 5-sample floor at some or all lags:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT (entry_date - signal_date) AS lag_days, COUNT(*) FROM signal_outcomes WHERE entry_date IS NOT NULL AND signal_direction = 'BUY' AND pct_return IS NOT NULL GROUP BY lag_days ORDER BY lag_days;"
```

---

