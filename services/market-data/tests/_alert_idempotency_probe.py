"""D2 price-alert idempotency and D1 watchlist-add disclosure, through the REAL route functions.

SQLite by default; PostgreSQL when $ALERTS_PG_URL names a THROWAWAY database (its public schema is
dropped). Only PostgreSQL can show the two things that matter most:

  - the MIGRATION: `price_alerts` already exists in production, so the probe recreates that
    pre-migration shape (no idempotency columns, no index) and runs the real init_db();
  - genuinely SIMULTANEOUS saves with one key, serialised only by the partial unique index.

Prints one JSON object.
"""
import json
import os
import pathlib
import sys
import tempfile
import threading
import types
from unittest.mock import MagicMock

PG = os.environ.get("ALERTS_PG_URL", "")
URL = PG or "sqlite:///" + str(pathlib.Path(tempfile.mkdtemp()) / "alerts.db")
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "market-data"))
for m in ["redis", "yfinance"]:
    sys.modules.setdefault(m, MagicMock())
import common                                                        # noqa: E402
_cfg = types.ModuleType("common.config")


class _Settings(types.SimpleNamespace):
    def __getattr__(self, _n):
        return ""


_cfg.get_settings = lambda: _Settings(database_url=URL, admin_password=None, jwt_secret="x")
sys.modules["common.config"] = _cfg
common.config = _cfg

from fastapi import HTTPException, Response                          # noqa: E402
from sqlalchemy import create_engine, inspect, text                  # noqa: E402
from sqlalchemy.orm import sessionmaker                              # noqa: E402

R = {"_meta": {"server": "postgresql" if PG else "sqlite"}}
eng = create_engine(URL)
if PG:
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))

from db import models as M                                          # noqa: E402
M.Base.metadata.create_all(eng)

if PG:
    # Recreate PRODUCTION'S CURRENT SHAPE: the table exists without the new columns or index.
    with eng.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS uq_price_alerts_user_idempotency"))
        c.execute(text("ALTER TABLE price_alerts DROP COLUMN idempotency_key, "
                       "DROP COLUMN request_fingerprint"))
    before = {c["name"] for c in inspect(eng).get_columns("price_alerts")}
    from db import init_db                                            # noqa: E402
    init_db()
    init_db()                                                         # idempotent re-run
    insp = inspect(eng)
    after = {c["name"] for c in insp.get_columns("price_alerts")}
    idx = {i["name"]: i for i in insp.get_indexes("price_alerts")}
    R["migration"] = {
        "columns_before": sorted({"idempotency_key", "request_fingerprint"} & before),
        "columns_after": sorted({"idempotency_key", "request_fingerprint"} & after),
        "index_unique": bool(idx.get("uq_price_alerts_user_idempotency", {}).get("unique")),
    }

Session = sessionmaker(bind=eng)
from src.api import alerts as A                                     # noqa: E402
from src.api import watchlist as W                                   # noqa: E402

with Session() as s:
    alice = M.User(id=1, username="alice", password_hash="x", email="a@example.com")
    bob = M.User(id=2, username="bob", password_hash="x", email=None)
    s.add_all([alice, bob])
    st = M.Stock(symbol="TESTCO", name="T", market=M.Market.US, exchange=M.Exchange.NASDAQ,
                 currency="USD")
    s.add(st); s.commit()


def user(uid):
    with Session() as s:
        u = s.get(M.User, uid)
        s.expunge(u)
        return u


def create(uid, key, threshold=100.0, note=None):
    with Session() as s:
        resp = Response()
        resp.status_code = None   # as FastAPI injects it: unset, so the route's 201 applies
        try:
            out = A.create_alert(A.AlertCreate(symbol="TESTCO", condition="above",
                                               threshold=threshold, note=note,
                                               idempotency_key=key), resp, s, user(uid))
            return ("ok", resp.status_code or 201, out.id)
        except HTTPException as e:
            return ("err", e.status_code, None)


def count(where="1=1"):
    with Session() as s:
        return s.execute(text(f"SELECT count(*) FROM price_alerts WHERE {where}")).scalar()


# ── Sequential semantics ──
first = create(1, "draft-key-0001")
again = create(1, "draft-key-0001")
other = create(1, "draft-key-0001", threshold=105.0)
bob_same_key = create(2, "draft-key-0001")
nokey_a, nokey_b = create(1, None), create(1, None)
R["sequential"] = {
    "first": first[:2], "retry_same": again[:2], "same_id": first[2] == again[2],
    "same_key_different_alert": other[:2], "other_user_same_key": bob_same_key[:2],
    "no_key_still_creates_each_time": [nokey_a[0], nokey_b[0]],
    "rows_for_key_alice": count("user_id=1 AND idempotency_key='draft-key-0001'"),
}

# ── Simultaneous saves with one key ──
outs, lock = [], threading.Lock()
barrier = threading.Barrier(8)


def worker():
    barrier.wait()
    r = create(1, "race-key-0001")
    with lock:
        outs.append(r)


ts = [threading.Thread(target=worker) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
R["simultaneous"] = {
    "rows": count("idempotency_key='race-key-0001'"),
    "all_ok": all(o[0] == "ok" for o in outs),
    "one_id": len({o[2] for o in outs}) == 1,
    "created_201": sum(1 for o in outs if o[1] == 201),
}

# ── D1: the preview discloses exactly what the add does ──
with Session() as s:
    s.add(M.Watchlist(id=10, user_id=1, name="Swing list", trading_style="SWING"))
    s.add(M.Watchlist(id=20, user_id=2, name="Bob list", trading_style=None))
    s.commit()
stock = None
with Session() as s:
    stock = s.query(M.Stock).filter_by(symbol="TESTCO").one()
    p_alice = W.preview_add("TESTCO", 10, user(1), s)
    p_bob = W.preview_add("TESTCO", 20, user(2), s)
    s.add(M.AlertPreference(user_id=1, alert_type="signal", enabled=False)); s.commit()
    p_alice_off = W.preview_add("TESTCO", 10, user(1), s)
    lists_before = s.query(M.Watchlist).count()
with Session() as s:
    W.add_to_watchlist("TESTCO", 10, user(1), s)
with Session() as s:
    sub = s.execute(text("SELECT count(*) FROM signal_alerts WHERE user_id=1 AND symbol='TESTCO' "
                         "AND horizon='SWING'")).scalar()
    p_after = W.preview_add("TESTCO", 10, user(1), s)
    try:
        W.preview_add("TESTCO", 20, user(1), s)     # bob's list
        foreign = "allowed"
    except HTTPException as e:
        foreign = e.status_code
R["preview"] = {
    "alice": {k: p_alice[k] for k in ("already_in_list", "email_on_file", "signal_emails_enabled")}
    | {"will_create": p_alice["signal_subscription"]["will_create"], "effect": p_alice["effect"]},
    "bob_no_email_effect": p_bob["effect"],
    "alice_prefs_off_effect": p_alice_off["effect"],
    "preview_created_no_list": lists_before == 2,
    "add_created_subscription": sub,
    "after_add": p_after["already_in_list"],
    "foreign_list": foreign,
}
print(json.dumps(R, default=str))
