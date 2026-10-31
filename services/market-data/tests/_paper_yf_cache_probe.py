"""Paper-trading Yahoo caches (2026-10-30): ATR and 5-day sector returns are reused for
_LIVE_INPUT_TTL_S and the index quote for _INDEX_QUOTE_TTL_S, per symbol; only missing or expired
symbols are downloaded; failures are never cached; the computed values are unchanged."""
import pathlib
import sys
import types
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "shared"))
sys.path.insert(0, str(ROOT / "services" / "market-data"))
for m in ["redis", "httpx", "structlog", "apscheduler", "apscheduler.schedulers",
          "apscheduler.schedulers.background", "apscheduler.triggers", "apscheduler.triggers.cron"]:
    sys.modules.setdefault(m, MagicMock())
import common  # noqa: E402
_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(database_url="postgresql+psycopg2://u:p@127.0.0.1:1/none",
                                                 admin_password=None, jwt_secret="x", email_provider="", email_from="")
sys.modules["common.config"] = _cfg
common.config = _cfg

downloads = []
fail_for = set()


def download(tickers, period=None, **kw):
    tickers = [tickers] if isinstance(tickers, str) else list(tickers)
    downloads.append(tuple(sorted(tickers)))
    idx = pd.date_range("2026-09-01", periods=30, freq="B")
    cols = {}
    for t in tickers:
        if t in fail_for:
            continue
        base = 100.0 + (sum(map(ord, t)) % 50)
        close = base + np.arange(30) * 0.5
        cols[("Close", t)], cols[("High", t)], cols[("Low", t)] = close, close + 2, close - 1
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols, index=idx)


quotes = []
yf = types.ModuleType("yfinance")
yf.download = download
yf.Ticker = lambda s: (quotes.append(s), types.SimpleNamespace(
    fast_info=types.SimpleNamespace(previous_close=100.0, last_price=99.0)))[1]
sys.modules["yfinance"] = yf

from src.services import paper_trading_engine as pte  # noqa: E402

clock = [1000.0]
pte._monotonic = lambda: clock[0]
fails = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else f"  {detail}"))
    if not cond:
        fails.append(label)


# ATR
first = pte._batch_compute_atr(["AAA", "BBB"])
check("ATR first call downloads both", downloads == [("AAA", "BBB")], downloads)
uncached = pte._batch_compute_atr_live(["AAA", "BBB"], 14)
check("cached values equal a fresh computation", first == uncached and all(v for v in first.values()), (first, uncached))
downloads.clear()
clock[0] += 600
again = pte._batch_compute_atr(["AAA", "BBB", "CCC"])
check("within TTL only the new symbol is downloaded", downloads == [("CCC",)], downloads)
check("cached values returned", again["AAA"] == first["AAA"] and again["BBB"] == first["BBB"])
downloads.clear()
clock[0] += 400   # AAA/BBB now 1000 s old > 900
pte._batch_compute_atr(["AAA", "BBB", "CCC"])
check("expired symbols re-downloaded, fresh one not", downloads == [("AAA", "BBB")], downloads)
downloads.clear()
fail_for.add("DDD")
r = pte._batch_compute_atr(["DDD"])
pte._batch_compute_atr(["DDD"])
check("a failed symbol is not cached", r["DDD"] is None and downloads == [("DDD",), ("DDD",)], (r, downloads))
fail_for.clear()
downloads.clear()
from datetime import date  # noqa: E402
seen = []
pte._batch_compute_atr_as_of = lambda syms, period, as_of: (seen.append(as_of), {x: 1.0 for x in syms})[1]
r = pte._batch_compute_atr(["AAA"], as_of=date(2026, 6, 1))
check("as_of path bypasses the cache and Yahoo", r == {"AAA": 1.0} and seen == [date(2026, 6, 1)] and downloads == [],
      (r, seen, downloads))

# index quote (the ATR per-symbol fallback above also called Ticker; start clean)
quotes.clear()
check("index quote fetched once", pte._index_quote("SPY") == (100.0, 99.0) and quotes == ["SPY"], quotes)
pte._index_quote("SPY")
check("index quote reused within 60 s", quotes == ["SPY"], quotes)
clock[0] += 61
pte._index_quote("SPY")
check("index quote refetched after 60 s", quotes == ["SPY", "SPY"], quotes)

# sector RS
downloads.clear()
pairs = [("AAA", "Technology"), ("BBB", "Energy")]
lag1 = pte._batch_sector_rs_lag(pairs)
check("sector RS first call downloads stocks and ETFs", downloads == [("AAA", "BBB", "XLE", "XLK")], downloads)
downloads.clear()
lag2 = pte._batch_sector_rs_lag(pairs)
check("sector RS within TTL downloads nothing", downloads == [] and lag1 == lag2, (downloads, lag1, lag2))
clock[0] += 901
pte._batch_sector_rs_lag(pairs)
check("sector RS after TTL downloads again", downloads == [("AAA", "BBB", "XLE", "XLK")], downloads)

print("FAILED" if fails else "paper yf cache probe passed")
sys.exit(1 if fails else 0)
