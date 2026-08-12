"""T270-SECTOR-THEME-FORECAST-EMAIL: weekly "themes with real supporting signals today" digest.

Per docs/DESIGN_SIX_ITEM_BATCH_2026-08-11.md item 5's own honesty analysis (accepted verbatim
for this build — see that doc's "Recommended reframing" section): the literal ask ("which
themes will rally in the next few weeks, with reasons") would, if built as asked, be the first
feature in this codebase to break its own established discipline of never letting an alert
claim more certainty than the underlying data supports. Reframed instead to report themes with
GENUINELY ALREADY-MEASURED momentum/K-Score/signal-breadth data, with an LLM writing the "why"
in prose grounded in those real numbers — never asked to predict. Matches the exact honesty
framing already applied to CAPE ("macro context, not a trigger"), options-flow sentiment
("a measured fact, not a prediction the move continues"), and every other trend feature in
this app.

There is no existing sub-industry taxonomy fine-grained enough for the specific themes named
when this was asked for (GPU vs. packaging vs. Gold vs. Space vs. Healthcare) — Stock.sector is
GICS-broad ("Semiconductors," "Healthcare"), not narrow enough. _THEMES below is therefore a
hand-curated (theme name -> representative symbols) mapping, not derived from Stock.sector —
the same "no automatic classification exists for this, so it's hand-picked" gap the design doc
itself flags.

All aggregation reads ONLY already-persisted DB rows (Price/Ranking/Signal) — no live yfinance
fetch, matching this app's established rate-limit discipline for anything running on a
scheduled cadence (see check_volume_anomalies()'s own docstring for the same reasoning applied
elsewhere). The only network call anywhere in this module is the one Claude Haiku call per
theme, mirroring event-intelligence's generate_reaction()/generate_earnings_impact() skeleton
exactly (same model, same fail-open contract, same markdown-fence-stripping fix).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import httpx
import structlog
from sqlalchemy import select, func

from db import Price, Ranking, Signal, SignalHorizon, SignalType, Stock, TimeFrame

log = structlog.get_logger()

# Starter theme list — hand-curated per the user's own named themes (semiconductors with its
# GPU/packaging sub-groupings, Gold, Space, Healthcare), plus a few more common, cleanly-
# symbol-mappable themes chosen to make the weekly digest broadly useful rather than a 4-row
# email. Each symbol must already exist in this app's own Stock universe (get_theme_signals()
# silently skips any symbol not found — a theme with zero resolvable symbols is skipped
# entirely, never fabricated from nothing) — no attempt is made to auto-discover new theme
# constituents; editing this list is the only way to change theme coverage.
THEMES: dict[str, list[str]] = {
    "AI / GPU Semiconductors": ["NVDA", "AMD", "AVGO", "TSM"],
    "Semiconductor Packaging & Testing": ["AMKR", "TSM", "MU"],
    "Passive Components (MLCC)": ["MU", "TXN", "ON"],
    "Gold & Precious Metals": ["GLD", "NEM", "GOLD", "AEM"],
    "Space & Satellite": ["RKLB", "LMT", "NOC", "ASTS"],
    "Healthcare & Biotech": ["UNH", "LLY", "JNJ", "ISRG"],
    "AI Infrastructure & Data Centers": ["MSFT", "GOOGL", "META", "AMZN"],
    "Clean Energy": ["ENPH", "FSLR", "TSLA", "NEE"],
}

_REDIS_THEME_LLM_ENABLED = "stockai:admin:feature:theme_forecast_email_enabled"

_SYSTEM = """You are a markets analyst producing a brief weekly theme read for a retail trading
app. You will receive a theme name, its representative stocks, and REAL already-measured data
for this week: average 5-day price return, average K-Score (a proprietary 0-100 composite
quality/momentum score), a breakdown of how many of the theme's stocks have a current BUY vs.
SELL signal, and each stock's own 5-day return/K-Score/signal. Respond ONLY with valid JSON (no
markdown, no explanation outside JSON) in this exact format:
{"summary":"<2-3 sentences>"}
summary must be 2-3 plain-English sentences describing what the numbers you were given show
about this theme's ALREADY-MEASURED momentum this week. Do NOT predict what will happen next
week or in "the coming weeks" — describe only what the provided numbers already show as of
today. If the data is mixed or inconclusive, say so plainly rather than manufacturing a
narrative. Never invent a number not provided to you."""


@dataclass
class ThemeSignalResult:
    theme: str
    symbol_count: int
    avg_return_5d_pct: float | None
    avg_kscore: float | None
    buy_signal_count: int
    sell_signal_count: int
    top_symbols: list[dict] = field(default_factory=list)


def _api_key() -> str:
    """Matches every other Claude call site in this codebase (macro_reaction.py, earnings.py,
    decision-engine's llm_scorer.py/risk_agent.py, market-data's own news.py, research-engine) —
    delegates to common.ai_keys.get_admin_ai_key() rather than reading a phantom env var."""
    from common.ai_keys import get_admin_ai_key
    return get_admin_ai_key("claude")


def _clean_summary(raw: object) -> str | None:
    """Same defensive contract as macro_reaction.py's/earnings.py's own _clean_sector_list() —
    a malformed LLM response degrades to None (no summary) rather than raising, so a bad parse
    never takes down the real, already-computed numeric fields it would have been paired with."""
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned[:500] or None


def compute_theme_signal(session, theme: str, symbols: list[str]) -> ThemeSignalResult | None:
    """Pure aggregation over already-persisted Price/Ranking/Signal rows for one theme's
    symbol list — no live network call. Returns None if none of the theme's symbols resolve to
    a real Stock row in this app's universe (never fabricates a result from zero real data).

    5-day return: latest daily close vs. the close 5 trading-bar-days earlier, per symbol
    (Price, timeframe=D1), averaged across symbols with enough history to compute it.
    K-Score: most recent Ranking.score per symbol.
    Signal breadth: most recent SWING-horizon Signal.signal per symbol (SWING chosen as the
    app's own "the current signal" horizon for a theme-level weekly read, matching how
    watchlist auto-rotation and several other cross-symbol summaries in this app default to
    SWING when a single representative horizon is needed).
    """
    stocks = session.execute(
        select(Stock).where(
            Stock.symbol.in_(symbols),
            Stock.active.is_(True),
            Stock.delisted.is_(False),
        )
    ).scalars().all()
    if not stocks:
        return None

    stock_ids = [s.id for s in stocks]
    id_to_symbol = {s.id: s.symbol for s in stocks}

    # Latest Ranking.score per stock
    latest_rank_subq = (
        select(Ranking.stock_id, func.max(Ranking.as_of).label("max_as_of"))
        .where(Ranking.stock_id.in_(stock_ids))
        .group_by(Ranking.stock_id)
        .subquery()
    )
    rank_rows = session.execute(
        select(Ranking.stock_id, Ranking.score)
        .join(latest_rank_subq,
              (Ranking.stock_id == latest_rank_subq.c.stock_id) &
              (Ranking.as_of == latest_rank_subq.c.max_as_of))
    ).all()
    kscore_by_stock = {r.stock_id: float(r.score) for r in rank_rows}

    # Latest SWING Signal.signal per stock
    latest_sig_subq = (
        select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
        .where(Signal.stock_id.in_(stock_ids), Signal.horizon == SignalHorizon.SWING)
        .group_by(Signal.stock_id)
        .subquery()
    )
    sig_rows = session.execute(
        select(Signal.stock_id, Signal.signal)
        .join(latest_sig_subq,
              (Signal.stock_id == latest_sig_subq.c.stock_id) &
              (Signal.ts == latest_sig_subq.c.max_ts) &
              (Signal.horizon == SignalHorizon.SWING))
    ).all()
    signal_by_stock = {r.stock_id: r.signal for r in sig_rows}

    # 5-day return: for each stock, take the last 6 daily closes (today's + 5 trailing) and
    # compare the newest to the oldest of that window — a fixed bar-count lookback, not a
    # calendar-day one, so weekends/holidays don't distort "5 trading days."
    return_by_stock: dict[int, float] = {}
    for sid in stock_ids:
        closes = session.execute(
            select(Price.close)
            .where(Price.stock_id == sid, Price.timeframe == TimeFrame.D1)
            .order_by(Price.ts.desc())
            .limit(6)
        ).scalars().all()
        if len(closes) >= 2:
            newest = float(closes[0])
            oldest = float(closes[-1])
            if oldest > 0:
                return_by_stock[sid] = (newest / oldest - 1) * 100

    returns = list(return_by_stock.values())
    kscores = list(kscore_by_stock.values())
    buy_count = sum(1 for v in signal_by_stock.values() if v == SignalType.BUY)
    sell_count = sum(1 for v in signal_by_stock.values() if v == SignalType.SELL)

    top_symbols = [
        {
            "symbol": id_to_symbol[sid],
            "return_5d_pct": round(return_by_stock.get(sid), 2) if sid in return_by_stock else None,
            "kscore": round(kscore_by_stock.get(sid), 1) if sid in kscore_by_stock else None,
            "signal": signal_by_stock.get(sid).value if sid in signal_by_stock else None,
        }
        for sid in stock_ids
    ]
    # Sort by return descending (None sorts last), matching the top-mover convention used
    # elsewhere in this app's digests (send_morning_digest's top5, etc.)
    top_symbols.sort(key=lambda d: d["return_5d_pct"] if d["return_5d_pct"] is not None else float("-inf"), reverse=True)

    return ThemeSignalResult(
        theme=theme,
        symbol_count=len(stocks),
        avg_return_5d_pct=round(sum(returns) / len(returns), 2) if returns else None,
        avg_kscore=round(sum(kscores) / len(kscores), 1) if kscores else None,
        buy_signal_count=buy_count,
        sell_signal_count=sell_count,
        top_symbols=top_symbols,
    )


async def generate_theme_summary(result: ThemeSignalResult) -> str | None:
    """Calls Claude for a prose summary grounded in already-measured numbers. Fail-open:
    returns None on any error — the caller stores None as "no summary available" and the email
    builder falls back to a plain numeric line, matching every other LLM call site in this
    codebase's fail-open discipline (advisory prose, never gate-blocking)."""
    api_key = _api_key()
    if not api_key:
        log.info("theme_signals.no_api_key", theme=result.theme)
        return None

    top_lines = "\n".join(
        f"  {s['symbol']}: 5d return {s['return_5d_pct']}%, K-Score {s['kscore']}, signal {s['signal']}"
        for s in result.top_symbols
    )
    prompt = (
        f"Theme: {result.theme}\n"
        f"Stocks tracked: {result.symbol_count}\n"
        f"Average 5-day return: {result.avg_return_5d_pct if result.avg_return_5d_pct is not None else 'unavailable'}%\n"
        f"Average K-Score: {result.avg_kscore if result.avg_kscore is not None else 'unavailable'}\n"
        f"BUY signals: {result.buy_signal_count}, SELL signals: {result.sell_signal_count}\n"
        f"Per-stock detail:\n{top_lines}\n"
    )
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 250,
        "temperature": 0.2,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        if r.status_code != 200:
            log.warning("theme_signals.api_error", theme=result.theme, status=r.status_code, body=r.text[:200])
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        data = json.loads(raw)
        return _clean_summary(data.get("summary"))
    except Exception as exc:
        log.warning("theme_signals.call_failed", theme=result.theme, error=str(exc))
        return None
