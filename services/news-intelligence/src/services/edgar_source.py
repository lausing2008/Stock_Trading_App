"""SEC EDGAR real-time filing feed — distinct from event-intelligence's existing DAILY-BATCH
8-K sync (services/event-intelligence/src/services/... 8-K ingestion documented elsewhere in
this repo's CLAUDE.md, T11/T208). This polls EDGAR's `action=getcurrent` endpoint, which lists
filings as they're actually submitted throughout the trading day — confirmed live during this
rewrite: a real request returned entries with <updated> timestamps only minutes old, not a
daily-batch snapshot. This is a genuinely faster-latency, complementary source, not a
replacement for the existing daily sync (which serves a different purpose — a complete,
reliable daily catch-up regardless of whether this real-time feed had a gap).

Matches every filing type, not just 8-K, filtered to types most likely to be market-moving for
a retail trading app (8-K itself, plus 4 — insider transactions — and SC 13D/G — activist/large
stakes) via EDGAR's own `type=` query param, one poll per type per cycle.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import feedparser
import structlog

log = structlog.get_logger()

_EDGAR_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"
_FILING_TYPES = ["8-K", "4", "SC 13D"]

# SEC EDGAR requires a real identifying User-Agent on every request (its own published fair-use
# policy) — a generic "Mozilla/5.0" alone can get rate-limited/blocked. Matches the convention
# already used by event-intelligence's own EDGAR calls elsewhere in this repo.
_USER_AGENT = "StockAI News Intelligence research@lausing.com"

# "8-K/A - Some Company Name, Inc. (0001234567) (Filer)" — extract company name + CIK.
_TITLE_RE = re.compile(r"^(?P<form>\S+(?:/\S+)?)\s*-\s*(?P<company>.+?)\s*\((?P<cik>\d+)\)\s*\(Filer\)$")


def _fetch_one(filing_type: str) -> list[dict]:
    url = f"{_EDGAR_BASE}?action=getcurrent&type={filing_type}&company=&dateb=&owner=include&count=40&output=atom"
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": _USER_AGENT})
    except Exception as exc:
        log.warning("edgar_source.parse_failed", filing_type=filing_type, error=str(exc))
        return []

    items: list[dict] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        m = _TITLE_RE.match(title)
        # Use the ACTUAL parsed form from the title (e.g. "8-K/A"), not the query's own
        # `filing_type` param — EDGAR's `type=8-K` query also returns 8-K/A amendments, and
        # mislabeling an amendment as a fresh "8-K filed" would misrepresent a real filing.
        form = m.group("form") if m else filing_type
        company = m.group("company") if m else title
        cik = m.group("cik") if m else None
        link = entry.get("link") or None
        updated = entry.get("updated_parsed")
        ts = datetime(*updated[:6], tzinfo=timezone.utc) if updated else datetime.now(timezone.utc)
        headline = f"{form} filed — {company}"
        items.append({"headline": headline, "url": link, "published_at": ts, "cik": cik})
    return items


def fetch_edgar_realtime() -> list[dict]:
    items: list[dict] = []
    for t in _FILING_TYPES:
        items.extend(_fetch_one(t))
    return items
