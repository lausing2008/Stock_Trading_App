"""Paper entry receipts: one durable row per `_open_paper_trade` attempt, opened or rejected.

Runs the REAL `_open_paper_trade` against SQLite, as `_concentration_probe.py` does, and reads the
rows back. Asserts:
  - a missing receipts table never changes whether a position opens;
  - every attempt writes exactly one receipt, opened or rejected;
  - freshness is classified from what the reservation actually valued: not_evaluated (stopped
    before it), no_sector_positions, all_marks_live, fallback_marks;
  - prices carry their source and fetch time, and an unmatched price is `unrecorded`;
  - every receipt names its release identity.
"""
import pathlib
import sys
import tempfile
import types
from datetime import date, datetime, timezone
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
_cfg_mod.get_settings = lambda: types.SimpleNamespace(
    database_url="postgresql+psycopg2://u:p@127.0.0.1:1/none", admin_password=None,
    jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg_mod
common.config = _cfg_mod

from sqlalchemy import create_engine, select                         # noqa: E402
from sqlalchemy.orm import sessionmaker                               # noqa: E402
from db.models import Base, PaperPortfolio, PaperTrade, Stock         # noqa: E402
from src.services import paper_trading_engine as pte                  # noqa: E402
from src.services.receipts import models as rm                        # noqa: E402
from src.services.receipts.identity import RELEASE_ENV                # noqa: E402

import os                                                             # noqa: E402
os.environ[RELEASE_ENV] = "abc123probe"

engine = create_engine(f"sqlite:///{pathlib.Path(tempfile.mkdtemp()) / 'receipts.db'}")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
EQUITY = 100_000.0
failures = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        failures.append(label)


def cfg(**over):
    c = dict(pte._DEFAULT_CONFIG)
    c.update(over)
    return c


def stock(s, symbol, sector):
    st = Stock(symbol=symbol, name=symbol, sector=sector, market="US", exchange="NASDAQ", currency="USD")
    s.add(st); s.flush()
    return st


def hold(s, pf, st, shares, entry):
    t = PaperTrade(portfolio_id=pf.id, symbol=st.symbol, stock_id=st.id, shares=shares,
                   entry_price=entry, current_stop=entry * 0.95, stop_loss=entry * 0.95,
                   stage="open", trading_style="SWING", sector=st.sector, entry_date=date(2026, 9, 1),
                   entry_time=datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc))
    s.add(t); s.flush()
    return t


def attempt(s, pf, st, price, live_prices, c, prefetched=()):
    sig = types.SimpleNamespace(id=None, symbol=st.symbol, confidence=70.0, reasons={},
                                signal="BUY", horizon=None)
    gp = {"stop": price * 0.95, "take_profit": price * 1.15}
    trade, reason = pte._open_paper_trade(
        s, pf, st, sig, None, price, gp, 8, [], "fallback", c, "SWING", EQUITY, 1.0, None,
        live_prices, list(prefetched), 2.0)
    return trade, reason


def receipts(s):
    return s.execute(select(rm.paper_entry_receipts).order_by(rm.paper_entry_receipts.c.decided_at)).mappings().all()


FETCHED = "2026-10-30T14:31:05+00:00"


def provenance(*symbols):
    pte._LIVE_PRICE_PROVENANCE.clear()
    pte._LIVE_PRICE_PROVENANCE.update({sym: {"source": "live_cache", "fetched_at": FETCHED,
                                             "session_date": "2026-10-30"} for sym in symbols})


print("1. no receipts table: the entry still opens")
with Session() as s:
    pf = PaperPortfolio(name="p0", initial_capital=EQUITY, current_cash=EQUITY, config={"market": "US"})
    s.add(pf); s.flush()
    a = stock(s, "NOTAB", "Energy"); s.commit()
    provenance("NOTAB")
    trade, reason = attempt(s, pf, a, 100.0, {"NOTAB": 100.0},
                            cfg(max_sector_pct=0.5, max_sector_positions=99, max_position_pct=0.1))
    s.commit()
    check("position opened without a receipts table", trade is not None, reason)

rm.metadata.create_all(engine)
with Session() as s:
    pf = PaperPortfolio(name="p1", initial_capital=EQUITY, current_cash=EQUITY, config={"market": "US"})
    s.add(pf); s.flush()
    held_live = stock(s, "HLIVE", "Energy")
    held_stale = stock(s, "HSTALE", "Utilities")
    e1, u1, m1, x1, n1, q1 = (stock(s, sym, sec) for sym, sec in (
        ("ENEW", "Energy"), ("UNEW", "Utilities"), ("MINP", "Materials"), ("ECAP", "Energy"),
        ("NSEC", "Health"), ("UNREC", "Health")))
    t_live = hold(s, pf, held_live, 50, 100.0)       # 5,000: 5% of equity
    t_stale = hold(s, pf, held_stale, 50, 100.0)
    s.commit()
    base = dict(max_sector_positions=99, max_position_pct=0.10)

    print("2. allowed, every sector mark live")
    provenance("ENEW", "HLIVE")
    trade, _ = attempt(s, pf, e1, 100.0, {"ENEW": 100.0, "HLIVE": 110.0}, cfg(max_sector_pct=0.5, **base))
    print("3. allowed, a sector mark fell back to the entry price")
    provenance("UNEW")
    attempt(s, pf, u1, 100.0, {"UNEW": 100.0}, cfg(max_sector_pct=0.5, **base))
    print("4. rejected before the reservation (minimum position value)")
    provenance("MINP")
    attempt(s, pf, m1, 100.0, {"MINP": 100.0}, cfg(max_sector_pct=0.5, min_position_value=1e9, **base))
    print("5. rejected by the sector cap")
    provenance("ECAP", "HLIVE", "ENEW")
    attempt(s, pf, x1, 100.0, {"ECAP": 100.0, "HLIVE": 110.0, "ENEW": 100.0}, cfg(max_sector_pct=0.06, **base))
    print("6. allowed, no open position in the sector")
    provenance("NSEC")
    attempt(s, pf, n1, 100.0, {"NSEC": 100.0}, cfg(max_sector_pct=0.5, **base))
    print("7. candidate price this step never fetched")
    pte._LIVE_PRICE_PROVENANCE.clear()
    attempt(s, pf, q1, 100.0, {"UNREC": 100.0}, cfg(max_sector_pct=0.5, **base))
    s.commit()

    rows = receipts(s)
    by = {r["symbol"]: r for r in rows}
    check("one receipt per attempt (6)", len(rows) == 6, len(rows))

    r = by.get("ENEW", {})
    check("live marks: opened", r.get("outcome") == "opened" and r.get("reason") == "opened", dict(r))
    check("live marks: freshness all_marks_live", r.get("freshness") == "all_marks_live", r.get("freshness"))
    check("live marks: the held position's mark is recorded with its fetch time",
          [(m["symbol"], m["provenance"], m["price"], m["fetched_at"]) for m in r.get("marks") or []]
          == [("HLIVE", "live", 110.0, FETCHED)], r.get("marks"))
    check("live marks: candidate price source and timestamp",
          r.get("candidate_price_source") == "live_cache"
          and r.get("candidate_price_fetched_at") == datetime(2026, 10, 30, 14, 31, 5)
          and r.get("candidate_price_session_date") == date(2026, 10, 30), dict(r))
    check("live marks: trade id recorded", r.get("trade_id") is not None)

    r = by.get("UNEW", {})
    check("fallback: freshness fallback_marks", r.get("freshness") == "fallback_marks", r.get("freshness"))
    check("fallback: would block, and enforcing would change the outcome",
          r.get("would_block_on_fresh_marks") is True and r.get("decision_would_change") is True, dict(r))
    check("fallback: not enforced, so the entry opened", r.get("outcome") == "opened"
          and r.get("fresh_marks_enforced") is False, dict(r))
    check("fallback: the substitute is named", [(m["symbol"], m["provenance"]) for m in r.get("marks") or []]
          == [("HSTALE", "entry_price")], r.get("marks"))

    r = by.get("MINP", {})
    check("pre-reservation rejection: rejected with its reason",
          r.get("outcome") == "rejected" and r.get("reason") == "min_position", dict(r))
    check("pre-reservation rejection: freshness not_evaluated, no marks",
          r.get("freshness") == "not_evaluated" and r.get("marks") is None, dict(r))

    r = by.get("ECAP", {})
    check("sector cap: rejected, reason sector_cap", r.get("outcome") == "rejected"
          and r.get("reason") == "sector_cap" and r.get("reservation_reason") == "sector_cap", dict(r))
    check("sector cap: enforcing freshness would change nothing", r.get("decision_would_change") is False, dict(r))
    check("sector cap: projected share and cap recorded", (r.get("projected_pct") or 0) > 0.06
          and r.get("cap_pct") == 0.06, dict(r))

    r = by.get("NSEC", {})
    check("empty sector: freshness no_sector_positions", r.get("freshness") == "no_sector_positions", dict(r))

    r = by.get("UNREC", {})
    check("unfetched candidate price is unrecorded, not attributed",
          r.get("candidate_price_source") == "unrecorded" and r.get("candidate_price_fetched_at") is None, dict(r))

    check("every receipt names the release and a code fingerprint",
          all(x["release_sha"] == "abc123probe" and len(x["code_fingerprint"]) == 64 for x in rows),
          [(x["release_sha"], x["code_fingerprint"]) for x in rows])
    check("session date and regular-session flag recorded",
          all(x["session_date"] is not None and x["regular_session"] is not None for x in rows))

if failures:
    print(f"\n{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("\nall entry-receipt checks passed")
