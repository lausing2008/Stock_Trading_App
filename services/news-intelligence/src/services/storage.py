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
from .scope import classification_scope as _classification_scope
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


# R05 (2026-09-24 follow-up audit): write the flag only if nobody changed it since we read it.
# The decision below is read-check-write, and without a compare the check can be made against a
# value a concurrent ingest has already replaced — silently discarding a newer adverse event.
_HOT_SET_IF_UNCHANGED_LUA = """
local cur = redis.call("GET", KEYS[1])
if (cur == false and ARGV[1] == "") or (cur == ARGV[1]) then
    redis.call("SET", KEYS[1], ARGV[2], "EX", ARGV[3])
    return 1
end
return 0
"""


# SR-08: a story first seen more than this many hours after publication is DELAYED
# DISCLOSURE, not a fresh catalyst. Set to the hot-news flag's own TTL window: inside it, the
# market reaction is still plausibly unfolding; beyond it, the information has been public
# longer than the flag would have survived, so treating it as just-released is a claim about
# timing the arrival time does not support.
_DELAYED_DISCLOSURE_HOURS = 2.0


def _mark_hot(symbol: str, headline: str, sentiment_label: str | None,
              published_at=None) -> None:
    """Set (or refresh) the hot-news flag for a symbol.

    AUD264-HOTNEWS-FLAG-STALE-NO-CLEAR-PATH: the payload carries `ts` so signal-engine's reader
    can decay the compression with real age rather than applying a flat binary for the full 2h.

    R05 — TWO DEFECTS FIXED HERE.

    1. A POSITIVE STORY COULD SILENTLY OVERWRITE AN UNRESOLVED NEGATIVE EVENT. Every material,
       non-macro classification called this function directly, so it replaced whatever was
       stored without consulting anything — the recency guard added for DA-09 sits on the CLEAR
       path and this route went around it entirely. An older material-positive article ingested
       late therefore removed an active negative flag just as effectively as clearing it, and
       nothing recorded that it had happened. Overwriting a negative flag with a non-negative
       one is a CLEAR decision wearing a different name, so it now has to satisfy the same test.
    2. `ts` WAS INGESTION TIME COMPARED AGAINST PUBLICATION TIME. The guard compares a
       follow-up's `published_at` to this field; storing `datetime.now()` in it meant comparing
       two different clocks, and a valid correction published after the adverse article but
       before its delayed ingestion was rejected as "older". Both are now stored, and the guard
       prefers publication-to-publication.

    STILL NOT CLOSED, and the audit says so: an unrelated newer POSITIVE story can still clear
    an unresolved event, because nothing links a story to the event it supposedly resolves.
    That needs the event table — ids, materiality, supersession, expiry — not another guard.
    """
    key = f"{_HOT_NEWS_KEY_PREFIX}{symbol.upper()}"
    incoming = (sentiment_label or "neutral").lower()
    now_iso = datetime.now(timezone.utc).isoformat()
    pub_iso = None
    if published_at is not None:
        try:
            pub_iso = published_at.isoformat() if hasattr(published_at, "isoformat") else str(published_at)
        except Exception:
            pub_iso = None

    # SR-08 (2026-10-02): DELAYED ARRIVAL USED TO RESET PERCEIVED ECONOMIC AGE.
    # The flag's `ts` is ingestion time and signal-engine's decay reads `ts`, so a story
    # PUBLISHED on September 1 and first ingested on October 1 received an October 1 stamp and
    # the full first-hour compression — a month-old fact treated as a just-released catalyst.
    # Publication time was already stored (R05) but nothing consumed it for age.
    #
    # The two clocks are now stated separately AND the writer classifies the gap, so the
    # consumer does not have to re-derive it from two fields it might read inconsistently.
    # Delayed material information is not discarded — newly-disclosed risk is still risk —
    # but it is labelled as delayed evidence rather than a fresh reaction.
    _pub_age_h = None
    if published_at is not None:
        try:
            _pub_dt = published_at if hasattr(published_at, "tzinfo") else datetime.fromisoformat(str(published_at))
            if _pub_dt.tzinfo is None:
                _pub_dt = _pub_dt.replace(tzinfo=timezone.utc)
            _pub_age_h = (datetime.now(timezone.utc) - _pub_dt).total_seconds() / 3600.0
        except Exception:
            _pub_age_h = None

    payload = json.dumps({
        "headline": headline,
        "sentiment_label": incoming,
        # Ingestion time — what this platform knew, and when. Kept under its original name so
        # signal-engine's age-decay reader is unaffected.
        "ts": now_iso,
        # R05: when the story was PUBLISHED, so a later comparison is like-for-like.
        "published_at": pub_iso,
        "ingested_at": now_iso,
        # SR-08: how old the INFORMATION was when this platform first saw it. None means the
        # source gave no publication time — unknown, which is not the same as zero.
        "publication_age_hours_at_ingest": (
            round(_pub_age_h, 3) if _pub_age_h is not None else None),
        # SR-08: the explicit classification. A reader must not have to infer "is this a fresh
        # catalyst or an old fact we just learned" from arithmetic it might get wrong.
        "delayed_disclosure": (
            None if _pub_age_h is None else bool(_pub_age_h > _DELAYED_DISCLOSURE_HOURS)),
    })

    try:
        r = get_redis()
        # Bounded optimistic-concurrency loop. Re-deciding on a lost race is NOT optional: the
        # alternative — dropping the write — loses a material NEGATIVE brake whenever an
        # unrelated story happens to land in the same instant, which is the very outcome this
        # function exists to prevent. Re-read, re-judge against what is actually there now, and
        # write again. Three attempts, then give up rather than spin.
        for _attempt in range(3):
            raw_prev = r.get(key)
            prev = _parse_hot(raw_prev)

            if (prev and (prev.get("sentiment_label") or "").lower() == "negative"
                    and incoming != "negative"):
                # R05: replacing a negative flag with a non-negative one IS a clear. Same test.
                if not _may_clear_negative_flag(symbol, {"sentiment_label": incoming},
                                                published_at, flagged=prev):
                    log.info("news_storage.hot_flag_overwrite_refused", symbol=symbol,
                             incoming=incoming, headline=headline[:120],
                             note="non-negative story may not replace an unresolved negative event")
                    return

            if r.eval(_HOT_SET_IF_UNCHANGED_LUA, 1, key,
                      raw_prev if raw_prev is not None else "",
                      payload, _HOT_NEWS_TTL_SECONDS):
                return
            log.info("news_storage.hot_flag_write_retry", symbol=symbol, attempt=_attempt + 1)
        log.warning("news_storage.hot_flag_write_gave_up", symbol=symbol, sentiment=incoming)
    except Exception as exc:
        log.warning("news_storage.hot_flag_failed", symbol=symbol, error=str(exc))


# R05: delete the flag ONLY if it is still the exact value the guard was allowed to judge. A
# plain DEL here erases whatever is present at the moment it runs — including a NEWER adverse
# event written between the guard's read and this call, which would silently drop a real brake.
_HOT_DEL_IF_UNCHANGED_LUA = """
if redis.call("GET", KEYS[1]) == ARGV[1] then
    redis.call("DEL", KEYS[1])
    return 1
end
return 0
"""


def _clear_hot(symbol: str, expect_raw: str | bytes | None = None) -> None:
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
    key = f"{_HOT_NEWS_KEY_PREFIX}{symbol.upper()}"
    try:
        r = get_redis()
        if expect_raw is None:
            # No observed value to compare against — the caller did not read first, so there is
            # nothing this could be racing with from its own point of view.
            r.delete(key)
            return
        if not r.eval(_HOT_DEL_IF_UNCHANGED_LUA, 1, key, expect_raw):
            log.info("news_storage.hot_flag_clear_raced", symbol=symbol,
                     note="flag changed after the guard read it; the newer flag was kept")
    except Exception as exc:
        log.warning("news_storage.hot_flag_clear_failed", symbol=symbol, error=str(exc))


def _current_hot_sentiment(symbol: str) -> str | None:
    payload = _current_hot_payload(symbol)
    return payload.get("sentiment_label") if payload else None


def _current_hot_raw(symbol: str) -> str | bytes | None:
    """The flag's stored BYTES. R05 needs the exact value, not just its parse, so the clear can
    be conditioned on nothing having replaced it since."""
    try:
        return get_redis().get(f"{_HOT_NEWS_KEY_PREFIX}{symbol.upper()}")
    except Exception:
        return None


def _parse_hot(raw) -> dict | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _current_hot_payload(symbol: str) -> dict | None:
    """The whole stored flag, not just its label — DA-09 needs the flagged event's timestamp."""
    return _parse_hot(_current_hot_raw(symbol))


def _may_clear_negative_flag(symbol: str, cls: dict, published_at, flagged: dict | None = None) -> bool:
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
    # R05: the caller may pass the flag it already read, so the guard and the compare-and-delete
    # judge the SAME value. Reading again here would reopen the race it is meant to close.
    if flagged is None:
        flagged = _current_hot_payload(symbol)
    if not flagged:
        return False
    # R05: COMPARE LIKE WITH LIKE. This originally read the flag's `ts`, which _mark_hot() wrote
    # as datetime.now() — INGESTION time — and compared it against the follow-up's PUBLICATION
    # time. Since ingestion always trails publication, a correction genuinely published after the
    # adverse article was still rejected as "older" whenever the adverse article was ingested
    # late. Prefer the flagged story's own publication time now that it is stored.
    flagged_ts = flagged.get("published_at") or flagged.get("ts")
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

        # TAGGED IDENTITY IS NOT RELEVANCE. Alpaca subscribes to `news: ["*"]` and ships a
        # symbols list on every article, and this took that list as-is — so an article about
        # any listed company in the world counted as "resolved" and was paid to classify.
        # Measured over 24h on 2026-10-05: 834 of 968 classified Alpaca articles (86.2%)
        # mentioned no active tracked stock, and single-headline calls alone burned 219,247
        # tokens. See docs/audits/2026-10-05-news-classification-volume-review.md.
        #
        # The tags are still kept — they are what the article is ABOUT. They just no longer
        # decide, by themselves, whether this platform pays to label it.
        _active = {sym for sym, _n, _m in _load_universe()}

        resolved_symbols: list[list | None] = []
        _in_scope: list[str | None] = []   # why each item is (or is not) worth classifying
        for it in _new_items:
            if symbol_mode == "tagged":
                _syms = it.get("symbols")
            elif symbol_mode == "cik":
                _s = symbol_for_cik(it.get("cik"))
                _syms = [_s] if _s else None
            else:
                _syms = extract_symbols(it["headline"])
            resolved_symbols.append(_syms)
            _in_scope.append(_classification_scope(_syms, _active))

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
        else:
            # "tagged" NOW DEPENDS ON THE UNIVERSE TOO. It previously needed no resolver —
            # the source supplied symbols — so it was exempt from this guard. Scoping those
            # tags against the active universe makes it depend on the universe loading, and
            # an empty universe would otherwise classify NOTHING from the busiest source
            # while looking like a saving. Same fail-open rule as the others.
            _resolver_ok = bool(_active)
        if not _resolver_ok:
            log.warning(
                "news_storage.resolver_unavailable_classifying_all",
                source=source, symbol_mode=symbol_mode, new_items=len(_new_items),
                note="ticker universe / CIK map empty — cannot tell 'untracked' from 'unknown'",
            )

        api_key = get_admin_ai_key("claude")
        _to_classify = [
            i for i, scope in enumerate(_in_scope)
            if scope is not None or _classify_untracked or not _resolver_ok
        ]
        classifications: list = [None] * len(_new_items)
        if api_key and _to_classify:
            _results = classify_in_batches(
                [_new_items[i]["headline"] for i in _to_classify], api_key,
                scopes=[_in_scope[i] for i in _to_classify],
                urls=[_new_items[i].get("url") for i in _to_classify],
            )
            for _pos, _idx in enumerate(_to_classify):
                if _pos < len(_results):
                    classifications[_idx] = _results[_pos]

        # COUNTED BY REASON, not just totals — the dashboard needs to show relevance, and
        # "how many did we skip" cannot be answered from a single number.
        _by_scope: dict[str, int] = {}
        for _s in _in_scope:
            _by_scope[_s or "out_of_scope"] = _by_scope.get(_s or "out_of_scope", 0) + 1
        log.info(
            "news_storage.classify_scoped",
            source=source, new_items=len(_new_items), classified=len(_to_classify),
            skipped_untracked=len(_new_items) - len(_to_classify),
            tracked=_by_scope.get("tracked", 0),
            market_context=_by_scope.get("market_context", 0),
            out_of_scope=_by_scope.get("out_of_scope", 0),
            resolver_ok=_resolver_ok, classify_untracked=_classify_untracked,
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
                        _mark_hot(sym, headline, cls["sentiment_label"],
                                  raw.get("published_at"))
                    # AUD264-HOTNEWS-FLAG-STALE-NO-CLEAR-PATH: any OTHER new, real,
                    # COMPANY-SPECIFIC classification for this symbol — positive, neutral, or
                    # simply non-material — is genuine evidence the situation has moved on and
                    # must be allowed to clear a currently-negative flag, not just silently
                    # fail to extend it. Still excludes "macro" (matching
                    # AUD264-NEWS-MACRO-CATEGORY-IGNORED's own established reasoning a few
                    # lines above): an index-level story is not evidence ABOUT this specific
                    # company either way, so it must not clear a company-specific flag any
                    # more than it should be allowed to set one.
                    elif sym and cls and cls["category"] != "macro":
                        # R05: ONE read serves both the guard and the delete. Previously
                        # `_current_hot_sentiment()` read the flag, `_may_clear_negative_flag()`
                        # read it again, and `_clear_hot()` then deleted whatever was present —
                        # three separate looks at a value another ingest can replace in between,
                        # so a newer adverse event arriving mid-decision was erased by a verdict
                        # that had never seen it. Read once, judge that value, and delete only
                        # if it is still the one that was judged.
                        _prev_raw = _current_hot_raw(sym)
                        _prev = _parse_hot(_prev_raw)
                        if (
                            (_prev or {}).get("sentiment_label") == "negative"
                            # DA-09: an unrelated, still-negative, non-material story used to
                            # clear a material-negative brake here.
                            and _may_clear_negative_flag(
                                sym, cls, raw.get("published_at"), flagged=_prev)
                        ):
                            _clear_hot(sym, expect_raw=_prev_raw)
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
