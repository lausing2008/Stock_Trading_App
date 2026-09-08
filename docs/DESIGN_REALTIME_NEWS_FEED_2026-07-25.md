# DESIGN — Real-Time News Feed Integration (Stock Titan RSS + Financial Juice)
**Date**: 2026-07-25
**Status**: Proposed
**Priority**: High
**Affects**: `services/market-data`, `services/signal-engine`

---

## Problem

The current news pipeline (`_fetch_news_sentiment` in `signals.py`) sources headlines from Yahoo Finance News and Google News RSS. Both are **delayed by 15–45 minutes** after the market-moving event. During that window:

- A negative headline (FDA rejection, earnings miss, M&A collapse) drops
- Price starts moving immediately
- Your signal engine has no knowledge of it
- `news_compression` does not fire — the BUY signal goes out into a stock already gapping down
- The `eight_k_flag` SEC filing check also misses it — filings are submitted *after* the news breaks

This is the "BUY into a gap-down" failure mode. It is distinct from the SA-33 entry-timing problem (which was about lagging TA indicators). This is about missing real-world events entirely.

### Current news latency by source

| Source | Typical delay after event |
|--------|--------------------------|
| Yahoo Finance News | 15–45 min |
| Google News RSS | 20–60 min |
| Stock Titan RSS | 5–60s |
| Financial Juice (Discord) | 5–30s |

---

## Proposed solution

Three components:

1. **Real-time news ingestor** — polls Stock Titan RSS every 30s and listens to Financial Juice Discord webhook; parses, classifies, and writes to a `realtime_news` DB table
2. **Hot-news gate** in `generate_all_signals()` — checks for negative headlines in the last 15 minutes before a BUY fires
3. **Catalyst boost** — positive real-time headlines (earnings beat, upgrade, M&A) can lift a HOLD to BUY

---

## Source details

### Stock Titan RSS
- URL: `https://www.stocktitan.net/news/rss.xml` (full feed) or per-symbol `https://www.stocktitan.net/news/{SYMBOL}/rss.xml`
- Latency: 5–60s after event
- Format: standard RSS/Atom XML — `<title>`, `<pubDate>`, `<description>`, `<link>`
- Cost: free
- Reliability: high — structured, stable format, no auth required
- Best for: earnings surprises, analyst actions, SEC filings, M&A, FDA decisions

### Financial Juice (Discord)
- Access: join their public Discord server; configure a webhook listener on the `#news` channel
- Latency: 5–30s after event
- Format: plain-text Discord messages — requires regex parsing to extract tickers
- Cost: free
- Reliability: medium — Discord format can change; bot can be rate-limited or banned
- Best for: Fed/macro headlines, breaking market news, pre-market movers

### Recommended build order
1. Stock Titan RSS first — structured, reliable, zero cost, no auth
2. Financial Juice Discord second — higher value but more fragile; add after RSS is stable

---

## Database schema

New table in the shared PostgreSQL database:

```sql
CREATE TABLE realtime_news (
    id             BIGSERIAL PRIMARY KEY,
    symbol         VARCHAR(32),          -- NULL for macro/market-wide headlines
    headline       TEXT NOT NULL,
    source         VARCHAR(32) NOT NULL, -- 'stock_titan' | 'financial_juice'
    url            TEXT,
    sentiment      VARCHAR(16),          -- 'positive' | 'negative' | 'neutral'
    sentiment_score FLOAT,               -- 0-100, 50=neutral (same scale as existing)
    is_material    BOOLEAN DEFAULT FALSE, -- TRUE for earnings/FDA/M&A/upgrade/downgrade
    category       VARCHAR(32),          -- 'earnings' | 'fda' | 'ma' | 'analyst' | 'macro' | 'other'
    published_at   TIMESTAMPTZ NOT NULL,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_realtime_news_symbol_ts ON realtime_news (symbol, published_at DESC);
CREATE INDEX idx_realtime_news_ts        ON realtime_news (published_at DESC);
```

Redis cache key for hot-news lookup (TTL 20 min):
```
stockai:realtime_news:{SYMBOL}:negative   → "1" if negative headline in last 15 min
stockai:realtime_news:{SYMBOL}:positive   → "1" if positive headline in last 15 min
```

---

## Implementation plan

### Phase 1 — Stock Titan RSS ingestor (market-data service)

New file: `services/market-data/src/services/realtime_news.py`

```python
import asyncio
import feedparser
import re
from datetime import datetime, timezone, timedelta
from common.logging import get_logger
from common.redis_client import get_redis

log = get_logger("realtime-news")

STOCK_TITAN_FEED = "https://www.stocktitan.net/news/rss.xml"
POLL_INTERVAL_S  = 30
NEWS_REDIS_TTL   = 20 * 60  # 20 minutes

# Keyword → category + sentiment mapping
_NEGATIVE_KEYWORDS = {
    "rejects", "rejection", "misses", "miss", "below expectations", "cuts guidance",
    "lowers guidance", "withdraws", "recall", "investigation", "lawsuit", "downgrade",
    "downgrades", "reduces", "suspended", "halted", "bankruptcy", "default",
}
_POSITIVE_KEYWORDS = {
    "beats", "beat", "exceeds", "raises guidance", "upgrade", "upgrades", "approves",
    "approval", "fda approves", "acquires", "merger", "buyout", "dividend increase",
    "buyback", "record revenue", "record earnings",
}
_MATERIAL_KEYWORDS = {
    "earnings", "eps", "revenue", "fda", "merger", "acquisition", "upgrade",
    "downgrade", "guidance", "dividend", "buyback", "recall", "bankruptcy",
}
_TICKER_RE = re.compile(r'\b([A-Z]{1,5})\b')


def _classify(headline: str) -> tuple[str, bool, str]:
    """Return (sentiment, is_material, category)."""
    h = headline.lower()
    sentiment = "neutral"
    if any(k in h for k in _NEGATIVE_KEYWORDS):
        sentiment = "negative"
    elif any(k in h for k in _POSITIVE_KEYWORDS):
        sentiment = "positive"
    is_material = any(k in h for k in _MATERIAL_KEYWORDS)
    category = "other"
    for cat, keywords in {
        "earnings": ["earnings", "eps", "revenue", "quarterly"],
        "fda":      ["fda", "approval", "approves", "rejects", "rejection"],
        "ma":       ["merger", "acquisition", "acquires", "buyout", "takeover"],
        "analyst":  ["upgrade", "upgrades", "downgrade", "downgrades", "price target"],
        "macro":    ["fed", "fomc", "inflation", "cpi", "gdp", "interest rate"],
    }.items():
        if any(k in h for k in keywords):
            category = cat
            break
    return sentiment, is_material, category


def _extract_symbols(headline: str, description: str) -> list[str]:
    """Best-effort ticker extraction from headline + description text."""
    text = f"{headline} {description}"
    candidates = _TICKER_RE.findall(text)
    # Filter out common false positives (English words that look like tickers)
    _STOPWORDS = {"A", "I", "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS",
                  "IT", "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US",
                  "WE", "CEO", "CFO", "COO", "IPO", "ETF", "SEC", "FDA", "FED"}
    return list({s for s in candidates if s not in _STOPWORDS})


async def poll_stock_titan(db_session_factory):
    """Continuously poll Stock Titan RSS and write new items to realtime_news."""
    seen_urls: set[str] = set()
    rc = get_redis()

    while True:
        try:
            feed = feedparser.parse(STOCK_TITAN_FEED)
            for entry in feed.entries:
                url = entry.get("link", "")
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                headline    = entry.get("title", "").strip()
                description = entry.get("summary", "").strip()
                pub_str     = entry.get("published", "")
                try:
                    published_at = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                except Exception:
                    published_at = datetime.now(timezone.utc)

                # Skip items older than 30 minutes on startup (avoid backfill flood)
                if (datetime.now(timezone.utc) - published_at) > timedelta(minutes=30):
                    continue

                sentiment, is_material, category = _classify(headline)
                symbols = _extract_symbols(headline, description)

                with db_session_factory() as db:
                    for symbol in (symbols or [None]):
                        db.execute(
                            """
                            INSERT INTO realtime_news
                                (symbol, headline, source, url, sentiment, is_material,
                                 category, published_at)
                            VALUES (:sym, :hl, 'stock_titan', :url, :sent, :mat, :cat, :pub)
                            ON CONFLICT DO NOTHING
                            """,
                            dict(sym=symbol, hl=headline, url=url, sent=sentiment,
                                 mat=is_material, cat=category, pub=published_at),
                        )
                    db.commit()

                # Write hot-news Redis flags for fast lookup in signal-engine
                for symbol in symbols:
                    if sentiment == "negative":
                        rc.setex(f"stockai:realtime_news:{symbol}:negative",
                                 NEWS_REDIS_TTL, "1")
                    elif sentiment == "positive":
                        rc.setex(f"stockai:realtime_news:{symbol}:positive",
                                 NEWS_REDIS_TTL, "1")

                log.info("realtime_news.ingested", source="stock_titan",
                         headline=headline[:80], sentiment=sentiment,
                         symbols=symbols, is_material=is_material)

        except Exception as exc:
            log.warning("realtime_news.poll_failed", source="stock_titan", error=str(exc))

        await asyncio.sleep(POLL_INTERVAL_S)
```

Start the poller as a background task in `market-data`'s FastAPI lifespan:

```python
# services/market-data/src/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(poll_stock_titan(SessionLocal))
    yield
```

### Phase 2 — Financial Juice Discord listener (market-data service)

New file: `services/market-data/src/services/financial_juice_discord.py`

```python
import discord
from common.config import get_settings
from common.logging import get_logger
from .realtime_news import _classify, _extract_symbols, NEWS_REDIS_TTL
from common.redis_client import get_redis

log = get_logger("financial-juice")

# Requires DISCORD_BOT_TOKEN and FINANCIAL_JUICE_CHANNEL_ID in .env
# Bot needs "Read Message History" + "View Channel" permissions only

class FinancialJuiceClient(discord.Client):
    def __init__(self, db_session_factory, **kwargs):
        super().__init__(**kwargs)
        self._db = db_session_factory
        self._rc = get_redis()
        s = get_settings()
        self._channel_id = int(s.financial_juice_channel_id)

    async def on_message(self, message: discord.Message):
        if message.channel.id != self._channel_id:
            return
        headline = message.content.strip()
        if not headline:
            return

        sentiment, is_material, category = _classify(headline)
        symbols = _extract_symbols(headline, "")

        with self._db() as db:
            for symbol in (symbols or [None]):
                db.execute(
                    """
                    INSERT INTO realtime_news
                        (symbol, headline, source, sentiment, is_material, category, published_at)
                    VALUES (:sym, :hl, 'financial_juice', :sent, :mat, :cat, now())
                    ON CONFLICT DO NOTHING
                    """,
                    dict(sym=symbol, hl=headline, sent=sentiment,
                         mat=is_material, cat=category),
                )
            db.commit()

        for symbol in symbols:
            if sentiment == "negative":
                self._rc.setex(f"stockai:realtime_news:{symbol}:negative",
                               NEWS_REDIS_TTL, "1")
            elif sentiment == "positive":
                self._rc.setex(f"stockai:realtime_news:{symbol}:positive",
                               NEWS_REDIS_TTL, "1")

        log.info("realtime_news.ingested", source="financial_juice",
                 headline=headline[:80], sentiment=sentiment, symbols=symbols)
```

### Phase 3 — Hot-news gate in signal-engine

New helper in `signals.py`:

```python
def _check_realtime_news(symbol: str) -> str | None:
    """Return 'negative', 'positive', or None from Redis hot-news flags.

    Checks the 20-minute rolling window written by the market-data ingestor.
    Returns None on any Redis failure (fail-open — never block signal generation).
    """
    try:
        from common.redis_client import get_redis as _get_redis
        rc = _get_redis()
        if rc.get(f"stockai:realtime_news:{symbol}:negative"):
            return "negative"
        if rc.get(f"stockai:realtime_news:{symbol}:positive"):
            return "positive"
    except Exception:
        pass
    return None
```

Wire into `generate_all_signals()` alongside the existing `eight_k_flag` block:

```python
# In generate_all_signals(), after the 8-K check:
_realtime_news = _check_realtime_news(symbol)
reasons["realtime_news_flag"] = _realtime_news  # 'negative' | 'positive' | None
```

Wire into `_apply_style_signal()` — two new adjustments after the existing news sentiment block:

```python
# ── Real-time news gate ───────────────────────────────────────────────────────
# Negative headline in the last 20 minutes: hard compress BUY regardless of
# style. This catches the "BUY into a gap-down" failure mode where Yahoo/Google
# News hasn't picked up the headline yet but the stock is already moving.
# Positive headline: small boost, same magnitude as options_sentiment="bullish".
_rt_news = base_reasons.get("realtime_news_flag")
if _rt_news == "negative" and fused > 0.5:
    fused = 0.5 + (fused - 0.5) * 0.60   # strong compress — breaking bad news
    fused = float(np.clip(fused, 0.0, 1.0))
    reasons["realtime_news_gate"] = "negative_compress"
elif _rt_news == "positive" and fused > 0.5:
    fused = float(np.clip(fused + 0.03, 0.0, 1.0))
    reasons["realtime_news_gate"] = "positive_boost"
else:
    reasons["realtime_news_gate"] = "none"
```

---

## New `.env` variables required

```bash
# Financial Juice Discord (Phase 2 only — not needed for Phase 1)
DISCORD_BOT_TOKEN=
FINANCIAL_JUICE_CHANNEL_ID=
```

Add to `.env.example` with empty values and a comment explaining how to obtain them.

---

## New `reasons` fields

| Field | Type | Values | Added by |
|-------|------|--------|----------|
| `realtime_news_flag` | str \| None | `'negative'` \| `'positive'` \| `None` | `generate_all_signals()` |
| `realtime_news_gate` | str | `'negative_compress'` \| `'positive_boost'` \| `'none'` | `_apply_style_signal()` |

---

## Signal impact summary

| Scenario | Before | After |
|----------|--------|-------|
| Negative headline drops, Yahoo not yet updated | BUY fires into gap-down | BUY compressed ×0.60 → likely falls below threshold |
| Positive earnings beat, Yahoo not yet updated | HOLD stays HOLD | HOLD gets +0.03 boost → may cross BUY threshold |
| No real-time news for symbol | Unchanged | Unchanged (`realtime_news_gate = "none"`) |
| Redis unavailable | Unchanged | Unchanged (fail-open) |

---

## Limitations and risks

| Risk | Mitigation |
|------|-----------|
| Ticker extraction false positives (e.g. "AT" parsed as AT&T) | `_STOPWORDS` filter; review false-positive rate after 2 weeks |
| Sentiment misclassification on ambiguous headlines ("FDA requests additional data") | Keyword classifier is intentionally conservative — only clear signals trigger; ambiguous headlines return `neutral` |
| Stock Titan RSS feed URL changes | Monitor with a health-check alert; fallback to Yahoo/Google if feed returns non-200 |
| Financial Juice Discord bot banned | Phase 2 is additive — Phase 1 RSS pipeline continues independently |
| Redis TTL mismatch (20 min news window vs 60s signal refresh) | TTL is intentionally longer than signal refresh — a negative headline should suppress BUYs for the full 20-minute danger window |

---

## Build order

| Phase | What | Effort | Value |
|-------|------|--------|-------|
| 1 | Stock Titan RSS ingestor + DB table + Redis flags | ~1 day | High — catches most material events |
| 2 | Hot-news gate in `generate_all_signals()` + `_apply_style_signal()` | ~2 hours | High — directly prevents gap-down BUYs |
| 3 | Financial Juice Discord listener | ~half day | Medium — faster but more fragile |
| 4 | Sentiment upgrade (Claude Haiku on material headlines) | ~2 hours | Medium — improves classification accuracy |

Phase 1 + 2 together are the minimum viable improvement. Phase 3 and 4 are additive.

---

## Verification query

After Phase 1 + 2 are live, check that the gate is firing:

```sql
-- Headlines ingested in the last hour
SELECT source, sentiment, category, symbol, headline, published_at
FROM realtime_news
WHERE published_at >= now() - interval '1 hour'
ORDER BY published_at DESC
LIMIT 20;

-- Signals where the gate fired
SELECT symbol, ts, signal, confidence,
       reasons->>'realtime_news_flag' AS news_flag,
       reasons->>'realtime_news_gate' AS news_gate
FROM signals
WHERE reasons->>'realtime_news_gate' != 'none'
  AND ts >= now() - interval '24 hours'
ORDER BY ts DESC;
```
