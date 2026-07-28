"""PR Newswire + Business Wire RSS pollers — both verified LIVE (real 200 + parseable feed
entries with a current Last-Modified, not just a reachable URL — see the "It's reachable ≠
it's current" discipline documented in this repo's CLAUDE.md) before this module was written.

The original design's GlobeNewswire RSS URL was checked directly during this rewrite and found
to 404 across every guessed URL pattern (their public RSS discovery page appears discontinued);
GlobeNewswire was dropped in favor of Business Wire, whose feed was confirmed live with a
Last-Modified timestamp within the hour and real, current parseable entries.

PR Newswire's financial-news category feed is pre-filtered by the source itself. Business
Wire's only public feed is a generic, multi-language international "home" firehose (no
finance-only category feed was found reachable) — filtered here to ASCII-only titles as a
simple, honest heuristic to drop the non-English noise; this is not a perfect language filter,
just good enough to remove the bulk of non-financial-relevant volume before it reaches
classification.
"""
from __future__ import annotations

from datetime import datetime, timezone

import feedparser
import structlog

log = structlog.get_logger()

# PR Newswire's financial-news RSS category feed.
_PR_NEWSWIRE_URL = "https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss"

# Business Wire's only public RSS feed — a generic international firehose, ASCII-filtered below.
_BUSINESSWIRE_URL = "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFpQXQ%3D%3D"


def _parse_feed(url: str, source_name: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
    except Exception as exc:
        log.warning("rss_source.parse_failed", source=source_name, url=url, error=str(exc))
        return []
    if getattr(feed, "bozo", False) and not feed.entries:
        log.warning("rss_source.bozo_empty", source=source_name, url=url, error=str(getattr(feed, "bozo_exception", "")))
        return []

    items: list[dict] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        link = entry.get("link") or None
        published = entry.get("published_parsed")
        if published:
            ts = datetime(*published[:6], tzinfo=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)
        items.append({"headline": title, "url": link, "published_at": ts})
    return items


def fetch_pr_newswire() -> list[dict]:
    return _parse_feed(_PR_NEWSWIRE_URL, "pr_newswire")


def fetch_businesswire() -> list[dict]:
    items = _parse_feed(_BUSINESSWIRE_URL, "businesswire")
    return [it for it in items if it["headline"].isascii()]
