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
