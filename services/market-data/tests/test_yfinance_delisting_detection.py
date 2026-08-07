"""Tests for AUD-SURVIVORSHIP-DELISTDETECT's yfinance_adapter.py half — YFTickerMissingError
must raise immediately (not be retried) and must propagate uncaught out of fetch_ohlcv().

conftest.py stubs `yfinance`/`tenacity` wholesale as MagicMock (Docker-only-dependency
convention) — both are REAL, installed packages in this local dev environment (confirmed
directly: yfinance 1.5.1). This test pops those two stubs before importing anything, so the
REAL yfinance.exceptions classes and the REAL tenacity retry logic are exercised, matching
this repo's established "load the real implementation instead of the blanket stub" technique
(e.g. test_broker_position_sync.py's stub-pop-and-restore for sqlalchemy/db).
"""
import sys
from unittest.mock import MagicMock, patch

_STUBBED_MODULES = ("yfinance", "tenacity", "structlog", "common", "common.logging")
_saved_stubs = {}
for _mod in _STUBBED_MODULES:
    if _mod in ("yfinance", "tenacity") and not isinstance(sys.modules.get(_mod), MagicMock):
        # BUG-ADDSTOCK-NORETRY's discovery: already swapped to the real module by an earlier
        # test file in this same process (or by this file on a re-collection) — do NOT pop and
        # reimport a second time. A second cold pop+import of the real yfinance package does
        # not reliably re-run yfinance/__init__.py's own lazy `.exceptions` submodule binding,
        # leaving `yf.exceptions` absent even though `yfinance.exceptions` is independently
        # importable. Confirmed directly, isolated from pytest entirely. See the matching note
        # on the restore step below and in test_add_stock_yfinance_retry.py.
        _saved_stubs[_mod] = None
        continue
    _saved_stubs[_mod] = sys.modules.pop(_mod, None)

import importlib.util  # noqa: E402
import pathlib  # noqa: E402

import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402
from yfinance.exceptions import YFPricesMissingError, YFRateLimitError, YFTzMissingError  # noqa: E402

# yfinance_adapter.py does `from common.logging import get_logger` — stub just that one
# function rather than restoring the full blanket "common" stub, so the real yfinance/tenacity
# imports right above aren't clobbered again by re-registering the stub module objects.
import types  # noqa: E402
_fake_common_logging = types.ModuleType("common.logging")
_fake_common_logging.get_logger = MagicMock(return_value=MagicMock())
sys.modules["common.logging"] = _fake_common_logging
_fake_common = types.ModuleType("common")
sys.modules["common"] = _fake_common

_adapter_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "adapters" / "yfinance_adapter.py"
_base_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "adapters" / "base.py"
_registry_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "adapters" / "registry.py"

# base.py and registry.py are imported as relative imports (`.base`, `.registry`) inside
# yfinance_adapter.py — load them under a fake parent package first so the relative imports
# resolve, matching how a real `from ..adapters import ...`-style module would be found.
import types as _types  # noqa: E402
_pkg = _types.ModuleType("src.adapters")
_pkg.__path__ = [str(_adapter_path.parent)]
sys.modules["src.adapters"] = _pkg

for _name, _path in (("src.adapters.base", _base_path), ("src.adapters.registry", _registry_path)):
    _spec = importlib.util.spec_from_file_location(_name, _path)
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _mod
    _spec.loader.exec_module(_mod)

_spec = importlib.util.spec_from_file_location("src.adapters.yfinance_adapter", _adapter_path)
_yf_adapter_mod = importlib.util.module_from_spec(_spec)
sys.modules["src.adapters.yfinance_adapter"] = _yf_adapter_mod
_spec.loader.exec_module(_yf_adapter_mod)

# Restore the stubs for every OTHER test file collected in the same pytest run — EXCEPT
# yfinance/tenacity themselves. BUG-ADDSTOCK-NORETRY's own test file (test_add_stock_yfinance_
# retry.py) needs the REAL yfinance/tenacity too, and popping a real yfinance package back out
# and reimporting it a SECOND time within the same pytest process does not reliably re-run
# yfinance/__init__.py's own lazy `.exceptions` submodule binding — confirmed directly: whichever
# of these two files collects SECOND fails with `AttributeError: module 'yfinance' has no
# attribute 'exceptions'`, regardless of collection order, if either one restores the stub. No
# other test file in this suite needs `yfinance`/`tenacity` to specifically be a MagicMock
# (grepped: none reference them as an imported name) — leaving the REAL modules loaded for the
# rest of the process is safe.
for _mod, _val in _saved_stubs.items():
    if _mod in ("yfinance", "tenacity"):
        continue
    if _val is not None:
        sys.modules[_mod] = _val
    else:
        sys.modules.pop(_mod, None)

YFinanceAdapter = _yf_adapter_mod.YFinanceAdapter


def _adapter():
    return YFinanceAdapter()


class TestYFTickerMissingErrorNotRetried:
    def test_raises_immediately_without_retrying_on_ticker_missing_error(self):
        """YFTickerMissingError must be excluded from the retry policy — retrying a genuine
        delisting/no-data condition 3x wastes time and delays the signal for no benefit,
        unlike a transient rate-limit error where retrying legitimately helps."""
        call_count = 0

        def _raise_missing(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise YFTzMissingError("FAKE")

        with patch.object(yf, "Ticker") as MockTicker:
            MockTicker.return_value.history.side_effect = _raise_missing
            adapter = _adapter()
            try:
                adapter.fetch_ohlcv("FAKE", __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 8))
                assert False, "expected YFTzMissingError to propagate"
            except YFTzMissingError:
                pass
        assert call_count == 1, f"expected exactly 1 call (no retry), got {call_count}"

    def test_a_generic_exception_still_retries_3_times(self):
        """Confirms the retry-exclusion is scoped correctly — a genuine transient error
        (not a YFTickerMissingError) must still retry, matching the pre-existing behavior."""
        call_count = 0

        def _raise_generic(*a, **kw):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("network blip")

        with patch.object(yf, "Ticker") as MockTicker:
            MockTicker.return_value.history.side_effect = _raise_generic
            adapter = _adapter()
            try:
                adapter.fetch_ohlcv("FAKE", __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 8))
                assert False, "expected ConnectionError to propagate after retries"
            except ConnectionError:
                pass
        assert call_count == 3, f"expected 3 retry attempts, got {call_count}"

    def test_yfratelimiterror_is_not_a_ticker_missing_error_subclass(self):
        """The structural guarantee the whole design depends on: a genuine rate-limit error
        must never be mistaken for a delisting signal."""
        assert not issubclass(YFRateLimitError, _yf_adapter_mod.yf.exceptions.YFTickerMissingError)

    def test_prices_missing_error_is_a_ticker_missing_error_subclass(self):
        assert issubclass(YFPricesMissingError, _yf_adapter_mod.yf.exceptions.YFTickerMissingError)

    def test_tz_missing_error_is_a_ticker_missing_error_subclass(self):
        assert issubclass(YFTzMissingError, _yf_adapter_mod.yf.exceptions.YFTickerMissingError)

    def test_empty_dataframe_without_exception_still_returns_empty_ohlcv(self):
        """A plain empty-but-not-erroring history() response (the pre-existing, non-delisting
        empty-data path) must still work exactly as before this change."""
        with patch.object(yf, "Ticker") as MockTicker:
            MockTicker.return_value.history.return_value = pd.DataFrame()
            adapter = _adapter()
            result = adapter.fetch_ohlcv("FAKE", __import__("datetime").date(2026, 1, 1), __import__("datetime").date(2026, 1, 8))
            assert result.df.empty
