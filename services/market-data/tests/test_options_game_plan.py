"""Tests for T322-OPTIONS-GAMEPLAN's compute_options_game_plan()/_nearest_expiry_in_dte_window()/
_nearest_strike() — the pure composition layer behind GET /{symbol}/options-game-plan.

routes.py can't be imported directly in this test environment (its import chain pulls in
common.config/db, none of which conftest.py stubs for real) — the real source of each function
is extracted and exec()'d, matching test_max_pain.py's/test_options_chain.py's established
source-text-extraction technique for functions in this exact file.
"""
import pathlib
from datetime import date, datetime, timezone

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_shared_namespace():
    """All three functions share one exec() namespace — compute_options_game_plan() calls the
    other two directly by name, so extracting it alone (with the helpers absent from its own
    namespace) would raise NameError the moment it's actually invoked."""
    start = _ROUTES_SOURCE.index("_OPTIONS_GAME_PLAN_MIN_PUT_DTE = ")
    end = _ROUTES_SOURCE.index('\n@router.get("/{symbol}/options-game-plan")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {"date": date, "datetime": datetime, "timezone": timezone}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of these pure functions' real source
    return namespace


_ns = _extract_shared_namespace()
_nearest_expiry_in_dte_window = _ns["_nearest_expiry_in_dte_window"]
_nearest_strike = _ns["_nearest_strike"]
compute_options_game_plan = _ns["compute_options_game_plan"]

TODAY = date(2026, 9, 1)


def _put_row(strike, bid, ask, last=0.0, oi=100, iv=30.0):
    return {"strike": strike, "bid": bid, "ask": ask, "last_price": last, "oi": oi, "iv": iv}


# ── _nearest_expiry_in_dte_window ────────────────────────────────────────────────────────────

def test_picks_the_expiry_closest_to_the_window_center_not_just_closest_to_min_dte():
    # window is [25, 60], center = 42.5 days out from TODAY (2026-09-01) -> 2026-10-14
    expiries = ["2026-09-26", "2026-10-14", "2026-10-30", "2026-12-04"]
    result = _nearest_expiry_in_dte_window(expiries, TODAY, 25, 60)
    assert result == "2026-10-14"


def test_falls_back_to_the_nearest_overall_expiry_when_none_fall_inside_the_window():
    # only far-dated expiries exist; none land in [25, 60] days out
    expiries = ["2026-09-08", "2027-01-15"]
    result = _nearest_expiry_in_dte_window(expiries, TODAY, 25, 60)
    assert result in expiries  # must still return SOMETHING real, never None, when expiries exist


def test_returns_none_for_an_empty_expiry_list():
    assert _nearest_expiry_in_dte_window([], TODAY, 25, 60) is None


def test_skips_a_malformed_expiry_string_rather_than_crashing():
    expiries = ["not-a-date", "2026-10-14"]
    result = _nearest_expiry_in_dte_window(expiries, TODAY, 25, 60)
    assert result == "2026-10-14"


def test_excludes_an_expiry_that_has_already_passed():
    expiries = ["2026-08-01", "2026-10-14"]  # first is BEFORE TODAY
    result = _nearest_expiry_in_dte_window(expiries, TODAY, 25, 60)
    assert result == "2026-10-14"


# ── _nearest_strike ───────────────────────────────────────────────────────────────────────────

def test_picks_the_closest_listed_strike_to_the_target_price():
    rows = [_put_row(140.0, 1, 2), _put_row(145.0, 2, 3), _put_row(150.0, 3, 4)]
    result = _nearest_strike(rows, 144.0)
    assert result["strike"] == 145.0


def test_returns_none_for_an_empty_row_list():
    assert _nearest_strike([], 145.0) is None


def test_returns_none_when_target_is_none():
    rows = [_put_row(140.0, 1, 2)]
    assert _nearest_strike(rows, None) is None


# ── compute_options_game_plan ────────────────────────────────────────────────────────────────

def test_protective_put_uses_the_bid_ask_midpoint_and_reports_a_real_effective_floor():
    result = compute_options_game_plan(
        current_price=150.0,
        stop_loss=142.0,
        take_profit=None,
        signal="BUY",
        put_expiries=["2026-10-14"],  # 43 days out, inside [25, 60]
        put_rows=[_put_row(140.0, 2.9, 3.1, oi=500, iv=32.0)],
        call_expiries=[],
        call_rows=[],
        today=TODAY,
    )
    pp = result["protective_put"]
    assert pp is not None
    assert pp["strike"] == 140.0
    assert pp["mid_price"] == 3.0
    assert pp["effective_floor_price"] == 137.0  # strike - mid
    assert pp["cost_per_contract"] == 300.0
    assert pp["in_target_window"] is True
    assert result["covered_call"] is None


def test_covered_call_reports_a_real_effective_cap_above_the_strike():
    result = compute_options_game_plan(
        current_price=150.0,
        stop_loss=None,
        take_profit=168.0,
        signal="BUY",
        put_expiries=[],
        put_rows=[],
        call_expiries=["2026-09-25"],  # 24 days out, inside [14, 45]
        call_rows=[{"strike": 168.0, "bid": 1.7, "ask": 2.0, "last_price": 0.0, "oi": 200, "iv": 28.0}],
        today=TODAY,
    )
    cc = result["covered_call"]
    assert cc is not None
    assert cc["strike"] == 168.0
    assert cc["mid_price"] == 1.85
    assert cc["effective_cap_price"] == 169.85  # strike + mid
    assert result["protective_put"] is None


def test_both_legs_can_be_computed_independently_in_one_call():
    result = compute_options_game_plan(
        current_price=150.0,
        stop_loss=142.0,
        take_profit=168.0,
        signal="BUY",
        put_expiries=["2026-10-14"],
        put_rows=[_put_row(140.0, 2.9, 3.1)],
        call_expiries=["2026-09-25"],
        call_rows=[{"strike": 168.0, "bid": 1.7, "ask": 2.0, "last_price": 0.0, "oi": 200, "iv": 28.0}],
        today=TODAY,
    )
    assert result["protective_put"] is not None
    assert result["covered_call"] is not None


def test_no_stop_loss_means_no_protective_put_even_with_real_put_data_present():
    result = compute_options_game_plan(
        current_price=150.0,
        stop_loss=None,
        take_profit=None,
        signal=None,
        put_expiries=["2026-10-14"],
        put_rows=[_put_row(140.0, 2.9, 3.1)],
        call_expiries=[],
        call_rows=[],
        today=TODAY,
    )
    assert result["protective_put"] is None


def test_falls_back_to_last_price_when_bid_and_ask_are_both_zero():
    # a real, if illiquid, case — a thin contract with no live bid/ask, only a stale last trade
    result = compute_options_game_plan(
        current_price=150.0,
        stop_loss=142.0,
        take_profit=None,
        signal=None,
        put_expiries=["2026-10-14"],
        put_rows=[_put_row(140.0, 0.0, 0.0, last=2.5)],
        call_expiries=[],
        call_rows=[],
        today=TODAY,
    )
    assert result["protective_put"]["mid_price"] == 2.5


def test_result_always_reports_the_inputs_passed_through():
    result = compute_options_game_plan(
        current_price=150.0, stop_loss=None, take_profit=None, signal="BUY",
        put_expiries=[], put_rows=[], call_expiries=[], call_rows=[], shares=100, today=TODAY,
    )
    assert result["signal"] == "BUY"
    assert result["current_price"] == 150.0
    assert result["shares"] == 100
