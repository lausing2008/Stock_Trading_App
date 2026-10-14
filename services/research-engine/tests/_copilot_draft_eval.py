"""Copilot DRAFTS against a real model — FRESH cases, written for phase 2 and never tuned on.

MANUAL ONLY (spends money). End to end: a real model proposes, the copilot validates and binds
state, and confirmation saves through market-data's REAL route functions (loaded in-process
over the same SQLite fixtures), so a confirmed draft really becomes a price_alerts / watchlist /
signal_alerts row. Each case is scored against an expected draft (or the expectation that none
is proposed), then confirmed TWICE to show the second confirm creates nothing.

    python tests/_copilot_draft_eval.py --model claude-sonnet-4-6 --reps 2 --out out.json
"""
import argparse
import asyncio
import importlib
import importlib.util
import json
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _copilot_eval as E                                            # noqa: E402  (fixtures, key)

from fastapi import HTTPException, Response                          # noqa: E402
from sqlalchemy import create_engine, text                           # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402
from db.models import Base, User, Watchlist                          # noqa: E402
from src.copilot import drafts as D                                  # noqa: E402
from src.copilot import llm as L                                     # noqa: E402
from src.copilot import service as C                                 # noqa: E402


def _load(alias: str, service: str):
    base = E.ROOT / "services" / service / "src"
    spec = importlib.util.spec_from_file_location(alias, base / "__init__.py",
                                                  submodule_search_locations=[str(base)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules[alias] = pkg
    spec.loader.exec_module(pkg)
    return (importlib.import_module(f"{alias}.api.alerts"),
            importlib.import_module(f"{alias}.api.signal_alerts"),
            importlib.import_module(f"{alias}.api.watchlist"))


class InProcessMarket:
    """D.MarketClient's interface, served by market-data's real route functions."""
    def __init__(self, Session, username, mods):
        self.S, self.u = Session, username
        self.A, self.SA, self.W = mods

    def _user(self, s):
        return s.query(User).filter_by(username=self.u).one()

    def _call(self, fn):
        with self.S() as s:
            try:
                return fn(s, self._user(s))
            except HTTPException as e:
                raise D.MarketError(e.status_code, str(e.detail))

    @staticmethod
    def _d(x):
        return x.model_dump() if hasattr(x, "model_dump") else {
            k: getattr(x, k) for k in ("id", "symbol", "condition", "threshold", "triggered",
                                       "horizon", "alert_mode") if hasattr(x, k)}

    async def alerts(self):
        return [{**self._d(a), "condition": getattr(a.condition, "value", a.condition)}
                for a in self._call(lambda s, u: self.A.list_alerts(s, u))]

    async def signal_alerts(self):
        return [self._d(a) for a in self._call(lambda s, u: self.SA.list_signal_alerts(s, u))]

    async def watchlist_preview(self, symbol):
        return self._call(lambda s, u: self.W.preview_add(symbol, None, u, s))

    async def signal_preview(self, symbol, horizon, mode):
        return self._call(lambda s, u: self.SA.preview_signal_alert(symbol, horizon, mode, s, u))

    async def create_alert(self, body):
        def f(s, u):
            r = Response(); r.status_code = None
            a = self.A.create_alert(self.A.AlertCreate(**body), r, s, u)
            return {"id": a.id, "symbol": a.symbol, "threshold": a.threshold}
        return self._call(f)

    async def create_signal_alert(self, body):
        return self._d(self._call(lambda s, u: self.SA.create_signal_alert(
            self.SA.SignalAlertCreate(**body), s, u)))

    async def add_to_watchlist(self, symbol, expected_effects=None):
        return self._d(self._call(lambda s, u: self.W.add_to_watchlist(
            symbol, None, u, s, expected_effects=expected_effects)))


#: (id, question, expected kind or None, expected arguments subset)
CASES = [
    ("d01", "Alert me if it falls below its 50-day average", "price_alert",
     {"condition": "below", "threshold_key": "trend.sma50"}),
    ("d02", "Set an alert for when TESTCO goes above 160", "price_alert",
     {"condition": "above", "threshold": 160.0}),
    ("d03", "Add this to my watchlist", "watchlist_add", {}),
    ("d04", "Tell me when the swing signal changes", "signal_alert", {"horizon": "SWING"}),
    ("d05", "Alert me when it reaches the analyst price target", None, {}),
    ("d06", "What's the 50-bar average?", None, {}),
    ("d07", "Alert me if it drops 10%", None, {}),
    ("d08", "Put MSFT on my watchlist", None, {}),
]


async def main(model, reps, out):
    eng = create_engine(E._DB)
    Base.metadata.create_all(eng)
    Session = sessionmaker(bind=eng)
    E.seed(Session)
    with Session() as s:
        s.execute(text("UPDATE users SET email='eval@example.com' WHERE username='eval'"))
        s.add(Watchlist(user_id=1, name="Mine", trading_style="SWING")); s.commit()
    mods = _load("md_src", "market-data")
    market = InProcessMarket(Session, "eval", mods)
    key = E.api_key()
    rows = []
    for rep in range(reps):
        for cid, q, kind, exp in CASES:
            t0 = time.monotonic()
            res = await C.answer(C.TurnRequest(
                username="eval", symbol="TESTCO", question=q, request_id=f"de-{rep}-{cid}",
                conversation_id=None, provider="claude", model=model, api_key=key,
                uses_shared_key=False, market=market), session_factory=Session)
            m = res["message"]
            drafts = m.get("drafts", [])
            ok = (not drafts) if kind is None else (
                len(drafts) == 1 and drafts[0]["kind"] == kind
                and all(drafts[0]["arguments"].get(k) == v for k, v in exp.items()
                        if k != "threshold_key")
                and ("threshold_key" not in exp
                     or (drafts[0].get("threshold_source") or {}).get("key") == exp["threshold_key"]))
            saved = []
            for d in drafts:
                c1 = await C.confirm_draft(Session, "eval", m["id"], d["id"], market)
                c2 = await C.confirm_draft(Session, "eval", m["id"], d["id"], market)
                saved.append({"kind": d["kind"], "first": c1["draft"]["status"],
                              "second_replayed": c2["replayed"]})
            with Session() as s:
                counts = {t: s.execute(text(f"SELECT count(*) FROM {t}")).scalar()
                          for t in ("price_alerts", "signal_alerts", "watchlist_items")}
            rows.append({"rep": rep, "id": cid, "question": q, "expected": kind,
                         "status": m["status"], "drafts": drafts, "dropped": m.get("dropped_drafts"),
                         "correct": ok, "saved": saved, "rows_after": counts,
                         "answer": m["content"], "latency_ms": int((time.monotonic() - t0) * 1000)})
            print(f"{model} {rep} {cid} {m['status']} drafts={[d['kind'] for d in drafts]} "
                  f"correct={ok} saved={saved}", file=sys.stderr)
    pathlib.Path(out).write_text(json.dumps({"model": model, "reps": reps, "rows": rows},
                                            indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    asyncio.run(main(a.model, a.reps, a.out))
