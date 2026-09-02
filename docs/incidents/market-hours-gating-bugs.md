## Recurring Issue: BUG-VOLANOM-STALEMARKET — Volume-Anomaly Alert Fired on a Closed Market's Frozen Daily Volume (Fixed 2026-07-21)

**Symptom:** a user received an "Abnormal Volume Detected" email for an HK stock (2513.HK) at
17:01 HKT — well before HK's 09:30 HKT market open. Log inspection showed the scan logging
`"triggered": 1` continuously, unchanged, for 30+ minutes straight — the tell that the
underlying input was frozen, not genuinely moving intraday data.

**Root cause:** `check_volume_anomalies()` (T257-VOLUME-ANOMALY-ALERT, `services/market-data/
src/services/scheduler.py`) reads `stockai:live_prices`/`stockai:avg_volume` from Redis with
**no check on whether the market a given row belongs to is actually open**. Its own tracker
entry's `impact` field even claimed the job was "market-hours gated" — it never was. The
scheduled 1-minute refresh job (`_live_price_refresh_job`) correctly no-ops when both US and HK
are closed — but `GET /stocks/latest_prices` (`services/market-data/src/api/routes.py`) has its
OWN cache-miss fallback: if the shared `stockai:live_prices` key has expired, it just re-fetches
from yfinance for the whole universe and rewrites the SAME key with a fresh 90s TTL, completely
independent of market hours. Confirmed via production logs: this fallback fired every ~2
minutes for over 3 hours straight (`"live_prices.ok", "source": "yfinance_bulk"`) during a dead
window with zero scheduled refreshes — almost certainly a frontend tab open somewhere polling a
page that calls this endpoint. `_fetch_live_bulk()` itself uses `yf.download(period="2d",
interval="1d")` — a **daily** bar — so every one of those re-fetches just returned HK's last
COMPLETED session's volume again, stamped with a fresh TTL each time. `check_volume_anomalies()`
then read that frozen daily-bar volume as if it were live "this cycle" data.

**Fix applied:** import and check `paper_trading_engine.py`'s already-established
`_is_market_hours()` helper (the same one `_should_enter()`'s fallback gate uses, correctly
handling HK's lunch break and holidays) — both as a whole-scan short-circuit when NEITHER
market is open, and per-row inside the scan loop, since HK can be closed while US is open (or
vice versa) and the shared cache holds both markets' rows at once:
```python
from .paper_trading_engine import _is_market_hours
_us_market_open = _is_market_hours("US")
_hk_market_open = _is_market_hours("HK")
if not _us_market_open and not _hk_market_open:
    return  # whole scan is a no-op — neither market trading

# inside the per-symbol loop:
if _is_hk_sym and not _hk_market_open:
    continue
if not _is_hk_sym and not _us_market_open:
    continue
```

**Deliberately NOT changed**: `GET /stocks/latest_prices`' own cache-miss fallback still
refreshes on-demand regardless of market hours — that's correct behavior for ITS consumers
(watchlists, screener, stock detail pages all legitimately want to show the last known price
even when markets are closed). The bug was specifically that a DIFFERENT consumer (this alert
scanner) treated any populated cache entry as automatically meaning "fresh, currently-trading
data" — fixed at the point of that incorrect assumption, not by changing the shared cache's
general contract.

**Tests**: 4 new cases in `services/market-data/tests/test_volume_anomaly_alert.py` (now 15
total, source-text regression checks — `scheduler.py` can't be imported in this test
environment) confirm: the real `_is_market_hours()` helper is used (not a hand-rolled second
check), the both-closed short-circuit happens before any threshold computation, and HK/US rows
are independently skipped when their own market is closed even if the other market is open.

**Adversarial verification** — 2 sabotage cycles, both caught and reverted: removing the
both-closed short-circuit entirely, and removing the per-row market-open checks inside the
loop.

Full 331-test market-data suite (up from 327) green; frontend typecheck clean.

**What to check if this looks wrong**:
```bash
docker exec stockai-market-data-1 grep -n "_is_market_hours" /app/src/services/scheduler.py
# Confirm no volume-anomaly email fires outside real trading hours for that symbol's market:
docker logs stockai-market-data-1 --since 6h | grep volume_anomaly.done
# A CONSTANT unchanging "triggered": N across many consecutive minutes is the same tell that
# caught this bug — real intraday volume moves minute to minute; a frozen value means the
# underlying cache/data source isn't actually updating.
```

---

