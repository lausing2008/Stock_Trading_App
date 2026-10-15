"""Plan mode against a REAL model: does it answer forecast / playbook questions from the plan
lines, and how often is it refused?

MANUAL ONLY — it spends money. Not collected by pytest. Reads ANTHROPIC_API_KEY from the
environment or the repo-root .env (never printed).

    python tests/_copilot_plan_eval.py --model claude-sonnet-4-6 --reps 1 --out plan.json

SYNTHETIC fixtures in a throwaway SQLite file: TESTCO (rising, noisy, 260 bars, calls on all four
horizons, earnings in 6 days, a thin per-stock record), MU (falling, SELL), STALECO (12-day-old
bars, 70 of them — too few for a year range or past-move ranges). The questions below were
written BEFORE the first run, so that run is held-out for plan mode; anything changed because
of it makes later runs tuned.
"""
import argparse
import asyncio
import json
import math
import os
import pathlib
import sys
import tempfile
import time
import types
from datetime import datetime, timedelta
from unittest.mock import MagicMock

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "research-engine"))
for m in ["redis", "structlog", "yfinance", "pandas"]:
    sys.modules.setdefault(m, MagicMock())
import common                                                        # noqa: E402
_cfg = types.ModuleType("common.config")
_DB = "sqlite:///" + str(pathlib.Path(tempfile.mkdtemp()) / "plan_eval.db")
_cfg.get_settings = lambda: types.SimpleNamespace(
    database_url=_DB, admin_password=None, jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg
common.config = _cfg

from sqlalchemy import create_engine                                 # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402
from db.models import (Base, EarningsEvent, Exchange, Market, Price,  # noqa: E402
                       Signal, SignalHorizon, SignalOutcomeHorizon, SignalType, Stock,
                       TimeFrame, User)
from src.copilot import grounding as G                               # noqa: E402
from src.copilot import intent as I                                  # noqa: E402
from src.copilot import llm as L                                     # noqa: E402
from src.copilot import service as C                                 # noqa: E402
from src.copilot.packet import build_packet                          # noqa: E402

PRICES = {"claude-sonnet-4-6": (3.00, 15.00), "claude-sonnet-5-5": (2.00, 10.00),
          "claude-haiku-5-5": (0.10, 0.50), "claude-opus-5-5": (4.00, 20.00)}
NOW = datetime.utcnow().replace(microsecond=0, second=0, minute=0)
os.environ.setdefault("COPILOT_DAILY_ANSWERS_PER_USER", "1000")

QUESTIONS = [
    ("p01", "TESTCO", "forecast", "What's your prediction for TESTCO over the next few days?"),
    ("p02", "TESTCO", "forecast", "Where will TESTCO be next week?"),
    ("p03", "TESTCO", "entry", "I want to buy TESTCO. What's the textbook plan?"),
    ("p04", "TESTCO", "entry_risk", "I can risk $400 on this trade. How many shares and where's my stop?"),
    ("p05", "TESTCO", "holding_gain", "I bought 200 shares at 112.40. What should I do now?"),
    ("p06", "TESTCO", "holding_loss", "I'm holding 50 shares from 140. Should I sell?"),
    ("p07", "TESTCO", "holding_no_entry", "I own some TESTCO, what's the plan?"),
    ("p08", "TESTCO", "track_record", "How accurate have the signals been for this stock?"),
    ("p09", "MU", "sell_call", "Should I buy MU here?"),
    ("p10", "TESTCO", "options_refused", "I bought calls at 4.20, what now?"),
    ("p11", "TESTCO", "earnings", "Is it a good time to buy before earnings?"),
    ("p12", "TESTCO", "probability", "What are the odds it goes up next week?"),
    ("p13", "STALECO", "stale_thin", "What's the outlook for STALECO?"),
    ("p14", "TESTCO", "two_entries", "I bought 100 at 105 and 100 at 115, what's my stop?"),
]


def seed(Session):
    with Session() as s:
        s.add(User(id=1, username="eval", password_hash="x"))
        specs = [("TESTCO", 80.0, 0.12, 0, 260), ("MU", 160.0, -0.15, 0, 260),
                 ("STALECO", 40.0, -0.02, 12, 70)]
        st, pid = {}, 0
        for sym, base, slope, age, n in specs:
            o = Stock(symbol=sym, name=sym.title(), market=Market.US, exchange=Exchange.NASDAQ,
                      currency="USD", sector="Technology")
            s.add(o); s.flush(); st[sym] = o
            for i in range(n):
                pid += 1
                px = base + slope * (n - i) + 3.0 * math.sin(i / 3.0)
                s.add(Price(id=pid, stock_id=o.id, ts=NOW - timedelta(days=i + age),
                            timeframe=TimeFrame.D1, open=px - 0.3, high=px + 1.2,
                            low=px - 1.2, close=px, volume=1_000_000 + (i % 7) * 50_000))
        sid = [0]

        def sig(sym, h, d, hours, conf):
            sid[0] += 1
            s.add(Signal(id=sid[0], stock_id=st[sym].id, ts=NOW - timedelta(hours=hours),
                         signal=SignalType[d], horizon=SignalHorizon[h], confidence=conf,
                         source="signal-engine"))
        sig("TESTCO", "SWING", "BUY", 2, 61); sig("TESTCO", "SHORT", "SELL", 4, 52)
        sig("TESTCO", "LONG", "BUY", 6, 58); sig("TESTCO", "GROWTH", "HOLD", 6, 40)
        sig("MU", "SWING", "SELL", 3, 48); sig("MU", "LONG", "SELL", 3, 55)
        sig("STALECO", "SWING", "BUY", 300, 50)
        s.add(EarningsEvent(id=1, stock_id=st["TESTCO"].id,
                            report_date=(NOW + timedelta(days=6)).date()))
        oid = [0]
        # Cohorts across all stocks (mixed), plus a THIN record for TESTCO itself.
        plan = [("TESTCO", "SWING", "BUY", 5, 300, 0.46), ("TESTCO", "SWING", "BUY", 10, 220, 0.48),
                ("TESTCO", "LONG", "BUY", 5, 400, 0.44), ("TESTCO", "LONG", "BUY", 10, 320, 0.45),
                ("TESTCO", "SHORT", "SELL", 5, 150, 0.38), ("TESTCO", "SHORT", "SELL", 10, 120, 0.40),
                ("MU", "SWING", "SELL", 5, 250, 0.41), ("MU", "SWING", "SELL", 10, 200, 0.43),
                ("MU", "LONG", "SELL", 5, 260, 0.39), ("MU", "LONG", "SELL", 10, 210, 0.40),
                ("STALECO", "SWING", "BUY", 5, 25, 0.5)]
        for sym, h, d, w, n, rate in plan:
            for k in range(n):
                oid[0] += 1
                sid[0] += 1
                own = k < 5            # the first 5 rows of each cohort belong to the stock
                stock = st[sym] if own else st["MU" if sym == "TESTCO" else "TESTCO"]
                ok = (k % 100) < rate * 100
                ret = (0.025 if ok else -0.02) * (1 if d == "BUY" else -1)
                sdate = (NOW - timedelta(days=20 + k % 60)).date()
                s.add(Signal(id=sid[0], stock_id=stock.id, ts=NOW - timedelta(days=30 + k % 60),
                             signal=SignalType[d], horizon=SignalHorizon[h], confidence=50,
                             source="signal-engine"))
                s.flush()
                s.add(SignalOutcomeHorizon(
                    id=oid[0], signal_id=sid[0], stock_id=stock.id, symbol=stock.symbol,
                    horizon=h, signal_direction=d, signal_date=sdate, window_days=w,
                    entry_date=sdate, entry_price=100.0, target_date=sdate + timedelta(days=w),
                    exit_date=sdate + timedelta(days=w), exit_price=100 * (1 + ret),
                    pct_return=ret, is_correct=ok, status="resolved",
                    resolved_at=NOW - timedelta(days=1),
                    evidence_status="unverified" if k % 3 else None))
        s.commit()


def api_key() -> str:
    k = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if k:
        return k
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("no ANTHROPIC_API_KEY")


class Recorder:
    def __init__(self):
        self.drafts = []

    async def __call__(self, provider, key, model, messages):
        t = time.monotonic()
        reply = await L.call(provider, key, model, messages)
        self.drafts.append({"raw": reply.text, "usage": reply.usage,
                            "stop_reason": reply.stop_reason,
                            "ms": int((time.monotonic() - t) * 1000)})
        return reply


async def run(model: str, reps: int, out_path: pathlib.Path):
    eng = create_engine(_DB)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    seed(Session)
    key = api_key()
    rows = []
    for rep in range(reps):
        for qid, sym, cat, q in QUESTIONS:
            rec = Recorder()
            t0 = time.monotonic()
            err = None
            try:
                out = await C.answer(C.TurnRequest(
                    username="eval", symbol=sym, question=q,
                    request_id=f"plan-{model[-8:]}-{rep}-{qid}", conversation_id=None,
                    provider="claude", model=model, api_key=key, uses_shared_key=False),
                    session_factory=Session, llm_call=rec)
                msg = out["message"]
            except C.CopilotError as e:
                msg, err = {}, f"{e.status} {e.message}"
            wall = int((time.monotonic() - t0) * 1000)
            pi_ = I.parse_position(q)
            with Session() as s:
                pk = build_packet(s, sym, plan_request=pi_ if pi_.kind else None)
            first = G.check(G.parse_draft(rec.drafts[0]["raw"]), pk) if rec.drafts else None
            tin = sum((d["usage"] or {}).get("input_tokens") or 0 for d in rec.drafts)
            tout = sum((d["usage"] or {}).get("output_tokens") or 0 for d in rec.drafts)
            pin, pout = PRICES.get(model, (None, None))
            cited = [r["key"] for r in msg.get("evidence_refs", [])]
            rows.append({
                "rep": rep, "qid": qid, "symbol": sym, "category": cat, "question": q,
                "plan_kind": pi_.kind, "error": err, "status": msg.get("status"),
                "attempts": len(rec.drafts),
                "first_draft_accepted": bool(first and first.accepted),
                "first_draft_reasons": [] if not first or first.accepted else first.reasons,
                "final_reasons": msg.get("rejection_reasons", []),
                "cited": cited,
                "cites_rules": any(k.startswith("rule.") for k in cited),
                "cites_records": any(k.startswith(("record.", "baseline.")) for k in cited),
                "cites_calls": any(k.startswith("call.") for k in cited),
                "latency_ms": wall, "input_tokens": tin, "output_tokens": tout,
                "cost_usd": round(tin / 1e6 * pin + tout / 1e6 * pout, 5) if pin else None,
                "answer": msg.get("content"), "limitations": msg.get("limitations"),
                "raw_drafts": [d["raw"] for d in rec.drafts],
            })
            print(f"{model} rep{rep} {qid} {cat:17s} {msg.get('status') or err} "
                  f"attempts={len(rec.drafts)} {wall}ms", file=sys.stderr)
    out_path.write_text(json.dumps({"model": model, "reps": reps, "run_at": NOW.isoformat(),
                                    "rows": rows}, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    asyncio.run(run(a.model, a.reps, pathlib.Path(a.out)))
