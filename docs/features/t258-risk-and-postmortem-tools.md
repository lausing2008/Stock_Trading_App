## Feature Reference: T258-WHATCOULDGOWRONG-AGENT — Adversarial Pre-Trade Risk Check (Built 2026-07-18)

**What this is**: the one genuinely-new agent from the "Combined Agent Catalog" fit-gap
analysis (see T258-FITGAP-AGENT-CATALOG). Before this, nothing in the codebase argued AGAINST
a proposed entry — research reports have risk sections but are slow, on-demand, per-report;
decision-engine's hard rejects block on rules but never enumerate concrete failure modes for
a trade that clears every gate.

**Implementation**: `services/decision-engine/src/api/risk_agent.py`, deliberately mirroring
`llm_scorer.py`'s exact established pattern rather than inventing a new LLM-call convention —
same `stockai:admin:claude_api_key` Redis lookup, same `httpx.AsyncClient` call to
`api.anthropic.com/v1/messages`, same fail-open-returns-None contract, same 6h Redis cache
keyed by symbol+style+date. Opt-in via `risk_check_enabled` config (default `False`, same
convention as `llm_scoring_enabled`). Called from `_decide()` in `routes.py` right after the
existing LLM-scoring step, using ONLY context `_decide()` already has in scope (game_plan,
regime, research_rec/score, `reasons` dict fields) — zero new fetches.

**Deliberately does NOT emit a probability_of_failure number.** Per the source design doc's
own honest-answer section and this repo's established "don't let a rubric that sounds right
stay in production unvalidated" discipline: an LLM narrating "73% chance of failure" is not
evidence of a 73% edge — it's evidence the model followed formatting instructions. The value
is the forced, concrete risk *enumeration* a human reads before entering, not an unvalidated
confidence number attached to it.

**Also deliberately returns `None`, not `[]`, when zero risks pass validation** — a forced-
adversarial prompt asking the model to argue against a trade will essentially always find
something to say, so an empty list is never a real "clean bill of health" finding worth
reporting; distinguishing "didn't run" from "found nothing" would invite over-trusting a rare,
likely-spurious empty response.

**Response shape**: new `RiskFlag` pydantic model (`category: macro|sector|company|technical`,
`severity: low|medium|high`, `note: str`) and a `risks: list[RiskFlag] | None` field on
`DecisionResult`. Frontend: `decide.tsx` gained a `RisksCard` component rendered only when
`risks` is a non-empty list, styled to match the existing `PositionCard`'s "illustrative only"
warning convention. `frontend/src/lib/api.ts`'s `DecisionResult` type also gained
`llm_verdict`/`llm_reasoning`/`llm_verdict_overridden_by_sizing` — these were real backend
fields the TypeScript type had been missing since T203, found while extending this type for
the new `risks` field.

**Tests**: `services/decision-engine/tests/test_risk_agent.py`, 16 cases — opt-in gate,
missing-API-key fail-open, successful parse, non-200/network-exception/malformed-JSON
fail-open, markdown-fence stripping, per-risk category/severity/note validation (invalid
entries filtered, not silently accepted), the all-invalid-degrades-to-`None` case, cache
hit/write behavior, pure prompt-construction checks. `redis` needed a local `pip install` to
run these tests (already a real pinned dependency in `requirements.txt`, just missing from
this local dev environment — same class of gap as the jose/requests_oauthlib incidents
documented elsewhere in this file).

**A real adversarial-verification gotcha worth remembering**: the first version of
`test_returns_none_when_risk_check_disabled` used `cfg={"risk_check_enabled": False}` alone
and passed — but sabotaging the opt-in gate itself (`if not cfg.get("risk_check_enabled",
False):` → `if False:`) did NOT make this test fail, because the sabotaged code path fell
through to the SEPARATE no-api-key early return (the test's cfg had no API key either), which
also returns `None`. Two different guards returning the same value can mask each other in a
naive test. Fixed by supplying a valid API key and asserting the API is never called — that
version correctly failed with the gate disabled before being fixed.

**What to check if this looks wrong**:
```bash
# Confirm the opt-in gate: risk_check_enabled must be explicitly set in portfolio config
docker exec stockai-decision-engine-1 python3 -c "
from src.api.risk_agent import check_risks
print('module loads OK')"

# Check cache state for a specific symbol/style/date:
docker exec stockai-redis-1 redis-cli get "de:risk:AAPL:SWING:2026-07-18"
```

---


## Feature Reference: T258-PORTFOLIO-CORRELATION-PREENTRY — Correlation-Aware Entry Scoring (Built 2026-07-18)

**What this is**: wires the ALREADY-EXISTING portfolio-risk correlation math
(`/portfolio-risk/risk`, portfolio-optimizer) into the pre-entry decision as an advisory score
layer — a candidate highly correlated with an already-open position now scores -1 in
`_should_enter()`, the DE-outage fallback gate. Never a hard reject, matching this repo's
established discipline of promoting a soft penalty to a hard gate only after outcome data
justifies it.

**Why the fallback gate, not decision-engine itself**: decision-engine's `scorer.py` scores
each candidate in complete isolation — `DecisionRequest.open_positions` is only a COUNT, never
a symbol list, by design. Extending decision-engine to accept and score against a real symbol
list (and the price history needed to correlate against it) would be a materially bigger,
more invasive change than "port an advisory layer" — this repo already treats `_should_enter()`
as the place to harden DE-parity behaviors (see the T232-DL-DUALSCORER-DEBT hard-reject ports
above), so the correlation layer landed there too, at the same M-effort scope as the tracker
item called for.

**Why local DB math, not an HTTP call to portfolio-optimizer**: market-data has direct DB
access to `Price`/`Stock`; portfolio-optimizer's own `/portfolio-risk/risk` endpoint fetches
prices over HTTP specifically BECAUSE it lacks that access (see that endpoint's own module
docstring). Calling out to portfolio-optimizer from `_should_enter()`'s hot path would add a
network round-trip to the single most capital-sensitive code path in the system for math this
service can already do directly — so the `df.corr()` logic was reimplemented locally instead
of reused via HTTP.

**Implementation**: two new functions in `paper_trading_engine.py`.
`_bulk_fetch_daily_closes(session, stock_ids)` — one bulk query (30-day lookback,
`Price.stock_id.in_(...)`) pivoted into a wide DataFrame, called ONCE per scan cycle for the
whole open book (not once per candidate). `_max_correlation_with_open_positions(session,
candidate_stock_id, open_stock_ids, open_closes_cache)` — fetches only the candidate's own
closes fresh, joins onto the pre-fetched open-book cache, returns the highest absolute
pairwise daily-return correlation or `None` if incomputable (no open positions, insufficient
overlapping history — matching the repo's convention that `None` and a real "no correlation"
value have different implications for the score layer, so they must be distinguishable).
`_should_enter()` gained a `max_open_corr` parameter and penalizes -1 when it exceeds `0.8` —
the SAME threshold portfolio-optimizer's own risk endpoint already uses for its "high
correlation" warning, chosen for consistency rather than picked fresh.

**Not built in this pass**: beta-weighted book exposure (the other half of the original
catalog design) — correlation was the higher-value, more tractable half for a per-candidate
score layer; beta-weighted exposure is more naturally a book-level dashboard readout than a
per-entry-decision score component, and is left as a smaller, separately-scoped follow-up.

**Tests**: 6 new cases in `test_should_enter_de_parity.py` (score-layer behavior: penalizes
`>0.8`, not at exactly `0.8`, not below, not on negative/hedge correlation, stacks
independently with the pre-existing regime/K-Score layers) plus 11 new cases in
`services/market-data/tests/test_correlation_preentry.py`, extending
`test_broker_position_sync.py`'s established real-sqlalchemy-via-stub-pop-and-restore
technique to the `Stock`/`Price` models — covers the bulk fetch, lookback-window exclusion,
high/low/insufficient-history correlation detection, and picking the highest absolute
correlation across multiple open positions.

**A real adversarial-verification finding worth remembering** (a near-miss on false test
confidence, not a shipped bug): the first version of the "candidate excluded from its own
open-position list" test built `open_closes_cache` from `[candidate_stock_id]` alone. Disabling
the actual self-exclusion filter in the source did NOT make this test fail — because with the
candidate's own column already present in `open_closes_cache`, the subsequent
`open_closes_cache.join(cand_wide[[candidate_stock_id]], how="outer")` call raises a plain
pandas `ValueError` (duplicate column name) on ANY code path, self-exclusion filter present or
not, which the function's own `except Exception` silently catches and returns `None` from —
the exact same return value the test expected, but for a completely different, coincidental
reason. Caught by disabling the filter and getting a passing test back (a red flag — the test
should have failed), then rewriting it to build a cache that does NOT contain the candidate's
column, with the candidate ID separately duplicated into `open_stock_ids` — that version
correctly produces a spurious `1.0` self-correlation and fails when the filter is removed.
**Lesson**: an adversarial-verification pass that produces "still passes" for a supposedly
protective guard is itself a finding — investigate why, don't just conclude the guard is
redundant, the way the broker-position-sync case earlier in this file genuinely was.

**A SQLite/BigInteger test-harness quirk hit again** (same class already documented for
`SignalOutcome` elsewhere in this file): `Price.id` is a `BigInteger` primary key, which
doesn't get SQLite's implicit `INTEGER PRIMARY KEY` autoincrement — test fixtures inserting
`Price` rows must assign `id` explicitly (a real Postgres sequence handles this in production;
this is a test-harness-only workaround).

**What to check if this looks wrong**:
```bash
# Confirm the correlation layer is actually computing values (not silently always None):
docker logs stockai-market-data-1 --since 1h | grep 'correlation_check_failed'
# Absence of this log line does NOT confirm success on its own — it only means no EXCEPTION
# occurred; None is also the normal, expected return for a portfolio with 0-1 open positions.

# Spot-check the bulk fetch + correlation math directly against real data:
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.services.paper_trading_engine import _bulk_fetch_daily_closes
from db import SessionLocal
s = SessionLocal()
df = _bulk_fetch_daily_closes(s, [1, 2, 3])  # real stock_ids
print(df.tail())"
```

---


## Feature Reference: T258-MACRO-SECTOR-IMPACT — Structured Sector Chips on Macro Reactions

**Built 2026-07-18.** Finishes what T249-P2 (macro post-announcement fast reaction) explicitly
deferred: `generate_reaction()` (`services/event-intelligence/src/services/macro_reaction.py`)
previously returned only a narrative `reaction_text` paragraph. It now also asks the same
single Haiku call for a structured `{sectors_helped: [], sectors_hurt: []}` block (0-4
GICS-style sector names each) — no second LLM call, same fail-open contract as before.

**Validation**: new `_clean_sector_list(raw: object) -> list[str]` — non-list input becomes
`[]`, non-string/empty entries are filtered, surviving strings are whitespace-stripped, capped
at 6. `generate_reaction()`'s return type changed from `str | None` to `dict | None` (`
{"reaction_text": ..., "sectors_helped": [...], "sectors_hurt": [...]}`); both
`check_release_day_fast_poll()` and `check_fomc_statement_poll()` were updated to unpack the
new shape.

**Storage**: two new nullable `EconomicEvent` columns, `sectors_helped`/`sectors_hurt` (both
`Text`), JSON-encoded strings — matching `reaction_text`'s existing TEXT-column convention
rather than introducing a new Postgres array/JSONB type for consistency with the sibling
columns on the same table. **Requires a manual `ALTER TABLE economic_events ADD COLUMN IF NOT
EXISTS sectors_helped TEXT, ADD COLUMN IF NOT EXISTS sectors_hurt TEXT;`** in every environment
— per this file's own `create_all()`-gap invariant (new columns on an existing, already-
populated table are never auto-applied).

**Read side**: `GET /events/overview` (`services/event-intelligence/src/api/routes.py`) parses
both columns defensively via an inline `_parse_sectors()` helper (degrades to `[]` on any parse
failure) into the `latest_macro_reaction` field. `frontend/src/pages/intelligence.tsx`'s
"Latest Macro Reaction" card renders green ▲ chips for `sectors_helped` and red ▼ chips for
`sectors_hurt`, between the actual/previous value line and the reaction paragraph.

**Deliberately not built this pass**: watchlist-join personalization ("you watch 3 names in a
sector this release pressures", from the original T249-P2 design) — scoped to the structured-
data half only; the chips already let a user do that cross-reference visually without a new
per-recipient query in `check_macro_reaction_alerts()`.

**Tests**: 21 new cases in `services/event-intelligence/tests/test_macro_reaction.py` (full
suite 143 passed) — `_clean_sector_list` validation (valid list, non-list, non-string
filtering, whitespace stripping, 6-entry cap, empty list), `generate_reaction()`'s new dict
shape via a `_FakeAsyncClient` async-context-manager pattern (mirroring `risk_agent.py`'s own
test technique, since `httpx` is a `MagicMock` in this test environment), and source-text
checks confirming both poll functions write the new columns. Adversarially verified
`_clean_sector_list` by replacing its body with `return raw` — 5 of 7 tests correctly failed,
then reverted.

**What to check if this looks wrong**:
```bash
docker exec stockai-postgres-1 psql -U stockai -d stockai -c "\d economic_events" | grep sectors
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/events/overview', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.json().get('latest_macro_reaction'))
"
```

---


## Feature Reference: T258-TRADE-POSTMORTEM — Per-Closed-Trade Plan-vs-Actual Review

**Built 2026-07-18.** The aggregate learning loop already existed and is validated
(`calibrate_entry_weights` learns from closed trades, `entry_factors` does per-factor win-rate
analysis, retro-feedback backfills realized EV onto `TuneHistory`) — but there was no per-trade
review: looking at one closed trade couldn't answer "did entry match plan, was the stop
respected, was the exit early vs. the time-stop, did price run further after exit." v1 is
mechanical only (no LLM) — `PaperTrade` already stores both the plan (entry/stop/take_profit at
entry) and the actuals (exit price/reason/pnl), so this is mostly presentation over existing
data plus one new bar-data query.

**Endpoint**: `GET /paper-portfolio/trades/{trade_id}/postmortem`
(`services/market-data/src/api/paper_portfolio.py`) — 404 if the trade doesn't exist, 400 if
`trade.stage != "closed"` (post-mortems only make sense on a finished trade). Computes:
- `is_mechanical_exit` — whether `exit_reason` is in `_MECHANICAL_EXIT_REASONS = {"stop_hit",
  "breakeven_stop", "target_reached", "time_stop"}` (plan-consistent) vs. anything else
  (discretionary/manual/decay).
- `plan_adherence.exit_vs_stop_pct` / `.exit_vs_target_pct` — actual exit price vs. the stored
  plan levels, as a percent.
- `hold_window.hold_days_vs_expected` — actual `hold_days` vs. the trading style's
  `_STYLE_OVERRIDES` `max_hold_days` (SHORT=10, GROWTH=60, SWING=20, LONG=90; unknown style
  falls back to 60) — a different concept from signal-engine's `_OUTCOME_HOLD_DAYS` (that one
  labels signal outcomes; this one is the paper-trade time-stop horizon).
- `max_favorable_excursion` — the highest daily `Price.high` between `entry_time` and
  `exit_time` for the trade's linked `stock_id`, vs. the actual exit price. One indexed range
  query against the same daily `Price` table already used elsewhere in this file — not a new
  data source.
- `entry_slippage_pct` — currently a placeholder, always `0.0`. Pure paper trades fill exactly
  at the signal's live price with no separate "planned" entry to diverge from; the field is
  kept in the response shape for forward compatibility with real-broker-synced trades
  (T257-BROKER-ORDER-HISTORY), where an actual fill CAN diverge from the paper-simulated
  `entry_price`.

**UI**: `frontend/src/pages/paper-portfolio.tsx`'s `PostmortemPanel` renders as an expandable
row under each closed trade in the trade history table — click a row to toggle
(`expandedTradeId`, the same pattern already used elsewhere on this page). Shows a
plan-consistent/discretionary badge plus 5 stat cells, with a callout when price ran more than
5% above the exit price afterward ("worth reviewing whether the exit was early").

**Deliberately not built this pass**: a v2 LLM call generating `what_went_right`/
`what_went_wrong`/`lessons` prose per trade — v1's mechanical fields are what this repo's own
calibration-loop discipline says to trust first; an LLM narrative layer is a later, optional
addition, not a prerequisite.

**Two real bugs caught in my own test-writing process during adversarial verification** (not
in the shipped feature — both were self-caught before either could ship with false test
confidence):
1. An early version of the test extraction hardcoded `_MECHANICAL_EXIT_REASONS` as a literal
   dict in the test namespace instead of pulling it from real source. Sabotaging the REAL
   constant in `paper_portfolio.py` (emptying the set) still passed the test — because the test
   was reading its own hardcoded duplicate, not the sabotaged value. Fixed by extracting the
   real constant's source line via string search and `exec()`-ing it into the namespace before
   the function body runs; re-verified the sabotage is now correctly caught.
2. Separately (unrelated to this feature, discovered while running the full suite in
   isolation): a genuine pre-existing wall-clock flakiness bug in
   `test_should_enter_de_parity.py` — its autouse `_always_market_hours` fixture only patched
   `_is_market_hours()`, never the separate time-of-day gate's own `datetime.now()` call. 13
   tests failed for real when run at 9:48 AM ET (inside the "first 30 min of market open" gate
   window). Fixed by also pinning `datetime.now()` to a fixed, safe mid-session instant (noon
   ET on a Monday) inside the same fixture; confirmed the per-test time-of-day-gate tests (which
   use their own local `_mock_local_time` override) still correctly take precedence over the
   fixture default.

**Tests**: 13 new cases in `services/market-data/tests/test_trade_postmortem.py`, using the
established real-sqlalchemy-via-stub-pop-and-restore technique (same as
`test_broker_position_sync.py`/`test_correlation_preentry.py`) to load real
`PaperPortfolio`/`PaperTrade`/`Stock`/`Price` models against an in-memory SQLite engine —
covering the 404/400 guards, mechanical-vs-discretionary exit-reason classification,
exit-vs-stop/target math, hold-days-vs-expected per style (including the unknown-style
fallback), and max-favorable-excursion (highest high within the hold window, ignoring prices
outside it — the specific case the entry_time-lower-bound sabotage above targets). Full
294-test market-data suite and frontend typecheck green.

**What to check if this looks wrong**:
```bash
# Confirm the endpoint returns real data for a known closed trade:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://api-gateway:8000/paper-portfolio/trades/<real_trade_id>/postmortem', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.status_code, r.json())
"
```

---


## Feature Reference: T258-SECTOR-ROTATION-TRAJECTORY — Sector Rank Trajectory Classification (Built 2026-07-22)

**The gap this closes**: `_compute_sector_rotation()` (T220-G, `services/market-data/src/
services/scheduler.py`, Sunday-scheduled) only ever wrote ONE Redis key
(`stockai:sector_rotation`, 3-day TTL) — each week's run overwrote the prior one, so nothing
could answer "is this sector's leadership rising or fading over several weeks," only "what does
this week's snapshot say." No history was persisted anywhere.

**New module**: `services/market-data/src/services/sector_trajectory.py` — pure, DB-independent
classification logic (no network/DB dependency, matching the `volume_area.py` precedent of
separating pure math from DB-touching wiring):
- `rank_sectors(rotation: dict) -> list[SectorRank]` — assigns a 1-indexed rank (1 = highest
  `recent_kscore`) to every sector that HAS a real `recent_kscore` this snapshot. A sector with
  no `recent_kscore` (insufficient ranking data that week) gets `rank=None` — excluded from
  ranking, never assigned a fake last place.
- `classify_trajectory(current_rank, prior_rank, total_sectors, flat_threshold=1) -> str | None`
  — six-class vocabulary from the original design doc's own "Combined Agent Catalog" cite:
  **Emerging Leader** (top half, rank improved by >1), **Established Leader** (top half, rank
  held within ±1), **Fading Leader** (top half, rank worsened by >1), and the mirror three for
  bottom half — **Emerging/Established/Fading Laggard**. Classification is by the sector's
  CURRENT half only — a sector that fell from rank 1 (top half) to rank 3 of 4 (bottom half,
  `half = (total+1)/2 = 2.5`) reads as "Fading Laggard," not "Fading Leader," since it's now
  actually in the bottom half; "Fading Leader" is reserved for a sector still IN the top half
  but losing ground within it. Returns `None` when either rank is unavailable (a sector newly
  entering the rankable set, or one that dropped out 4 weeks ago) — no trajectory is fabricated
  without both endpoints.
- `build_trajectories(current_ranks, prior_ranks) -> dict[str, dict]` — combines this
  snapshot's ranks with a prior snapshot's into `{sector: {rank, prior_rank, trajectory,
  recent_kscore}}`. `total_sectors` (used for the top/bottom-half cutoff) counts only rankable
  sectors THIS snapshot — an unrankable sector doesn't skew the cutoff for the others.

**New table**: `SectorRotationSnapshot` (`shared/db/models.py`) — `(sector, as_of)` unique,
stores `recent_kscore`/`prior_kscore`/`momentum`/`rank` per sector/week. A brand-new table, so
`create_all()` handles it automatically — no manual `ALTER TABLE` needed (per this file's own
standing `create_all()`-gap invariant, which only applies to adding a column to an EXISTING
table).

**Wiring**: `_compute_sector_rotation()` now, in addition to its existing Redis write: (1)
ranks this week's sectors via `rank_sectors()`; (2) queries the most recent
`SectorRotationSnapshot` `as_of` that's `<= today - 28 days` (not a fixed weeks-back count —
tolerant of a missed week); (3) classifies each sector's trajectory via `build_trajectories()`
against that prior snapshot's ranks; (4) folds `trajectory`/`rank`/`prior_rank` directly into
the SAME `rotation` dict already being cached to `stockai:sector_rotation` — nothing that
already reads that key needs to change, it just gains new fields; (5) upserts this week's
`SectorRotationSnapshot` rows via `ON CONFLICT DO UPDATE` on `(sector, as_of)`, matching
`volume_area.py`'s established idempotent-upsert pattern for the same class of dated-snapshot
table — safe to re-run for the same week without duplicate rows.

**API**: `GET /stocks/sector-rotation` (T220-G's existing endpoint) needed zero code changes —
it's a pure Redis-cache passthrough, so the new `trajectory`/`rank`/`prior_rank` fields just
flow through automatically once the scheduler starts writing them. Docstring updated to
document the new fields.

**Frontend**: `frontend/src/lib/api.ts` gained `sectorRotationKscore()` → `GET /stocks/sector-
rotation` and a `SectorRotationKscoreEntry` type — this endpoint had NO prior frontend consumer
at all; the Money Flow tab's existing "Sector Momentum" table was reading a DIFFERENT endpoint
entirely (`api.sectorRotation()` → `/rankings/sector_rotation`, ranking-engine's own RS-based
sector rotation — confirmed by checking the actual response shape/fields, not assumed from the
similar name). Renamed that pre-existing card's title from "Sector Momentum (K-Score-based)" to
"Sector Momentum (Relative Strength)" to correct a standing label mismatch (it was never
K-Score-based — its own columns are Avg RS/RS Change), found while wiring up the real K-Score
endpoint alongside it. Added a new "Sector K-Score Momentum & Trajectory (US)" card to
`reports.tsx`'s Money Flow tab, with a trajectory-colored chip per sector (green shades for
Leader classes, gray/red for Laggard/Fading) and a rank readout (`#N (was #M)`).

**Three DISTINCT sector-rotation endpoints now exist in this app, easy to confuse by name
alone** (a reminder for future work, not a bug): `/stocks/sector_rotation` (RES-4, US sector
ETFs vs SPY, ETF-ticker-based), `/rankings/sector_rotation` (ranking-engine, Relative-Strength-
based, used by `FlowTab`'s pre-existing card and `TopStocksTab`), and `/stocks/sector-rotation`
(T220-G/T258, this feature, K-Score-momentum-based, the only one with rank-trajectory history).
Verify the ACTUAL response shape before reusing any of these three for a new call site — same
discipline already documented elsewhere in this file for `/events/overview`'s nested fields.

**Tests**: `services/market-data/tests/test_sector_trajectory.py` (20 cases) — direct,
DB-independent tests of `rank_sectors()`/`classify_trajectory()`/`build_trajectories()`,
covering unrankable-sector exclusion, all 6 trajectory classes, the flat-threshold band, the
odd-vs-even `total_sectors` half-cutoff tie-break, missing-rank fallback to `None`, and
zero-`total_sectors` safety. `services/market-data/tests/test_sector_rotation_trajectory_
wiring.py` (5 cases) — source-text regression checks for the `scheduler.py` wiring (matching
this repo's established pattern for scheduler.py functions that can't be imported directly in
this test environment — its import chain pulls in `apscheduler`, and `conftest.py`'s
`MagicMock()`-stubbed `sqlalchemy`/`db` would silently mask a real `NameError`/missing import).
Confirms: only locally-imported names are used, the upsert targets `(sector, as_of)`, the
prior-snapshot query looks back 28 days, the trajectory fold happens BEFORE the existing Redis
`setex` call (not a separate/new key), and the persist happens inside the same session as the
read with an explicit commit.

**Adversarial verification** — 3 sabotage cycles on the wiring tests, all caught and reverted:
removing `"as_of"` from the `on_conflict_do_update` index_elements (caught by the upsert-target
test); replacing `timedelta(days=28)` with a same-day cutoff (caught by the four-weeks-ago
test); removing the trajectory-fold block entirely before the `setex` call (caught by the
folds-into-same-payload test). Separately, `classify_trajectory()`'s half-cutoff comparison
operator (`<=` vs `<`) was sabotaged and reverted, caught by the odd-total-sectors tie-break
test. All reverts confirmed byte-identical to the pre-sabotage source before moving on.

**A real, unrelated corruption caught and fixed during this same session**: while restoring
`scheduler.py` from a `/tmp` backup after one of the sabotage cycles above, the restore
inadvertently reverted TWO already-shipped, already-committed pieces of code back to an older
state — `check_signal_alerts()`'s earnings-reminder block reverted from the consolidated
per-user digest (AUD-EARNINGS-DIGEST, committed `0a8ba04`) back to the old per-symbol
`send_email()` loop, and the already-deleted `_earnings_reminder_body()` helper reappeared.
Caught by the full test suite failing on an unrelated, pre-existing test
(`test_reminder_wiring_sends_one_consolidated_digest_not_per_symbol_emails`) — confirmed via
`git diff` that the corruption was isolated to those two already-committed regions (a `git
stash`/`stash pop` round-trip proved the failure disappeared against the clean committed state),
then surgically restored just those two regions from `git show HEAD` via targeted `Edit` calls
rather than a blanket file overwrite, byte-verified against HEAD before proceeding. **Lesson
reinforced**: a `cp <backup> <target>` restore during adversarial sabotage testing must restore
from a backup taken of the CURRENT intended state, not an earlier, possibly-stale snapshot —
after any such restore, diff the full file against `git show HEAD` (not just re-run the tests
for the function you were sabotaging) to catch collateral reversion in unrelated regions before
it ships.

**Not yet built (deferred, matching the tracker item's own note)**: an HK sector-ETF rotation
equivalent — the K-Score rotation query is hardcoded `WHERE s.market = 'US'`; HK support would
need its own sector-ETF universe first, tracked as a separate follow-up.

**What to check if this looks wrong**:
```bash
# Confirm the new table exists and has real rows after the next Sunday run:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT sector, as_of, recent_kscore, rank FROM sector_rotation_snapshots ORDER BY as_of DESC, rank ASC LIMIT 20;"

# Confirm trajectory is actually landing in the Redis payload:
docker exec stockai-redis-1 redis-cli get stockai:sector_rotation

# Manually trigger the job to see it live (safe, idempotent — upserts on conflict):
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from src.services.scheduler import _compute_sector_rotation
_compute_sector_rotation()
"

# Live check the API response:
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'scheduler','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.get('http://localhost:8001/stocks/sector-rotation', headers={'Authorization': f'Bearer {tok}'}, timeout=15)
print(r.status_code, r.json())
"
```
A `trajectory: null` for every sector on the FIRST run after this deploy is expected (no 4-week-
prior snapshot exists yet) — it should start populating from the second Sunday run onward.

---


## Feature Reference: T258-ACCUM-DIST-BREAKOUT-QUALITY — Volume-Pattern A/D Classifier + Breakout Quality (Built 2026-07-22)

**The gap this closes**: per the tracker's own framing, volume analysis was otherwise
extensively covered (volume profile POC/VAH/VAL/HVN, session-scaled RVOL, T257 volume anomaly
alert, FVG, OBV conviction layer, 13F QoQ institutional accumulation) but two reads remained
manual-only: (a) an explicit accumulation-vs-distribution classification (OBV direction
existed as a boolean conviction layer, but no named A/D state was surfaced anywhere), and (b)
breakout FOLLOW-THROUGH assessment — the docs literally taught "poke-and-reject = false
breakout" as a manual chart read (Volume Profile "How to trade it" section), with nothing
automating it.

**Honesty constraint carried over from the tracker's own text**: no block-trade/dark-pool data
source exists anywhere in this app — both new functions are explicitly framed as
volume-PATTERN-based reads, not true institutional-flow detection. Neither claims to detect
real institutional buying/selling directly.

**New functions**: `services/technical-analysis/src/indicators/trendlines.py`:
- `detect_accumulation_distribution(df, window=20)` — combines OBV trend (10-bar MA vs.
  30-bar MA of cumulative volume×price-direction — the same construction signal-engine's own
  `obv_trend_bullish` already uses) with an up/down-day total-volume ratio over `window` bars.
  Both signals must agree (ratio `>1.2` for accumulation, `<1/1.2` for distribution) — one
  agreeing and one not degrades to `'neutral'` rather than a rough guess from a single signal.
  Returns the component readings alongside `state` so a caller sees the actual evidence.
- `assess_breakout_quality(df, level, direction="up", window=20)` — finds the actual bar that
  first crossed `level` in the given direction (scanning backward for the transition, not just
  "is today's close beyond the level," which would misreport an established multi-week uptrend
  as a fresh break every single day). Classifies `'real'` (next bar held beyond the level AND
  the breakout bar's own volume was RVOL > 1.0), `'failed'` (next bar reversed back across —
  the classic poke-and-reject), or `'unconfirmed'` (breakout is the most recent bar with no
  next-bar data yet, or held without volume confirmation — deliberately does NOT guess `'real'`
  in that case, since real-vs-failed is genuinely unknowable from price alone without volume
  backing). Returns `None` when nothing has actually broken the level in that direction.

**`detect_sr_context()` gained two new fields** (`sr_cleared_resistance`/`sr_cleared_support`)
— a real design gap caught mid-implementation: `sr_nearest_resistance`/`sr_nearest_support`
are ALWAYS on the not-yet-reached side of price by construction (the nearest level still
ahead), so neither can ever be "the level a breakout just cleared." `cleared_res`/`cleared_sup`
(the highest resistance `<=` current / lowest support `>=` current) were already computed
internally by `detect_sr_context()` for its own breakout classification but never exposed —
now exposed as the correct levels to feed into `assess_breakout_quality()`.

**API**: `GET /ta/{symbol}/levels` gained `accumulation_distribution` and `breakout_quality`
fields, reusing the SAME `df`/`levels`/`sr_context` already computed in that route — no second
level-detection pass, no new endpoint (folded into the existing levels response, matching how
`sr_context`/`fair_value_gaps` were added previously).

**Frontend**: new `SrContext`/`AccumulationDistribution`/`BreakoutQuality` types in
`frontend/src/lib/api.ts` (the `Levels` type had never gained `sr_context` either, despite that
field shipping earlier — added alongside these two new ones). New "Volume Pattern Read" card
on the stock detail page, placed directly after the Fair Value Gap Trade Plan card, matching
that card's exact visual convention (color-coded state, an explanatory footer disclaiming the
pattern-vs-confirmed-flow distinction).

**A real design bug caught and fixed DURING implementation, not shipped**: the first draft of
`assess_breakout_quality()`'s docstring promised a `'failed'` classification but the
implementation only ever checked the CURRENT bar's close vs. level — never looked at a "next
bar" at all, so `'failed'` could never actually be produced. Caught by re-reading my own
docstring against my own implementation before writing tests. Fixed by re-architecting the
function to scan backward for the actual bar that first crossed the level (the transition
point), then check the bar immediately after it — which is what makes a genuine
real/failed/unconfirmed classification possible in the first place.

**Tests**: `services/technical-analysis/tests/test_accum_dist_breakout_quality.py` (13 cases)
— accumulation/distribution detection (heavier up-day vs. down-day volume, insufficient
history, zero-down-days infinite-ratio edge case, and a DETERMINISTICALLY-constructed
just-below-threshold case rather than relying on random-seed luck), and breakout-quality's
full state space (no breakout at all → `None`, breakout-on-last-bar → `unconfirmed`,
held-with-volume → `real`, reversed-next-bar → `failed`, held-without-volume → `unconfirmed`
not `real`, breakdown direction, and the "price already beyond the level for many bars must
still find the FIRST crossing" case). Plus a `sr_context` integration test confirming
`sr_cleared_resistance` differs from `sr_nearest_resistance` as designed.

**A real test-construction lesson hit while writing the accumulation/distribution disagreement
test**: OBV direction and the up/down-volume ratio are correlated by construction (both derive
from the same volume-times-price-direction data) — an initial attempt to build a fixture where
"OBV reads bullish but the volume ratio reads bearish" kept producing BOTH signals agreeing
instead, no matter how the random data was skewed, because heavy down-day volume that drags the
ratio down also drags OBV's own cumulative sum down. Abandoned the "genuine disagreement"
fixture as non-representative of realistic data and replaced it with a simpler, fully
deterministic "ratio just below the 1.2 threshold" test instead — a more useful regression
guard for the actual threshold boundary than a hard-to-construct edge case.

**Adversarial verification** — 3 sabotage cycles, all caught and reverted: loosening the
accumulation threshold from `1.2` to `1.0` (caught by the just-below-threshold test);
hardcoding the "next bar reversed" branch to never fire (`elif False:`) (caught by the
failed-breakout test); hardcoding `volume_confirmed = True` unconditionally (caught by the
no-volume-confirmation test).

**Verification**: full technical-analysis suite (44 tests, up from 31 — includes one
pre-existing `test_sr_context.py` test updated for the 2 new `detect_sr_context()` return
fields, not a regression), frontend vitest suite (89 tests, unaffected), frontend typecheck,
and a full `next build` all green.

**What to check if this looks wrong**:
```bash
docker exec stockai-technical-analysis-1 python3 -c "
import sys; sys.path.insert(0, '/app')
from src.indicators.trendlines import detect_accumulation_distribution, assess_breakout_quality
print('module loads OK')
"
# Live check against a real symbol:
docker exec stockai-technical-analysis-1 curl -s 'http://localhost:8002/ta/AAPL/levels?timeframe=1d' \
  | python3 -c "import sys, json; d = json.load(sys.stdin); print(d['accumulation_distribution']); print(d['breakout_quality'])"
```
If `breakout_quality` is always `None` for a symbol you'd expect a real recent break on, check
whether `sr_context`'s `sr_cleared_resistance`/`sr_cleared_support` are actually populated for
that symbol first — `assess_breakout_quality()` never computes a level itself, it only
evaluates whichever cleared-level `detect_sr_context()` already found.

---

