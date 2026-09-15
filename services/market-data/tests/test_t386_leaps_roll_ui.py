"""T386-LEAPS-ROLL-UI — the rolling backtest existed only as a Python function.

REPORTED BY THE USER: "I don't see the rolling Leap backtests".

They were right. T385 built and tested `backtest_leaps_rolling()`, measured it against real
captured chains, and then exposed it nowhere — no API route, no UI. From the application it was
indistinguishable from never having been built.

THIS IS THE THIRD TIME THIS EXACT SHAPE HAS APPEARED in this codebase:
  * `AUD-GAMEPLANBATCH-WRONGIMPORT` — computed, never reached the caller
  * `T370-EARNINGS-DIRECTION` — `impact_text` written to the DB, never serialised by any route
  * `T383-DARKPOOL-UI` — T377 stored the dark-pool side; no endpoint returned it

The recurring lesson: **a backend function is not a feature until something a person can open
calls it.** Worth checking explicitly at the end of any "add capability X" task.

WHAT THE ROUTE MUST PRESERVE from T385, since a thin wrapper is the easiest place to lose it:
  * `hold_days` and `min_dte` are INDEPENDENT — `min_dte` says how long-dated the CONTRACT is,
    `hold_days` how long it is HELD. A 730-DTE contract sold after 182 days is the normal case.
  * `total_spread_cost` is the number that decides whether rolling beat holding (measured: 4
    six-month cycles paid $1,638 against $384 for one 2-year hold — 4.3x).
  * `cycles_skipped` names any unpriceable window; a gap mid-sequence changes what the total
    return means.

A MISTAKE MADE WHILE SHIPPING THIS, recorded because CLAUDE.md already warns about it: I ran
`docker restart` to pick up the route while a long backfill was running via `docker exec`, and
the restart killed it. `docker cp` alone would have sufficed. No data was lost only because
`capture_option_chain_history(skip_existing=True)` is resumable by design.
"""
import pathlib

import pytest

ROUTES = (
    pathlib.Path(__file__).resolve().parents[1] / "src/api/paper_portfolio.py"
).read_text()
API_TS = (
    pathlib.Path(__file__).resolve().parents[3] / "frontend/src/lib/api.ts"
).read_text()
PANEL = (
    pathlib.Path(__file__).resolve().parents[3]
    / "frontend/src/components/LeapsBacktestPanel.tsx"
).read_text()


def _route() -> str:
    i = ROUTES.index('@router.get("/backtest/leaps/rolling")')
    return ROUTES[i:ROUTES.index("\n@router", i + 10)]


# ── The route exists and is wired to the real engine ────────────────────────────────────

def test_the_route_exists():
    """THE BUG — T385's function had no route at all."""
    assert '@router.get("/backtest/leaps/rolling")' in ROUTES


def test_it_calls_the_audited_engine():
    """Re-implementing the loop here would fork away from T385's compounding fix and T380's
    contract-gap walk."""
    assert "from ..backtest.leaps_backtest import backtest_leaps_rolling as _roll" in _route()
    assert "_roll(symbol, d_in, d_out, hold_days" in _route()


def test_hold_days_is_a_first_class_parameter():
    """The user's literal ask: 'sell before 2 years like half a year or a year'."""
    assert "hold_days: int = Query(" in _route()


def test_hold_days_and_min_dte_are_independent():
    """If the route derived one from the other, the whole point would be lost — a 730-DTE
    contract sold after 182 days is the NORMAL case for this mode."""
    r = _route()
    assert "min_dte: int = Query(" in r
    assert "hold_days=min_dte" not in r and "min_dte=hold_days" not in r


def test_it_is_auth_gated_like_its_sibling_routes():
    assert "_: User = Depends(get_current_user)," in _route()


# ── Input validation ────────────────────────────────────────────────────────────────────

def test_bad_dates_are_rejected_with_400():
    r = _route()
    assert 'raise HTTPException(400, "dates must be YYYY-MM-DD")' in r
    assert "end_date must be after start_date" in r


def test_a_range_shorter_than_one_cycle_is_rejected_explicitly():
    """Otherwise the engine returns None and the caller sees a bare 404, when the real problem
    is that not even one full cycle fits in the requested window."""
    r = _route()
    assert "(d_out - d_in).days < hold_days" in r
    assert "not even one full cycle fits" in r


def test_no_result_returns_404_with_a_reason():
    """Never a fabricated result. Matches the single-cycle route's own contract."""
    r = _route()
    assert "raise HTTPException(404" in r
    assert "coverage" in r, "points the caller at the coverage endpoint"


# ── The frontend actually calls it ──────────────────────────────────────────────────────

def test_the_api_client_has_a_rolling_method():
    assert "leapsRolling:" in API_TS
    assert "/paper-portfolio/backtest/leaps/rolling?" in API_TS


def test_the_client_sends_hold_days_and_compound():
    i = API_TS.index("leapsRolling:")
    blk = API_TS[i:i + 900]
    assert "hold_days: String(p.hold_days)" in blk
    assert "compound: String(p.compound ?? true)" in blk


def test_a_rolling_result_type_exists_and_types_the_cycles():
    assert "export type LeapsRolling = {" in API_TS
    assert "cycles: LeapsTrade[];" in API_TS


def test_the_panel_has_a_roll_mode():
    assert "'hold' | 'roll'" in PANEL
    assert "api.leapsRolling(" in PANEL


def test_switching_mode_clears_the_previous_result():
    """A stale hold result sitting under a roll run would be read as the roll's own output."""
    i = PANEL.index("setMode(m)")
    assert "setResult(null)" in PANEL[i:i + 120]
    assert "setRollResult(null)" in PANEL[i:i + 120]


def test_rolling_runs_one_symbol_at_a_time():
    """Each symbol produces its own cycle SEQUENCE. Stacking several into the compare table
    would imply a like-for-like ranking across sequences with different cycle counts and
    different skipped windows."""
    assert "symbol: selected[0]" in PANEL


# ── The cost of rolling stays visible ───────────────────────────────────────────────────

def test_the_panel_shows_the_spread_paid():
    """THE NUMBER THAT DECIDES whether rolling beat holding. Measured: $1,638 over 4 cycles
    against $384 for one 2-year hold."""
    assert "total_spread_cost" in PANEL
    # A GAP MY OWN SABOTAGE TESTING EXPOSED: asserting only that the FIELD is referenced let a
    # rename of the visible label ("Spread paid" -> "Cycles run") pass, which would leave the
    # number on screen under a caption that describes something else entirely. Pin the label
    # to the value it sits above.
    i = PANEL.index("total_spread_cost")
    around = PANEL[max(0, i - 400):i + 200]
    assert "Spread paid" in around, "the spread figure must be labelled as the spread"
    assert "$" in around, "and rendered as money"


def test_the_panel_warns_before_concluding_rolling_wins():
    assert "before concluding rolling wins" in PANEL


def test_skipped_cycles_are_surfaced_with_their_reasons():
    """A gap mid-sequence changes what the total return means — it must never be silent."""
    assert "cycles_skipped.length > 0" in PANEL
    assert "the total covers only the cycles that ran" in PANEL


def test_a_relaxed_delta_cycle_is_still_badged():
    """T381's NEAR badge must survive into the per-cycle table, or a relaxed entry inside a
    roll would look like a strict one."""
    assert "c.delta_relaxed &&" in PANEL
    assert "NEAR" in PANEL


def test_the_panel_does_not_falsy_test_returns():
    """`c.return_pct != null`, not truthiness — a genuine 0.00% cycle must render as 0.00%,
    not as an em-dash."""
    assert "c.return_pct != null" in PANEL


# ── The recurring lesson, pinned ────────────────────────────────────────────────────────

def test_the_route_documents_the_independence_of_hold_and_dte():
    """So a future edit does not "simplify" one into the other."""
    r = _route()
    assert "how long-dated the CONTRACT is" in r
    assert "how long it is HELD" in r


def test_the_measured_roll_vs_hold_numbers_are_in_the_route_docstring():
    """A caller reading only the API reference still sees that rolling paid 4.3x the spread."""
    r = _route()
    assert "+131.48%" in r and "+118.00%" in r
    assert "4.3x" in r
