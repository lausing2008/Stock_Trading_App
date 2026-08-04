"""Tests for T232-DL-REGIME5X's remaining gap: _fetch_market_regime() (the US regime
classifier) was called fresh — a real yfinance download + full reclassification — on EVERY
_refresh_5m tick during market hours, with no debounce of any kind. _regime_cache/
_regime_cache_ts existed only as a 4-hour FAILURE fallback (inside the except block), never as
a rate limiter for the normal happy path — unlike _fetch_hk_market_regime(), which already had
a 30-minute freshness check at its own top.

Fix: added the identical 30-minute freshness check to _fetch_market_regime(), mirroring HK's
own already-proven pattern exactly (same TTL, not a new number). 30 minutes is comfortably
above the function's own _REGIME_HYSTERESIS_TICKS mechanism (2 consecutive calls, ~10 min at
the 5-min refresh cadence), so this doesn't sacrifice any real responsiveness.

paper_trading_engine.py imports cleanly in this test environment (confirmed directly — no
apscheduler/db.models import-chain issue for this specific module), so these are real
behavioral tests against the actual function, not source-text extraction.
"""
import sys
import time
import types

import numpy as np
import pandas as pd

import src.services.paper_trading_engine as pte


def _reset_regime_cache():
    pte._regime_cache = {}
    pte._regime_cache_ts = 0.0


def _real_shaped_download_fixture() -> pd.DataFrame:
    """A REAL-shaped multi-ticker Close DataFrame, matching exactly what yf.download() returns
    for a multi-symbol request — used so a successful (non-raising) fake download can be
    distinguished from _fetch_market_regime()'s own pre-existing exception-fallback path.

    A first draft of these tests used a fake download() that RAISED to signal "was this
    called" — but that accidentally routed through the function's own separate, pre-existing
    except-block cache-fallback logic (lines ~1133-1144), which ALSO returns the cached dict on
    any error, masking whether the NEW debounce check (vs. the OLD exception-fallback) was what
    actually produced the result. A fake that succeeds with real-shaped data avoids that
    ambiguity entirely — if it's called, the function completes normally, not via the
    error-recovery path.
    """
    n = 300
    idx = pd.date_range("2025-01-01", periods=n)
    cols = pd.MultiIndex.from_product([["Close"], ["SPY", "QQQ", "^VIX", "^VIX9D", "IWM", "MDY"]])
    rng = np.random.default_rng(1)
    data = rng.normal(0.3, 1.0, (n, 6)).cumsum(axis=0) + 400
    return pd.DataFrame(data, index=idx, columns=cols)


def _install_fake_yfinance(monkeypatch, call_counter: dict):
    fake_yf = types.ModuleType("yfinance")

    def _download(*a, **kw):
        call_counter["n"] += 1
        return _real_shaped_download_fixture()

    fake_yf.download = _download
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)


def test_fresh_cache_short_circuits_before_any_yfinance_call(monkeypatch):
    """The exact fix: a cache younger than 30 minutes must return immediately, never
    reaching the yfinance download at all."""
    _reset_regime_cache()
    pte._regime_cache = {"state": "bull", "spy_price": 500.0}
    pte._regime_cache_ts = time.time() - 60  # 1 minute old — well within the 30-min window

    call_counter = {"n": 0}
    _install_fake_yfinance(monkeypatch, call_counter)

    result = pte._fetch_market_regime({})

    assert call_counter["n"] == 0, "yfinance.download must not be called when the cache is fresh"
    assert result["state"] == "bull"
    assert result["spy_price"] == 500.0


def test_stale_cache_beyond_30_minutes_triggers_a_real_refetch(monkeypatch):
    """A cache older than 30 minutes must NOT short-circuit — the debounce is a freshness
    window, not a permanent skip, so real reclassification must still happen eventually."""
    _reset_regime_cache()
    pte._regime_cache = {"state": "bull", "spy_price": 500.0}
    pte._regime_cache_ts = time.time() - 1900  # ~31.7 minutes old — just past the 30-min window

    call_counter = {"n": 0}
    _install_fake_yfinance(monkeypatch, call_counter)

    result = pte._fetch_market_regime({})

    assert call_counter["n"] == 1, "a stale cache must trigger a real yfinance call, not another short-circuit"
    # A real classification ran (not the exception-fallback path) — state reflects the fixture
    # data, not the stale cached "bull" value verbatim.
    assert "state" in result


def test_empty_cache_always_triggers_a_real_fetch(monkeypatch):
    """A container that just started (empty cache, ts=0.0) must never short-circuit on the
    freshness check — there's nothing valid to return yet."""
    _reset_regime_cache()

    call_counter = {"n": 0}
    _install_fake_yfinance(monkeypatch, call_counter)

    pte._fetch_market_regime({})

    assert call_counter["n"] == 1


def test_debounce_ttl_matches_hk_regimes_own_proven_1800_second_value():
    """The fix deliberately reuses HK's own already-production-proven TTL rather than
    inventing a new number — confirms both freshness checks use the identical 1800-second
    (30-minute) window, not two different values that could silently drift apart."""
    import inspect
    us_source = inspect.getsource(pte._fetch_market_regime)
    hk_source = inspect.getsource(pte._fetch_hk_market_regime)
    assert "< 1800" in us_source
    assert "< 1800" in hk_source


def test_debounce_check_happens_before_the_yfinance_import_not_after(monkeypatch):
    """Confirms the freshness check is a genuine early-return BEFORE any network call is
    attempted — not, say, a check that happens to exist somewhere in the function but after
    the download already fired (which would defeat the whole point of the fix)."""
    import inspect
    source = inspect.getsource(pte._fetch_market_regime)
    debounce_idx = source.index("< 1800")
    download_idx = source.index("yf.download(")
    assert debounce_idx < download_idx


def teardown_module(module):
    """Restore the module's real cache state to empty after this file's tests run, so later
    test files in the same pytest session don't see a stale mocked/simulated regime cache."""
    _reset_regime_cache()
