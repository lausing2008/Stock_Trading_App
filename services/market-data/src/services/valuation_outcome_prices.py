"""Bounded adjusted-close observations for frozen report cohorts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json

import yfinance as yf

from .valuation_inputs import completed_session, number, INPUT_CACHE_PREFIX


def collect(cache, symbols, *, now=None):
    now = now or datetime.now(timezone.utc)
    day = completed_session(now).isoformat()
    def load(symbol):
        key = f"stockai:valuation:outcome-prices:v1:{day}:{symbol}"
        try:
            old = json.loads(cache.get(key) or "null")
            if old:
                return symbol, old
        except Exception:
            pass
        try:
            raw = json.loads(cache.get(f"{INPUT_CACHE_PREFIX}:{symbol}") or "null")
            stamp = datetime.fromisoformat(raw["fetched_at"]) if raw else None
            bars = (raw.get("bars") or []) if raw else []
            if (raw and raw.get("provider", "Yahoo") == "Yahoo" and stamp and stamp.tzinfo is not None
                    and 0 <= (now - stamp).total_seconds() < 86400 and bars and bars[-1].get("date") == day
                    and all(number(b.get("adj_close")) is not None and b["adj_close"] > 0 for b in bars)):
                return symbol, {"state": "OK", "retrieved_at": raw["fetched_at"],
                                "bars": [{"date": b["date"], "adj_close": b["adj_close"]} for b in bars]}
        except Exception:
            pass
        value = {"state": "UNAVAILABLE", "retrieved_at": now.isoformat(), "bars": []}
        try:
            # Financial-statement requests are deliberately absent from this price-only path.
            frame = yf.Ticker(symbol).history(period="1y", interval="1d", auto_adjust=False,
                                              actions=False, timeout=3)
            bars = []
            for stamp, row in frame.iterrows():
                price = number(row.get("Adj Close"))
                if stamp.date().isoformat() <= day and price is not None and price > 0:
                    bars.append({"date": stamp.date().isoformat(), "adj_close": price})
            if bars:
                value.update(state="OK", bars=sorted(bars, key=lambda b: b["date"]))
        except Exception:
            pass
        try:
            cache.setex(key, 86400 if value["state"] == "OK" else 300, json.dumps(value))
        except Exception:
            pass
        return symbol, value
    with ThreadPoolExecutor(max_workers=2) as pool:
        series = dict(pool.map(load, symbols))
    return {"price_session": day, "source": "Yahoo daily adjusted closes",
            "basis": "Provider Adj Close as retrieved; split/dividend adjustment history can be revised",
            "assembled_at": now.isoformat(), "series": series}
