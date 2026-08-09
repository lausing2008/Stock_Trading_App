"""Tests for AUD263-WATCHDOG-MASKS-VALIDATED-THRESHOLD's fix — _clear_watchdog_override().

calibration.py can't be imported directly in this environment (it needs common.jwt_auth /
FastAPI Depends / db, none for-real-installed here — matching test_calibrate_ta_weights_
validation.py's own documented constraint for this exact file) — _clear_watchdog_override()'s
real source is extracted via exec() and run against a fake Redis client + fake log object,
matching this repo's established source-text-extraction convention. The two write-site call
sites (outcomes_calibrate_apply, tune_strategy) are covered by source-text regression checks.
"""
import pathlib
from unittest.mock import MagicMock

_CALIBRATION_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CALIBRATION_SOURCE = _CALIBRATION_PATH.read_text()


def _extract_clear_watchdog_override(fake_redis, fake_log):
    start = _CALIBRATION_SOURCE.index("def _clear_watchdog_override(")
    end = _CALIBRATION_SOURCE.index("\n\n\n", start)
    namespace = {"_get_redis": lambda: fake_redis, "log": fake_log}
    exec(_CALIBRATION_SOURCE[start:end], namespace)  # noqa: S102
    return namespace["_clear_watchdog_override"]


class _FakeRedis:
    def __init__(self, seed: dict[str, str] | None = None):
        self._store = dict(seed or {})
        self.deleted: list[str] = []

    def get(self, key):
        return self._store.get(key)

    def delete(self, key):
        self.deleted.append(key)
        self._store.pop(key, None)


# ── _clear_watchdog_override() — direct behavioral tests against the real, extracted source ──

def test_deletes_both_the_threshold_and_tighten_count_keys():
    fake_redis = _FakeRedis({
        "stockai:watchdog:SWING:threshold": "0.71",
        "stockai:watchdog:SWING:tighten_count": "2",
    })
    fn = _extract_clear_watchdog_override(fake_redis, MagicMock())
    fn("SWING")
    assert "stockai:watchdog:SWING:threshold" in fake_redis.deleted
    assert "stockai:watchdog:SWING:tighten_count" in fake_redis.deleted
    assert fake_redis.get("stockai:watchdog:SWING:threshold") is None
    assert fake_redis.get("stockai:watchdog:SWING:tighten_count") is None


def test_no_op_when_no_override_was_active():
    """A style with no active watchdog override must not raise or log a spurious 'cleared'
    message — deletes on absent keys are a no-op in real Redis, and the function's own log
    line is explicitly gated on had_override to avoid misleading noise."""
    fake_redis = _FakeRedis()
    fake_log = MagicMock()
    fn = _extract_clear_watchdog_override(fake_redis, fake_log)
    fn("GROWTH")
    fake_log.info.assert_not_called()  # nothing logged — no override was ever active to clear


def test_logs_when_an_override_was_actually_cleared():
    fake_redis = _FakeRedis({"stockai:watchdog:LONG:threshold": "0.68"})
    fake_log = MagicMock()
    fn = _extract_clear_watchdog_override(fake_redis, fake_log)
    fn("LONG")
    fake_log.info.assert_called_once_with(
        "signal_watchdog.override_cleared_by_validated_recalibration", style="LONG"
    )


def test_redis_failure_does_not_raise():
    fake_redis = MagicMock()
    fake_redis.get.side_effect = RuntimeError("redis down")
    fn = _extract_clear_watchdog_override(fake_redis, MagicMock())
    fn("SHORT")  # must not raise


def test_only_touches_the_named_styles_keys():
    fake_redis = _FakeRedis({
        "stockai:watchdog:SWING:threshold": "0.71",
        "stockai:watchdog:SHORT:threshold": "0.60",
    })
    fn = _extract_clear_watchdog_override(fake_redis, MagicMock())
    fn("SWING")
    assert fake_redis.get("stockai:watchdog:SHORT:threshold") == "0.60"  # untouched


# ── Write-site wiring — source-text regression checks ───────────────────────────────────────

def test_outcomes_calibrate_apply_clears_the_watchdog_override_on_a_fresh_write():
    start = _CALIBRATION_SOURCE.index("def outcomes_calibrate_apply(")
    end = _CALIBRATION_SOURCE.index("\n@router.", start + 1)
    body = _CALIBRATION_SOURCE[start:end]
    write_idx = body.index('redis_client.setex(redis_key, _REDIS_TTL, str(round(best_t, 4)))')
    clear_idx = body.index("_clear_watchdog_override(h)")
    history_idx = body.index("_record_tune_history(\n            session, _run_id, \"signal_threshold\", \"buy_threshold\"")
    # The clear must happen AFTER the write (so it only fires once the new value is actually
    # committed to Redis) and BEFORE the TuneHistory record (matching every other side-effect
    # ordering in this file — the record call is always the last step of a promoted branch).
    assert write_idx < clear_idx < history_idx


def test_tune_strategy_clears_the_watchdog_override_on_a_fresh_write():
    start = _CALIBRATION_SOURCE.index("def tune_strategy(")
    end = _CALIBRATION_SOURCE.index('@router.post("/watchdog")', start)
    body = _CALIBRATION_SOURCE[start:end]
    write_idx = body.index('redis_client.setex(_buy_thresh_redis_key, _REDIS_TTL, str(round(best_buy, 4)))')
    clear_idx = body.index("_clear_watchdog_override(h)")
    history_idx = body.index('_record_tune_history(\n            session, _run_id, "joint_strategy"')
    assert write_idx < clear_idx < history_idx


def test_signal_watchdog_itself_never_calls_clear_on_its_own_write():
    """The watchdog's OWN tighten/relax writes must never clear themselves — only a DIFFERENT,
    validated mechanism's write should reset the watchdog's state. If signal_watchdog() ever
    called _clear_watchdog_override() on its own action, it would immediately erase the very
    override it just set."""
    start = _CALIBRATION_SOURCE.index("def signal_watchdog(")
    end = _CALIBRATION_SOURCE.index("\n@router.", start + 1)
    body = _CALIBRATION_SOURCE[start:end]
    assert "_clear_watchdog_override(" not in body
