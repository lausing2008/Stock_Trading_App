"""Tests for BUG-REASONSJSON-NAN: json.dumps() happily serializes float('nan')/float('inf')
into the bare, non-standard tokens NaN/Infinity/-Infinity — Postgres's `CAST(:x AS jsonb)` is
strict and rejects them with a real psycopg2.errors.InvalidTextRepresentation, aborting the
whole INSERT ... ON CONFLICT upsert for that signal row.

Live-verified against real production data before writing this fix (see the CLAUDE.md
write-up): 6951.HK's macd_hist is genuinely NaN (a thin-history stock below the 26-bar
slow-EMA warmup window, per signals.py's own T233-SIG-RSI1 comment) on all 4 horizons, and
this was confirmed to be the exact root cause of 429 real failures/24h in production logs
(both in _bulk_persist()'s scheduled upsert and signal_for()'s manual-refresh upsert).

signals_shared.py imports directly under this test environment's pytest/conftest.py setup —
_json_safe() is a pure function with no DB/network dependency, tested directly with real
inputs. routes.py's own call-site wiring is checked via source-text regression (matching
this repo's established pattern for functions in routes.py that need common.jwt_auth/DB
session coupling this test environment doesn't stub).
"""
import json
import math
import pathlib

from src.api.signals_shared import _json_safe

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


# ── _json_safe() — pure function, direct behavioral tests ───────────────────────────────────

def test_nan_float_is_replaced_with_none():
    assert _json_safe(float("nan")) is None


def test_positive_and_negative_infinity_are_replaced_with_none():
    assert _json_safe(float("inf")) is None
    assert _json_safe(float("-inf")) is None


def test_finite_floats_pass_through_unchanged():
    assert _json_safe(3.14) == 3.14
    assert _json_safe(0.0) == 0.0
    assert _json_safe(-42.5) == -42.5


def test_non_float_values_pass_through_unchanged():
    assert _json_safe(True) is True
    assert _json_safe("neutral") == "neutral"
    assert _json_safe(None) is None
    assert _json_safe(7) == 7


def test_recurses_into_nested_dicts():
    """The real production case: macd_hist=nan lives inside a large, flat reasons dict, but
    this must also handle a nested structure (e.g. a list of per-pillar sub-scores) correctly,
    not just top-level floats."""
    raw = {"macd_hist": float("nan"), "trend_above_sma50": False, "nested": {"inner_nan": float("inf")}}
    safe = _json_safe(raw)
    assert safe == {"macd_hist": None, "trend_above_sma50": False, "nested": {"inner_nan": None}}


def test_recurses_into_lists():
    raw = {"scores": [1.0, float("nan"), 3.0, float("-inf")]}
    safe = _json_safe(raw)
    assert safe == {"scores": [1.0, None, 3.0, None]}


def test_real_production_reasons_shape_round_trips_to_valid_json():
    """Reproduces the EXACT failure shape confirmed live in production (6951.HK's reasons
    dict): a large, mostly-clean dict with ONE genuinely NaN float buried among many other
    real fields. The fixed output must contain no bare NaN/Infinity token anywhere."""
    raw = {
        "trend_above_sma50": False, "sma50_above_sma200": False, "golden_cross_event": False,
        "death_cross_event": False, "gc_spread_pct": None, "macd_hist": float("nan"),
        "macd_rising": False, "catalyst_score": 0.5, "insider_score": 0.0,
        "congress_score": 0.0, "composite_score": 12.6, "institutional_score": 0.0,
        "sector_momentum": -1,
    }
    s = json.dumps(_json_safe(raw))
    assert "NaN" not in s
    assert "Infinity" not in s
    # And it must be genuinely valid, standard JSON — not just missing the literal substring.
    reparsed = json.loads(s)
    assert reparsed["macd_hist"] is None
    assert reparsed["composite_score"] == 12.6  # untouched, real values survive unchanged


def test_original_dict_is_not_mutated():
    """_json_safe() must return a NEW dict/list — mutating the caller's own `ai.reasons` in
    place would be a surprising side effect on an object other code (e.g. the HTTP response
    body) may still read after this call."""
    raw = {"macd_hist": float("nan")}
    safe = _json_safe(raw)
    assert safe is not raw
    assert math.isnan(raw["macd_hist"])  # the original is untouched


# ── routes.py wiring — both real INSERT call sites must sanitize reasons before json.dumps ──

def test_bulk_persist_upsert_uses_json_safe_before_dumps():
    """_bulk_persist() — the scheduled refresh path, the dominant source of the 429/24h real
    production failures (77x/day refresh cycles across the whole universe)."""
    start = _ROUTES_SOURCE.index("def _bulk_persist(")
    end = _ROUTES_SOURCE.index("\ndef ", start + 1)
    body = _ROUTES_SOURCE[start:end]
    assert "rsns=json.dumps(_json_safe(ai.reasons))" in body


def test_signal_for_upsert_uses_json_safe_before_dumps():
    """signal_for()'s manual-refresh/persist=True path — the second real call site.
    signal_for() is the LAST function in routes.py (its own catch-all GET /{symbol} route
    must sort last per BUG233-ROUTERORDER, documented in this repo's own CLAUDE.md), so
    there's no trailing "\\ndef " to slice against — the body runs to end of file."""
    start = _ROUTES_SOURCE.index("def signal_for(")
    body = _ROUTES_SOURCE[start:]
    assert "rsns=json.dumps(_json_safe(ai.reasons))" in body


def test_no_remaining_unsafe_dumps_of_reasons_anywhere_in_routes():
    """Guards against a THIRD call site being added later without the same fix — a bare
    json.dumps(ai.reasons) or json.dumps(sig.reasons) with no _json_safe() wrapper anywhere
    in this file would silently reintroduce this exact bug class."""
    assert "json.dumps(ai.reasons)" not in _ROUTES_SOURCE
    assert "json.dumps(sig.reasons)" not in _ROUTES_SOURCE
