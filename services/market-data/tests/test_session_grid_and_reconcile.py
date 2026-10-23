"""Tracker 457: completeness against the expected candle grid, and the post-close reconciliation."""
import pathlib
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.session_grid import completeness, expected_grid
import src.services.scheduler as sched

UTC = timezone.utc


def _naive(g):
    return [x.replace(tzinfo=None) for x in g]


def test_grid_regular_early_close_holiday_and_hk_lunch():
    us = expected_grid("US", date(2026, 10, 22))
    assert len(us) == 78 and us[0] == datetime(2026, 10, 22, 13, 30, tzinfo=UTC) and us[-1] == datetime(2026, 10, 22, 19, 55, tzinfo=UTC)
    est = expected_grid("US", date(2026, 11, 6))                     # after DST: 09:30 ET = 14:30 UTC
    assert est[0] == datetime(2026, 11, 6, 14, 30, tzinfo=UTC) and len(est) == 78
    early = expected_grid("US", date(2026, 11, 27))                  # NYSE 13:00 ET close
    assert len(early) == 42 and early[-1] == datetime(2026, 11, 27, 17, 55, tzinfo=UTC)
    assert expected_grid("US", date(2026, 11, 26)) == []             # Thanksgiving
    hk = expected_grid("HK", date(2026, 10, 22))
    assert len(hk) == 66 and datetime(2026, 10, 22, 4, 0, tzinfo=UTC) not in hk   # 12:00 HKT lunch


def test_row_counts_can_hide_a_missing_candle_the_grid_cannot():
    grid = _naive(expected_grid("US", date(2026, 10, 22)))
    # 77 valid candles + 2 opening snapshots = 79 rows, yet the final candle is missing.
    rows = grid[:-1] + [datetime(2026, 10, 22, 13, 30, 1), datetime(2026, 10, 22, 13, 30, 6)]
    c = completeness(rows, "US", date(2026, 10, 22))
    assert len(rows) == 79 and c["present"] == 77 and c["final_candle_missing"] is True
    assert c["off_grid"] == [datetime(2026, 10, 22, 13, 30, 1), datetime(2026, 10, 22, 13, 30, 6)]
    full = completeness(grid + [datetime(2026, 10, 22, 13, 30, 1)], "US", date(2026, 10, 22))
    assert full["missing"] == [] and len(full["off_grid"]) == 1


def test_a_midday_check_does_not_expect_the_afternoon():
    hk_morning = [g.replace(tzinfo=None) for g in expected_grid("HK", date(2026, 10, 22)) if g.hour < 4]
    c = completeness(hk_morning, "HK", date(2026, 10, 22), until=datetime(2026, 10, 22, 4, 2, tzinfo=UTC))
    assert c["expected"] == 30 and c["missing"] == [] and c["final_candle_missing"] is False


def _reconcile(stored, now=datetime(2026, 10, 22, 20, 2, tzinfo=UTC), *, symbols=("SMH", "AAPL"), market="US",
               written=None, second_pass=False, redis=None, ingest_raises=False):
    calls, redis = [], ({} if redis is None else redis)
    def forbid(*a, **k): raise AssertionError("reconciliation must not run trading steps or RZY")
    def ingest(syms, tf):
        calls.append((tuple(syms), tf))
        if ingest_raises: raise RuntimeError("provider down")
    fake_redis = SimpleNamespace(set=lambda k, v: redis.__setitem__(k, v), get=lambda k: redis.get(k))
    with patch.object(sched, "_is_us_trading_day", lambda *a: True), patch.object(sched, "_is_hk_trading_day", lambda *a: True), \
         patch.object(sched, "_symbols_for", lambda m: list(symbols)), patch.object(sched, "ingest_universe", ingest), \
         patch.object(sched, "_stored_5m_rows", lambda syms, lo, hi: stored), \
         patch.object(sched, "_candle_last_written", lambda syms, ts: {x: (written or {}).get(x) for x in syms}), \
         patch.object(sched, "_run_paper_trading_step", forbid), patch.object(sched, "check_little_rzy_workflows", forbid), \
         patch.object(sched, "_record_job_status", lambda *a, **k: redis.__setitem__("status", a[1])), \
         patch.object(sched, "_get_redis", lambda: fake_redis):
        detail = sched._reconcile_session_5m(market, now=now, second_pass=second_pass)
    return detail, calls, redis


GRID = _naive(expected_grid("US", date(2026, 10, 22)))
DONE = {s: datetime(2026, 10, 22, 20, 2) for s in ("SMH", "AAPL", "XYZ")}


def test_reconciliation_ingests_and_reports_grid_completeness():
    stored = [("AAPL", t) for t in GRID] + [("SMH", t) for t in GRID[:-1]] + [("SMH", datetime(2026, 10, 22, 13, 30, 1))]
    detail, calls, redis = _reconcile(stored, written=DONE)
    assert calls == [(("SMH", "AAPL"), "5m")]
    assert detail["complete"] == 1 and detail["final_candle_missing"] == ["SMH"]
    assert detail["missing_candles"] == {"SMH": 1} and detail["off_grid_rows"] == {"SMH": 1}
    assert redis["status"] == "partial" and "scheduler:job:us_5m_reconcile:detail" in redis
    _, _, redis = _reconcile([("AAPL", t) for t in GRID] + [("SMH", t) for t in GRID], written=DONE)
    assert redis["status"] == "ok"


def test_a_final_candle_still_forming_is_partial():
    """Present but last written at 15:55:25 ET, before the candle completed at 16:00."""
    detail, _, redis = _reconcile([("AAPL", t) for t in GRID] + [("SMH", t) for t in GRID],
                                  written={"AAPL": datetime(2026, 10, 22, 19, 55, 25), "SMH": datetime(2026, 10, 22, 20, 2)})
    assert detail["final_candle_forming"] == ["AAPL"] and redis["status"] == "partial"


def test_a_symbol_with_no_session_data_does_not_make_every_run_partial():
    detail, _, redis = _reconcile([("AAPL", t) for t in GRID] + [("SMH", t) for t in GRID], symbols=("SMH", "AAPL", "XYZ"), written=DONE)
    assert detail["no_session_data"] == ["XYZ"] and redis["status"] == "ok"


def test_the_second_pass_reingests_only_what_the_first_left_incomplete():
    redis = {}
    _reconcile([("AAPL", t) for t in GRID] + [("SMH", t) for t in GRID[:-1]], written=DONE, redis=redis)
    _, calls, _ = _reconcile([("AAPL", t) for t in GRID] + [("SMH", t) for t in GRID], written=DONE, redis=redis,
                             second_pass=True, now=datetime(2026, 10, 22, 20, 7, tzinfo=UTC))
    assert calls == [(("SMH",), "5m")]


def test_ingest_failure_is_an_error_status_and_a_holiday_does_nothing():
    _, _, redis = _reconcile([("AAPL", t) for t in GRID] + [("SMH", t) for t in GRID], written=DONE, ingest_raises=True)
    assert redis["status"] == "error"
    with patch.object(sched, "_is_us_trading_day", lambda *a: False):
        assert sched._reconcile_session_5m("US", now=datetime(2026, 11, 26, 21, 2, tzinfo=UTC)) == {"status": "not_a_trading_day"}


def test_hk_midday_pass_expects_only_the_morning_session():
    hk = [g.replace(tzinfo=None) for g in expected_grid("HK", date(2026, 10, 22)) if g.hour < 4]
    detail, _, redis = _reconcile([("0700.HK", t) for t in hk], symbols=("0700.HK",), market="HK",
                                  now=datetime(2026, 10, 22, 4, 2, tzinfo=UTC), written={"0700.HK": datetime(2026, 10, 22, 4, 2)})
    assert detail["complete"] == 1 and detail["final_candle_missing"] == [] and redis["status"] == "ok"


def _cron_kwargs_by_job():
    import ast
    tree = ast.parse(pathlib.Path(sched.__file__).read_text())
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_job":
            jid = next((k.value.value for k in node.keywords if k.arg == "id" and isinstance(k.value, ast.Constant)), None)
            if jid:
                out[jid] = [{k.arg: ast.literal_eval(k.value) for k in c.keywords}
                            for c in ast.walk(node) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "CronTrigger"]
    return out


def test_reconciliation_runs_after_each_close_and_after_the_hk_auction():
    t = _cron_kwargs_by_job()
    assert t["us_5m_reconcile"] == [dict(hour=16, minute=2, day_of_week="mon-fri", timezone="America/New_York")]
    assert t["us_5m_reconcile_retry"] == [dict(hour=16, minute=7, day_of_week="mon-fri", timezone="America/New_York")]
    assert [(x["hour"], x["minute"], x["timezone"]) for x in t["hk_5m_reconcile"]] == [(12, 2, "Asia/Hong_Kong"), (16, 12, "Asia/Hong_Kong")]
    assert [(x["hour"], x["minute"]) for x in t["hk_5m_reconcile_retry"]] == [(12, 7), (16, 17)]
    # The US intraday job's last fire is 15:55: the gap the reconciliation exists for.
    assert t["us_5m_intraday"][0]["hour"] == "9,10,11,12,13,14,15"
