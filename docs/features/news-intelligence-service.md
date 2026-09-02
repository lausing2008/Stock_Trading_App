## Feature Reference: T259-NEWS-INTELLIGENCE — New Service (port 8011), Real-Time Company
## Headline Ingestion + Hot-News Signal Gate (Built 2026-07-27)

**Replaces the abandoned `docs/DESIGN_REALTIME_NEWS_FEED_2026-07-25.md` design entirely.**
That design was reviewed before any code was written and rejected for 3 concrete reasons: its
core data source (a Stock Titan RSS URL) was verified DEAD via a direct `curl` (a genuine 404,
not rate-limiting — see the "It's reachable ≠ it's current" discipline elsewhere in this file),
its ticker-extraction regex matched common English acronyms as if they were real tickers
("EPS"/"CEO"/"AI"/"IPO"/"FDA" all read as plausible 2-4 letter all-caps tokens), and its Phase 2
needed an unproven new dependency. The user then asked for a full redesign — a genuinely
broader capability (not just the original narrow "prevent gap-down BUY signals" goal), built as
a real standalone microservice, with both a user-facing page and a signal-engine gate. Alpaca
was added on the user's own explicit instruction ("include Alpaca signup now"), overriding this
session's initial recommendation to defer it.

**Deliberately does NOT cover macro data releases (CPI/NFP/GDP/FOMC) or earnings dates/
results** — both are already covered by dedicated, already-built systems (`event-intelligence`'s
T249-MARKETMOVER P0-P2 for macro; P1 for earnings). This service is scoped specifically to
ad-hoc company headline news (press releases, 8-Ks, M&A, FDA decisions, executive changes) that
neither of those systems touches.

### Data sources — all verified LIVE via direct request before being coded against

1. **PR Newswire** (`services/news-intelligence/src/services/rss_sources.py`) — the
   `financial-services-latest-news` RSS category feed. Confirmed live: real 200, real parseable
   entries.
2. **Business Wire** — the original design's GlobeNewswire URL (and every guessed alternate
   pattern) 404'd; GlobeNewswire's public RSS discovery appears discontinued. Business Wire's
   only public feed (`feed.businesswire.com/rss/home/...`) was confirmed live instead (a real
   `Last-Modified` within the hour, real current entries) — it's a generic international
   firehose (no finance-only category feed was found reachable), so `fetch_businesswire()`
   filters to ASCII-only titles as a simple heuristic to drop the non-English noise.
3. **SEC EDGAR real-time filings** (`edgar_source.py`) — `action=getcurrent` on
   `sec.gov/cgi-bin/browse-edgar`, confirmed live with real, minutes-old `<updated>` timestamps.
   This is a genuinely faster-latency, COMPLEMENTARY source to `event-intelligence`'s existing
   DAILY-BATCH 8-K sync (T11/T208) — not a replacement. Polls 3 filing types (8-K, 4, SC 13D)
   every 2 minutes. Resolves each filing's company to a tracked symbol via an exact `Stock.cik`
   lookup (`symbol_for_cik()` in `tickers.py`), never headline text-matching — a filer's CIK is
   unambiguous.
4. **Alpaca news WebSocket** (`alpaca_source.py`) — `wss://stream.data.alpaca.markets/v1beta1/news`.
   The only PUSH-based source (the other 3 are polled); genuinely new infrastructure for this
   codebase (zero prior WebSocket client code exists anywhere — `T230-DATA-STREAMING-QUOTES`
   documents this gap for price data specifically). Long-lived background task with automatic
   reconnect-with-exponential-backoff (5s → up to 300s). Subscribes to `news: ["*"]` (all
   symbols) rather than a fixed list, since Alpaca's news stream has no per-symbol cost and
   items already arrive natively ticker-tagged.

### Universe-aware ticker matching — the fix for the original design's core bug

`tickers.py`'s `extract_symbols()` deliberately does NOT use a standalone regex over headline
text. It only ever matches against this app's own real, finite stock universe
(`Stock.symbol`/`Stock.name`, loaded fresh every 15 min and cached in-process) — a headline can
only ever be tagged with a ticker this app actually tracks, never an arbitrary all-caps token.
Two independent signals, either sufficient: (1) the ticker's own base symbol (`.HK` suffix
stripped) appears as a standalone word — only checked for symbols with 3+ characters, since
1-2 letter symbols would match too many common short words; (2) the company's full name appears
verbatim in the headline (catches the common real-world case where a PR Newswire/Business Wire
headline never spells out the ticker at all, e.g. "Apple Inc announces..." with no "AAPL"
anywhere).

### Classification — Claude Haiku, batched

`classify.py`'s `classify_headlines()` reuses the established Claude-call pattern
(`market-data/src/api/news.py`'s `_claude_sentiment()`, `_strip_markdown_fence()`) — one Haiku
call per batch of up to 8 headlines, returning `{sentiment_score (0-100), sentiment_label,
is_material, category}` per headline. Fail-open: an empty/missing admin-configured key or any
call failure degrades to an all-`None` list of the same length, never blocking ingestion.

### Hot-news signal gate — signal-engine's BUY gate

A **material** headline (earnings, FDA, M&A, guidance change, executive departure, downgrade/
upgrade) sets a 2-hour Redis flag (`stockai:hot_news:{symbol}`, written by `storage.py`'s
`_mark_hot()`). `services/signal-engine/src/generators/signals.py`'s new `_fetch_hot_news()`
reads it via `GET /news/hot/{symbol}` (fail-open, matching every other optional cross-service
enrichment in this file — `_fetch_options_flow`, `_fetch_sr_context_from_ta`, etc.) and
`_apply_style_signal()` applies it as a **direction-aware compression**, not a hard reject:

- Material **negative** news + a BUY-leaning fused probability (`fused > 0.5`) → compress 30%
  toward neutral (`fused = 0.5 + (fused - 0.5) * 0.70`) — don't recommend buying INTO bad news.
- Material **positive/neutral** news, or ANY news when the signal is already SELL/HOLD-leaning
  → logged into `reasons["hot_news_flag"]` only, zero effect on `fused`. This is a
  **suppression-only gate**, matching the original design's own defensive intent (generalized
  beyond just gap-downs) — a positive headline alone is not independent TA/ML confirmation, so
  it must never boost a signal back toward BUY on its own.

### Alpaca admin credentials — Settings page, mirrors the Claude/DeepSeek key pattern exactly

`shared/common/ai_keys.py`'s new `get_alpaca_credentials()` reads
`stockai:admin:alpaca_api_key`/`stockai:admin:alpaca_secret_key` from Redis (fail-open, returns
`("", "")` if unset). `services/market-data/src/api/admin.py`'s `ConfigRequest`/`update_config()`
gained `alpaca_api_key`/`alpaca_secret_key`/`unshare_alpaca_key` fields, writing/deleting those
same 2 Redis keys — same admin-configured-credential convention as every AI provider key in this
app, NEVER stored in `.env`/files. `frontend/src/pages/settings.tsx` gained an admin-only
"Real-Time News — Alpaca" section (separate from the AI Assistant section, since this is a
server-wide credential with no per-browser localStorage persistence at all — unlike
`claudeApiKey`/`deepseekApiKey`, which ARE a per-browser bring-your-own-key convenience) with
two `KeyInput` fields (API Key ID, Secret Key) and Save/Remove buttons calling `api.pushConfig()`.

### Architecture

- `shared/db/models.py` — `RealtimeNewsItem` (new table, `create_all()`-friendly — no manual
  migration needed). `(source, url, symbol)` unique constraint makes re-polling the same feed
  item idempotent (`ON CONFLICT DO NOTHING`). `symbol=None` means macro/market-wide, no ticker
  matched — one row per (symbol, headline) pair, deliberately denormalized rather than a join
  table (headlines rarely mention more than 1-2 tickers).
- `services/news-intelligence/` — new service, FastAPI + `AsyncIOScheduler` (matches
  `event-intelligence`'s scaffolding exactly). `src/services/tickers.py` (universe matching),
  `classify.py` (Claude Haiku), `rss_sources.py`/`edgar_source.py`/`alpaca_source.py`
  (the 4 ingestors), `storage.py` (shared persistence + hot-news flag), `scheduler.py`
  (RSS/EDGAR polling every 1-2 min via a dedicated `ThreadPoolExecutor` + `run_in_executor()` —
  matching `event-intelligence/src/services/macro_reaction.py`'s established fix for the
  "blocking I/O on a shared AsyncIOScheduler event loop" bug class — plus the long-lived Alpaca
  task started once at startup). `src/api/routes.py` — `GET /news` (list, filterable by symbol),
  `GET /news/hot/{symbol}` (read-side for signal-engine + the frontend badge).
- `shared/common/config.py` — `news_intelligence_url: str = "http://news-intelligence:8011"`.
- `services/api-gateway/src/api/proxy.py` — `"news": _settings.news_intelligence_url` added to
  `_ROUTES` (not in `_PUBLIC_PREFIXES`, so it requires a JWT like every other real endpoint).
- `docker/docker-compose.yml` — new `news-intelligence` service (inherits the `py-common` YAML
  anchor exactly like every sibling), added to `api-gateway`'s `depends_on`.
- Frontend: `frontend/src/pages/news.tsx` (new page — symbol/material/source filters, 60s SWR
  refresh), a new "Real-Time News" nav entry under the Research group in `_app.tsx`, and
  `RealtimeNewsItem`/`HotNewsFlag` types + `api.news()`/`api.newsHot()` wrappers in `api.ts`.

### A real bug caught and fixed during test-writing, before it shipped

`edgar_source.py`'s `_fetch_one()` originally built each headline using the QUERY's own
`filing_type` param (e.g. `"8-K"`) rather than the ACTUAL parsed form from the entry's title
(e.g. `"8-K/A"`) — since EDGAR's `type=` filter isn't perfectly exclusive across every returned
entry, a real amendment could be silently mislabeled as a fresh original filing. Caught while
writing `test_edgar_source.py` (a test asserting the headline started with the query's own
`filing_type` failed against realistic fixture data). Fixed to use the regex's own captured
`form` group, falling back to the query param only when the title doesn't match the expected
`"FORM - Company (CIK) (Filer)"` pattern at all.

### A real near-miss caught during adversarial verification, not shipped

The first version of `tickers.py`'s substring-collision regression test used a "Camden"/"AMD"
fixture — sabotaging the real word-boundary regex guard did NOT make the test fail, because
bare-ticker matching is already case-sensitive and the mixed-case "Camden" never collided with
the all-caps "AMD" pattern in the first place, regardless of the boundary guard. Investigated
why the sabotage "still passed" (this repo's own standing red flag for exactly this situation)
and fixed by using a genuine same-case adversarial fixture ("PYRAMDING", which DOES contain
"AMD" as an exact-case substring) — re-verified the sabotage is now correctly caught.

### Tests

47 new tests in `services/news-intelligence/tests/` (`tickers.py`'s universe-aware matching,
including the exact EPS/CEO/AI/IPO/FDA false-positive case the original design's regex failed
on; `rss_sources.py`/`edgar_source.py` against REAL `feedparser` on constructed fixture RSS/
Atom XML, not live network calls; `classify.py` against a mocked `httpx.Client`; the Alpaca
message parser; the hot-news Redis flag). 7 new tests in
`services/signal-engine/tests/test_hot_news_gate.py` for the BUY gate itself. All real
dependencies (`sqlalchemy`, `feedparser`, `redis`, `httpx`, `structlog`) are installed in this
local dev environment — only `psycopg2` and this repo's own `common`/`db` packages needed
stubbing (`tests/conftest.py`), so the tests run against the REAL implementations, not
hand-copied reimplementations. Full frontend vitest suite (89 tests), typecheck, and a full
`next build` all green throughout.

**What to check if this looks wrong**:
```bash
# Confirm the service is running and healthy:
docker exec stockai-news-intelligence-1 curl -fs http://localhost:8011/health

# Confirm ingestion is actually writing rows:
docker exec stockai-postgres-1 psql -U stockai -d stockai -c \
  "SELECT source, COUNT(*), MAX(published_at) FROM realtime_news_items GROUP BY source;"

# Confirm the hot-news flag for a specific symbol:
docker exec stockai-redis-1 redis-cli get stockai:hot_news:AAPL

# Confirm signal-engine is actually reaching news-intelligence (not silently failing):
docker exec stockai-signal-engine-1 curl -s 'http://news-intelligence:8011/news/hot/AAPL'

# Confirm Alpaca credentials are set (if configured) and the WebSocket connected:
docker exec stockai-redis-1 redis-cli get stockai:admin:alpaca_api_key
docker logs stockai-news-intelligence-1 --since 10m | grep 'alpaca_source'
# Should show alpaca_source.connecting / alpaca_source.subscribed if a key IS configured, or
# alpaca_source.no_credentials_configured (a normal, expected state) if not.
```

---

