"""HKEX Stock Connect southbound flow data ingest — T209.

Fetches daily southbound (Mainland→HK) net buy/sell data for HK stocks.
Source: Eastmoney data center (public JSON API, no authentication required).

MD-HKCONNECT2 (2026-07-13): replaced the dead HKEX endpoint (see "MD-HKCONNECT1, historical"
below) with Eastmoney's Stock Connect holdings-ranking report
(reportName=RPT_MUTUAL_STOCK_HOLDRANKS), the same report backing the `akshare` Python library's
stock_hsgt_individual_em() function — called directly via httpx rather than adding the akshare
dependency (which pulls in py-mini-racer/a bundled V8 engine, akracer, lxml, etc. for what is,
under the hood, a single plain `requests.get()` call with fixed query params; confirmed by
reading akshare's own source).

This report is NOT the same disclosure that was killed by mainland exchanges' 2024-08-19 Stock
Connect information-disclosure change (that killed the daily per-stock TRANSACTION net-buy/sell
figure). This is a HOLDINGS report — cumulative shares/market-value currently held via Southbound
Connect for a given stock, snapshotted daily, which mainland exchanges still publish. Its
HOLD_SHARES_CHANGE / ADD_MARKET_CAP fields are exactly the day-over-day change in that holding —
a legitimate proxy for net flow direction (net accumulation = net buying, net distribution = net
selling), even though it is derived from a holdings snapshot rather than reconstructed from raw
buy/sell tickets. Verified live (2026-07-13) against 2 real symbols (00700.HK Tencent, 09988.HK
Alibaba): genuinely day-to-day changing HOLD_SHARES values through the current date, confirming
this is live data, not a frozen/cached snapshot.

If the Eastmoney API returns no data for a symbol (e.g. not in the Stock Connect southbound
scheme, a non-trading day, or a transient API issue), a zero-flow row is NOT written — the
symbol is simply skipped and logged at DEBUG level, same behavior as before.

MD-HKCONNECT1 (2026-07-09, historical — superseded by the above): the HKEX endpoint below was
CONFIRMED DEAD — it returned HTTP 302 redirecting to an ASP.NET error path (`?aspxerrorpath=...`)
instead of JSON, on every request. HKEX retired this legacy `.asmx` web service with no public
per-stock JSON/CSV replacement of its own (their current Stock Connect Statistics pages only
publish aggregate market-wide turnover, not per-stock). Confirmed via production logs (100%
failure, 3+ consecutive days, ongoing through 2026-07-13) and the DB (hk_connect_flows had 0 rows
total). At the time, the best free per-stock alternative found (Eastmoney via akshare) was
believed too fragile to rush — a follow-up investigation (2026-07-13) confirmed the underlying
Eastmoney API works live and is a plain, callable JSON endpoint (not fragile HTML scraping as
originally assumed), which is what's now wired in above.

  GET https://www.hkex.com.hk/eng/csm/ws/stock-connect-details.asmx/
        GetBuySellTurnOverDetails?MarketID=01&StockCode={CODE}&mktType=0&LangCode=en
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import httpx
from common.logging import get_logger

log = get_logger("hk_connect")

_EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StockAI/1.0)",
    "Accept": "application/json",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _symbol_to_eastmoney_code(symbol: str) -> str | None:
    """Convert '0700.HK' → '00700.HK', '9988.HK' → '09988.HK'.

    Eastmoney's SECUCODE filter requires the 5-digit zero-padded HK code (confirmed live —
    the 4-digit HKEX convention this module previously used returns 0 rows against this API).
    Returns None if the symbol is not a HK-listed stock.
    """
    if not symbol.upper().endswith(".HK"):
        return None
    code = symbol.upper().replace(".HK", "")
    return f"{code.zfill(5)}.HK"


# ── Eastmoney API fetch ────────────────────────────────────────────────────────

def _fetch_southbound_stock(eastmoney_code: str) -> dict | None:
    """Fetch the most recent southbound holdings-change snapshot for a single HK stock.

    Returns a dict with keys buy_hkd (None — not separable from this report), sell_hkd (None),
    net_buy_hkd (HOLD_SHARES_CHANGE priced at the day's close, i.e. ADD_MARKET_CAP), or None on
    failure/no data.
    """
    params = {
        "sortColumns": "TRADE_DATE",
        "sortTypes": "-1",
        "pageSize": "1",
        "pageNumber": "1",
        "reportName": "RPT_MUTUAL_STOCK_HOLDRANKS",
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "filter": f'(SECUCODE="{eastmoney_code}")(MUTUAL_TYPE="002")',
    }
    try:
        resp = httpx.get(_EASTMONEY_URL, params=params, headers=_HEADERS, timeout=10)
        if resp.status_code != 200:
            log.debug("hk_connect.http_error", code=eastmoney_code, status=resp.status_code)
            return None

        try:
            outer = resp.json()
        except Exception:
            log.debug("hk_connect.json_parse_failed", code=eastmoney_code)
            return None

        result = outer.get("result") if isinstance(outer, dict) else None
        rows = (result or {}).get("data") or []
        if not rows:
            log.debug("hk_connect.no_data", code=eastmoney_code)
            return None

        row = rows[0]
        add_market_cap = row.get("ADD_MARKET_CAP")
        net_buy_hkd = float(add_market_cap) if add_market_cap is not None else None
        if net_buy_hkd is None:
            log.debug("hk_connect.no_flow_field", code=eastmoney_code, keys=list(row.keys()))
            return None

        # DQ-EARNINGS-FETCHED-AT-FROZEN-class lesson applied here: use the report's OWN
        # TRADE_DATE, not the sync job's run date — this report is a daily snapshot from
        # mainland exchanges, which may not have a same-day row yet (weekend, holiday, or
        # publication lag), and storing it under "today" when it's actually stale data would
        # make hk_connect_flows look fresher than it really is.
        trade_date_str = str(row.get("TRADE_DATE") or "")[:10]
        row_trade_date = date.fromisoformat(trade_date_str) if trade_date_str else None
        if row_trade_date is None:
            log.debug("hk_connect.no_trade_date_field", code=eastmoney_code)
            return None

        return {
            "buy_hkd":     None,  # this report gives net holding change, not gross buy/sell split
            "sell_hkd":    None,
            "net_buy_hkd": net_buy_hkd,
            "trade_date":  row_trade_date,
        }

    except Exception as exc:
        log.debug("hk_connect.fetch_exception", code=eastmoney_code, exc=str(exc))
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
    import structlog as _sl
    _log = _sl.get_logger()  # fresh proxy bound after configure_logging() — immune to stale cache
    from sqlalchemy import text

    processed = stored = failed = 0

    for symbol in hk_symbols:
        eastmoney_code = _symbol_to_eastmoney_code(symbol)
        if not eastmoney_code:
            continue
        processed += 1

        flow = _fetch_southbound_stock(eastmoney_code)
        if flow is None:
            failed += 1
            _log.debug("hk_connect.no_data", symbol=symbol, eastmoney_code=eastmoney_code)
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
                "td":  flow["trade_date"],
                "net": flow["net_buy_hkd"],
                "buy": flow["buy_hkd"],
                "sell": flow["sell_hkd"],
            })
            db.commit()
            stored += 1
            _log.debug("hk_connect.stored", symbol=symbol, net_buy_hkd=flow["net_buy_hkd"], trade_date=str(flow["trade_date"]))
        except Exception as exc:
            _log.warning("hk_connect.insert_failed", symbol=symbol, exc=str(exc))
            try:
                db.rollback()
            except Exception:
                pass
            failed += 1

        time.sleep(0.2)  # polite rate-limiting to Eastmoney

    # MD-HKCONNECT1 (2026-07-09): a full-batch failure (every symbol failed) previously logged
    # at the same info level as a normal, mostly-successful run — indistinguishable in
    # production logs without counting failed/processed by hand. Kept the same
    # promote-to-error-on-full-failure discipline after the MD-HKCONNECT2 (2026-07-13) source
    # replacement — this stays valuable regardless of which upstream source is behind it, since
    # the paper-trading HK mainland-flow gate (hk_flow_gate in paper_trading_engine.py, T224-A)
    # depends on flow_5d_net_hkd being populated, and a silent full-batch failure would return
    # this gate to permanently fail-open with no visibility, same risk as before.
    if processed > 0 and failed == processed:
        _log.error(
            "hk_connect.ingest_all_failed",
            processed=processed, failed=failed,
            note="every symbol failed — Eastmoney source likely down; hk_flow_gate is fail-open while this persists",
        )
    elif failed > 0:
        _log.warning(
            "hk_connect.ingest_partial_failure",
            processed=processed, stored=stored, failed=failed,
        )
    else:
        _log.info(
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
