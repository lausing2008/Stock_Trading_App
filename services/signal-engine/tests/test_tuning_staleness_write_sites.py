"""Source-text regression tests for AUD263-TUNED-PARAMS-SILENTLY-REVERT-ON-TTL's write-side
wiring — confirms every real TTL'd tuned-parameter Redis write in calibration.py is paired with
a _mark_tuned() call, and that tune_status() surfaces staleness for both per-style and global
mechanisms. calibration.py can't be imported directly in this environment (needs
common.jwt_auth/FastAPI Depends/db) — matching every other calibration.py test file's
established source-text-extraction convention.

Deliberately excludes signal_watchdog()'s own 2 setex() sites (the 7-day emergency-nudge key) —
per this fix's own design, the watchdog is a self-expiring-by-design mechanism that already has
a separate visibility path (override_active_no_action logging), not a silent-reversion risk the
same way the 30/90-day calibration keys are.
"""
import pathlib

_CAL_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CAL_SOURCE = _CAL_PATH.read_text()


def _function_body(func_name: str) -> str:
    start = _CAL_SOURCE.index(f"def {func_name}(")
    next_def = _CAL_SOURCE.index("\ndef ", start + 1)
    next_router = _CAL_SOURCE.find("\n@router", start + 1)
    end = min(x for x in (next_def, next_router) if x != -1)
    return _CAL_SOURCE[start:end]


def test_mark_tuned_is_imported_from_signals_shared():
    assert "_mark_tuned" in _CAL_SOURCE.split("from .signals_shared import")[1].split(")")[0]


def test_calibrate_ta_weights_marks_tuned_after_its_setex():
    body = _function_body("calibrate_ta_weights")
    write_idx = body.index('setex("stockai:ta_weights"')
    mark_idx = body.index('_mark_tuned("stockai:ta_weights")')
    assert write_idx < mark_idx


def test_calibrate_conviction_weights_marks_tuned_after_its_setex():
    body = _function_body("calibrate_conviction_weights")
    write_idx = body.index('setex("stockai:conviction_weights"')
    mark_idx = body.index('_mark_tuned("stockai:conviction_weights")')
    assert write_idx < mark_idx


def test_outcomes_calibrate_apply_marks_tuned_for_both_buy_and_sell():
    body = _function_body("outcomes_calibrate_apply")
    # BUY threshold
    buy_write = body.index("redis_client.setex(redis_key, _REDIS_TTL")
    buy_mark = body.index("_mark_tuned(redis_key)")
    assert buy_write < buy_mark
    # SELL threshold
    sell_write = body.index("redis_client.setex(_sell_redis_key, _REDIS_TTL")
    sell_mark = body.index("_mark_tuned(_sell_redis_key)")
    assert sell_write < sell_mark


def test_tune_sell_pillars_marks_tuned():
    body = _function_body("tune_sell_pillars")
    write_idx = body.index("redis_client.setex(_pillars_redis_key, _REDIS_TTL")
    mark_idx = body.index("_mark_tuned(_pillars_redis_key)")
    assert write_idx < mark_idx


def test_tune_style_profiles_marks_tuned_for_all_4_write_sites():
    body = _function_body("tune_style_profiles")
    # ml_weight_cap
    assert body.index("redis_client.setex(_ml_cap_redis_key, _REDIS_TTL") < body.index("_mark_tuned(_ml_cap_redis_key)")
    # adx_min
    assert body.index("redis_client.setex(_adx_redis_key, _REDIS_TTL") < body.index("_mark_tuned(_adx_redis_key)")
    # breadth_compression appears twice (two distinct promotion branches) — both must mark.
    assert body.count("_mark_tuned(_bc_redis_key)") == 2
    assert body.count("redis_client.setex(_bc_redis_key, _REDIS_TTL") == 2


def test_tune_strategy_marks_tuned_for_both_buy_and_ml_cap():
    body = _function_body("tune_strategy")
    assert body.index("redis_client.setex(_buy_thresh_redis_key, _REDIS_TTL") < body.index("_mark_tuned(_buy_thresh_redis_key)")
    assert body.index("redis_client.setex(_ml_cap_redis_key, _REDIS_TTL") < body.index("_mark_tuned(_ml_cap_redis_key)")


def test_signal_watchdog_does_not_call_mark_tuned():
    """The watchdog's 7-day emergency-nudge key is deliberately excluded from this mechanism —
    it already self-expires by design and has its own separate visibility
    (override_active_no_action logging), unlike the 30/90-day calibration keys."""
    body = _function_body("signal_watchdog")
    assert "_mark_tuned(" not in body


def test_tune_status_surfaces_per_style_staleness_for_all_4_tuned_fields():
    body = _function_body("tune_status")
    assert '"calibrated_threshold": _tuning_staleness(f"stockai:signal_thresholds:{style}")' in body
    assert '"ml_weight_cap": _tuning_staleness(f"stockai:style_tune:{style}:ml_weight_cap")' in body
    assert '"adx_min": _tuning_staleness(f"stockai:style_tune:{style}:adx_min")' in body
    assert '"breadth_compression": _tuning_staleness(f"stockai:style_tune:{style}:breadth_compression")' in body


def test_tune_status_surfaces_global_staleness_for_ta_and_conviction_weights():
    body = _function_body("tune_status")
    assert '"ta_weights": _tuning_staleness("stockai:ta_weights")' in body
    assert '"conviction_weights": _tuning_staleness("stockai:conviction_weights")' in body
