"""R09 (2026-09-24 follow-up audit): the DA-02 flag gated only the arithmetic.

THE DEFECT. DA-02 stopped the meta probability from moving the number, and that part was
correct. But the prediction still RAN on every call, and a non-null result still added `meta`
to `model_probabilities` and `_meta` to the model name. So a member contributing exactly
nothing was reported as a contributor: the response said `ensemble_xgb_lgb_rf_meta` and carried
meta's probability while the blend was off. The audit's probe: base probabilities 0.7/0.6/0.5
with meta 0.9 and the flag false returns 0.59975 — correctly excluding meta — and then names
meta anyway.

Two costs, and the second is the one that matters. Anything reading provenance (signal reasons,
the admin panel, a later audit asking which models produced a call) was told something false.
And the sector/market-cap lookup plus inference behind the call was spent producing a value
that was immediately discarded.

THE FIX. The flag is resolved ONCE, at the top, into `_meta_blend_on`, and that single value
drives all three decisions: whether meta runs, whether it blends, and whether it is named. A
second read at the blend site could disagree with the first if the flag flipped mid-request.

SHADOW MODE is separate and off by default, because "compute it but do not use it" is a real
thing to want while validating a replacement — but it must be labelled. Its output goes to
`meta_shadow_probability` with an explicit applied weight of 0.0, never into
`model_probabilities` and never into the model name.

`applied_weights` is new: the weights that actually produced `bullish_probability`, so a reader
does not infer provenance from a model name and a constant buried in the function.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_predict_latest_ensemble_three_falsy_zero import (  # noqa: E402
    _TRAINER_SOURCE, _make_ensemble_three,
)

_STUB_PKG = "_r09_extracted_trainer_pkg"
META_PROB = 0.9


def _result(prob, auc=0.60):
    return {"bullish_probability": prob, "confidence": 50.0,
            "metrics": {"auc": auc, "cv_auc_mean": auc, "buy_threshold": 0.5},
            "oos_suppressed": False}


def _call(*, blend=False, shadow=False, meta_prob=META_PROB, probs=(0.7, 0.6, 0.5)):
    """Run the REAL extracted function with the meta flags under test control.

    The call inside is `from .meta_trainer import predict_meta` — a RELATIVE import, which the
    extracted namespace cannot resolve on its own (it raises "'__name__' not in globals", the
    broad except swallows it, and meta is always None). Left that way every test here would
    pass for entirely the wrong reason, so the namespace is given a package context and a stub
    is registered under the name that relative import actually resolves to. `predict_meta` is
    then genuinely imported and genuinely called, which is what makes "was it called?" a real
    assertion rather than a tautology.

    That this import has been silently failing is not hypothetical: T237-ML-META3 is exactly
    this bug, live in production, unnoticed for a long time.
    """
    fn = _make_ensemble_three(*(_result(p) for p in probs))
    ns = fn.__globals__
    ns["_meta_blend_enabled"] = lambda: blend
    ns["_meta_shadow_enabled"] = lambda: shadow

    calls = {"n": 0}

    def _predict_meta(**kw):
        calls["n"] += 1
        return meta_prob

    import types
    pkg = types.ModuleType(_STUB_PKG)
    pkg.__path__ = []
    sub = types.ModuleType(f"{_STUB_PKG}.meta_trainer")
    sub.predict_meta = _predict_meta
    sys.modules[_STUB_PKG] = pkg
    sys.modules[f"{_STUB_PKG}.meta_trainer"] = sub
    ns["__name__"] = f"{_STUB_PKG}.trainer"
    ns["__package__"] = _STUB_PKG
    try:
        return fn("AAPL", horizon=5, style="SWING"), calls
    finally:
        sys.modules.pop(f"{_STUB_PKG}.meta_trainer", None)
        sys.modules.pop(_STUB_PKG, None)


# ── The finding: a disabled member reported as a contributor ─────────────────

def test_a_disabled_meta_is_not_named_in_the_model(_=None):
    """THE CORE R09 CASE. The audit's own probe shape."""
    out, _calls = _call(blend=False)
    assert out["model"] == "ensemble_xgb_lgb_rf", out["model"]
    assert "_meta" not in out["model"]


def test_a_disabled_meta_is_not_listed_in_model_probabilities():
    out, _calls = _call(blend=False)
    assert "meta" not in out["model_probabilities"]
    assert set(out["model_probabilities"]) == {"xgboost", "lightgbm", "random_forest"}


def test_a_disabled_meta_is_not_computed_at_all():
    """The other half of the finding: the feature/DB work behind the call was being spent on a
    value that was then thrown away."""
    _out, calls = _call(blend=False)
    assert calls["n"] == 0, "meta inference ran even though its result could not be used"


def test_the_probability_is_unchanged_by_the_reporting_fix():
    """R09 is a provenance and latency fix. It must not move a single number — DA-02 already
    settled what the probability should be, and changing it here would conflate two decisions."""
    off, _ = _call(blend=False)
    on, _ = _call(blend=True)
    assert off["bullish_probability"] != on["bullish_probability"]
    # The disabled path is the plain 3-model result, exactly as DA-02 left it.
    assert round(off["bullish_probability"], 5) == 0.59975


# ── Enabled: reported as a real contributor ──────────────────────────────────

def test_an_enabled_meta_is_named_and_listed():
    out, calls = _call(blend=True)
    assert "_meta" in out["model"]
    assert out["model_probabilities"]["meta"] == round(META_PROB, 4)
    assert calls["n"] == 1


def test_applied_weights_report_what_actually_produced_the_probability():
    """The audit: "report actual contributors and applied weights"."""
    on, _ = _call(blend=True)
    assert on["applied_weights"]["meta"] == 0.15
    assert abs(sum(on["applied_weights"].values()) - 1.0) < 1e-6
    assert set(on["applied_weights"]) == {"xgboost", "lightgbm", "random_forest", "meta"}

    off, _ = _call(blend=False)
    assert "meta" not in off["applied_weights"]
    assert abs(sum(off["applied_weights"].values()) - 1.0) < 1e-6


# ── Shadow mode: labelled, never a contributor ───────────────────────────────

def test_shadow_mode_computes_meta_but_reports_it_separately():
    out, calls = _call(blend=False, shadow=True)
    assert calls["n"] == 1, "shadow mode must actually run the model"
    assert out["meta_shadow_probability"] == round(META_PROB, 4)
    assert out["meta_shadow_applied_weight"] == 0.0


def test_shadow_mode_does_not_change_the_probability_or_the_contributors():
    plain, _ = _call(blend=False, shadow=False)
    shadow, _ = _call(blend=False, shadow=True)
    assert shadow["bullish_probability"] == plain["bullish_probability"]
    assert shadow["model"] == plain["model"]
    assert "meta" not in shadow["model_probabilities"]
    assert "meta" not in shadow["applied_weights"]


def test_the_shadow_fields_are_absent_when_shadow_mode_is_off():
    """An always-present field reading 0.0 would look like a member with zero weight rather
    than a mode that is not running."""
    out, _ = _call(blend=False, shadow=False)
    assert "meta_shadow_probability" not in out
    assert "meta_shadow_applied_weight" not in out


def test_blend_takes_precedence_over_shadow():
    """Both on is a misconfiguration, not a mode. Meta is a real contributor then, and reporting
    it as a shadow at the same time would be two contradictory provenance claims."""
    out, calls = _call(blend=True, shadow=True)
    assert calls["n"] == 1, "meta must not be run twice"
    assert "meta" in out["model_probabilities"]
    assert "meta_shadow_probability" not in out


# ── Structure ────────────────────────────────────────────────────────────────

def test_both_flags_fail_closed():
    """A Redis outage must leave an unvalidated contract off, not fall back TO it."""
    import ast
    tree = ast.parse(_TRAINER_SOURCE)
    for name in ("_meta_blend_enabled", "_meta_shadow_enabled"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        src = ast.get_source_segment(_TRAINER_SOURCE, fn)
        handlers = [n for n in ast.walk(fn) if isinstance(n, ast.ExceptHandler)]
        assert handlers, f"{name} has no except path"
        assert "return False" in src, f"{name}'s except path must disable"
        assert '== "1"' in src, f"{name} must require an explicit positive flag"


def test_the_flags_are_each_resolved_exactly_once_per_call():
    """Two reads can disagree if the flag flips mid-request, and the response would then name a
    contributor that never ran — or hide one that did."""
    import ast
    tree = ast.parse(_TRAINER_SOURCE)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
              and n.name == "predict_latest_ensemble_three")
    for name in ("_meta_blend_enabled", "_meta_shadow_enabled"):
        n_calls = sum(1 for node in ast.walk(fn)
                      if isinstance(node, ast.Call)
                      and isinstance(node.func, ast.Name) and node.func.id == name)
        assert n_calls == 1, f"{name} is called {n_calls} times inside the prediction"


def test_the_skip_does_not_log_a_failure():
    """The block meta sits in ends in a broad `except Exception` that logs a WARNING. Routing
    the deliberate, default-state skip through it would fill the log with
    "meta_predict_failed" and bury a real one — which is precisely how the T237-ML-META3 bug
    (a ModuleNotFoundError on every single call) stayed hidden."""
    assert "class _MetaDisabled(Exception):" in _TRAINER_SOURCE
    assert "raise _MetaDisabled" in _TRAINER_SOURCE
    i_specific = _TRAINER_SOURCE.index("    except _MetaDisabled:")
    i_broad = _TRAINER_SOURCE.index("    except Exception as exc:", i_specific - 4000)
    assert i_specific < i_broad, "the sentinel must be caught before the broad handler"
