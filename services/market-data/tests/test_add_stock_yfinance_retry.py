"""Tests for BUG-ADDSTOCK-NORETRY (2026-08-07) — POST /admin/add_stock's yf.Ticker(symbol).info
fetch had zero retry, unlike YFinanceAdapter.fetch_ohlcv (the bulk-ingestion path), which already
tolerates a transient YFRateLimitError via a 3-attempt/1-8s-backoff tenacity policy. Reproduced
live in production: yfinance was actively rate-limiting the container (~37% of calls in a 30-min
window), and every single add_stock attempt for a not-yet-in-DB symbol failed on the very first
hit with no second chance, surfacing to the user as a 502 on the dashboard's "Add to Universe"
flow.

conftest.py stubs `yfinance`/`tenacity` wholesale as MagicMock (Docker-only-dependency
convention) — both are REAL, installed packages in this local dev environment. This test extracts
_fetch_yf_info()'s real source via exec() (rather than importing admin.py wholesale, which would
also need db/../adapters/../services/.auth faked out for a function with zero dependency on any
of them) and executes it against the REAL yfinance.exceptions classes and REAL tenacity retry
logic, matching this repo's established "load the real implementation instead of the blanket
stub" technique (test_yfinance_delisting_detection.py's stub-pop-and-restore for the same two
modules).
"""
import pathlib
import sys

import pytest

from unittest.mock import MagicMock  # noqa: E402

# BUG-ADDSTOCK-NORETRY's own discovery: popping a real yfinance package back out of
# sys.modules and reimporting it a SECOND time within the same pytest process does NOT
# reliably re-run yfinance/__init__.py's own lazy `.exceptions` submodule binding — confirmed
# directly in isolation (no pytest involved): a second cold pop+import of the real package
# produces a fresh module object missing the `.exceptions` attribute, even though
# `yfinance.exceptions` is independently importable as its own sys.modules entry. This bites
# whenever TWO test files (this one and test_yfinance_delisting_detection.py, both of which
# need the real yfinance/tenacity instead of conftest.py's MagicMock stub) each try their own
# pop-and-restore dance — whichever collects SECOND breaks, regardless of order, if either one
# restores the stub in between. Fix: only pop if the module is STILL the stub (i.e., nobody
# has already done this swap earlier in the same process) — a guarded, idempotent swap that
# is safe to run from multiple files without the double-reimport hazard, and deliberately
# never restores yfinance/tenacity back to a stub afterward (no other test file in this suite
# needs either to specifically be a MagicMock — grepped: none reference them as an imported
# name — so leaving the REAL modules loaded for the rest of the process is safe).
for _mod in ("yfinance", "tenacity"):
    if isinstance(sys.modules.get(_mod), MagicMock):
        sys.modules.pop(_mod, None)

import yfinance as yf  # noqa: E402
import yfinance.exceptions  # noqa: E402,F401 — force-bind the submodule as an attribute.
from tenacity import retry, retry_if_not_exception_type, stop_after_attempt, wait_exponential  # noqa: E402


def _extract_fetch_yf_info():
    """Source-text-extract _fetch_yf_info() from the real admin.py and exec() it against the
    REAL yf/tenacity imported above — admin.py itself can't be imported wholesale here without
    also faking db/../adapters/../services/.auth, none of which this one function touches."""
    admin_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
    source = admin_path.read_text()
    start = source.index("@retry(\n")
    end = source.index("\n\n\n@router.post(\"/add_stock\")", start)
    func_source = source[start:end]

    namespace = {
        "yf": yf,
        "retry": retry,
        "retry_if_not_exception_type": retry_if_not_exception_type,
        "stop_after_attempt": stop_after_attempt,
        "wait_exponential": wait_exponential,
    }
    exec(func_source, namespace)  # noqa: S102
    return namespace["_fetch_yf_info"]


_fetch_yf_info = _extract_fetch_yf_info()


class _RateLimitedThenOK:
    """Simulates yfinance raising YFRateLimitError on the first N calls, then succeeding."""

    def __init__(self, fail_times: int, info: dict):
        self.fail_times = fail_times
        self.calls = 0
        self._info = info

    def __call__(self, symbol: str):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise yf.exceptions.YFRateLimitError()
        return _FakeTicker(self._info)


class _FakeTicker:
    def __init__(self, info: dict):
        self.info = info


class _AlwaysMissing:
    """Simulates a genuinely delisted/nonexistent symbol — should NOT be retried."""

    def __init__(self):
        self.calls = 0

    def __call__(self, symbol: str):
        self.calls += 1
        raise yf.exceptions.YFTickerMissingError(symbol, "no data found, symbol may be delisted")


def test_retries_and_recovers_from_a_transient_rate_limit(monkeypatch):
    fake = _RateLimitedThenOK(fail_times=2, info={"longName": "Rocket Lab USA"})
    monkeypatch.setattr(yf, "Ticker", fake)

    result = _fetch_yf_info("RKLB")

    assert result == {"longName": "Rocket Lab USA"}
    assert fake.calls == 3


def test_gives_up_after_3_attempts_on_sustained_rate_limiting(monkeypatch):
    fake = _RateLimitedThenOK(fail_times=99, info={})
    monkeypatch.setattr(yf, "Ticker", fake)

    with pytest.raises(yf.exceptions.YFRateLimitError):
        _fetch_yf_info("RKLB")

    assert fake.calls == 3


def test_a_genuinely_missing_symbol_is_never_retried(monkeypatch):
    fake = _AlwaysMissing()
    monkeypatch.setattr(yf, "Ticker", fake)

    with pytest.raises(yf.exceptions.YFTickerMissingError):
        _fetch_yf_info("ZZZZNOTREAL")

    # The whole point of excluding YFTickerMissingError from the retry policy: a real 404
    # should fail fast, not waste ~3-11s retrying a condition retrying can never resolve.
    assert fake.calls == 1


def test_succeeds_immediately_when_yfinance_is_healthy(monkeypatch):
    fake = _RateLimitedThenOK(fail_times=0, info={"longName": "Apple Inc."})
    monkeypatch.setattr(yf, "Ticker", fake)

    result = _fetch_yf_info("AAPL")

    assert result == {"longName": "Apple Inc."}
    assert fake.calls == 1


def test_returns_empty_dict_when_info_is_falsy(monkeypatch):
    fake = _RateLimitedThenOK(fail_times=0, info=None)
    monkeypatch.setattr(yf, "Ticker", fake)

    result = _fetch_yf_info("XYZ")

    assert result == {}


def test_add_stock_endpoint_calls_the_retrying_helper_not_a_bare_yf_ticker_call():
    admin_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
    source = admin_path.read_text()
    start = source.index('@router.post("/add_stock")')
    end = source.index("\n\n\n# ── SL-1:", start)
    func_source = source[start:end]

    assert "_fetch_yf_info(symbol)" in func_source
    assert "yf.Ticker(symbol)" not in func_source, (
        "add_stock() must call the retrying _fetch_yf_info() helper, not construct a bare, "
        "unretried yf.Ticker(symbol) directly — that's the exact regression this fix closes."
    )
