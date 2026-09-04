"""Regression test for BUG-FUNDAMENTALS-STALEDTE: get_fundamentals()'s days_to_earnings is a
DERIVED, day-relative value ((next_earnings_date - today).days) that was computed once at fetch
time and cached alongside the rest of fundamentals for 24h (_FUND_TTL) -- a TTL that's correct
for every OTHER field on this payload (which only change quarterly) but not this one. A payload
cached the day before a report (days_to_earnings=0, correct then) was still served as-is a day
later, after the report already happened, still reading "reports today" -- the exact stale
"HPE reports Today" reminder email a user received a day after HPE's real earnings date.

Fixed via _refresh_days_to_earnings(): recomputes days_to_earnings fresh from the STABLE
next_earnings_date (an absolute date string that doesn't go stale the same way) at every point a
fundamentals payload is returned, whether served from cache or freshly fetched -- the persisted
days_to_earnings integer itself is never trusted.
"""
from datetime import date, timedelta
from unittest.mock import patch

from src.api.routes import _refresh_days_to_earnings


def _iso(delta_days: int) -> str:
    return (date.today() + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def test_recomputes_days_to_earnings_from_next_earnings_date():
    payload = {"next_earnings_date": _iso(3), "days_to_earnings": 999}  # stale stored value
    result = _refresh_days_to_earnings(payload)
    assert result["days_to_earnings"] == 3


def test_todays_earnings_date_recomputes_to_zero():
    payload = {"next_earnings_date": _iso(0), "days_to_earnings": 1}  # yesterday's stale "1"
    result = _refresh_days_to_earnings(payload)
    assert result["days_to_earnings"] == 0


def test_a_next_earnings_date_now_in_the_past_clears_both_fields():
    """The exact HPE case: next_earnings_date was yesterday (the report already happened) --
    must never emit a negative days_to_earnings or keep advertising a stale future date."""
    payload = {"next_earnings_date": _iso(-1), "days_to_earnings": 0}
    result = _refresh_days_to_earnings(payload)
    assert result["days_to_earnings"] is None
    assert result["next_earnings_date"] is None


def test_no_next_earnings_date_is_a_no_op():
    payload = {"next_earnings_date": None, "days_to_earnings": None, "other_field": "x"}
    result = _refresh_days_to_earnings(payload)
    assert result == payload


def test_missing_next_earnings_date_key_is_a_no_op():
    payload = {"other_field": "x"}
    result = _refresh_days_to_earnings(payload)
    assert result == payload


def test_malformed_next_earnings_date_fails_open_leaving_payload_unchanged():
    payload = {"next_earnings_date": "not-a-date", "days_to_earnings": 5, "other_field": "x"}
    result = _refresh_days_to_earnings(payload)
    assert result == payload


def test_wired_into_the_cache_hit_return_path():
    """get_fundamentals()'s cache-hit branch must apply the recompute, not return the raw
    cached JSON as-is -- this is the exact path the scheduler's earnings-reminder email reads
    from on every cycle, so a fix that only lives in the fresh-fetch path would never protect
    the (far more common) cache-hit case that caused the original bug."""
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py").read_text()
    start = source.index("def get_fundamentals(")
    end = source.index("\n\n\ndef ", start)
    body = source[start:end]
    assert "_refresh_days_to_earnings(json.loads(cached))" in body
    assert "_refresh_days_to_earnings(json.loads(stale))" in body
