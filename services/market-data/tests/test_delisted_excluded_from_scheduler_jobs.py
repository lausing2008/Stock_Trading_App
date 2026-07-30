"""Regression tests for BUG-DELISTED-GENERATION-BLIND (market-data scheduler.py).

Stock.delisted (aud14-survivorship) never flips Stock.active — a confirmed-delisted stock
stays "active" forever. Three scheduler.py jobs never excluded it: _avg_volume_refresh_job
(real yfinance 1mo-history downloads per symbol), check_volume_anomalies (a delisted stock's
frozen last-known price/volume can look like a real "anomaly" against its own stale average),
and the watchlist auto-rotation candidate query (could add a delisted stock to a watchlist).

Deliberately NOT touched: _symbols_for() — ingestion's own universe list must keep including
delisted symbols, since ingestion is what DETECTS/reconfirms delisting in the first place
(_record_delisting_signal() lives in ingestion.py's own per-symbol fetch loop). Excluding
delisted symbols there would break the detection mechanism itself, not just skip wasted work.

scheduler.py can't be imported directly in this test environment — its import chain pulls in
apscheduler plus several unstubbed modules (see test_price_alert_price_check.py's docstring
for the same constraint). These are source-text regression checks.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SOURCE = _scheduler_path.read_text()


def test_avg_volume_refresh_job_excludes_delisted_stocks():
    start = _SOURCE.index("def _avg_volume_refresh_job(")
    end = _SOURCE.index("_scheduler.add_job(", _SOURCE.index("_avg_volume_refresh_job", start + 10))
    body = _SOURCE[start:end]
    assert "Stock.delisted.is_(False)" in body
    assert "Stock.active.is_(True)" in body


def test_check_volume_anomalies_market_symbols_excludes_delisted_stocks():
    start = _SOURCE.index("_market_symbols = {")
    end = _SOURCE.index("}", start) + 1
    block = _SOURCE[start:end]
    assert "Stock.delisted.is_(False)" in block
    assert "Stock.active.is_(True)" in block


def test_watchlist_auto_rotation_candidate_query_excludes_delisted_stocks():
    start = _SOURCE.index("cand_rows = session.execute(")
    end = _SOURCE.index(").all()", start) + len(").all()")
    block = _SOURCE[start:end]
    assert "Stock.delisted.is_(False)" in block
    assert "Stock.active.is_(True)" in block


def test_symbols_for_deliberately_still_includes_delisted_stocks():
    """The universe helper every ingestion job uses must NOT exclude delisted symbols —
    ingestion is what detects/reconfirms delisting via _record_delisting_signal(), which
    only ever runs on symbols this function returns. Excluding them here would silently
    disable the detection mechanism itself, not just skip wasted downstream work."""
    start = _SOURCE.index("def _symbols_for(")
    end = _SOURCE.index("\n\n\n", start)
    body = _SOURCE[start:end]
    assert "Stock.delisted" not in body
