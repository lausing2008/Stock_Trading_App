## Feature Reference: T249-EARNINGS-LLM-IMPACT — Earnings LLM Impact Report (Built 2026-07-29)

**User ask, verbatim**: after asking whether earnings/macro results already get an LLM impact
read (answer: macro did, via T249-P2's `generate_reaction()`; earnings only had a non-LLM
numeric reaction), the user said: "add the LLM feature to earning report as well same as
Marco to get the impact report and all the details" — then, immediately after: "put all those
to feature flag as well so that I can have control" (referring to both this new feature and
the pre-existing, previously-unflagged macro reaction feature).

**Mirrors `event-intelligence/src/services/macro_reaction.py`'s `generate_reaction()` exactly**
— same Claude Haiku call shape, same fail-open contract (`None` on any error), same structured
`{"impact_text": ..., "sectors_helped": [...], "sectors_hurt": [...]}` return shape and
`_clean_sector_list()` validation, same markdown-fence-stripping fix already established for
`risk_agent.py`/`news.py`. New `generate_earnings_impact()` in
`services/event-intelligence/src/services/earnings.py` takes EPS/revenue actual-vs-estimate/
surprise-pct/earnings-strength-score instead of a macro release's actual/expected/previous
values — the earnings-specific inputs, same LLM-call plumbing.

**Detection differs from macro's release-day-armed poll** — macro's poll knows the EXACT
minute a release is due (`_FRED_RELEASES`/`_FOMC_DATES`); earnings land unpredictably per
company throughout the day/after-hours, so `check_earnings_impact_poll()` is instead a plain
5-minute interval scan for `EarningsEvent` rows where `eps_actual` has already landed (via the
existing daily `sync_all_earnings()` job) but `impact_text` hasn't been generated yet — a
cheap, single indexed query on the common no-op case.

**New DB columns on `EarningsEvent`** (`shared/db/models.py`): `impact_text`,
`impact_generated_at`, `impact_sent_at`, `sectors_helped`, `sectors_hurt` — byte-for-byte
mirroring `EconomicEvent`'s own 5 reaction-tracking columns. Per this repo's own standing
`create_all()`-gap invariant (new columns on an existing, already-populated table are never
auto-applied), a matching `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` migration was added to
`shared/db/session.py`'s `_run_migrations()`.

**Delivery — new `check_earnings_impact_alerts()` in `services/market-data/src/services/
scheduler.py`**, the earnings-side mirror of `check_macro_reaction_alerts()`: polls for
`impact_text IS NOT NULL AND impact_sent_at IS NULL` rows and emails the same
`PriceAlert`-subscribed audience as every other T249 alert type. `impact_sent_at` only
advances inside an `if any_sent:` gate — a failed send cycle must retry next minute, not get
silently marked done (same discipline already established for `reaction_sent_at`, and
adversarially re-verified here too).

### Feature flags — closing the "put all those to feature flag" ask

Three global admin flags now exist for the app's Claude-calling features, each with different
default semantics matched to its actual risk/track record:

| Flag | Redis key | Default | Why |
|---|---|---|---|
| `auto_research_enabled` | `stockai:admin:feature:auto_research_enabled` | **OFF** | Real production cost-leak bug found same session (CLAUDE-API-COST-AUDIT) |
| `macro_llm_reaction_enabled` | `stockai:admin:feature:macro_llm_reaction_enabled` | **ON** | Live and relied upon since T249-P2, no cost incident — flag exists for control, not because it needed fixing |
| `earnings_llm_impact_enabled` | `stockai:admin:feature:earnings_llm_impact_enabled` | **OFF** | Brand-new feature, matches every other new opt-in Claude feature's own default-off convention |

`macro_llm_reaction_enabled`'s Redis semantics are deliberately inverted from the other two:
the read side checks `get(...) == "0"` (only an explicit `"0"` disables it) rather than
`get(...) != "1"` (only an explicit `"1"` enables it) — this preserves the feature's own
already-live production behavior for every existing deployment where the key has never been
set, rather than silently turning off a feature that's been running fine since T249-P2 the
moment this flag code deploys. `admin.py`'s `get_feature_flags()`/`get_feature_flags_public()`
match this same inverted-default semantics on the read side (`!= "0"` reports `True` when
unset).

All 3 flags follow the identical 5-touch-point wiring pattern established for
`auto_research_enabled`: `ConfigRequest` field, both `GET /admin/feature-flags` endpoints,
`POST /admin/config`'s write-guard + write branch + log line.

**Admin AI Assistant Features page** (`frontend/src/pages/admin-ai-features.tsx`) — both new
flags added as real toggles in the existing "Global" card (previously only had Auto Research);
the old, now-redundant "Macro Reaction Analysis" **info-only** row under "Always on" was
removed since it's a real toggle now — a feature can't be both read-only-info AND
user-controlled in the same page without being misleading about which one is true.

**Tests**: `services/event-intelligence/tests/test_earnings_impact.py` (18 cases) —
`_clean_sector_list()` behavior (identical contract to macro's own), `generate_earnings_impact()`'s
full fail-open matrix (no key / non-200 / network exception / malformed JSON / missing impact
text / markdown-fence stripping / 500-char truncation), and `check_earnings_impact_poll()`'s
feature-flag gate (unset/explicit-off/Redis-error all correctly skip, checked before any DB
query). `services/market-data/tests/test_earnings_impact_delivery.py` (9 cases, source-text —
`scheduler.py` can't be imported directly in this test environment) — confirms
`check_earnings_impact_alerts()`'s flag gate, lock pattern, generated-but-unsent query shape,
and the `any_sent`-gated `impact_sent_at` write; plus confirms the RETROACTIVELY-added flag
gate on `check_macro_reaction_alerts()` (default-on semantics, checked before the lock).
`services/market-data/tests/test_earnings_macro_llm_admin_flags.py` (10 cases) — real
behavioral tests against `admin.py` (genuinely importable in this test environment), covering
both flags' read/write/default-semantics plus a cross-file check that all 3 services'
independently-hardcoded Redis key literals agree.

**Adversarial verification** — every guard sabotaged and reverted, all caught correctly: 2
sabotage cycles on `_clean_sector_list()`/the feature-flag gate in `earnings.py` (8 tests
caught across both); 3 cycles on `scheduler.py` (the new function's flag gate, the
`any_sent` gate, and the retroactive macro flag gate — 8 tests caught total); 1 cycle on
`admin.py`'s write-guard (4 tests caught, confirming a request setting ONLY one of the new
flags would otherwise silently no-op). Full suites green throughout: event-intelligence 177
(159 baseline + 18 new), market-data 602 (583 baseline + 19 new — 9 delivery + 10 admin-flag).
`pyflakes` clean on every touched file (confirmed via `git stash` that every pre-existing
warning predates this change).

**What to check if this looks wrong**:
```bash
# Confirm both new flags' current state:
docker exec stockai-redis-1 redis-cli get stockai:admin:feature:earnings_llm_impact_enabled
docker exec stockai-redis-1 redis-cli get stockai:admin:feature:macro_llm_reaction_enabled

# Check whether the detection poll is finding/generating anything (needs the flag ON first):
docker logs stockai-event-intelligence-1 --since 1h | grep 'earnings_impact'

# Check whether delivery is sending anything:
docker logs stockai-market-data-1 --since 1h | grep 'earnings_impact_sent\|earnings_impact_error'

# Check real impact rows in the DB:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT stock_id, report_date, impact_generated_at, impact_sent_at FROM earnings_events WHERE impact_text IS NOT NULL ORDER BY report_date DESC LIMIT 10;"

# Manually trigger the detection poll (needs the flag ON, and a recent real EarningsEvent row
# with eps_actual populated and impact_text still NULL to have any real effect):
docker exec stockai-event-intelligence-1 python3 -c "
import asyncio, sys; sys.path.insert(0, '/app')
from src.services.earnings import check_earnings_impact_poll
print(asyncio.run(check_earnings_impact_poll()))
"
```

---


## Feature Reference: Earnings Calendar Now Shows Analyst Consensus + Beat-Rate History (Built 2026-08-25)

**User ask, verbatim**: "Can we also provide what're the estimates from the market for the
stock before earning reports like NVDA?" — the earnings calendar cards already showed
`EPS est`/`Rev growth`/`EPS growth`/`Cap`, but nothing about what analysts currently expect the
stock's PRICE to do, or how reliably this specific stock has beaten estimates historically.

**Both were already computed elsewhere in this codebase, just never wired into this specific
endpoint** — a pure composition task, no new data source needed:
- `eps_beat_rate`/`eps_avg_surprise_pct` — already stored on the SAME cached fundamentals blob
  `GET /stocks/events/calendar`'s earnings block already reads for `forward_eps`/etc. Zero new
  fetch cost.
- `analyst_price_target_mean`/`analyst_price_target_weighted`/`analyst_n_firms` — from
  `_compute_weighted_analyst_consensus()` (the accuracy-weighted analyst price-target
  consensus already built for the per-symbol `/analyst-consensus` endpoint, `wsz-analyst-
  accuracy-weighting`). This IS a real DB query (2 queries per call) — so it's only invoked
  for symbols that actually have a near-term earnings event within the requested window, never
  for the full active-stock universe `events_calendar()` otherwise iterates over for macro
  events.

**A real unit-convention trap caught before shipping**: `eps_avg_surprise_pct` is stored
ALREADY multiplied by 100 (`routes.py`'s own `data.eps_avg_surprise_pct = round(... * 100, 2)`
at the fundamentals-refresh site) — a genuinely different convention from `revenue_growth`/
`earnings_growth`, which are raw fractions the frontend's existing `fmtPct()` helper multiplies
by 100 itself. Confirmed the correct display convention directly against
`email_service.py`'s own existing consumer of this exact field (`f"{surprise:+.1f}%"`, no
`*100`) before writing the frontend — using `fmtPct()` on this field would have silently
100x'd every real value (e.g. a genuine `12.5` rendering as `+1250.0%`). Added a dedicated
`fmtSurprise()` helper instead, with a comment explaining why it's a separate function from
`fmtPct()`.

**Frontend**: `CalendarEvent` type gained the 5 new optional fields; `earnings.tsx`'s
`EventCard` gained a second row (only rendered when at least one of the new fields is
non-null) showing "Analyst target: $X (N firms)" — using the accuracy-weighted mean when
available, falling back to the simple mean — and "Beat history: N% (avg +X.X%)", color-coded
green/red on whether the beat rate clears 50%.

**Deliberately NOT added**: an upside/downside % versus the current price — `CalendarEvent`
has no current-price field, and adding one would mean threading in yet another data source per
calendar card just for this. A user can compare the shown analyst target against the real
price elsewhere (the stock detail page). Scoped this addition to what's cheaply and honestly
available rather than half-implementing a computed upside figure with a stale/missing price.

**Tests**: `services/market-data/tests/test_earnings_calendar_market_estimates.py` (5 cases,
source-text regression — `events_calendar()` can't be imported directly in this test
environment, matching `test_fundamentals_cache_miss_logging.py`'s established pattern for this
exact constraint). Covers: the 2 free fields read from the same cached blob (not a fresh
fetch), the consensus function called exactly once and only INSIDE the earnings block (never
once per stock in the outer per-active-stock loop, which would be a real, wasteful per-symbol
DB-query cost on every calendar request), the call happening before the `events.append()` that
reads its result, all 3 consensus fields read from the computed dict (not hardcoded), and the
5 pre-existing fields confirmed still present (a refactor mistake here could have silently
dropped one).

**Adversarially verified** — 2 sabotage cycles, both caught and reverted: removing all 5 new
fields entirely (caught by 2 dedicated tests); moving the consensus computation into the outer
per-stock loop instead of the earnings-only block — the exact wasteful-relocation mistake this
feature must avoid (caught by 2 dedicated tests, including a real `ValueError: substring not
found` when the call no longer existed inside the scoped block at all). Both reverted and
confirmed byte-identical via `diff` before moving on.

**Live latency check before deploying**: baseline `GET /stocks/events/calendar?days_ahead=45`
(63 events, 24 earnings) measured at 106ms before this change — re-measured after deploy to
confirm the 24 new consensus-query calls didn't meaningfully regress it.

Full 2020-test market-data suite green (up from 2015); pyflakes clean (all 6 remaining warnings
confirmed via `git stash` to predate this change — only line numbers shifted). Full 132-test
frontend vitest suite unaffected; `npx tsc --noEmit` and a full `next build` both clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_compute_weighted_analyst_consensus(session, stock.symbol)" /app/src/api/routes.py

# Live-check a real symbol's earnings event includes the new fields:
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/events/calendar?days_ahead=45' \
  -H "Authorization: Bearer <token>" | python3 -c "
import json, sys
events = json.load(sys.stdin)
for e in events:
    if e.get('type') == 'earnings' and e.get('symbol') == 'NVDA':
        print(e)
"
```
If the analyst-target fields are always `null` for a symbol you'd expect real coverage on,
check `GET /stocks/{symbol}/analyst-consensus` directly first — a genuinely empty `firms` list
there means no firm has issued a target for that symbol in the last 90 days, which is a real,
correct "no data" state, not a bug in this wiring.

---


## Feature Reference: Full Earnings History & Forward Consensus — EPS/Revenue Actuals + Chart + Market Estimates (Built 2026-08-25)

**User follow-up, verbatim**: after the analyst price-target consensus shipped ("I would like
to get the company and market estimate earning data not only the target stock price"), then
mid-turn: "Also, I would like to see the past Quarter data/history and the graph."

**Scope decision confirmed via `AskUserQuestion` before building**: one combined "Earnings
History & Estimates" section on the stock detail page (not split across multiple sections, not
a new standalone page) showing BOTH past-quarter actuals (with a chart) and forward-looking
market consensus together.

### What was investigated before building

Checked yfinance's real, live response for `earnings_estimate`, `revenue_estimate`, `eps_trend`,
`eps_revisions`, `quarterly_financials`, and `growth_estimates` against NVDA directly (not
assumed from documentation) — confirmed all 4 forward-consensus DataFrames and the historical
`quarterly_financials` statement return real, current data, each indexed by period
(`"0q"`/`"+1q"`/`"0y"`/`"+1y"`, sometimes `"LTG"`). `earnings_dates` was tried and found broken
in this environment (`Missing optional dependency 'lxml'`) — not used, since `quarterly_
financials`' own "Total Revenue" row already supplies what was actually needed (real historical
revenue), making the broken dependency moot.

### Backend — `services/market-data/src/api/routes.py`, `get_fundamentals()`

**`earnings_consensus`** (new field on `FundamentalsOut`) — forward-looking market estimates for
the next report: `earnings_estimate`/`revenue_estimate` (avg/low/high/analyst-count/growth),
`eps_trend` (current vs. 7/30/90 days ago — shows whether the Street is revising estimates up or
down), `eps_revisions` (analyst up/down-revision counts). Only the 4 real, priceable periods
(`"0q"`/`"+1q"`/`"0y"`/`"+1y"`) are kept — `"LTG"` (long-term growth) has no matching row in
`earnings_estimate`/`revenue_estimate` at all, so it's dropped rather than emitted
half-populated. `None` (not `{}`) when yfinance has zero consensus data for a symbol — the same
"absent means genuinely absent, never fabricated" convention already established for
`_prebreakout_calibration_for_band()` elsewhere in this codebase.

**A real, caught-before-shipping NaN hazard**: yfinance's own consensus DataFrames can carry
genuine `NaN` (confirmed live: `earnings_estimate`'s `yearAgoEps` for a thinly-covered symbol
with no comparable prior period, and `growth_estimates`' own `LTG` row) — `json.dumps(float
('nan'))` produces the literal, non-standard `NaN` token, rejected by a strict `JSON.parse` the
same way this repo's own documented `Infinity`-in-JSON incident (`updown_vol_ratio`) was. New
`_consensus_num(v)` helper explicitly checks `fv != fv` (the standard self-inequality NaN test)
and degrades to `None` — verified this is a REAL, reachable case via a live yfinance call before
writing the guard, not a hypothetical worry.

**`revenue_history`** (new field) — past-quarter ACTUAL revenue from `ticker.quarterly_
financials`' `"Total Revenue"` row, sorted oldest-first, `.dropna()`'d. Deliberately a SEPARATE
field from the pre-existing `eps_history` (which is actual-vs-ESTIMATE) — `quarterly_
financials` has no matching "what was estimated at the time" figure for revenue the way
`earnings_history` does for EPS, so this is real-values-only, not a comparison.

Both new fetches are wrapped in their OWN dedicated `try/except` blocks, isolated from each
other and from the pre-existing `eps_history` fetch — a failure in one must never prevent the
others from running. Neither is persisted to the DB `Fundamental` table (that table only ever
stored flat scalars, matching this app's own `create_all()`-gap invariant for adding new
columns to an existing table) — both are Redis-cache-only via the existing 24h-TTL
`FundamentalsOut` JSON blob, the same lifecycle `eps_history` already had.

### Frontend — `frontend/src/pages/stock/[symbol].tsx`

New `EarningsHistoryAndEstimates` component, placed right after the pre-existing "EPS Surprise
History" mini-cards (kept, unchanged — its per-quarter surprise-%.badges are still genuinely
useful and this new section doesn't duplicate them). Two hand-rolled SVG bar charts (matching
`ConfidenceTrend`'s established convention on this same page — no charting library) side by
side: EPS actual-vs-estimate (green=beat, red=miss, with a translucent "estimate" ghost bar
behind each real bar) and revenue-actual-only. Below the charts, a forward-consensus table: EPS
estimate + range, revenue estimate + range, analyst count, and 30-day up/down revision counts,
one column per period (`Next Qtr`/`Qtr After`/`This FY`/`Next FY`). A thin-coverage warning
("Thin analyst coverage — treat this consensus as low-confidence") shows when the max analyst
count across all periods is ≤3.

### Tests

`services/market-data/tests/test_earnings_consensus_and_revenue_history.py` (14 cases) —
`_consensus_num()` is directly, behaviorally tested via source-text `exec()` extraction
(pure, dependency-free function, matching `test_fundamentals_cache_miss_logging.py`'s
established `_extract_log_helper()` technique): real numbers pass through unchanged, `None`
stays `None`, a real `NaN` degrades to `None` (and a JSON round-trip of that result is
confirmed to never contain the literal `NaN`/`Infinity` tokens), non-numeric strings degrade
safely, and a real `0` is preserved (not falsy-zero-coerced to `None`, a distinct bug class
this repo has caught before elsewhere). `get_fundamentals()`'s own wiring is covered via
source-text regression checks (the function can't be imported directly in this test
environment — matches `test_fundamentals_empty_fetch_guard.py`'s established pattern): only the
4 real periods are kept, all 4 yfinance sources are read, `earnings_consensus` defaults to
`None` not `{}`, the consensus fetch is isolated in its own try/except separate from
`eps_history`'s, `revenue_history` reads the right DataFrame row, is sorted oldest-first,
drops NaN rows, and is isolated in its own try/except.

**Adversarially verified** — 2 sabotage cycles, both caught and reverted: removing the NaN
self-inequality check from `_consensus_num()` (caught by 2 dedicated tests, with a real,
concrete demonstration that `json.dumps(float('nan'))` genuinely produces the literal `NaN`
token — not a hypothetical); removing `.dropna()` from the revenue-history extraction (caught
by its own dedicated test). Both reverted and confirmed byte-identical via `diff`.

Full 2034-test market-data suite green (up from 2020); pyflakes clean (all 6 remaining warnings
confirmed via `git stash` to predate this change — only line numbers shifted). Full 132-test
frontend vitest suite unaffected; `npx tsc --noEmit` and a full `next build` both clean
(`/stock/[symbol]` grew 54.6kB → 56.1kB, a reasonable size for a new chart+table section).

**Live latency check before deploying**: measured the real, expanded yfinance fetch (info + 6
DataFrames: `earnings_estimate`/`revenue_estimate`/`eps_trend`/`eps_revisions`/
`earnings_history`/`quarterly_financials`) against NVDA directly — 0.54s total. Acceptable
given this whole endpoint is already Redis-cached for 24h per symbol (the existing, unchanged
`_FUND_TTL` lifecycle), not a hot-path cost.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "def _consensus_num\|ticker.quarterly_financials" /app/src/api/routes.py

# Live-check a real symbol's full response includes both new fields:
docker exec stockai-market-data-1 curl -s 'http://localhost:8001/stocks/NVDA/fundamentals?refresh=true' \
  -H "Authorization: Bearer <token>" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('earnings_consensus keys:', list((d.get('earnings_consensus') or {}).keys()))
print('revenue_history:', d.get('revenue_history'))
"
```
If `earnings_consensus`/`revenue_history` are always `null`/`[]` for a symbol you'd expect real
coverage on, check whether the SAME symbol's `ticker.earnings_estimate`/`quarterly_financials`
return real data directly via yfinance first — a genuinely thin-coverage or newly-listed stock
correctly has nothing here, which is not a bug in this wiring.

---


## Feature Reference: AUD-EARNINGSFORECAST — On-Demand Pre-Earnings LLM Forecast (Built 2026-08-26)

**User ask, verbatim**: "Can we do a forecast on the upcoming earnings report like NVDA
tomorrow. What's the 'Whisper Number' and forward guidance. And how to interpret the market
impact like [an attached scenario table]. And the Broad Macro Impact: The Tech Bellwether
Effect. Can you show me the a Modal or popup with these information when I clicked on the
stock with Earnings in the events calendar. Or where would you recommend" — followed by an
explicit cost-minimization instruction ("we can use LLM for assistance but how to minimal the
cost?... make one call for both") and a placement choice ("Both" — modal AND a stock-detail
section).

**Verified before designing anything, not assumed**: a live yfinance query confirmed NO
"whisper number" or forward-guidance TEXT field exists anywhere in this data source — both are
Wall-Street-analyst-community concepts (a whisper number is an informal, crowd-sourced
estimate distinct from the published consensus; forward guidance is management's own verbal/
written commentary) that no free data provider in this app's stack captures. Rather than
fabricate either, the feature uses an LLM to interpret the REAL data this app already has
(analyst consensus, revision trend, beat-rate history, growth-vs-index) into the requested
scenario-table format — an honest substitute, not a synthetic "whisper number."

### Cost-minimal, on-demand design — the FIRST click-triggered (not scheduled-poll) LLM
### feature in this codebase

Every prior Claude-calling feature in this session's own history (macro reaction, earnings
impact, weekly theme signals, weekly trade coach) is either a scheduled poll or fires on a
data-sync event. This one is genuinely different: `generate_earnings_forecast()`
(`services/event-intelligence/src/services/earnings.py`) only ever runs when a user actually
clicks into a stock's upcoming earnings event — never a background job. **One combined Claude
call** produces the narrative (`watching_for`) AND the fixed 3-row scenario table
(`scenarios`) AND an optional bellwether note (`bellwether_note`) together, per the user's own
explicit "make one call for both" instruction — never 2-3 separate calls. Cached 24h per
symbol (`_FORECAST_CACHE_TTL_S`), so repeat clicks on the same symbol within a day cost
nothing extra. Gated behind a new, default-OFF admin flag
(`earnings_llm_forecast_enabled`), matching every other opt-in Claude feature's own convention
since the `CLAUDE-API-COST-AUDIT` incident.

### Data sources — all real, all already computed by this session's own earlier work

- `earnings_consensus`/`growth_vs_index` — both already fetched by `GET /stocks/{symbol}/
  fundamentals` (`AUD-EARNINGSCONSENSUS`, `AUD-EARNINGSFORECAST`'s own new
  `growth_vs_index` field). `_fetch_fundamentals_sync()` reuses this SAME 24h-cached blob via
  a sync `httpx.get()` to market-data — no second yfinance fetch, no new data pipeline.
- `growth_vs_index` (new): `ticker.growth_estimates` (yfinance), `stockTrend`/`indexTrend`
  columns, same period-key structure (`"0q"`/`"+1q"`/`"0y"`/`"+1y"`) as `earnings_consensus` —
  this stock's own projected growth vs. the broader market index's, the real data behind the
  requested "Tech Bellwether Effect" read. A dedicated `_growth_num()` NaN-guard, deliberately
  an INDEPENDENT copy of the sibling `earnings_consensus` block's own `_consensus_num()` — a
  real scoping bug was caught and avoided during development: `_consensus_num` is defined
  INSIDE a sibling `try:` block a few lines above, and referencing it from a separate `try:`
  block would risk a real `NameError` if that earlier block's own try raised before ever
  reaching its `def` line.

### Executor-wrapped blocking I/O

`_fetch_fundamentals_sync()` is a genuinely blocking sync call — reused via `earnings.py`'s
pre-existing `_executor = ThreadPoolExecutor(max_workers=4)` (already built for a different
purpose, `T249-EARNINGS-LLM-IMPACT`'s own docstring explains why the httpx.AsyncClient calls
elsewhere in this file DON'T need it) + `loop.run_in_executor(...)`, matching this file's own
established `AUD-EI-MACRO-REACTION-BLOCKING` fix pattern exactly — never a bare synchronous
call inside an `async def` that would block the shared event loop for every other concurrent
request this service is serving.

### A real bug caught and fixed during development, before shipping

The lazy `from common.redis_client import get_redis` import originally sat ONE LINE ABOVE its
own enclosing `try:` block (unlike the sibling `check_earnings_impact_poll()`, whose identical
import sits INSIDE its own try) — meaning a genuinely broken/missing `common.redis_client`
module in production would raise a raw, uncaught `ModuleNotFoundError` past this whole
function's fail-open contract, crashing the caller instead of degrading gracefully. Fixed by
moving the import inside the try, matching the sibling function's shape exactly. A dedicated
regression test (`test_the_lazy_redis_client_import_lives_inside_the_flag_check_try_block`)
locks this in — adversarially verified by reverting the import's position and confirming the
test fails with a real, meaningful line-order assertion.

### A second real bug found in the TEST HARNESS itself, distinct from the production bug above

Writing tests for this feature surfaced a genuine, previously-undiscovered gap in `event-
intelligence/tests/conftest.py`: `sys.modules.setdefault("common.redis_client", MagicMock())`
registers the submodule under its OWN dotted key, but does NOT link it as an attribute on the
parent `common` MagicMock — Python's real import machinery normally does this bookkeeping
itself for a genuine package, but a bare MagicMock parent has no such behavior. Without an
explicit link, `from common.redis_client import get_redis` (the exact statement `earnings.py`
uses) resolves via `getattr(sys.modules["common"], "redis_client")` — which auto-vivifies a
**completely different, unlinked** child mock, not the one registered in
`sys.modules["common.redis_client"]`. A test patching either the `sys.modules` entry directly,
OR pytest's own dotted-string `monkeypatch.setattr("common.redis_client.get_redis", ...)` form
(which resolves via the IDENTICAL `getattr` path internally — confirmed by reading
`_pytest.monkeypatch.resolve()`'s own source), would silently observe a mock the real
production import never reaches. This ALSO retroactively explains why the pre-existing sibling
tests for `check_earnings_impact_poll()` had been passing for the wrong reason the whole time:
that function's own `from common.redis_client import get_redis` line sits inside a bare
`try: ... except Exception: return {"skipped": "feature_disabled"}` — so the `ModuleNotFoundError`
this gap produces was ALWAYS being silently swallowed, coincidentally landing on the exact
return value those tests expect, regardless of whether Redis was ever genuinely reached.

**Fix applied**: `conftest.py` now explicitly links each submodule onto the parent mock after
registration (`for _m in ("config", "logging", "redis_client"): setattr(sys.modules["common"],
_m, sys.modules[f"common.{_m}"])`), mirroring the identical explicit-link fix already
established for `common.indicators` in `market-data/tests/conftest.py`. This is a genuinely
shared, cross-service test-infrastructure gap — `market-data/tests/conftest.py`'s own
`_cfg.get_settings = MagicMock(...)` line has the SAME underlying issue (confirmed directly:
`get_settings() is _cfg.get_settings` returns `False` there too) — left undocumented/unfixed
in that service since it doesn't block anything there today (no test in that service currently
depends on 2 separate references to the same mocked attribute actually being identical), but
worth knowing if a similarly-shaped test failure appears there in the future.

### Frontend — modal AND stock-detail section, per the user's own "Both" answer

New `frontend/src/components/EarningsForecastPanel.tsx` — the shared content renderer (real
consensus context always shown; the LLM narrative/scenario-table/bellwether-note section shown
when available, a clear "no forecast yet" state when the admin flag is off or the call fails).
Deliberately factored out as its OWN component (not duplicated) so both placements can never
silently drift apart from each other.

- **Modal placement**: `frontend/src/components/EarningsForecastModal.tsx` (the fixed-overlay
  shell, matching `positions.tsx`'s established modal pattern exactly), triggered by a new
  "🔮 Forecast" button on `frontend/src/pages/earnings.tsx`'s `EventCard`, next to the existing
  "🔔 Alert me" button.
- **Stock detail placement**: folded into the existing `EarningsHistoryAndEstimates`
  component (`frontend/src/pages/stock/[symbol].tsx`, `AUD-EARNINGSCONSENSUS`'s own section) —
  renders below the "Market Consensus (Forward)" table whenever `f.days_to_earnings != null`,
  using the page's own already-fetched `symbol`/`data.price?.sector`/`f.days_to_earnings`, no
  new network round-trip beyond the forecast call itself.

**Route**: `GET /events/earnings/forecast` (event-intelligence) — always returns a real 200
with a `{"forecast": ... | null}` shape, never a 404/403 when the admin flag is off, so the
frontend can render its own real consensus data unconditionally and simply omit the LLM
section when null (matching this codebase's established "an optional LLM addition must never
block a real data display" convention).

**Admin toggle**: `earnings_llm_forecast_enabled`, added to `admin-ai-features.tsx`'s Global
card right after "Earnings Impact Analysis" — 6-touch-point wiring in `market-data/src/api/
admin.py` (`_REDIS_EARNINGS_FORECAST_ENABLED` constant, `ConfigRequest` field, both
`get_feature_flags`/`get_feature_flags_public` reads, the `update_config` write-guard, the
write branch, the audit log line), matching every other flag's established pattern exactly.

### Tests

`services/event-intelligence/tests/test_earnings_forecast.py` (34 cases) — `_clean_scenarios()`'s
whole-result-degrades-to-None contract (deliberately stricter than the sibling
`_clean_sector_list()`'s partial-degrade convention, since this feature's entire value IS the
tailored table), `_nearest_forecast_period()`'s `"0q"`-key resolution, `_fetch_fundamentals_
sync()`'s real HTTP fail-open matrix, and `generate_earnings_forecast()`'s full pipeline
(flag gate, API-key gate, cache hit/corrupted-cache-fallthrough/write-failure, fundamentals
fetch failure, missing `"0q"` consensus, malformed/fenced JSON, truncation, and the try-block-
ordering regression guard). `services/market-data/tests/test_earnings_consensus_and_revenue_
history.py` gained 15 new cases for the `growth_vs_index` fetch block, extending the file's own
established source-text-extraction convention — including a dedicated regression test
(`test_growth_num_is_an_independent_copy_not_a_reference_to_consensus_num`) that directly
EXERCISES the extracted `_growth_num()` function in isolation (proving it never calls the
sibling `_consensus_num`, rather than a fragile substring check that would false-positive on
the function's own explanatory comment — a real test-writing mistake caught and fixed during
development).

**Adversarial verification** — every real guard sabotaged and confirmed to fail correctly,
then restored and confirmed byte-identical via `md5sum`: `event-intelligence/tests/
conftest.py`'s explicit parent-mock link (removing it reproduces all 7 of the original,
real-cause-identified test failures exactly); the try-block-import-ordering fix in
`generate_earnings_forecast()`; `_growth_num()`'s NaN self-inequality guard; and the
`growth_vs_index` block's own try/except isolation (removing it correctly broke the dedicated
isolation test with a real substring-not-found failure).

Full 318-test event-intelligence suite (up from 284) and 2043-test market-data suite (up from
2028) green; `pyflakes` clean on every touched backend file (confirmed via `git stash` that
every remaining warning predates this change). Frontend: `npx tsc --noEmit` clean, full
132-test vitest suite unaffected, a full `next build` clean (all 51 routes;
`/earnings`/`/stock/[symbol]`/`/admin-ai-features` all confirmed via direct grep to contain the
new content in their actual compiled chunks, not just correct-looking source).

**What to check if this looks wrong**:
```bash
docker exec stockai-event-intelligence-1 grep -n "generate_earnings_forecast\|_REDIS_EARNINGS_FORECAST_ENABLED" /app/src/services/earnings.py
docker exec stockai-market-data-1 grep -n "growth_vs_index" /app/src/api/routes.py

# Confirm the admin flag's current state:
docker exec stockai-redis-1 redis-cli get stockai:admin:feature:earnings_llm_forecast_enabled

# Live-check the forecast endpoint for a real symbol with an upcoming report (needs the flag ON):
docker exec stockai-event-intelligence-1 curl -s 'http://localhost:8010/events/earnings/forecast?symbol=NVDA&sector=Technology&days_to_event=1' \
  -H "Authorization: Bearer <token>" | python3 -m json.tool

# Check the per-symbol forecast cache directly:
docker exec stockai-redis-1 redis-cli get 'stockai:earnings_forecast:NVDA'

# Confirm the compiled frontend bundles contain the new UI:
docker exec stockai-frontend-1 sh -c "grep -o 'Broad Macro Impact' /app/.next/static/chunks/pages/earnings-*.js /app/.next/static/chunks/pages/stock/\[symbol\]-*.js"
```
If the modal/section always shows "No AI forecast available" despite a real upcoming report,
first confirm `earnings_llm_forecast_enabled` is actually `1` in Redis — this feature is
off by default on every fresh deploy, matching every other opt-in Claude feature's own
convention.

---


## Feature Reference: AUD-EARNINGSFORECAST-EXTEND — Real Post-Earnings Reaction History Feeds the LLM Forecast (Built 2026-08-26)

**Closes the gap this session's own AUD-EARNINGSFORECAST entry left open**: `EarningsEvent.
post_earnings_return_1d`/`post_earnings_return_5d` were real, DEFINED columns that no job in
this codebase ever wrote — confirmed via grep before building this. The PRE-earnings forecast
feature's own `_FORECAST_SYSTEM` prompt previously had no way to ground `typical_reaction` in a
stock's own real history, only generic market-pattern language.

**`_compute_post_earnings_returns(bars, report_date)`** (`services/event-intelligence/src/
services/earnings.py`) — pure, dependency-free bar-index math. Baseline is the LAST close
STRICTLY BEFORE `report_date` (not `report_date`'s own close), so the measurement is consistent
regardless of BMO/AMC timing. `ret_1d`/`ret_5d` each independently gated on having enough real
after-bars (`len(after) >= 2` / `>= 6`) — never a calendar-day offset, matching `gate_harness.
py`'s own T196 convention that a fixed number of TRADING days must be used, not calendar days.
Returns `(None, None)` rather than a fabricated/partial value when there isn't enough real bar
history on either side.

**`backfill_post_earnings_returns()`** — new daily cron (06:40 UTC, right after `sync_earnings`
at 06:30), genuinely separate from `check_earnings_impact_poll()`'s 5-min interval: a 5-trading-
day-later return can't be measured any faster than real trading days elapse regardless of poll
cadence. Reads `Price` DIRECTLY (event-intelligence already has DB access to the shared model)
rather than an HTTP round-trip to market-data — cheaper than `generate_earnings_forecast()`'s
own `_fetch_fundamentals_sync()`. Per-row `try/except` isolation (one symbol's price-fetch
failure must never abort the whole batch), bounded to a 45-day lookback window.

**`_fetch_past_reactions_sync(symbol, limit=4)`** — a sync, blocking DB read, wrapped in
`_executor` exactly like `_fetch_fundamentals_sync()`'s own established fix
(`AUD-EI-MACRO-REACTION-BLOCKING`'s pattern) — a bare sync call inside an `async def` would
block the shared event loop for every other concurrent request this service serves. Returns up
to 4 most-recent reports with a real, measured `return_1d`/`return_5d`, most-recent-first.

**Prompt extension**: `_FORECAST_SYSTEM` now instructs the LLM to ground `typical_reaction` in
a stock's own REAL history WHEN AVAILABLE AND GENUINELY RELEVANT to that specific scenario row
(e.g. a real prior beat's real 1-day move for the "Beat + Raise" row), but to fall back to
general market-pattern education when the real history doesn't support a given scenario (e.g.
no real past misses to draw on for "Miss or Cut") — explicitly still never a prediction of the
UPCOMING report's own outcome, matching this feature's original honesty discipline.

**A real test-writing bug caught and fixed, not shipped**: `_FakePriceModel` (the query-plumbing
stand-in in `test_post_earnings_returns.py`) originally defined only `stock_id`/`timeframe`/`ts`
as class attributes — `select(Price.ts, Price.close)` in the real code raised `AttributeError:
type object '_FakePriceModel' has no attribute 'close'` before ever reaching the fake session,
silently producing `filled=0` for every test regardless of whether the real fill logic worked.
Diagnosed by temporarily disabling the outer per-row `except` and printing the real caught
exception directly — the fix was adding `close = _FakeColumn()` to the fake model. A second,
separate test bug: `test_leaves_a_row_null_...` originally asserted `ev.post_earnings_return_1d
is None` against a bare `MagicMock()` — whose default auto-vivified attribute is a `MagicMock`,
never `None`, so this assertion was checking nothing real. Fixed by replacing the `MagicMock`
fixture with a real `_FakeEvInstance` object whose fields genuinely start `None`.

**Also, mid-session: an accidental `git checkout -- earnings.py`** (run during an md5sum-based
adversarial-verification revert, before this feature's own commit existed) discarded the whole
uncommitted extension from the working tree — recovered by re-applying every edit from the
`sed`/`grep` reads already captured in the same turn, then confirmed via the full 334-test
suite + pyflakes (only pre-existing warnings) that the reconstruction was functionally
identical, not just checksum-matched.

**Tests**: `services/event-intelligence/tests/test_post_earnings_returns.py` (10 cases) —
`_compute_post_earnings_returns()`'s full boundary matrix (baseline-strictly-before, no-bar-
before/after, zero baseline, independent 1d/5d resolution) plus `backfill_post_earnings_
returns()`'s fill/skip/isolation behavior. `test_earnings_forecast.py` gained 6 new cases —
`_fetch_past_reactions_sync()`'s fail-open matrix (unknown symbol, real rows, no measured
reactions yet, DB exception) and 2 for the prompt-wiring (`past_reactions` reaches both the
prompt text and the final result; an empty history degrades the prompt gracefully to
"unavailable" rather than crashing).

**Adversarial verification** — 3 sabotage/revert cycles, all caught and reverted (confirmed
byte-identical via `md5sum` before moving on): the baseline-selection line (`before[-1][1]` →
`after[0][1]`, the exact BMO/AMC bug this design avoids) — caught by 5 of 10 tests including the
dedicated baseline test; the per-row `try/except` isolation removed entirely — caught by exactly
the dedicated isolation test with a real, unguarded `ConnectionError`; the `past_reactions`
fetch call removed from `generate_earnings_forecast()` (replaced with a hardcoded `[]`) — caught
by the dedicated prompt-wiring test.

Full 334-test event-intelligence suite green; `pyflakes` clean (all 4 remaining warnings
confirmed via `git stash` to predate this change).

**What to check if this looks wrong**:
```bash
docker exec stockai-event-intelligence-1 grep -n "def _compute_post_earnings_returns\|def backfill_post_earnings_returns\|def _fetch_past_reactions_sync" /app/src/services/earnings.py

# Confirm the daily backfill job actually ran and populated real rows:
docker logs stockai-event-intelligence-1 --since 24h | grep backfill_post_earnings_returns
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT s.symbol, e.report_date, e.post_earnings_return_1d, e.post_earnings_return_5d FROM earnings_events e JOIN stocks s ON s.id = e.stock_id WHERE e.post_earnings_return_1d IS NOT NULL ORDER BY e.report_date DESC LIMIT 10;"

# Manually trigger the backfill job (safe — idempotent, only touches rows still missing the field):
docker exec stockai-event-intelligence-1 python3 -c "
import asyncio, sys; sys.path.insert(0, '/app')
from src.services.earnings import backfill_post_earnings_returns
print(asyncio.run(backfill_post_earnings_returns()))
"
```
If `past_reactions` is always `[]` in a real forecast response despite a symbol having recent
resolved reports, first confirm the backfill job has actually populated `post_earnings_return_
1d` for that symbol's rows — `_fetch_past_reactions_sync()` only reads already-persisted values,
it never computes on the fly.

---


## Feature Reference: AUD-EPSTRENDROW-DEADFIELDS — A Fabricated Survey Finding Hid a Real One (2026-08-26)

**Context**: after AUD-EARNINGSFORECAST-EXTEND shipped, 2 parallel survey agents were launched
for the next improvement batch — one covering `paper_trading_engine.py`/`hard_rejects.py`/
`gate_harness.py` parity, one covering real-money/data-integrity frontend pages. Both results
were personally re-verified against the real current source before any code was touched,
matching this session's own repeatedly-demonstrated discipline that a background survey
agent's report is a claim to check, never a fact to act on.

**Survey 1 (paper trading engine/decision-engine parity) came back genuinely clean** — a
well-disciplined "nothing found" after cross-checking every candidate against CLAUDE.md's own
extensive dated fix history for those exact files, including confirming the T232-DL-
DUALSCORER-DEBT gate-porting series really is complete (every gate in `_should_enter()` has a
verified twin in `hard_rejects.py`). No action taken — an honest negative result, not padded
with a manufactured finding.

**Survey 2 (frontend real-money pages) reported one specific finding with quoted code and a
named failure mechanism**: a claimed `period.eps_trend?.[k]` nested-object read (keyed by
`'current'`/`'7daysAgo'`/`'30daysAgo'`/`'90daysAgo'`) in `EarningsHistoryAndEstimates`
(`frontend/src/pages/stock/[symbol].tsx`), described as silently dropping the "Est. Now" row
whenever yfinance omits the `current` field.

**Verified directly and found the quoted code does not exist anywhere in this codebase** — a
`grep -n "eps_trend"` against the real file returned zero matches for any nested-object access
pattern. The real field shape (confirmed in `frontend/src/lib/api.ts`) is 4 FLAT fields:
`eps_trend_current`/`eps_trend_7d_ago`/`eps_trend_30d_ago`/`eps_trend_90d_ago` on each
`earnings_consensus` period row — never a nested object indexed by a string-key array. The
described "silent mislabeling" mechanism was entirely fabricated, matching the exact class of
false-positive this session has already hit multiple times with other survey agents.

**Rather than simply dropping the disproven finding, checked one level further** (per this
session's own standing discipline that a fabricated CLAIM doesn't rule out a real, adjacent
GAP existing underneath it) — and found one: `eps_trend_current`/`_7d_ago`/`_30d_ago`/
`_90d_ago` ARE real, correctly-fetched, correctly-typed fields (confirmed present on the API
response and already CONSUMED by the earnings-forecast LLM prompt itself, per
`AUD-EARNINGSFORECAST`'s own "EPS revision trend" prompt line), but were never rendered
anywhere in the stock detail page's "Market Consensus (Forward)" table — a real, previously-
undocumented, low-severity gap (unused-but-real backend data, not a display bug affecting an
already-shown number).

**Fix**: added an "Est. trend (30d ago→now)" row to the existing consensus table, directly
below the pre-existing "Revisions (30d)" row — `eps_trend_30d_ago → eps_trend_current` per
period, reusing the table's own established green-up/red-down color convention (matching every
other row in the same table, not a new style). Zero backend changes needed — purely an
additive frontend rendering fix using data already fetched.

**Verification**: `npx tsc --noEmit` clean, full 132-test frontend vitest suite unaffected
(no test imports `stock/[symbol].tsx` directly), a full `next build` clean (`/stock/[symbol]`
56.5kB → 56.6kB), confirmed via direct grep that "Est. trend" reached the actual compiled
`stock/[symbol]` chunk, not just source.

**Design invariant reinforced**: a survey finding that turns out to be fabricated on close
inspection is not automatically a dead end — checking the SAME area of code the fabricated
claim pointed at, with fresh eyes rather than trusting either "the finding is real" or "the
finding is fake, move on," is what surfaced this real gap. Both directions of skepticism
(don't trust an agent's claim; don't over-correct into ignoring the area entirely once a claim
in that area is disproven) matter.

**What to check if this looks wrong**:
```bash
docker exec stockai-frontend-1 sh -c "grep -o 'Est. trend' /app/.next/static/chunks/pages/stock/\[symbol\]-*.js"
```

---

