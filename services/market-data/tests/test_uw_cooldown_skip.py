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


# ── real ingest_symbol on PostgreSQL ────────────────────────────────────────────────────

URL = os.environ.get("RZY_PG_URL")


@pytest.mark.skipif(not URL, reason="RZY_PG_URL is absent; required in make test-integration-pg-schema")
def test_real_ingest_skips_a_fresh_symbol_and_fetches_a_stale_one_on_postgresql():
    # TZ=UTC as in the container: ingest's window ends at date.today(), a local date.
    run = subprocess.run([sys.executable, str(pathlib.Path(__file__).with_name("_cooldown_skip_probe.py"))],
                         capture_output=True, text=True, timeout=120, env={**os.environ, "TZ": "UTC"})
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-4000:]
    r = json.loads(run.stdout.strip().splitlines()[-1])
    assert r["first"]["skipped"] is None and r["fetches_after_first"] == 1
    assert r["fresh"] == {"skipped": "primary_cooldown", "fetches": 1}
    assert r["stale"] == {"skipped": None, "fetches": 2}
    assert r["uw_available"] == {"skipped": None, "fetches": 3}
    assert r["partial_5m"] == {"skipped": "primary_cooldown", "completion_pending": True, "fetched": 0}
    assert r["completed_5m"] == {"skipped": "primary_cooldown", "completion_pending": False, "fetched": 0}


# ── AUD-RZY-PARTIALCANDLE: a skipped partial candle is completed from the primary ───────────

class _Prov:
    def __init__(self, last_written_at):
        self.last_written_at = last_written_at


class _ProvSession:
    def __init__(self, rows):
        self.rows = rows

    def get(self, model, key):
        return self.rows.get(key)


HEAD = _naive(IN_SESSION - timedelta(minutes=5))          # 15:25 ET bar, closed at 15:30 (= now)


def _pending(tf, head, rows, now=IN_SESSION):
    return ING._head_needs_completion(_ProvSession(rows), _Stock(), "US", tf, head, now=now)


def test_a_closed_head_written_only_while_forming_needs_completion():
    """Nov 6 ANF: written 27 s into the candle, never rewritten after its close."""
    assert _pending("5m", HEAD, {(7, "5m", HEAD): _Prov(HEAD + timedelta(seconds=27))}) is True


def test_a_head_rewritten_after_its_close_does_not():
    assert _pending("5m", HEAD, {(7, "5m", HEAD): _Prov(HEAD + timedelta(minutes=5, seconds=1))}) is False


def test_a_head_with_no_provenance_is_treated_as_partial():
    assert _pending("5m", HEAD, {}) is True


def test_a_still_forming_head_is_left_to_the_next_run():
    forming = _naive(IN_SESSION - timedelta(minutes=2))
    assert _pending("5m", forming, {}) is False


def test_the_1h_head_of_the_last_hour_completes_at_the_session_close():
    """The 15:30 ET hour bar ends at 16:00, not 16:30 (bar_provenance._completed_at)."""
    head = _naive(datetime(2026, 10, 28, 19, 30, tzinfo=timezone.utc))       # 15:30 ET
    at_close = datetime(2026, 10, 28, 20, 1, tzinfo=timezone.utc)            # 16:01 ET
    written_1555 = _naive(datetime(2026, 10, 28, 19, 55, tzinfo=timezone.utc))
    assert _pending("1h", head, {(7, "1h", head): _Prov(written_1555)}, now=at_close) is True
    written_1600 = _naive(datetime(2026, 10, 28, 20, 0, 30, tzinfo=timezone.utc))
    assert _pending("1h", head, {(7, "1h", head): _Prov(written_1600)}, now=at_close) is False


def test_daily_and_first_ingests_never_need_completion():
    assert _pending("1d", datetime(2026, 10, 28), {}) is False
    assert _pending("5m", None, {}) is False


def _universe(monkeypatch, outcomes, remaining=12.0):
    """outcomes: symbol -> list of results returned by successive ingest_symbol calls."""
    calls, slept = [], []

    def fake_ingest(sym, timeframe="1d", force=False, **kw):
        calls.append((sym, timeframe, force, tuple(sorted(kw))))
        return outcomes[sym].pop(0)

    monkeypatch.setattr(ING, "ingest_symbol", fake_ingest)
    monkeypatch.setattr(ING, "_bust_live_price_cache", lambda: None)
    monkeypatch.setattr(ING, "_completion_sleep", slept.append)
    monkeypatch.setattr(UWA, "cooldown_remaining_s", lambda: remaining)
    return calls, slept


PENDING = {"symbol": "X", "skipped": "primary_cooldown", "completion_pending": True}
FRESH = {"symbol": "X", "skipped": "primary_cooldown", "completion_pending": False}
DONE = {"symbol": "X", "inserted": 13}


def test_pending_symbols_are_refetched_once_after_the_cooldown(monkeypatch):
    # A is still pending after the retry; B completes; C was fresh; D was fetched normally.
    calls, slept = _universe(monkeypatch, {"A": [dict(PENDING), dict(PENDING)], "B": [dict(PENDING), dict(DONE)],
                                           "C": [dict(FRESH)], "D": [dict(DONE)]})
    out = ING.ingest_universe(["A", "B", "C", "D"], timeframe="5m")
    assert sorted(c[0] for c in calls) == ["A", "A", "B", "B", "C", "D"]      # one retry, pending only
    assert slept == [12.5]                                                    # waited out the cooldown
    assert out[1] == DONE and out[0]["completion_pending"] is True            # B completed, A left pending
    assert all(c[3] == () for c in calls), "no provider override: the retry never forces the fallback"


def test_no_pending_symbol_means_no_wait_and_no_retry(monkeypatch):
    calls, slept = _universe(monkeypatch, {"C": [dict(FRESH)], "D": [dict(DONE)]})
    ING.ingest_universe(["C", "D"], timeframe="5m")
    assert len(calls) == 2 and slept == []


def test_the_wait_is_bounded_and_skipped_when_no_cooldown_is_armed(monkeypatch):
    calls, slept = _universe(monkeypatch, {"A": [dict(PENDING), dict(DONE)]}, remaining=10_000)
    ING.ingest_universe(["A"], timeframe="5m")
    assert slept == [ING._COMPLETION_WAIT_MAX_S + 0.5]
    calls, slept = _universe(monkeypatch, {"A": [dict(PENDING), dict(DONE)]}, remaining=0.0)
    ING.ingest_universe(["A"], timeframe="5m")
    assert slept == [] and len(calls) == 2


def test_cooldown_remaining_fails_open(monkeypatch):
    import common.redis_client as RC

    def boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr(RC, "get_redis", boom)
    assert UWA.cooldown_remaining_s() == 0.0


def test_the_skip_reports_completion_pending_before_any_fetch():
    src = pathlib.Path(ING.__file__).read_text()
    j = src.index("_skip_during_primary_cooldown(session, stock, market, timeframe, head, adapters)")
    k = src.index("# T230-CHARTING-PREMARKET", j)
    block = src[j:k]
    assert "_head_needs_completion(session, stock, market, timeframe, head)" in block
    assert '"completion_pending": pending' in block and "fetch_ohlcv" not in block
