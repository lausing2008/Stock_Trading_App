## Claude API Cost Audit (2026-07-28) — Full Usage Map + Fix for the Real Leak

**User ask, verbatim**: "review the claude api AI assistant uaage, still burning fast. Where
are we using it? Document it and see if we can use it in the right place."

### Every real Claude/Anthropic call site in the app (9 total)

| # | Site | Model | Trigger | Cache | Gate |
|---|---|---|---|---|---|
| 1 | news-intelligence `classify_headlines()` | Haiku | poll every 1-2 min, 24/7 | DB URL-dedup (fixed, see BUG-NEWSCLASSIFY-REPEATCOST) | always on |
| 2 | market-data `_claude_sentiment()` | Haiku | per stock-page view | Redis 4h TTL | always on |
| 3 | market-data `_claude_market_themes()` | Haiku | per Market Pulse view | Redis 30min TTL | always on |
| 4 | event-intelligence `generate_reaction()` | Haiku | armed poll, real macro days only | none needed (rare) | always on |
| 5 | decision-engine `llm_scorer.py` | Haiku | every 5-min scan cycle IF enabled | Redis 6h TTL | **off by default** |
| 6 | decision-engine `risk_agent.py` | Haiku | same as #5 | Redis 6h TTL | **off by default** |
| 7 | research-engine `_call_claude()` (full report) | **Sonnet** | auto-triggered every ~5 min scan cycle + user clicks | in-memory dict, 24h TTL, portfolio-param-scoped | **was always-on, no opt-out — fixed here** |
| 8 | research-engine `chat_research()` | Sonnet | user chat message | none | user-initiated |
| 9 | research-engine `ai_proxy.py` chat | Sonnet | user chat message | none | user-initiated |

**Live production evidence (checked directly on EC2, last 24h)**: news-intelligence showed
0 real classify calls (its own dedup fix from an earlier session was holding). Research-engine
showed **72 real `research.generated` events in 24h — every one a full Sonnet report
generation**, concentrated on roughly 3 symbols (~24/symbol/day vs. an intended ~4/symbol/day)
— the actual dominant cost driver, confirmed via logs, not assumed from code alone.

### Root cause — two independent, compounding bugs, both in the auto-trigger path

**Bug A**: `_auto_trigger_research()` (`services/market-data/src/services/scheduler.py`)
queried `Signal` joined to `Stock`, ordered by confidence, `LIMIT 5` — with **no dedup by
symbol**. `Signal` has 4 horizon rows per stock (SHORT/SWING/LONG/GROWTH); a stock that's a
≥65%-confidence BUY across 3 horizons could occupy 3 of the 5 "top" slots. Confirmed live in
production logs: `[RXT, SMTC, RXT, MU, RXT]` — only 3 distinct symbols in 5 slots. This
function runs inside `_refresh_market()`, fired ~77×/day for US alone (open burst, every 5 min
intraday, close burst, post-close) plus a comparable HK schedule — so a triplicated symbol
could trigger repeatedly across many refresh cycles per day.

**Bug B**: `trigger_research()`'s (`services/research-engine/src/api/routes.py`) "6-hour
cooldown" was a plain `_cache.get(symbol)` age-check on an in-memory dict — not a lock, not
the same guard `generate_research()` itself uses (`_inflight_research`, an
`asyncio.Event`-keyed dict). Since `_auto_trigger_research()`'s loop fired all 5 (here: 3×
RXT) `/trigger` HTTP calls back-to-back synchronously, each background generation task started
independently, and each one's `_cache.get()` check ran BEFORE the first one's generation had
written back to the cache — all 3 passed the "cooldown" and all 3 queued a real Claude call.
The genuine in-flight dedup only exists one hop later, inside `generate_research()` — too late
to stop `/trigger` itself from firing the duplicate background tasks in the first place.

### Fix (user chose: fix both bugs + gate auto-trigger behind an opt-in flag)

1. **`scheduler.py`'s `_auto_trigger_research()`** — the symbol query now does
   `select(Stock.symbol, func.max(Signal.confidence)).group_by(Stock.symbol)` instead of a
   bare `select(Stock.symbol, Signal.confidence)` — each symbol occupies exactly one of the
   top-5 slots regardless of how many BUY-confidence horizon rows it has. A per-symbol Redis
   `SET NX EX` lock (`stockai:auto_research_sent:{symbol}`, `nx=True`,
   `ex=_AUTO_RESEARCH_COOLDOWN_S` = 21,600s, matching research-engine's own 6h cache window)
   now gates every `/trigger` POST — even if duplicate rows somehow survived, or overlapping
   refresh cycles fired close together, only the first call within the cooldown window can
   ever proceed; every other candidate that fails the lock logs `"status":
   "cooldown_local"` and `continue`s to the next symbol without aborting the whole cycle.
   The whole function is now also gated behind a global admin feature flag (Redis
   `stockai:admin:feature:auto_research_enabled`, checked and fail-closed FIRST, before any
   DB query or HTTP call) — **default OFF**, since this was the only Claude-calling feature in
   the app with no opt-in/opt-out anywhere, despite being the single most expensive
   model+prompt combination (full Sonnet report generation).
2. **`routes.py`'s `trigger_research()`** — now also checks `_inflight_research` (the SAME
   dict `generate_research()` already uses to dedupe concurrent generations) after the
   pre-existing cache-age check, before scheduling the background task. If the symbol is
   already registered there (a prior trigger's background task has already reached
   `generate_research()` and is actively generating), this returns `{"status":
   "already_in_flight"}` instead of also queuing a second, duplicate background task.
   **Deliberately READ-ONLY** — never writes to `_inflight_research` itself.
3. **`admin.py`** — `auto_research_enabled` added to `ConfigRequest`, both
   `GET /admin/feature-flags` / `/feature-flags/public`, and `POST /admin/config`'s write
   branch — mirroring the pre-existing `broker_enabled` flag's exact 4 touch-points.

**A self-caught near-miss during implementation, not shipped**: the first draft of fix #2
synchronously pre-registered `_inflight_research[sym] = asyncio.Event()` in
`trigger_research()` itself, immediately before scheduling the background task, reasoning this
would close the race window as tightly as possible. This is a real, would-have-shipped
deadlock: `_generate_with_service_token()` (what the background task actually calls) makes a
genuine, separate outbound `httpx.AsyncClient` POST back to this SAME service's own
`/research/{sym}` endpoint — a fresh request that re-enters `generate_research()` completely
independently. That fresh call's own `if sym in _inflight_research:` check finds the
pre-registered entry and takes the "someone else is already generating this — wait for them"
branch (`await asyncio.wait_for(_inflight_research[sym].wait(), timeout=60.0)`) — but nothing
ever calls `.set()` on that Event, since the actual generation logic never runs (the function
took the wait branch, not the owner branch). Every single real trigger would have silently
gained a 60-second hang before falling through to generate anyway, with zero benefit. Caught
by tracing exactly what `_generate_with_service_token()` does before trusting the draft;
fixed by making the check strictly read-only. `test_trigger_research_inflight_check.py`'s
`test_trigger_research_never_writes_to_inflight_research_itself` guards against this exact
regression recurring — adversarially verified by reintroducing the pre-registration line and
confirming that one test (and only that one) failed correctly before reverting.

**Tests**: `services/market-data/tests/test_auto_research_cost_audit.py` (8 cases, source-text
regression checks — `scheduler.py` can't be imported directly in this test environment) cover
the feature-flag gate ordering and fail-closed behavior, the `GROUP BY`/aggregated-`ORDER BY`
dedup fix, the `SET NX EX` lock's exact parameters and ordering relative to the HTTP POST, and
the cooldown-window constant matching research-engine's own 6h cache TTL.
`services/market-data/tests/test_auto_research_admin_flag.py` (7 cases) — a genuine, direct
behavioral test (admin.py imports cleanly under this test environment's real
fastapi/pydantic), covering `get_feature_flags`/`get_feature_flags_public`/`update_config`'s
read/write/omit-leaves-untouched/guard-still-fetches-redis behavior for the new flag, plus a
cross-file check that admin.py's and scheduler.py's independently-hardcoded Redis key
literals actually match. `services/research-engine/tests/
test_trigger_research_inflight_check.py` (5 cases, source-text — `trigger_research` is
decorator-wrapped and the decorator is a `MagicMock` in this test environment, so it can't be
called directly) cover the in-flight check's presence, ordering (before the background-task
schedule, after the pre-existing cache-age check), and the critical
never-writes-to-`_inflight_research` deadlock-avoidance property.

**Adversarial verification** — every guard in all 3 fixes sabotaged and reverted, all caught
correctly: the `GROUP BY` dedup (2 tests caught it), the feature-flag gate (2 tests caught
it), the Redis lock's `nx=True` (3 tests caught it), admin.py's `update_config` guard
controlling whether Redis gets fetched at all (3 tests caught it, one via a genuine
`AttributeError` — confirming the guard prevents a real crash, not just a value mismatch), and
research-engine's in-flight check both by removing it entirely (3 tests caught it) and by
reintroducing the deadlock-prone pre-registration specifically (the one dedicated test built
for exactly this caught it). Full 579-test market-data suite and research-engine suite (61 of
64 tests, 3 pre-existing unrelated `test_scoring.py` failures confirmed via `git stash` to
predate this session) both green; `pyflakes` clean on all 3 touched files (confirmed via `git
stash` that every pre-existing warning predates this change).

**What to check if this looks wrong**:
```bash
# Confirm the feature flag's current state:
docker exec stockai-redis-1 redis-cli get stockai:admin:feature:auto_research_enabled

# Check whether auto-trigger fired recently and how many distinct symbols vs. slots:
docker logs stockai-market-data-1 --since 24h | grep 'scheduler.auto_research_triggered'

# Check the per-symbol lock state directly:
docker exec stockai-redis-1 redis-cli keys 'stockai:auto_research_sent:*'
docker exec stockai-redis-1 redis-cli ttl 'stockai:auto_research_sent:<SYMBOL>'

# Check research-engine's real generation volume over the last 24h (the actual cost signal):
docker logs stockai-research-engine-1 --since 24h | grep -c 'research.generated'

# Toggle the flag directly (needs an admin JWT):
docker exec stockai-market-data-1 python3 -c "
import sys, uuid, time; sys.path.insert(0,'/app'); sys.path.insert(0,'/app/src')
from common.config import get_settings; from jose import jwt as _jwt; import httpx
s = get_settings()
tok = _jwt.encode({'sub':'lausing','jti':str(uuid.uuid4()),'exp':int(time.time())+86400}, s.jwt_secret, algorithm='HS256')
r = httpx.post('http://localhost:8001/admin/config', json={'auto_research_enabled': True}, headers={'Authorization': f'Bearer {tok}'}, timeout=10)
print(r.status_code, r.json())
"
```

**Design invariant**: any future Claude/Anthropic-calling feature added to this codebase
should default to an opt-in admin feature flag (matching `llm_scoring_enabled`/
`risk_check_enabled`'s existing per-portfolio convention, or a global Redis flag like this
one and `broker_enabled` for features with no natural per-portfolio scope) UNLESS it is
already tightly cached/rate-limited by design (per-symbol page-view caches, armed
release-day-only polls) — a feature with no opt-out and a real production trigger frequency
(a scan cycle, a poll) is exactly the shape that produced this incident.

**Not yet built (explicit follow-up request from the user, in progress)**: a page under
Admin listing every AI-Assistant-calling feature with real on/off toggles and explanatory
copy — see the tracker/next session for the frontend half of this work.

**Built same session** — see the Admin AI Assistant Features page section immediately below.

---

