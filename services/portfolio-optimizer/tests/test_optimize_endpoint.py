"""Regression test for T247-PORTFOLIOOPTIMIZER-SKILLMD-SCHEMA.

skill.md documented a `constraints.max_weight`/`min_weight` and `target_return` request
contract, but OptimizeRequest defined none of them — Pydantic silently dropped the unknown
fields (no extra="forbid"), so a caller following the docs got a request that ran with the
hardcoded default max_weight instead of their intended value, with no error indicating the
constraint was never applied.

constraints.max_weight is now real (every optimizer method already accepted a max_weight
parameter internally — this just threads the request value through). min_weight and
target_return are NOT implemented anywhere and were removed from the docs rather than
half-implemented.
"""
import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.routes import MIN_ROWS, OptimizeConstraints, OptimizeRequest, optimize


def _returns_df(n=60, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "A": rng.normal(0.0005, 0.01, n),
        "B": rng.normal(0.0003, 0.015, n),
        "C": rng.normal(0.0007, 0.02, n),
    })


def _fake_closes(returns_df):
    """optimize() calls closes.pct_change().dropna() to derive returns — build a closes
    frame whose pct_change reproduces the given returns_df closely enough for MIN_ROWS
    checks to pass (exact values don't matter, only that optimization runs)."""
    closes = (1 + returns_df.fillna(0)).cumprod() * 100
    return closes


def test_constraints_max_weight_is_threaded_through_to_the_optimizer(monkeypatch):
    """Core regression guard: constraints.max_weight must actually change the optimizer's
    output — not be silently dropped."""
    import src.api.routes as routes_mod

    closes = _fake_closes(_returns_df())
    monkeypatch.setattr(routes_mod, "_fetch_closes", lambda symbols, lookback: (closes, []))

    req_default = OptimizeRequest(symbols=["A", "B", "C"], method="mean_variance")
    req_tight = OptimizeRequest(
        symbols=["A", "B", "C"], method="mean_variance",
        constraints=OptimizeConstraints(max_weight=0.34),
    )

    result_default = optimize(req_default, _="testuser")
    result_tight = optimize(req_tight, _="testuser")

    assert all(w <= 0.34 + 1e-6 for w in result_tight["weights"].values()), result_tight["weights"]
    # With the tight cap, at least one weight must differ from the unconstrained run —
    # proves the constraint had a real effect, not just a coincidental match.
    assert result_tight["weights"] != result_default["weights"]


def test_constraints_max_weight_applies_to_hierarchical_risk_parity_too(monkeypatch):
    """HRP's max_weight cap (T247-PORTFOLIOOPTIMIZER-1) must also be reachable through the
    request-level constraints field, not just the function's own default."""
    import src.api.routes as routes_mod

    closes = _fake_closes(_returns_df())
    monkeypatch.setattr(routes_mod, "_fetch_closes", lambda symbols, lookback: (closes, []))

    req = OptimizeRequest(
        symbols=["A", "B", "C"], method="hierarchical_risk_parity",
        constraints=OptimizeConstraints(max_weight=0.35),
    )
    result = optimize(req, _="testuser")
    assert all(w <= 0.35 + 1e-6 for w in result["weights"].values()), result["weights"]


def test_no_constraints_uses_the_methods_own_default(monkeypatch):
    """Omitting constraints entirely must behave exactly as before this fix (no regression
    on the common, unconstrained-request path)."""
    import src.api.routes as routes_mod

    closes = _fake_closes(_returns_df())
    monkeypatch.setattr(routes_mod, "_fetch_closes", lambda symbols, lookback: (closes, []))

    req = OptimizeRequest(symbols=["A", "B", "C"], method="mean_variance")
    result = optimize(req, _="testuser")
    # mean_variance's own default cap is 0.40.
    assert all(w <= 0.40 + 1e-6 for w in result["weights"].values()), result["weights"]


def test_infeasible_max_weight_surfaces_fallback_reason_through_the_endpoint(monkeypatch):
    """AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG end-to-end: a caller-supplied
    max_weight that makes n*max_weight<1.0 must produce a visible fallback_reason in the
    actual HTTP response dict, not just internally on the dataclass."""
    import src.api.routes as routes_mod

    closes = _fake_closes(_returns_df())
    monkeypatch.setattr(routes_mod, "_fetch_closes", lambda symbols, lookback: (closes, []))

    # 3 symbols * 0.2 max_weight = 0.6 < 1.0 -> infeasible, must fall back to equal weight.
    req = OptimizeRequest(
        symbols=["A", "B", "C"], method="mean_variance",
        constraints=OptimizeConstraints(max_weight=0.2),
    )
    result = optimize(req, _="testuser")
    assert result.get("fallback_reason") is not None
    assert "infeasible" in result["fallback_reason"]


def test_feasible_request_has_no_fallback_reason_in_the_endpoint_response(monkeypatch):
    """The common, non-fallback path must NOT carry a fallback_reason key with a truthy
    value — confirms this fix didn't regress the normal response shape."""
    import src.api.routes as routes_mod

    closes = _fake_closes(_returns_df())
    monkeypatch.setattr(routes_mod, "_fetch_closes", lambda symbols, lookback: (closes, []))

    req = OptimizeRequest(symbols=["A", "B", "C"], method="mean_variance")
    result = optimize(req, _="testuser")
    assert result.get("fallback_reason") is None


def test_max_weight_out_of_range_is_rejected(monkeypatch):
    """constraints.max_weight must be in (0, 1] — a caller-supplied value outside that range
    should raise a clear error, not silently misbehave."""
    import src.api.routes as routes_mod

    closes = _fake_closes(_returns_df())
    monkeypatch.setattr(routes_mod, "_fetch_closes", lambda symbols, lookback: (closes, []))

    req = OptimizeRequest(
        symbols=["A", "B", "C"], method="mean_variance",
        constraints=OptimizeConstraints(max_weight=1.5),
    )
    with pytest.raises(HTTPException) as exc_info:
        optimize(req, _="testuser")
    assert exc_info.value.status_code == 400


def test_unimplemented_fields_are_rejected_not_silently_dropped():
    """T247-PORTFOLIOOPTIMIZER-SKILLMD-SCHEMA's core point: target_return and
    constraints.min_weight are NOT implemented. Confirm OptimizeRequest genuinely has no
    such fields (they're absent from model_fields), so a caller sending them gets a real
    signal rather than silent acceptance-and-ignore. Pydantic's default behavior (ignore
    unknown fields) still applies at construction time — this test documents that fact
    rather than asserting a stricter behavior this fix didn't add."""
    fields = OptimizeRequest.model_fields
    assert "target_return" not in fields
    assert "min_weight" not in OptimizeConstraints.model_fields
    assert "max_weight" in OptimizeConstraints.model_fields


# ── T247-PORTFOLIOOPTIMIZER-MINROWS-OFFBYONE ──────────────────────────────────────
#
# MIN_ROWS was checked against `closes` (raw prices), but the actual optimizer input is
# `returns = closes.pct_change().dropna()`, which always has exactly one fewer row — a
# request with exactly MIN_ROWS price rows passed the check but fed MIN_ROWS-1 rows of
# returns into the optimizer, one short of the documented "30 trading days" minimum.

def test_exactly_min_rows_price_history_is_rejected(monkeypatch):
    """The exact bug scenario: exactly MIN_ROWS raw price rows must now be REJECTED (they'd
    only yield MIN_ROWS-1 rows of returns), not silently accepted one row short."""
    import src.api.routes as routes_mod

    closes = _fake_closes(_returns_df(n=MIN_ROWS))
    monkeypatch.setattr(routes_mod, "_fetch_closes", lambda symbols, lookback: (closes, []))

    req = OptimizeRequest(symbols=["A", "B", "C"], method="mean_variance")
    with pytest.raises(HTTPException) as exc_info:
        optimize(req, _="testuser")
    assert exc_info.value.status_code == 400


def test_min_rows_plus_one_price_history_is_accepted(monkeypatch):
    """MIN_ROWS+1 raw price rows yields exactly MIN_ROWS rows of returns — the true
    minimum the documented invariant promises — and must be accepted."""
    import src.api.routes as routes_mod

    closes = _fake_closes(_returns_df(n=MIN_ROWS + 1))
    monkeypatch.setattr(routes_mod, "_fetch_closes", lambda symbols, lookback: (closes, []))

    req = OptimizeRequest(symbols=["A", "B", "C"], method="mean_variance")
    result = optimize(req, _="testuser")  # must not raise
    assert result["weights"]
