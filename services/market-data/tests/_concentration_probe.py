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
from datetime import datetime, timedelta, timezone
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

# ── 9. RESERVATION LIFECYCLE — rollback, crash, double-count, reconciliation ──────────────────
from db.models import PortfolioExposureReservation as _Res                 # noqa: E402
import importlib.util as _ilu2                                             # noqa: E402

_espec = _ilu2.spec_from_file_location("common.exposure", ROOT / "shared" / "common" / "exposure.py")
_exp = _ilu2.module_from_spec(_espec); sys.modules["common.exposure"] = _exp
_espec.loader.exec_module(_exp)

_PRICE = lambda t: (float(t.entry_price), None)    # noqa: E731  (None = live mark)
T = datetime(2026, 10, 1, 12, 0, 0)

with Session() as s:
    pf2 = PaperPortfolio(name="p2", initial_capital=EQUITY, current_cash=EQUITY,
                         config={"market": "US"}); s.add(pf2); s.flush()
    s.commit()
    PF2 = pf2.id

# (a) ROLLBACK — NOT AUTHORITATIVE ON SQLITE, and the probe says so rather than guessing.
#
# NOT "SQLite cannot test rollback". Under the DEFAULT pysqlite configuration used here the
# driver does not emit BEGIN for DML, so a released SAVEPOINT is already durable and
# `session.rollback()` does not undo it; SQLite itself supports rollback, and the documented
# workaround (isolation_level=None plus an explicit BEGIN) would restore it. A driver/
# transaction-configuration property, not a property of the reservation — so the guarantee is
# asserted on PostgreSQL instead.
#
# Its own portfolio, because the first version of this scenario leaked a 10,000 reservation into
# the NEXT scenario and silently turned a valid reservation into a `sector_cap` refusal — a
# fixture leak that looked exactly like a code defect.
with Session() as s:
    _rb = PaperPortfolio(name="rollback", initial_capital=EQUITY, current_cash=EQUITY,
                         config={"market": "US"}); s.add(_rb); s.flush(); s.commit()
    PF_RB = _rb.id
with Session() as s:
    _, rolled_reason = _exp.reserve(s, portfolio_id=PF_RB, intent_id="roll-1", symbol="RB",
                                    sector="Energy", value=10_000.0, equity=EQUITY,
                                    cap_pct=0.15, price_for=_PRICE, now=T)
    s.rollback()
with Session() as s:
    R["reservation_rollback"] = {
        "reserve_reason": rolled_reason,
        "rows_after_rollback": s.query(_Res).filter_by(portfolio_id=PF_RB).count(),
        "reserved_value_after_rollback": _exp.active_reserved_value(s, PF_RB, "Energy", now=T),
        "authoritative_on_this_engine": False,
        "why": "under the default pysqlite configuration the driver does not emit BEGIN for "
               "DML, so a released SAVEPOINT is already durable and rollback does not undo it; "
               "SQLite itself supports rollback (isolation_level=None plus an explicit BEGIN "
               "restores it). Asserted on PostgreSQL instead.",
    }

# (b) WORKER CRASH: reserved, never consumed or released. Must not block forever.
with Session() as s:
    _exp.reserve(s, portfolio_id=PF2, intent_id="crash-1", symbol="CR", sector="Energy",
                 value=14_000.0, equity=EQUITY, cap_pct=0.15, price_for=_PRICE,
                 ttl_seconds=60, now=T)
    s.commit()
with Session() as s:
    blocked = _exp.reserve(s, portfolio_id=PF2, intent_id="crash-blocked", symbol="X",
                           sector="Energy", value=5_000.0, equity=EQUITY, cap_pct=0.15,
                           price_for=_PRICE, now=T)[1]
    s.commit()
LATER = T + timedelta(seconds=600)
with Session() as s:
    # Read-time expiry frees it even before the sweep runs.
    freed_at_read = _exp.active_reserved_value(s, PF2, "Energy", now=LATER)
    n_expired = _exp.expire_stale(s, now=LATER)
    s.commit()
with Session() as s:
    after = _exp.reserve(s, portfolio_id=PF2, intent_id="crash-after", symbol="X2",
                         sector="Energy", value=5_000.0, equity=EQUITY, cap_pct=0.15,
                         price_for=_PRICE, now=LATER)[1]
    s.commit()
    row = s.query(_Res).filter_by(intent_id="crash-1").one()
    R["worker_crash"] = {
        "blocked_while_held": blocked,
        "reserved_value_after_ttl": freed_at_read,
        "expired_by_sweep": n_expired, "state": row.state, "reason": row.terminal_reason,
        "reserve_after_expiry": after,
    }

# (c) NO DOUBLE COUNT: once consumed, the reservation stops counting — the position carries it.
with Session() as s:
    s.query(_Res).filter_by(portfolio_id=PF2).delete(); s.commit()
with Session() as s:
    st = _mk_stock(s, "DC1", "Energy"); s.commit()
    r, _ = _exp.reserve(s, portfolio_id=PF2, intent_id="dc-1", symbol="DC1", sector="Energy",
                        value=10_000.0, equity=EQUITY, cap_pct=0.15, price_for=_PRICE, now=T)
    s.commit()
    before = _exp.active_reserved_value(s, PF2, "Energy", now=T)
    tr = _mk_open(s, s.query(PaperPortfolio).filter_by(id=PF2).one(), st, 100, 100.0, 95.0)
    s.flush()
    consumed = _exp.consume(s, r, trade_id=tr.id, now=T)
    s.commit()
with Session() as s:
    committed, _fb = _exp.committed_value(s, PF2, "Energy", price_for=_PRICE)
    R["no_double_count"] = {
        "reserved_before_consume": before, "consumed": consumed,
        "reserved_after_consume": _exp.active_reserved_value(s, PF2, "Energy", now=T),
        "committed_after_consume": committed,
        "total_counted_once": committed + _exp.active_reserved_value(s, PF2, "Energy", now=T),
    }

# (d) RELEASE returns exposure immediately, without waiting out the TTL.
with Session() as s:
    r, _ = _exp.reserve(s, portfolio_id=PF2, intent_id="rel-1", symbol="RL", sector="Materials",
                        value=12_000.0, equity=EQUITY, cap_pct=0.15, price_for=_PRICE, now=T)
    s.commit()
    held = _exp.active_reserved_value(s, PF2, "Materials", now=T)
    released = _exp.release(s, r, reason="rejected: sector_count_cap", now=T)
    s.commit()
with Session() as s:
    R["release"] = {
        "held": held, "released": released,
        "after": _exp.active_reserved_value(s, PF2, "Materials", now=T),
        "second_release_is_refused": _exp.release(
            s, s.query(_Res).filter_by(intent_id="rel-1").one(), reason="again", now=T),
    }

# (e) A CONSUMED reservation cannot be released, and an EXPIRED one cannot be consumed.
# (c) deleted this portfolio's earlier reservations, so build a fresh expired one here rather
# than reaching for one a previous scenario may have removed.
with Session() as s:
    _exp.reserve(s, portfolio_id=PF2, intent_id="sm-expired", symbol="SM", sector="Utilities",
                 value=1.0, equity=EQUITY, cap_pct=0.90, price_for=_PRICE, ttl_seconds=60,
                 now=T)
    s.commit()
with Session() as s:
    _exp.expire_stale(s, now=T + timedelta(seconds=600)); s.commit()
with Session() as s:
    consumed_row = s.query(_Res).filter_by(intent_id="dc-1").one()
    expired_row = s.query(_Res).filter_by(intent_id="sm-expired").one()
    R["state_machine"] = {
        "release_a_consumed_reservation": _exp.release(s, consumed_row, reason="x", now=T),
        "consume_an_expired_reservation": _exp.consume(s, expired_row, trade_id=1, now=T),
    }
    s.rollback()

# (f) RECONCILIATION surfaces a consumed reservation whose position is gone.
with Session() as s:
    open_ids = {t.id for t in s.query(PaperTrade).filter_by(portfolio_id=PF2, stage="open")}
    R["reconciliation_clean"] = _exp.reconcile(s, portfolio_id=PF2, open_trade_ids=open_ids,
                                               now=T)
    R["reconciliation_orphaned"] = _exp.reconcile(s, portfolio_id=PF2, open_trade_ids=set(),
                                                  now=T)

# (g) FAIL CLOSED when a position cannot be valued at all.
with Session() as s:
    none_price = _exp.reserve(s, portfolio_id=PF2, intent_id="fc-1", symbol="FC",
                              sector="Energy", value=1.0, equity=EQUITY, cap_pct=0.90,
                              price_for=lambda t: (None, "unvaluable"), now=T)[1]
    stale_ok = _exp.reserve(s, portfolio_id=PF2, intent_id="fc-2", symbol="FC2",
                            sector="Energy", value=1.0, equity=EQUITY, cap_pct=0.90,
                            price_for=lambda t: (float(t.entry_price), "entry_price"),
                            now=T)[1]
    s.rollback()
with Session() as s:
    stale_refused = _exp.reserve(s, portfolio_id=PF2, intent_id="fc-3", symbol="FC3",
                                 sector="Energy", value=1.0, equity=EQUITY, cap_pct=0.90,
                                 price_for=lambda t: (float(t.entry_price), "entry_price"),
                                 require_fresh_marks=True, now=T)[1]
    s.rollback()
# Shadow telemetry: what a fresh-mark policy WOULD have done, without doing it.
with Session() as s:
    _tel_fallback, _tel_fresh = {}, {}
    _exp.reserve(s, portfolio_id=PF2, intent_id="tel-1", symbol="T1", sector="Energy",
                 value=1.0, equity=EQUITY, cap_pct=0.90,
                 price_for=lambda t: (float(t.entry_price), "entry_price"),
                 telemetry=_tel_fallback, now=T)
    s.rollback()
with Session() as s:
    _exp.reserve(s, portfolio_id=PF2, intent_id="tel-2", symbol="T2", sector="Energy",
                 value=1.0, equity=EQUITY, cap_pct=0.90,
                 price_for=lambda t: (float(t.entry_price), None), telemetry=_tel_fresh, now=T)
    s.rollback()

R["fail_closed"] = {"unvaluable_position": none_price, "stale_mark_default": stale_ok,
                    "stale_mark_when_fresh_required": stale_refused}
R["shadow_fresh_marks"] = {
    "with_fallback": {k: _tel_fallback.get(k) for k in
                      ("fallback_marks", "fallback_value", "would_block_on_fresh_marks",
                       "would_exceed_cap", "decision_would_change", "unvaluable")},
    "fallback_provenance": sorted({f["provenance"]
                                   for f in (_tel_fallback.get("fallback_detail") or [])}),
    "all_fresh": {k: _tel_fresh.get(k) for k in
                  ("fallback_marks", "would_block_on_fresh_marks", "decision_would_change")},
    "entry_still_allowed_with_fallback": stale_ok,
}

# ── 10. M20: eligibility must not depend on notification infrastructure ───────────────────────
#
# The architectural tests assert that the entry path REFERENCES no outbox or delivery state.
# This asserts the BEHAVIOUR that restriction exists to guarantee: identical trading inputs
# produce an identical eligibility decision whether notification infrastructure is healthy or
# completely unavailable. A source check can be satisfied by an indirect call; this cannot.
def _entry_outcome(s, pf, stock, snapshot, cfg, live_price=100.0):
    gp = {"stop": live_price * 0.95, "take_profit": live_price * 1.15}
    trade, reason = pte._open_paper_trade(
        s, pf, stock, _sig(stock.symbol), None, live_price, gp, 8, [], "fallback",
        cfg, "SWING", EQUITY, 1.0, None, {stock.symbol: live_price}, snapshot, 2.0)
    return {"opened": trade is not None, "reason": reason,
            "shares": round(float(trade.shares), 6) if trade is not None else None,
            "entry_price": round(float(trade.entry_price), 6) if trade is not None else None}


class _Exploding:
    """Every attribute access raises. Stands in for notification infrastructure that is not
    merely failing but unreachable — the strongest form of 'unavailable'."""
    def __getattr__(self, name):
        raise ConnectionError(f"notification infrastructure unavailable: {name}")


with Session() as s:
    pf3 = PaperPortfolio(name="parity", initial_capital=EQUITY, current_cash=EQUITY,
                         config={"market": "US"}); s.add(pf3); s.flush()
    st_a = _mk_stock(s, "PAR1", "Industrials")
    st_b = _mk_stock(s, "PAR2", "Industrials")
    s.commit()
    cfg_par = _cfg(max_sector_pct=0.50, max_sector_positions=99, max_position_pct=0.10)
    healthy = _entry_outcome(s, pf3, st_a, [], cfg_par)
    s.commit()

import src.services.email_service as _email                                # noqa: E402
_saved = (_email.send_email, getattr(pte, "_exposure"))
try:
    _email.send_email = lambda *a, **kw: (_ for _ in ()).throw(
        ConnectionError("smtp unreachable"))
    sys.modules["common.outbox"] = _Exploding()
    with Session() as s:
        pf3 = s.query(PaperPortfolio).filter_by(name="parity").one()
        st_b = s.query(Stock).filter_by(symbol="PAR2").one()
        degraded = _entry_outcome(s, pf3, st_b, [], cfg_par)
        s.commit()
finally:
    _email.send_email = _saved[0]

R["eligibility_parity_under_notification_outage"] = {
    "healthy": healthy,
    "notifications_unavailable": degraded,
    # Symbol differs by construction (two candidates); everything that constitutes ELIGIBILITY
    # must match exactly.
    "identical_eligibility": (healthy["opened"] == degraded["opened"]
                              and healthy["reason"] == degraded["reason"]
                              and healthy["shares"] == degraded["shares"]
                              and healthy["entry_price"] == degraded["entry_price"]),
}

# ── 11. M25: the broker commit boundary ───────────────────────────────────────────────────────
import importlib.util as _ilu3                                             # noqa: E402
_bspec = _ilu3.spec_from_file_location(
    "broker_submission",
    ROOT / "services" / "market-data" / "src" / "services" / "broker_submission.py")
bs = _ilu3.module_from_spec(_bspec); _bspec.loader.exec_module(bs)


class FlagRedis:
    def __init__(self, on): self.on = on
    def get(self, k): return "1" if self.on else None


class BrokerRecorder:
    """Records every call, so a DUPLICATE submission is detectable rather than assumed absent."""
    def __init__(self, behaviour="accept"):
        self.behaviour = behaviour
        self.calls = []

    def __call__(self, session, trade, portfolio):
        self.calls.append(trade.id)
        if self.behaviour == "timeout":
            raise TimeoutError("broker read timeout after submit")
        if self.behaviour == "reject":
            raise RuntimeError("broker rejected: insufficient buying power")
        if self.behaviour == "silent_fallback":
            return                      # the historical behaviour: swallows and falls back
        trade.broker_order_id = f"ord-{trade.id}"


R["broker_flag"] = {
    "absent": bs.submit_after_commit_enabled(FlagRedis(False)),
    "set": bs.submit_after_commit_enabled(FlagRedis(True)),
    "no_client": bs.submit_after_commit_enabled(None),
}

with Session() as s:
    bpf = PaperPortfolio(name="broker", initial_capital=EQUITY, current_cash=EQUITY,
                         config={"market": "US"}); s.add(bpf); s.flush()
    bst = _mk_stock(s, "BRK1", "Financials"); s.commit()
    BPF, BST = bpf.id, bst.id


def _pending_trade(s, symbol="BRK1"):
    st = s.query(Stock).filter_by(id=BST).one()
    t = _mk_open(s, s.query(PaperPortfolio).filter_by(id=BPF).one(), st, 10, 100.0, 95.0)
    bs.mark_pending(t, path="deferred")
    s.flush()
    return t


# (a) The intent is recorded and COMMITTED before anything is sent.
with Session() as s:
    t = _pending_trade(s); s.commit()
    R["broker_intent"] = {"state": t.broker_submission_state, "order_id": t.broker_order_id,
                          "claimable": len(bs.claimable(s, portfolio_id=BPF))}

# (b) Happy path: submitting -> submitted, exactly one call.
rec = BrokerRecorder("accept")
with Session() as s:
    res = bs.submit_pending(s, place=rec, commit=s.commit, portfolio_id=BPF)
with Session() as s:
    t = s.query(PaperTrade).filter_by(portfolio_id=BPF).order_by(PaperTrade.id).first()
    R["broker_submit_ok"] = {"batch": res, "state": t.broker_submission_state,
                             "order_id": t.broker_order_id, "calls": len(rec.calls),
                             "attempts": t.broker_submit_attempts}

# (c) TIMEOUT after a possible acceptance -> unknown, never retried.
rec_t = BrokerRecorder("timeout")
with Session() as s:
    _pending_trade(s, "BRK1"); s.commit()
with Session() as s:
    res_t = bs.submit_pending(s, place=rec_t, commit=s.commit, portfolio_id=BPF)
with Session() as s:
    again = bs.submit_pending(s, place=rec_t, commit=s.commit, portfolio_id=BPF)
    unknown_rows = [t.id for t in bs.needs_reconciliation(s)]
    R["broker_timeout"] = {"batch": res_t, "retried": again["claimed"],
                           "calls": len(rec_t.calls), "awaiting_reconciliation": len(unknown_rows)}

# (d) A definite rejection is retryable, and bounded.
rec_r = BrokerRecorder("reject")
with Session() as s:
    _pending_trade(s); s.commit()
with Session() as s:
    first = bs.submit_pending(s, place=rec_r, commit=s.commit, portfolio_id=BPF)
with Session() as s:
    rows = [t for t in s.query(PaperTrade).filter_by(portfolio_id=BPF).all()
            if t.broker_submission_state == "failed"]
    R["broker_reject"] = {"batch": first, "failed_rows": len(rows),
                          "attempts": max((t.broker_submit_attempts for t in rows), default=0)}

# (e) A call that returns WITHOUT an order id is a failure, not a success.
rec_s = BrokerRecorder("silent_fallback")
with Session() as s:
    t = _pending_trade(s); s.commit(); SILENT = t.id
with Session() as s:
    res_s = bs.submit_pending(s, place=rec_s, commit=s.commit, portfolio_id=BPF, limit=50)
with Session() as s:
    t = s.query(PaperTrade).filter_by(id=SILENT).one()
    R["broker_silent_fallback"] = {"state": t.broker_submission_state,
                                   "order_id": t.broker_order_id,
                                   "error": t.broker_error}

# (f) A row stuck in `submitting` is NEVER re-claimed — it may already exist at the broker.
with Session() as s:
    t = _pending_trade(s); s.flush()
    bs.begin_submission(s, t); s.commit(); STUCK = t.id
with Session() as s:
    claim_ids = [x.id for x in bs.claimable(s, portfolio_id=BPF, limit=50)]
    recon_ids = [x.id for x in bs.needs_reconciliation(s)]
    R["broker_stuck_submitting"] = {
        "reclaimed": STUCK in claim_ids,
        "surfaced_for_reconciliation": STUCK in recon_ids,
    }

# (g) A MISSING ORDER ID IS UNKNOWN, not a failure — it may exist at the broker.
rec_sf = BrokerRecorder("silent_fallback")
with Session() as s:
    t = _pending_trade(s); s.commit(); SILENT2 = t.id
with Session() as s:
    bs.submit_pending(s, place=rec_sf, commit=s.commit, portfolio_id=BPF, limit=50)
with Session() as s:
    t = s.query(PaperTrade).filter_by(id=SILENT2).one()
    reclaim = [x.id for x in bs.claimable(s, portfolio_id=BPF, limit=50)]
    R["broker_missing_id_is_unknown"] = {
        "state": t.broker_submission_state,
        "retains_exposure": bs.retains_reserved_exposure(t),
        "counts_as_confirmed_fill": bs.confirmed_broker_fill(t),
        "reclaimed_for_replacement": SILENT2 in reclaim,
    }

# (h) Stable client identity, minted and committed with the intent.
with Session() as s:
    t = _pending_trade(s); s.commit()
    R["broker_identity"] = {"client_order_id": t.broker_client_order_id,
                            "path": t.broker_submission_path,
                            "before_any_broker_contact": t.broker_order_id is None}

# (i) A legacy-routed intent is NEVER claimable by the deferred dispatcher.
with Session() as s:
    st = s.query(Stock).filter_by(id=BST).one()
    t = _mk_open(s, s.query(PaperPortfolio).filter_by(id=BPF).one(), st, 10, 100.0, 95.0)
    t.broker_submission_state = "pending"
    t.broker_submission_path = "legacy"
    s.commit(); LEGACY = t.id
with Session() as s:
    R["broker_path_isolation"] = {
        "legacy_intent_claimable_by_deferred": LEGACY in
        [x.id for x in bs.claimable(s, portfolio_id=BPF, limit=50)]}

# (j) Terminal does not mean unrecordable: a later-confirmed fill can still be written.
with Session() as s:
    t = s.query(PaperTrade).filter_by(id=SILENT2).one()
    try:
        bs.reconcile_submission(s, t, resolution="submitted", evidence="", actor="ops")
        no_ev = "accepted"
    except ValueError as exc:
        no_ev = str(exc)[:60]
    try:
        bs.reconcile_submission(s, t, resolution="submitted",
                                evidence="broker order list shows it", actor="ops")
        no_id = "accepted"
    except ValueError as exc:
        no_id = str(exc)[:60]
    bs.reconcile_submission(s, t, resolution="submitted",
                            evidence="broker order list shows ord-9 for this client id",
                            actor="ops:sing", order_id="ord-9")
    s.commit()
with Session() as s:
    t = s.query(PaperTrade).filter_by(id=SILENT2).one()
    R["broker_reconcile"] = {"evidence_required": no_ev, "order_id_required": no_id,
                             "state": t.broker_submission_state,
                             "order_id": t.broker_order_id}

print(json.dumps(R, indent=2, default=str))
