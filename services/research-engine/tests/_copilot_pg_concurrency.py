"""Copilot concurrency and startup migration — PostgreSQL ONLY.

SQLite serialises writers, so a race that is real on PostgreSQL cannot occur on it; these
scenarios are therefore meaningless there and the script refuses to run without
STOCKAI_INTEL_DB pointing at a THROWAWAY PostgreSQL database (it drops the public schema).

Run:  STOCKAI_INTEL_DB=postgresql://... python tests/_copilot_pg_concurrency.py
Prints one JSON object of measurements. `test_copilot_pg.py` asserts them when the variable
is set and skips otherwise.
"""
import asyncio
import itertools
import json
import os
import pathlib
import sys
import threading
import time
import types
from datetime import datetime
from unittest.mock import MagicMock

URL = os.environ.get("STOCKAI_INTEL_DB", "")
if not URL.startswith("postgresql"):
    sys.exit("STOCKAI_INTEL_DB must name a throwaway PostgreSQL database")

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "research-engine"))
for m in ["redis", "httpx", "structlog", "yfinance", "pandas"]:
    sys.modules.setdefault(m, MagicMock())
import common                                                        # noqa: E402
_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(
    database_url=URL, admin_password=None, jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg
common.config = _cfg

from sqlalchemy import create_engine, inspect, text                  # noqa: E402

R = {}

# ── 1. Startup migration on a FRESH database, twice ──────────────────────────
eng = create_engine(URL)
with eng.begin() as c:
    c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
from db import init_db, SessionLocal                                 # noqa: E402
t0 = time.monotonic()
init_db()
first_s = time.monotonic() - t0
init_db()                                                            # idempotent re-run
insp = inspect(eng)
cols = {c["name"] for c in insp.get_columns("copilot_messages")}
uqs = {u["name"]: u["column_names"] for u in insp.get_unique_constraints("copilot_messages")}
fks = {f["referred_table"]: f["options"].get("ondelete") for f in insp.get_foreign_keys("copilot_messages")}
R["migration"] = {
    "tables": sorted(t for t in insp.get_table_names() if t.startswith("copilot")),
    "has_claimed_at": "claimed_at" in cols,
    "unique": uqs.get("uq_copilot_msg_user_request_role"),
    "fk_ondelete": fks,
    "first_run_s": round(first_s, 1),
    "second_run_ok": True,
}

from db.models import (CopilotMessage, Exchange, Market, Price, Signal,  # noqa: E402
                       SignalHorizon, SignalType, Stock, TimeFrame, User)
from common import llm_budget                                        # noqa: E402
from src.copilot import llm as L                                     # noqa: E402
from src.copilot import service as C                                 # noqa: E402

NOW = datetime.utcnow().replace(microsecond=0)
with SessionLocal() as s:
    s.add_all([User(id=1, username="alice", password_hash="x"),
               User(id=2, username="bob", password_hash="x")])
    st = Stock(symbol="TESTCO", name="Test", market=Market.US, exchange=Exchange.NASDAQ,
               currency="USD")
    s.add(st); s.flush()
    from datetime import timedelta
    for i in range(60):
        s.add(Price(id=i + 1, stock_id=st.id, ts=NOW - timedelta(days=i), timeframe=TimeFrame.D1,
                    open=100, high=101, low=99, close=100 + i * 0.1, volume=1e6))
    s.add(Signal(id=1, stock_id=st.id, ts=NOW - timedelta(hours=1), signal=SignalType.BUY,
                 horizon=SignalHorizon.SWING, confidence=55.0, source="signal-engine"))
    s.commit()

GOOD = json.dumps({"answer": "Close {{price.close}}.", "limitations": []})


class SlowModel:
    """Thread-safe call counter; each call holds for `delay` so racers overlap it."""
    def __init__(self, delay=1.5, fail=False):
        self.delay, self.fail, self.n, self.lock = delay, fail, 0, threading.Lock()

    async def __call__(self, *a):
        with self.lock:
            self.n += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise L.LlmError("provider down", status=500)
        return L.LlmReply(text=GOOD, model="slow", usage={"input_tokens": 900, "output_tokens": 50})


def turn(rid, user="alice"):
    return C.TurnRequest(username=user, symbol="TESTCO", question="price?", request_id=rid,
                         conversation_id=None, provider="claude", model="m", api_key="k",
                         uses_shared_key=True)


def race(n, make_coro):
    """Start n threads behind a barrier, each with its own event loop. Returns outcomes."""
    barrier, out, lock = threading.Barrier(n), [], threading.Lock()

    def worker():
        barrier.wait()
        try:
            r = asyncio.new_event_loop().run_until_complete(make_coro())
            res = ("ok", r["replayed"], r["message"]["id"])
        except C.CopilotError as e:
            res = ("err", e.status, None)
        with lock:
            out.append(res)
    ts = [threading.Thread(target=worker) for _ in range(n)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return out


def count(where):
    with SessionLocal() as s:
        return s.execute(text(f"SELECT count(*) FROM copilot_messages WHERE {where}")).scalar()


# ── 2. Eight simultaneous FIRST requests with one request id ─────────────────
m = SlowModel()
out = race(8, lambda: C.answer(turn("race-first-0001"), session_factory=SessionLocal,
                               llm_call=m, budget=llm_budget))
later = asyncio.new_event_loop().run_until_complete(
    C.answer(turn("race-first-0001"), session_factory=SessionLocal, llm_call=m, budget=llm_budget))
R["same_id_first"] = {
    "model_calls": m.n, "outcomes": sorted(str(o[:2]) for o in out),
    "assistant_rows": count("request_id='race-first-0001' AND role='assistant'"),
    "user_rows": count("request_id='race-first-0001' AND role='user'"),
    "later_replayed": later["replayed"],
}
with SessionLocal() as s:
    R["same_id_first"]["conversations"] = s.execute(text(
        "SELECT count(DISTINCT conversation_id) FROM copilot_messages "
        "WHERE request_id='race-first-0001'")).scalar()

# ── 3. Eight simultaneous RETRIES of a failed turn ───────────────────────────
try:
    asyncio.new_event_loop().run_until_complete(C.answer(
        turn("race-retry-0001"), session_factory=SessionLocal, llm_call=SlowModel(0, fail=True),
        budget=llm_budget))
except C.CopilotError:
    pass
m2 = SlowModel()
out2 = race(8, lambda: C.answer(turn("race-retry-0001"), session_factory=SessionLocal,
                                llm_call=m2, budget=llm_budget))
R["same_id_retry"] = {
    "model_calls": m2.n, "outcomes": sorted(str(o[:2]) for o in out2),
    "assistant_rows": count("request_id='race-retry-0001' AND role='assistant'"),
}

# ── 4. Budget ledger under concurrency, through the real service ─────────────
with SessionLocal() as s:
    s.execute(text("DELETE FROM llm_budget_day")); s.execute(text("DELETE FROM llm_reservations"))
    s.commit()
m3 = SlowModel(delay=1.0)
# Reservation size depends on the packet; take it from a dry reservation of the real request.
from src.copilot.packet import build_packet                          # noqa: E402
with SessionLocal() as s:
    pk = build_packet(s, "TESTCO")
real_text = "\n".join([L.user_turn(pk.model_view(), "price?")]) + L.SYSTEM_PROMPT
per_call = llm_budget.upper_bound_tokens(real_text, L.MAX_OUTPUT_TOKENS)
os.environ["LLM_BUDGET_COPILOT_CHAT"] = str(per_call * 3 + per_call // 2)
_ids = itertools.count()
_ids_lock = threading.Lock()


def _budget_turn():
    with _ids_lock:
        i = next(_ids)
    return C.answer(turn(f"budget-race-{i:04d}", user="bob"), session_factory=SessionLocal,
                    llm_call=m3, budget=llm_budget)


outs = race(10, _budget_turn)
with SessionLocal() as s:
    day_reserved = s.execute(text(
        "SELECT reserved FROM llm_budget_day WHERE scope='copilot_chat'")).scalar()
    open_res = s.execute(text(
        "SELECT count(*) FROM llm_reservations WHERE scope='copilot_chat' AND settled_at IS NULL"
    )).scalar() if "settled_at" in {c["name"] for c in insp.get_columns("llm_reservations")} else None
R["budget"] = {
    "per_call_reservation": per_call, "cap": int(os.environ["LLM_BUDGET_COPILOT_CHAT"]),
    "admitted": sum(1 for o in outs if o[0] == "ok"),
    "refused_429": sum(1 for o in outs if o == ("err", 429, None)),
    "model_calls": m3.n,
    "day_reserved_after_settle": day_reserved,
    "unsettled_reservations": open_res,
}
del os.environ["LLM_BUDGET_COPILOT_CHAT"]
R["_meta"] = {"server": eng.dialect.name}
print(json.dumps(R, default=str))
