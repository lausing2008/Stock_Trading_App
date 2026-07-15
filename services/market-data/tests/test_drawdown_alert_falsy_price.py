"""Regression test for T247-MARKETDATA-DRAWDOWNALERT-FALSYPRICE.

The portfolio-drawdown alert (T230-ALERTING-PORTFOLIO-ALERTS) previously used `not cur_px` to
gate the drawdown check — a legitimate fetched price of 0 (a delisted/halted ticker briefly
reporting 0) was treated the same as a fetch failure and silently skipped, even though a 0
price on an open position is exactly the kind of extreme drawdown (-100%) this alert exists to
catch.

scheduler.py can't be imported directly in this test environment (see
test_price_alert_price_check.py's docstring for the same constraint) — _drawdown_alert_should_
skip() is pure/dependency-free, so it's loaded directly from source via importlib.
"""
import importlib.util
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_source = _scheduler_path.read_text()
_start = _source.index("def _drawdown_alert_should_skip")
_end = _source.index("\n\n\n", _start)
_func_source = _source[_start:_end]

_namespace: dict = {}
exec(_func_source, _namespace)  # noqa: S102 — isolated eval of one pure function's source
_drawdown_alert_should_skip = _namespace["_drawdown_alert_should_skip"]


def test_zero_current_price_is_not_skipped_it_is_a_real_extreme_drawdown():
    """The exact bug scenario: a genuinely fetched price of 0.0 must NOT be treated as a
    fetch failure — it's a real (if extreme) price that should be evaluated for drawdown."""
    assert _drawdown_alert_should_skip(cur_px=0.0, entry_price=100.0) is False


def test_none_current_price_is_skipped_as_a_genuine_fetch_failure():
    assert _drawdown_alert_should_skip(cur_px=None, entry_price=100.0) is True


def test_missing_entry_price_is_skipped():
    assert _drawdown_alert_should_skip(cur_px=50.0, entry_price=None) is True
    assert _drawdown_alert_should_skip(cur_px=50.0, entry_price=0.0) is True


def test_negative_entry_price_is_skipped():
    assert _drawdown_alert_should_skip(cur_px=50.0, entry_price=-10.0) is True


def test_normal_prices_are_not_skipped():
    assert _drawdown_alert_should_skip(cur_px=95.0, entry_price=100.0) is False
