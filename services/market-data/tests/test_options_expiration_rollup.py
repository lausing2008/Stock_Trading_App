"""Tests for MPE-03's compute_expiration_rollup() — per-expiration OI/volume rollup with a
NORMAL/ELEVATED/HIGH/EXTREME concentration classification relative to the OTHER expiries
fetched in the same call (no historical per-expiration OI time series exists anywhere in this
app to compare against, so this is an honest relative-to-peers read, not a fabricated baseline).

routes.py can't be imported directly — source-text extraction, matching test_max_pain.py's
established technique.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_compute_expiration_rollup():
    start = _ROUTES_SOURCE.index("def compute_expiration_rollup(")
    end = _ROUTES_SOURCE.index('\n\n@router.get("/{symbol}/options-expirations")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {}
    exec(func_source, namespace)  # noqa: S102
    return namespace["compute_expiration_rollup"]


compute_expiration_rollup = _extract_compute_expiration_rollup()


def _row(expiry, call_oi, put_oi, call_volume=0, put_volume=0):
    return {"expiry": expiry, "call_oi": call_oi, "put_oi": put_oi,
            "call_volume": call_volume, "put_volume": put_volume}


def test_returns_empty_list_when_total_oi_is_zero_not_a_crash():
    result = compute_expiration_rollup([_row("2026-09-19", 0, 0)])
    assert result == []


def test_returns_empty_list_for_empty_input():
    result = compute_expiration_rollup([])
    assert result == []


def test_an_even_4_way_split_lands_at_25_pct_each_classified_high_at_the_boundary():
    """4 expiries with identical OI split the total evenly at 25% each — right at the
    documented HIGH boundary (>= 25%), the exact even-split reference point the thresholds
    were chosen relative to."""
    rows = [_row(f"exp{i}", 100, 100) for i in range(4)]
    result = compute_expiration_rollup(rows)
    for r in result:
        assert r["concentration_pct"] == 25.0
        assert r["level"] == "high"


def test_a_single_dominant_expiration_is_classified_extreme():
    rows = [_row("2026-09-19", 5000, 5000), _row("2026-10-17", 100, 100), _row("2026-11-21", 100, 100)]
    result = compute_expiration_rollup(rows)
    dominant = next(r for r in result if r["expiry"] == "2026-09-19")
    assert dominant["level"] == "extreme"
    assert dominant["concentration_pct"] > 40.0


def test_thresholds_hand_verified_at_each_boundary():
    """5 expiries with hand-picked OI so each one's concentration_pct lands exactly at a
    documented boundary: 5/700=... not exact, so use round numbers directly.
    Total = 1000. Rows: 400 (40% -> extreme), 250 (25% -> high), 150 (15% -> elevated),
    100 (10% -> normal), 100 (10% -> normal)."""
    rows = [
        _row("extreme_exp", 200, 200),   # 400/1000 = 40%
        _row("high_exp", 125, 125),      # 250/1000 = 25%
        _row("elevated_exp", 75, 75),    # 150/1000 = 15%
        _row("normal_exp_a", 50, 50),    # 100/1000 = 10%
        _row("normal_exp_b", 50, 50),    # 100/1000 = 10%
    ]
    result = compute_expiration_rollup(rows)
    by_expiry = {r["expiry"]: r for r in result}
    assert by_expiry["extreme_exp"]["concentration_pct"] == 40.0
    assert by_expiry["extreme_exp"]["level"] == "extreme"
    assert by_expiry["high_exp"]["concentration_pct"] == 25.0
    assert by_expiry["high_exp"]["level"] == "high"
    assert by_expiry["elevated_exp"]["concentration_pct"] == 15.0
    assert by_expiry["elevated_exp"]["level"] == "elevated"
    assert by_expiry["normal_exp_a"]["concentration_pct"] == 10.0
    assert by_expiry["normal_exp_a"]["level"] == "normal"


def test_put_call_oi_ratio_computed_correctly():
    rows = [_row("2026-09-19", call_oi=200, put_oi=100)]
    result = compute_expiration_rollup(rows)
    assert result[0]["put_call_oi_ratio"] == 0.5


def test_put_call_oi_ratio_is_none_not_a_divide_by_zero_when_call_oi_is_zero():
    rows = [_row("2026-09-19", call_oi=0, put_oi=500)]
    result = compute_expiration_rollup(rows)
    assert result[0]["put_call_oi_ratio"] is None


def test_total_oi_is_call_plus_put_for_that_expiry():
    rows = [_row("2026-09-19", call_oi=300, put_oi=200)]
    result = compute_expiration_rollup(rows)
    assert result[0]["total_oi"] == 500


def test_volume_fields_pass_through_unchanged():
    rows = [_row("2026-09-19", call_oi=100, put_oi=100, call_volume=42, put_volume=17)]
    result = compute_expiration_rollup(rows)
    assert result[0]["call_volume"] == 42
    assert result[0]["put_volume"] == 17


def test_a_single_expiry_alone_is_always_100_pct_and_extreme():
    """With only one expiration fetched, it necessarily holds 100% of the (single-row) total —
    a real, if degenerate, case that must not crash."""
    rows = [_row("2026-09-19", call_oi=500, put_oi=500)]
    result = compute_expiration_rollup(rows)
    assert result[0]["concentration_pct"] == 100.0
    assert result[0]["level"] == "extreme"


def test_missing_oi_fields_default_to_zero_not_a_crash():
    result = compute_expiration_rollup([{"expiry": "2026-09-19"}])
    assert result == []


def test_route_is_registered_as_a_real_literal_path():
    assert '@router.get("/{symbol}/options-expirations")' in _ROUTES_SOURCE


def test_no_catch_all_get_symbol_route_exists_in_this_file_to_shadow_it():
    """The BUG233-ROUTERORDER class this repo has hit before: a bare GET /{symbol} catch-all
    registered earlier in the same router would silently swallow a later literal-path route."""
    assert '@router.get("/{symbol}")' not in _ROUTES_SOURCE
