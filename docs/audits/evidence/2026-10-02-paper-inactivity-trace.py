"""READ-ONLY trace for recommendations 2, 3 and 5 of the paper-inactivity follow-up.

SELECT only. No entries, no config changes, no marker resets, no writes of any kind.
"""
import json, sys
sys.path.insert(0, "/app"); sys.path.insert(0, "/app/shared")
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from db import SessionLocal

OUT = {}
with SessionLocal() as s:
    s.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))

    # ── R2: HK candidate supply, especially portfolio 9 ────────────────────────────────
    OUT["portfolios"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT id, name, is_active,
               config->>'market'        AS market,
               config->>'trading_style' AS style,
               config->>'enabled'       AS enabled,
               config->>'paused'        AS paused
        FROM paper_portfolios ORDER BY id
    """))]

    # Fresh BUY signals by market/horizon — the raw supply before any portfolio filter.
    OUT["signal_supply"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT st.market, sg.horizon, sg.signal, COUNT(*) AS n,
               MAX(sg.ts) AS newest
        FROM signals sg JOIN stocks st ON st.id = sg.stock_id
        WHERE sg.ts > NOW() - INTERVAL '3 days'
        GROUP BY st.market, sg.horizon, sg.signal
        ORDER BY st.market, sg.horizon, sg.signal
    """))]

    # Tradeable HK universe: how many HK stocks are even eligible to produce a candidate.
    OUT["hk_universe"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT COUNT(*) FILTER (WHERE NOT COALESCE(delisted, false)) AS live_hk_stocks,
               COUNT(*) AS all_hk_stocks
        FROM stocks WHERE market = 'HK'
    """))]

    # HK BUY signals in the last 3 days, by horizon — the supply portfolio 9 would draw on.
    OUT["hk_buy_signals"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT sg.horizon, COUNT(*) AS buys, COUNT(DISTINCT sg.stock_id) AS symbols,
               MAX(sg.ts) AS newest
        FROM signals sg JOIN stocks st ON st.id = sg.stock_id
        WHERE st.market = 'HK' AND sg.signal = 'BUY' AND sg.ts > NOW() - INTERVAL '3 days'
        GROUP BY sg.horizon ORDER BY sg.horizon
    """))]

    # ── R3 + R4 evidence: the scan log, per portfolio ──────────────────────────────────
    OUT["scan_log_latest"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT DISTINCT ON (portfolio_id)
               portfolio_id, scanned_at, portfolio_gate, portfolio_gate_reason,
               candidates_seen, skip_tally
        FROM paper_entry_scan_logs
        ORDER BY portfolio_id, scanned_at DESC
    """))]
    OUT["scan_log_window"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT portfolio_id,
               COUNT(*) AS scans,
               COUNT(*) FILTER (WHERE portfolio_gate IS NOT NULL) AS blocked_scans,
               COUNT(*) FILTER (WHERE portfolio_gate IS NULL AND COALESCE(candidates_seen,0) = 0) AS zero_candidate_scans,
               COUNT(*) FILTER (WHERE COALESCE(candidates_seen,0) > 0) AS scans_with_candidates,
               MIN(scanned_at) AS oldest, MAX(scanned_at) AS newest
        FROM paper_entry_scan_logs
        WHERE scanned_at > NOW() - INTERVAL '7 days'
        GROUP BY portfolio_id ORDER BY portfolio_id
    """))]

    # ── R5: entries actually recorded, so a UI claim can be checked against the DB ─────
    OUT["entries_since_sep8"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT portfolio_id, COUNT(*) AS entries,
               MIN(entry_time) AS first_entry, MAX(entry_time) AS last_entry
        FROM paper_trades WHERE entry_time >= '2026-09-08'
        GROUP BY portfolio_id ORDER BY portfolio_id
    """))]
    OUT["last_entry_any"] = [dict(r._mapping) for r in s.execute(text("""
        SELECT portfolio_id, MAX(entry_time) AS last_entry, COUNT(*) AS total_trades
        FROM paper_trades GROUP BY portfolio_id ORDER BY portfolio_id
    """))]

print(json.dumps(OUT, indent=2, default=str))
