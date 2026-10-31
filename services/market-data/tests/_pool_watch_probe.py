"""pool_watch against a real SQLAlchemy QueuePool (SQLite file): holders are tracked from checkout
to checkin, pressure is logged near the limit with each holder's origin, at most once per interval,
and a full pool still behaves exactly as before (the timeout is SQLAlchemy's own)."""
import pathlib
import sys
import tempfile
import threading
import types

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services" / "market-data")]
logged = []
logmod = types.ModuleType("common.logging")
logmod.get_logger = lambda *a, **k: types.SimpleNamespace(
    warning=lambda event, **kw: logged.append((event, kw)))
import common  # noqa: E402
sys.modules["common.logging"] = logmod
common.logging = logmod

from sqlalchemy import create_engine, exc, text  # noqa: E402
from sqlalchemy.pool import QueuePool  # noqa: E402
from src.services import pool_watch as W  # noqa: E402

engine = create_engine(f"sqlite:///{pathlib.Path(tempfile.mkdtemp()) / 'p.db'}", poolclass=QueuePool,
                       pool_size=4, max_overflow=2, pool_timeout=0.3)
W.install(engine)
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(label)


def hold_in_named_function():
    return engine.connect()


c1 = hold_in_named_function()
check("one holder tracked", len(W.snapshot()) == 1)
check("origin names our own code", any("hold_in_named_function" in o for o in W.snapshot()[0]["origin"]),
      W.snapshot())
check("no pressure below limit - margin", not logged, logged)
c1.close()
check("checkin removes the holder", W.snapshot() == [])

conns = []
t = threading.Thread(target=lambda: conns.append(engine.connect()), name="sched-job-x")
t.start(); t.join()
conns += [engine.connect() for _ in range(2)]
check("no pressure at 3 of 6", not logged, logged)
conns.append(engine.connect())     # 4 of limit 6: within the margin of 2
check("pressure logged near the limit", logged and logged[0][0] == "db.pool_pressure", logged)
h = logged[0][1]["holders"] if logged else []
check("holders listed oldest first with thread names", [x["thread"] for x in h][:1] == ["sched-job-x"], h)
conns += [engine.connect() for _ in range(2)]
check("logged at most once per interval", len(logged) == 1, logged)
check("pressure log names the limit", logged and logged[0][1]["limit"] == 6 and logged[0][1]["checked_out"] == 4, logged)
try:
    engine.connect()
    check("full pool still times out as before", False)
except exc.TimeoutError:
    check("full pool still times out as before", True)
for c in conns:
    c.close()
check("all holders released", W.snapshot() == [])
with engine.connect() as c:
    check("connections still work", c.execute(text("select 1")).scalar() == 1)
print("FAILED" if fails else "pool_watch probe passed")
sys.exit(1 if fails else 0)
