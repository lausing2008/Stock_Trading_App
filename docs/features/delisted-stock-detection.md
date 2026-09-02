## Feature Reference: aud14-survivorship — Real Delisting Detection Closes a Dead Column (Built 2026-07-27)

**Closes a long-standing gap**: ml-prediction's training-universe query at 5 call sites
(`services/ml-prediction/src/api/routes.py` — `train_all`, `tune_all`,
`train_all_ensemble_three`, `train_all_ensemble`, `train_all_horizons`) already did
`WHERE or_(Stock.active.is_(True), Stock.delisted.is_(True))` — but `Stock.delisted` was
confirmed a genuinely dead column: always `False` in production, zero writers anywhere in the
codebase. The OR clause was a real, confirmed no-op since the query-level fix was re-applied
2026-07-15 (`aud14-survivorship`'s own prior implementation note documents this explicitly).

### The real detection signal — yfinance's own exception hierarchy

Researched before building: yfinance has a purpose-built exception class,
`YFTickerMissingError` (raised in practice as either `YFPricesMissingError` or
`YFTzMissingError`, both subclasses), whose message is Yahoo's OWN API reporting "no data
found, symbol may be delisted." This is **structurally separate** from `YFRateLimitError`
(confirmed: not a subclass) — a genuine rate-limit/network blip can never be mistaken for a
delisting signal by this design.

Verified live against 5 real, confirmed-delisted tickers (Lehman Brothers `LEHMQ`, Sears
`SHLDQ`, Bed Bath & Beyond `BBBYQ`, and others) — all consistently raise a
`YFTickerMissingError` subclass. Also checked the one real false-positive risk: a valid,
currently-listed stock queried with a `start` date before it existed (e.g. pre-IPO) ALSO
raises this exception — but confirmed this app's actual call shape (`start` = last known bar
minus 7 days, or a 3-year lookback for a fresh/forced ingest) never legitimately produces that
shape for a genuinely-still-listed stock (verified live: a real recent IPO, `ARM`, queried with
a 3-year lookback correctly returns its real post-IPO history with no error).

### Implementation

- `services/market-data/src/adapters/yfinance_adapter.py` — `ticker.history()` now called
  with `raise_errors=True` so the error surfaces as a real exception instead of silently
  becoming an empty DataFrame (the pre-existing behavior for every OTHER kind of empty
  result). `YFTickerMissingError` is excluded from the adapter's own 3x `@retry` policy via
  `retry_if_not_exception_type` — retrying a genuine delisting can never succeed, unlike a
  real transient error, so excluding it avoids wasting ~8s of retry backoff per occurrence and
  delaying the signal.
- `services/market-data/src/services/ingestion.py` — new `_record_delisting_signal()`/
  `_clear_delisting_signal()`, wired into `ingest_symbol()`'s adapter loop, **gated to the
  daily-bar cycle only** (`timeframe == "1d"`) — intraday ingestion runs far more often and
  would reach a false "confirmed" state within hours on a stock that's merely rate-limited,
  not genuinely delisted. Requires **2 consecutive** ingestion runs (a Redis counter,
  `stockai:delisting_signal:{symbol}`, 30-day TTL) before setting `Stock.delisted = True` — a
  conservative confirmation margin against a genuine one-off provider glitch, even though
  `YFTickerMissingError` itself is already excluded from the adapter's own retry policy. Any
  successful fetch clears the counter immediately.

### A real regression caught and fixed in the same session, before shipping

The new `from yfinance.exceptions import X` and `from common.redis_client import Y` import
forms broke 4 UNRELATED test files' collection (`test_macro_events_from_db.py`,
`test_premarket_session_classify.py`, `test_promotion_history_reader.py`,
`test_validation.py`) — all transitively import `ingestion.py` via `routes.py`/`admin.py`/
`scheduler.py`, and this repo's `conftest.py` stubs `yfinance`/`common` as bare `MagicMock()`
objects for local testing. A bare `from X.submodule import Y` import statement requires `X` to
be a real package to resolve `X.submodule` — it fails hard against a `MagicMock`-stubbed `X`,
unlike `import X as x` followed by `x.submodule.Y` attribute access (which works fine on a
mock, since attribute access on a `MagicMock` never raises). Fixed by switching both new
imports to the `import X` + attribute-access form, and separately adding the missing
`common.redis_client` entry to `conftest.py`'s stub list (a gap that existed independently of
this fix, only surfaced by it).

**What to check if this looks wrong**:
```bash
# Confirm the fix is actually setting delisted=True when it should (needs a real delisted
# ticker to observe naturally — will only fire for a stock genuinely in this app's universe
# that gets delisted going forward):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, delisted FROM stocks WHERE delisted = true;"

# Check the Redis confirmation counter for a specific symbol mid-confirmation:
docker exec stockai-redis-1 redis-cli get stockai:delisting_signal:<SYMBOL>

# Check ingestion logs for the signal being recorded/confirmed:
docker logs stockai-market-data-1 --since 24h | grep 'delisting_signal\|delisted_confirmed'
```

---


## Feature Reference: T260-DELISTED-BADGE — Informational Badge, Deliberately No Auto-Removal (Built 2026-07-27)

**Direct follow-up to aud14-survivorship** (above), after the user asked "if delisted, should
we remove from watchlist?" Researched before building: today `Stock.delisted` touches nothing
user-facing at all — watchlists, alerts, and paper positions each handle "no fresh data"
independently and inconsistently (silently stale prices, alerts that quietly never fire again,
paper positions that freeze open forever with only a log-level warning, per
`_monitor_positions()`'s own staleness-escalation comment explicitly naming delisting as one of
the scenarios it guards against).

**Decision, given 3 options presented (badge-only / auto-remove-with-notification / auto-
remove-silently-matching-precedent)**: badge-only, no auto-removal. The one existing precedent
for "auto-modify a user's watchlist from a backend signal" — `_run_watchlist_auto_rotation()`'s
win-rate-based drops — is silent (audit-trail-only, no user notification) and is justified
specifically because a win-rate-based drop is *reversible* (a stock can earn its way back onto
the candidate list next week). Delisting is *terminal* — copying that same silent-removal
behavior for an irreversible condition would repeat the one real weak spot of that pattern in
exactly the case where it matters most. A badge preserves the user's historical record and
lets them decide when to remove it themselves.

**Implementation**: `Stock.delisted` already existed (aud14-survivorship) — this pass only
threads it through to two existing response models that already receive the full `Stock`
ORM row, no new endpoint needed:
- `services/market-data/src/api/watchlist.py` — `WatchlistItemOut.delisted: bool = False`,
  set in `_item_out()` from `stock.delisted` (the function already receives the full `Stock`
  object).
- `services/market-data/src/api/routes.py` — `StockOut.delisted: bool = False`. Since
  `StockOut` uses `from_attributes = True` and `get_stock()` returns the `Stock` ORM row
  directly, this required zero handler-function changes — the field just appears.
  `api-gateway`'s `GET /aggregate/overview/{symbol}` (`aggregate.py`) builds its `price` field
  as a direct pass-through of this same `GET /stocks/{symbol}` response, so the stock detail
  page picks it up for free too.

**Frontend**: `frontend/src/pages/watchlist.tsx` (badge next to the symbol on each card) and
`frontend/src/pages/stock/[symbol].tsx` (badge next to the `<h1>` symbol heading) — both a
small red "DELISTED" pill with an explanatory hover tooltip, matching this app's established
small-badge visual convention (e.g. `ExitBadge`/`BrokerStatusBadge` elsewhere in this repo).
`WatchlistItem`/`Stock` TypeScript types both gained `delisted?: boolean`.

**Deliberately NOT built this pass** (documented, not silently dropped, since the same research
surfaced these as real, related gaps): alerts on a delisted symbol still silently stop firing
forever with no escalation; paper positions in a delisted stock still freeze open indefinitely
with only a log-level warning, no user notification, no auto-exit. Both are real, but distinct
scoped follow-ups from "should the watchlist react to this" — worth their own focused pass
rather than bundling into this narrower, already-scoped badge change.

**Tests**: `services/market-data/tests/test_watchlist_delisted_badge.py` (3 cases) — imports
the real `watchlist.py` module directly (a pure function, no DB/session dependency, so no
source-extraction workaround was needed) and confirms `_item_out()` correctly threads
`stock.delisted` through. Adversarially verified: removing the field assignment correctly
failed 2 of 3 tests before being reverted. Full 535-test market-data suite green (up from 532);
frontend typecheck, vitest suite (89 tests), and a full `next build` all clean.

**What to check if this looks wrong**:
```bash
# Confirm a delisted stock's watchlist item reports the flag:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'lausing','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://localhost:8001/watchlist', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print([item for item in r.json() if item.get('delisted')])
"
```

---


## Feature Reference: T232-OC6 (Revisited) — Confirmed Delistings Now Scored as Real Losses (2026-07-28)

**Gap closed**: `evaluate_signal_outcomes()`'s censoring branch
(`services/signal-engine/src/api/outcomes.py`) has always written a `SignalOutcome` row with
`skip_reason="no_exit_price"` and `is_correct=NULL` whenever a signal's hold window closed
with no exit price found (after a 10-day ingestion-lag grace period) — a deliberate
2026-07-03 fix that stopped these outcomes from silently vanishing, but `is_correct=NULL`
still EXCLUDES the row from every win-rate/calibration query (all filter `is_correct IS NOT
NULL`), meaning a real delisting after a BUY signal never hurt the win rate at all — the
worst-case outcome was omitted from calibration rather than penalized. `docs/
KNOWN_LIMITATIONS.md`'s T232-OC6 entry documented this as a deliberate, honest deferral:
"there is no reliable signal in this system to distinguish 'confirmed delisting' from
'benign, longer-than-10-day ingestion gap'" — explicitly naming `Stock.delisted` becoming
real as the prerequisite to revisit.

**That prerequisite is now satisfied** — `aud14-survivorship` (2026-07-27, documented
elsewhere in this file) built a real, conservative delisting detector via yfinance's
`YFTickerMissingError` exception hierarchy, requiring 2 CONSECUTIVE ingestion failures before
`Stock.delisted` flips `True`. This session wired that column into the censoring branch.

**Implementation**:
1. `pending_signals`' existing `select(Signal, Stock.symbol).join(Stock, ...)` query extended
   to `select(Signal, Stock.symbol, Stock.delisted)` — zero new queries, reusing the SAME
   join already in place. All 3 unpacking sites (`pending_stock_ids`, `price_min_ts`, the main
   `for sig, symbol in pending_signals:` loop) updated to `for sig, symbol, is_delisted in
   pending_signals:`.
2. Inside the existing 10-day-grace-period censoring branch:
   ```python
   _is_confirmed_delisting = bool(is_delisted) and sig.signal == SignalType.BUY
   ...
   is_correct=(False if _is_confirmed_delisting else None),
   skip_reason=("delisted_loss" if _is_confirmed_delisting else "no_exit_price"),
   ```
3. **SELL signals are deliberately NOT scored on a confirmed delisting** — a delisting
   doesn't confirm a SELL thesis was right (an unrelated acquisition at a premium would also
   delist the stock without validating "this will fall"), so guessing a direction there would
   trade one bias for a different, harder-to-detect one — exactly the risk the original
   T232-OC6 entry's own reasoning warned about. SELL rows on a delisted stock keep the prior,
   unchanged censored/NULL behavior.
4. `outcomes_summary()`'s `censored` count query (`GET /signals/outcomes/summary`) was also
   corrected: it previously filtered `SignalOutcome.skip_reason.is_not(None)`, which would
   have double-counted a `delisted_loss` row as BOTH "scored" (via `is_correct`) AND
   "censored" (via the summary's own count) at once. Now filters
   `SignalOutcome.skip_reason == "no_exit_price"` specifically.

**Because `is_correct=False` (not NULL) is a real value, not a new code path**: every
existing `is_correct.is_not(None)` filter across `outcomes.py`/`calibration.py` (8+ call
sites) automatically counts a `delisted_loss` row as a loss in every win-rate/calibration
query — zero downstream query changes needed anywhere else.

**Tests**: `services/signal-engine/tests/test_delisted_loss_scoring.py` (11 cases), following
`test_evaluate_outcomes_nested_savepoint.py`'s established convention exactly (source-text
extraction for structural checks against the real production code — `evaluate_signal_
outcomes()` can't be driven end-to-end in this test environment — plus a real in-memory
SQLite model to directly exercise the classification and persistence). Covers: the extended
join/unpacking exists, the classification requires BOTH `is_delisted` AND `SignalType.BUY`,
`is_correct`/`skip_reason` are set correctly for the confirmed case vs. the ordinary case, the
`outcomes_summary` censored-count fix, and behavioral round-trip tests against the real
`SignalOutcome` model confirming a `delisted_loss` row IS picked up by `is_correct.is_not
(None)` while an ordinary censored row still is NOT.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. The classification condition (`_is_confirmed_delisting = False`) — caught by the dedicated
   test checking that exact expression's source text.
2. The `is_correct`/`skip_reason` assignment (reverted to the pre-fix hardcoded
   `skip_reason="no_exit_price"`) — caught by 2 dedicated tests.
3. The `outcomes_summary` censored-count fix (reverted to `skip_reason.is_not(None)`) — caught
   by its own dedicated test.

Full 111-in-scope-test signal-engine suite green (up from 100, excluding the 2 pre-existing,
unrelated failure groups already documented elsewhere in this file — `test_signal_
generator.py`'s `_decide` import-collection error and 4 `test_analyst_momentum.py` failures,
both confirmed via `git stash` to predate this change). `pyflakes` clean (the sole warning,
an unused `httpx` import, confirmed pre-existing via `git stash`).

**What to check if this looks wrong**:
```bash
docker exec stockai-signal-engine-1 grep -n "_is_confirmed_delisting\|delisted_loss" /app/src/api/outcomes.py

# Check real delisted_loss rows in production, once any confirmed delisting ages past the
# 10-day grace period after its hold window closes:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, signal_date, is_correct, skip_reason FROM signal_outcomes WHERE skip_reason = 'delisted_loss' ORDER BY signal_date DESC LIMIT 10;"

# Confirm the ordinary censored count no longer includes delisted_loss rows:
docker exec stockai-signal-engine-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from db import SessionLocal; from sqlalchemy import text
s = SessionLocal()
print('no_exit_price:', s.execute(text(\"SELECT COUNT(*) FROM signal_outcomes WHERE skip_reason='no_exit_price'\")).scalar())
print('delisted_loss:', s.execute(text(\"SELECT COUNT(*) FROM signal_outcomes WHERE skip_reason='delisted_loss'\")).scalar())
s.close()"
```

---


## Feature Reference: BUG-PAPERPOS-DELISTED-FROZEN + BUG-ALERTS-DELISTED-SILENT — Delisting Now Wired Into Paper-Position Exits + Alert Deactivation (Built 2026-07-29)

**Gap this closes**: `Stock.delisted` (aud14-survivorship, 2026-07-27) is a real, conservative
signal (2 consecutive yfinance `YFTickerMissingError` failures before it flips `True`) and is
already surfaced as a badge on the watchlist/stock-detail pages (T260-DELISTED-BADGE) — but
before this fix it was consumed in exactly those 2 read-only display sites and NOWHERE else.
Both gaps were explicitly flagged as deliberately-deferred follow-ups in T260-DELISTED-BADGE's
own writeup ("alert-on-delisted-symbol silent-forever-non-firing and paper-position-freezes-
open-on-delisting are both real, related gaps... scoped out as their own separate follow-ups")
and confirmed still real by re-reading the current code before building anything.

### 1. Paper positions froze open forever on a delisted stock

**Root cause**: `_monitor_positions()`'s missing-live-quote fallback
(BUG-MONITORPOS-STALEPRICE, 2026-07-21) already tracks consecutive stale cycles in Redis and
escalates from `log.warning` to `log.error` once a real threshold is crossed — but that
escalation is diagnostic-only. Nothing ever actually CLOSED the position. A delisted stock's
open paper position would sit at `stage="open"` indefinitely, its stop/target math running
against an increasingly stale fallback price, permanently distorting the portfolio's reported
equity/P&L with no resolution.

**Fix**: bulk-fetch `Stock.delisted` for every open symbol once per monitoring cycle (same
batch-fetch pattern the function already uses for signals/kscores/OBV divergence — no
per-trade N+1 query), then add a new `exit_reason = "delisted"` hard-exit branch checked
FIRST, ahead of the stop/target/signal-exit chain — a delisted stock has no real market left
to compute a meaningful stop/target breach against, so this must preempt rather than compete
with those checks. Reuses the EXACT same "Execute exit" block every other hard exit already
flows through (fills, commission, cash credit, `signal_outcomes` write-back, broker exit
routing) — no bespoke shortcut. `exit_reason` is a plain `String(64)` column with no enum
constraint, so no schema/migration was needed for the new value.

**Deliberately NOT added to `_MECHANICAL_EXIT_REASONS`** (`paper_portfolio.py`'s trade-
postmortem classifier) — that set is already narrower than "any hard exit" (it excludes
`signal_exit`/`hold_stall_timeout`/`momentum_fade`/`momentum_exit` too), and a delisting is
neither a plan-consistent mechanical exit nor a discretionary one — it's a forced exit the
market itself imposed. Left uncategorized rather than force a decision outside this fix's
scope.

### 2. Alerts on a delisted symbol went silent forever with no notification

**Root cause**: `check_price_alerts()` fetches live prices via `yf.Tickers(...).fast_info.
last_price` — a delisted symbol simply never gets a usable price, so `prices.get(alert.symbol)`
returns `None` and the alert is silently skipped every cycle forever, with `triggered` staying
`False` indefinitely. `check_signal_alerts()`'s existing DP-3 freshness check (a 4-day
`Price.ts` staleness window) has the identical blind spot — it correctly excludes a stale
symbol from firing, but can't distinguish "genuinely delisted, will never update again" from
"a normal few-day data gap," so the user's subscription just quietly stops working with zero
indication anything is wrong.

**Fix — two different terminal actions, because the two alert types have genuinely different
lifecycles**:
- **`PriceAlert`** already self-terminates via `triggered=True` once fired (`select(PriceAlert)
  .where(PriceAlert.triggered.is_(False))`) — so a confirmed delisting is a real terminal
  state here, not just a notice. Bulk-fetches `Stock.delisted` for all alert symbols, sets
  `triggered=True` + `triggered_at` on every delisted symbol's alerts, commits, and — if an
  email is on file — sends a one-time "this alert can no longer fire, SYMBOL is delisted"
  notice. The main per-alert loop also explicitly skips `delisted_symbols` (defense-in-depth
  against a transient stale-cached yfinance value slipping through before the real exception
  path fires, even though the pre-existing `if price is None: continue` already makes this a
  no-op in practice).
- **`SignalAlert`** has NO such lifecycle at all — it's a persistent subscription with no
  `triggered` field, and a relisting is rare but not impossible, so deleting/deactivating the
  row outright would be the wrong, more destructive move. Instead: a one-time notification per
  `(alert)` via a Redis `SET NX EX` dedup key (`stockai:alert_delisted_notice:{alert.id}`, 90-day
  TTL — matching this file's own established one-time-notice convention, e.g. `stockai:
  auto_research_sent:{sym}`), so the notice fires exactly once per alert rather than every
  minute this job runs. The subscription itself is left completely untouched.

Both fixes fail open on a DB error for the delisted-lookup itself (matching every other
optional batch-fetch in these functions) — a query hiccup must never crash the whole
monitoring/alert cycle for every other open trade or alert.

**Tests**: `services/market-data/tests/test_delisted_position_autoexit.py` (6 cases) and
`test_delisted_alert_deactivation.py` (12 cases) — both source-text regression checks
(`paper_trading_engine.py`/`scheduler.py` can't be imported directly in this test environment,
matching every other test file's documented constraint for these two modules). Cover: the
bulk-fetch pattern and its fail-open guard, the delisted-exit branch's priority ordering
(strictly first, before the stop-check `elif`, with exactly one `exit_reason =` assignment
between the branch start and the stop check), that it flows through the shared execute-exit
block rather than a bypass, `PriceAlert`'s `triggered=True`+commit+main-loop-skip-guard, and
`SignalAlert`'s no-delete guarantee + Redis dedup key with `nx=True` + email-on-file gate.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted:
1. Relocating the delisted-exit branch to fire AFTER the stop/target/signal chain (an `elif`
   inserted just before the momentum-fade check) — caught by the ordering test with a real
   `substring not found` failure (the exact `if trade.symbol in delisted_symbols:` prefix no
   longer existed at its expected position), proving the test catches a genuine relocation
   bug, not just a coincidental string match.
2. Removing `PriceAlert`'s main-loop defense-in-depth skip guard — caught directly.
3. Removing `nx=True` from `SignalAlert`'s dedup key (which would have resent the notice every
   single minute this job runs) — caught directly.

Full 620-test market-data suite (up from 602) green after every revert; `pyflakes` clean on
both touched files (confirmed via `git stash` that all 7 pre-existing warnings predate this
change — only line numbers shifted).

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n 'exit_reason = "delisted"\|delisted_symbols' /app/src/services/paper_trading_engine.py
docker exec stockai-market-data-1 grep -n 'stockai:alert_delisted_notice\|delisted_fired' /app/src/services/scheduler.py

# Check for a real auto-exit having fired:
docker logs stockai-market-data-1 --since 24h | grep 'paper.delisted_auto_exit'

# Check for a real alert deactivation/notice having fired:
docker logs stockai-market-data-1 --since 24h | grep 'price_alert.delisted_deactivated\|signal_alert.delisted'

# Confirm a specific delisted symbol's open trades actually closed:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT symbol, stage, exit_reason FROM paper_trades WHERE symbol IN (SELECT symbol FROM stocks WHERE delisted = true);"

---

