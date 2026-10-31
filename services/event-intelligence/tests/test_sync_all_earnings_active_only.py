"""YF-EARNINGS-ACTIVEONLY (2026-10-30): the full earnings sync walks tracked, live names only.

Runs the real sync_all_earnings against real Stock rows in SQLite (the harness in
test_earnings_backfill_report_dates.py) and records which symbols it asks Yahoo for."""
import asyncio
from unittest.mock import patch

import test_earnings_backfill_report_dates as H

select = H.select   # the real one; conftest stubs sqlalchemy for this package


def test_sync_all_earnings_fetches_active_non_delisted_stocks_only():
    with H._SessionLocal() as s:
        for sym, active, delisted in (("LIVE", True, False), ("XLK", False, False),
                                      ("GONE", True, True), ("OLD", False, True)):
            s.add(H._models.Stock(symbol=sym, name=sym, market="US", exchange="NASDAQ",
                                  active=active, delisted=delisted))
        s.commit()
    asked = []

    async def no_sleep(*a, **k):
        return None

    with patch.object(H.earnings, "SessionLocal", H._SessionLocal), \
         patch.object(H.earnings, "select", select), \
         patch.object(H.earnings, "Stock", H._models.Stock), \
         patch.object(H.earnings, "_fetch_earnings_for_symbol", lambda sym, sid, stats: asked.append(sym) or 0), \
         patch.object(H.earnings, "_record_coverage_attempt", lambda *a, **k: None), \
         patch.object(H.earnings.asyncio, "sleep", no_sleep):
        out = asyncio.run(H.earnings.sync_all_earnings())
    assert "LIVE" in asked and not {"XLK", "GONE", "OLD"} & set(asked), asked
    assert out["symbols_processed"] == len(asked)
