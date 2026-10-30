"""AUD-UW-COOLDOWN-YFSPILL (2026-10-30): a UW cooldown no longer hands the regular session to yfinance.

Oct 28 close window: daily-bar fetches went 85 UW / 661 yfinance in the ten minutes with two UW
429s, 336 / 10 once they stopped, and Yahoo rate-limited the host (592 failed symbol ingests).
A fresh symbol now skips the run instead; a stale one, or anything outside the regular session,
still falls back exactly as before.

The PostgreSQL test SKIPS unless `RZY_PG_URL` names a disposable local database (see
_cooldown_skip_probe.py).
"""
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import src.services.ingestion as ING
import src.adapters.unusual_whales_adapter as UWA

IN_SESSION = datetime(2026, 10, 28, 19, 30, tzinfo=timezone.utc)      # Wed 15:30 ET
PREMARKET = datetime(2026, 10, 28, 13, 0, tzinfo=timezone.utc)        # 09:00 ET
POST_CLOSE = datetime(2026, 10, 28, 20, 2, tzinfo=timezone.utc)       # 16:02 ET, final-candle pass
SATURDAY = datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)


class _A:
    def __init__(self, name):
        self.name = name


YF_ONLY = [_A("yfinance"), _A("alpha_vantage")]
WITH_UW = [_A("unusual_whales")] + YF_ONLY


class _Rec:
    def __init__(self, last_success_at):
        self.last_success_at = last_success_at


class _Session:
    def __init__(self, rec=None):
        self.rec = rec

    def get(self, model, key):
        return self.rec


class _Stock:
    id = 7


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _skip(tf, head, adapters=YF_ONLY, now=IN_SESSION, market="US", rec=None):
    with patch.object(ING, "primary_provider", lambda m, t: "unusual_whales"):
        return ING._skip_during_primary_cooldown(_Session(rec), _Stock(), market, tf, head, adapters, now=now)


# ── intraday ────────────────────────────────────────────────────────────────────────────

def test_a_fresh_5m_symbol_skips_while_uw_steps_aside():
    assert _skip("5m", _naive(IN_SESSION - timedelta(minutes=5))) is True


def test_a_5m_symbol_falls_back_once_its_bars_are_stale():
    """Newest bar started 25 min ago -> ended 20 min ago -> past the 15 min window."""
    assert _skip("5m", _naive(IN_SESSION - timedelta(minutes=25))) is False


def test_the_window_is_measured_from_the_bars_end_not_its_start():
    assert _skip("5m", _naive(IN_SESSION - timedelta(minutes=20))) is True     # ended 15 min ago
    assert _skip("1h", _naive(IN_SESSION - timedelta(minutes=70))) is True     # ended 10 min ago


def test_nothing_is_skipped_when_uw_is_available():
    assert _skip("5m", _naive(IN_SESSION - timedelta(minutes=5)), adapters=WITH_UW) is False


@pytest.mark.parametrize("now", [PREMARKET, POST_CLOSE, SATURDAY])
def test_outside_the_regular_session_the_fallback_is_unchanged(now):
    """Premarket uses yfinance by design, and the 16:02 final-candle pass must not be skipped."""
    assert _skip("5m", _naive(now - timedelta(minutes=5)), now=now) is False


def test_hk_and_first_ingests_are_untouched():
    assert _skip("5m", _naive(IN_SESSION - timedelta(minutes=5)), market="HK") is False
    assert _skip("5m", None) is False


# ── daily ───────────────────────────────────────────────────────────────────────────────

def test_daily_freshness_is_the_last_successful_fetch_not_the_date_head():
    head = datetime(2026, 10, 28)
    assert _skip("1d", head, rec=_Rec(_naive(IN_SESSION - timedelta(minutes=5)))) is True
    assert _skip("1d", head, rec=_Rec(_naive(IN_SESSION - timedelta(minutes=40)))) is False
    assert _skip("1d", head, rec=None) is False
    assert _skip("1d", head, rec=_Rec(None)) is False


def test_the_skip_sits_on_the_us_incremental_branch_only():
    src = pathlib.Path(ING.__file__).read_text()
    i = src.index("adapters = get_adapters(market, timeframe)")
    j = src.index("_skip_during_primary_cooldown(session, stock, market, timeframe, head, adapters)")
    k = src.index("# T230-CHARTING-PREMARKET", i)
    assert i < j < k
    assert '"skipped": "primary_cooldown"' in src[j:k]


# ── per-minute headroom on the bar path ─────────────────────────────────────────────────

class _Redis:
    def __init__(self):
        self.h, self.ttl = {}, {}

    def hget(self, k, f):
        return self.h.get(k, {}).get(f)

    def hset(self, k, f, v):
        self.h.setdefault(k, {})[f] = str(v)

    def expire(self, k, s):
        self.ttl[k] = s


def test_the_lowest_remaining_per_minute_is_kept():
    r = _Redis()
    with patch("common.redis_client.get_redis", lambda: r):
        for used, rem in ((10, 110), (90, 30), (40, 80)):
            assert UWA.UnusualWhalesAdapter._note_minute_headroom(
                {"x-uw-minute-req-counter": str(used), "x-uw-req-per-minute-remaining": str(rem)}) == (used, rem)
    (key, fields), = r.h.items()
    assert key.startswith("stockai:metric:uw_bar_minute_remaining:")
    minute = [f for f in fields if f != "limit"]
    assert len(minute) == 1 and fields[minute[0]] == "30"
    assert fields["limit"] == "120"
    assert r.ttl[key] >= 48 * 3600


def test_headroom_recording_never_raises():
    def boom():
        raise RuntimeError("redis down")
    with patch("common.redis_client.get_redis", boom):
        assert UWA.UnusualWhalesAdapter._note_minute_headroom({"x-uw-req-per-minute-remaining": "5"}) == (None, None)
    assert UWA.UnusualWhalesAdapter._note_minute_headroom({}) == (None, None)


def test_headers_are_read_before_the_429_branch_so_refusals_are_measured():
    src = pathlib.Path(UWA.__file__).read_text()
    i = src.index("self._note_minute_headroom(_headers)")
    assert i < src.index("if r.status_code == 429:") < src.index('"unusual_whales.bar_rate_limited"')


# ── real ingest_symbol on PostgreSQL ────────────────────────────────────────────────────

URL = os.environ.get("RZY_PG_URL")


@pytest.mark.skipif(not URL, reason="RZY_PG_URL is absent; required in make test-integration-pg-schema")
def test_real_ingest_skips_a_fresh_symbol_and_fetches_a_stale_one_on_postgresql():
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("_cooldown_skip_probe.py"))],
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    r = json.loads(run.stdout.strip().splitlines()[-1])
    assert r["first"]["skipped"] is None and r["fetches_after_first"] == 1
    assert r["fresh"] == {"skipped": "primary_cooldown", "fetches": 1}
    assert r["stale"] == {"skipped": None, "fetches": 2}
    assert r["uw_available"] == {"skipped": None, "fetches": 3}
