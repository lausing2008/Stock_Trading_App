"""Universe-aware ticker matching — deliberately NOT a bare regex over the headline.

The abandoned DESIGN_REALTIME_NEWS_FEED_2026-07-25.md design used a standalone regex
(`\\b[A-Z]{1,5}\\b`-style) to guess tickers directly from headline text, which matched common
English acronyms as if they were real stock symbols ("EPS", "CEO", "AI", "IPO", "FDA" all read
as plausible 2-4 letter all-caps tokens). This module fixes that by only ever matching against
the app's own real, finite stock universe (Stock.symbol + Stock.name), loaded fresh from the
DB and cached in-process — a headline can only ever be tagged with a ticker this app actually
tracks, never an arbitrary all-caps token.
"""
from __future__ import annotations

import re
import time

import structlog
from sqlalchemy import select

from db import SessionLocal, Stock

log = structlog.get_logger()

_UNIVERSE_TTL_SECONDS = 900  # 15 min — the universe changes rarely (new stock added, etc.)
_universe_cache: list[tuple[str, str, str]] = []  # (symbol, name_upper, market)
_universe_cache_at: float = 0.0
_cik_cache: dict[str, str] = {}  # cik (zero-stripped) -> symbol
_cik_cache_at: float = 0.0


def _load_universe() -> list[tuple[str, str, str]]:
    global _universe_cache, _universe_cache_at
    now = time.time()
    if _universe_cache and now - _universe_cache_at < _UNIVERSE_TTL_SECONDS:
        return _universe_cache
    try:
        with SessionLocal() as session:
            rows = session.execute(
                select(Stock.symbol, Stock.name, Stock.market).where(Stock.active.is_(True))
            ).all()
        _universe_cache = [(sym, (name or "").upper(), str(market)) for sym, name, market in rows]
        _universe_cache_at = now
    except Exception as exc:
        log.warning("tickers.universe_load_failed", error=str(exc))
    return _universe_cache


def _load_cik_map() -> dict[str, str]:
    global _cik_cache, _cik_cache_at
    now = time.time()
    if _cik_cache and now - _cik_cache_at < _UNIVERSE_TTL_SECONDS:
        return _cik_cache
    try:
        with SessionLocal() as session:
            rows = session.execute(
                select(Stock.cik, Stock.symbol).where(Stock.cik.is_not(None))
            ).all()
        _cik_cache = {cik.lstrip("0"): sym for cik, sym in rows if cik}
        _cik_cache_at = now
    except Exception as exc:
        log.warning("tickers.cik_map_load_failed", error=str(exc))
    return _cik_cache


def symbol_for_cik(cik: str | None) -> str | None:
    """Resolve a raw SEC CIK (any zero-padding) to this app's tracked symbol, or None if the
    filer isn't in our tracked universe — most EDGAR filers aren't stocks we track at all, so
    a miss here is the normal, expected case, not an error."""
    if not cik:
        return None
    return _load_cik_map().get(cik.lstrip("0"))


# A bare ticker string ("AAPL") only counts as a real mention when it appears as its own
# word — not as a substring of a longer word — and (for US tickers) is usually written with a
# $ prefix or all-caps in financial headlines; matching bare 1-2 letter symbols is deliberately
# skipped (too many false positives, e.g. a real symbol "A" or "T" matching the article "a"/"to"
# case-insensitively) — this mirrors real financial-headline convention where short symbols are
# almost always announced with the company name alongside them anyway.
def _symbol_pattern(symbol: str) -> re.Pattern[str]:
    base = symbol.split(".")[0]  # strip .HK suffix for headline matching — headlines don't use it
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(base)}(?![A-Za-z0-9])")


# AUD264-NEWS-COMPANY-NAME-UNBOUNDED-SUBSTRING: the company-name branch used to be a bare
# `name_upper in upper` substring test with only a 4-character length floor — no boundary at
# all. "Target" matched "target"/"targets"/"targeted"; "Block" matched "Regulators block
# merger"; "Match" matched "...matched...". Reuses the exact same alphanumeric-boundary
# construction _symbol_pattern() already applies to ticker matching, so "TARGETS"/"TARGETED"
# (a different WORD, not the same word with trailing punctuation) can no longer match — this
# does not eliminate every false positive a common-English-word company name can produce (a
# headline genuinely using "target" as a verb, same letters, same case-insensitive match, is
# structurally indistinguishable from the company name without much heavier NLP), but it closes
# the concrete substring-continuation cases named above, matching the fix this tracker item
# scopes to.
def _name_pattern(name_upper: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9]){re.escape(name_upper)}(?![A-Za-z0-9])")


def extract_symbols(headline: str, max_matches: int = 5) -> list[str]:
    """Return real, universe-matched stock symbols mentioned in `headline`.

    Matches on two independent signals, either is sufficient: (1) the stock's own ticker
    symbol appears as a standalone token (only checked for symbols with 3+ base characters,
    to avoid 1-2 letter symbols matching common short words), or (2) the stock's full company
    name (or a distinctive multi-word prefix of it) appears as a standalone word/phrase in the
    headline (see _name_pattern() — same alphanumeric-boundary construction as the ticker
    branch, not a bare substring test). Company-name matching catches headlines that never
    spell out the ticker at all ("Apple announces ..." with no "AAPL" anywhere) — the more
    common real-world case for PR Newswire/GlobeNewswire headlines, which are written for a
    general audience, not a trading terminal.

    AUD264-NEWS-HK-NUMERIC-TICKER-FALSE-POSITIVE: a PURELY NUMERIC base (HK tickers like
    "0700", after the .HK suffix is stripped for matching) collides with ordinary numeric
    headline text — times ("0700 GMT"), dates, percentages — in a way alphabetic tickers/names
    essentially never do; the alphanumeric-boundary check above only excludes SUBSTRING
    continuations (e.g. "07001"), not a genuinely standalone but unrelated numeric token. Fixed
    by requiring a numeric-only base to also co-occur with real context in the SAME headline —
    either the .HK suffix explicitly, or the stock's own company name — before counting a bare
    numeric match. This mirrors the tracker item's own first candidate approach (require the
    .HK suffix or company name to co-occur) rather than the $-prefix/context-word alternative,
    since HK-market PR headlines conventionally include the .HK suffix directly (e.g.
    "0700.HK reports record profit") far more often than a bare "$0700"-style dollar prefix.
    """
    if not headline:
        return []
    upper = headline.upper()
    universe = _load_universe()
    matches: list[str] = []
    for symbol, name_upper, _market in universe:
        base = symbol.split(".")[0]
        matched = False
        if len(base) >= 3 and _symbol_pattern(symbol).search(headline):
            if base.isdigit():
                if symbol.upper() in upper or (name_upper and _name_pattern(name_upper).search(upper)):
                    matched = True
            else:
                matched = True
        if not matched and name_upper and len(name_upper) >= 4 and _name_pattern(name_upper).search(upper):
            matched = True
        if matched:
            matches.append(symbol)
        if len(matches) >= max_matches:
            break
    return matches
