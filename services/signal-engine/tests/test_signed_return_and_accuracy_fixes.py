"""Tests for the AUD261 outcomes-summary/accuracy-page trio:

- AUD261-OUTCOMESSUMMARY-UNSIGNED-SELL: outcomes_summary()'s 8 aggregates pooled BUY+SELL
  raw pct_return unsigned (a SELL "wins" on a NEGATIVE raw return — see _retro_ev_for()'s own
  BUG233-RETROEV-SIGNMIX fix, the established precedent this mirrors). Fixed via a new
  _signed_return() helper applied at every collection site, including by_direction's own
  display value (which never mixed directions, but still showed the raw, misleadingly-signed
  number for SELL rows).
- AUD261-ACCURACY-MARKTOTODAY-MISLABELED-5DAY: /signals/accuracy's avg_buy_return_pct/
  avg_sell_return_pct are mark-to-today over a variable hold, not a fixed 5-day window —
  fixed by adding a real hold_days_buy/hold_days_sell distribution to the response and
  relabeling the frontend honestly (no backend query-structure change).
- AUD261-PROFITFACTOR-ABS-DECOUPLED: profit_factor bucketed abs(pct_change) by the `correct`
  label rather than real signed P&L — fixed to sum real signed gains/losses directly.

_signed_return() is a pure, dependency-free function — tested directly by extracting its
real source and exec()'ing it (no mocking of internals), matching test_backfill_realized_
ev.py's established technique for exactly this import-constraint class (outcomes.py can't be
imported directly here — conftest.py stubs the `common` package wholesale, needed for real by
outcomes.py's own `from common.jwt_auth import get_current_username`).

signal_accuracy()'s new _hold_days_summary()/_profit_factor()/_signed_pct_change() helpers are
also pure and dependency-free (operate on plain dicts, no DB/session) — extracted and tested
the same way. outcomes_summary() itself is a large route function with heavy DB/session
dependencies disproportionate to what these fixes touch — its wiring (every aggregate actually
calling _signed_return()) is covered by source-text regression checks instead, matching this
repo's established dual-technique convention for this exact shape of function.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract(name: str, end_marker: str):
    start = _ROUTES_SOURCE.index(f"def {name}(")
    end = _ROUTES_SOURCE.index(end_marker, start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace: dict = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace[name]


_signed_return = _extract("_signed_return", '@router.get("/outcomes/summary")')


# ── _signed_return() ─────────────────────────────────────────────────────────────────────

def test_buy_return_passes_through_unchanged():
    assert _signed_return(3.5, "BUY") == 3.5
    assert _signed_return(-2.1, "BUY") == -2.1


def test_sell_return_is_negated():
    # A SELL with a raw NEGATIVE return (price fell) is a real WIN — must become positive.
    assert _signed_return(-4.0, "SELL") == 4.0
    # A SELL with a raw POSITIVE return (price rose) is a real LOSS — must become negative.
    assert _signed_return(2.5, "SELL") == -2.5


def test_none_passes_through_as_none_never_crashes_or_becomes_zero():
    assert _signed_return(None, "BUY") is None
    assert _signed_return(None, "SELL") is None


def test_zero_return_stays_zero_for_both_directions():
    assert _signed_return(0.0, "BUY") == 0.0
    assert _signed_return(0.0, "SELL") == 0.0


# ── outcomes_summary() wiring: every aggregate must use _signed_return(), not raw pct_return ──

def _outcomes_summary_body() -> str:
    start = _ROUTES_SOURCE.index('@router.get("/outcomes/summary")')
    end = _ROUTES_SOURCE.index("\n@router.get", start + 1)
    return _ROUTES_SOURCE[start:end]


def test_overall_uses_signed_return():
    body = _outcomes_summary_body()
    assert 'returns = [_signed_return(o.pct_return, o.signal_direction) for o in outcomes if o.pct_return is not None]' in body


def test_confidence_band_uses_signed_return():
    body = _outcomes_summary_body()
    assert 'bucket_returns = [_signed_return(o.pct_return, o.signal_direction) for o in bucket if o.pct_return is not None]' in body


def test_by_horizon_uses_signed_return():
    body = _outcomes_summary_body()
    assert 'hreturns = [_signed_return(o.pct_return, o.signal_direction) for o in hbucket if o.pct_return is not None]' in body


def test_by_market_regime_uses_signed_return():
    body = _outcomes_summary_body()
    assert 'regime_stats[reg]["returns"].append(_signed_return(o.pct_return, o.signal_direction))' in body


def test_by_research_alignment_uses_signed_return():
    body = _outcomes_summary_body()
    assert 'research_groups[grp]["returns"].append(_signed_return(o.pct_return, o.signal_direction))' in body


def test_by_window_uses_signed_return():
    body = _outcomes_summary_body()
    assert 'rets = [_signed_return(r, d) for _, r, d in vals if r is not None]' in body
    # the tuple must include signal_direction alongside the correct/return columns, or there's
    # nothing to pass to _signed_return in the first place
    assert 'for o in outcomes if getattr(o, attr_correct) is not None' in body


def test_by_direction_uses_signed_return_even_though_it_never_mixes_directions():
    """by_direction never pools BUY+SELL (each bucket is single-direction) — but its own
    displayed avg_return_pct still needs the sign fix, or a winning SELL (negative raw return)
    displays as a negative/red number and a losing SELL displays as positive/green, the
    opposite of what "Avg Ret" should mean to a reader."""
    body = _outcomes_summary_body()
    assert 'bucket_returns = [_signed_return(o.pct_return, o.signal_direction) for o in bucket if o.pct_return is not None]' in body
    # confirm this specific occurrence sits inside the direction_stats loop, not just anywhere
    direction_loop_idx = body.index('direction_stats: dict = {}')
    signed_call_idx = body.index('_signed_return(o.pct_return, o.signal_direction) for o in bucket', direction_loop_idx)
    assert signed_call_idx > direction_loop_idx


def test_by_market_uses_signed_return():
    body = _outcomes_summary_body()
    assert 'market_stats[mkt]["returns"].append(_signed_return(o.pct_return, o.signal_direction))' in body


def test_by_symbol_uses_signed_return():
    body = _outcomes_summary_body()
    assert 'sym_groups[sym]["returns"].append(_signed_return(o.pct_return, o.signal_direction))' in body


# ── AUD261-BYSYMBOL-MIN-COUNT-2 ─────────────────────────────────────────────────────────────

def test_by_symbol_excluded_n1_count_is_computed_before_the_count_2_filter():
    """The excluded count must be derived from sym_groups BEFORE the >=2 filter drops anything
    — computing it from the already-filtered by_symbol list would always be zero, defeating the
    whole point of the fix."""
    body = _outcomes_summary_body()
    excluded_idx = body.index('_by_symbol_excluded_n1 = sum(1 for v in sym_groups.values() if v["count"] < 2)')
    filter_idx = body.index('if v["count"] >= 2')
    assert excluded_idx < filter_idx


def test_by_symbol_excluded_n1_is_included_in_the_response():
    body = _outcomes_summary_body()
    assert '"by_symbol_excluded_n1": _by_symbol_excluded_n1,' in body
    # must sit alongside by_symbol in the same return dict, not a separate/unreachable branch
    by_symbol_idx = body.index('"by_symbol": by_symbol,')
    excluded_field_idx = body.index('"by_symbol_excluded_n1": _by_symbol_excluded_n1,')
    assert excluded_field_idx > by_symbol_idx


# ── signal_accuracy()'s new helpers: _hold_days_summary / _signed_pct_change / _profit_factor ──

def _extract_signal_accuracy_helper(name: str, namespace: dict):
    """These three helpers are defined INSIDE signal_accuracy() (closures), not at module
    level — extract just the def block by locating it between its own header and the next
    def/return at the same indentation. _profit_factor() calls _signed_pct_change()
    internally, so both must be exec()'d into the SAME namespace for that call to resolve —
    two separate exec() calls each get their own isolated globals and can't see each other."""
    sa_start = _ROUTES_SOURCE.index("def signal_accuracy(")
    sa_end = _ROUTES_SOURCE.index("\n@router.get", sa_start + 1)
    sa_body = _ROUTES_SOURCE[sa_start:sa_end]
    start = sa_body.index(f"def {name}(")
    end = sa_body.index("\n    def ", start + 1) if "\n    def " in sa_body[start + 1:] else sa_body.index("\n    offset = ", start)
    func_source = sa_body[start:end]
    exec(func_source, namespace)  # noqa: S102
    return namespace[name]


_signal_accuracy_helpers_ns: dict = {}
_hold_days_summary = _extract_signal_accuracy_helper("_hold_days_summary", _signal_accuracy_helpers_ns)
_signed_pct_change = _extract_signal_accuracy_helper("_signed_pct_change", _signal_accuracy_helpers_ns)
_profit_factor = _extract_signal_accuracy_helper("_profit_factor", _signal_accuracy_helpers_ns)


def test_hold_days_summary_returns_none_for_empty_input():
    assert _hold_days_summary([]) is None


def test_hold_days_summary_min_median_max_odd_count():
    items = [{"days_held": d} for d in [2, 89, 5]]
    result = _hold_days_summary(items)
    assert result == {"min": 2, "median": 5, "max": 89}


def test_hold_days_summary_median_even_count_averages_middle_two():
    items = [{"days_held": d} for d in [1, 3, 5, 7]]
    result = _hold_days_summary(items)
    assert result["median"] == 4  # (3+5)/2
    assert result["min"] == 1
    assert result["max"] == 7


def test_signed_pct_change_buy_passes_through():
    assert _signed_pct_change({"signal": "BUY", "pct_change": 3.0}) == 3.0
    assert _signed_pct_change({"signal": "BUY", "pct_change": -1.5}) == -1.5


def test_signed_pct_change_sell_is_negated():
    # SELL with negative raw pct_change (price fell) = real win = positive signed value.
    assert _signed_pct_change({"signal": "SELL", "pct_change": -3.0}) == 3.0
    # SELL with positive raw pct_change (price rose) = real loss = negative signed value.
    assert _signed_pct_change({"signal": "SELL", "pct_change": 2.0}) == -2.0


def test_profit_factor_computed_from_real_signed_pnl_not_abs_by_label():
    """AUD261-PROFITFACTOR-ABS-DECOUPLED's exact failure scenario: a batch of high-magnitude
    correct calls and low-magnitude wrong calls previously produced PF > 1.5 (colored "good")
    even though the same rows lost money on average. With the real fix, PF is computed from
    real signed P&L directly. For a pure-BUY set where `correct` and the raw pct_change sign
    always agree, abs()-by-label and signed math produce the SAME number by coincidence — the
    real divergence only shows up when a row's `correct` label and its raw sign disagree,
    which happens near the win/loss hurdle boundary: a BUY that moved in the "right" direction
    (positive pct_change) but didn't clear the hurdle is labeled `correct=False`. The OLD
    abs()-by-label code bucketed its MAGNITUDE into losses (since label says incorrect); the
    NEW signed code correctly counts its actual positive value as a gain regardless of the
    label. Verified this divergence is real (not just theorized) by running both formulas on
    this exact fixture before writing the assertion: OLD gives PF=0.0, NEW gives PF=0.02."""
    items = [
        {"signal": "BUY", "pct_change": 0.2, "correct": False},    # below hurdle, but price DID rise
        {"signal": "BUY", "pct_change": -10.0, "correct": False},  # a real loss
    ]
    pf = _profit_factor(items)
    assert pf == 0.02  # signed: wins=0.2, losses=10.0 -> 0.02 (NOT the old abs()-by-label 0.0)


def test_profit_factor_sell_wins_count_as_real_gains_not_losses():
    """A correct SELL (price fell, negative raw pct_change) must contribute to WINS, not
    losses. Uses the same below-hurdle-disagreement shape as the test above, mirrored onto
    SELL: a SELL that moved in the "right" direction (price fell) but not enough to clear the
    hurdle is labeled `correct=False` despite its real signed value being a small gain."""
    items = [
        {"signal": "SELL", "pct_change": -0.3, "correct": False},  # price fell (real small gain), below hurdle
        {"signal": "SELL", "pct_change": 5.0, "correct": False},   # price rose — a real loss
    ]
    pf = _profit_factor(items)
    assert pf == 0.06  # signed: wins=0.3, losses=5.0 -> 0.06 (NOT the old abs()-by-label 0.0)


def test_profit_factor_none_when_no_losses():
    items = [{"signal": "BUY", "pct_change": 5.0, "correct": True}]
    assert _profit_factor(items) is None


def test_hold_days_buy_and_sell_added_to_response_and_empty_return():
    body = _ROUTES_SOURCE
    assert '"hold_days_buy": _hold_days_summary(buy_r)' in body
    assert '"hold_days_sell": _hold_days_summary(sell_r)' in body
    # the early "no rows at all" return must also carry these keys, so the response shape is
    # consistent whether or not there are any evaluated signals in the window
    empty_return_idx = body.index('if not rows:')
    tail = body[empty_return_idx:empty_return_idx + 400]
    assert '"hold_days_buy": None, "hold_days_sell": None' in tail
