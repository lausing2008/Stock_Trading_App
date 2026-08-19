"""Tests for IF-05's compute_max_pain() — the strike at which options WRITERS would owe the
LEAST total intrinsic value at expiry.

routes.py can't be imported directly in this test environment (its import chain pulls in
common.config/db, none of which conftest.py stubs for real) — compute_max_pain()'s real source
is extracted and exec()'d, matching test_options_chain.py's established source-text-extraction
technique for functions in this exact file.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_compute_max_pain():
    start = _ROUTES_SOURCE.index("def compute_max_pain(")
    end = _ROUTES_SOURCE.index('\n@router.get("/{symbol}/options-chain")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["compute_max_pain"]


compute_max_pain = _extract_compute_max_pain()


def _row(strike, oi):
    return {"strike": strike, "oi": oi}


def test_returns_none_when_both_sides_have_zero_open_interest():
    result = compute_max_pain([_row(100.0, 0)], [_row(100.0, 0)])
    assert result is None


def test_returns_none_when_there_are_no_strikes_at_all():
    result = compute_max_pain([], [])
    assert result is None


def test_simple_symmetric_case_picks_the_strike_with_the_most_combined_oi():
    """A single strike with real OI on both sides is trivially its own max pain point —
    there's nowhere else for the price to go that produces less payout."""
    calls = [_row(100.0, 500)]
    puts = [_row(100.0, 500)]
    result = compute_max_pain(calls, puts)
    assert result["max_pain_strike"] == 100.0
    assert result["total_call_oi"] == 500
    assert result["total_put_oi"] == 500
    assert result["put_call_oi_ratio"] == 1.0


def test_heavy_call_oi_at_a_lower_strike_pulls_max_pain_down():
    """Verified by hand: calls concentrated at 95 with heavy OI, puts thin and spread out —
    the call side dominates the total-payout curve, so max pain should sit AT or BELOW the
    heavy call strike (going above 95 makes every one of those calls newly ITM, a large payout
    increase that dwarfs the puts)."""
    calls = [_row(95.0, 10_000), _row(100.0, 100), _row(105.0, 50)]
    puts = [_row(95.0, 50), _row(90.0, 100)]
    result = compute_max_pain(calls, puts)
    assert result["max_pain_strike"] <= 95.0


def test_heavy_put_oi_at_a_higher_strike_pulls_max_pain_up():
    """Mirror case: puts concentrated at 105 with heavy OI."""
    calls = [_row(95.0, 50), _row(100.0, 100)]
    puts = [_row(105.0, 10_000), _row(100.0, 100), _row(95.0, 50)]
    result = compute_max_pain(calls, puts)
    assert result["max_pain_strike"] >= 105.0


def test_a_strike_that_only_has_puts_listed_can_still_be_the_candidate():
    """A strike present on only ONE side must still be considered as a candidate max-pain
    point — the union of both sides' strikes, not just one side's."""
    calls = [_row(100.0, 5000)]
    puts = [_row(90.0, 5000)]
    result = compute_max_pain(calls, puts)
    # With these two isolated, heavy, symmetric-magnitude OI blocks, the true minimum sits
    # somewhere between them — confirm it picked one of the two REAL listed strikes, not a
    # fabricated value outside the candidate set.
    assert result["max_pain_strike"] in (90.0, 100.0)


def test_put_call_oi_ratio_is_none_when_call_oi_is_zero_not_a_divide_by_zero_crash():
    calls = [_row(100.0, 0)]
    puts = [_row(100.0, 500)]
    result = compute_max_pain(calls, puts)
    assert result is not None
    assert result["put_call_oi_ratio"] is None


def test_a_realistic_multi_strike_chain_computed_by_hand():
    """A small, fully hand-computed chain to confirm the actual arithmetic, not just the
    directional behavior of the tests above.

    Strikes: 95, 100, 105. Calls OI: {95: 200, 100: 300, 105: 100}. Puts OI:
    {95: 100, 100: 300, 105: 200}.

    Total payout at each candidate strike S = sum(call_oi[K] * max(0, S-K)) + sum(put_oi[K] * max(0, K-S)):
      S=95:  calls: 0                                    = 0
             puts:  100*(95-95)+300*(100-95)+200*(105-95) = 0+1500+2000 = 3500
             total = 3500
      S=100: calls: 200*(100-95)+300*0+100*0 = 1000
             puts:  100*0+300*0+200*(105-100) = 1000
             total = 2000
      S=105: calls: 200*(105-95)+300*(105-100)+100*0 = 2000+1500+0 = 3500
             puts:  0
             total = 3500

    Minimum is at S=100 (2000), clearly less than either flank (3500 each) — a real,
    non-degenerate case where the middle strike wins on total payout, not just OI concentration.
    """
    calls = [_row(95.0, 200), _row(100.0, 300), _row(105.0, 100)]
    puts = [_row(95.0, 100), _row(100.0, 300), _row(105.0, 200)]
    result = compute_max_pain(calls, puts)
    assert result["max_pain_strike"] == 100.0
    assert result["total_call_oi"] == 600
    assert result["total_put_oi"] == 600
