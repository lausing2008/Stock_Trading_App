"""HKEX Stock Connect southbound flow data ingest — T209.

Fetches daily southbound (Mainland→HK) net buy/sell data for HK stocks.
Source: HKEX public data API (no authentication required).

HKEX API notes
──────────────
HKEX provides a public endpoint that returns buy/sell turnover per HK stock
via the Stock Connect scheme (mainland investors buying HK shares):

  GET https://www.hkex.com.hk/eng/csm/ws/stock-connect-details.asmx/
        GetBuySellTurnOverDetails?MarketID=01&StockCode={CODE}&mktType=0&LangCode=en

Where CODE is the 4-digit HK stock code (e.g. "0700" for Tencent).
The endpoint is rate-limit-sensitive; requests are spaced 200ms apart.

If the HKEX API returns no data (e.g. non-trading day, API change, or
stock not in Stock Connect scheme), a zero-flow row is NOT written —
the symbol is simply skipped and logged at DEBUG level.
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import httpx
from common.logging import get_logger

log = get_logger("hk_connect")

_HKEX_BASE = "https://www.hkex.com.hk"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockAI/1.0)",
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.hkex.com.hk/",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _symbol_to_hk_code(symbol: str) -> str | None:
    """Convert '0700.HK' → '0700', '9988.HK' → '9988'.

    Returns None if the symbol is not a HK-listed stock.
    """
    if not symbol.upper().endswith(".HK"):
        return None
    code = symbol.upper().replace(".HK", "")
    return code.zfill(4)


def _parse_hkd(val) -> float | None:
    """Parse an HKD turnover value from HKEX JSON — handles commas and None."""
    if val is None:
        return None
    try:
        return float(str(val).replace(",", ""))
    except (ValueError, TypeError):
        return None


# ── HKEX API fetch ────────────────────────────────────────────────────────────

def _fetch_southbound_stock(hk_code: str) -> dict | None:
    """Fetch today's southbound flow for a single HK stock code.

    Returns a dict with keys buy_hkd, sell_hkd, net_buy_hkd, or None on failure.
    """
    url = (
        f"{_HKEX_BASE}/eng/csm/ws/stock-connect-details.asmx"
        f"/GetBuySellTurnOverDetails"
        f"?MarketID=01&StockCode={hk_code}&mktType=0&LangCode=en"
    )
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=10, follow_redirects=True)
        if resp.status_code != 200:
            log.debug("hk_connect.http_error", code=hk_code, status=resp.status_code)
            return None

        # Response may be JSON with {"d": "{...}"} or {"d": {...}}
        try:
            outer = resp.json()
        except Exception:
            log.debug("hk_connect.json_parse_failed", code=hk_code)
            return None

        inner = outer.get("d") if isinstance(outer, dict) else outer
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except Exception:
                return None

        if not isinstance(inner, dict):
            return None

        # Try multiple key casing variants HKEX has used over time
        buy = _parse_hkd(
            inner.get("BuyTurnover") or inner.get("buy_turnover") or
            inner.get("buyTurnover") or inner.get("Buy")
        )
        sell = _parse_hkd(
            inner.get("SellTurnover") or inner.get("sell_turnover") or
            inner.get("sellTurnover") or inner.get("Sell")
        )

        if buy is None and sell is None:
            log.debug("hk_connect.no_turnover_fields", code=hk_code, keys=list(inner.keys()))
            return None

        return {
            "buy_hkd":     buy,
            "sell_hkd":    sell,
            "net_buy_hkd": (buy or 0.0) - (sell or 0.0),
        }

    except Exception as exc:
        log.debug("hk_connect.fetch_exception", code=hk_code, exc=str(exc))
        return None


# ── Public ingest function ────────────────────────────────────────────────────

def ingest_southbound_flows(db, hk_symbols: list[str]) -> dict:
    """Fetch and store southbound flows for the given HK symbols.

    One DB upsert per symbol — ON CONFLICT updates in-place so re-runs on the
    same day are idempotent.  Uses CAST() not ::type casts to avoid the
    SQLAlchemy named-param :: ambiguity (see CLAUDE.md BUG-6).

    Returns {"processed": int, "stored": int, "failed": int}
    """
    from common.logging import configure_logging
    configure_logging()
    from sqlalchemy import text

    today = date.today()
    processed = stored = failed = 0

    for symbol in hk_symbols:
        hk_code = _symbol_to_hk_code(symbol)
        if not hk_code:
            continue
        processed += 1

        flow = _fetch_southbound_stock(hk_code)
        if flow is None:
            failed += 1
            log.debug("hk_connect.no_data", symbol=symbol, hk_code=hk_code)
            continue

        try:
            db.execute(text("""
                INSERT INTO hk_connect_flows
                    (symbol, trade_date, net_buy_hkd, buy_hkd, sell_hkd)
                VALUES
                    (:sym, :td, :net, :buy, :sell)
                ON CONFLICT (symbol, trade_date) DO UPDATE SET
                    net_buy_hkd = EXCLUDED.net_buy_hkd,
                    buy_hkd     = EXCLUDED.buy_hkd,
                    sell_hkd    = EXCLUDED.sell_hkd
            """), {
                "sym": symbol,
                "td":  today,
                "net": flow["net_buy_hkd"],
                "buy": flow["buy_hkd"],
                "sell": flow["sell_hkd"],
            })
            db.commit()
            stored += 1
            log.debug("hk_connect.stored", symbol=symbol, net_buy_hkd=flow["net_buy_hkd"])
        except Exception as exc:
            log.warning("hk_connect.insert_failed", symbol=symbol, exc=str(exc))
            try:
                db.rollback()
            except Exception:
                pass
            failed += 1

        time.sleep(0.2)  # polite rate-limiting — ~5 req/s max to HKEX

    log.info(
        "hk_connect.ingest_complete",
        processed=processed, stored=stored, failed=failed,
    )
    return {"processed": processed, "stored": stored, "failed": failed}


# ── Flow summary for signal-engine consumption ────────────────────────────────

def get_flow_summary(db, symbol: str, days: int = 20) -> dict:
    """Return a rolling flow summary for a single HK symbol.

    Used by the market-data /stocks/hk-connect-flow/{symbol} endpoint which
    signal-engine calls to enrich the reasons dict on HK BUY signals.

    Return keys:
      flow_5d_net_hkd  — 5-day net buy sum, HKD millions (positive = net buy)
      flow_20d_net_hkd — 20-day net buy sum, HKD millions
      flow_strength    — (avg 5-day daily flow) / (avg 20-day daily flow); >1 = accelerating
    Returns {} when no data is available.
    """
    from sqlalchemy import text

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    try:
        rows = db.execute(text("""
            SELECT trade_date, net_buy_hkd
            FROM hk_connect_flows
            WHERE symbol = :sym AND trade_date >= :cutoff
            ORDER BY trade_date DESC
            LIMIT :days
        """), {"sym": symbol, "cutoff": cutoff, "days": days}).fetchall()
    except Exception as exc:
        log.warning("hk_connect.summary_query_failed", symbol=symbol, exc=str(exc))
        return {}

    if not rows:
        return {}

    nets = [r.net_buy_hkd for r in rows if r.net_buy_hkd is not None]
    if not nets:
        return {}

    n5  = min(5,  len(nets))
    n20 = min(20, len(nets))
    flow_5d  = sum(nets[:n5])
    flow_20d = sum(nets[:n20])

    avg_5d  = flow_5d  / n5  if n5  > 0 else 0.0
    avg_20d = flow_20d / n20 if n20 > 0 else 0.0
    flow_strength = (avg_5d / avg_20d) if avg_20d != 0 else 0.0

    return {
        "flow_5d_net_hkd":  round(flow_5d  / 1e6, 2),   # HKD millions
        "flow_20d_net_hkd": round(flow_20d / 1e6, 2),
        "flow_strength":    round(flow_strength, 3),
    }
