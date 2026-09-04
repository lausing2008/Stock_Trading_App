## Feature Reference: Unusual Whales Integration Batch — Guide Examples, Squeeze Corroboration,
## Options Game Plan Surfacing, Real Expected-Move in Decision-Making (2026-09-03)

**User's request** (verbatim, following the 6-domain platform audit series): worked examples in
the Option Trading Guide for reading this app's own alerts into an entry; use Unusual Whales to
improve Short Squeeze accuracy; surface an Options Game Plan (strike/expiry/premium) on AI Signal
BUY signals, Advanced-tier gated even in email; and use UW data to improve ML/decision-making —
specifically replacing decision-engine's fabricated 2.00:1 R:R fallback with real market-implied
data. Approved as one 4-item batch ("yes all of them"), implemented and verified in priority
order below.

---

### Item #1 — Option Trading Guide worked examples (content only)

Added a new Section to `frontend/src/pages/option-trading-guide.tsx`, "Reading this app's own
alerts into an actual entry," with 3 subsections (Short Squeeze alert → entry, Gamma Unwind alert
→ entry, Dark Pool print → entry) plus a combined worked example Callout. Cross-links to
`/alerts-guide` and `/dark-pool-guide`.

Also added a "What is gamma?" explainer Callout inside the Gamma Unwind subsection (a mid-task
user question) — explains delta/gamma, why market makers hedge, and ties directly into
`gamma_flip` (the same field the Gamma Exposure panel and this batch's own expected-move work
both reference), so a reader gets the mechanism, not just the symptom.

Content-only change, no backend risk. Verified via `npx tsc --noEmit` (clean).

---

### Item #2 — Real UW short-interest as a corroboration check on the classic Short Squeeze alert

**Problem**: the classic Short Squeeze alert (`check_short_squeeze_alerts()` in
`services/market-data/src/services/scheduler.py`) computes short-percent-of-float from this
app's own free-data proxy. The 2026-08-31 five-part deep audit series (Domain 5) found real
cases (IMVT, CRWV) where that free reading diverged meaningfully from UW's own paid,
directly-reported short-interest figure.

**Fix applied**: right after the candidate dict is built (before the game-plan loop), each
candidate is now cross-checked against `unusual_whales.get_short_interest()`. When UW is
available and the relative difference between the two readings exceeds
`_SQUEEZE_UW_DISAGREEMENT_REL_THRESHOLD = 0.20` (20%), the candidate gets
`uw_short_percent_of_float` and `uw_disagrees = True` set and both readings render side-by-side
in the alert email (HTML + plain text). Wrapped in try/except — a UW lookup failure fails open
(`_uw_si = None`) and never suppresses the candidate; this is a corroboration/visibility layer
only, never a new hard gate.

**Files**: `scheduler.py` (the corroboration block + constant), `email_service.py` (the
`uw_disagree_html`/`uw_disagree_text` rendering).

**Tests**: `tests/test_short_squeeze_alert.py` — 5 new tests covering the render-both-readings
case, the no-disagreement case (no extra content), wiring-after-candidates-built, "never
suppresses, only flags," and UW-lookup-failure fail-open. Adversarially verified via 2 sabotage
cycles (removed the email rendering → 1 test failed as expected; removed the scheduler wiring →
3 tests failed as expected); both restored and confirmed byte-identical.

---

### Item #3 — Options Game Plan surfaced on the screener + BUY-signal email, Advanced-tier gated

**Scope decisions** (all explicit user choices): surface on both the scan-list/screener row AND
email alerts; gate to Advanced-tier users **even in email**, not just the interactive UI; use
the same safe ATR-based stop/target method the Short Squeeze alert's own game plan already uses
(not the live route's nearest-support/analyst-target) — the two methods are allowed to disagree,
this is not a bug; reuse the same bounded symbol set as the existing options-flow/GEX EOD
snapshots; build the screener UI as a full expandable row detail (not a simple icon/popover).

**New EOD batch job**: `compute_options_game_plan_snapshots_eod()` (scheduler.py), scheduled
17:30 ET (mon–fri), 15 minutes after the existing GEX job (17:15), maintaining the established
stagger so the 3 yfinance-options-chain-touching batch jobs never fire concurrently against the
same bounded symbol set. Reuses `_bounded_options_flow_symbols()` exactly (per the user's own
scoping choice) and the established per-symbol try/except isolation + one-commit-per-batch
pattern from `options_flow_snapshot.py`/`gex_snapshot.py`.

**New module**: `services/market-data/src/services/options_game_plan_snapshot.py` —
`compute_options_game_plan_snapshot()` fetches a real options chain, derives stop/target via
`_build_game_plan_for_style(symbol, "SWING", ...)` (the same function the Short Squeeze alert's
`_squeeze_game_plan()` already calls), and reuses `compute_options_game_plan()` (routes.py,
confirmed pure — no DB/HTTP) directly for strike/expiry/mid-price selection rather than
duplicating that logic. `upsert_options_game_plan_snapshot()` does an idempotent
`ON CONFLICT DO UPDATE` upsert on `(stock_id, as_of)`; `get_latest_options_game_plan()` reads the
most recent row.

**New table**: `OptionsGamePlanSnapshot` (`shared/db/models.py`) — mirrors `GexSnapshot`'s exact
structural convention (unique constraint on `(stock_id, as_of)`); relies on `create_all()`'s
"creates missing tables" behavior, matching `GexSnapshot`'s own precedent (no dedicated migration
file).

**Email integration**: `check_signal_alerts()` (scheduler.py) — on a BUY transition, the
recipient is checked (`alert.user.role == UserRole.ADMIN or alert.user.tier ==
UserTier.ADVANCED`) BEFORE the snapshot is even looked up; only Advanced-tier/admin recipients get
the block rendered, matching the user's explicit "Advanced user even in email" requirement. This
required new plumbing — background scheduler jobs iterate `User` rows directly (no FastAPI
`Depends`), so this is the first place tier-gating had to be applied by hand rather than via
`get_advanced_user()`.

**Screener UI**: `frontend/src/pages/screener.tsx` — new "Options Plan" column (Advanced-tier
only), batch-fetched via a new `GET /options-game-plan/batch?symbols=...` route
(`get_advanced_user()`-gated, reads snapshots only — never a live fetch) for all currently-visible
BUY rows in one call. Clicking "📊 Plan ▼" expands a full detail row (protective put / covered
call strike, expiry, mid-price, floor/cap) via `<React.Fragment key={row.symbol}>` (a shorthand
`<>` fragment can't carry a `key`, required when returning a fragment from inside `.map()`).

**Tests**: `test_options_game_plan_email.py` (10), `test_options_game_plan_snapshot.py` (5),
`test_options_game_plan_batch_route.py` (6) — tier gate, snapshot-read-never-live-fetch,
BUY-only scoping, fail-open, rate-limit sleep, scheduling stagger, route-path
literal-segment safety (confirmed safe from router-ordering/catch-all shadowing — see
`docs/incidents/router-ordering-catchall-shadowing.md`). Adversarially verified via sabotage
cycles. Also fixed 2 pre-existing tests broken by fallout from the new
`options_game_plan=options_game_plan,` email kwarg (`test_alerts_env_gate.py`'s job-id
classification list, `test_alert_dedup_and_isolation_fixes.py`'s hardcoded source-slice offset).

---

### Item #4 — AUD-DECIDE4-EXPECTEDMOVE: real UW-derived expected move replaces the fabricated
### 2.00:1 R:R fallback in decision-making

**Root cause** (confirmed via the Domain 2 platform audit, 2026-09-03): the dominant real
decision-engine reject reason was a fabricated 2.00:1 reward:risk ratio, traced to
`_build_game_plan_for_style()`'s `_STYLE_PARAMS["SWING"]` (`stop_pct=0.945`,
`default_tp_pct=1.12` → R:R ≈ 2.18). `take_profit` was found to be **always** the fixed
percentage regardless of ATR availability — only `stop` had an ATR-based branch — a broader gap
than "no-ATR fallback" alone implied.

**Data source**: real, per-symbol implied volatility from UW's `/api/stock/{ticker}/iv-rank` —
confirmed via WebFetch against UW's own published API operation doc (fields: `close`, `date`,
`iv_rank_1y`, `updated_at`, `volatility`). Not previously wired anywhere in this codebase; UW's
own GEX/greek-exposure endpoints were checked first and confirmed to lack IV entirely.

**Formula**: `expected_move_pct = iv_fraction * sqrt(dte/365) * 100`, using a fixed 30-day
reference window (`_EXPECTED_MOVE_REFERENCE_DTE`, matching SWING's own typical hold-period order
of magnitude) rather than any one listed contract's expiry, since IV rank is a continuous
per-symbol reading. Includes a defensive fraction-vs-percent normalization (`volatility > 10.0`
→ treat as a percent, divide by 100) since UW's own spec doesn't state the unit — **flagged as
needing re-verification against a real live UW response once a subscription is active, not to be
silently trusted.**

**Architecture**: computed once per symbol per day inside the existing Options Game Plan EOD
batch (item #3's own infrastructure), NOT via a live per-candidate fetch —
`_build_game_plan_for_style()` runs inside the entry-scan **hot loop**, and a live fetch there
would repeat the exact rate-limit-amplification shape
`docs/incidents/yfinance-rate-limit-amplification.md` already warns against.
`_build_game_plan_for_style()` gained optional `session`/`stock_id` params (default `None`,
fully backward-compatible — the other 3 call sites, `scheduler.py`'s `_squeeze_game_plan()`,
`conditional_orders.py`, and `options_game_plan_snapshot.py`'s own internal call, are
deliberately left unmodified and unaffected); when both are supplied, it reads YESTERDAY's
snapshot's `expected_move_pct` via `get_latest_options_game_plan()`.

**Take-profit/stop combination logic** (explicit user direction, after I flagged this as a real
design choice): a real expected move **replaces the fixed take-profit target outright** — no
`max()` floor against the fixed default, since overriding an honestly-closer real target with a
more aggressive fixed guess would defeat the point. The **stop** side keeps its existing
safety-floor treatment: `max(atr_or_expected_move_stop, fixed_pct_stop)` — the user did not ask
to change stop's own floor behavior, only take-profit's.

**New model fields**: `OptionsGamePlanSnapshot.expected_move_pct`, `.expected_move_dte`
(both nullable) — `shared/db/models.py`.

**New UW function**: `get_iv_rank(symbol)` / `IVRankData` dataclass — `unusual_whales.py`,
mirrors `get_gex_levels()`'s exact fail-open/caching pattern (`_IV_RANK_TTL = 900`, 15 min).

**Bug found and fixed during this work**: `get_iv_rank()` initially had a dead double-unwrap of
UW's response envelope (`data.get("data") if isinstance(data, dict) else data`) — `_get()`
already unwraps the real `{"data": [...]}` envelope once, so `data` was already the row list by
the time `get_iv_rank()` received it. Harmless in practice (fell through to `else data`, which
still worked), but cleaned up to `rows = data` directly, matching `get_gex_levels()`'s own
cleaner handling.

**Tests**: 22 new — `test_unusual_whales.py` (9, `get_iv_rank()` parsing/caching/fail-open/TTL),
`test_game_plan_expected_move.py` (8, `_build_game_plan_for_style()`'s new branch logic:
backward-compat with no session/stock_id, replace-not-max for take-profit, ATR-still-wins-over-
expected-move for stop, fixed-percentage floor still applies, no-snapshot/None/lookup-failure
fallbacks), `test_options_game_plan_expected_move.py` (5, the batch computation itself: real
fractional IV, percent-style IV normalization, missing/zero IV, lookup-failure fail-open leaving
the rest of the snapshot intact). Adversarially verified via 3 sabotage cycles (take-profit
replace-logic, IV unit normalization, first-row-vs-last-row selection) — all correctly caught,
all restored byte-identical. Full market-data suite: 2546 passed.

**What to check if this looks wrong**: once a real UW subscription is active, pull one live
`/api/stock/{ticker}/iv-rank` response and confirm whether `volatility` is actually a fraction or
a percent — the `> 10.0` normalization guard in `options_game_plan_snapshot.py` is a defensive
guess, not a confirmed fact.

---

### AUD-IVRANK — Wire `iv_rank_1y` through the same surfaces, plus a real explainer (2026-09-03)

**User follow-up**: after item #4 above shipped, the user asked what "IV Rank" actually is, then
asked to both wire it into the UI and document it. UW's own `/iv-rank` endpoint (already fetched
for `expected_move_pct`, no extra call) also returns `iv_rank_1y` — a 0-100 percentile of where
today's IV sits within this symbol's own trailing 1-year range. This field had been captured on
the `IVRankData` dataclass all along but never persisted, never surfaced through any API route,
and never rendered anywhere — a real gap independent of the `expected_move_pct`/
`expected_move_dte` work.

**Also found and fixed in the same pass**: `expected_move_pct`/`expected_move_dte` themselves had
the identical gap — computed and persisted onto `OptionsGamePlanSnapshot` by item #4, but
`get_options_game_plan_batch()` (the route the screener/email actually read from) never included
either field in its response shape. Both fields were silently dead on arrival until this fix.

**Model**: new `OptionsGamePlanSnapshot.iv_rank_1y` field (`shared/db/models.py`), captured
independently of `expected_move_pct`'s own `volatility > 0` gate — `iv_rank_1y` is still a real,
useful reading even on the rare occasion `volatility` itself comes back null/zero.

**Computation**: `options_game_plan_snapshot.py`'s `compute_options_game_plan_snapshot()` now
also reads `_iv_data.iv_rank_1y` from the same `get_iv_rank()` call already made for
`expected_move_pct` — genuinely zero extra API cost.

**API**: `get_options_game_plan_batch()` (`routes.py`) now includes `expected_move_pct`,
`expected_move_dte`, and `iv_rank_1y` in its per-symbol response shape (previously omitted
entirely). `frontend/src/lib/api.ts`'s `OptionsGamePlanSnapshotResult` type extended to match.

**Email gate relaxed** (`AUD-IVRANK-EMAILGATE`, `scheduler.py`'s `check_signal_alerts()`): the
existing gate only passed a snapshot through to the email if it had a real put or call leg. A
symbol with real IV/IV-Rank data but no listed contract in today's DTE window would have that
real, useful reading silently dropped along with the (legitimately absent) legs. Gate widened to
`put_strike is not None or call_strike is not None or expected_move_pct is not None or
iv_rank_1y is not None` — any one of the four is independently worth showing.

**Rendering**: `email_service.py`'s `send_signal_alert_email()` gained a new "📈 Implied
Volatility" row (rendered whenever `expected_move_pct` or `iv_rank_1y` is present, independent of
whether either leg exists) showing `Expected move ±X.X% (Nd)` and/or `IV Rank NN/100 (<reading>)`
— the reading label is `"options relatively expensive"` at IV Rank ≥70, `"options relatively
cheap"` at ≤30, `"mid-range"` in between. `screener.tsx`'s expandable row detail gained a matching
indigo-bordered "📈 Implied Volatility (Unusual Whales)" box using the same thresholds/labels.

**Documentation**: `option-trading-guide.tsx`'s "How the Options Game Plan card works" section
gained a new subsection, "Implied Volatility and IV Rank (screener + BUY-signal email, Advanced
tier)," explaining both concepts and their practical use (high IV Rank favors selling premium,
low IV Rank favors buying it) — explicitly framed as a read on the OPTIONS' own pricing, not a
buy/sell signal on the underlying stock.

**Tests**: 12 new — `test_options_game_plan_expected_move.py` gained 3 (`iv_rank_1y` captured
from the same fetch, captured independently of a null/zero `volatility`, `None` when UW has no
data), `test_options_game_plan_email.py` gained 5 (both fields render alongside legs, correct
low/high/mid-range labeling, the row renders even with zero legs, the section stays fully absent
when neither IV data nor legs exist, source-text confirmation of the relaxed scheduler gate),
`test_options_game_plan_batch_route.py` gained 1 (all 3 new fields present in the route's
response construction). Also fixed a pre-existing test fixture gap: `test_options_game_plan_
expected_move.py`'s `_FakeIVRank` mock lacked an `iv_rank_1y` attribute entirely, so the new
`_iv_data.iv_rank_1y` read raised an uncaught `AttributeError` inside the existing broad
try/except — silently wiping `expected_move_pct` too on 2 pre-existing tests until the fixture
was corrected to carry the attribute. Adversarially verified via 3 sabotage cycles (email
rendering removed, scheduler gate reverted, batch-route fields removed) — all correctly caught,
all restored byte-identical. Full market-data suite: 2562 passed; frontend `tsc --noEmit` and
`next build` both clean, confirmed shipped in the compiled `option-trading-guide`/`screener`
bundles via grep.

---

### AUD-GREEKS — Per-strike Greeks (delta/gamma/theta/vega/vanna/charm) for the Game Plan's
### exact selected contracts (2026-09-03)

**User request**: after a design review of Unusual Whales' full API surface (published as its
own artifact), the user asked to build the proposed features one by one, starting with this one
— closing a gap the app's own Options Trading Guide explicitly documented ("no real per-contract
Greeks beyond implied volatility are shown"). Real field shape confirmed via a direct WebFetch
against UW's own published operation doc for `/api/stock/{ticker}/greeks` (fetched 2026-09-03):
per-strike rows with `call_delta/gamma/theta/vega/vanna/charm` and the put-side equivalents.

**New UW function**: `get_greeks(symbol, expiry)` / `StrikeGreeks` dataclass
(`unusual_whales.py`) — returns a list of per-strike rows for one expiry (never `None`, matching
`get_flow_alerts()`'s own list-returning fail-open contract), Redis-cached 15 min per
`(symbol, expiry)` pair (`_GREEKS_TTL`).

**Computation**: `compute_options_game_plan_snapshot()` (`options_game_plan_snapshot.py`) calls
`get_greeks()` once per DISTINCT expiry actually in use (put/call legs frequently share the same
expiry, so usually one call, never more than two), then matches the returned rows in-memory to
the EXACT strike `compute_options_game_plan()` already selected for each leg. Isolated in its own
try/except — a failure here only costs the 12 Greek fields, never the rest of the snapshot.

**Bug found and fixed during this work**: the first implementation used
`_greeks_cache.setdefault(expiry, _uw.get_greeks(symbol, expiry))` to memoize the per-expiry
fetch — but `dict.setdefault()`'s default-value argument is evaluated eagerly regardless of
whether the key already exists, so `get_greeks()` was actually called twice even when
`put_exp == call_exp` (the common real case), defeating the whole point of memoizing it. Caught
immediately by `test_same_expiry_for_put_and_call_only_fetches_greeks_once` before this ever
shipped. Fixed with an explicit `if expiry not in cache` check instead of relying on
`setdefault`'s (non-lazy) default-argument evaluation.

**Model**: 12 new nullable fields on `OptionsGamePlanSnapshot` — `put_delta/gamma/theta/vega/
vanna/charm` and the `call_*` equivalents.

**API**: `get_options_game_plan_batch()` nests the 6 Greeks inside each leg's own
`protective_put`/`covered_call` object (not top-level snapshot fields, since they're specific to
that leg's contract) — `frontend/src/lib/api.ts`'s `OptionsGamePlanSnapshotLeg` type extended
to match.

**Rendering**: `screener.tsx`'s expandable row detail gained a compact "Δ Γ Θ V" line under each
leg (only rendered when at least one of delta/theta/vega is present); `email_service.py`'s
`send_signal_alert_email()` gained a matching `(Δ -0.45 Θ -0.04 V 0.11)` suffix appended to each
leg's existing HTML/text row. `option-trading-guide.tsx`'s "What this does NOT do" callout —
which previously said outright "no real Greeks... this app doesn't compute or source true option
Greeks" — was corrected (that claim is no longer true) and a new subsection added explaining
delta/gamma/theta/vega in plain terms and how each relates to a protective put/covered call
specifically.

**Tests**: 20 new — `test_unusual_whales.py` gained 9 (`get_greeks()` parsing/caching/multi-
strike/fail-open/TTL/cache-key-scoped-per-expiry), a new `test_options_game_plan_greeks.py` (7,
behavioral: exact-strike matching, single-fetch-for-shared-expiry, two-fetches-for-different-
expiries, no-match-leaves-null, empty-response, fetch-exception-fails-open, put-only-never-
fetches-call-side — this file's 2nd test is the one that caught the `setdefault` bug above),
`test_options_game_plan_batch_route.py` gained 1 (all 12 Greek fields present in both legs'
response construction), `test_options_game_plan_email.py` gained 3 (suffix renders when present,
omitted entirely — no empty parens — when absent, independent per leg so one leg's Greeks never
leak into the other's row). Adversarially verified via 2 sabotage cycles on the strike-matching
logic (return `rows[0]` unconditionally instead of matching strike — 2 tests failed correctly)
and 2 more on the email suffix (function renamed, guard condition disabled — both caught
correctly), all restored byte-identical. Full market-data suite: 2582 passed; frontend
`tsc --noEmit`/`next build` clean, confirmed shipped in the compiled `option-trading-guide`/
`screener` bundles via grep.

---

### AUD-MAXPAIN — Real Max Pain + OI-per-strike wired into the price chart and Market
### Pressure panel (2026-09-03)

**User request**: second feature from the Unusual Whales API design review, in the agreed build
order — "Real Max Pain + OI-wall levels on the price chart." Real field shapes confirmed via
direct WebFetch against UW's own published operation docs (fetched 2026-09-03):
`/api/stock/{ticker}/max-pain` (per-expiry `{expiry, max_pain}` array) and
`/api/stock/{ticker}/oi-per-strike` (per-strike `{strike, call_oi, put_oi}`, across all
expiries).

**Conceptual distinction from existing GEX fields** (documented inline at every layer): max pain
is the strike where option WRITERS, in aggregate, lose the least at expiry — a real, independent
magnet-effect theory, distinct from `call_wall`/`put_wall`/`gamma_flip` (which describe dealer
HEDGING pressure, not option-writer P&L). OI-per-strike is the raw, unweighted open-interest
count per strike — the actual number `call_wall`/`put_wall` only imply indirectly (those are
gamma-weighted, not a plain OI count).

**New UW functions**: `get_max_pain(symbol)` / `MaxPainRow` and `get_oi_per_strike(symbol)` /
`OIPerStrikeRow` (`unusual_whales.py`) — both list-returning, fail-open (never `None`, matching
`get_flow_alerts()`'s/`get_greeks()`'s own contract), 15-min Redis cache
(`_MAX_PAIN_TTL`/`_OI_PER_STRIKE_TTL`).

**Wiring**: unlike items #1-3 (which live in the daily `OptionsGamePlanSnapshot` batch), this
route (`GET /{symbol}/gamma-exposure`) was already a **live, per-request** UW fetch with no
existing DB persistence — so both new fields were added directly to that same live route rather
than introducing a new batch job, matching its existing architecture exactly. Both fetches happen
independently after the existing GEX availability gate; a max-pain/OI-per-strike fetch failure
never blocks the GEX fields from still being returned (each list simply comes back empty).

**Rendering**:
- `PriceChart.tsx` gained a new `maxPainLevel` prop, rendered as a distinct dotted purple
  `createPriceLine` (daily mode only) — the same mechanism already used for every other flat
  level on this chart (S/R, FVGs, game-plan entry/stop/target). Drawn even when a Game Plan
  overlay is active, since max pain is a genuinely different KIND of level (an options-market
  structural reading, not a trade plan), not a competing entry/stop/target.
- `stock/[symbol].tsx` added its own `useSWR` fetch for gamma exposure (same cache key as
  `MarketPressurePanel`'s own fetch — SWR dedupes identical concurrent keys, so this doesn't
  double the real network call) specifically to extract the nearest-expiry `max_pain` value as a
  prop for the chart, since `MarketPressurePanel` doesn't expose its own fetch result to its
  parent.
- `MarketPressurePanel.tsx`'s existing GEX numbers card gained a 5th row ("Max pain (expiry)"),
  plus a new sibling card showing the top 6 strikes by total (call+put) open interest — real,
  unweighted OI counts, distinct from the existing per-expiration OI-concentration table already
  on this panel (that one's a different, already-existing free-tier proxy).

**Types**: `frontend/src/lib/api.ts`'s `GammaExposure` type extended with `max_pain: MaxPainRow[]`
and `oi_per_strike: OIPerStrikeRow[]`.

**Tests**: 17 new — `test_unusual_whales.py` gained 12 (6 each for `get_max_pain()`/
`get_oi_per_strike()`: not-available, cache-hit, multi-row parsing, non-list response, fetch
exception, own TTL constant — plus a cache-key-scoping check for OI-per-strike), and
`test_gamma_exposure_route.py` gained 5 (both functions called via the real UW module path,
fetched only after the availability gate, both new list fields present in the response, both
correctly filter out null-valued rows rather than passing through fabricated-looking nulls).
Adversarially verified via 2 sabotage cycles (removed the null-filtering guards on both new
response fields — 2 tests failed correctly; forced `max_pain` to always parse as `None` — 1 test
failed correctly), both restored byte-identical. Full market-data suite: 2599 passed; frontend
`tsc --noEmit`/`next build` clean, confirmed shipped in the compiled `stock/[symbol]` page
bundle via grep.

---

### AUD-NOPE — Real, live delta-weighted directional options pressure (2026-09-03)

**User request**: third feature from the UW API design review, item #3 of 7 in the agreed build
order. This one carried an open design question flagged in the original review: NOPE is
published per-MINUTE by UW, unlike every other field this app consumes (all daily-batch) — the
cadence had to be resolved before building. User asked for the recommendation that best serves
them; the answer was **live per-page-view fetch**, not a daily batch and not a new polling job:
NOPE only matters when someone is actually looking at a stock's page right now (same reasoning
already applied to GEX/Max Pain/OI-per-strike on this exact route), a daily batch would only ever
show yesterday's stale reading (defeating the entire point of a live intraday gauge), and a
polling job would burn API budget refreshing symbols nobody is currently viewing.

**Real field shape** confirmed via WebFetch against UW's own published operation doc (fetched
2026-09-03): `GET /api/stock/{ticker}/nope`, single-object response (not an array, unlike
max-pain/oi-per-strike) — `call_delta`, `call_fill_delta`, `call_vol` (int), `nope` (string),
`nope_fill` (string), `put_delta`, `put_fill_delta`, `put_vol` (int), `stock_vol` (int),
`timestamp` (ISO 8601, start-of-minute).

**Conceptual distinction from this app's own existing Pressure score**
(`compute_options_pressure_score()`, routes.py): that score is built from raw call/put premium
ratio and volume/OI ratio; NOPE weights by each option's actual DELTA — a more
theoretically-grounded read of real directional exposure, and a genuine second, independently-
computed cross-check, not a duplicate.

**New UW function**: `get_nope(symbol)` / `NopeReading` (`unusual_whales.py`) — single-object,
fail-open to `None` (matching `get_gex_levels()`'s own contract, not the list-returning contract
of `get_max_pain()`/`get_oi_per_strike()`, since this endpoint returns one current reading).
**New, deliberately short TTL**: `_NOPE_TTL = 60` (1 minute) — every other UW field on this route
uses a 15-min TTL, which here would serve a stale intraday snapshot for 15x longer than the data
itself remains valid; still real caching (not zero), so this route's own multiple UW calls in one
page load never double-spend API budget on identical data within the same minute.

**Wiring**: added to the same live `GET /{symbol}/gamma-exposure` route as Max Pain/OI-per-strike
(item #2) — fetched independently after the existing availability gate, fails open to `null`
without blocking the GEX/Max Pain/OI fields from still returning.

**Rendering**: `MarketPressurePanel.tsx` gained a new card — a bullish/bearish label, the raw
`nope` value, a visual gauge bar (bidirectional from center, green right / red left), and the
`nope_fill` variant shown alongside since UW documents neither as strictly superior to the other.
`option-trading-guide.tsx` gained a new "Market Pressure panel" section explaining Max Pain, OI
walls, and NOPE together (folding in the item #2 explainers that hadn't been written into the
guide yet either) — each framed as a genuinely distinct lens, not a duplicate of GEX or of each
other.

**Types**: `frontend/src/lib/api.ts`'s `GammaExposure` type extended with `nope: NopeReading |
null`; new `NopeReading` type added.

**Tests**: 13 new — `test_unusual_whales.py` gained 8 (not-available, cache-hit, real dict-shape
parsing, defensive list-shape parsing matching `get_gex_levels()`'s own precedent, missing-
symbol, fetch-exception, the short-TTL assertion itself — explicitly checked against both the
constant and the literal `60`, plus a negative-cache-entry check), `test_gamma_exposure_route.py`
gained 5 (fetched via the real function, fetched only after the availability gate, the response
includes a `nope` field, that field degrades to `null` when either the reading itself or its own
`nope` value is missing, and every one of the 8 sub-fields is surfaced — not just the headline
number). Adversarially verified via 2 sabotage cycles (collapsed the route's nope object down to
one field, forced `get_nope()`'s own `nope` field to always parse `None`) — both caught cleanly,
both restored byte-identical. Full market-data suite: 2612 passed; frontend `tsc --noEmit`/
`next build` clean, confirmed shipped in the compiled `option-trading-guide`/`stock/[symbol]`
bundles via grep.

---

### AUD-EARNINGSMOVE — Real historical expected-move / post-earnings-move data on the Earnings
### Calendar (2026-09-04)

**User request**: fourth feature from the UW API design review, item #4 of 7 in the agreed build
order. Real field shape confirmed via WebFetch against UW's own published operation doc for
`GET /api/earnings/{ticker}` (fetched 2026-09-04): per-historical-report `expected_move`/
`expected_move_perc` (the pre-report, options-market-implied forecast) paired with
`post_earnings_move_1d`/`1w`/`2w`/`3d` and `pre_earnings_move_1d`/`1w`/`2w`/`3d` (what the stock
actually did) — every value a string in the real response.

**Conceptual distinction from AUD-DECIDE4-EXPECTEDMOVE** (documented inline): that earlier work's
`expected_move_pct` is a GENERIC 30-day reference-window figure derived from `get_iv_rank()`,
computed for any symbol on any day, feeding the paper-trading engine's stop/target math. This is
a real, EARNINGS-SPECIFIC expected move for one specific historical report, paired with the
actual outcome — "was the options market's fear justified THIS time," a genuinely different
question the Earnings Calendar previously had no answer for at all (it only had backward-looking
EPS beat-rate/surprise stats, nothing about the stock's own PRICE reaction to a report).

**New UW function**: `get_historical_earnings_moves(symbol, limit=8)` /
`HistoricalEarningsMoveRow` (`unusual_whales.py`) — list-returning, fail-open (never `None`,
matching `get_max_pain()`'s own contract), sorted most-recent-first, capped at `limit` (default 8,
matching this app's own existing `eps_beat_rate` 8-quarter-lookback convention). Deliberately
kept only 7 of the real response's 20+ fields (report_date/report_time/expected_move/
expected_move_perc/post_earnings_move_1d/1w/source) rather than surfacing every raw field —
those are the ones that actually answer the "was the fear justified" question; the rest (2w/3d
variants, straddle pricing) were left unmapped as not yet needed. **New TTL**: `_EARNINGS_MOVE_TTL
= 21600` (6h, matching `_SHORT_INTEREST_TTL`'s own rationale) — this is historical per-report
data that only grows once per quarter per symbol, not a fast-moving reading like GEX/NOPE.

**Wiring**: `events_calendar()` (`routes.py`) — the same function AUD-EARNINGSCAL-MARKETESTIMATES
already extended with `eps_beat_rate`/analyst consensus — gained a new call to
`get_historical_earnings_moves()`, scoped identically to the existing analyst-consensus call
(only for symbols with a real near-term earnings event in the requested window, never the full
active-stock universe this loop otherwise iterates). `earnings_expected_move_perc` (the next
report's forecast, taken from the most recent historical row since UW doesn't publish a separate
forward-only forecast endpoint) and `earnings_move_history` (the full capped list) both added to
the earnings event dict.

**Rendering**: `earnings.tsx` gained an "Expected move: ±X.X%" chip next to the existing analyst-
target/beat-history row, plus a new "Past moves (expected → actual)" strip showing up to 4 prior
quarters, colored by whether the actual 1-day move was positive or negative.
`option-trading-guide.tsx` gained a new subsection explaining the concept and its distinct value
from the existing beat-rate stat — explicitly framed as "was the market's own fear historically
accurate for this stock," relevant context before paying for a pre-earnings straddle or
protective put at the currently-quoted premium.

**Types**: `frontend/src/lib/api.ts`'s `EventCalendarItem`-equivalent type extended with
`earnings_expected_move_perc`/`earnings_move_history`; new `EarningsMoveHistoryRow` type added.

**Tests**: 16 new — `test_unusual_whales.py` gained 10 (not-available, cache-hit, real-response
parsing, most-recent-first sorting, limit-capping at both a custom value and the default 8,
malformed-row (no `report_date`) skipping without crashing, non-list response, fetch exception,
own 6h TTL distinct from NOPE's 60s), `test_earnings_calendar_market_estimates.py` gained 6
(called only inside the earnings block — not once per stock in the outer loop — call happens
before the event dict is appended, both new fields present, the expected-move field reads the
most recent row rather than a hardcoded value, the history list correctly maps all 3 sub-fields,
the `unusual_whales` module is imported before the symbol loop rather than inside it).
Adversarially verified via 2 sabotage cycles (both new response fields hardcoded to
empty/`None` — 3 tests failed correctly; the sort call removed from `get_historical_earnings_
moves()` — 1 test failed correctly), both restored byte-identical. Full market-data suite: 2628
passed; frontend `tsc --noEmit`/`next build` clean, confirmed shipped in the compiled
`earnings`/`option-trading-guide` bundles via grep.

---

### AUD-TRANSCRIPT — Earnings call transcript → LLM impact analysis (2026-09-04)

**User request**: fifth feature from the UW API design review, item #5 of 7 — the one explicitly
flagged in the original design doc as needing extra verification (the `statements` array's own
per-item shape was undocumented on UW's rendered docs page). Real field shape was confirmed by
pulling UW's own **full published OpenAPI YAML spec directly** (not the rendered docs page, which
showed only a `[null]` placeholder) — the real `Transcript Statement` schema has exactly 4 fields:
`content` (statement text), `sentiment` (UW's own per-statement score), `speaker` (name), `title`
(role, e.g. "CEO").

**Two real risks surfaced during research, both explicitly raised with the user before building**:
1. This specific UW endpoint's own docs state it **"Requires Advanced+ tier (Advanced, Enterprise,
   or Enterprise + Kafka)"** — a higher UW subscription level than every other endpoint this app
   uses assumes. Resolved by building anyway with the existing fail-open pattern: a 403 from an
   insufficient tier is caught by `_get()`'s own `UnusualWhalesAuthError` handling and degrades
   to an empty list, indistinguishable at the call site from "no transcript published yet" — this
   app deliberately never tries to tell the two apart.
2. UW requires an exact `"YYYYQ[1-4]"` quarter string, but this codebase's own `fiscal_year`/
   `fiscal_quarter` fields are already documented (AUD264) as "best-effort calendar-month label,"
   not reliably correct. Resolved by deriving the quarter directly from `report_date` (this
   codebase's own established reliable identity for a specific earnings event) via simple
   calendar-quarter math instead — a wrong guess for a company whose fiscal quarters don't align
   to calendar quarters fails open the same way every other failure mode here does (UW simply
   returns no transcript for a quarter string it doesn't recognize).

**New UW function**: `get_earnings_transcript(symbol, quarter)` / `TranscriptStatement`
(`unusual_whales.py`) — list-returning, fail-open, 24h cache (`_TRANSCRIPT_TTL` — a published
transcript is a fixed historical record, unlike GEX/NOPE's fast-moving readings). New helper
`earnings_quarter_from_report_date()` derives the quarter string.

**New market-data route**: `GET /{symbol}/earnings-transcript?report_date=YYYY-MM-DD` — the
cross-service entry point event-intelligence (a separate service/container with no direct Python
import path to `unusual_whales.py`) calls over HTTP, matching the established
`_fetch_fundamentals_sync()`-style pattern exactly.

**LLM integration** (`event-intelligence/earnings.py`): `generate_earnings_impact()` gained an
OPTIONAL `transcript_statements` param (default `None`, fully backward-compatible — every
pre-existing caller unaffected). New `_select_transcript_excerpts()` picks a bounded,
sentiment-ranked slice (max 20 statements, max 4000 total chars, each statement capped at 400
chars) to keep prompt size/cost predictable regardless of how long a call ran — full transcripts
can run to hundreds of statements. The LLM's existing response schema gained a new
`management_tone` field: a genuinely qualitative read (confident/defensive/evasive) grounded in
the actual transcript words, empty string when no excerpts were given or none supported a clear
read — the system prompt explicitly forbids inventing a tone the excerpts don't support.

**Wiring**: `check_earnings_impact_poll()` now fetches the transcript (via a new
`_fetch_transcript_statements_sync()` helper, run inside the existing `_executor` like every
other blocking call in this file) before calling `generate_earnings_impact()`, and writes the
returned `management_tone` to the new `EarningsEvent.management_tone` column.

**New model field**: `EarningsEvent.management_tone` (nullable `Text`) — needs a manual
`ALTER TABLE` in every environment (an existing, already-populated table; `create_all()` only
creates missing tables).

**Delivery**: `check_earnings_impact_alerts()` (scheduler.py) — the email delivery half — gained
a `Management tone: ...` line, rendered only when present, inserted between the existing
LLM-generated impact paragraph and the mechanical earnings playbook (never replacing either).
No frontend rendering exists for this feature at all — matching the pre-existing `impact_text`
field's own delivery-only-via-email precedent (this app's earnings LLM impact feature has never
had a frontend surface).

**Guide**: `option-trading-guide.tsx` gained a new subsection explaining what this line is, why
it's genuinely different from the numeric beat/miss read, and its honest-when-unavailable
behavior (no placeholder line when a transcript wasn't available).

**Tests**: 24 new in market-data (15 in `test_unusual_whales.py` — 6 for
`earnings_quarter_from_report_date()`'s calendar-quarter-boundary math including the exact
month-3/month-4 boundary, 9 for `get_earnings_transcript()`'s parsing/caching/fail-open
including a dedicated 403/`UnusualWhalesAuthError` case; 9 in a new
`test_earnings_transcript_route.py` — availability gate ordering, quarter derived from
`report_date` never `fiscal_year`/`fiscal_quarter`, invalid-date/no-data/disabled all degrading
to `available: False` with an honest `reason`, an empty statements list never passing through as
`available: True`) plus 2 more in `test_earnings_playbook.py` for the email's new tone line.
event-intelligence gained 13 new tests in `test_earnings_impact.py` (8 for
`_select_transcript_excerpts()` — sentiment-ranking, content/sentiment-missing filtering,
statement-count cap, total-char cap, per-statement truncation, missing-speaker/title handling,
empty input, non-dict-row skipping; 3 for `generate_earnings_impact()`'s transcript-augmented
behavior — omitted param is byte-identical to before, provided excerpts are actually folded into
the real prompt sent to Claude, an empty list produces no transcript block at all; 3 for the
poll's own wiring — transcript fetched before the LLM call, passed through correctly, written to
the new DB column) plus 1 corrected pre-existing test whose hardcoded exact-string assertion
needed updating for the new intermediate `management_tone` field. Adversarially verified via 4
sabotage cycles across both services (quarter-math off-by-one on the calendar boundary,
sentiment-ranking sort key reverted to non-absolute-value, the DB-write line removed entirely,
the html/text tone-guards both individually removed and re-verified with a tightened test after
the first sabotage attempt was caught by a still-passing sibling guard rather than the mutated
one) — all correctly caught, all restored byte-identical. Full suites: market-data 2654 passed,
event-intelligence 353 passed; frontend `tsc --noEmit`/`next build` clean, confirmed shipped in
the compiled `option-trading-guide` bundle via grep.

**What to check once a real UW Advanced+ subscription is active**: pull one live
`GET /api/companies/{ticker}/transcripts/{quarter}` response and confirm the `Transcript
Statement` shape matches exactly (`speaker`/`title`/`content`/`sentiment`) — this was derived
from UW's own published OpenAPI spec, not a live sample, so it's a real documented contract but
not yet confirmed against an actual response.

---

### AUD-SEASONALITY — Sector calendar-effects seasonality panel (2026-09-04)

**User request**: sixth and final feature from the UW API design review, item #6 of 7 — the
least-verified item at design time (only the category's existence was confirmed, no field
shapes). Fully verified this pass by pulling UW's own complete published OpenAPI YAML spec
directly and locating all 4 real seasonality endpoints with exact paths, params, and — for
`/api/seasonality/market` specifically — a complete schema WITH real example data:
`GET /api/seasonality/market` returns average/median/min/max return, positive-close count,
positive-months %, and years of history, per (ticker, month), for UW's own fixed 13-ticker
sector/index ETF set (SPY, QQQ, IWM, XLE, XLC, XLK, XLV, XLP, XLY, XLRE, XLF, XLI, XLB).

**Conceptual distinction from the existing sector-rotation feature** (documented inline):
`sector_trajectory.py`/`/sector-rotation` is K-Score-momentum-based — "who's outperforming right
now." This is calendar-effects-based — "who has historically tended to do well in THIS calendar
month, independent of current momentum." Genuinely complementary, never a replacement.

**New UW function**: `get_sector_seasonality()` / `SeasonalityRow` (`unusual_whales.py`) — takes
NO parameters (a real, deliberate API shape difference from every other UW function in this
codebase — UW returns its full 13-ticker × 12-month matrix, up to 156 rows, in one call), list-
returning, fail-open, 24h cache (`_SEASONALITY_TTL` — genuinely multi-year historical stats, no
reason to re-fetch more than daily).

**New route**: `GET /sector-seasonality?month=N` (routes.py) — defaults `month` to the current
calendar month when omitted (the reading most people actually want), filters the full fetched
matrix down to just that month, sorts by median return descending, and falls back to
`available: False` with an honest reason (`unusual_whales_disabled`/`no_data`) rather than a
fabricated ranking.

**Rendering**: `sector-rotation.tsx` gained a new self-contained `SeasonalityPanel` component
(own fetch, matching `MarketPressurePanel.tsx`'s own established "renders nothing when
unavailable" pattern) — a table of median/average return, % positive months, min/max range, and
years of history per sector, joined to sector NAMES via the existing `SectorRotationEntry.etf`
field (UW's own ticker naming matches this app's existing ETF map exactly, no translation table
needed). Gated to `market === 'US'` only — UW's fixed 13-ticker set is entirely US ETFs; showing
it on the HK sector view would misleadingly imply US seasonality applies there.

**Tests**: 19 new — `test_unusual_whales.py` gained 9 (not-available, cache-hit, real-response
parsing using UW's own published example row, multi-ticker/multi-month parsing, non-list
response, fetch exception, non-dict-row skipping, a dedicated check confirming the function
takes zero arguments — the one real API-shape difference from every sibling function — and its
own 24h TTL), a new `test_sector_seasonality_route.py` (10 — disabled/no-data both degrading to
`available: False` with honest reasons, month defaulting to the current calendar month, correct
month-filtering, ticker-null-row filtering, descending sort by median return, all 8 stat fields
surfaced, an empty-rows list never passing through as `available: True`, availability checked
before any fetch). Adversarially verified via 2 sabotage cycles (the route's own filtering/
no-data-guard logic stripped out entirely — 4 tests failed correctly; `ticker` forced to always
parse `None` in the UW function itself — 3 tests failed correctly), both restored byte-identical.
Full market-data suite: 2673 passed; frontend `tsc --noEmit`/`next build` clean, confirmed
shipped in the compiled `sector-rotation` bundle via grep.
