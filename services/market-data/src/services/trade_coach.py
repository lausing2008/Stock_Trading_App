"""T286-TRADE-PATTERN-COACH: aggregate, cross-trade "what patterns show up in how this
account trades" digest — extends T258-TRADE-POSTMORTEM's per-trade mechanical review into a
rolling-window aggregate across ALL closed trades, with an LLM writing the "what this means"
prose grounded strictly in the real, already-computed numbers.

Matches theme_signals.py's own established honesty discipline exactly (same fail-open Claude
Haiku call, same markdown-fence-stripping fix, same "describe only what the numbers already
show, never predict/prescribe" system prompt): this reports MEASURED behavioral patterns
(e.g. "winning trades that hit target_reached exited a median of 4.2% below their own
highest_price during the hold" — a real, computable fact from PaperTrade.highest_price vs.
exit_price) — it never tells the user what to do differently, since that would be unvalidated
trading advice this app has no track record backing.

All aggregation reads ONLY already-persisted PaperTrade rows (closed, across all portfolios)
— no live yfinance/network call anywhere except the one Claude Haiku call, matching this
app's established rate-limit discipline.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from sqlalchemy import select

from db import PaperTrade

log = structlog.get_logger()

_MECHANICAL_EXIT_REASONS = {"stop_hit", "trailing_stop", "breakeven_stop", "target_reached", "time_stop"}
_MIN_TRADES_FOR_PATTERNS = 10  # a coaching read needs a real sample, not 2-3 trades

_SYSTEM = """You are a trading-behavior analyst producing a brief weekly coaching read for a
paper-trading account. You will receive REAL, already-computed aggregate statistics over the
account's own closed trades in the last 90 days: win rate and average return by exit reason,
average how-far-below-peak-price each winning trade exited, average hold days vs. each style's
own expected hold window, and the most common exit reasons. Respond ONLY with valid JSON (no
markdown, no explanation outside JSON) in this exact format:
{"summary":"<2-4 sentences>"}
summary must describe ONLY what the provided numbers ALREADY show about this account's own
trading behavior over this window — e.g. whether winners are commonly exited well below their
peak price (giving back gains), whether stop-outs cluster on trades held far longer than the
style's own expected window, or whether one exit reason dominates the loss total. Do NOT give
generic trading advice, do NOT predict future performance, and do NOT invent a pattern the
numbers don't actually support. If the data is too thin or shows no clear pattern, say so
plainly rather than manufacturing one. Never invent a number not provided to you."""


@dataclass
class TradePatternResult:
    n_trades: int
    window_days: int
    win_rate: float | None
    avg_return_pct: float | None
    by_exit_reason: list[dict] = field(default_factory=list)
    avg_giveback_pct_on_winners: float | None = None  # avg (peak - exit) / peak on winning trades
    avg_hold_days_vs_expected: float | None = None    # avg (actual_hold_days - style's own expected)
    worst_exit_reason: dict | None = None             # exit_reason with the most negative total pnl


def _api_key() -> str:
    """Matches every other Claude call site in this codebase — delegates to
    common.ai_keys.get_admin_ai_key() rather than reading a phantom env var."""
    from common.ai_keys import get_admin_ai_key
    return get_admin_ai_key("claude")


def _clean_summary(raw: object) -> str | None:
    """Same defensive contract as theme_signals.py's own _clean_summary() — a malformed LLM
    response degrades to None rather than raising, so a bad parse never takes down the real,
    already-computed numeric fields it would have been paired with."""
    if not isinstance(raw, str):
        return None
    cleaned = raw.strip()
    return cleaned[:600] or None


def compute_trade_patterns(session, window_days: int = 90) -> TradePatternResult | None:
    """Pure aggregation over already-persisted, closed PaperTrade rows across ALL portfolios
    in the last window_days — no live network call. Returns None below _MIN_TRADES_FOR_PATTERNS
    (never fabricates a "pattern" from a handful of trades).
    """
    from .paper_trading_engine import _STYLE_OVERRIDES

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    trades = session.execute(
        select(PaperTrade).where(
            PaperTrade.stage == "closed",
            PaperTrade.exit_time.isnot(None),
            PaperTrade.exit_time >= cutoff,
        )
    ).scalars().all()

    if len(trades) < _MIN_TRADES_FOR_PATTERNS:
        return None

    n_trades = len(trades)
    returns = [float(t.pct_return) for t in trades if t.pct_return is not None]
    wins = [r for r in returns if r > 0]
    win_rate = round(len(wins) / len(returns), 3) if returns else None
    avg_return_pct = round(sum(returns) / len(returns) * 100, 2) if returns else None

    # By exit reason: count, win rate, avg return, total pnl.
    by_reason: dict[str, dict] = {}
    for t in trades:
        reason = t.exit_reason or "unknown"
        bucket = by_reason.setdefault(reason, {"count": 0, "wins": 0, "returns": [], "total_pnl": 0.0})
        bucket["count"] += 1
        if t.pct_return is not None:
            bucket["returns"].append(float(t.pct_return))
            if t.pct_return > 0:
                bucket["wins"] += 1
        if t.pnl is not None:
            bucket["total_pnl"] += float(t.pnl)

    by_exit_reason = [
        {
            "exit_reason": reason,
            "count": b["count"],
            "win_rate": round(b["wins"] / len(b["returns"]), 3) if b["returns"] else None,
            "avg_return_pct": round(sum(b["returns"]) / len(b["returns"]) * 100, 2) if b["returns"] else None,
            "total_pnl": round(b["total_pnl"], 2),
        }
        for reason, b in by_reason.items()
    ]
    by_exit_reason.sort(key=lambda d: d["count"], reverse=True)

    worst_exit_reason = min(by_exit_reason, key=lambda d: d["total_pnl"]) if by_exit_reason else None

    # Giveback on winners: how far below its own peak (highest_price, tracked live during the
    # hold — never re-derived from a second Price range query) a WINNING trade exited. A
    # mechanical exit (stop_hit/trailing_stop/target_reached/etc.) that still gave back a large
    # chunk of its own peak is the concrete, measurable version of "exits winners too early"/
    # "gives back gains" — never asserted as a claim, only reported as a real percentage.
    givebacks: list[float] = []
    for t in trades:
        if t.pct_return is None or t.pct_return <= 0:
            continue
        if not t.highest_price or not t.exit_price or float(t.highest_price) <= 0:
            continue
        peak = float(t.highest_price)
        exitp = float(t.exit_price)
        if peak > exitp:  # only a real giveback, never a negative "gained past peak" artifact
            givebacks.append((peak - exitp) / peak * 100)
    avg_giveback_pct_on_winners = round(sum(givebacks) / len(givebacks), 2) if givebacks else None

    # Hold-days-vs-expected: reuses _STYLE_OVERRIDES' own max_hold_days per style — the SAME
    # value T258-TRADE-POSTMORTEM's own per-trade hold_days_vs_expected field already uses,
    # never a second, independently re-derived expectation that could drift from it.
    hold_deltas: list[float] = []
    for t in trades:
        if t.hold_days is None:
            continue
        expected = _STYLE_OVERRIDES.get(t.trading_style, {}).get("max_hold_days", 60)
        hold_deltas.append(t.hold_days - expected)
    avg_hold_days_vs_expected = round(sum(hold_deltas) / len(hold_deltas), 1) if hold_deltas else None

    return TradePatternResult(
        n_trades=n_trades,
        window_days=window_days,
        win_rate=win_rate,
        avg_return_pct=avg_return_pct,
        by_exit_reason=by_exit_reason,
        avg_giveback_pct_on_winners=avg_giveback_pct_on_winners,
        avg_hold_days_vs_expected=avg_hold_days_vs_expected,
        worst_exit_reason=worst_exit_reason,
    )


async def generate_trade_coach_summary(result: TradePatternResult) -> str | None:
    """Calls Claude for a prose summary grounded in already-measured numbers. Fail-open:
    returns None on any error — the caller stores None as "no summary available" and the email
    builder falls back to the plain numeric breakdown, matching theme_signals.py's own
    generate_theme_summary() fail-open discipline (advisory prose, never gate-blocking)."""
    api_key = _api_key()
    if not api_key:
        log.info("trade_coach.no_api_key")
        return None

    def _reason_line(r: dict) -> str:
        if r["win_rate"] is not None:
            return (
                f"  {r['exit_reason']}: {r['count']} trades, win rate {r['win_rate']*100:.0f}%, "
                f"avg return {r['avg_return_pct']}%, total pnl ${r['total_pnl']}"
            )
        return f"  {r['exit_reason']}: {r['count']} trades, total pnl ${r['total_pnl']}"

    reason_lines = "\n".join(_reason_line(r) for r in result.by_exit_reason)

    win_rate_line = (
        f"Overall win rate: {result.win_rate*100:.0f}%\n" if result.win_rate is not None else ""
    )
    prompt = (
        f"Window: last {result.window_days} days, {result.n_trades} closed trades\n"
        f"{win_rate_line}"
        f"Overall average return: {result.avg_return_pct}%\n"
        f"By exit reason:\n{reason_lines}\n"
        f"Average giveback on winning trades (peak price vs. actual exit price): "
        f"{result.avg_giveback_pct_on_winners if result.avg_giveback_pct_on_winners is not None else 'unmeasurable'}%\n"
        f"Average hold days vs. each style's own expected hold window: "
        f"{result.avg_hold_days_vs_expected if result.avg_hold_days_vs_expected is not None else 'unmeasurable'} days\n"
    )
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
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
            log.warning("trade_coach.api_error", status=r.status_code, body=r.text[:200])
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        data = json.loads(raw)
        return _clean_summary(data.get("summary"))
    except Exception as exc:
        log.warning("trade_coach.call_failed", error=str(exc))
        return None
