"""YF-AVGVOL-NONTRADING (2026-10-30): on a day neither market trades, the 4-hourly average-volume
job keeps the cached values alive instead of downloading a month of history per symbol; on any
trading day, or when there is nothing to keep alive, it refreshes as before."""
from datetime import datetime, timezone
from unittest.mock import patch

import src.services.scheduler as S


class _Redis:
    def __init__(self, exists=True, fail=False):
        self.exists_, self.fail, self.calls = exists, fail, []

    def expire(self, key, ttl):
        self.calls.append((key, ttl))
        if self.fail:
            raise RuntimeError("redis down")
        return self.exists_


def _run(now, redis):
    with patch.object(S, "_get_redis", lambda: redis):
        return S._avg_volume_keep_alive_on_closed_day(now)


SAT = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)      # Saturday in New York and Hong Kong
FRI = datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)
SUN_NIGHT_ET = datetime(2026, 11, 2, 2, 0, tzinfo=timezone.utc)  # Sun 21:00 ET = Mon 10:00 HKT


def test_closed_day_keeps_the_cache_alive_with_its_full_ttl():
    r = _Redis()
    assert _run(SAT, r) is True
    from src.api.routes import _AVG_VOLUME_KEY, _AVG_VOLUME_TTL
    assert r.calls == [(_AVG_VOLUME_KEY, _AVG_VOLUME_TTL)]


def test_trading_days_refresh_as_before():
    r = _Redis()
    assert _run(FRI, r) is False and r.calls == []
    assert _run(SUN_NIGHT_ET, r) is False and r.calls == []    # HK is trading


def test_missing_cache_or_redis_failure_falls_back_to_the_refresh():
    assert _run(SAT, _Redis(exists=False)) is False
    assert _run(SAT, _Redis(fail=True)) is False
