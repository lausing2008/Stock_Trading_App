"""Source-text regression checks for AUD265-SHORT-INTEREST-AGE-NEVER-CHECKED's `is_stale`
surfacing on the two screener endpoints, GET /short-interest and GET /short_squeeze.

Both routes execute real SQL (short_interest() via SQLAlchemy text()) or read a live Redis
cache (short_squeeze()) — building a full DB/Redis fixture to exercise either end-to-end is
disproportionate to what this fix actually changed (a 2-line is_stale boolean added to an
already-existing response dict per route). Matches test_squeeze_watch_routes.py's own
established convention of source-text checks for wiring that isn't cheaply testable behaviorally
in this environment.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


def _route_body(route_path: str, func_name: str) -> str:
    marker = f'@router.get("{route_path}")\ndef {func_name}('
    start = _routes_source.index(marker)
    end = _routes_source.index("\n@router.", start + 1)
    return _routes_source[start:end]


# ── GET /short-interest — the DB-backed screener ─────────────────────────────────────────────

def test_short_interest_route_selects_the_settlement_date_column():
    body = _route_body("/short-interest", "short_interest")
    assert "f.short_interest_date" in body


def test_short_interest_route_computes_is_stale_with_a_30_day_floor():
    body = _route_body("/short-interest", "short_interest")
    assert "_stale_cutoff = _date.today() - _timedelta(days=30)" in body
    assert '"is_stale": (r.short_interest_date is None) or (r.short_interest_date < _stale_cutoff)' in body


def test_short_interest_route_never_filters_out_stale_rows_only_flags_them():
    """AUD265's own design choice: a stale reading is still the best data available — hiding it
    outright would be worse UX than honestly labeling it. The route must not WHERE-clause on
    short_interest_date at all."""
    body = _route_body("/short-interest", "short_interest")
    assert "short_interest_date IS NOT NULL" not in body
    assert "short_interest_date >" not in body


def test_short_interest_route_surfaces_the_real_date_not_a_placeholder():
    body = _route_body("/short-interest", "short_interest")
    assert '"short_interest_date": r.short_interest_date.isoformat() if r.short_interest_date is not None else None' in body


# ── GET /short_squeeze — the Redis-cache-backed screener ─────────────────────────────────────

def test_short_squeeze_route_computes_a_string_comparable_cutoff():
    """This route reads short_interest_date back out of a JSON-serialized Redis cache (a plain
    ISO string, not a real date object) — the cutoff must be built the same way (isoformat
    string) so string comparison is valid, not a date-vs-string type mismatch."""
    body = _route_body("/short_squeeze", "short_squeeze")
    assert "_stale_cutoff_str = (" in body
    assert ".isoformat()" in body


def test_short_squeeze_route_flags_missing_or_stale_dates():
    body = _route_body("/short_squeeze", "short_squeeze")
    assert '"short_interest_date": data.get("short_interest_date")' in body
    assert 'data.get("short_interest_date") is None' in body
    assert 'data.get("short_interest_date") < _stale_cutoff_str' in body


def test_short_squeeze_route_also_never_filters_out_stale_candidates():
    """Same honesty-over-hiding design choice as the DB-backed screener — this route is a
    browsable dashboard with a human in the loop, unlike the alert path (which DOES reject
    outright, see test_short_squeeze_alert.py). is_stale must be a response FIELD here, not a
    skip/continue condition the way it is in check_short_squeeze_alerts()."""
    body = _route_body("/short_squeeze", "short_squeeze")
    assert '"is_stale": (' in body
