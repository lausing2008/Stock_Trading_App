"""Which headlines this platform pays to classify, and why.

THE DEFECT THIS CLOSES. Alpaca subscribes to `news: ["*"]` and tags every article with the
symbols it mentions. The storage path treated the presence of a tag as relevance, so an article
about any listed company anywhere counted as in-scope. Measured over 24h on 2026-10-05: 834 of
968 classified Alpaca articles — 86.2% — mentioned no active tracked stock.

OUT OF UNIVERSE IS NOT THE SAME AS USELESS, which is why this is a scope list rather than a
watchlist filter. An SPY, oil or rates story is market context this platform genuinely uses
even though SPY is not a tracked position. The measured out-of-universe mentions were led by
SPY (33 distinct URLs), USO (22), BTCUSD (19) — the first two are exactly that kind of context.
So context symbols are named EXPLICITLY and classified; everything else is still STORED, with
its headline, source and timestamp, and simply not labelled.

WHY AN EXPLICIT LIST AND NOT A HEURISTIC. A rule like "classify anything an ETF mentions" would
drift silently as new tickers appear. A named list is auditable: a reader can see exactly what
this platform considers market context, and adding to it is a visible decision.
"""
from __future__ import annotations

#: Broad market and volatility proxies.
_MARKET = {"SPY", "QQQ", "DIA", "IWM", "VTI", "VOO", "RSP", "MDY",
           "VIX", "VIXY", "UVXY", "^VIX", "^GSPC", "^DJI", "^IXIC", "^RUT"}

#: Rates, credit and currency — the macro channels the reports name as drivers.
_RATES_CREDIT_FX = {"TLT", "IEF", "SHY", "BIL", "AGG", "BND", "LQD", "HYG", "JNK", "TIP",
                    "UUP", "FXE", "FXY", "UDN", "DXY"}

#: Commodities that act as input costs rather than positions.
_COMMODITIES = {"USO", "UNG", "BNO", "GLD", "IAU", "SLV", "DBC", "DBA", "XLE"}

#: Sector SPDRs — "which sector moved" is context for a stock in it.
_SECTORS = {"XLK", "XLF", "XLV", "XLY", "XLP", "XLI", "XLB", "XLRE", "XLU", "XLC",
            "SMH", "SOXX", "XBI", "IBB", "KRE", "ITB", "XRT", "XHB", "JETS"}

#: Hong Kong market context, because HK reports exist and a US-only list would silently
#: under-serve them.
_HK = {"2800.HK", "2828.HK", "3033.HK", "^HSI"}

#: The full set. Membership is the whole rule — no prefixes, no pattern matching.
MARKET_CONTEXT_SYMBOLS: frozenset[str] = frozenset(
    _MARKET | _RATES_CREDIT_FX | _COMMODITIES | _SECTORS | _HK)


def classification_scope(symbols, active_universe) -> str | None:
    """Why this headline is worth classifying: "tracked", "market_context", or None.

    `symbols` is whatever the source resolved — provider tags, a CIK lookup or a headline
    extraction. `active_universe` is the set of currently active tracked symbols.

    RETURNS None RATHER THAN FALSE so the caller cannot accidentally treat "no reason" as a
    reason; the value is also the audit label for why the platform paid.
    """
    if not symbols:
        return None
    upper = {str(s).upper() for s in symbols if s}
    if upper & {str(a).upper() for a in active_universe}:
        return "tracked"
    if upper & MARKET_CONTEXT_SYMBOLS:
        return "market_context"
    return None
