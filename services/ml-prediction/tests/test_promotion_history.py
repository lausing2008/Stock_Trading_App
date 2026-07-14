"""Regression test for T247-MLPREDICTION-PROMOTIONHISTORY-RACE.

_record_promotion_status() previously did an unsynchronized GET/append-in-Python/SETEX on
the meta_model:promotion_history Redis key — two concurrent retrains could both read the
same list, each append their own run, and each write back, with the second write silently
clobbering the first's append. RPUSH/LTRIM/EXPIRE are native, atomic Redis operations — no
read-modify-write race is possible even under concurrent writers.
"""
import importlib.util as _ilu
import json
import pathlib as _pathlib
import sys
from unittest.mock import MagicMock

_meta_trainer_path = _pathlib.Path(__file__).resolve().parents[1] / "src" / "training" / "meta_trainer.py"

_fake_trainer = MagicMock()
_fake_trainer._HORIZON_BY_STYLE = {"SHORT": 5, "SWING": 10, "LONG": 20, "GROWTH": 10}
sys.modules.setdefault("src.training", MagicMock())
sys.modules["src.training.trainer"] = _fake_trainer

_spec = _ilu.spec_from_file_location("meta_trainer_promotion_test", _meta_trainer_path)
_meta_trainer_mod = _ilu.module_from_spec(_spec)
_meta_trainer_mod.__package__ = "src.training"
_spec.loader.exec_module(_meta_trainer_mod)

_record_promotion_status = _meta_trainer_mod._record_promotion_status


def test_uses_rpush_not_get_then_setex(monkeypatch):
    """The core regression guard: the write path must use RPUSH (atomic append), never a
    GET-then-SETEX read-modify-write on the history key."""
    fake_redis = MagicMock()
    fake_redis_lib = MagicMock()
    fake_redis_lib.from_url.return_value = fake_redis
    monkeypatch.setitem(sys.modules, "redis", fake_redis_lib)

    _record_promotion_status(promoted=True, auc=0.72, previous_auc=0.68, n_samples=500)

    fake_redis.rpush.assert_called_once()
    args, _ = fake_redis.rpush.call_args
    assert args[0] == "meta_model:promotion_history"
    entry = json.loads(args[1])
    assert entry["promoted"] is True
    assert entry["auc"] == 0.72

    # GET on the history key must never be called — that's the read-modify-write pattern
    # this fix removes entirely.
    for call in fake_redis.get.call_args_list:
        assert call.args[0] != "meta_model:promotion_history"


def test_ltrim_keeps_only_last_20():
    fake_redis = MagicMock()
    fake_redis_lib = MagicMock()
    fake_redis_lib.from_url.return_value = fake_redis
    import sys as _sys
    _sys.modules["redis"] = fake_redis_lib

    _record_promotion_status(promoted=False, auc=0.55, previous_auc=0.60, n_samples=200)

    fake_redis.ltrim.assert_called_once_with("meta_model:promotion_history", -20, -1)


def test_expire_sets_the_90_day_ttl():
    fake_redis = MagicMock()
    fake_redis_lib = MagicMock()
    fake_redis_lib.from_url.return_value = fake_redis
    import sys as _sys
    _sys.modules["redis"] = fake_redis_lib

    _record_promotion_status(promoted=True, auc=0.70, previous_auc=None, n_samples=100)

    fake_redis.expire.assert_called_once_with("meta_model:promotion_history", 86400 * 90)


def test_redis_failure_does_not_raise():
    """Best-effort semantics: a Redis failure here must never break the retrain itself."""
    fake_redis_lib = MagicMock()
    fake_redis_lib.from_url.side_effect = ConnectionError("redis unreachable")
    import sys as _sys
    _sys.modules["redis"] = fake_redis_lib

    _record_promotion_status(promoted=True, auc=0.70, previous_auc=None, n_samples=100)  # must not raise
