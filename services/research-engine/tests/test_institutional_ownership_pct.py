"""Regression test for T247-RESEARCHENGINE-INSTOWNERSHIP-SCALE.

yfinance's held_percent_institutions is a decimal fraction (0.62 for 62%). Every other
_pct-suffixed field sent to the LLM prompt (fcf_margin_pct, roe_pct, short_float_pct) is
pre-scaled to a real percent, but this field was embedded unscaled directly into the exact
JSON structure Claude is instructed to fill in — a concrete numeric literal (0.62) Claude was
very likely to echo back unchanged, producing a report claiming 0.62% institutional ownership
for a stock that is actually 62% institutionally owned.
"""
from src.api.routes import _institutional_ownership_pct


def test_typical_fraction_is_scaled_to_percent():
    """The exact bug scenario: 0.62 (62% ownership) must become 62.0, not stay 0.62."""
    assert _institutional_ownership_pct({"held_percent_institutions": 0.62}) == 62.0


def test_full_ownership_scales_correctly():
    assert _institutional_ownership_pct({"held_percent_institutions": 1.0}) == 100.0


def test_zero_ownership_stays_zero():
    assert _institutional_ownership_pct({"held_percent_institutions": 0.0}) == 0.0


def test_missing_field_defaults_to_zero():
    assert _institutional_ownership_pct({}) == 0.0


def test_none_value_defaults_to_zero():
    assert _institutional_ownership_pct({"held_percent_institutions": None}) == 0.0


def test_rounds_to_one_decimal():
    assert _institutional_ownership_pct({"held_percent_institutions": 0.6234}) == 62.3
