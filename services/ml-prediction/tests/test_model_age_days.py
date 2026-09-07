"""AUD-MLAGE: _model_age_days() — the null/zero distinction that makes the age column honest."""
import sys, re, types
from datetime import datetime, timezone, timedelta

# routes.py pulls in heavy ML deps; extract the pure function by source text, matching this
# repo's own established convention for trainer.py (see test_feature_ablation.py).
src = open(__file__.rsplit("/tests/",1)[0] + "/src/api/routes.py").read()
m = re.search(r"def _model_age_days\(.*?\n(?=\n@router)", src, re.S)
assert m, "could not extract _model_age_days"
ns = {"datetime": datetime, "timezone": timezone}
exec(m.group(0), ns)
f = ns["_model_age_days"]

def test_none_returns_none():
    # Pre-Tier-21 bundles have no trained_at. Must be UNKNOWN, never 0 — coercing to 0 would
    # report the fleet's oldest models as freshly trained, exactly inverted.
    assert f(None) is None
    assert f("") is None

def test_today_returns_zero_not_none():
    # Falsy-zero discipline: a model trained today is age 0, a real value that must survive.
    assert f(datetime.now(timezone.utc).isoformat()) == 0

def test_known_age():
    assert f((datetime.now(timezone.utc) - timedelta(days=82)).isoformat()) == 82

def test_naive_timestamp_treated_as_utc():
    # Matches predict_latest()'s own handling in trainer.py — a naive string must not raise.
    naive = (datetime.now(timezone.utc) - timedelta(days=5)).replace(tzinfo=None).isoformat()
    assert f(naive) == 5

def test_malformed_returns_none_not_crash():
    # A corrupt field must degrade to "unknown", never take down the whole fleet listing.
    assert f("not-a-date") is None
    assert f(12345) is None
