import numpy as np
import pandas as pd
import pytest

from src.optimizers import ai_allocation, hierarchical_risk_parity, mean_variance, risk_parity
from src.optimizers.methods import _cap_and_redistribute


def _returns():
    rng = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "A": rng.normal(0.0005, 0.01, 500),
            "B": rng.normal(0.0003, 0.015, 500),
            "C": rng.normal(0.0007, 0.02, 500),
        }
    )


def _lowvol_highvol_returns(n=500, seed=1):
    """Two symbols with very different volatility — the exact shape that reproduced HRP's
    99.4%/0.6% concentration bug (T247-PORTFOLIOOPTIMIZER-HRP-MAXWEIGHT). Only 2 symbols, so
    the DEFAULT 0.40 cap is infeasible (0.40*2=0.80<1.0) and falls back to equal weight —
    use a custom max_weight >=0.5 to actually observe capping+redistribution with only 2
    symbols (see test_hrp_respects_a_custom_max_weight below).
    """
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "LOWVOL": rng.normal(0.0003, 0.001, n),
        "HIGHVOL": rng.normal(0.0003, 0.05, n),
    })


def _three_symbol_concentrated_returns(n=500, seed=2):
    """3 symbols, one much lower-vol than the other two — enough symbols that a 0.40 cap is
    feasible (0.40*3=1.20>=1.0), so capping/redistribution genuinely activates."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "LOWVOL": rng.normal(0.0003, 0.001, n),
        "MIDVOL": rng.normal(0.0003, 0.02, n),
        "HIGHVOL": rng.normal(0.0003, 0.05, n),
    })


def test_mvo_weights_sum_to_one():
    r = mean_variance(_returns())
    assert abs(sum(r.weights.values()) + r.cash - 1.0) < 1e-3
    assert all(0 <= w <= 0.40 + 1e-3 for w in r.weights.values())
    # AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG: a genuine (non-fallback) result
    # must NOT carry a fallback_reason — this fixture has enough symbols/history to converge.
    assert r.fallback_reason is None


def test_risk_parity_weights_sum_to_one():
    r = risk_parity(_returns())
    assert abs(sum(r.weights.values()) - 1.0) < 1e-3
    assert r.fallback_reason is None


def test_hrp_weights_sum_to_one():
    r = hierarchical_risk_parity(_returns())
    assert abs(sum(r.weights.values()) - 1.0) < 1e-3


def test_hrp_enforces_max_weight_cap_with_three_symbols():
    """T247-PORTFOLIOOPTIMIZER-HRP-MAXWEIGHT regression guard: reproduces the same
    low-vol/high-vol concentration shape with enough symbols (3) that the default 0.40 cap
    is actually feasible (0.40*3=1.20>=1.0), so capping/redistribution must activate rather
    than falling back to equal weight."""
    r = hierarchical_risk_parity(_three_symbol_concentrated_returns())
    assert all(w <= 0.40 + 1e-6 for w in r.weights.values()), r.weights
    assert abs(sum(r.weights.values()) - 1.0) < 1e-6
    # Capping/redistribution activating (not falling back to flat equal weight) is a
    # genuine result, not a fallback.
    assert r.fallback_reason is None


def test_hrp_two_symbols_with_default_cap_falls_back_to_equal_weight():
    """With only 2 symbols, the default 0.40 cap is infeasible (0.40*2=0.80<1.0) — HRP must
    fall back to equal weight (50/50) rather than silently violating the cap."""
    r = hierarchical_risk_parity(_lowvol_highvol_returns())
    assert all(abs(w - 0.5) < 1e-6 for w in r.weights.values()), r.weights
    # AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG: this IS a fallback and must say so.
    assert r.fallback_reason is not None
    assert "infeasible" in r.fallback_reason


def test_hrp_respects_a_custom_max_weight():
    r = hierarchical_risk_parity(_lowvol_highvol_returns(), max_weight=0.55)
    assert all(w <= 0.55 + 1e-6 for w in r.weights.values()), r.weights


def test_hrp_fixture_genuinely_reproduces_a_large_vol_ratio():
    """Sanity check that _lowvol_highvol_returns() still reproduces the shape that caused
    the original 99.4%/0.6% concentration bug — proves the cap tests above aren't
    accidentally vacuous (testing a cap that was never going to bind anyway)."""
    from src.optimizers.methods import _prepare
    mu, cov = _prepare(_lowvol_highvol_returns())
    vols = np.sqrt(np.diag(cov))
    assert vols[1] / vols[0] > 5, "fixture no longer reproduces a large vol ratio"


def test_cap_and_redistribute_feasible_case_never_exceeds_cap():
    """3 assets, cap 0.40: 0.40*3=1.20>=1.0 is feasible -> the cap must actually hold."""
    capped, fallback_reason = _cap_and_redistribute(np.array([0.5, 0.3, 0.2]), 0.40)
    assert all(w <= 0.40 + 1e-9 for w in capped)
    assert abs(capped.sum() - 1.0) < 1e-9
    assert fallback_reason is None


def test_cap_and_redistribute_infeasible_case_falls_back_to_equal_weight_exactly():
    """2 assets, cap 0.40: 0.40*2=0.80<1.0 is infeasible (can never sum to 1 while every
    weight stays at/under 0.40) -> must fall back to equal weight, which legitimately
    exceeds the nominal cap since satisfying it is mathematically impossible here."""
    capped, fallback_reason = _cap_and_redistribute(np.array([0.9, 0.1]), 0.40)
    assert capped == pytest.approx([0.5, 0.5], abs=1e-6)
    assert fallback_reason is not None
    assert "infeasible" in fallback_reason


def test_cap_and_redistribute_falls_back_to_equal_weight_when_infeasible():
    """max_weight * n < 1.0 makes capping alone unable to reach 100% invested — same
    infeasibility condition TA-PO1 already guards for the SLSQP-based methods."""
    capped, fallback_reason = _cap_and_redistribute(np.array([0.9, 0.1]), max_weight=0.3)
    assert capped == pytest.approx([0.5, 0.5])
    assert fallback_reason is not None


def test_cap_and_redistribute_noop_when_nothing_exceeds_cap():
    w = np.array([0.3, 0.35, 0.35])
    capped, fallback_reason = _cap_and_redistribute(w, max_weight=0.40)
    assert capped == pytest.approx(w)
    assert fallback_reason is None


def test_mean_variance_logs_on_slsqp_failure(monkeypatch):
    """T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT regression guard: a non-converged SLSQP result
    must be logged, not silently swallowed into an indistinguishable flat-1/n fallback."""
    import src.optimizers.methods as methods_mod

    class _FakeResult:
        success = False
        message = "simulated non-convergence"
        x = np.full(3, 1 / 3)

    monkeypatch.setattr(methods_mod, "minimize", lambda *a, **k: _FakeResult())
    logged = {}
    monkeypatch.setattr(methods_mod.log, "warning", lambda event, **kw: logged.update(event=event, **kw))

    out = methods_mod.mean_variance(_returns())
    assert logged.get("event") == "portfolio.slsqp_failed_fallback_to_equal_weight"
    assert logged.get("method") == "mean_variance"
    # AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG: the log line alone isn't visible to
    # an API caller — the response itself must say so too.
    assert out.fallback_reason is not None
    assert "did not converge" in out.fallback_reason


def test_risk_parity_logs_on_slsqp_failure(monkeypatch):
    import src.optimizers.methods as methods_mod

    class _FakeResult:
        success = False
        message = "simulated non-convergence"
        x = np.full(3, 1 / 3)

    monkeypatch.setattr(methods_mod, "minimize", lambda *a, **k: _FakeResult())
    logged = {}
    monkeypatch.setattr(methods_mod.log, "warning", lambda event, **kw: logged.update(event=event, **kw))

    out = methods_mod.risk_parity(_returns())
    assert logged.get("event") == "portfolio.slsqp_failed_fallback_to_equal_weight"
    assert logged.get("method") == "risk_parity"
    assert out.fallback_reason is not None
    assert "did not converge" in out.fallback_reason


def test_risk_parity_infeasibility_guard_skips_optimization_entirely(monkeypatch):
    """TA-PO1 guard applied to risk_parity(): n * max_weight < 1.0 must bypass SLSQP
    entirely (equal-weight, no log line — this is an expected/designed bypass, not a
    failure) rather than calling minimize() and hitting guaranteed non-convergence."""
    import src.optimizers.methods as methods_mod

    called = {"n": 0}
    def _should_not_be_called(*a, **k):
        called["n"] += 1
        raise AssertionError("minimize() should not be called when infeasible")
    monkeypatch.setattr(methods_mod, "minimize", _should_not_be_called)

    r = methods_mod.risk_parity(_returns(), max_weight=0.2)  # 3 * 0.2 = 0.6 < 1.0
    assert called["n"] == 0
    # PortfolioWeights rounds to 4 decimals (_pack()) — use a tolerance that accounts for that,
    # not float-equality precision.
    assert all(abs(w - 1 / 3) < 1e-3 for w in r.weights.values())
    # AUD250-PORTFOLIOOPTIMIZER-SILENT-FALLBACK-NO-FLAG: an infeasible max_weight is a
    # fallback too (not an SLSQP failure), and must be just as visible in the response.
    assert r.fallback_reason is not None
    assert "infeasible" in r.fallback_reason


# ── T247-PORTFOLIOOPTIMIZER-DEADSCOREFALLBACK ─────────────────────────────────────
#
# ai_allocation()'s raw_scores lookup used `scores.get(s, 50.0)`, a fallback that's
# unreachable in normal operation (every s in `keep` already passed a real-score check)
# but would fabricate a "neutral" 50.0 for a symbol with no real score if `keep`'s own
# filter (`scores.get(s, -1) >= min_score`) were ever bypassed by an out-of-range min_score.

def test_ai_allocation_logs_and_flags_on_slsqp_failure(monkeypatch):
    """Same T247-PORTFOLIOOPTIMIZER-SLSQP-SILENT / AUD250 fallback-flag coverage as
    mean_variance/risk_parity — ai_allocation() builds PortfolioWeights directly rather than
    via _pack(), so it needs its own dedicated regression test rather than relying on _pack()
    coverage to catch a regression here."""
    import src.optimizers.methods as methods_mod

    class _FakeResult:
        success = False
        message = "simulated non-convergence"
        x = np.full(3, 1 / 3)

    monkeypatch.setattr(methods_mod, "minimize", lambda *a, **k: _FakeResult())
    logged = {}
    monkeypatch.setattr(methods_mod.log, "warning", lambda event, **kw: logged.update(event=event, **kw))

    scores = {"A": 90.0, "B": 70.0, "C": 60.0}
    out = methods_mod.ai_allocation(_returns(), scores, min_score=50.0)
    assert logged.get("event") == "portfolio.slsqp_failed_fallback_to_equal_weight"
    assert logged.get("method") == "ai_allocation"
    assert out.fallback_reason is not None
    assert "did not converge" in out.fallback_reason


def test_ai_allocation_uses_every_keeper_symbols_real_score():
    """Every symbol that clears the min_score filter must use its OWN real score, not a
    fabricated neutral fallback — confirms scores[s] direct-index behavior end to end."""
    returns = _returns()
    scores = {"A": 90.0, "B": 70.0, "C": 60.0}
    result = ai_allocation(returns, scores, min_score=50.0)
    assert set(result.weights.keys()) == {"A", "B", "C"}
    assert result.fallback_reason is None


def test_ai_allocation_infeasibility_guard_flags_fallback():
    """TA-PO1 guard applied to ai_allocation(): n * max_weight < 1.0 must fall back to equal
    weight AND set fallback_reason (this path doesn't call minimize() at all, so it needs
    its own coverage distinct from the SLSQP-non-convergence test above)."""
    returns = _returns()
    scores = {"A": 90.0, "B": 70.0, "C": 60.0}
    result = ai_allocation(returns, scores, min_score=50.0, max_weight=0.2)  # 3*0.2=0.6<1.0
    assert result.fallback_reason is not None
    assert "infeasible" in result.fallback_reason


def test_ai_allocation_raises_if_a_kept_symbol_has_no_real_score():
    """T247 regression guard: if `keep`'s filter is ever bypassed (e.g. a future caller
    passes min_score <= -1) and a symbol with NO real score slips into `keep`, the fixed
    code (scores[s], no fallback) must raise a real KeyError instead of silently
    fabricating a 50.0 'neutral' score for it."""
    returns = _returns()
    # Simulate the bypass directly: a symbol reaches raw_scores lookup with no entry in scores.
    scores = {"A": 90.0, "B": 70.0}  # "C" deliberately missing
    with pytest.raises(KeyError):
        ai_allocation(returns, scores, min_score=-5.0)
