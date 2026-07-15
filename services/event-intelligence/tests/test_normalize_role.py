"""Regression test for T247-EVENTINTELLIGENCE-DEADROLELOOP.

_normalize_role()'s for-loop over _ROLE_WEIGHTS was dead code — both the loop's return and
the post-loop fallback returned the identical raw.strip()[:64], so the loop could never
produce a different outcome than skipping it entirely. This test guards the ACTUAL behavior
(strip + truncate + "Officer" default), which is unchanged by removing the dead loop.
"""
from src.services.insider import _normalize_role


def test_empty_or_none_defaults_to_officer():
    assert _normalize_role("") == "Officer"
    assert _normalize_role(None) == "Officer"


def test_role_is_stripped_and_returned_verbatim():
    assert _normalize_role("  Chief Executive Officer  ") == "Chief Executive Officer"


def test_role_matching_a_known_weight_key_is_unaffected_by_the_removed_loop():
    """The exact case the dead loop appeared to special-case (a role containing a
    _ROLE_WEIGHTS key like 'ceo') must behave identically to any other role — proving the
    loop never had a real effect."""
    assert _normalize_role("CEO") == "CEO"
    assert _normalize_role("director") == "director"


def test_role_longer_than_64_chars_is_truncated():
    long_role = "x" * 100
    result = _normalize_role(long_role)
    assert len(result) == 64
