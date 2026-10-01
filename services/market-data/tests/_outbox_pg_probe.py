"""PostgreSQL concurrency and crash-recovery probe for the outbox.

WHY THIS EXISTS SEPARATELY FROM THE SQLITE PROBE. SQLite establishes the state machine, but it
serialises writers, so it cannot exercise the cases that actually matter in production:
genuinely simultaneous claims from separate connections, two workers racing to settle one row,
`BigInteger` identity, and transaction conflicts under real MVCC. A claim protocol verified only
on SQLite is a claim protocol verified on the one engine that cannot contend.

Needs a disposable PostgreSQL at $OUTBOX_PG_URL. Skips (exit 0, "skipped": true) when absent, so
the suite stays runnable without Docker.
"""
import json
import os
import pathlib
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

_URL = os.environ.get("OUTBOX_PG_URL")
if not _URL:
    print(json.dumps({"skipped": True, "reason": "OUTBOX_PG_URL not set"}))
    sys.exit(0)

_ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT / "shared"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
import importlib.util as _ilu

# `common` must be the REAL package from shared/, not a stub: the trading engine imports
# common.logging, common.indicators and others from it. Only `common.config` is replaced, so the
# engine's module-level engine construction points at this throwaway database.
import common                                                             # noqa: E402
_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(
    database_url=_URL, admin_password=None, jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg
common.config = _cfg

# The REAL `db` package. Unlike the SQLite probes, nothing needs stubbing here: session.py
# builds its engine from `database_url`, which is this throwaway PostgreSQL, so the pool
# arguments it passes are valid and `db/__init__.py` runs normally — which the trading engine's
# `from db import AlertPreference, ...` requires.
import db.models as _models                                               # noqa: E402

NotificationOutbox, Base = _models.NotificationOutbox, _models.Base
PaperPortfolio = _models.PaperPortfolio
PortfolioExposureReservation = _models.PortfolioExposureReservation

_ospec = _ilu.spec_from_file_location("common.outbox", _ROOT / "shared" / "common" / "outbox.py")
ob = _ilu.module_from_spec(_ospec); sys.modules["common.outbox"] = ob
_ospec.loader.exec_module(ob)

engine = create_engine(_URL, pool_size=20, max_overflow=20)
# The outbox carries an FK to users, so the whole schema is created. This is a disposable
# database; nothing here touches any other container's data.
with engine.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)

T0 = datetime(2026, 10, 1, 12, 0, 0)
R = {"skipped": False}


def _fresh():
    with Session() as s:
        s.query(NotificationOutbox).delete(); s.commit()


def _enq(s, eid, **kw):
    kw.setdefault("recipient", "a@example.com"); kw.setdefault("subject", "s")
    kw.setdefault("body_html", "<p>b</p>"); kw.setdefault("body_text", "b")
    return ob.enqueue(s, event_id=eid, **kw)


# ── 1. BIGINT identity actually works on the production engine ────────────────────────────────
_fresh()
with Session() as s:
    r, _ = _enq(s, "pg-id"); s.commit()
    R["bigint_identity"] = {"id_assigned": r.id is not None, "id": r.id}

# ── 2. SIMULTANEOUS CLAIMS from separate connections ──────────────────────────────────────────
# 8 threads, 8 connections, 20 rows. Each row must be claimed exactly once.
_fresh()
N_ROWS, N_WORKERS = 20, 8
with Session() as s:
    for i in range(N_ROWS):
        _enq(s, f"pg-race-{i}")
    s.commit()


def _worker(name):
    with Session() as s:
        try:
            got = ob.claim(s, owner=name, limit=N_ROWS, now=T0)
            ids = [r.id for r in got]
            s.commit()
            return ids
        except Exception as exc:                      # noqa: BLE001
            s.rollback()
            return {"error": type(exc).__name__}


with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
    batches = list(ex.map(_worker, [f"w{i}" for i in range(N_WORKERS)]))

errors = [b for b in batches if isinstance(b, dict)]
claimed = [i for b in batches if isinstance(b, list) for i in b]
with Session() as s:
    leased = s.query(NotificationOutbox).filter(NotificationOutbox.state == "leased").count()
    attempts = [a for (a,) in s.query(NotificationOutbox.attempts).all()]
R["concurrent_claims"] = {
    "rows": N_ROWS, "workers": N_WORKERS,
    "total_claimed": len(claimed),
    "distinct_claimed": len(set(claimed)),
    "double_claimed": len(claimed) - len(set(claimed)),
    "leased_in_db": leased,
    "max_attempts_on_any_row": max(attempts) if attempts else None,
    "transaction_errors": errors,
}

# ── 3. TWO WORKERS SETTLING THE SAME ROW — only the lease holder may win ──────────────────────
_fresh()
with Session() as s:
    _enq(s, "pg-settle"); s.commit()
with Session() as s:
    ob.claim(s, owner="holder", lease_seconds=300, now=T0); s.commit()


def _settle(owner):
    with Session() as s:
        row = s.query(NotificationOutbox).filter_by(event_id="pg-settle").one()
        try:
            won = ob.mark_accepted(s, row, owner=owner, now=T0)
            s.commit()
            return {"owner": owner, "won": won}
        except Exception as exc:                      # noqa: BLE001
            s.rollback()
            return {"owner": owner, "error": type(exc).__name__}


with ThreadPoolExecutor(max_workers=4) as ex:
    settles = list(ex.map(_settle, ["holder", "impostor-a", "impostor-b", "impostor-c"]))
with Session() as s:
    row = s.query(NotificationOutbox).filter_by(event_id="pg-settle").one()
R["concurrent_settle"] = {
    "results": settles,
    "winners": [r["owner"] for r in settles if r.get("won")],
    "final_state": row.state,
}

# ── 4. LEASE EXPIRY under a real clock-independent comparison ─────────────────────────────────
_fresh()
with Session() as s:
    _enq(s, "pg-lease"); s.commit()
with Session() as s:
    ob.claim(s, owner="first", lease_seconds=60, now=T0); s.commit()
with Session() as s:
    during = ob.claim(s, owner="second", now=T0 + timedelta(seconds=30)); s.commit()
with Session() as s:
    after = ob.claim(s, owner="second", now=T0 + timedelta(seconds=120)); s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
R["lease_expiry"] = {"stolen_during": len(during), "reclaimed_after": len(after),
                     "owner": row.lease_owner, "attempts": row.attempts}

# ── 5. IDEMPOTENCY under concurrent enqueue of the SAME event ─────────────────────────────────
_fresh()


def _enqueue_same(n):
    with Session() as s:
        try:
            row, created = _enq(s, "pg-dup")
            s.commit()
            return {"created": created}
        except Exception as exc:                      # noqa: BLE001
            s.rollback()
            return {"error": type(exc).__name__}


with ThreadPoolExecutor(max_workers=8) as ex:
    dups = list(ex.map(_enqueue_same, range(8)))
with Session() as s:
    R["concurrent_enqueue"] = {
        "rows": s.query(NotificationOutbox).filter_by(event_id="pg-dup").count(),
        "created_true": sum(1 for d in dups if d.get("created") is True),
        "created_false": sum(1 for d in dups if d.get("created") is False),
        "errors": [d for d in dups if "error" in d],
    }

# ── 6. CRASH RECOVERY: an abandoned in-flight row is quarantined, not resent ──────────────────
_fresh()
with Session() as s:
    _enq(s, "pg-crash"); s.commit()
with Session() as s:
    got = ob.claim(s, owner="doomed", lease_seconds=60, now=T0)
    ob.begin_dispatch(s, got[0], owner="doomed", now=T0)
    s.commit()                                        # in flight; the process then dies
LATER = T0 + timedelta(seconds=600)
with Session() as s:
    reclaim = ob.claim(s, owner="next", now=LATER); s.commit()
with Session() as s:
    n = ob.quarantine_uncertain(s, now=LATER); s.commit()
with Session() as s:
    row = s.query(NotificationOutbox).one()
R["crash_recovery"] = {"reclaimed_without_sweep": len(reclaim), "quarantined": n,
                       "state": row.state, "reason": row.terminal_reason}

# ── 7. STATS reconcile on the production engine ───────────────────────────────────────────────
with Session() as s:
    st = ob.stats(s, now=LATER)
R["stats"] = {"reconciles": st["reconciles"], "awaiting_review": st["awaiting_review"],
              "by_state": st["by_state"]}

# ── 8. EXPOSURE RESERVATION under real transactions and real contention ───────────────────────
# SQLite could not answer either of these: pysqlite does not open a transaction for DML, so a
# released SAVEPOINT is already durable, and it serialises writers so contention never happens.
_espec = _ilu.spec_from_file_location("common.exposure", _ROOT / "shared" / "common" / "exposure.py")
exposure = _ilu.module_from_spec(_espec); sys.modules["common.exposure"] = exposure
_espec.loader.exec_module(exposure)

_PRICE = lambda t: (float(t.entry_price), None)          # noqa: E731
TR = datetime(2026, 10, 1, 12, 0, 0)

with Session() as s:
    pf = PaperPortfolio(name="pg", initial_capital=100_000.0, current_cash=100_000.0,
                        config={"market": "US"})
    s.add(pf); s.flush(); s.commit()
    PFID = pf.id

# (a) ROLLBACK genuinely discards the reservation.
with Session() as s:
    _, why = exposure.reserve(s, portfolio_id=PFID, intent_id="pg-roll", symbol="RB",
                              sector="Energy", value=10_000.0, equity=100_000.0, cap_pct=0.15,
                              price_for=_PRICE, now=TR)
    s.rollback()
with Session() as s:
    R["reservation_rollback"] = {
        "reserve_reason": why,
        "rows_after_rollback": s.query(PortfolioExposureReservation).filter_by(
            portfolio_id=PFID).count(),
        "reserved_after_rollback": exposure.active_reserved_value(s, PFID, "Energy", now=TR),
    }

# (b) CONCURRENT RESERVATION: 8 threads each claim 4% against a 15% cap. At most 3 may win.
with Session() as s:
    s.query(PortfolioExposureReservation).filter_by(portfolio_id=PFID).delete(); s.commit()


def _try_reserve(i):
    with Session() as s:
        try:
            row, why = exposure.reserve(
                s, portfolio_id=PFID, intent_id=f"pg-race-{i}", symbol=f"S{i}",
                sector="Energy", value=4_000.0, equity=100_000.0, cap_pct=0.15,
                price_for=_PRICE, now=TR)
            s.commit()
            return why
        except Exception as exc:                      # noqa: BLE001
            s.rollback()
            return f"error:{type(exc).__name__}"


with ThreadPoolExecutor(max_workers=8) as ex:
    outcomes = list(ex.map(_try_reserve, range(8)))
with Session() as s:
    total = exposure.active_reserved_value(s, PFID, "Energy", now=TR)
R["concurrent_reservation"] = {
    "outcomes": outcomes,
    "granted": sum(1 for o in outcomes if o == "reserved"),
    "refused": sum(1 for o in outcomes if o == "sector_cap"),
    "errors": [o for o in outcomes if o.startswith("error:")],
    "total_reserved_value": total,
    "cap_value": 15_000.0,
    "within_cap": total <= 15_000.0,
}

# (c) IDEMPOTENT intent: the same proposed entry reserved twice claims once.
with Session() as s:
    s.query(PortfolioExposureReservation).filter_by(portfolio_id=PFID).delete(); s.commit()


def _same_intent(i):
    with Session() as s:
        try:
            _, why = exposure.reserve(s, portfolio_id=PFID, intent_id="pg-dup",
                                      symbol="D", sector="Energy", value=1_000.0,
                                      equity=100_000.0, cap_pct=0.15, price_for=_PRICE, now=TR)
            s.commit()
            return why
        except Exception as exc:                      # noqa: BLE001
            s.rollback()
            return f"error:{type(exc).__name__}"


with ThreadPoolExecutor(max_workers=6) as ex:
    dup = list(ex.map(_same_intent, range(6)))
with Session() as s:
    R["idempotent_intent"] = {
        "outcomes": dup,
        "rows": s.query(PortfolioExposureReservation).filter_by(intent_id="pg-dup").count(),
        "reserved_value": exposure.active_reserved_value(s, PFID, "Energy", now=TR),
    }

# ── 9. MIXED-WRITER INTEGRATION: organic entry vs conditional order, real paths ───────────────
#
# Racing two calls to reserve() shows the primitive works. It does NOT show that the two
# PRODUCTION writers are actually protected: they are different modules, build their own
# `prefetched_open`, and could in principle reach the cap by different routes. This races the
# real `_open_paper_trade` (organic shape) against the real `conditional_orders._execute_buy`
# (which builds its own snapshot and calls the same function) and checks the resulting
# positions against the cap.
import types as _types                                                    # noqa: E402
from unittest.mock import MagicMock as _MM                                # noqa: E402

for _m in ["redis", "httpx", "structlog", "apscheduler", "apscheduler.schedulers",
           "apscheduler.schedulers.background", "apscheduler.triggers",
           "apscheduler.triggers.cron", "yfinance"]:
    sys.modules.setdefault(_m, _MM())
sys.path.insert(0, str(_ROOT / "services" / "market-data"))
common.exposure = exposure

from src.services import paper_trading_engine as pte                      # noqa: E402
from src.services import conditional_orders as co                         # noqa: E402

Stock = _models.Stock
PaperTrade = _models.PaperTrade
Signal = _models.Signal
SignalType = _models.SignalType
SignalHorizon = _models.SignalHorizon

# UPSTREAM GATES ARE STUBBED; `_open_paper_trade` IS NOT.
#
# `_execute_buy` runs a full eligibility chain — stored signal, drawdown, loss streak, win rate,
# game plan, decision engine — before it reaches the entry path. None of that is what this
# scenario tests: the question is whether two DIFFERENT production writers contend correctly at
# the concentration cap. Stubbing the chain is what makes the race actually happen; leaving it
# in place meant the conditional order was rejected by an earlier gate and the test passed
# while racing nothing.
pte._should_enter = lambda *a, **kw: (True, 8, [])
pte._call_decision_engine = lambda *a, **kw: None
pte._compute_portfolio_drawdown = lambda *a, **kw: 0.0
pte._consec_loss_streak = lambda *a, **kw: 0
pte._recent_win_rate = lambda *a, **kw: 0.6
pte._entry_gates_override_active = lambda *a, **kw: False
pte._build_game_plan_for_style = lambda *a, **kw: {"stop": 95.0, "take_profit": 115.0,
                                                  "atr": None}
pte._compute_equity = lambda *a, **kw: 100_000.0

MW_EQUITY = 100_000.0
MW_CAP = 0.15                       # 15,000 of room; each entry is ~10,010


def _mw_cfg():
    c = dict(pte._DEFAULT_CONFIG)
    c.update(max_sector_pct=MW_CAP, max_sector_positions=99, max_position_pct=0.10,
             max_open_risk_pct=0.90, market="US")
    return c


with Session() as s:
    s.query(PortfolioExposureReservation).delete()
    s.query(PaperTrade).delete()
    s.commit()
    mwpf = PaperPortfolio(name="mixed", initial_capital=MW_EQUITY, current_cash=MW_EQUITY,
                          config={**_mw_cfg(), "trading_style": "SWING"})
    s.add(mwpf); s.flush()
    org = Stock(symbol="ORG", name="ORG", sector="Industrials", market="US",
                exchange="NASDAQ", currency="USD")
    cnd = Stock(symbol="CND", name="CND", sector="Industrials", market="US",
                exchange="NASDAQ", currency="USD")
    s.add_all([org, cnd]); s.flush()
    # The conditional path requires a real, already-eligible BUY signal — it never fabricates
    # one. That gate is genuine and is left in place.
    s.add(Signal(stock_id=cnd.id, signal=SignalType.BUY, horizon=SignalHorizon.SWING,
                 confidence=70.0, reasons={}, source="probe"))
    s.flush()
    s.commit()
    MWPF, ORG_ID, CND_ID = mwpf.id, org.id, cnd.id

_MW_PRICE = 100.0
_MW_GP = {"stop": 95.0, "take_profit": 115.0}


def _organic_entry():
    """The scan path: a snapshot built before the candidate loop, then _open_paper_trade."""
    with Session() as s:
        try:
            pf = s.query(PaperPortfolio).filter_by(id=MWPF).one()
            st = s.query(Stock).filter_by(id=ORG_ID).one()
            snapshot = [(t, s.query(Stock).filter_by(symbol=t.symbol).one())
                        for t in s.query(PaperTrade).filter_by(portfolio_id=MWPF,
                                                               stage="open").all()]
            sig = _types.SimpleNamespace(id=None, symbol="ORG", confidence=70.0, reasons={},
                                         signal="BUY", horizon=None)
            trade, reason = pte._open_paper_trade(
                s, pf, st, sig, None, _MW_PRICE, dict(_MW_GP), 8, [], "fallback",
                _mw_cfg(), "SWING", MW_EQUITY, 1.0, None, {"ORG": _MW_PRICE}, snapshot, 2.0)
            s.commit()
            return {"path": "organic", "opened": trade is not None, "reason": reason}
        except Exception as exc:                        # noqa: BLE001
            s.rollback()
            return {"path": "organic", "error": f"{type(exc).__name__}: {exc}"[:200]}


def _conditional_entry():
    """The conditional-order path: the REAL _execute_buy, which builds its own snapshot and
    calls the same _open_paper_trade."""
    with Session() as s:
        try:
            pf = s.query(PaperPortfolio).filter_by(id=MWPF).one()
            order = _types.SimpleNamespace(
                id=1, portfolio_id=MWPF, symbol="CND", action="buy", style="SWING",
                condition_type="price_above", threshold=1.0, notes=None)
            ok, msg, trade_id = co._execute_buy(order, pf, _MW_PRICE, s)
            s.commit()
            return {"path": "conditional", "opened": ok, "reason": msg}
        except Exception as exc:                        # noqa: BLE001
            s.rollback()
            return {"path": "conditional", "error": f"{type(exc).__name__}: {exc}"[:200]}


with ThreadPoolExecutor(max_workers=2) as ex:
    mixed = list(ex.map(lambda f: f(), [_organic_entry, _conditional_entry]))

with Session() as s:
    opened = s.query(PaperTrade).filter_by(portfolio_id=MWPF, stage="open").all()
    total_value = sum(float(t.shares) * float(t.entry_price) for t in opened)
    res_rows = s.query(PortfolioExposureReservation).filter_by(portfolio_id=MWPF).all()
    R["mixed_writer"] = {
        "results": mixed,
        "positions_opened": len(opened),
        "combined_value": round(total_value, 2),
        "cap_value": MW_EQUITY * MW_CAP,
        "within_cap": total_value <= MW_EQUITY * MW_CAP,
        "reservation_states": sorted(r.state for r in res_rows),
        # Every reservation must be terminal and every consumed one must point at a real trade:
        # that is the reserved -> committed handover with no gap and no double count.
        "consumed_point_at_open_trades": all(
            r.trade_id in {t.id for t in opened} for r in res_rows if r.state == "consumed"),
        "none_left_reserved": all(r.state != "reserved" for r in res_rows),
        "reconcile": exposure.reconcile(s, portfolio_id=MWPF,
                                        open_trade_ids={t.id for t in opened}, now=TR),
    }

# ── 10. CRASH RECLAMATION MUST NOT FREE CAPACITY MID-COMMIT ───────────────────────────────────
with Session() as s:
    s.query(PortfolioExposureReservation).filter_by(portfolio_id=PFID).delete(); s.commit()
with Session() as s:
    r, _ = exposure.reserve(s, portfolio_id=PFID, intent_id="pg-commit", symbol="CM",
                            sector="Energy", value=14_000.0, equity=100_000.0, cap_pct=0.15,
                            price_for=_PRICE, ttl_seconds=60, now=TR)
    exposure.begin_commit(s, r, now=TR)               # entry is being written right now
    s.commit()
LONG_AFTER = TR + timedelta(seconds=3600)
with Session() as s:
    swept = exposure.expire_stale(s, now=LONG_AFTER)
    s.commit()
with Session() as s:
    still_held = exposure.active_reserved_value(s, PFID, "Energy", now=LONG_AFTER)
    blocked = exposure.reserve(s, portfolio_id=PFID, intent_id="pg-commit-block", symbol="B",
                               sector="Energy", value=5_000.0, equity=100_000.0, cap_pct=0.15,
                               price_for=_PRICE, now=LONG_AFTER)[1]
    row = s.query(PortfolioExposureReservation).filter_by(intent_id="pg-commit").one()
    stale = exposure.stale_committing(s, older_than_seconds=900, now=LONG_AFTER)
    # Read every attribute INSIDE the session: the rollback below detaches these instances.
    R["committing_not_reclaimed"] = {
        "swept_by_expiry": swept,
        "state": row.state,
        "capacity_still_held": still_held,
        "new_entry_blocked": blocked,
        "surfaced_for_review": [x.intent_id for x in stale],
    }
    s.rollback()

# The real trading engine's structlog writes to stdout, so the result is delimited rather than
# assumed to be the only thing on it.
# ── 11. THE CRASH BOUNDARY — two distinct cases ───────────────────────────────────────────────
#
# Sweeping an already-persisted `committing` row proves the EXPIRY RULE. It does not prove that
# `committing` is durably VISIBLE at the moment a crash happens, which is the only thing that
# protects capacity. Those are different claims and are separated here.

# (a) INTERNAL PAPER ENTRY — reservation, trade and consumption are one transaction.
# A rollback must leave no trade AND no stranded capacity.
with Session() as s:
    s.query(PortfolioExposureReservation).filter_by(portfolio_id=PFID).delete()
    s.commit()
with Session() as s:
    r, _ = exposure.reserve(s, portfolio_id=PFID, intent_id="pg-atomic", symbol="AT",
                            sector="Energy", value=5_000.0, equity=100_000.0, cap_pct=0.15,
                            price_for=_PRICE, now=TR)
    exposure.begin_commit(s, r, now=TR)
    st_at = Stock(symbol="ATOMIC", name="ATOMIC", sector="Energy", market="US",
                  exchange="NASDAQ", currency="USD")
    s.add(st_at); s.flush()
    tr_at = PaperTrade(portfolio_id=PFID, symbol="ATOMIC", stock_id=st_at.id, shares=50,
                       entry_price=100.0, current_stop=95.0, stop_loss=95.0, stage="open",
                       trading_style="SWING", sector="Energy",
                       entry_date=TR.date(), entry_time=TR)
    s.add(tr_at); s.flush()
    exposure.consume(s, tr_at and r, trade_id=tr_at.id, now=TR)
    s.rollback()                                   # the crash
with Session() as s:
    R["crash_internal_entry"] = {
        "trades": s.query(PaperTrade).filter_by(portfolio_id=PFID, symbol="ATOMIC").count(),
        "reservations": s.query(PortfolioExposureReservation).filter_by(
            intent_id="pg-atomic").count(),
        "stranded_capacity": exposure.active_reserved_value(s, PFID, "Energy", now=TR),
    }

# (b) BROKER BOUNDARY — is `committing` visible to ANOTHER session before the transaction
# commits? This is the case that actually decides whether capacity is protected across a crash
# that happens after a broker has accepted an order.
with Session() as s:
    s.query(PortfolioExposureReservation).filter_by(portfolio_id=PFID).delete(); s.commit()
with Session() as worker:
    r2, _ = exposure.reserve(worker, portfolio_id=PFID, intent_id="pg-broker", symbol="BK",
                             sector="Energy", value=5_000.0, equity=100_000.0, cap_pct=0.15,
                             price_for=_PRICE, ttl_seconds=60, now=TR)
    worker.commit()                                # the reservation itself IS durable
    exposure.begin_commit(worker, r2, now=TR)      # ...but this is NOT yet committed
    # A second worker sweeps while the first is mid-entry (e.g. awaiting a broker response).
    #
    # A `statement_timeout` is essential here: without one this BLOCKS indefinitely, because the
    # uncommitted `begin_commit` UPDATE holds a row lock. That block is the real finding — the
    # sweeper cannot steal the row — but an un-timed-out test just hangs instead of reporting it.
    with Session() as sweeper:
        sweeper.execute(text("SET LOCAL statement_timeout = '2s'"))
        seen = sweeper.query(PortfolioExposureReservation).filter_by(
            intent_id="pg-broker").one().state
        try:
            swept = exposure.expire_stale(sweeper, now=TR + timedelta(seconds=600))
            sweeper.commit()
            blocked = False
        except Exception as exc:                      # noqa: BLE001
            sweeper.rollback()
            swept, blocked = 0, True
            block_error = type(exc).__name__
    worker.rollback()                              # the crash, after the broker accepted
with Session() as s:
    row = s.query(PortfolioExposureReservation).filter_by(intent_id="pg-broker").one()
    R["crash_broker_boundary"] = {
        "state_seen_by_other_worker": seen,
        "swept_by_other_worker": swept,
        "final_state": row.state,
        "capacity_after_crash": exposure.active_reserved_value(s, PFID, "Energy", now=TR),
        # If the sweeper saw `reserved` and expired it, `committing` protected nothing at this
        # boundary: it was never durable when it mattered.
        "sweeper_blocked_on_row_lock": blocked,
        # Two different mechanisms could protect capacity here. Distinguish them:
        #   visibility  — the sweeper READ `committing` and declined to sweep it
        #   row lock    — the sweeper could not touch the row at all while the entry was open
        "protected_by_visibility": seen == "committing" and swept == 0 and not blocked,
        "protected_by_row_lock": blocked,
    }

print("===PROBE_JSON===")
print(json.dumps(R, indent=2, default=str))
