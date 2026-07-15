"""Tests for valuation.py — CAPE (Shiller PE) sync for the AI-bubble-warning indicator.

Data source is multpl.com (Atom feed for the current value, HTML by-month table for
history), NOT Yale's own ie_data.xls — that file was found stale (Last-Modified Oct 2023)
and Shiller's site was mid-migration with no working direct download at investigation time.
multpl.com's Atom feed pattern is confirmed identical across multiple indicator pages (not a
one-off scrape), verified live before choosing it.

Fixtures under tests/fixtures/ are real (trimmed) responses captured directly from
multpl.com on 2026-07-14, not hand-authored — so the parser is tested against the actual
response shape, not an idealized one.
"""
import pathlib
from datetime import date

from src.services.valuation import cape_band, _parse_atom, _parse_table

_FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _atom_fixture() -> bytes:
    return (_FIXTURES / "cape_atom_sample.xml").read_bytes()


def _table_fixture() -> bytes:
    return (_FIXTURES / "cape_table_sample.html").read_bytes()


# ── cape_band() — threshold classification ────────────────────────────────────

def test_normal_band_below_30():
    assert cape_band(15.0) == "normal"
    assert cape_band(29.99) == "normal"


def test_elevated_band_30_to_35():
    assert cape_band(30.0) == "elevated"
    assert cape_band(34.99) == "elevated"


def test_high_band_35_to_40():
    assert cape_band(35.0) == "high"
    assert cape_band(39.99) == "high"


def test_extreme_band_at_and_above_40():
    assert cape_band(40.0) == "extreme"
    assert cape_band(44.19) == "extreme"  # Dec 1999 dot-com all-time-high peak


# ── _parse_atom() — real multpl.com Atom feed response ────────────────────────

def test_parse_atom_extracts_the_real_current_value_and_date():
    """The exact real response captured from multpl.com/shiller-pe/atom on 2026-07-14."""
    reading_date, cape_value = _parse_atom(_atom_fixture())
    assert reading_date == date(2026, 7, 14)
    assert cape_value == 42.00


def test_parse_atom_raises_on_missing_value():
    malformed = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
        <entry>
            <updated>2026-07-14T16:00:00-04:00</updated>
            <content type="html">no value here</content>
        </entry>
    </feed>"""
    import pytest
    with pytest.raises(ValueError):
        _parse_atom(malformed)


# ── _parse_table() — real multpl.com by-month table response ──────────────────

def test_parse_table_extracts_real_rows_newest_first():
    """The exact real (trimmed) by-month table response — 10 rows, Jul 2026 down to Oct 2025."""
    rows = _parse_table(_table_fixture(), months=24)
    assert len(rows) == 10
    assert rows[0] == (date(2026, 7, 14), 42.00)
    assert rows[1] == (date(2026, 6, 1), 41.32)
    assert rows[-1] == (date(2025, 10, 1), 39.31)


def test_parse_table_respects_months_cap():
    rows = _parse_table(_table_fixture(), months=3)
    assert len(rows) == 3
    assert rows[0] == (date(2026, 7, 14), 42.00)


def test_parse_table_raises_when_table_missing():
    import pytest
    with pytest.raises(ValueError):
        _parse_table(b"<html><body>no table here</body></html>", months=24)
