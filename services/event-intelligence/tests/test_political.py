"""Regression test for T247-EVENTINTELLIGENCE-AWARDAMOUNT-TYPE.

USASpending's "Award Amount" field is documented to sometimes come back as a string (e.g.
"2500000.00") depending on the endpoint/version. The original code compared this raw value
against an int directly (`amount < 1_000_000`), raising TypeError for a string amount —
caught by the outer per-TICKER except, silently aborting sync for every other (valid) award
for that ticker in the same response, not just the malformed one.

_parse_award_amount() is the fix: explicit float coercion with a safe fallback.
"""
import pytest

from src.services.political import _parse_award_amount


def test_parses_a_normal_numeric_amount():
    assert _parse_award_amount(2_500_000) == 2_500_000.0


def test_parses_a_string_amount():
    """The exact bug scenario: USASpending returns Award Amount as a string."""
    assert _parse_award_amount("2500000.00") == 2_500_000.0


def test_none_amount_returns_zero():
    assert _parse_award_amount(None) == 0.0


def test_missing_key_returns_zero():
    """award.get("Award Amount") on a dict with no such key returns None."""
    assert _parse_award_amount(None) == 0.0


def test_zero_amount_returns_zero():
    assert _parse_award_amount(0) == 0.0


def test_unparseable_string_returns_zero_not_raises():
    """A genuinely garbage value (not just a numeric string) must not raise — it should be
    treated as 0.0 and excluded by the caller's $1M floor, not abort the whole ticker's loop."""
    assert _parse_award_amount("not-a-number") == 0.0


def test_unparseable_type_returns_zero_not_raises():
    assert _parse_award_amount(["not", "a", "number"]) == 0.0


@pytest.mark.parametrize("raw,expected", [
    (1_500_000, 1_500_000.0),
    ("1500000", 1_500_000.0),
    ("1500000.50", 1_500_000.50),
    (1_500_000.50, 1_500_000.50),
])
def test_various_valid_formats(raw, expected):
    assert _parse_award_amount(raw) == expected
