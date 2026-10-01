"""M15 — portfolio concentration limits, exercised with SYNTHETIC positions.

WHY SYNTHETIC. The register's own note is "test limits now with synthetic positions; do not wait
for trades to test a hard limit." Waiting for live exposure to accumulate means the first time a
hard limit is exercised is in production, on real positions — and a limit that has never bound is
a limit nobody has evidence works.

This calls the REAL `_open_paper_trade`, the same function organic entries and conditional
orders both route through, against a real database with hand-built open positions. It does not
reimplement the cap arithmetic; reimplementing the logic under test is how a test ends up
asserting its own copy of a bug.

OUTPUT IS A MEASUREMENT, not a pass/fail. Several dimensions below are expected to reveal gaps;
those are findings to report, not failures to paper over.
"""
import json
import pathlib
import sys
import types
from datetime import datetime, timezone
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "market-data"))

for m in ["redis", "httpx", "structlog", "apscheduler", "apscheduler.schedulers",
          "apscheduler.schedulers.background", "apscheduler.triggers",
          "apscheduler.triggers.cron", "yfinance"]:
    sys.modules.setdefault(m, MagicMock())

import common                                                        # noqa: E402
_cfg_mod = types.ModuleType("common.config")
# A lazily-constructed PostgreSQL URL: `create_engine` does not connect, and SQLite rejects the
# pool arguments session.py passes. The engine built at import is never used — the probe binds
# its own SQLite engine below.
_cfg_mod.get_settings = lambda: types.SimpleNamespace(
    database_url="postgresql+psycopg2://u:p@127.0.0.1:1/none", admin_password=None,
    jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg_mod
common.config = _cfg_mod

from sqlalchemy import create_engine                                  # noqa: E402
from sqlalchemy.orm import sessionmaker                               # noqa: E402
from db.models import Base, PaperPortfolio, PaperTrade, Signal, Stock   # noqa: E402
from datetime import date as _date                                    # noqa: E402
from src.services import paper_trading_engine as pte                  # noqa: E402

import tempfile                                                       # noqa: E402
_DB = pathlib.Path(tempfile.mkdtemp()) / "conc.db"
engine = create_engine(f"sqlite:///{_DB}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

R = {}
EQUITY = 100_000.0

_src = (ROOT / "services" / "market-data" / "src" / "services"
        / "paper_trading_engine.py").read_text()


def _cfg(**over):
    c = dict(pte._DEFAULT_CONFIG)
    c.update(over)
    return c


def _mk_stock(s, symbol, sector, market="US"):
    st = Stock(symbol=symbol, name=symbol, sector=sector, market=market,
               exchange="NASDAQ" if market == "US" else "HKEX",
               currency="USD" if market == "US" else "HKD")
    s.add(st); s.flush()
    return st


def _mk_open(s, pf, stock, shares, entry, stop):
    t = PaperTrade(portfolio_id=pf.id, symbol=stock.symbol, stock_id=stock.id,
                   shares=shares, entry_price=entry, current_stop=stop, stop_loss=stop,
                   stage="open", trading_style="SWING", sector=stock.sector,
                   entry_date=_date(2026, 9, 1),
                   entry_time=datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc))
    s.add(t); s.flush()
    return t


def _sig(symbol):
    """`_open_paper_trade` reads only `sig.reasons`, `sig.confidence` and `sig.id` before the cap
    checks, so a stand-in is used rather than a persisted Signal row. The caps are what is under
    test; Signal persistence is not."""
    return types.SimpleNamespace(id=None, symbol=symbol, confidence=70.0, reasons={},
                                 signal="BUY", horizon=None)


def _attempt(s, pf, stock, live_price, prefetched, cfg, equity=EQUITY, atr=2.0):
    """One real entry attempt. Returns the rejection reason, or 'opened'."""
    gp = {"stop": live_price * 0.95, "take_profit": live_price * 1.15}
    trade, reason = pte._open_paper_trade(
        s, pf, stock, _sig(stock.symbol), None, live_price, gp, 8, [], "fallback",
        cfg, "SWING", equity, 1.0, None, {stock.symbol: live_price}, prefetched, atr)
    return reason or ("opened" if trade is not None else "unknown")


# ── 1. EXISTING EXPOSURE binds the sector cap ─────────────────────────────────────────────────
with Session() as s:
    pf = PaperPortfolio(name="p", initial_capital=EQUITY, current_cash=EQUITY,
                        config={"market": "US"}); s.add(pf); s.flush()
    tech = [_mk_stock(s, f"T{i}", "Technology") for i in range(4)]
    newc = _mk_stock(s, "TNEW", "Technology")
    s.commit()
    # Three open tech positions at 10% each = 30% of equity; cap is max_sector_pct.
    opens = [(_mk_open(s, pf, tech[i], 100, 100.0, 95.0), tech[i]) for i in range(3)]
    s.commit()
    cfg = _cfg(max_sector_pct=0.30, max_sector_positions=99, max_position_pct=0.20)
    R["existing_exposure_binds"] = {
        "sector_value_pct": 30.0,
        "reason": _attempt(s, pf, newc, 100.0, opens, cfg),
    }

# ── 2. PENDING ORDERS are not open trades — are they counted? ─────────────────────────────────
# A conditional/pending order represents committed-but-unfilled exposure. `prefetched_open` is
# built from OPEN TRADES, so this measures whether an unfilled order protects the cap.
with Session() as s:
    pf = s.query(PaperPortfolio).first()
    tech = s.query(Stock).filter(Stock.symbol.like("T%")).all()
    newc = s.query(Stock).filter_by(symbol="TNEW").one()
    opens = [(t, s.query(Stock).filter_by(symbol=t.symbol).one())
             for t in s.query(PaperTrade).filter_by(stage="open").all()]
    cfg = _cfg(max_sector_pct=0.30, max_sector_positions=99, max_position_pct=0.20)
    # The decisive fact is the PROVENANCE of the snapshot, not one sized fixture: the query
    # that builds it selects open trades only, so anything not yet a filled position — a
    # pending order, a working conditional, an accepted-but-unfilled broker order — contributes
    # zero to every concentration check.
    _q = _src[_src.index("_prefetched_open: list[tuple] = session.execute("):][:400]
    R["pending_orders_counted"] = {
        "snapshot_query_filters": [f for f in ('PaperTrade.stage == "open"',
                                               "portfolio_id == portfolio.id") if f in _q],
        "mentions_pending_or_order_state": any(
            k in _q for k in ("pending", "working", "order", "unfilled")),
        "open_positions_in_snapshot": len(opens[:2]),
    }

# ── 3. CONCURRENT ENTRIES against one snapshot ────────────────────────────────────────────────
# Two candidates sized against the SAME prefetched_open. Neither sees the other.
with Session() as s:
    pf = s.query(PaperPortfolio).first()
    a = _mk_stock(s, "CA", "Energy"); b = _mk_stock(s, "CB", "Energy"); s.commit()
    snapshot = []                                     # empty sector: neither sees the other
    cfg = _cfg(max_sector_pct=0.15, max_sector_positions=99, max_position_pct=0.10)
    r1 = _attempt(s, pf, a, 100.0, snapshot, cfg)
    r2 = _attempt(s, pf, b, 100.0, snapshot, cfg)
    s.commit()
    opened = s.query(PaperTrade).filter(PaperTrade.symbol.in_(("CA", "CB")),
                                        PaperTrade.stage == "open").all()
    combined = sum(float(t.shares) * float(t.entry_price) for t in opened)
    R["concurrent_entries"] = {
        "first": r1, "second_same_snapshot": r2,
        "sector_cap_pct": 15.0,
        "positions_opened": len(opened),
        "combined_value": round(combined, 2),
        "combined_pct_of_equity": round(combined / EQUITY * 100, 2),
        "breaches_sector_cap": combined / EQUITY > 0.15,
    }

# ── 4. STALE / MISSING MARKS ──────────────────────────────────────────────────────────────────
# A position that has doubled is still counted at entry price when no live mark is available.
with Session() as s:
    pf = s.query(PaperPortfolio).first()
    hs = _mk_stock(s, "HOLD1", "Utilities"); nw = _mk_stock(s, "UNEW", "Utilities"); s.commit()
    held = _mk_open(s, pf, hs, 200, 100.0, 95.0)      # entry 100 -> 20% of equity at entry
    s.commit()
    cfg = _cfg(max_sector_pct=0.30, max_sector_positions=99, max_position_pct=0.15)
    priced = pte._best_price(held, {"HOLD1": 200.0})   # live mark: doubled
    stale = pte._best_price(held, {})                  # no mark at all
    R["stale_marks"] = {
        "entry_price": 100.0, "live_price": 200.0,
        "priced_with_live_mark": priced, "price_when_mark_missing": stale,
        "exposure_understated_by_pct": round((200.0 - stale) / 200.0 * 100, 1),
    }

# ── 5. CURRENCY: are HKD and USD values summed raw? ───────────────────────────────────────────
with Session() as s:
    pf = s.query(PaperPortfolio).first()
    hk = _mk_stock(s, "0700.HK", "Technology", market="HK")
    us = _mk_stock(s, "USTECH", "Technology", market="US"); s.commit()
    hk_pos = _mk_open(s, pf, hk, 1000, 300.0, 285.0)   # 300,000 HKD ~= 38,500 USD
    s.commit()
    cfg = _cfg(max_sector_pct=0.50, max_sector_positions=99, max_position_pct=0.50)
    raw = pte._best_price(hk_pos, {"0700.HK": 300.0}) * hk_pos.shares
    R["currency"] = {
        "hk_position_local_value": raw,
        "equity_usd": EQUITY,
        "raw_ratio_vs_equity": round(raw / EQUITY, 2),
        "note": "values are summed in LOCAL currency with no FX conversion",
    }

# ── 6. RISK-REDUCING EXITS must remain possible when entry limits are breached ────────────────
# Entry caps live in _open_paper_trade. Exits route through a different path entirely; this
# records whether any exit function GATES on the entry caps.


_lines = _src.split("\n")
_starts = {i: l for i, l in enumerate(_lines) if l.startswith("def ")}


def _fn_body(name):
    """Slice ONE top-level function.

    The first version took `index("\\ndef ", i+1)`, which is only correct if the next top-level
    def immediately follows. `_monitor_positions` runs 1,026 lines and the naive slice swallowed
    `_scan_for_entries` whole — so the probe reported the ENTRY scan's caps as if they were
    inside the exit path. The finding was an artifact of the slice, not of the code.
    """
    i = next(k for k, l in _starts.items() if l.startswith(f"def {name}("))
    j = next((k for k in sorted(_starts) if k > i), len(_lines))
    return "\n".join(_lines[i:j])


_exit_fns = [n for n in ("_monitor_positions",) if f"\ndef {n}(" in _src]
_mon = _fn_body("_monitor_positions")
R["exits_when_capped"] = {
    "exit_function": "_monitor_positions",
    "body_lines": len(_mon.split("\n")),
    "entry_cap_names_present": [k for k in ("max_sector_pct", "max_position_pct",
                                            "max_positions", "max_open_risk_pct", "sector_cap",
                                            "open_risk_cap") if k in _mon],
    # The distinction that matters: a cap NAME appearing in the exit path is not a cap GATE.
    # Every occurrence here must be a log, never a return/continue that skips an exit.
    "cap_usage_is_warning_only": "log.warning(\"paper.sector_cap_exceeded\"" in _mon,
    "exit_returns_guarded_by_a_cap": [
        ln.strip() for ln in _mon.split("\n")
        if ("return" in ln or "continue" in ln)
        and any(k in ln for k in ("max_sector_pct", "sector_cap", "max_position_pct"))
    ],
}

# ── 7. PARTIAL FILLS: exposure must follow FILLED shares, not ordered ─────────────────────────
with Session() as s:
    pf = s.query(PaperPortfolio).first()
    pstock = _mk_stock(s, "PART", "Materials"); pnew = _mk_stock(s, "PNEW", "Materials")
    s.commit()
    full = _mk_open(s, pf, pstock, 100, 100.0, 95.0)       # 10,000 = 10%
    s.commit()
    partial_view = [(full, pstock)]
    full.shares = 30                                        # only 30 of 100 filled
    s.commit()
    cfg = _cfg(max_sector_pct=0.15, max_sector_positions=99, max_position_pct=0.10)
    R["partial_fills"] = {
        "shares_field_is_filled_quantity": full.shares,
        "reason_after_partial": _attempt(s, pf, pnew, 100.0, [(full, pstock)], cfg),
        "note": "PaperTrade.shares is the only quantity field; there is no ordered-vs-filled "
                "distinction in this model",
    }

# ── 8. OPTIONS ASSIGNMENT: does assigned stock exposure reach the concentration check? ────────
R["options_assignment"] = {
    "paper_trade_has_option_fields": [c.name for c in PaperTrade.__table__.columns
                                      if "option" in c.name or "assign" in c.name],
    "concentration_reads": "prefetched_open, built from PaperTrade rows with stage='open'",
}

print(json.dumps(R, indent=2, default=str))
