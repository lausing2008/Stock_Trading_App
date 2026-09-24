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
from .tickers import _load_cik_map, _load_universe, extract_symbols, symbol_for_cik

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
    # AUD264-HOTNEWS-FLAG-STALE-NO-CLEAR-PATH: the payload previously carried no timestamp at
    # all, so signal-engine's reader could not tell a 2-minute-old headline from a
    # 119-minute-old one — the compression applied was a flat, binary all-or-nothing for the
    # full 2h window. Adding `ts` (a real ISO timestamp, not just relying on the Redis key's
    # own TTL) lets the reader compute real age and decay the compression's strength with it.
    try:
        r = get_redis()
        r.setex(
            f"{_HOT_NEWS_KEY_PREFIX}{symbol.upper()}",
            _HOT_NEWS_TTL_SECONDS,
            json.dumps({
                "headline": headline,
                "sentiment_label": sentiment_label or "neutral",
                "ts": datetime.now(timezone.utc).isoformat(),
            }),
        )
    except Exception as exc:
        log.warning("news_storage.hot_flag_failed", symbol=symbol, error=str(exc))


def _clear_hot(symbol: str) -> None:
    """AUD264-HOTNEWS-FLAG-STALE-NO-CLEAR-PATH: no delete path existed anywhere — a stale
    NEGATIVE flag could only ever be overwritten by another MATERIAL follow-up (never cleared
    by a genuine, non-material correction/retraction), and a positive material follow-up for
    the same symbol would overwrite it too, but only if is_material was independently true for
    THAT headline — a positive non-material follow-up ("shares recover after..." style
    coverage, common and often not itself flagged is_material by the LLM) could never clear a
    stale negative flag at all. This explicit clear function is called whenever a NEW
    classified headline for a symbol that currently has a NEGATIVE hot flag is anything other
    than itself material-and-negative — i.e. any genuine improvement (positive, neutral, or a
    non-material follow-up) actively clears the stale warning instead of leaving it to silently
    ride out its full 2h TTL regardless of what's actually happened since.
    """
    try:
        get_redis().delete(f"{_HOT_NEWS_KEY_PREFIX}{symbol.upper()}")
    except Exception as exc:
        log.warning("news_storage.hot_flag_clear_failed", symbol=symbol, error=str(exc))


def _current_hot_sentiment(symbol: str) -> str | None:
    payload = _current_hot_payload(symbol)
    return payload.get("sentiment_label") if payload else None


def _current_hot_payload(symbol: str) -> dict | None:
    """The whole stored flag, not just its label — DA-09 needs the flagged event's timestamp."""
    try:
        raw = get_redis().get(f"{_HOT_NEWS_KEY_PREFIX}{symbol.upper()}")
        if not raw:
            return None
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _may_clear_negative_flag(symbol: str, cls: dict, published_at) -> bool:
    """DA-09 (2026-09-24): whether this story is evidence an adverse event has resolved.

    The old condition was "any inserted, classified, non-macro story for a symbol whose flag is
    currently negative" — so an UNRELATED, still-NEGATIVE, non-material headline cleared a
    material-negative brake. The signal engine uses that flag to compress bullish fused scores,
    so removing it changes trading evidence on the strength of a story that said nothing good.

    Two guards, both answerable from what is already stored:

      * SENTIMENT. A negative story is not evidence that a negative situation improved. Only
        positive or neutral classifications may clear.
      * RECENCY. An older article ingested late must not clear a newer flag — that is a clock
        artefact, not news. Missing or unparseable timestamps fail CLOSED (no clear), because
        "I cannot tell which came first" is not grounds for removing a risk brake.

    HONESTLY INCOMPLETE. This narrows the defect, it does not close it: an unrelated POSITIVE
    story can still clear an unresolved adverse event, because nothing here links a story to the
    event it supposedly resolves. The audit's own remedy — event IDs, materiality, supersession
    and expiry, with active events aggregated per symbol instead of one last-writer-wins flag —
    needs a schema and is deliberately not attempted in passing.
    """
    if (cls.get("sentiment_label") or "neutral") == "negative":
        return False
    flagged = _current_hot_payload(symbol)
    if not flagged:
        return False
    flagged_ts = flagged.get("ts")
    if not flagged_ts or not published_at:
        return False
    try:
        from datetime import datetime as _dt

        ft = _dt.fromisoformat(str(flagged_ts))
        pt = published_at if hasattr(published_at, "tzinfo") else _dt.fromisoformat(str(published_at))
        if ft.tzinfo is None:
            ft = ft.replace(tzinfo=timezone.utc)
        if pt.tzinfo is None:
            pt = pt.replace(tzinfo=timezone.utc)
        return pt >= ft
    except Exception:
        return False


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

        # AUD-NEWSCLASSIFY-ORDERING (2026-09-22): resolve the symbol BEFORE spending a Claude
        # call, not after. This loop used to run only after classify_in_batches() had already
        # priced every headline — so the code paid to classify, then discovered the item mapped
        # to nothing. Measured over 263,457 classified rows: 186,461 (70.8%) had symbol IS NULL
        # and could never influence any decision. By source, sec_edgar 148,860 items / 97.0%
        # unusable, pr_newswire 99.9%, businesswire 99.9% — while alpaca is 0%, precisely
        # because it ships native ticker tags. 29,303 424B2 prospectus supplements were
        # classified to yield ONE material flag; ~7,400 fund-prospectus filings yielded zero of
        # anything. news_classify is 87% of this platform's entire Claude spend.
        #
        # Nothing here needed the LLM to run first: the EDGAR CIK is on the raw item and
        # symbol_for_cik() is a local lookup, and extract_symbols() reads only the headline
        # text. The ordering was simply backwards.
        #
        # THE TRADE-OFF, stated rather than buried: symbol-less rows still persist and still
        # appear in the market-wide /news feed, but now WITHOUT sentiment/materiality labels.
        # That is a real, if small, product change — an untracked company's headline keeps its
        # title, source and timestamp and loses its sentiment chip. Set
        # CLASSIFY_UNTRACKED_HEADLINES=1 to restore the old behaviour if the feed's labels turn
        # out to matter more than ~70% of the Claude bill.
        # See docs/audits/2026-09-22-news-llm-hmm-prediction-audit.md.
        import os as _os
        _classify_untracked = _os.getenv("CLASSIFY_UNTRACKED_HEADLINES") == "1"

        resolved_symbols: list[list | None] = []
        for it in _new_items:
            if symbol_mode == "tagged":
                _syms = it.get("symbols")
            elif symbol_mode == "cik":
                _s = symbol_for_cik(it.get("cik"))
                _syms = [_s] if _s else None
            else:
                _syms = extract_symbols(it["headline"])
            resolved_symbols.append(_syms)

        # FAIL OPEN, VISIBLY, WHEN THE RESOLVER ITSELF IS BROKEN. Skipping classification
        # because a headline genuinely maps to no tracked symbol is the intended saving.
        # Skipping it because the ticker universe or CIK map failed to LOAD is a different
        # thing entirely — it would silently stop every hot-news flag from ever being set, and
        # the only visible trace would be a warning in tickers.py. That is the exact
        # "an empty result must say WHICH nothing" failure this repo already paid for once
        # (T403: a dead feed and "this stock has no options" were the same string).
        # So: an unavailable resolver reverts to the old classify-everything behaviour and says
        # so, rather than quietly saving money by disabling a gate.
        if symbol_mode == "cik":
            _resolver_ok = bool(_load_cik_map())
        elif symbol_mode == "extract":
            _resolver_ok = bool(_load_universe())
        else:  # "tagged" — the source supplies symbols directly, nothing to load
            _resolver_ok = True
        if not _resolver_ok:
            log.warning(
                "news_storage.resolver_unavailable_classifying_all",
                source=source, symbol_mode=symbol_mode, new_items=len(_new_items),
                note="ticker universe / CIK map empty — cannot tell 'untracked' from 'unknown'",
            )

        api_key = get_admin_ai_key("claude")
        _to_classify = [
            i for i, syms in enumerate(resolved_symbols)
            if syms or _classify_untracked or not _resolver_ok
        ]
        classifications: list = [None] * len(_new_items)
        if api_key and _to_classify:
            _results = classify_in_batches(
                [_new_items[i]["headline"] for i in _to_classify], api_key
            )
            for _pos, _idx in enumerate(_to_classify):
                if _pos < len(_results):
                    classifications[_idx] = _results[_pos]

        log.info(
            "news_storage.classify_scoped",
            source=source, new_items=len(_new_items), classified=len(_to_classify),
            skipped_untracked=len(_new_items) - len(_to_classify),
        )

        inserted = 0
        for raw, cls, symbols in zip(_new_items, classifications, resolved_symbols):
            headline = raw["headline"]
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
                    # AUD264-HOTNEWS-FLAG-STALE-NO-CLEAR-PATH: any OTHER new, real,
                    # COMPANY-SPECIFIC classification for this symbol — positive, neutral, or
                    # simply non-material — is genuine evidence the situation has moved on and
                    # must be allowed to clear a currently-negative flag, not just silently
                    # fail to extend it. Still excludes "macro" (matching
                    # AUD264-NEWS-MACRO-CATEGORY-IGNORED's own established reasoning a few
                    # lines above): an index-level story is not evidence ABOUT this specific
                    # company either way, so it must not clear a company-specific flag any
                    # more than it should be allowed to set one.
                    elif (
                        sym and cls and cls["category"] != "macro"
                        and _current_hot_sentiment(sym) == "negative"
                        # DA-09: an unrelated, still-negative, non-material story used to clear
                        # a material-negative brake here. See _may_clear_negative_flag().
                        and _may_clear_negative_flag(sym, cls, raw.get("published_at"))
                    ):
                        _clear_hot(sym)
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
