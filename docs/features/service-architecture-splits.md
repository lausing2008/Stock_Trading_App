## Feature Reference: T233-ARCH-PORTFOLIO-CONSOLIDATE — portfolio.py Moved to portfolio-optimizer (Built 2026-07-18)

**What moved**: market-data's `api/portfolio.py` (correlation matrix, beta, parametric VaR,
sector concentration — `GET /portfolio-risk/risk`) relocated verbatim to
`services/portfolio-optimizer/src/api/risk.py`. Same route path, same response shape — the
frontend (`board.tsx`, `portfolio.tsx`, both via `api.portfolioRisk()`) needed **zero changes**.

**This does NOT consolidate the two correlation implementations into one** — portfolio.py's
simple `df.corr()` and portfolio-optimizer's own `methods.py` (Ledoit-Wolf shrinkage
covariance, used by `/portfolio/optimize`) remain two separate implementations, just now both
living in the same service. Replacing one with the other is a separate, riskier numerical-
methods change deliberately not bundled into this architectural relocation.

**The real complication this move had to solve**: portfolio-optimizer has **no direct DB
access at all** (confirmed via grep — zero `from db import` anywhere in it; it's a pure
HTTP-consumer service). market-data's original `portfolio.py` queried `Price`/`Stock` directly
via SQLAlchemy. The moved version's `_fetch_returns()`/`_fetch_stock_meta()` instead call
market-data's own `GET /stocks/{symbol}/prices` and `GET /stocks/{symbol}` over HTTP — the
SAME two endpoints this service's pre-existing `_fetch_closes()` (in `routes.py`, backing
`/portfolio/optimize`) already relies on, so this isn't a new integration pattern for this
service, just reusing an existing one for a second endpoint.

**New runtime dependency**: `yfinance>=0.2.54` added to portfolio-optimizer's
`requirements.txt` (needed for the SPY/HSI benchmark-beta fetch, wasn't there before) — this
means the deploy needs a real image rebuild (`docker compose build portfolio-optimizer`), not
just a `docker cp` hotfix, per this repo's own "new dependency needs a real rebuild" rule
(same class of gap as the api-gateway numpy incident documented elsewhere in this file).

**Routing**: api-gateway's `proxy.py` route table's `"portfolio-risk"` entry repointed from
`market_data_url` to `portfolio_optimizer_url` — one line, since the path itself didn't change.
market-data's `main.py` had `portfolio_router` removed; the old file was deleted outright
(`git rm`), not deprecated in place.

**Tests**: `services/portfolio-optimizer/tests/test_portfolio_risk.py`, 8 cases, direct function
calls with `monkeypatch` on the module's own `_fetch_returns`/`_fetch_stock_meta`/`yf` —
matching this service's existing `test_optimize_endpoint.py` convention exactly (`fastapi`/
`httpx`/`pandas`/`numpy` are all real, installed packages in this test environment per
`conftest.py`'s own docstring, so no stub workaround was needed). Covers the 2/10-symbol
bounds, mismatched-weights rejection, insufficient-history `422`, full correlation/beta/
sector-weight computation, the HK-vs-US benchmark selection rule, high-correlation/
concentration warning triggers, and a graceful `beta=1.0` fallback when the yfinance benchmark
fetch itself fails. Adversarially verified the high-correlation warning check by disabling it
and confirming the dedicated test caught it before reverting.

**What to check if this looks wrong**:
```bash
# Confirm the route resolves to portfolio-optimizer, not a stale market-data instance:
docker exec stockai-api-gateway-1 python3 -c "
from src.api.proxy import _ROUTES
print(_ROUTES['portfolio-risk'])"

# Live check against a real deployed container:
docker exec stockai-portfolio-optimizer-1 curl -s \
  'http://localhost:8007/portfolio-risk/risk?symbols=AAPL,MSFT' -H "Authorization: Bearer <token>"
```

---


## Feature Reference: T233-ARCH-INSERVICE-SPLITS (research-engine half) — Scoring Functions Extracted to scoring.py (Built 2026-07-19)

**Gap this closes**: `services/research-engine/src/api/routes.py` had grown to 1,877 lines,
bundling report aggregation/orchestration (Claude calls, caching, route handlers) with three
independently-testable quant subsystems (technical scoring, fundamental scoring, DCF
valuation). `tests/test_scoring.py` already imported several of these functions directly with
zero FastAPI/network dependency, proving they were already decoupled in practice — just not in
file layout, making the file a review hazard (a change to Claude-prompt-building code sits in
the same diff/file as a change to DCF math, with no structural signal separating the two).

**What moved**: a verified, genuinely self-contained block — `_last`, `_second_last`, `_atr`,
`_institutional_ownership_pct`, `_fmt_cap`, `_score_technical` (+ its `_rsi_interp`/
`_macd_interp`/`_hist_interp` helpers), `_sector_bench`, `_score_fundamental`,
`_build_checklist`, `_position_sizing_matches`, `_position_size`, `_dcf_fair_value` — into a
new `services/research-engine/src/scoring.py`. Verified before moving that this block has zero
`httpx`/`log`/`async`/network dependency (a plain `grep` across the extracted range came back
empty) — confirming it's pure computation, not orchestration wearing a scoring-sounding name.
`_call_claude()` and `_fallback_ai()` — which sit immediately after this block in the original
file and DO make a real `httpx.AsyncClient` call — were deliberately NOT moved; they're
orchestration, not scoring, despite living in the same neighborhood of the original file.

**`routes.py` re-imports all 15 names from `..scoring`**, so every existing `from
src.api.routes import X` call site — both the real route handlers below and every test file in
`tests/` — keeps working completely unchanged. This was a deliberate choice: an alternative
("update every test file's import path to `from src.scoring import X`") would have touched 4
test files for zero behavioral benefit, just to avoid one import-forwarding block in `routes.py`.

**Result**: `routes.py` went from 1,877 → 1,018 lines; `scoring.py` is a new, self-contained
893-line module with no FastAPI/network/logging dependency at all — genuinely independently
testable and reviewable now, not just in principle.

**signal-engine's half of this same tracker item was deliberately NOT done this session** —
`services/signal-engine/src/api/routes.py` is 6,190 lines across 34 routes (grown from the
tracker's stale 4,805-line citation), and is the single most safety-critical service in this
app (live signal generation, self-tuning, backtesting). A split there is a materially larger
and riskier undertaking than research-engine's clean, already-isolated 15-function extraction
— it doesn't fit the "about as safe as a refactor gets" framing the tracker's own impact note
uses for the pair. Left as its own separately-scoped follow-up (enumerate the 24 self-tuning/
analytics routes vs. 8 hot-path routes by `@router` decorator, split into `outcomes.py` +
`calibration.py`) rather than rushed into the same session as the low-risk half.

**Verification performed**:
1. **Zero test regression**: ran `python3 -m pytest tests/` on research-engine both before and
   after the split (via `git stash`/`git stash pop` to compare the exact same test run against
   the unmodified file) — identical result both times: 53 passed, 3 failed. The 3 failures
   (`test_fundamental_empty_returns_neutral_50` and two balance-sheet assessment tests) are a
   **real, pre-existing bug** unrelated to this split — confirmed by reproducing them on the
   completely unmodified original `routes.py` — left uninvestigated as genuinely out of scope
   for a pure file-layout task (a fix would need to determine whether `_score_fundamental`'s
   empty-input early return or the test fixtures themselves are wrong, a separate decision).
2. **Import chain verified directly**: `from src.api.routes import router, _score_technical,
   _score_fundamental, _build_checklist, _dcf_fair_value, _position_size,
   _position_sizing_matches, _atr, _last, _second_last, _institutional_ownership_pct,
   _fmt_cap` — all 15 re-exported names resolve correctly under the same stubbed test harness
   `main.py` itself would use in production (conftest.py's `pydantic`/`fastapi`-as-MagicMock
   stubbing), not just "the file parses."

**What to check if this looks wrong**: if a route handler in `routes.py` throws
`NameError`/`ImportError` on one of the 15 moved names, check the `from ..scoring import (...)`
block near the top of `routes.py` first — it's the only place those names re-enter this
file's namespace. If a scoring function itself looks wrong, it lives in `scoring.py` now, not
`routes.py` — the extraction was verbatim (no logic changes), so a bug found there was
already present before this split, not introduced by it.

```bash
# Confirm the split is live and both files parse in production:
docker exec stockai-research-engine-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.api.routes import router, _score_technical, _dcf_fair_value
print('routes.py + scoring.py import chain OK')
"
docker exec stockai-research-engine-1 wc -l /app/src/api/routes.py /app/src/scoring.py
```

---


## Feature Reference: T233-ARCH-INSERVICE-SPLITS (signal-engine half) — routes.py Split Into 4 Files (Built 2026-07-22)

**Gap this closes**: `services/signal-engine/src/api/routes.py` had grown to 6,289 lines / 35
routes — the single most safety-critical service in this app (live BUY/SELL signal
generation), bundling three structurally distinct concerns in one file: hot-path signal
reads/writes (what real trading traffic depends on every few minutes), self-tuning/
calibration mechanisms (weekly jobs), and analytics/backtest/outcome-evaluation (on-demand
reports). This mirrors the research-engine half of the same tracker item (`scoring.py`
extraction, done 2026-07-19) but at a materially larger scale — signal-engine's file was
~5x research-engine's original size, with real cross-cutting shared state, which is exactly
why this half was deliberately deferred to its own separately-scoped session rather than
rushed into the same pass as the low-risk research-engine half.

**What moved (verbatim — verified byte-identical, see below)**:
- `services/signal-engine/src/api/signals_shared.py` (new, 329 lines) — helpers called from
  MORE than one of the three route files: Redis cache helpers (`_get_redis`/`_cache_get`/
  `_cache_set`/`_redis_get_float`), the service-to-service JWT (`_service_token`), the
  `TuneHistory` recorder (`_record_tune_history`), the confidence-calibration read path
  (`_cal_bucket_key`/`_build_confidence_calibration`/`_get_confidence_calibration`/
  `_calibrated_win_rate`, used by BOTH live signal reads in routes.py and the calibration map
  endpoint in calibration.py), `_compute_stability`/`_stored_signal_for_style`, and the
  outcome-window/hurdle constants (`_OUTCOME_HOLD_DAYS`/`_SELL_OUTCOME_HOLD_DAYS`/
  `_OUTCOME_CENSOR_GRACE_DAYS`/`_OUTCOME_WIN_HURDLE_PCT`, used by BOTH calibration sweeps and
  the outcomes evaluator).
- `services/signal-engine/src/api/routes.py` (trimmed, 1,192 lines) — 9 hot-path routes:
  `GET ""` (all_latest_signals), `/consensus`, `/refresh`, `/reset`, `/suppressed`,
  `/recent_changes`, `/{symbol}/history`, `/{symbol}/patterns`, `/{symbol}` (signal_for) — plus
  `_bulk_persist()` (the ~2200-line core signal-generation function these routes share).
- `services/signal-engine/src/api/calibration.py` (new, 2,313 lines) — 13 self-tuning/
  calibration routes: `/watchdog_self_tuning_report`, `/ml-weight-validation`,
  `/calibrate_ml_weight`, `/calibrate_ta_weights`, `/calibrate_conviction_weights`,
  `/outcomes/calibration`, `/outcomes/calibrate`, `/outcomes/calibrate/apply`,
  `/tune_style_profiles`, `/tune_strategy`, `/watchdog`, `/tune_status`,
  `/confidence-calibration`.
- `services/signal-engine/src/api/outcomes.py` (new, 2,536 lines) — 13 analytics/backtest/
  outcome-evaluation routes: `/backfill_realized_ev`, `/accuracy`, `/rolling_accuracy`,
  `/factor-exposure`, `/trade_performance`, `/filter_audit`, `/walkforward`,
  `/outcomes/summary`, `/alpha_decay`, `/information_coefficient`, `/factor_attribution`,
  `/outcomes/evaluate`, `/gate_backtest`.
- `services/signal-engine/src/main.py` — now mounts all 3 routers (`routers=[router,
  calibration_router, outcomes_router]`); FastAPI's `include_router()` natively supports
  multiple routers sharing the same `prefix="/signals"` as long as individual paths don't
  collide, which they don't (routes were split, never duplicated) — confirmed via a direct
  diff of all 35 `@router.` decorators across the 3 files against the original 35, zero
  duplicates.

**Real bug caught DURING extraction, before it ever ran**: several module-level constants
initially landed in the wrong file purely from a naive "boundary = next route's line number"
slicing approach — e.g. `_CONF_BANDS`/`_CONF_CAL_MIN_COUNT`/`_CONF_CAL_TTL` (needed by
`_build_confidence_calibration` in `shared.py`) initially trailed into `calibration.py`'s
`watchdog_self_tuning_report` segment instead; `_OUTCOME_HOLD_DAYS`/`_SELL_OUTCOME_HOLD_DAYS`/
`_OUTCOME_CENSOR_GRACE_DAYS`/`_OUTCOME_WIN_HURDLE_PCT` (needed by BOTH calibration and
outcomes routes) initially landed only in one; `_WATCHDOG_STEP`/`_WATCHDOG_RELAX_STEP`
initially landed in `outcomes.py` instead of `calibration.py`; `_DECAY_DAYS` initially landed
with `tune_status` (calibration) instead of its real consumer `alpha_decay` (outcomes). Caught
by a systematic Python script cross-referencing every module-level `_UPPER_CASE` constant's
definition-file against every file that actually references it — not by manual read-through,
which would very plausibly have missed one or more of these given the file's size. Separately,
`shared.py` was initially missing a `TuneHistory` import entirely (used by
`_record_tune_history`) — caught by running `pyflakes` against all 4 draft files before
copying anything into place, which is also how the 3 real "unused import" cleanups (a stray
top-level `import json` shadowed by local re-imports in 2 files, an unused `date` import in
`routes.py`) were found. Every pre-existing pyflakes warning in the ORIGINAL file (an unused
`generate_signal`/`sqlalchemy.desc`/`horizon_enum`/`httpx`) was deliberately left untouched in
whichever new file inherited it — this split fixes zero pre-existing issues, only issues the
split itself introduced.

**Verified as genuinely verbatim, not just "looks right"**: wrote a Python AST-based
comparison (not a naive text/line-count check) extracting every top-level function's own
decorator+signature+body from the original committed `routes.py` and from the combined new 4
files, then diffed each of the 52 functions by name — **0 mismatches, 52/52 identical**,
confirming no function's actual logic changed anywhere during the split, only its file
location. Ran the equivalent check for module-level constants after the fixes above.

**Test suite impact — 5 existing test files locate functions via hardcoded source-string
extraction from `routes.py`** (this repo's established technique for functions that can't be
exercised behaviorally due to Docker-only import constraints, e.g.
`_ROUTES_SOURCE.index("def watchdog_self_tuning_report(")`) — these needed two kinds of
updates, not a rewrite: (1) `_ROUTES_PATH` repointed to whichever new file the function
actually lives in now (`test_evaluate_outcomes_nested_savepoint.py`/`test_backfill_realized_
ev.py` → `outcomes.py`; `test_watchdog_self_tuning_report.py`/`test_tune_strategy.py` →
`calibration.py`; `test_signal_get_path_upsert.py` unchanged, since `signal_for`/`_bulk_
persist` both stayed in `routes.py`); (2) 3 test assertions whose END boundary was a hardcoded
string like `"\n\n\n# ── T223"` (the comment block that used to immediately follow a function
in the OLD single-file layout) needed the boundary changed to whatever function/route now
immediately follows it in the NEW file — the "T223" comment itself moved to `shared.py`, so it
simply doesn't exist anymore in `outcomes.py`/`calibration.py`.

**Pre-existing, unrelated test failures confirmed via `git stash` to predate this split**: 4
`test_analyst_momentum.py` failures and `test_signal_generator.py`'s `_decide`-import
collection error — both already documented elsewhere in this file, both reproduced identically
on the clean pre-split commit.

**Verification**: 63/63 in-scope tests pass (the two pre-existing failure groups excluded and
separately confirmed pre-existing); `pyflakes` clean on all 4 new files modulo the 4
pre-existing warnings inherited verbatim from the original file.

**What to check if this looks wrong**:
```bash
# Confirm all 35 routes are registered with no duplicates across the 3 files:
docker exec stockai-signal-engine-1 grep -h '^@router\.' /app/src/api/routes.py /app/src/api/calibration.py /app/src/api/outcomes.py | sort | uniq -d
# Should return NOTHING — any output here means two files registered the same path.

# Confirm main.py mounts all 3 routers:
docker exec stockai-signal-engine-1 grep -A3 "routers=\[" /app/src/main.py

# Live-verify a route from each of the 3 files still resolves correctly:
docker exec stockai-signal-engine-1 curl -s -o /dev/null -w 'consensus: %{http_code}\n' 'http://localhost:8005/signals/consensus' -H "Authorization: Bearer <token>"
docker exec stockai-signal-engine-1 curl -s -o /dev/null -w 'tune_status: %{http_code}\n' 'http://localhost:8005/signals/tune_status' -H "Authorization: Bearer <token>"
docker exec stockai-signal-engine-1 curl -s -o /dev/null -w 'accuracy: %{http_code}\n' 'http://localhost:8005/signals/accuracy' -H "Authorization: Bearer <token>"
```
If a route 404s that used to work, check whether it landed in the wrong file (a route
decorator with a typo'd/duplicate path) or whether `main.py` is missing one of the 3 router
imports.

---


## Feature Reference: AUD291-SIGNALENGINE-GODFILES-UNEVALUATED — outcomes.py Split Write-vs-Read; signals.py Correctly Left Unsplit (2026-08-26)

**Evaluated both files individually, per the tracker item's own real question** ("do these
files have a natural fault line the way `routes.py`'s split did"), rather than reflexively
splitting either just because they're large.

**`signals.py` (2,921 lines) — correctly LEFT UNSPLIT.** Traced its 43 functions' real
coupling: `_ta_score()`/`_apply_style_signal()` alone are referenced 31 times across the file,
and every helper ultimately feeds one of the two central entry points
(`generate_all_signals()`/`generate_signal()`). No clean "this half is unrelated to that half"
boundary exists — every candidate split point is a forced cut through a genuinely single,
cohesive computational pipeline. This matches the exact reasoning
`T233-ARCH-MARKETDATA-GODSERVICE` already established for `scheduler.py`/
`paper_trading_engine.py`: tight shared state, splitting adds complexity for no benefit.

**`outcomes.py` (3,040 lines, 15 routes) — split into `outcomes.py` (3 WRITE routes:
`/backfill_realized_ev`, `/outcomes/evaluate`, `/backfill_bearish_pillars`) + a new
`analytics.py` (12 READ-only reporting routes: `/accuracy`, `/rolling_accuracy`,
`/factor-exposure`, `/trade_performance`, `/filter_audit`, `/walkforward`, `/outcomes/summary`,
`/alpha_decay`, `/signal_age_decay`, `/information_coefficient`, `/factor_attribution`,
`/gate_backtest`).** A genuinely clean fault line, confirmed via grep that every module-level
constant/helper (`_RETRO_MIN_SAMPLES`, `_DECAY_DAYS`, `_BACKFILL_MIN_BARS`, etc.) is file-local
to exactly one route — no cross-boundary shared state. `main.py` now mounts a third
`analytics_router` alongside `calibration_router`/`outcomes_router`, all 3 still registered
BEFORE `routes.py`'s own catch-all `GET /{symbol}` (confirmed neither new/kept file contains a
catch-all of its own — `routes.py` owns the only one, so the existing ordering rule already
covers this with zero additional risk, matching the `BUG233-ROUTERORDER` lesson).

**Verified genuinely verbatim via AST comparison, not a visual diff** — extracted every
top-level function's full AST dump from the original file and the combined new pair: all 45
functions present, zero missing, zero extra, zero mismatched bodies, both before AND after a
post-split pyflakes import trim (confirming the trim only touched import lines).

**Test-file fallout**: 11 test files using the established source-text-extraction technique
needed their target path repointed from `outcomes.py` to `analytics.py` (mechanical, preserving
each file's own variable-naming convention). 3 more broke on end-marker strings that assumed
adjacency now broken by the split (e.g. a test using `@router.get("/gate_backtest")` as its own
end-of-function marker, when `gate_backtest` moved to a different file) — fixed with the real,
still-adjacent marker in the new layout. `test_main_router_order.py` extended to assert
`analytics_router`'s presence/ordering too.

**Verification**: full 349-test signal-engine suite green (up from 346) modulo the 2
pre-existing, unrelated failure groups already documented elsewhere in this file
(`test_signal_generator.py`'s `_decide` import-collection error, 4 `test_analyst_momentum.py`
failures) — confirmed via `git stash` that both predate this change. pyflakes clean; the sole
remaining warning (a local `httpx` import inside `gate_backtest()`) confirmed pre-existing and
correctly relocated to `analytics.py`, the file that now actually contains `gate_backtest()`.

**What to check if this looks wrong**:
```bash
docker exec stockai-signal-engine-1 grep -n "analytics_router" /app/src/main.py
docker exec stockai-signal-engine-1 curl -s -o /dev/null -w '%{http_code}\n' 'http://localhost:8005/signals/accuracy' -H "Authorization: Bearer <token>"
docker exec stockai-signal-engine-1 curl -s -o /dev/null -w '%{http_code}\n' 'http://localhost:8005/signals/outcomes/evaluate' -X POST -H "Authorization: Bearer <token>"
```

---

