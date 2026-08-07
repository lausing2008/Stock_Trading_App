"""Shared persistence for every ingestion source — one upsert path, one hot-news Redis flag.

Every ingestor (RSS pollers, EDGAR poller, Alpaca WebSocket) funnels through
`persist_news_items()` so classification, dedup, and the downstream hot-news signal flag all
happen in exactly one place regardless of which source produced the headline.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.ai_keys import get_admin_ai_key
from common.redis_client import get_redis
from db import SessionLocal, RealtimeNewsItem

from .classify import classify_in_batches
from .tickers import extract_symbols, symbol_for_cik

log = structlog.get_logger()

# T258-NEWS-INTELLIGENCE: signal-engine's BUY gate reads this key (see
# services/signal-engine/src/generators/signals.py's _hot_news_symbols() usage) — a short TTL
# per symbol, refreshed on every new material headline. Kept genuinely short (2h) since "hot"
# news should stop suppressing new BUY signals once the initial reaction window has passed;
# see docs/DESIGN_REALTIME_NEWS_FEED_2026-07-25.md's original intent (gap-down BUY suppression)
# for why this exists at all — this module generalizes it to any material headline, any
# direction, not just gap-downs.
_HOT_NEWS_TTL_SECONDS = 2 * 3600
_HOT_NEWS_KEY_PREFIX = "stockai:hot_news:"


def _mark_hot(symbol: str, headline: str, sentiment_label: str | None) -> None:
    try:
        r = get_redis()
        r.setex(
            f"{_HOT_NEWS_KEY_PREFIX}{symbol.upper()}",
            _HOT_NEWS_TTL_SECONDS,
            json.dumps({"headline": headline, "sentiment_label": sentiment_label or "neutral"}),
        )
    except Exception as exc:
        log.warning("news_storage.hot_flag_failed", symbol=symbol, error=str(exc))


def persist_news_items(
    raw_items: list[dict],
    source: str,
    symbol_mode: str = "extract",
) -> int:
    """Persist a batch of raw headlines for one source. Each raw item must have at minimum
    `headline`, `url` (may be None), `published_at` (datetime). Returns the count of new
    (symbol, headline) rows actually inserted (a re-poll of the same feed re-sees already-seen
    items — those are skipped via ON CONFLICT DO NOTHING on the (source, url, symbol) unique
    constraint, not counted here).

    BUG-NEWSCLASSIFY-REPEATCOST: found live — every RSS/EDGAR poll cycle re-fetches the SAME
    feed URL, which returns its most-recent N items regardless of what was already seen (RSS
    feeds are not "since last poll" incremental). The ON CONFLICT dedup above only prevents a
    duplicate DB ROW — it does nothing to prevent re-classifying an already-seen headline via
    Claude on every single cycle before that dedup ever runs. Confirmed live: pr_newswire had
    2,640 stored rows for only 22 distinct headlines (~120x reclassification), businesswire
    792-for-6 (~132x), sec_edgar 5,489-for-154 (~35x) — each duplicate a real, paid Claude call
    for content already classified minutes earlier. Fixed by checking which of this batch's
    URLs are ALREADY in the DB for this source BEFORE calling classify_in_batches() at all —
    only genuinely new URLs are ever sent to Claude. Items with url=None (rare) can't be
    deduped this way and are always classified — a small, bounded exception, not the common case.

    `symbol_mode` picks how each item's tracked symbol(s) are determined:
      - "extract" (RSS sources): run extract_symbols() against the headline text itself.
      - "cik" (EDGAR): resolve the item's own `cik` field via symbol_for_cik() — a filer's CIK
        is an exact, unambiguous identifier, so this never needs headline text-matching at all.
      - "tagged" (Alpaca): the item already carries a `symbols` list directly from the source's
        own native ticker tagging — used as-is.
    Classification (sentiment/materiality) always runs for genuinely new items, regardless of
    symbol_mode, since none of the three sources provide that metadata themselves.
    """
    if not raw_items:
        return 0

    with SessionLocal() as session:
        _urls = [it["url"] for it in raw_items if it.get("url")]
        _known_urls: set[str] = set()
        if _urls:
            _known_urls = set(
                session.execute(
                    select(RealtimeNewsItem.url).where(
                        RealtimeNewsItem.source == source,
                        RealtimeNewsItem.url.in_(_urls),
                    )
                ).scalars().all()
            )
        _new_items = [it for it in raw_items if not it.get("url") or it["url"] not in _known_urls]
        _skipped = len(raw_items) - len(_new_items)

        api_key = get_admin_ai_key("claude")
        headlines = [it["headline"] for it in _new_items]
        classifications = classify_in_batches(headlines, api_key) if api_key else [None] * len(headlines)

        inserted = 0
        for raw, cls in zip(_new_items, classifications):
            headline = raw["headline"]
            if symbol_mode == "tagged":
                symbols = raw.get("symbols")
            elif symbol_mode == "cik":
                sym = symbol_for_cik(raw.get("cik"))
                symbols = [sym] if sym else None
            else:
                symbols = extract_symbols(headline)
            symbols = symbols or [None]  # None = macro/market-wide, no ticker matched
            for sym in symbols:
                stmt = pg_insert(RealtimeNewsItem).values(
                    symbol=sym,
                    headline=headline,
                    source=source,
                    url=raw.get("url"),
                    sentiment_score=cls["sentiment_score"] if cls else None,
                    sentiment_label=cls["sentiment_label"] if cls else None,
                    is_material=bool(cls["is_material"]) if cls else False,
                    category=cls["category"] if cls else None,
                    published_at=raw["published_at"],
                ).on_conflict_do_nothing(
                    index_elements=["source", "url", "symbol"]
                )
                result = session.execute(stmt)
                if result.rowcount:
                    inserted += 1
                    # AUD264-NEWS-MACRO-CATEGORY-IGNORED: a headline classified "macro" (e.g.
                    # "Nasdaq slides as AAPL, MSFT and NVDA drag megacaps lower") is a story
                    # about the MARKET, not about any of the symbols it happens to name — it
                    # must never set a per-symbol hot-news flag, which exists specifically to
                    # suppress a BUY signal on company-specific bad news, not on an index-level
                    # move that mentions the company in passing. The classification already
                    # exists (classify.py) and is already persisted (category, just above) —
                    # this was previously the one place that computed it but never read it back.
                    if sym and cls and cls["is_material"] and cls["category"] != "macro":
                        _mark_hot(sym, headline, cls["sentiment_label"])
        session.commit()

    log.info(
        "news_storage.persisted",
        source=source, seen=len(raw_items), inserted=inserted,
        skipped_already_seen=_skipped, classified=len(_new_items),
    )
    return inserted


def is_hot(symbol: str) -> dict | None:
    """Read-side helper for signal-engine (via HTTP, not a direct import — separate service)."""
    try:
        raw = get_redis().get(f"{_HOT_NEWS_KEY_PREFIX}{symbol.upper()}")
        return json.loads(raw) if raw else None
    except Exception:
        return None


def recent_items(symbol: str | None, limit: int, since_hours: int = 48) -> list[RealtimeNewsItem]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)
    with SessionLocal() as session:
        q = select(RealtimeNewsItem).where(RealtimeNewsItem.published_at >= cutoff)
        if symbol:
            q = q.where(RealtimeNewsItem.symbol == symbol.upper())
        q = q.order_by(RealtimeNewsItem.published_at.desc()).limit(limit)
        return list(session.execute(q).scalars().all())
