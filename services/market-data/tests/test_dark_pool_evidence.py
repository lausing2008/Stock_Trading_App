"""Options review Phase B4/B5: print time status, persistence normalisation, baseline status,
the stock-page route and the alert email. Real functions; only the provider, DB and send stubbed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.services import dark_pool_evidence as DPE
import src.services.scheduler as sched

NOW = datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("raw,status,utc", [
    ("2026-10-21T17:55:00Z", "ok", datetime(2026, 10, 21, 17, 55, tzinfo=timezone.utc)),
    ("2026-10-21T13:55:00-04:00", "ok", datetime(2026, 10, 21, 17, 55, tzinfo=timezone.utc)),
    ("2026-10-21T18:01:30Z", "ok", datetime(2026, 10, 21, 18, 1, 30, tzinfo=timezone.utc)),   # within tolerance
    ("2026-10-21T18:02:01Z", "future", datetime(2026, 10, 21, 18, 2, 1, tzinfo=timezone.utc)),
    ("2026-10-22T18:00:00Z", "future", datetime(2026, 10, 22, 18, 0, tzinfo=timezone.utc)),  # the review's reproduction
    ("2026-10-21T17:55:00", "ambiguous_timezone", None),
    (datetime(2026, 10, 21, 17, 55), "ambiguous_timezone", None),
    (None, "missing", None), ("", "missing", None), ("nonsense", "unparseable", None), (42, "unparseable", None),
])
def test_print_time_status(raw, status, utc):
    got, st = DPE.print_time(raw, NOW)
    assert st == status and got == utc


def test_a_future_print_is_never_fresh():
    cutoff = NOW - timedelta(minutes=90)
    assert DPE.is_recent("2026-10-21T17:00:00Z", cutoff, NOW) is True
    assert DPE.is_recent("2026-10-22T18:00:00Z", cutoff, NOW) is False


def test_the_alert_freshness_check_rejects_a_future_print():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=90)
    assert sched._dark_pool_print_is_recent(SimpleNamespace(executed_at=future), cutoff) is False


def _row(at, price=10.0, size=1000):
    return SimpleNamespace(executed_at=at, price=price, size=size, premium=price * size, venue="L",
                           nbbo_bid=9.9, nbbo_ask=10.1)


def test_persistence_stores_utc_and_quarantines_future_and_zone_less_prints():
    captured = {}
    stmt = MagicMock()
    stmt.values.side_effect = lambda payload: (captured.__setitem__("payload", payload), stmt)[1]
    session = MagicMock()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with patch("sqlalchemy.dialects.postgresql.insert", lambda model: stmt), patch.object(sched, "log") as log:
        n = sched._persist_dark_pool_prints(session, 1, "MU", [
            _row("2026-10-21T13:55:00-04:00"), _row(future), _row("2026-10-21T17:55:00")])
    assert n == 1
    assert captured["payload"][0]["executed_at"] == datetime(2026, 10, 21, 17, 55)      # UTC, zone dropped after conversion
    log.warning.assert_any_call("dark_pool_print.quarantined", symbol="MU", future=1, ambiguous_timezone=1)


def _baseline(result=None, error=None):
    session = MagicMock()
    if error:
        session.execute.side_effect = error
    else:
        session.execute.return_value.one.return_value = result
    with patch.object(sched, "text", lambda sql: sql):
        return sched._dark_pool_baseline(session, "MU", before=NOW), session


def test_baseline_status_keeps_insufficient_history_and_query_failure_apart():
    ok, _ = _baseline(SimpleNamespace(n=40, med=250_000.0, first_at=datetime(2026, 10, 8), last_at=datetime(2026, 10, 21)))
    assert ok["status"] == "ok" and ok["median"] == 250_000.0 and ok["first_captured"] == "2026-10-08T00:00:00"
    thin, _ = _baseline(SimpleNamespace(n=5, med=250_000.0, first_at=None, last_at=None))
    assert thin["status"] == "insufficient_history" and thin["median"] is None
    failed, session = _baseline(error=RuntimeError("db down"))
    assert failed["status"] == "query_failed" and failed["median"] is None
    session.rollback.assert_called_once()
    assert sched._dark_pool_premium_baseline(MagicMock(execute=MagicMock(side_effect=RuntimeError())), "MU") is None


def test_route_reports_time_status_age_side_estimate_and_baseline():
    from src.api import routes
    from src.services import unusual_whales as UW
    now = datetime.now(timezone.utc)
    rows = [UW.DarkPoolPrintRow(symbol="MU", price=10.09, size=1000, premium=10_090.0, venue="L",
                                executed_at=(now - timedelta(minutes=12)).isoformat(), nbbo_bid=10.0, nbbo_ask=10.1),
            UW.DarkPoolPrintRow(symbol="MU", price=10.0, size=1000, premium=10_000.0, venue="L",
                                executed_at=(now + timedelta(days=1)).isoformat(), nbbo_bid=10.0, nbbo_ask=10.1)]
    base = {"status": "query_failed", "median": None, "prints": None, "window_days": 14, "min_prints": 20,
            "first_captured": None, "last_captured": None, "population": "captured"}
    with patch.object(UW, "is_available", lambda: True), patch.object(UW, "get_dark_pool_prints", lambda s: rows), \
         patch.object(sched, "_dark_pool_baseline", lambda session, sym: base):
        out = routes.get_dark_pool_prints_route("mu", session=None)
    first, second = out["prints"]
    assert first["time_status"] == "ok" and 11.5 <= first["age_minutes"] <= 12.5 and first["side_estimate"] == "buy"
    assert second["time_status"] == "future" and second["age_minutes"] is None
    assert out["baseline"]["status"] == "query_failed" and "FAILED" in out["baseline"]["text"]
    assert "estimate" in out["side_basis"] and "does not establish" in out["venue_note"]


def test_a_print_stamped_inside_the_tolerance_never_shows_a_negative_age():
    from src.api import routes
    from src.services import unusual_whales as UW
    ahead = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    rows = [UW.DarkPoolPrintRow(symbol="MU", price=10.0, size=1, premium=10.0, venue="L", executed_at=ahead)]
    base = {"status": "ok", "median": 1.0, "prints": 30, "window_days": 14, "min_prints": 20,
            "first_captured": None, "last_captured": None, "population": "captured"}
    with patch.object(UW, "is_available", lambda: True), patch.object(UW, "get_dark_pool_prints", lambda s: rows), \
         patch.object(sched, "_dark_pool_baseline", lambda session, sym: base):
        p = routes.get_dark_pool_prints_route("MU", session=None)["prints"][0]
    assert p["time_status"] == "ok" and p["age_minutes"] == 0.0
