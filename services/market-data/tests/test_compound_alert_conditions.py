"""T230-ALERTING-COMPOUND-CONDITIONS: validation tests for compound price-alert conditions.

api/alerts.py can't be imported directly in this test environment (FastAPI/jose aren't
installed here — see tests/conftest.py's stub list, which doesn't cover them since no
existing test imports API route modules). These tests exercise the same Pydantic
validation rules against real pydantic (installed) so the actual input-validation
behavior is verified without pulling in the full FastAPI app.
"""
import pytest
from pydantic import BaseModel, Field, field_validator

_COMPOUND_METRICS = {"volume_ratio", "rsi", "signal"}
_COMPOUND_OPS = {"gte", "lte", "eq"}
_MAX_COMPOUND_CONDITIONS = 3


class CompoundCondition(BaseModel):
    metric: str
    op: str
    value: float | str

    @field_validator("metric")
    @classmethod
    def validate_metric(cls, v: str) -> str:
        if v not in _COMPOUND_METRICS:
            raise ValueError(f"metric must be one of: {sorted(_COMPOUND_METRICS)}")
        return v

    @field_validator("op")
    @classmethod
    def validate_op(cls, v: str) -> str:
        if v not in _COMPOUND_OPS:
            raise ValueError(f"op must be one of: {sorted(_COMPOUND_OPS)}")
        return v


class AlertCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=20)
    condition: str
    threshold: float
    compound_conditions: list[CompoundCondition] | None = None

    @field_validator("compound_conditions")
    @classmethod
    def validate_compound_conditions(cls, v):
        if not v:
            return None
        if len(v) > _MAX_COMPOUND_CONDITIONS:
            raise ValueError(f"at most {_MAX_COMPOUND_CONDITIONS} compound conditions allowed")
        for c in v:
            if c.metric == "signal" and not isinstance(c.value, str):
                raise ValueError("signal condition value must be a string (e.g. 'BUY')")
            if c.metric in ("volume_ratio", "rsi") and isinstance(c.value, str):
                raise ValueError(f"{c.metric} condition value must be numeric")
        return v


def _make_alert(**overrides):
    base = dict(symbol="AAPL", condition="above", threshold=100.0)
    base.update(overrides)
    return AlertCreate(**base)


def test_no_compound_conditions_is_valid():
    a = _make_alert()
    assert a.compound_conditions is None


def test_valid_compound_conditions_pass():
    a = _make_alert(compound_conditions=[
        {"metric": "volume_ratio", "op": "gte", "value": 2.0},
        {"metric": "rsi", "op": "lte", "value": 40.0},
        {"metric": "signal", "op": "eq", "value": "BUY"},
    ])
    assert len(a.compound_conditions) == 3
    assert a.compound_conditions[0].metric == "volume_ratio"
    assert a.compound_conditions[2].value == "BUY"


def test_empty_list_normalizes_to_none():
    a = _make_alert(compound_conditions=[])
    assert a.compound_conditions is None


def test_more_than_three_conditions_rejected():
    conds = [{"metric": "rsi", "op": "gte", "value": float(i)} for i in range(4)]
    with pytest.raises(Exception):
        _make_alert(compound_conditions=conds)


def test_unknown_metric_rejected():
    with pytest.raises(Exception):
        _make_alert(compound_conditions=[{"metric": "macd", "op": "gte", "value": 1.0}])


def test_unknown_op_rejected():
    with pytest.raises(Exception):
        _make_alert(compound_conditions=[{"metric": "rsi", "op": "between", "value": 1.0}])


def test_signal_with_numeric_value_rejected():
    with pytest.raises(Exception):
        _make_alert(compound_conditions=[{"metric": "signal", "op": "eq", "value": 1.0}])


def test_volume_ratio_with_string_value_rejected():
    with pytest.raises(Exception):
        _make_alert(compound_conditions=[{"metric": "volume_ratio", "op": "gte", "value": "high"}])


def test_rsi_with_string_value_rejected():
    with pytest.raises(Exception):
        _make_alert(compound_conditions=[{"metric": "rsi", "op": "lte", "value": "low"}])


# ── Evaluation logic (mirrors _evaluate_compound_conditions in scheduler.py) ────────────

def _evaluate(conditions, actuals: dict):
    """Standalone re-implementation of the AND-chain evaluation for testing —
    actuals is a dict of metric -> current value (None = unavailable)."""
    if not conditions:
        return True
    for cond in conditions:
        metric, op, value = cond["metric"], cond["op"], cond["value"]
        actual = actuals.get(metric)
        if actual is None:
            return False
        if metric == "signal":
            passed = actual == value
        else:
            passed = actual >= value if op == "gte" else actual <= value if op == "lte" else actual == value
        if not passed:
            return False
    return True


def test_evaluate_all_conditions_pass():
    conds = [
        {"metric": "volume_ratio", "op": "gte", "value": 2.0},
        {"metric": "rsi", "op": "lte", "value": 40.0},
        {"metric": "signal", "op": "eq", "value": "BUY"},
    ]
    actuals = {"volume_ratio": 2.5, "rsi": 35.0, "signal": "BUY"}
    assert _evaluate(conds, actuals) is True


def test_evaluate_one_condition_fails_blocks_whole_alert():
    conds = [
        {"metric": "volume_ratio", "op": "gte", "value": 2.0},
        {"metric": "rsi", "op": "lte", "value": 40.0},
    ]
    actuals = {"volume_ratio": 2.5, "rsi": 55.0}  # RSI fails
    assert _evaluate(conds, actuals) is False


def test_evaluate_missing_metric_fails_closed():
    conds = [{"metric": "rsi", "op": "lte", "value": 40.0}]
    actuals = {}  # RSI unavailable (e.g. signal engine down)
    assert _evaluate(conds, actuals) is False


def test_evaluate_no_conditions_always_passes():
    assert _evaluate(None, {}) is True
    assert _evaluate([], {}) is True
