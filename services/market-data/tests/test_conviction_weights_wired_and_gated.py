"""Tests for AUD263-CONVICTION-WEIGHTS-UNGATED (Deep Audit #3, Tier 263).

Two halves fixed together:

1. calibrate_conviction_weights() (signal-engine) previously fit on the full sample and wrote
   conviction_weights.json/Redis UNCONDITIONALLY — no chronological split, no baseline
   comparison, no TuneHistory record (confirmed empty in production: 0 rows for
   parameter_class LIKE '%conviction%'). Now uses the same chronological 70/30 split +
   validation-beats-baseline + TuneHistory pattern as calibrate_ta_weights.

2. Its output (edge_pct) had NO consumer anywhere in the codebase — load_conviction_weights()
   existed with zero callers. _is_conviction_buy() (this file's tests) now reads it and uses
   it to ADDITIVELY extend the gate's soft-fail allowance to layers whose underlying flag has
   near-zero/negative calibrated edge — never removing an existing hardcoded soft layer.

_is_conviction_buy() is loaded via exec() from source, matching
test_conviction_buy_overextension_guards.py's established technique. Because it now calls
_load_conviction_edges() (which reaches Redis), that helper is injected as a namespace stub —
the test controls exactly what "calibrated edge data" is available, without touching Redis.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_source = _scheduler_path.read_text()


def _load_function(name: str, namespace: dict | None = None):
    start = _source.index(f"def {name}")
    end = _source.index("\n\n\n", start)
    namespace = namespace if namespace is not None else {}
    exec(_source[start:end], namespace)  # noqa: S102
    return namespace[name]


def _load_constant(name: str):
    start = _source.index(f"{name}: dict")
    end = _source.index("\n}\n", start) + len("\n}")
    namespace: dict = {}
    exec(_source[start:end], namespace)  # noqa: S102
    return namespace[name]


def _load_dict_literal(name: str):
    """For _CONVICTION_LAYER_FLAG, a plain (not `: dict`-annotated) module-level dict."""
    start = _source.index(f"{name} = {{")
    end = _source.index("\n}\n", start) + len("\n}")
    namespace: dict = {}
    exec(_source[start:end], namespace)  # noqa: S102
    return namespace[name]


def _make_namespace(conviction_edges: dict[str, float] | None = None) -> dict:
    edges = conviction_edges if conviction_edges is not None else {}
    ns = {
        "_REGIME_THRESHOLDS": _load_constant("_REGIME_THRESHOLDS"),
        "_CONVICTION_LAYER_FLAG": _load_dict_literal("_CONVICTION_LAYER_FLAG"),
        "_CONVICTION_EDGE_NOISE_THRESHOLD_PCT": 2.0,
        "_load_conviction_edges": lambda: edges,
    }
    return ns


def _clean_reasons(**overrides) -> dict:
    base = {
        "market_regime": "bull",
        "sma50_above_sma200": True,
        "trend_above_sma50": True,
        "rsi": 55.0,
        "macd_hist": 0.5,
        "macd_rising": True,
        "macd_zero_cross_up": False,
        "obv_trend_bullish": True,
        "adx_trending": True,
        "adx": 30.0,
        "ml_probability": 0.90,
        "ml_weight": 0.5,
        "rsi_divergence": None,
        "stoch_rsi_overbought": False,
        "stoch_rsi_still_hot": False,
        "near_recent_high_hot": False,
        "pct_from_20d_high": 0.10,
    }
    base.update(overrides)
    return base


def _signal(horizon="SWING", **reason_overrides) -> dict:
    return {"horizon": horizon, "reasons": _clean_reasons(**reason_overrides)}


# ── Consumer wiring: load_conviction_weights() now has a real caller ─────────────────────

def test_load_conviction_edges_is_called_inside_is_conviction_buy():
    """The whole point of the fix: _is_conviction_buy must actually call the loader, not just
    have it sit unused elsewhere in the file."""
    start = _source.index("def _is_conviction_buy(")
    end = _source.index("\n\n\ndef ", start)
    body = _source[start:end]
    assert "_load_conviction_edges()" in body


def test_no_calibration_data_behaves_exactly_like_before_the_fix():
    """Empty edge map (never calibrated, or Redis unreachable) must fail open to the ORIGINAL
    hardcoded soft-layer set — this fix must never make anything stricter."""
    ns = _make_namespace(conviction_edges={})
    is_conviction_buy = _load_function("_is_conviction_buy", ns)
    # ADX fails (hard, in the original hardcoded soft set) + Uptrend fails (not in the
    # hardcoded set) -> 2 failures, only 1 of which is soft -> "failed" tier without the fix
    # extending anything, since Uptrend has no calibration data to draw on.
    sig = _signal(adx_trending=False, adx=15.0, trend_above_sma50=False, sma50_above_sma200=False)
    all_passed, tier, passed, failed = is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert tier == "failed"
    assert all_passed is False


def test_low_edge_uptrend_flag_becomes_soft_failable():
    """The core new behavior: Uptrend is NOT in the hardcoded soft set, but if the calibrated
    data shows trend_above_sma50 has near-zero edge (not actually predictive), a single
    Uptrend failure alongside an already-passing everything-else should now land in the
    'near' tier instead of 'failed'."""
    ns = _make_namespace(conviction_edges={"trend_above_sma50": 0.5})  # below the 2.0 noise floor
    is_conviction_buy = _load_function("_is_conviction_buy", ns)
    sig = _signal(trend_above_sma50=False, sma50_above_sma200=False)  # only Uptrend fails
    all_passed, tier, passed, failed = is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert tier == "near"
    assert all_passed is True


def test_high_edge_uptrend_flag_does_not_become_soft_failable():
    """A flag with a REAL, strong calibrated edge must NOT be added to the soft set — only
    near-zero/negative edges (genuinely uninformative) should be treated as soft-failable."""
    ns = _make_namespace(conviction_edges={"trend_above_sma50": 25.0})  # well above the noise floor
    is_conviction_buy = _load_function("_is_conviction_buy", ns)
    sig = _signal(trend_above_sma50=False, sma50_above_sma200=False)
    all_passed, tier, passed, failed = is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert tier == "failed"
    assert all_passed is False


def test_negative_edge_flag_also_becomes_soft_failable():
    """A NEGATIVE edge (the flag is more common in LOSERS than winners) is even more clearly
    "not a reliable positive signal" than a near-zero edge — must also qualify as soft."""
    ns = _make_namespace(conviction_edges={"trend_above_sma50": -10.0})
    is_conviction_buy = _load_function("_is_conviction_buy", ns)
    sig = _signal(trend_above_sma50=False, sma50_above_sma200=False)
    all_passed, tier, passed, failed = is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert tier == "near"
    assert all_passed is True


def test_extension_is_additive_never_removes_an_existing_hardcoded_soft_layer():
    """OBV/ADX/ML/MACD must remain soft-failable regardless of calibration data — the fix
    only ADDS layers to the soft set, never removes the pre-existing ones."""
    ns = _make_namespace(conviction_edges={"obv_trend_bullish": 99.0})  # a strong edge, irrelevant here
    is_conviction_buy = _load_function("_is_conviction_buy", ns)
    sig = _signal(obv_trend_bullish=False)  # OBV fails; it's hardcoded soft regardless of edge data
    all_passed, tier, passed, failed = is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert tier == "near"
    assert all_passed is True


def test_two_soft_failures_still_fail_the_gate_near_tier_only_allows_exactly_one():
    """The near-conviction tier's own 'exactly 1 soft failure' rule must be untouched by this
    fix — even with both Uptrend and OBV made soft-failable by calibration data, TWO
    simultaneous soft failures must still land in 'failed', not 'near'."""
    ns = _make_namespace(conviction_edges={"trend_above_sma50": 0.5})
    is_conviction_buy = _load_function("_is_conviction_buy", ns)
    sig = _signal(trend_above_sma50=False, sma50_above_sma200=False, obv_trend_bullish=False)
    all_passed, tier, passed, failed = is_conviction_buy(sig, kscore=70.0, regime="bull")
    assert tier == "failed"
    assert all_passed is False
