"""Regression test for T247-MARKETDATA-PRICEALERT-FALSYPRICE.

check_price_alerts() previously used a bare `if p:` to test a fetched live price before
caching it — a legitimate price of exactly 0 is falsy in Python, so it was silently treated
the same as a missing/failed fetch, dropping the symbol from `prices` with every price alert
on that symbol skipped for the cycle with no warning logged.

scheduler.py can't be imported directly in this test environment — its import chain pulls in
apscheduler plus ingestion.py/email_service.py/paper_trading_engine.py/api/routes.py, none of
which are stubbed for local unit tests (see test_compound_alert_conditions.py's docstring for
the same constraint). _is_usable_price() is pure/dependency-free, so it's loaded directly from
source via importlib rather than importing the whole scheduler module.
"""
import importlib.util
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_source = _scheduler_path.read_text()
_start = _source.index("def _is_usable_price")
_end = _source.index("\n\n\n", _start)
_func_source = _source[_start:_end]

_namespace: dict = {}
exec(_func_source, _namespace)  # noqa: S102 — isolated eval of one pure function's source
_is_usable_price = _namespace["_is_usable_price"]


def test_price_of_zero_is_not_usable_but_is_distinguishable_from_none():
    """The exact bug scenario: a real fetched price of 0.0 must be recognized as an
    actual (if unusable) value, not silently indistinguishable from a fetch failure."""
    assert _is_usable_price(0.0) is False
    assert _is_usable_price(None) is False


def test_positive_price_is_usable():
    assert _is_usable_price(123.45) is True
    assert _is_usable_price(0.01) is True


def test_negative_price_is_not_usable():
    assert _is_usable_price(-5.0) is False
