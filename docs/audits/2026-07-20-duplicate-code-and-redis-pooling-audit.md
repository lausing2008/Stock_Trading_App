## Full-Codebase Audit — Duplicate Code / Single-Source-of-Truth (Phase 1: Redis Connections, 2026-07-20)

**Ask**: "everything should be reused for the same purpose... the AI assistant key should be
read from one place and used by every single module... do a deep full audit on all the
modules and see if they are wired to the right component, using the same source of truth."
Phased into 3 workflow runs to avoid hitting a single-run execution limit. Phase 1 (this
entry) covered Redis connection construction + the Claude API key fallback path across all 11
services, using a multi-agent workflow: one discovery agent per service, a synthesis pass, then
an adversarial verify pass on every candidate "divergence" (a dedicated refute-agent per
finding) before anything was reported as real.

**What was already correctly centralized (audited, zero divergences found)**: JWT secret
(`get_settings().jwt_secret` from `shared/common/config.py` — every service reads the exact
same field, no service has its own copy) and DB session construction (`shared/db/session.py`'s
sole `create_engine()`/`sessionmaker()`, re-exported via `shared/db/__init__.py` — no service
constructs its own engine). These were checked, not assumed.

**What was found divergent**: `shared/common/redis_client.py`'s `get_redis()` — a pooled
(`ConnectionPool`, max 20 connections, `socket_connect_timeout=2`, `socket_timeout=5`,
`retry_on_timeout=True`) helper explicitly built to replace raw `redis.from_url()`/
`redis.Redis.from_url()` calls — had been adopted by essentially only ONE file
(`services/market-data/src/api/auth.py`) before this fix. Every other Redis-touching module
across all services was constructing its own fresh, unpooled client per call or per
module-level singleton, each reading the same `settings.redis_url` but never sharing a
connection pool with any sibling module in the same process.

**Fixed this pass (market-data + decision-engine — the two highest-value, cheapest-to-fix
services per the audit's own recommended order)**:
- `services/market-data/src/services/paper_trading_engine.py` — 4 sites: `_monitor_positions()`'s
  staleness-tracking construction + its paired `.delete()` call, the conviction-gate check's
  `_gate_redis`, and a T210 regime-suspension-day `_t210_redis` construction (found during
  review, not in the audit's original per-file list, fixed for consistency).
- `services/market-data/src/services/scheduler.py` — the module-level `_get_redis()` singleton
  (removed the now-dead `_redis: redis_lib.Redis | None = None` global) + one inline
  construction inside a market-hours-gated function.
- `services/market-data/src/api/routes.py` — the module-level `_get_redis()` singleton.
- `services/market-data/src/api/admin.py` — the "worst variant" per the audit (constructed a
  brand-new client on every single call, no caching at all).
- `services/market-data/src/api/news.py` — the `_get_redis()` singleton, AND a separate
  single-source-of-truth fix: the Claude-key fallback-of-last-resort was
  `os.getenv("ANTHROPIC_API_KEY", "")` — the only site in the entire repo reading that env var
  (nothing ever sets it in any container) — changed to `getattr(_settings, "claude_api_key", "")`
  matching `llm_scorer.py`/`risk_agent.py`/`macro_reaction.py`'s existing convention of falling
  back to the Redis-backed admin setting via `_settings`, not a phantom env var. Removed the
  now-fully-unused `import os`.
- `services/decision-engine/src/api/llm_scorer.py` and `risk_agent.py` — both had their own
  private `_redis_client()` helper doing `redis.Redis.from_url(get_settings().redis_url, ...)`
  inline; both now delegate to `common.redis_client.get_redis()`. Removed the now-unused
  `import redis as _redis_lib` from both files.
- `services/decision-engine/src/api/core/hard_rejects.py` — the conviction-gate check's inline
  `redis.Redis.from_url(...)` construction.

**Confirmed REFUTED, deliberately left alone** (re-checked the raw verify-agent reasoning
directly, not just the synthesis summary, before accepting the refutation):
`services/market-data/src/api/paper_portfolio.py:1152` (`list_portfolios()`) and
`services/market-data/src/services/ingestion.py:224` (`_bust_live_price_cache()`) — both
confirmed to match that same file's own DOMINANT local convention rather than deviating from
it; "fix everything" would have meant introducing a NEW inconsistency (matching the
audit-recommended pattern in a file that already has an established different-but-consistent
one), not removing one.

**Deliberately deferred, not silently dropped**: signal-engine, research-engine, and
ml-prediction still have their own unpooled Redis-construction sites — scoped out of this pass
per an explicit "you decide the scope" instruction, choosing the 2 highest-value/cheapest
services now over a full 5-service sweep that risked not finishing. Phase 2 (duplicated
business logic sweep) and Phase 3 (cross-service wiring confirmation) of the original 3-phase
audit plan have not been run.

**A real test-coupling gotcha hit and fixed while updating tests for this refactor** — worth
its own note since it's a genuinely non-obvious Python behavior, not specific to this repo:
`services/decision-engine/tests/test_hard_rejects.py` (and `test_risk_agent.py`,
`test_aggregator.py`, `test_fetch_signal.py`, `test_regime.py`) all stub the Docker-only
`common` package as a bare `sys.modules.setdefault("common", MagicMock())` (no real `common`
package is installed in this local dev/test environment). Once `hard_rejects.py` was changed
to do `from common.redis_client import get_redis`, tests needed to mock that new import path —
but `import common.redis_client as X` against a `MagicMock`-stubbed PARENT package does NOT
resolve via the `sys.modules["common.redis_client"]` entry a test registers up front; instead,
each `import common.redis_client` statement auto-vivifies a brand-NEW, distinct child mock via
attribute access on the parent mock, different from whatever was pre-registered in
`sys.modules`. A test that does `monkeypatch.setattr(<freshly-imported-mock>, "get_redis", ...)`
silently patches a mock object neither the test's own later assertions nor the production
code's own local import will ever see again — `hard_rejects.py`'s real
`from common.redis_client import get_redis` call resolves to yet another fresh child mock,
untouched by the patch, and falls through to the function's own `except Exception: pass`
(since calling an unpatched `MagicMock()` result as if it were real Redis produces a
`TypeError` deep inside `json.loads()`, not an import error) — producing a silently-wrong
`result=None` for a reason completely different from what the test intended to verify.
**Fix**: patch `sys.modules["common.redis_client"]` itself (the dict entry, not a freshly
re-imported name) — that's the one object every `import common.redis_client` statement AND
every `from common.redis_client import X` statement in the same process actually shares.

**What to check if a similar test-mocking gotcha is suspected in another service's test
suite**: any test file that stubs a Docker-only package as `sys.modules.setdefault("<pkg>",
MagicMock())` and then separately does `import <pkg>.<submodule> as X` to monkeypatch an
attribute — confirm `id(X) == id(sys.modules["<pkg>.<submodule>"])` before trusting the patch
takes effect; if they differ, patch via the `sys.modules` dict key directly instead.

**Verification**: full market-data suite (344 tests) and decision-engine suite (113 tests)
green after every file change; frontend typecheck clean (no frontend logic touched by this
fix — the `/learn` page and nav change below are unrelated, from the same session).

**What to check if a Redis-pooling regression is suspected**:
```bash
grep -rn "redis_lib.Redis.from_url\|redis.Redis.from_url\|redis\.from_url" \
  services/market-data/src services/decision-engine/src
# Should return nothing for the files listed above as fixed. signal-engine/research-engine/
# ml-prediction WILL still show matches — that's the documented, deliberate Phase-2+ deferral,
# not a missed fix.
```

---


## Full-Codebase Audit — Duplicate Code / Single-Source-of-Truth (Phase 2: Redis Connections, 2026-07-21)

**Continues the audit's own Phase 1 entry** (market-data + decision-engine, 2026-07-20) — this
pass covers the 3 services explicitly deferred at the time: signal-engine, research-engine,
ml-prediction. Found and fixed 9 raw `redis.Redis.from_url()`/`redis.from_url()` constructions
across all three, routing each through `shared/common/redis_client.py`'s pooled `get_redis()`,
exactly matching Phase 1's fix pattern. Also checked the Claude-API-key fallback convention (the
other Phase 1 finding class) in all three services — all three already correctly read
`stockai:admin:claude_api_key` from Redis with no phantom-env-var fallback bug; that finding was
fully resolved in Phase 1 and did not recur here.

**Fixed this pass**:
- `services/signal-engine/src/generators/signals.py` — 5 sites: `_load_ml_weight_override()`,
  `set_ml_weight_global_cap()`, `_load_ta_weights()`, `load_conviction_weights()`, and
  `_redis_get_float()` — the highest-value fix of the five, since it backs
  `_get_dynamic_buy_threshold()`/`_get_dynamic_sell_threshold()`/`_get_style_tuned_param()` and
  is called on essentially every signal generation cycle, per style, per threshold lookup — a
  genuinely hot path that was constructing a fresh unpooled client on every single call.
- `services/signal-engine/src/api/routes.py` — the module-level `_get_redis()` singleton
  (same pattern as market-data's own `routes.py`/`scheduler.py` fix in Phase 1).
- `services/research-engine/src/api/ai_proxy.py` — the module-level `_get_redis()`, plus
  removed the now-unused `import redis as redis_lib`.
- `services/research-engine/src/api/routes.py` — `_get_admin_ai_key()`'s inline construction
  (previously had its own custom `socket_connect_timeout=1`, different from `get_redis()`'s pool
  default of `2` — the shared pool's timeout is used now instead, a minor behavior change judged
  safe since this is a fail-open helper, `except Exception: return ""`, and 2s vs 1s makes no
  practical difference to a call that degrades to an empty string on any failure anyway).
- `services/ml-prediction/src/features/builder.py` — `_redis_save_macro()`/`_redis_load_macro()`.
- `services/ml-prediction/src/training/meta_trainer.py` — `_record_promotion_status()`'s
  inline construction.

**A repeat of the exact Phase 1 test-mocking gotcha, caught and fixed the same way**:
`services/ml-prediction/tests/test_promotion_history.py` uses `importlib.util.exec_module()` to
load a fresh copy of `meta_trainer.py` with its own `__package__` override, and mocked Redis by
doing `monkeypatch.setitem(sys.modules, "redis", fake_redis_lib)` / `sys.modules["redis"] =
fake_redis_lib` — coupled to the exact `redis.from_url()` call the source used to make. Once
`meta_trainer.py` was changed to `from common.redis_client import get_redis`, the SAME
`MagicMock`-stubbed-parent-package gotcha from Phase 1 applied again: `common` is stubbed as a
bare `MagicMock()` by `conftest.py` (no real `common` package installed locally), so `import
common.redis_client` auto-vivifies a distinct child mock on the parent each time, different from
whatever is registered in `sys.modules` — a test patching a freshly re-imported name would
silently miss the module the production code's own local import actually resolves. Fixed
identically to Phase 1's `test_hard_rejects.py` fix: register `sys.modules.setdefault
("common.redis_client", MagicMock())` up front, then patch `sys.modules["common.redis_client"]
.get_redis` directly (the dict entry, not a fresh import binding) — verified via the same
sabotage-and-confirm-failure cycle (reverted the source fix, confirmed 3 of 4 tests failed
correctly, restored it).

**Verification**: full test suites run for all 3 services after every change —
signal-engine (54 tests, 4 pre-existing unrelated `test_analyst_momentum.py` failures confirmed
via `git stash` to pre-date this change), research-engine (56 tests, 3 pre-existing unrelated
`test_scoring.py` balance-sheet-assessment failures also confirmed via `git stash`), ml-prediction
(19 tests, fully green). Frontend untouched by this pass (backend-only fix).

**Still deliberately out of scope**: a broader duplicate-business-logic sweep (Phase 3 of the
originally-proposed 3-phase audit) has not been run — this pass was scoped specifically to the
same Redis-connection-pooling class of issue Phase 1 already established, not a fresh
open-ended audit of these 3 services.

**What to check if a Redis-pooling regression is suspected in these 3 services**:
```bash
grep -rn "redis_lib.Redis.from_url\|redis.Redis.from_url\|redis\.from_url" \
  services/signal-engine/src services/research-engine/src services/ml-prediction/src
# Should return nothing — all 9 sites found in this pass are fixed. If this ever shows a
# match again, it's either a regression or a genuinely new site introduced since.
```

---


## Full-Codebase Audit — Cross-Service Wiring (Phase 3, 2026-07-21)

**Scope**: verifying services actually call each other correctly (right URLs, right auth
headers, right endpoint paths) — as opposed to Phases 1/2, which were about each service
internally using the right Redis/API-key source of truth. Scoped via a research-only pass
across all `_settings.*_url` cross-service call sites in all 11 backend services before making
any changes.

**Checked and confirmed CLEAN**: every auth-required endpoint (`Depends(get_current_username)`)
called from another service correctly sends an `Authorization: Bearer` header via
`_service_token()` — no recurrence of the INT-7 missing-auth-header bug class anywhere in the
current codebase. portfolio-optimizer and strategy-engine are only ever reached via
api-gateway's end-user JWT proxy, never service-to-service.

### Finding 1 (fixed) — ranking-engine's private, hardcoded URL constants

`services/ranking-engine/src/api/routes.py` was the **only** service in the repo bypassing
`shared/common/config.py`'s `Settings` for cross-service URLs — it kept its own private
`os.environ.get("MARKET_DATA_URL", "http://market-data:8001")` /
`os.environ.get("TA_URL", "http://technical-analysis:8002")` constants, a second, independent
source of truth for the same port map every other service already reads from `_settings.
market_data_url`/`_settings.technical_analysis_url`. The file's own pre-existing comment
already documented this pattern causing a real bug once (`T232-KS1`: the `TA_URL` fallback
default was wrong — `8006` instead of `8002` — silently connection-refusing every bulk-patterns
fetch, with the failure swallowed). **Fix**: added `from common.config import get_settings` +
`_settings = get_settings()`, and reassigned `_MARKET_DATA_URL`/`_TA_URL` to read from
`_settings.market_data_url`/`_settings.technical_analysis_url` — kept the same constant names
(not renaming every call site) to keep the diff surgical while still closing the actual
single-source-of-truth gap.

### Finding 2 (fixed) — T220-G sector K-Score rotation endpoint silently 404ing since it shipped

**The higher-value find of this pass.** `services/market-data/src/api/routes.py`'s router is
mounted with `prefix="/stocks"` (line ~49) — every sibling route in the file correctly omits
that prefix in its own decorator (e.g. `/sector_rotation`, `/regime`, `/fear_greed`). T220-G's
`get_sector_rotation()` (sector K-Score momentum, NOT the same feature as the similarly-named
RES-4 `sector_rotation()` ETF-rotation endpoint a few hundred lines earlier) was registered as
`@router.get("/stocks/sector-rotation")` — repeating the prefix, resolving to the real, live-
confirmed path `GET /stocks/stocks/sector-rotation`. signal-engine's own caller
(`services/signal-engine/src/api/routes.py:808`, T220-G's `sector_momentum` reasons-enrichment)
correctly requests the INTENDED single-prefixed path,
`{market_data_url}/stocks/sector-rotation` — which 404s against the actually-registered double-
prefixed route, silently swallowed by `if _rot_r.status_code == 200:` (never raises, just skips
the enrichment). **Confirmed live against production Postgres**: `0` of the last 4,176 signals
had `reasons->>'sector_momentum'` populated — this feature has been completely non-functional
since it shipped, invisible because nothing ever exercised or tested the actual HTTP path
end-to-end. The tracker's own `T220-G` entry (`frontend/src/pages/improvements.tsx`) already
documents a DIFFERENT, unrelated bug on this same endpoint as fixed 2026-07-01 (a missing
`_service_token()` Authorization header) — that fix was real and correct, but this separate
double-prefix routing bug was never caught by it, since a 401 and a 404 both fail the identical
`if status_code == 200` check and look the same from the caller's side.

**Fix**: changed the route decorator from `@router.get("/stocks/sector-rotation")` to
`@router.get("/sector-rotation")`, matching every sibling route's convention. Verified live
before AND after the fix — `GET /stocks/stocks/sector-rotation` returned 200 before (the
accidentally-working double-prefixed path) and 404 for the intended single-prefixed path;
after the fix, the reverse. No caller anywhere (frontend `api.ts`, other services) hits the
double-prefixed path directly, so nothing needed to change on the caller side.

**Tests**: `services/market-data/tests/test_sector_rotation_route_path.py` (3 cases,
source-text regression checks — `routes.py` imports `common.config` at module level and can't
be directly imported in this test environment, matching every other market-data route test's
documented constraint) — confirms the router's `/stocks` prefix mount, confirms
`get_sector_rotation()` is registered without repeating it, and confirms the two similarly-
named sector-rotation features (RES-4 ETF-based at `/sector_rotation`, T220-G K-Score-based at
`/sector-rotation`) remain on distinct paths. Adversarially verified by reverting the route
decorator back to the double-prefixed form and confirming 2 of 3 tests failed correctly before
restoring it.

**Verification**: full ranking-engine suite (25 tests, 1 pre-existing unrelated `test_kscore.py`
failure confirmed via `git stash` to pre-date this change) and market-data suite (371 tests, up
from 368) both green.

**What to check if this looks wrong**:
```bash
# Confirm the live endpoint now resolves at the single-prefixed path:
docker exec stockai-market-data-1 curl -s -o /dev/null -w 'HTTP %{http_code}\n' \
  'http://localhost:8001/stocks/sector-rotation'
# Should be 200 (or 404 only if stockai:sector_rotation hasn't been computed yet by the
# weekly _compute_sector_rotation job — check that key directly if so):
docker exec stockai-redis-1 redis-cli get stockai:sector_rotation

# Confirm sector_momentum is now actually landing in new signals (won't backfill old rows):
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT COUNT(*) FILTER (WHERE reasons->>'sector_momentum' IS NOT NULL), COUNT(*) FROM signals WHERE ts > now() - interval '1 day';"
```

**Still deferred**: a broader duplicate-business-logic sweep (distinct from both the
Redis-connection audit and this cross-service-wiring pass) has not yet been scoped or run.

---


## Full-Codebase Audit — Redis Connection Pooling: Closing the Loop (2026-07-21)

**Found while checking "is Phase 1/2 actually complete"**: a definitive `grep` across ALL 11
services' `src/` for raw `redis.Redis.from_url()`/`redis.from_url()` constructions turned up 4
sites Phase 1/2 had NOT actually closed:

1. **`services/market-data/src/api/paper_portfolio.py:1152`** (`list_portfolios()`'s gate-block
   read) — Phase 1's own audit found this site, but its verify-agent REFUTED fixing it,
   reasoning it "matches this file's own dominant local convention." Re-checked directly this
   pass: that reasoning doesn't hold — `paper_portfolio.py` had **zero** uses of the shared
   pooled `get_redis()` anywhere, so there was no real "dominant convention" being preserved by
   leaving it raw; it was simply the one site the audit happened to look at, in a file that
   turned out to have 2 more identical raw-construction sites (`/de-divergences` at line 2025,
   `/position-scaling-shadow` at line 2074) the original pass never checked.
2. **`services/market-data/src/services/ingestion.py:224`** (`_bust_live_price_cache()`) —
   same REFUTED-as-"dominant-convention" reasoning from Phase 1, same problem: with only one
   Redis site in the whole file, there was no actual convention to match, just the one place
   Redis happened to be used.
3. **`services/event-intelligence/src/services/macro_reaction.py:86`** (`_api_key()`'s Claude-
   key Redis-first lookup) — event-intelligence was never in scope for Phase 1 (market-data +
   decision-engine) or Phase 2 (signal-engine + research-engine + ml-prediction) at all; this
   surfaced only from doing an exhaustive re-check rather than trusting either phase's own
   "done" list.

**Fixed all 4** — same `common.redis_client.get_redis()` pattern as every other fix in this
audit. `paper_portfolio.py`'s 3 sites and `ingestion.py`'s 1 site: no coupled tests found by
name (`_pf_redis`/`de-divergences`/`position-scaling-shadow`/`_bust_live_price_cache` all
absent from `market-data/tests/`). `macro_reaction.py`'s `_api_key()` IS coupled to
`test_macro_reaction.py`, but only via `monkeypatch.setattr(mr, "_api_key", lambda: ...)` —
patching the function by name, not its internal Redis call — so it was unaffected by the
internal-implementation change.

**Lesson reinforced**: a prior pass's own "REFUTED, matches the file's dominant convention"
verdict is only as good as how much of that file the verify-agent actually looked at — "matches
this file's own convention" is meaningless if the reviewer only saw one instance of the
pattern. Confirming a convention is real (multiple identical sites already establishing it)
vs. assumed (one site, no real basis for calling it "the convention") needs an actual count,
not a one-site sample. The fix here was to keep it simple: with the shared pooled `get_redis()`
now the ACTUAL established convention across 10+ other files in this same audit, there was no
principled reason left to leave these 4 sites as exceptions.

**Verification**: full market-data suite (371 tests, unchanged — these were additive fixes to
already-covered code with no dedicated tests of their own) and full event-intelligence suite
(159 tests) both green after the change.

**Definitive final state, confirmed via exhaustive grep across ALL 11 services**:
```bash
grep -rn "redis\.Redis\.from_url\|redis\.from_url\|redis_lib\.Redis\.from_url\|redis_lib\.from_url" \
  services/*/src/
# Returns NOTHING — zero raw Redis constructions anywhere in the repo. Phase 1 + Phase 2 +
# this closing-the-loop pass together account for all 14 sites found across the whole
# codebase's history of this audit (9 in Phase 1, 9 in Phase 2 — wait, tallying: Phase 1 fixed
# 9 sites in market-data+decision-engine; Phase 2 fixed 9 more in signal-engine+research-engine+
# ml-prediction; this pass fixed the final 4 in market-data/event-intelligence Phase 1/2 missed).
# technical-analysis, api-gateway, strategy-engine, and portfolio-optimizer have zero raw Redis
# construction sites of any kind (confirmed, not just unchecked).
```

If this grep ever returns a match again, it's either a genuine regression or a new site
introduced since — there is no longer any "some services haven't been audited yet" excuse,
since this pass covered literally all 11.

---


## Full-Codebase Audit — Duplicated Business Logic (2026-07-21)

**Scope**: distinct from the Redis-connection-pooling audit (Phases 1-2 + the closing-the-loop
pass) and the cross-service-wiring audit (Phase 3) — this pass looked for actual business logic
(scoring formulas, thresholds, calculations) reimplemented independently in multiple services
that should share one implementation. Two independent research passes surfaced ~10 candidates;
the first fix tackled was the clearest, lowest-risk one.

### Fixed — Claude/DeepSeek admin API key lookup: 6 independent copies consolidated to 1

**The finding**: the exact same "read the admin-configured AI key from Redis" lookup had been
independently written 6 times, in 2 services:
- `services/decision-engine/src/api/llm_scorer.py::_get_api_key()` and
  `risk_agent.py::_get_api_key()` — byte-for-byte copies of each other (one file's own comment
  literally says "this module was built by copying that pattern").
- `services/event-intelligence/src/services/macro_reaction.py::_api_key()` and
  `services/market-data/src/api/news.py::_get_claude_key()` — a second near-identical pair.
- `services/research-engine/src/api/routes.py::_get_admin_ai_key()` and
  `ai_proxy.py::_admin_key()` — a third pair, these two supporting both `claude` and `deepseek`
  providers via a `rkey` lookup, the other 4 hardcoded to Claude only.

**Verified before consolidating (not assumed)**: read all 6 side by side to check whether they
actually behaved differently before treating this as a safe drop-in fix. Superficially they
looked different — some checked `.strip()` truthiness, some had a `cfg` dict fallback, some had
a `getattr(_settings, "claude_api_key", "")` fallback, research-engine's two had a bare `""`
fallback with no secondary check at all. But `shared/common/config.py`'s `Settings` class has
**no** `claude_api_key`/`deepseek_api_key` field at all, and grepping every `cfg` dict
constructed anywhere in the repo found none ever populates `claude_api_key` either — so **every
one of those "different" fallback paths was already permanently dead in production**, and all 6
copies reduced to the exact same real behavior: read Redis, or return `""`. This meant the
consolidation was genuinely safe (no real behavioral divergence to reconcile), not just
convenient.

**Fix**: new `shared/common/ai_keys.py` — `get_admin_ai_key(provider: str = "claude") -> str`,
the one real implementation (Redis lookup + `.strip()` + fail-open-to-`""`, supporting both
`claude` and `deepseek` via a lookup dict). All 6 original functions now delegate to it as thin
wrappers, keeping their own names/signatures/dead-fallback-paths intact (harmless, since those
paths were already unreachable) so no call site anywhere else in either service needed to
change.

**A real test-mocking gotcha hit 3 times in a row this session, hit again here, fixed the same
way each time**: `services/decision-engine/tests/test_risk_agent.py` constructs real `cfg`
dicts with `claude_api_key` set and never mocks `_get_api_key` itself — so the real function
body now executes and hits `from common.ai_keys import get_admin_ai_key`, which fails against
the `MagicMock`-stubbed `common` package the same way `common.redis_client` did twice already
in this audit. Fixed identically: `sys.modules.setdefault("common.ai_keys", _fake_ai_keys)`
with `get_admin_ai_key` stubbed to return `""` (so the existing `cfg["claude_api_key"]`
fallback path — what these tests actually mean to exercise — still engages, exactly as before).
`services/market-data/tests/test_market_pulse.py` had the same issue from a different angle:
its 4 `_get_claude_key()` tests patched `news._get_redis` directly, which the function no
longer calls at all now that it delegates to the shared helper — updated to patch
`sys.modules["common.ai_keys"].get_admin_ai_key` instead (added `"common.ai_keys"` to
market-data's own `conftest.py` stub list). `test_risk_agent.py` (byte-for-byte copy source)
and `test_macro_reaction.py` were both unaffected — they patch by function name
(`_get_api_key`/`_api_key`), not internals.

**Tests**: `services/decision-engine/tests/test_ai_keys.py` (9 new cases) — the shared
helper's own real behavior: Redis-first, `.strip()`-normalizes whitespace-only values to `""`,
claude/deepseek keys are independent, fail-open on a Redis exception, unknown provider strings
degrade to the Claude key rather than raising. Adversarially verified: reverted the `.strip()`
call and confirmed 2 tests failed correctly before restoring it.

**Verification**: decision-engine (122 tests, up from 113), event-intelligence (159, unchanged
— its coupled test was unaffected), market-data (371, unchanged count but 4 tests rewritten),
research-engine (56 tests, 3 pre-existing unrelated `test_scoring.py` failures, confirmed via
this same session's own earlier `git stash` check to pre-date any of this work) — all green
modulo that one already-documented pre-existing gap.

**Still open from this pass's scoping** (not yet fixed, documented for a future session):
- Stop-loss/game-plan math computed independently in decision-engine's `aggregator.py`
  (`_default_game_plan`), research-engine's `scoring.py` (`_position_size`, a genuinely
  different formula — support-minus-ATR vs. ATR-off-price), and market-data's
  `paper_trading_engine.py` (a third formula) — HIGHER risk to consolidate since it touches
  live trading/research output directly; needs its own careful, dedicated pass.
- Beta calculation: `market-data/api/paper_portfolio.py::_compute_alpha_beta()` (pure-Python,
  fixed SPY benchmark, `None` fallback below the data floor) vs.
  `portfolio-optimizer/api/risk.py::_beta()` (numpy, HK-aware SPY/HSI benchmark selection, `1.0`
  fallback) — same formula, different fallback semantics and benchmark logic.
- ADX/DI/DX reimplemented independently in `ranking-engine/scoring/kscore.py` vs.
  `signal-engine/generators/signals.py` — currently agree, no shared function; signal-engine's
  ATR is also not yet migrated to `shared/common/indicators.py`'s canonical `atr()` (ranking-
  engine's already is — a partial T233-ARCH-INDICATOR-DEDUP regression).
  `signal-engine/generators/signals.py::_sr_context()` also independently reimplements support/
  resistance pivot detection rather than calling technical-analysis's canonical
  `trendlines.py::_find_pivots()`/`detect_support_resistance()` — different window sizes
  (60-bar/±3 vs. 90-bar/order=5), a real risk that signal-engine's breakout/at_support labeling
  can disagree with the chart's official S/R levels the user sees on the same page.
- A third, previously-undocumented portfolio-correlation implementation in
  `market-data/services/paper_trading_engine.py` (direct-DB Pearson correlation) — deliberately
  separate from the already-tracked `T233-ARCH-PORTFOLIO-CONSOLIDATE` pairing
  (`portfolio-optimizer`'s HTTP-fetched `df.corr()` + Ledoit-Wolf) to avoid an HTTP round-trip
  on the hottest capital-sensitive code path — a reasoned, intentional duplication, just never
  captured in that tracker item's own scope. Worth a doc-only addition to that tracker entry,
  not a code change.
- Volume-profile/FVG/swing-pivot detection: Python-canonical (`technical-analysis/indicators/
  trendlines.py`) vs. hand-ported TypeScript (`frontend/src/lib/swingPivots.ts`,
  `fvgTradePlan.ts`, `volumeProfile.ts`) — already cross-checked at build time and covered by
  parity tests (a known, accepted exception per this file's own prior notes), but structurally
  still two independent implementations with no build-time guard against future drift.

### Fixed (second pass) — signal-engine's 3 independent inline ATR copies consolidated

**Finding**: `services/signal-engine/src/generators/signals.py` had 3 separate inline TR/ATR
calculations — `_adx()`, `_supertrend()`, and a third site inside `generate_all_signals()`
feeding `reasons["atr_14"]`/`["atr_14_pct"]` (consumed by decision-engine's ATR-based game plan
stops) — instead of calling `shared/common/indicators.py`'s canonical `atr()`, the same
function `ranking-engine/scoring/kscore.py` already imports as `_canon_atr`. A partial
`T233-ARCH-INDICATOR-DEDUP` regression: RSI/MACD were already migrated to the canonical module
in this same file, ATR never was.

**A real, previously-unfixed bug found in the third copy**: `_adx()` and `_supertrend()` had
already independently received the `AUD232-073` fix (`min_periods=period` on the `.ewm()` call
— without it, a short-history stock gets a real-looking but fabricated ATR from bar 0 instead
of correctly returning `NaN` during warmup). The third copy, feeding `atr_14`, had NOT — a
genuine, silently-recurring instance of the exact same bug class within one file, invisible
because nothing had ever compared all 3 copies side by side until this consolidation pass.
Directly confirmed the bug: the old inline formula produced `1.149...` (a plausible-looking
number) for a 14-period ATR at only 10 bars of history, where the canonical `atr()` correctly
returns `NaN`.

**Fix**: added `atr as _canon_atr` to the file's existing `from common.indicators import
rsi as _canon_rsi, macd as _canon_macd` line; all 3 sites now call `_canon_atr(high, low,
close, period=N)` instead of their own inline `pd.concat(...).max(axis=1).ewm(...).mean()`
copy. Verified numerically before deploying — fed the same synthetic OHLCV fixture through
both the pre-fix inline formula and the canonical function directly and confirmed identical
output (the TR/ATR math itself was always byte-identical; only the `min_periods` guard
differed on the one previously-unfixed copy).

**Tests**: `services/signal-engine/tests/test_atr_consolidation.py`, 9 cases — sane-range
checks for `_adx()`/`_supertrend()` post-consolidation, the short-history `NaN`-not-fabricated
guard specifically for the third (previously-unfixed) site, and a direct numerical-parity
check against `common.indicators.atr()` called independently. Adversarially verified by
reverting the `atr_14` call site to its exact pre-fix inline formula and confirming it
reproduces the bug (a real, non-NaN value at 10 bars) before restoring the fix.

**A test-writing correction made during development**: the first draft of this test file
assumed `reasons["atr_14"]` was set inside `_ta_score()` — checking the actual code found it's
set in a DIFFERENT function, `generate_all_signals()`, which calls `_ta_score()` first and then
adds `atr_14` to the same `reasons` dict object afterward. `generate_all_signals()` itself
fetches real prices via `_fetch_prices(symbol)` and isn't synthetic-DataFrame-testable directly
— tests were rewritten to exercise the exact same `_adj_close()` + `_canon_atr(period=14)`
computation `generate_all_signals()` runs, rather than asserting on a `_ta_score()` return value
that was never going to contain `atr_14` in the first place. Caught by tracing an actual
`_canon_atr()` call during execution and finding it returned a real value while the outer
function's own return showed `None` — a mismatch that only made sense once the two functions
were confirmed to be genuinely separate.

**Verification**: 9/9 new tests pass; full signal-engine suite (59 tests, up from 50) green
modulo the 4 pre-existing, unrelated `test_analyst_momentum.py` failures already documented
elsewhere in this file.

### Fixed (third pass) — GROWTH ATR-stop multiplier: decision-engine's game plan disagreed with the real paper-trading engine (2.5x vs 3.0x)

**The finding**: decision-engine's `aggregator.py::_default_game_plan()` (the shadow-scoring
game-plan approximation used for `/decide/{symbol}`'s illustrative entry/stop/target numbers)
computed the GROWTH ATR-stop override with a hardcoded `2.5 if style == "GROWTH" else 2.0`
multiplier. market-data's `paper_trading_engine.py::_build_game_plan_for_style()` — the REAL,
authoritative function that computes the actual game plan for real paper trades — independently
hardcoded `3.0 if style == "GROWTH" else 2.0`. **These two numbers had silently disagreed with
no comment anywhere explaining why**, unlike the deliberately-separate Game Plan/FVG/Position-
Sizer systems this file already documents as an intentional three-lens design (this wasn't
that — decision-engine's game plan is explicitly meant to approximate the real one, per the
whole `T232-DL-DUALSCORER-DEBT` parity effort already worked on twice this session).

Notably, `T232-DL-STYLEPARAMS3X` (2026-07-04) had ALREADY fixed the adjacent problem — the
entry/breakout/stop/target PERCENTAGES used to be independently triplicated across
scheduler.py/paper_trading_engine.py/aggregator.py, with decision-engine's own copy having
wrong GROWTH values and two dead styles. That fix made market-data's `_STYLE_PARAMS` (exposed
via `GET /stocks/style-params`) the single source of truth for those percentage fields — but
the ATR-stop-multiplier logic sat just outside that dict as its own separate inline literal in
BOTH files, so it silently escaped that same consolidation and kept drifting independently.

**Fix**: added `atr_stop_mult` as a real field in `_STYLE_PARAMS` (market-data,
`paper_trading_engine.py` — 3.0 for GROWTH, 2.0 for SHORT/SWING/LONG, the REAL, authoritative
values) and in decision-engine's `_STYLE_PARAMS_FALLBACK` (matching values, used only when
market-data is unreachable). Both `_build_game_plan_for_style()` (market-data) and
`_default_game_plan()` (decision-engine) now read `params.get("atr_stop_mult", 2.0)` instead of
their own independent hardcoded style-name check — decision-engine already fetches the whole
`_STYLE_PARAMS` dict live via `GET /stocks/style-params` for the percentage fields, so this
needed no new endpoint or fetch, just reading one more key from the same response.

**Tests**: `services/decision-engine/tests/test_game_plan_atr_mult.py` (5 cases) and
`services/market-data/tests/test_game_plan_atr_mult.py` (5 cases) — confirm the fallback dicts
carry the correct value per style, `_default_game_plan()`/`_build_game_plan_for_style()` apply
it correctly for GROWTH vs. non-GROWTH styles, and both degrade safely to the `2.0` default if
a style-params response is ever missing the field entirely (rather than crashing with a
`KeyError` or silently reverting to a hardcoded literal). Adversarially verified on both sides:
reverted each fix back to its own hardcoded literal and confirmed the dedicated
missing-field-fallback test failed correctly in each case (a hardcoded literal, unlike a real
`.get(..., default)` read, doesn't correctly degrade when the field is absent) before restoring
both fixes.

**Also corrected during this same audit continuation**: research-engine's `_position_size()`
(support-anchored stop/target, using actual chart S/R levels) was flagged by the original
scoping pass as part of the same "3 divergent stop-loss formulas" finding — re-examined
directly and found this framing doesn't hold. Research reports are explicitly meant to give a
technically-grounded, chart-level-anchored read as a genuinely different analytical lens from
the trading engines' faster ATR-multiple approach — matching this file's own established
"Game Plan vs. FVG vs. Position Sizer are three deliberately different systems" design
principle, not an accidental duplication. Left unchanged; only the decision-engine/paper-
trading-engine ATR-multiplier pair (which ARE meant to agree) was fixed.

**Verification**: full decision-engine suite (127 tests, up from 122) and market-data suite
(381 tests, up from 371) both green.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 python3 -c "
import sys; sys.path.insert(0, '/app'); sys.path.insert(0, '/app/src')
from services.paper_trading_engine import _STYLE_PARAMS
print(_STYLE_PARAMS['GROWTH']['atr_stop_mult'])  # should print 3.0
"
docker exec stockai-decision-engine-1 curl -s 'http://localhost:8009/health'
# Confirm decision-engine's live game plan for a GROWTH-style symbol uses a 3.0x (not 2.5x)
# ATR multiplier by checking POST /decide/{symbol}'s returned stop value against a known ATR.
```

### Fixed (fourth pass) — signal-engine's S/R detection consolidated onto technical-analysis's canonical levels engine

**The finding**: `services/signal-engine/src/generators/signals.py::_sr_context()` independently
reimplemented pivot detection (60-bar window, ±3-bar local-max/min) to classify a stock's
position relative to support/resistance as `breakout`/`at_resistance`/`at_support`/`neutral` —
a simpler, less sophisticated approach than `services/technical-analysis/src/indicators/
trendlines.py::detect_support_resistance()` (a 3-tier strategy: 90-bar local structure →
full-history 35% band → Fibonacci fallback), which is the actual canonical source the chart's
own official S/R levels (`GET /ta/{symbol}/levels`) use — and which had already been fixed once
for a close-vs-high/low pivot mismatch (`T247-TA-CLUSTERPIVOTS-CLOSE-HIGH-MISMATCH`) that
signal-engine's independent copy never received. Real risk: a signal's breakout/at_support
labeling could silently disagree with the chart's own S/R levels for the same symbol at the
same moment.

**Fix**: ported signal-engine's own classification logic (52-week high/low, the `cleared_res`
all-time-high-breakout fallback, prev-bar comparison) into a new
`detect_sr_context(df, levels=None)` function in `trendlines.py` — built on top of
`detect_support_resistance()`'s own output, so it inherits every fix that module has already
received. Exposed as a new `sr_context` field on `GET /ta/{symbol}/levels`'s response (reusing
the `levels` list the endpoint already computes once, not recomputing them a second time).
signal-engine's `_sr_context()` now takes an optional `symbol` param: when provided, fetches
the classification from technical-analysis via a new `_fetch_sr_context_from_ta()` helper
(matching this file's existing `_fetch_patterns_from_ta()` HTTP-to-TA integration pattern —
not a new architecture); falls back to the original local computation (kept, unchanged) if
`symbol` is omitted or technical-analysis is unreachable — signal generation must never
hard-fail on a TA-service outage.

**Verified numerically before wiring anything up**: fed the same synthetic breakout fixture
through both `detect_sr_context()` (new, canonical) and the original `_sr_context()` (old,
local) and confirmed both produced `sr_context: "breakout"` with the same
`sr_nearest_resistance` (100.8 exactly) — the tiny support-level difference (98.22 vs. 97.93)
reflects the different, more sophisticated pivot-detection windows, expected and correct.

**Tests**: `services/technical-analysis/tests/test_sr_context.py` (5 cases) — fresh-breakout,
neutral-mid-range, all expected keys present, precomputed-levels reuse (confirms the endpoint's
one call to `detect_support_resistance()` isn't duplicated), and the all-time-high
`cleared_res` fallback path. `services/signal-engine/tests/test_sr_context_consolidation.py`
(7 cases) — remote-result-used-when-reachable, local-fallback-when-unreachable,
no-HTTP-call-when-symbol-omitted, and 4 cases on `_fetch_sr_context_from_ta()`'s own
fail-open behavior (non-200, network exception, malformed response missing the `sr_context`
key, successful parse). Adversarially verified on both sides: reverted technical-analysis's
`cleared_res` fallback and confirmed 2 tests failed correctly; reverted signal-engine's
remote-result usage and confirmed the primary-path test failed correctly; both restored.

**Verification**: full technical-analysis suite (31 tests, up from 26) and signal-engine suite
(66 tests, up from 59) both green modulo the 4 pre-existing, unrelated
`test_analyst_momentum.py` failures.

### Fixed (final closing sweep) — strategy-engine's 4th independent ATR copy + frontend R:R triplication

**A dedicated closing-sweep pass** (after the 4 fixes above) re-verified the whole audit for
completeness rather than assuming it was done, and found two more real, previously-uncaught
instances:

**1. `services/strategy-engine/src/dsl/evaluator.py::compute_features()`** had its own
byte-identical inline TR/ATR copy (`pd.concat([...]).max(axis=1).ewm(alpha=1/14, adjust=False,
min_periods=14).mean()`) that the earlier ATR-consolidation pass (which only covered
signal-engine) missed entirely — a 4th independent copy of the exact same formula, on a service
neither research pass had checked. Fixed by importing `atr as _canon_atr` from
`shared/common/indicators.py`, matching every other service. Required adding a new
`services/strategy-engine/tests/conftest.py` (this service previously had none) to real-load
`common.indicators` the same way market-data/ranking-engine/signal-engine's own conftest.py
files already do, since `common` isn't installed as a real package in this local dev
environment. 3 new tests (`test_atr_consolidation.py`) including a direct
`pd.testing.assert_series_equal()` parity check against the canonical function and the
min_periods-warmup-NaN guard; adversarially verified by reverting to the old inline formula and
confirming both failed correctly. Full strategy-engine suite (15 tests, up from 12) green.

**2. Frontend R:R computation — `PositionSizer.tsx` and `PriceChart.tsx`** had the exact same
direction-validity-guarded risk:reward formula, independently fixed for the identical inverted-
R:R bug in two separate sessions with two separate comment tags
(`AUD-POSITIONSIZER-INVERTEDRR` / `AUD-CHART-INVERTEDRR`) and zero shared source — a textbook
"hand-mirror silently drifts" risk (a future fix to one could easily be forgotten in the
other). Consolidated into a new `frontend/src/lib/riskReward.ts::computeRiskReward()`, which
both components now call. **`frontend/src/lib/fvgTradePlan.ts`'s own `Math.abs()`-based R:R
was investigated and confirmed to be a DIFFERENT case, not the same bug class** — its `target`
is derived from `entry ± risk*minRR` based on the gap's own `kind`, so it's mathematically
guaranteed to land on the correct side by construction; there's no externally-supplied target
that could be on the wrong side to guard against, unlike PositionSizer/PriceChart's analyst-
target/game-plan-target inputs. Left unchanged, correctly.

**Tests**: `frontend/src/lib/riskReward.test.ts`, 10 cases — valid long/short setups, the
exact inverted-target regression case for both directions, missing/zero/negative inputs, and
the zero-risk divide-by-zero guard. Adversarially verified by removing the
`targetDirectionValid` guard from the `rr` computation and confirming 2 tests failed correctly
before restoring it. Full frontend suite (89 tests, up from 79), typecheck, and a full
`next build` all green.

**This closing-sweep pass's own methodology note**: an initial verification attempt used a
general-purpose research agent that, on its SECOND turn, described itself as "waiting for a
background task to notify it" and returned a report synthesized from memory rather than from
actually re-running any tools — its claims (e.g. "signal-engine/research-engine/ml-prediction
still have raw Redis constructions") were stale and WRONG, contradicted by a direct `grep`
re-run in the same turn. A second, independently re-prompted pass (explicitly told not to wait
on anything and to run real tools) produced the two genuine findings above. **Lesson**: a
subagent's own claim of "waiting on a background process" mid-task is itself a red flag — a
research/analysis agent has no legitimate reason to defer to a background notification for its
own final answer; always resume/re-prompt and verify the report actually came from fresh tool
calls before trusting it, exactly the same "verify, don't just trust a status claim" discipline
this file already applies to stale tracker entries and prior sessions' own "done" claims.

**Audit now considered complete**: exhaustive re-verification found no further duplicated
business-logic instances across position-sizing, R:R, confidence/probability scoring, EV/win-
rate, Sharpe ratio, max drawdown, or K-Score categories beyond what's now fixed or already
correctly identified as intentional (research-engine's support-anchored stops, the paper-
portfolio-vs-portfolio-optimizer beta calculations, the third portfolio-correlation
implementation, and the Python/TypeScript volume-profile/FVG/swing-pivot pairing). Sharpe ratio
and max drawdown each have 3-4 independent per-service implementations
(`paper_portfolio.py`, `strategy-engine/backtest/engine.py`, `portfolio-optimizer/methods.py`)
that were flagged as plausibly-intentional-but-never-formally-audited — noted here as a
possible future pass, not fixed in this one, since each serves a genuinely different consumer
(paper-trading reporting vs. strategy backtest vs. portfolio optimization objective) and
none showed a concrete, confirmed drift the way the 6 items fixed in this audit did.

---

