## AUD250 — Deep Audit of the 2026-07-11 to 2026-07-16 Work Window (73 Commits, 11 Services)

**What this was:** a full multi-agent review (one agent per touched service, reviewing that
service's REAL git diff against a fixed base commit, not a generic fresh bug hunt) of every
logic/code change made in a 5-day window: the Tier 247 full 11-service audit, Tier 248/249/250
features (CAPE valuation, Market-Mover Monitoring, Volume Profile), and this session's own
SE-F2/retro-feedback/premarket work. 16 findings surfaced; all 16 survived independent
adversarial verification. Two (a `TuneHistory` column and 3 `EconomicEvent` columns) were
refuted immediately by checking live production Postgres directly — the audit agents could only
reason from git history and commit-message language, and in both cases a commit's OWN language
was internally inconsistent about whether a required `ALTER TABLE` had actually been run. This
reinforces the "verify against live state, not just git history" discipline documented
elsewhere in this file — a subagent without SSH access will get this exact class of finding
wrong in either direction, and the fix is always to check the actual running system.

### CRITICAL (fixed same-day, before its first scheduled run) — Watchlist Auto-Rotation Never Actually Ran

**Symptom:** none visible yet — this was caught BEFORE it could produce a symptom. The
`watchlist_auto_rotation_weekly` job (Sunday 17:00 ET, `services/market-data/src/services/scheduler.py`)
had run zero times successfully since being merged 2026-07-13; the coming Sunday would have
been its first live-scheduled fire.

**Root cause:** `_run_watchlist_auto_rotation()` had TWO independent `NameError`-causing bugs in
the same function: (1) `Market.US` used for a market tie-break with `Market` never imported
anywhere in `scheduler.py`'s ~4,700 lines; (2) `desc(Ranking.score)` used with `desc` never
imported (the function's own local `from sqlalchemy import func as _func, case as _case, delete
as _delete` didn't include it). Both are caught by the function's own top-level
`except Exception`, which logs `watchlist_auto_rotation_failed` and records job status "error" —
silent from the outside, no adds, no drops, indistinguishable from "ran fine, found nothing to
do" unless someone actually reads the error log. **Zero test coverage existed for this
function** — and critically, `conftest.py` stubs `sqlalchemy`/`db` as `MagicMock()` for local
tests, and a `MagicMock()` attribute access never raises `NameError`/`AttributeError` — so even
importing and exercising the real function under the existing test harness would NOT have
caught either bug. This is the same "stub a whole module, mask a real missing-import bug" gap
already documented for `services/signal-engine/tests/conftest.py` elsewhere in this repo's
history — worth checking any OTHER heavily-stubbed test suite in this repo for the same blind
spot before trusting "all tests pass" as proof a new function is wired correctly.

**Fix applied (2026-07-16):** Added `Market` to the module-level `from db import ...` line and
`desc` to the function's local `from sqlalchemy import ...` line. Verified by actually CALLING
`_run_watchlist_auto_rotation()` live in the production container immediately after deploying —
not just re-running the (still-stubbed) pytest suite — and confirmed a real completion log line
(`watchlist_auto_rotation_complete`, dropped=8, no exception) where before it would have logged
`_failed` every time. Added `services/market-data/tests/test_scheduler_static_names.py` — two
narrow, source-text regression checks (not a general "does every name resolve" static
analyzer, which was attempted and abandoned: nested closures/lambdas/comprehensions made a
hand-rolled scope resolver produce more false positives than real signal in this file).

**What to check if this or a similar function looks silently broken again:**
```bash
# Confirm the job's real completion status, not just "container is Up":
docker logs stockai-market-data-1 --since 24h | grep 'watchlist_auto_rotation'
# Should show watchlist_auto_rotation_complete, not _failed, after every Sunday 17:00 ET run.

# To directly re-verify a scheduler function actually runs (don't trust stubbed pytest alone):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0,'/app')
from src.services.scheduler import _run_watchlist_auto_rotation
_run_watchlist_auto_rotation()
"
```

**Design invariant:** any test suite that stubs a whole module as `MagicMock()` (common in this
repo's conftest.py files, needed to avoid real Docker-only dependencies like psycopg2/redis at
import time) provides ZERO protection against a missing import or undefined name inside code
that only runs through that stubbed module — `MagicMock()` silently accepts any attribute
access. A brand-new function added to a heavily-stubbed file needs either (a) a direct live
call against a real deployed container before trusting it's wired correctly, or (b) a narrow
source-text regression test for its specific imports/names, matching the pattern in
`test_scheduler_static_names.py` — "all pytest passed" alone does not prove the function can
actually execute.

### HIGH — event-intelligence's Macro-Reaction Poll Blocked Its Own Event Loop

**Symptom:** none reported yet (release-day-armed, so only exposed during the ~90-minute
CPI/PPI/GDP/NFP window or FOMC statement windows) — caught by the audit before it could cause a
real incident.

**Root cause:** `check_release_day_fast_poll()`, `check_fomc_statement_poll()`, and
`generate_reaction()` in `services/event-intelligence/src/services/macro_reaction.py` are all
`async def` (registered on `AsyncIOScheduler`, running on the SAME event loop as the FastAPI app
serving real-time HTTP requests to this service — confirmed via `main.py`'s
`on_startup=start_scheduler`), but called `httpx.get()` and `feedparser.parse()` — both
blocking, synchronous I/O — directly. Any concurrent request to event-intelligence during a
poll would hang for up to that call's own timeout (8-10s per FRED/regime call). This is the
exact same bug class already fixed once in this repo for decision-engine's `regime.py`
(`_regime_executor`) — the fix wasn't ported to this newer service when it was built.

**Fix applied (2026-07-16):** Added a dedicated `_macro_reaction_executor =
ThreadPoolExecutor(max_workers=2)` and routed all three blocking calls through
`loop.run_in_executor(...)`, matching `regime.py`'s established pattern exactly. New
`test_macro_reaction_not_blocking.py` (4 source-text checks) confirms the executor exists and
each of the three call sites actually uses it — adversarially verified by reverting one call to
its direct blocking form and confirming the test caught it before restoring.

**Design invariant:** any new `AsyncIOScheduler` job in a service whose FastAPI app shares the
same event loop (true for every service in this repo except market-data, which uses
`BackgroundScheduler` — a separate thread pool by design) must route ALL blocking I/O (HTTP
libraries without an async variant used correctly, `feedparser`, file I/O, etc.) through a
dedicated `ThreadPoolExecutor` + `run_in_executor()`. Grep for `httpx.get(` / `httpx.post(`
(not `AsyncClient`) and any third-party library without a documented async mode inside any
`async def` in a service using `AsyncIOScheduler` before considering a new job "done."

### MEDIUM — research-engine's In-Flight Dedup Silently Lost Tracking on a Mismatch Fallthrough

**Root cause:** `generate_research()`'s concurrent-request dedup (`_inflight_research: dict[str,
asyncio.Event]`) has the first caller `pop()` its own entry right before firing its completion
event. A second, concurrent caller that was waiting on that event, then found the finished
report's baked-in portfolio params didn't match its own request (T247-RESEARCHENGINE-CACHEKEY's
own fix, working as intended), fell through to compute its own report — but never re-registered
itself in `_inflight_research`. A third concurrent request arriving in that exact window would
see the symbol as not-in-flight and start its OWN duplicate LLM generation instead of deduping
against caller #2's now-in-progress work — a real, if narrow-window, dead-code-defeating-its-
own-purpose bug.

**Fix applied (2026-07-16):** Re-register `_inflight_research[sym] = asyncio.Event()` on the
mismatch-fallthrough path, exactly matching the `else` branch's own registration. Not covered
by a new automated test — `generate_research()` has heavy runtime dependencies (httpx gather
across 6+ services, DB, LLM call) that make a real behavioral test for this specific race
expensive to build safely; documented here instead, matching the same effort-vs-risk judgment
already applied elsewhere in this file (e.g. the signal-engine rollback finding below).

### MEDIUM (documented, not fixed this pass) — Two Real Findings Requiring Larger, Riskier Changes

**1. `evaluate_signal_outcomes()`'s per-signal `try/except` calls `session.rollback()` mid-loop**
(`services/signal-engine/src/api/routes.py`, function starts ~line 5006). SQLAlchemy's
`Session.rollback()` expires every ORM object in the session's identity map by default —
including every already-bulk-loaded `Signal` row in `pending_signals`, a list iterated across
potentially thousands of loop iterations. After any single signal's exception, every
SUBSEQUENT iteration's `sig.xxx` attribute access silently triggers a fresh per-attribute SELECT
against the DB (a real N+1 performance regression on every failure, not silent data
corruption — Signal rows aren't otherwise concurrently mutated, so the re-fetched values should
still be correct, just expensive to re-fetch). The textbook-correct fix is `session.begin_nested()`
(a SAVEPOINT) around each signal's own processing, rolling back only that one signal's `add()`
without expiring the whole identity map — deliberately NOT applied this pass given the function's
size (250+ lines, multiple exit paths, existing counters) and the real risk of a rushed
structural change to a function this delicate. Revisit as its own focused task.

**2. `_macro_events_from_db()`'s per-type (not per-`(type, date-range)`) fallback tracking**
(`services/market-data/src/api/routes.py`, ~line 1634). `types_with_db_rows` is a plain
`set[str]` — if the DB has even ONE release-date row for a macro type anywhere in the requested
window, ALL `_MACRO_2026` hardcoded fallback entries for that type are skipped across the
ENTIRE window, including date ranges the DB sync never actually reached.
`sync_fred_release_dates()` only syncs 180 days ahead by default; `GET
/stocks/events/calendar?days_ahead=365` is a valid, allowed request (`Query(90, ..., le=365)`) —
a caller requesting >180 days ahead can see a real DB row for a near-term release, which then
silently suppresses fallback coverage for months 181-365 that the DB genuinely has no data for.
**Low real-world exposure today**: the frontend only ever requests the 90-day default (under
the 180-day sync window), so this has not yet produced a visible gap. Fix requires tracking
covered date-ranges per type, not just a type-level boolean — real but deliberately deferred as
its own scoped task rather than rushed.

### LOW severity, fixed

- **research-engine**: a genuinely verified `institutional_ownership.pct == 0.0` (real data,
  0% institutional ownership) rendered as "Unknown" in the fundamentals checklist — a classic
  falsy-zero bug, now newly REACHABLE now that this field carries real precisely-scaled data
  (T247-RESEARCHENGINE-INSTOWNERSHIP-SCALE) instead of an LLM free-text guess that almost never
  landed on an exact `0`. Fixed by tracking whether real fundamentals data was available
  separately from the numeric value itself.
- **frontend `research/[symbol].tsx`**: removed the `pct * 100 > 1 ? pct : pct * 100` scale-
  detection heuristic — dead weight now that the backend fix above guarantees `pct` is always a
  real, pre-scaled percent; the heuristic existed specifically to compensate for the OLD
  unscaled behavior and was never removed when that was fixed.
- **event-intelligence `congress.py`**: `on_conflict_do_update`'s `SET` clause included
  `stock_id`, which could overwrite a previously-resolved `stock_id` with `NULL` if a later
  re-sync's ticker-to-stock lookup failed transiently. Fixed with `COALESCE(excluded.stock_id,
  congress_trades.stock_id)` so a failed re-resolution can't regress an already-correct link.
  (Caught myself nearly reintroducing this exact NameError bug class while writing this fix —
  `sqlalchemy.func` wasn't imported in this file either; added it alongside the coalesce fix.)

### LOW severity, documented only (real but low-impact / needs larger scoped work)

- **decision-engine**: `abuild_game_plan()` shares the same 4-worker `_yf_executor` thread pool
  with the unrelated yfinance-price-fallback path, instead of its own dedicated executor like
  `regime.py`'s `_regime_executor` — undercuts (but doesn't defeat) the parallelism a batch
  `/decide/batch` request is supposed to get; tasks queue behind each other rather than
  stalling the event loop.
- **signal-engine**: `signal_watchdog()`'s cross-mechanism-coupling note is written into
  `TuneHistory.gate_failures` while `promoted=True` — every other of ~15 call sites treats
  `gate_failures` as exclusively a rejection reason paired with `promoted=False`. This IS an
  intentional, already-documented design choice (see `SELFIMPROVE-CROSS-MECHANISM-BLINDNESS`'s
  own implementedNote elsewhere in this file, and `admin.py`'s pre-existing "reverted" marker
  reusing the same field on a `promoted=True` row) — a real field-overloading code smell, not
  an accidental bug, left as-is rather than a schema change chasing a smell with no functional
  impact.
- **portfolio-optimizer**: a user-supplied `constraints.max_weight` that makes the mean-
  variance/risk-parity SLSQP optimization infeasible falls back to flat equal-weight with an
  HTTP 200 and no field in the `PortfolioWeights` response indicating this happened — only
  visible via a `log.warning`. A real, previously-unaddressed gap; fixing it means adding a new
  field to the response schema (and the frontend consuming it), a genuine small API-contract
  change deliberately not rushed into this pass.
- **ranking-engine test quality**: `test_rank_symbol_market_scoping.py`'s regression test for
  the CROSSMARKET fix hand-duplicates the query construction rather than extracting and
  exercising `rank_symbol()`'s REAL source (the source-text-extraction pattern already
  established elsewhere in this repo, e.g. `test_backfill_realized_ev.py`,
  `test_price_alert_price_check.py`) — it can pass even if the real `routes.py` regresses. The
  underlying PRODUCTION fix is real and already verified working; this is a test-infrastructure
  quality gap, not a live bug. Worth converting to source-extraction as a follow-up.

### Refuted — Confirmed Live in Production, Not Actually Missing

Two findings claimed a required `ALTER TABLE` for an existing-table column addition was never
actually applied to production, based on git-history/commit-message language alone (the audit
agents had no way to SSH and check the real database). Both were checked directly against
production Postgres and found to already be live:
```
\d signal_outcomes  → research_rec is already varchar(32) (widened, not the original 16)
\d economic_events   → reaction_text / reaction_generated_at / reaction_sent_at all already present
```
Filed here as a reminder of the "verify against live state, not git history" discipline
documented elsewhere in this file — a commit's own language can be internally contradictory
(one part says "pending," another part of the SAME commit says "verified live") and only a
direct check of the real running system resolves which half was actually true.

---


## Deep Audit: Trading Gate / Chart / Reports (2026-07-17) — 10 Confirmed Bugs, All Fixed

**Trigger:** user asked for a full audit of everything touched recently, with explicit focus
on "paper trading, decision engine, market regime, ai signal, FVG, entry point, stop loss,
target price." Process: 6 parallel agents each independently reviewed a real `git diff`
against a fixed base commit for their area (chart/FVG/drawing tools; Reports/nav/API types;
market-data backend; docs/tracker consistency; decision-engine+paper-trading gate; AI-signal-
to-entry/stop/target pipeline), reading the actual current code, not just diff text. 3
adversarial verification agents then tried to REFUTE the highest-stakes candidates before
anything was reported — one verified the date-vs-datetime claim by querying the real
production Postgres container directly. All 10 reported findings were CONFIRMED.

**Two of the ten broke features shipped in the SAME session** — a reminder that shipping a
feature and auditing it are genuinely different activities; neither the original build nor its
own tests (which mock/stub the exact boundary the bug lived in) caught either one.

### 1. Pre-market brief's macro section could never show anything (date-vs-datetime)

`_macro_events_from_db(session, today, today)` compared `EconomicEvent.event_date`
(DateTime, rows land at e.g. 08:30 UTC) against a bare `date` cutoff. Postgres coerces the
date to midnight for the comparison — confirmed live: `'2026-07-17 08:30:00'::timestamp <=
'2026-07-17'::date` returns `false`. Invisible for `events_calendar()`'s existing 90-day-ahead
window (the exclusion only clipped the far edge); fatal for the brief's `cutoff==today` call,
which excluded literally every same-day release. **Fix:** widen the upper bound to end-of-day
via `datetime.combine(cutoff, datetime.max.time())` inside `_macro_events_from_db()` itself —
a no-op for the wide-window caller, fixes the same-day case.

### 2. US and HK pre-market briefs were near-duplicates

The recipient query (`select(PriceAlert).where(triggered.is_(False))`) had no market filter at
all — both jobs emailed the identical full subscriber list, and macro content (US-only FRED/
FOMC data) was identical in both, useless at 8am HKT. **Fix:** recipients now filtered by
`_sym_market(a.symbol) in markets` (only users watching a symbol in THIS market get THIS
market's brief); macro-releases and macro-reactions sections both gated behind `if "US" in
markets`, so the HK brief only ever contains real HK earnings data or doesn't send.

### 3. Pre-regime double-penalty (introduced by this session's own T232 DE-parity fix)

`_scan_for_entries` already raised `min_entry_score` for `is_pre_choppy`/`is_pre_risk_off`
(the pre-existing `RE-9` mechanism). Adding a score-layer subtraction for the same flags (this
session's earlier T232-DL-DUALSCORER-DEBT parity fix) meant a pre-regime window now hit twice
— raised floor AND lowered score — a discontinuous 2-point swing at the boundary with zero
backtest coverage (`gate_harness.py` replays with `live_regime=None`). Checked decision-engine's
own `min_score_for_regime()`: it only reads `regime_state`, never the pre-regime flags — DE
applies the effect exactly once, via score only. **Fix:** removed the `min_entry_score` raise
from `RE-9`'s pre-regime block, kept only its sizing tighten — the score-layer subtraction is
now the sole pre-regime effect, matching DE exactly instead of over-correcting past it.

### 4/5. Inverted-looking R:R + no currency handling (PositionSizer, PriceChart)

Both `PositionSizer.tsx` and `PriceChart.tsx`'s risk-reward label computed `Math.abs()` on
both the risk leg AND the reward leg — a take-profit on the wrong side of entry (e.g. an
analyst `target_price` below current price, a bearish signal) still produced a positive-
looking R:R, displayed as if it were a favorable long. Separately, `PositionSizer` has one
global USD account-size setting with no currency awareness at all — HK stock entry/stop/
target (HKD) were silently sized as if USD, off by the FX rate (~7.8x) with zero indication.
**Fix:** direction is now inferred from stop-vs-entry (this tool has no explicit long/short
toggle); when target lands on the wrong side for that inferred direction, the R:R figure is
suppressed entirely with a visible warning instead of showing a misleading ratio. For
currency: no FX-conversion data source exists anywhere in this app, so rather than guess an
exchange rate, a `currency` prop (from the stock detail page's live-price data) now drives a
mismatch warning banner and relabels "Account Size ($)" to the stock's real currency when it
isn't USD — surfacing the problem honestly rather than pretending to solve it.

### 6. ETF fundamentals permanently uncached (this session's OWN empty-fetch guard, over-applied)

The `fetch_looks_empty` guard added earlier this session (to fix a real null-overwrite
incident) checked `market_cap is None and trailing_pe is None and total_revenue is None` —
exactly the three fields genuinely absent from every real, successful ETF fetch (GLD/SPY/
sector ETFs report `totalAssets` instead). Every ETF request tripped the guard, permanently
skipping caching and DB persistence, re-hitting yfinance on every page view with zero cache
protection — the guard couldn't tell "yfinance failed" from "this ETF genuinely has none of
these three." **Fix:** added `quoteType in ("ETF", "MUTUALFUND") or totalAssets is not None`
as a fund-type carve-out — a genuinely failed fetch (`info == {}`) still correctly trips the
guard since neither signal would be present either.

### 7. All 7 Reports nav items highlighted as "current" simultaneously

`NavGroup`'s item-level `isCurrent` used `navPath(item.href)` (strips the query string) — the
7 Reports items differ ONLY by `?tab=`, so they all reduced to the same pathname and all
highlighted at once regardless of which tab was actually open. **Fix:** new `isItemCurrent()`
helper compares the query string too when an href actually has one (a no-op for every other
plain-path `NAV_GROUPS` entry); `currentSearch` (from `router.asPath`) threaded through both
`NavGroup` and `MobileNavDrawer`. Group-level `isActive` (the whole Reports group highlighting
regardless of tab) was already correct and untouched.

### 8/9. Fair Value Gap detection: cap ordering and single-bar fill requirement

`detect_fair_value_gaps()`'s `max_gaps` cap was a pure `gaps[-max_gaps:]` slice — the most
RECENTLY FORMED gaps by bar index, mixing filled/unfilled with no regard for actual relevance.
A genuinely nearest, still-actionable OLDER gap could be silently dropped if 20+ newer (even
already-filled, far-away) gaps had formed since. Separately, the fill check required a SINGLE
bar's range to span the entire gap — a gap traded through gradually over several bars (each
covering only part of the range) never satisfied that and stayed "unfilled" forever, rendering
a long-dead gap as a live level. **Fix (8):** gaps are now sorted by `(filled, distance-to-
last-close)` before the cap is applied — unfilled-and-nearest survives over filled-and-recent
— then restored to chronological order for stable rendering. **Fix (9):** replaced the single-
bar check with a cumulative contiguous-coverage tracker: extends a covered `[lo, hi]` sub-range
only when a new bar's overlap with the gap is itself contiguous with what's already covered.
Caught a real bug while implementing this fix — a first version incorrectly marked a gap
"filled" just because a LATER bar's high exceeded the gap's top, even though that bar's full
range sat entirely ABOVE the gap and never actually touched it; a dedicated test
(`test_a_bar_entirely_above_or_below_the_gap_does_not_falsely_mark_it_filled`) catches this
distinct failure mode from the "disjoint touches with an untraded middle strip" case.

### 10. Calibration mixing two score scales under one coefficient

This session's own T232 DE-parity fix (added 2026-07-17, earlier in the day) shifted
`_should_enter()`'s score scale for every trade entered from that point forward.
`calibrate_entry_weights()` persists `entry_score` verbatim and fits `w_score` across the FULL
closed-trade history with no distinction — pre- and post-change trades, on two different
score scales, mixed under one coefficient. Assessed as real but bounded (self-correcting once
enough post-change trades accumulate; the fit is already gated by "must beat baseline EV on a
held-out validation slice," which would catch a badly-mis-fit model, just not necessarily a
subtly-biased one). **Fix:** added `PaperTrade.entry_date >= date(2026, 7, 17)` to the query —
every future calibration run now trains on a single, internally-consistent score scale. Not a
schema change or a new `score_version` column — the existing `entry_date` column plus a fixed
cutoff constant was sufficient and far less invasive.

### Verification discipline applied throughout

Every fix with a plausible sabotage point was adversarially self-verified DURING
implementation, not just claimed: temporarily broke the FVG contiguity check (removed the
`lo <= covered_hi and hi >= covered_lo` guard) and confirmed the disjoint-gap test correctly
failed before restoring it; temporarily removed the K-Score `is not None` guard from an
earlier fix in the same file and confirmed 3 tests caught it (repeat of the same discipline
already documented above for `_should_enter_de_parity.py`). 24 new/updated test cases across
4 files, all passing; full existing suites (193 `market-data`, 26 `technical-analysis`, 42
frontend) stay green; frontend typecheck and a full `next build` both clean.

**Not fixed this pass — documented as known, lower-priority gaps, not silently dropped:**
`regime_min_rr_ratio` is never forwarded from `paper_trading_engine` to decision-engine (DE
falls back to a hardcoded 3.0, the fallback path uses the calibrated value) — a real
asymmetry, but assessed lower-impact than the 10 fixed here, deserving its own focused pass
rather than a rushed addition to an already-large batch. `_monitor_positions` can compute exit
logic against a stale cached price during a multi-cycle live-quote gap (a data-gap-driven
phantom stop/target, not a logic bug in the boundary check itself). The pre-market brief has
no send-state dedup (a restart within the 60s misfire-grace window could re-email every
recipient) and no per-user error isolation in its send loop (one bad address aborts the rest).
The mobile nav drawer doesn't lock body scroll, ignores the impersonation banner's height in
its `maxHeight` calc, and duplicates `GlobalSearch` (two keydown listeners, one of which tries
to focus a CSS-hidden desktop input). Trendline drawings can break across timeframe switches
(bar indices captured on one `activePrices` array reused against a different one). "Top Buys"
cards can display net-negative (net-selling) rows under a "buys" heading. `CapeResponse.latest`
is typed non-nullable but the backend can return `null` — currently harmless since the one call
site guards it, but a real type-safety gap for a future consumer.

**Standing exposure, unchanged by this pass:** 5 backend files remain hotfix-only in
production (deployed via `docker cp` during this session, never baked into a rebuilt image) —
`scheduler.py`, `email_service.py`, `paper_trading_engine.py`,
`technical-analysis/src/indicators/trendlines.py`, and its `routes.py`. Per the standing
"docker cp is a session-scoped hotfix, owed a real image rebuild" invariant documented
elsewhere in this file — this audit's own fixes to these same files are ALSO currently hotfix-
only until a real image build happens.

---

