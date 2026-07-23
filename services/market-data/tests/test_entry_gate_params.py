"""Tests for T234-CONFIG-DECIDE-DEFAULT-MISMATCH's resolve_entry_gate_params().

Background: decision-engine's standalone GET /decide/{symbol}/explain (used by decide.tsx)
never runs _scan_for_entries()'s own config merge — it's not a real portfolio scan — so it
silently used _DEFAULT_CFG's own disconnected literal (min_confidence=62.0) instead of the
real per-style/market value a live portfolio would actually gate on (SWING=50/HK=65, LONG=40,
etc.). resolve_entry_gate_params() replicates _scan_for_entries()'s exact merge order
(_DEFAULT_CONFIG -> _STYLE_OVERRIDES[style] -> HK override if market == 'HK'), restricted to
the entry-gate-relevant subset of keys, so a new market-data endpoint can expose the REAL
values for decision-engine to consume instead of guessing.
"""
from src.services.paper_trading_engine import (
    _DEFAULT_CONFIG,
    _HK_MARKET_OVERRIDES,
    _STYLE_OVERRIDES,
    resolve_entry_gate_params,
)


class TestResolveEntryGateParamsMatchesRealScanLogic:
    """Cross-check every returned value directly against what _scan_for_entries() itself would
    compute for the same style/market — the actual regression guard this whole feature exists
    for. Hand-copying expected numbers would risk drifting from the real merge if either dict
    changes; deriving them here from the SAME source dicts _scan_for_entries() reads keeps this
    test honest about testing the real merge, not a duplicated expectation."""

    def _expected(self, style, market):
        cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}
        if market == "HK":
            for k, v in _HK_MARKET_OVERRIDES.items():
                cfg[k] = v
        return cfg

    def test_all_four_styles_us_match_the_real_scan_merge(self):
        for style in ("SHORT", "SWING", "LONG", "GROWTH"):
            expected = self._expected(style, "US")
            result = resolve_entry_gate_params(style, "US")
            for key in ("min_confidence", "min_kscore", "min_entry_score"):
                assert result[key] == expected[key], f"{style}/US {key} mismatch"

    def test_all_four_styles_hk_match_the_real_scan_merge(self):
        for style in ("SHORT", "SWING", "LONG", "GROWTH"):
            expected = self._expected(style, "HK")
            result = resolve_entry_gate_params(style, "HK")
            for key in ("min_confidence", "min_kscore", "min_entry_score"):
                assert result[key] == expected[key], f"{style}/HK {key} mismatch"

    def test_hk_min_confidence_is_uniformly_65_regardless_of_style(self):
        """The specific complaint this whole feature closes: HK's min_confidence override (65)
        must win over EVERY style's own US baseline, not just some of them."""
        for style in ("SHORT", "SWING", "LONG", "GROWTH"):
            assert resolve_entry_gate_params(style, "HK")["min_confidence"] == 65.0

    def test_swing_us_min_confidence_is_50_not_the_stale_decision_engine_literal_62(self):
        """The exact real-world case reported: decision-engine's own _DEFAULT_CFG said 62.0 —
        a value that exists NOWHERE in the real trading engine's own style/market matrix."""
        result = resolve_entry_gate_params("SWING", "US")
        assert result["min_confidence"] == 50.0
        assert result["min_confidence"] != 62.0

    def test_long_us_has_the_lowest_min_confidence_of_the_four_styles(self):
        values = {s: resolve_entry_gate_params(s, "US")["min_confidence"] for s in ("SHORT", "SWING", "LONG", "GROWTH")}
        assert values["LONG"] == min(values.values())


class TestMinTaScoreDefaultsToZeroNotAStyleValue:
    def test_growth_us_has_no_min_ta_score_override_so_it_defaults_to_0(self):
        """min_ta_score has NO _DEFAULT_CONFIG entry at all — 0.0 (gate disabled) is the correct
        default for any style/market combo that never set it, matching every other read site's
        own fallback (paper_trading_engine.py's _call_decision_engine() comment documents this
        exact convention: 'min_ta_score has NO _DEFAULT_CONFIG entry... matched exactly here,
        NOT _DEFAULT_CONFIG[\"min_ta_score\"]')."""
        assert "min_ta_score" not in _DEFAULT_CONFIG
        assert resolve_entry_gate_params("GROWTH", "US")["min_ta_score"] == 0.0

    def test_swing_us_has_a_real_min_ta_score_override(self):
        assert resolve_entry_gate_params("SWING", "US")["min_ta_score"] == 0.65

    def test_hk_min_ta_score_override_applies_even_to_styles_without_their_own(self):
        assert resolve_entry_gate_params("GROWTH", "HK")["min_ta_score"] == 0.65


class TestMinRrRatioIsCalibrationAware:
    def test_min_rr_ratio_matches_default_min_rr_ratio_neutral(self):
        """min_rr_ratio must be resolved via _default_min_rr_ratio(), not a frozen 2.0 literal —
        so this endpoint stays correct even after a future calibration run changes the real
        default, matching how _call_decision_engine() itself resolves the SAME key."""
        from src.services.paper_trading_engine import _default_min_rr_ratio
        expected = _default_min_rr_ratio("neutral")
        for style in ("SHORT", "SWING", "LONG", "GROWTH"):
            assert resolve_entry_gate_params(style, "US")["min_rr_ratio"] == expected


class TestUnknownStyleAndMarketDegradeGracefully:
    def test_unknown_style_falls_back_to_bare_defaults(self):
        """An unrecognized style must not KeyError — _STYLE_OVERRIDES.get(style, {}) degrades
        to the plain _DEFAULT_CONFIG values with no style-specific override applied."""
        result = resolve_entry_gate_params("NOT_A_REAL_STYLE", "US")
        assert result["min_confidence"] == _DEFAULT_CONFIG["min_confidence"]

    def test_lowercase_style_and_market_are_normalized(self):
        assert resolve_entry_gate_params("swing", "hk") == resolve_entry_gate_params("SWING", "HK")

    def test_missing_style_defaults_to_swing(self):
        assert resolve_entry_gate_params("", "US") == resolve_entry_gate_params("SWING", "US")


class TestEntryGateParamsRoute:
    """The actual GET /stocks/entry-gate-params route — a thin wrapper confirmed to delegate
    to resolve_entry_gate_params() rather than reimplementing or diverging from it."""

    def test_route_delegates_to_the_real_resolver(self):
        from src.api.routes import entry_gate_params
        route_result = entry_gate_params(style="SWING", market="HK")
        direct_result = resolve_entry_gate_params("SWING", "HK")
        assert route_result == direct_result

    def test_route_defaults_to_swing_us_when_no_params_given(self):
        from src.api.routes import entry_gate_params
        assert entry_gate_params() == resolve_entry_gate_params("SWING", "US")
