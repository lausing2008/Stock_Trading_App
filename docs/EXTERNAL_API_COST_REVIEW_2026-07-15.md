# External API Cost-Effectiveness Review — 2026-07-15

Full inventory of every external/third-party data source this app depends on, what's free
vs. paid, what's actually configured in production, and a concrete recommendation on where
(if anywhere) paying for an upgrade would move the needle. Triggered by a request to review
this "to be cost effective" before deciding whether to add any paid API.

## The single biggest finding: `ANTHROPIC_API_KEY` is not set in production

This matters more than any data-source question below. Confirmed live (safe existence/length
checks only, never printing the value):

```bash
grep -c '^ANTHROPIC_API_KEY=' .env                                    # → 0 (line doesn't exist)
docker exec stockai-redis-1 redis-cli exists stockai:admin:claude_api_key   # → 0 (not set)
```

Every LLM-backed feature reads the key from Redis (`stockai:admin:claude_api_key`, set via the
admin Settings page — see `_get_admin_ai_key()` in research-engine, `_get_api_key()` in
decision-engine's `llm_scorer.py`, `_api_key()` in event-intelligence's new `macro_reaction.py`)
or falls back to a `.env`/config value if Redis has nothing. Neither exists today. Practical
effect on features already built and marketed as working:

- **T249-MARKETMOVER-P2's macro reaction alerts** (built today) — `generate_reaction()` fails
  open (returns `None`) on every call. The detection/polling machinery runs correctly, but no
  reaction email will ever actually fire until a key is set. This is a real functional gap in
  a feature that otherwise deployed clean.
- **research-engine's AI research reports** — every report is currently the canned
  `_fallback_ai()` template (`"_is_fallback": True`, `"Analysis unavailable — AI provider
  returned an error"` placeholder text), not a real LLM-generated report. Anyone viewing a
  stock's Research tab today is seeing fallback text, not analysis.
- **research-engine's AI chat** — same key, same fallback.
- **market-data's news sentiment scoring** (`news.py`) — falls back to a VADER lexicon score
  instead of Claude's read, which is a smaller quality drop than the two above (VADER is a
  reasonable approximation for headline sentiment) but still a degraded mode.
- **decision-engine's `llm_scorer.py`** — the one exception: `llm_scoring_enabled` defaults to
  `False` per-portfolio, so this one is opt-in and not silently broken; it simply does nothing
  until a user turns it on AND a key exists.

**This is a $/mo-vs-nothing decision unlike the data-source questions below** — Claude API
usage is pay-per-token, no fixed monthly commitment, and turning it on immediately fixes 3
already-built, already-shipped features (P2 reactions, research reports, AI chat) that are
currently running in a degraded/no-op state without any visible error to the user. **This is
the highest-leverage, lowest-cost fix available** — likely worth doing regardless of the
data-source questions below, since the infrastructure and prompts are already built and
tested; the only gap is the missing key.

## Full external data source inventory

| Source | Service(s) | Provides | Status in production |
|---|---|---|---|
| **yfinance** | market-data, event-intelligence, ranking-engine, signal-engine | Default OHLCV bars, earnings history/calendar, news, options chains, fundamentals | Free, no key — primary source everywhere |
| **Polygon.io** | market-data (`polygon_adapter.py`) | US equity OHLCV aggregates (`/v2/aggs` only — no trades/quotes) | Key declared but **empty** in `.env.production` → never actually used, always falls through to yfinance |
| **Alpha Vantage** | market-data (`alpha_vantage_adapter.py`) | US daily/weekly adjusted OHLCV | Key declared but **empty** → unused |
| **iTick** | config only | N/A | Declared in config, **zero call sites anywhere** — dead setting |
| **FRED** | event-intelligence (`economic.py`) | Macro series + release-date calendar (T249-P0) | Free (needs key) — **key IS set** (rotated today after the httpx-logging incident) and working live |
| **Anthropic Claude** | market-data, research-engine, decision-engine, event-intelligence (P2) | LLM reasoning/reports/sentiment/reactions across 4 features | Paid (usage-billed) — **not set anywhere**, see above |
| **multpl.com** | event-intelligence (`valuation.py`) | Shiller CAPE (Bubble Warning feature) | Free, no key, scraped |
| **kadoa-org GitHub feed** | event-intelligence (`congress.py`) | Congress trading disclosures | Free, no key, unauthenticated |
| **SEC EDGAR** | event-intelligence (`insider.py`, `institutional.py`, `edgar_8k.py`) | Form 4 insider trades, 13F institutional ownership, 8-K filings | Free, no key, rate-limited to SEC's own 10 req/s fair-use policy |
| **USAspending.gov** | event-intelligence (`political.py`) | Government contract awards | Free, no key |
| **Google News RSS** | market-data (`news.py`) | Supplemental headlines (HK stocks, thin-coverage fallback) | Free, no key |
| **Eastmoney** | market-data (`hk_connect.py`) | HK/Southbound Stock Connect flows | Free, no key, scraped |

## Quiver Quantitative — already resolved, no longer a live question

Project memory and some stale docs (`CLAUDE.md`, `services/event-intelligence/skill.md`) still
describe Quiver as an optional $30/mo upgrade path for congress-trading data
(`quiver_api_key` in Settings). **This is outdated** — Quiver has been fully removed from the
live codebase. `quiver_api_key` no longer exists in `shared/common/config.py`; the current
`congress.py` (both the market-data and event-intelligence versions) exclusively uses the free
kadoa-org GitHub feed, which was adopted specifically because it replaced the free sources that
died (housestockwatcher/senatestockwatcher going permanently offline). Quiver was evaluated
seriously in the past (`T171-B` in the tracker called it "priority #1, best ROI") but was never
purchased, and the free replacement has been working well since. **No action needed here** —
the free path is already the current, deliberate choice, not a stopgap waiting on budget.

## Polygon.io — what upgrading would actually unlock

Currently: key is present in `.env.production` but **empty**, so `polygon_adapter.py`'s
`supports()` check always returns `False` and the app silently runs on yfinance for
everything, 100% of the time, in production today. The adapter code itself only calls
Polygon's aggregates endpoint (bars) — no trades/quotes code exists yet, so even fully paying
for Polygon today would only get bar-level improvements (fresher intraday data than yfinance's
scrape-based delay, higher rate limits, official uptime SLA) unless new adapter code were also
written for trades/quotes (needed for the deferred true-footprint-chart feature, and for
options put/call ratio, which the ML feature vector already has a documented TODO for).

**Recommendation: do not pay for Polygon yet.** The free yfinance path is currently serving
every US/HK symbol without a documented reliability incident specific to data freshness (the
one real yfinance incident on file — `ranking-engine`'s relative-strength calc collapsing — was
an import/rate-limit bug, not a data-quality gap Polygon would have prevented). Revisit only if:
(a) the footprint-chart feature becomes a real priority (needs Polygon's paid trades/quotes
tier specifically, no way around it), or (b) options put/call ratio becomes a wanted ML
feature and free alternatives are exhausted.

## Alpha Vantage — no action needed

Same empty-key, unused-in-practice state as Polygon, but there's no evidence anywhere in the
tracker or CLAUDE.md that Alpha Vantage's specific data (US daily/weekly OHLCV) is a gap
yfinance doesn't already cover for this app's use cases. Alpha Vantage exists in the adapter
priority chain as a middle fallback that's never actually exercised. No recommendation to pay
for this at any tier — it doesn't provide anything the app currently lacks.

## iTick — recommend removing, not funding

`itick_api_key` exists in `shared/common/config.py` but has zero call sites anywhere in any
service. This is dead configuration, not an unfunded feature — nothing currently checks or
would benefit from this key being set. Worth a cleanup pass (delete the config key) the next
time someone is in that file, but not a cost-effectiveness question at all.

## Recommendation summary, ranked by leverage

1. **Set `ANTHROPIC_API_KEY`** (via the admin Settings page, which writes to the Redis key
   every LLM call site already reads) — fixes 3 already-built features running in silent
   fallback mode today (P2 macro reactions, research reports, AI chat) at zero fixed cost
   (pay-per-token usage only). By far the highest-leverage item in this review.
2. **No action on Polygon/Alpha Vantage** — both keys are present-but-empty and neither gap
   has documented evidence of hurting the app today; revisit only if footprint charts or
   options-flow ML features become active priorities.
3. **No action on Quiver** — already resolved via the free kadoa feed; the historical "priority
   #1" tracker note is stale and should not be treated as still-live guidance.
4. **Consider deleting `itick_api_key`** — pure dead-code cleanup, not a spend decision.

## What to check if revisiting this later

```bash
# Confirm current key state (safe — length/existence only, never prints the value):
ssh ... "grep -c '^ANTHROPIC_API_KEY=' .env"
docker exec stockai-redis-1 redis-cli exists stockai:admin:claude_api_key

# Confirm whether Polygon/Alpha Vantage keys have since been set:
ssh ... "grep -c '^POLYGON_API_KEY=.\+' .env"       # non-empty value check
ssh ... "grep -c '^ALPHA_VANTAGE_API_KEY=.\+' .env"
```
