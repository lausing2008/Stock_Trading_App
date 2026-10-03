"""Broker lifecycle under REAL PostgreSQL concurrency — the acceptance the SQLite probe could not meet.

WHY POSTGRESQL AND WHY THREADS. The earlier probe established SF-01 SEQUENTIALLY in SQLite:
select, let a closure commit, then claim. That shows the predicate is re-evaluated, but it never
exercises the case the dispatcher actually meets in production — two connections contending for
the SAME ROW at the SAME TIME, where one UPDATE blocks on the other's row lock and then must
re-evaluate its WHERE clause against what the winner committed. SQLite cannot demonstrate that:
it serialises writers at the database level, so the interleaving under test never occurs.

Each scenario below runs against a real PostgreSQL 15 (production runs 15) with one session per
thread and explicit barriers, so the interleaving is forced rather than hoped for.

OUTPUT IS A MEASUREMENT. Scenarios that reveal gaps are findings to report, not failures to
paper over. Run with STOCKAI_RACE_DB set to a throwaway database.
"""
import json
import os
import pathlib
import sys
import threading
import types
from datetime import datetime, date, timedelta, timezone
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "market-data"))

for m in ["redis", "httpx", "structlog", "apscheduler", "apscheduler.schedulers",
          "apscheduler.schedulers.background", "apscheduler.triggers",
          "apscheduler.triggers.cron", "yfinance"]:
    sys.modules.setdefault(m, MagicMock())

import common                                                        # noqa: E402
_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(
    database_url=os.environ["STOCKAI_RACE_DB"], admin_password=None,
    jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg
common.config = _cfg

from sqlalchemy import create_engine, select, text, update           # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402
from db.models import (Base, BrokerConnection, PaperPortfolio,       # noqa: E402
                      PaperTrade, User)
from src.services import broker_submission as bs                     # noqa: E402

ENGINE = create_engine(os.environ["STOCKAI_RACE_DB"], pool_size=10, max_overflow=10)
Session = sessionmaker(bind=ENGINE)
R: dict = {}
PF = 1

#: The scenarios below are about the LIFECYCLE, so they hold the price still at the sized entry
#: price; drift gets its own scenario (R9).
#: Quotes are (price, as_of) pairs: a bare number carries no freshness evidence and is refused.
AT_ENTRY = lambda t: (float(t.entry_price), AS_OF)                   # noqa: E731

#: EVERY scenario that goes through `submit_pending` pins `now` to just after the fixture's
#: entry_time. Without this the intent-expiry control blocks the dispatch on wall-clock age and
#: every scenario reports zero provider calls — passing for a reason that has nothing to do with
#: the property under test. A control doing the work of the assertion is a false green.
AS_OF = datetime(2026, 10, 2, 13, 40)
CLOCK = lambda: AS_OF                                                # noqa: E731


def reset():
    """One portfolio, one open trade with a committed `pending` intent."""
    with Session() as s:
        s.execute(text("TRUNCATE paper_trades, paper_portfolios, broker_connections, users "
                       "RESTART IDENTITY CASCADE"))
        # A genuinely broker-LINKED portfolio, because the link is now part of eligibility.
        s.add(User(id=1, username="race", email="race@example.invalid", password_hash="x"))
        s.flush()
        s.add(BrokerConnection(id=1, user_id=1, name="race", broker_type="etrade_sandbox",
                               config={}, is_active=True, is_authorized=True))
        s.flush()
        s.add(PaperPortfolio(id=PF, name="race", initial_capital=100_000.0,
                             current_cash=100_000.0, config={}, broker_connection_id=1))
        s.flush()
        t = PaperTrade(portfolio_id=PF, symbol="AAPL", trading_style="SWING",
                       entry_date=date(2026, 10, 2), entry_time=datetime(2026, 10, 2, 13, 35),
                       entry_price=100.0, shares=10.0, stop_loss=95.0, take_profit=110.0,
                       current_stop=95.0, stage="open")
        bs.mark_pending(t, path="deferred")
        s.add(t)
        s.commit()
        return t.id


def _close(trade_id, *, hold_lock_until=None, released=None):
    """The closure path: exactly what every real exit does to the row — set stage='closed'."""
    with Session() as s:
        s.execute(update(PaperTrade).where(PaperTrade.id == trade_id)
                  .values(stage="closed", exit_time=datetime(2026, 10, 2, 14, 0),
                          exit_price=101.0, exit_reason="manual"))
        if hold_lock_until is not None:
            released.set()              # the row lock is held from here
            hold_lock_until.wait(10)    # ...until the test says to let go
        s.commit()


# ─────────────────────────────────────────────────────────────────────────────
# R1 — close COMMITS BEFORE the claim. The provider must never be contacted.
# ─────────────────────────────────────────────────────────────────────────────
def r1_close_wins_before_claim():
    tid = reset()
    calls = []
    with Session() as d:
        rows = bs.claimable(d, portfolio_id=PF)          # dispatcher has selected the row
        _close(tid)                                      # ...and the closure commits first
        out = bs.submit_pending(d, place=lambda s, t, p: calls.append(t.id),
                                commit=d.commit, quote=AT_ENTRY, portfolio_id=PF,
                                now=AS_OF, clock=CLOCK)
    with Session() as s:
        row = s.get(PaperTrade, tid)
        R["r1_close_wins_before_claim"] = {
            "selected_by_dispatcher": [t.id for t in rows],
            "provider_calls": len(calls),
            "result": out,
            "final_stage": row.stage,
            "final_submission_state": row.broker_submission_state,
            "attempts": row.broker_submit_attempts,
            "passes": len(calls) == 0 and row.broker_submission_state == bs.PENDING,
        }


# ─────────────────────────────────────────────────────────────────────────────
# R2 — the real PostgreSQL contention: the closure holds an UNCOMMITTED row lock,
#      the claim blocks on it, and must re-evaluate after the closure commits.
#      This is the interleaving SQLite cannot produce.
# ─────────────────────────────────────────────────────────────────────────────
def r2_claim_blocks_on_uncommitted_close():
    tid = reset()
    calls, verdict = [], {}
    lock_taken, let_go = threading.Event(), threading.Event()

    closer = threading.Thread(target=_close, args=(tid,),
                              kwargs={"hold_lock_until": let_go, "released": lock_taken})
    closer.start()
    lock_taken.wait(10)                                  # closure now holds the row lock

    def dispatch():
        with Session() as d:
            t0 = datetime.now(timezone.utc)
            verdict["result"] = bs.submit_pending(
                d, place=lambda s, t, p: calls.append(t.id), commit=d.commit,
                quote=AT_ENTRY, portfolio_id=PF, now=AS_OF, clock=CLOCK)
            verdict["blocked_seconds"] = (datetime.now(timezone.utc) - t0).total_seconds()

    worker = threading.Thread(target=dispatch)
    worker.start()
    threading.Event().wait(1.0)                          # let the UPDATE actually block
    verdict["dispatcher_still_blocked_while_lock_held"] = worker.is_alive()
    let_go.set()
    closer.join(10); worker.join(10)

    with Session() as s:
        row = s.get(PaperTrade, tid)
        R["r2_claim_blocks_on_uncommitted_close"] = {
            **verdict,
            "provider_calls": len(calls),
            "final_stage": row.stage,
            "final_submission_state": row.broker_submission_state,
            "passes": len(calls) == 0 and row.stage == "closed"
                      and row.broker_submission_state == bs.PENDING,
        }


# ─────────────────────────────────────────────────────────────────────────────
# R3 — two dispatchers, one row. Exactly one provider call.
# ─────────────────────────────────────────────────────────────────────────────
def r3_two_dispatchers_one_row():
    tid = reset()
    calls, results = [], []
    lock, gate = threading.Lock(), threading.Barrier(2)

    def dispatch():
        with Session() as d:
            rows = bs.claimable(d, portfolio_id=PF)
            gate.wait(10)                                # both hold the same stale selection
            for t in rows:
                if bs.begin_submission(d, t):
                    d.commit()
                    with lock:
                        calls.append(t.id)
                    bs.settle(d, t, outcome=bs.SUBMITTED)
                    d.commit()
                    with lock:
                        results.append("claimed")
                else:
                    with lock:
                        results.append("lost_race")

    ts = [threading.Thread(target=dispatch) for _ in range(2)]
    [t.start() for t in ts]; [t.join(15) for t in ts]
    with Session() as s:
        row = s.get(PaperTrade, tid)
        R["r3_two_dispatchers_one_row"] = {
            "provider_calls": len(calls),
            "outcomes": sorted(results),
            "attempts": row.broker_submit_attempts,
            "final_submission_state": row.broker_submission_state,
            "passes": len(calls) == 1 and sorted(results) == ["claimed", "lost_race"]
                      and row.broker_submit_attempts == 1,
        }


# ─────────────────────────────────────────────────────────────────────────────
# R4 — the CLAIM wins. A real order may now exist. What does closure do about it?
# ─────────────────────────────────────────────────────────────────────────────
def r4_claim_wins_then_close():
    tid = reset()
    with Session() as d:
        t = bs.claimable(d, portfolio_id=PF)[0]
        claimed = bs.begin_submission(d, t)
        d.commit()                                       # durable: a call is about to happen
    # The real closure paths now stamp the disposition before setting stage='closed'; this
    # mirrors what `record_closure_disposition` does at each of those four sites.
    with Session() as c:
        row = c.get(PaperTrade, tid)
        disposition = bs.record_closure_disposition(row, actor="probe_exit")
        c.commit()
    _close(tid)
    with Session() as s:
        row = s.get(PaperTrade, tid)
        R["r4_claim_wins_then_close"] = {
            "claimed": claimed,
            "final_stage": row.stage,
            "final_submission_state": row.broker_submission_state,
            "retains_reserved_exposure": bs.retains_reserved_exposure(row),
            "confirmed_broker_fill": bs.confirmed_broker_fill(row),
            "appears_in_needs_reconciliation": tid in [r.id for r in bs.needs_reconciliation(s)],
            "closure_recorded_broker_exposure": bool(row.broker_error),
            "closure_disposition": disposition,
            "passes": (row.broker_error or "").startswith("closed_with_open_broker_intent")
                      and bs.retains_reserved_exposure(row)
                      and tid in [r.id for r in bs.needs_reconciliation(s)],
        }


# ─────────────────────────────────────────────────────────────────────────────
# R5 — a `submitting` row must never be re-claimed; it may be a REAL order.
# ─────────────────────────────────────────────────────────────────────────────
def r5_submitting_never_reclaimed():
    tid = reset()
    calls = []
    with Session() as d:
        t = bs.claimable(d, portfolio_id=PF)[0]
        bs.begin_submission(d, t); d.commit()            # worker dies here, mid-call
    with Session() as d2:
        out = bs.submit_pending(d2, place=lambda s, t, p: calls.append(t.id),
                                commit=d2.commit, quote=AT_ENTRY, portfolio_id=PF,
                                now=AS_OF, clock=CLOCK)
    with Session() as s:
        R["r5_submitting_never_reclaimed"] = {
            "second_dispatcher_claimed": out["claimed"],
            "provider_calls": len(calls),
            "in_needs_reconciliation": tid in [r.id for r in bs.needs_reconciliation(s)],
            "passes": out["claimed"] == 0 and len(calls) == 0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# R6 — attempt cap under concurrency: never more provider calls than the cap.
# ─────────────────────────────────────────────────────────────────────────────
def r6_attempt_cap_under_concurrency():
    tid = reset()
    calls = []

    def boom(s, t, p):
        calls.append(t.id)
        raise TimeoutError("provider did not answer")

    for _ in range(bs.MAX_SUBMIT_ATTEMPTS + 2):
        with Session() as d:
            # `unknown` is terminal, so re-arm to `pending` the way a reconciliation would, to
            # isolate what the CAP does rather than what terminality does.
            row = d.get(PaperTrade, tid)
            if row.broker_submission_state == bs.UNKNOWN:
                row.broker_submission_state = bs.PENDING
                d.commit()
            bs.submit_pending(d, place=boom, commit=d.commit, quote=AT_ENTRY,
                              portfolio_id=PF, now=AS_OF, clock=CLOCK)
    with Session() as s:
        row = s.get(PaperTrade, tid)
        R["r6_attempt_cap_under_concurrency"] = {
            "provider_calls": len(calls),
            "attempts_recorded": row.broker_submit_attempts,
            "cap": bs.MAX_SUBMIT_ATTEMPTS,
            "passes": len(calls) <= bs.MAX_SUBMIT_ATTEMPTS,
        }


# ─────────────────────────────────────────────────────────────────────────────
# R7 — INTENT EXPIRY / PRICE DRIFT. An intent recorded at 09:45 and dispatched
#      hours later is a real order at a price nobody approved.
# ─────────────────────────────────────────────────────────────────────────────
def r7_stale_intent_dispatched():
    tid = reset()
    calls = []
    with Session() as s:                                 # age the intent by six hours
        row = s.get(PaperTrade, tid)
        row.entry_time = datetime(2026, 10, 2, 7, 35)
        s.commit()
    with Session() as d:
        bs.submit_pending(d, place=lambda s, t, p: calls.append(
            {"id": t.id, "entry_price": float(t.entry_price)}),
            commit=d.commit, quote=AT_ENTRY, portfolio_id=PF,
            now=datetime(2026, 10, 2, 13, 35), clock=lambda: datetime(2026, 10, 2, 13, 35))
    with Session() as s:
        row = s.get(PaperTrade, tid)
        R["r7_stale_intent_dispatched"] = {
            "provider_calls": len(calls),
            "intent_age_hours": 6.0,
            "blocked_reason": row.broker_error,
            "still_pending": row.broker_submission_state == bs.PENDING,
            "attempts_unburned": row.broker_submit_attempts == 0,
            "listed_as_expired": tid in [t.id for t in bs.expired_intents(
                s, now=datetime(2026, 10, 2, 13, 35))],
            "passes": len(calls) == 0 and row.broker_submit_attempts == 0,
        }


# ─────────────────────────────────────────────────────────────────────────────
# R8 — the broker LINK is not part of eligibility. Unlink the portfolio after the
#      intent is recorded and the dispatcher still submits.
# ─────────────────────────────────────────────────────────────────────────────
def r8_unlinked_portfolio_still_dispatches():
    tid = reset()
    calls = []
    with Session() as s:
        pf = s.get(PaperPortfolio, PF)
        pf.broker_connection_id = None                   # the link is gone...
        s.commit()
    with Session() as d:
        bs.submit_pending(d, place=lambda s, t, p: calls.append(t.id),
                          commit=d.commit, quote=AT_ENTRY, portfolio_id=PF, now=AS_OF, clock=CLOCK)
    R["r8_unlinked_portfolio_still_dispatches"] = {
        "provider_calls": len(calls),
        # Checked against the COMPILED SQL, not the repr of the clause list: the repr of a
        # correlated subquery does not name the column it filters on.
        "eligibility_consults_broker_link": "broker_connection_id" in str(
            select(PaperTrade).where(*bs._eligibility())),
        "passes": len(calls) == 0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# R9 — PRICE DRIFT. The position was sized at 100; the market is now elsewhere.
# ─────────────────────────────────────────────────────────────────────────────
def r9_price_drift_and_unverifiable_quote():
    out = {}
    for label, q in (("at_entry", lambda t: (100.0, AS_OF)),
                     ("drift_up_2pct", lambda t: (102.0, AS_OF)),
                     ("drift_down_2pct", lambda t: (98.0, AS_OF)),
                     ("inside_tolerance", lambda t: (100.5, AS_OF)),
                     ("no_quote", lambda t: None),
                     ("nan_quote", lambda t: (float("nan"), AS_OF)),
                     ("bare_number", lambda t: 100.0),
                     ("stale_quote", lambda t: (100.0, AS_OF - timedelta(minutes=5))),
                     ("raising_source", lambda t: (_ for _ in ()).throw(ConnectionError("down")))):
        tid = reset()
        calls = []
        with Session() as d:
            bs.submit_pending(d, place=lambda s, t, p: calls.append(t.id),
                              commit=d.commit, quote=q, portfolio_id=PF,
                              now=datetime(2026, 10, 2, 13, 40), clock=CLOCK)
        with Session() as s:
            row = s.get(PaperTrade, tid)
            out[label] = {"provider_calls": len(calls), "reason": row.broker_error}
    R["r9_price_drift_and_unverifiable_quote"] = {
        **out,
        "passes": (out["at_entry"]["provider_calls"] == 1
                   and out["inside_tolerance"]["provider_calls"] == 1
                   and all(out[k]["provider_calls"] == 0 for k in
                           ("drift_up_2pct", "drift_down_2pct", "no_quote", "nan_quote",
                            "bare_number", "stale_quote", "raising_source"))),
    }


# ─────────────────────────────────────────────────────────────────────────────
# R10 — the controls were checked BEFORE a claim that then blocked on a row lock.
#       By the time the claim returns, the evidence they passed on has expired.
# ─────────────────────────────────────────────────────────────────────────────
def r10_quote_ages_while_the_claim_waits():
    tid = reset()
    calls = []
    lock_taken, let_go = threading.Event(), threading.Event()

    # A second session holds the row lock WITHOUT closing the trade, so the claim blocks and
    # then SUCCEEDS — the case where the pre-claim checks stay valid-looking but go stale.
    def hold():
        with Session() as s:
            s.execute(update(PaperTrade).where(PaperTrade.id == tid).values(symbol="AAPL"))
            lock_taken.set()
            let_go.wait(10)
            s.commit()

    holder = threading.Thread(target=hold); holder.start()
    lock_taken.wait(10)
    verdict = {}

    def dispatch():
        with Session() as d:
            # The clock has advanced past the quote's freshness bound by the time the claim
            # returns; the quote itself is still stamped at the batch's reference instant.
            verdict["result"] = bs.submit_pending(
                d, place=lambda s, t, p: calls.append(t.id), commit=d.commit,
                quote=lambda t: (float(t.entry_price), AS_OF), portfolio_id=PF,
                now=AS_OF, clock=lambda: AS_OF + timedelta(minutes=5))

    worker = threading.Thread(target=dispatch); worker.start()
    threading.Event().wait(0.8)
    let_go.set(); holder.join(10); worker.join(15)

    with Session() as s:
        row = s.get(PaperTrade, tid)
        R["r10_quote_ages_while_the_claim_waits"] = {
            "result": verdict.get("result"),
            "provider_calls": len(calls),
            "final_submission_state": row.broker_submission_state,
            "attempts_after_release": row.broker_submit_attempts,
            "reason": row.broker_error,
            # Claimed, then handed back UNSENT because the evidence expired while queued — and
            # the attempt is returned, because nobody contacted the broker.
            "passes": (len(calls) == 0
                       and row.broker_submission_state == bs.PENDING
                       and row.broker_submit_attempts == 0
                       and (row.broker_error or "").startswith("claim released unsent")),
        }


def main():
    for fn in (r1_close_wins_before_claim, r2_claim_blocks_on_uncommitted_close,
               r3_two_dispatchers_one_row, r4_claim_wins_then_close,
               r5_submitting_never_reclaimed, r6_attempt_cap_under_concurrency,
               r7_stale_intent_dispatched, r8_unlinked_portfolio_still_dispatches,
               r9_price_drift_and_unverifiable_quote,
               r10_quote_ages_while_the_claim_waits):
        fn()
    R["_meta"] = {"engine": str(ENGINE.url).split("@")[-1],
                  "server_version": Session().execute(text("show server_version")).scalar(),
                  "scope": "throwaway local database; no production access or writes"}
    print(json.dumps(R, indent=2, default=str))


if __name__ == "__main__":
    main()
