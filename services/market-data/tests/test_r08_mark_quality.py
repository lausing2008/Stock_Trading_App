"""R08 (2026-09-24 follow-up audit): marks without durable freshness evidence.

DA-06 gave short_option_liability() an arbitrage floor and _latest_option_ask() an age bound,
and both were right. What neither established is a POLICY. `_MAX_ASK_AGE_DAYS = 5` was doing
two incompatible jobs at once: deciding whether an archived quote may be READ, and implying
that anything it returns is a good MARK.

Those are different questions, and the audit's probe is where they come apart: a four-day-old
$0.01 ask on an OTM put is inside the query window and becomes a $1 liability, with its date
discarded, labelled `quote_ask` exactly like a same-session quote. The intrinsic floor cannot
help — an OTM option has zero intrinsic, so the floor binds nothing and stale TIME VALUE passes
straight through. Five sessions is a reasonable bound on an archive; it is nowhere near a
freshness standard for a valuation.

FOUR THINGS WERE MISSING, and the audit names each:

  1. The quote's DATE. The query already selected `as_of` and then discarded it, so nothing
     downstream could distinguish four days old from today.
  2. A GRADE distinct from eligibility — see mark_quality().
  3. The UNDERLYING's own provenance. `_fetch_live_prices()` returns the CURRENT price whatever
     date is being valued, so a historical snapshot mixed a past option quote with today's
     underlying; and a missing live price fell back to the position's ENTRY price, reporting an
     unchanged position however far the underlying had moved.
  4. DURABILITY. All of it was counted into a log line, which answers the question for as long
     as the log is retained and not at all afterwards. The audit's bar: "Readers must
     reconstruct why an equity value was used without consulting ephemeral logs."

NOT A REVALUATION. No mark that was used before is rejected now. Every number is what it was;
what changed is that each one says what it is.
"""
import pathlib
from datetime import date, timedelta

import pytest

from src.services.options_income_engine import (
    _APPROXIMATE_MARK_QUALITIES, _MARK_FRESH_MAX_AGE_DAYS, _MARK_RECENT_MAX_AGE_DAYS,
    _MAX_ASK_AGE_DAYS, mark_quality, short_option_liability,
)

_ENGINE = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "services" / "options_income_engine.py").read_text()


def _executable_only(src: str) -> str:
    """Source with its docstring and comments removed — see the prose-collision note above."""
    for q in ('"' * 3, "'" * 3):
        first = src.find(q)
        if first != -1:
            second = src.find(q, first + 3)
            if second != -1:
                src = src[:first] + src[second + 3:]
                break
    return "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())

# ── The grade ────────────────────────────────────────────────────────────────

def test_a_same_session_quote_is_fresh():
    assert mark_quality("quote_ask", 0) == "fresh"
    assert mark_quality("quote_ask", 1) == "fresh"


def test_a_two_or_three_session_old_quote_is_recent_not_fresh():
    assert mark_quality("quote_ask", 2) == "recent"
    assert mark_quality("quote_ask", 3) == "recent"


def test_the_audits_four_day_old_quote_is_stale_even_though_it_was_accepted():
    """THE CORE R08 CASE. The quote is inside the archive window — it was read, and it is still
    used — but it is no longer presented as a current valuation."""
    assert mark_quality("quote_ask", 4) == "stale"
    assert mark_quality("quote_ask", _MAX_ASK_AGE_DAYS) == "stale"


def test_a_quote_of_unknown_age_is_not_treated_as_fresh():
    """Absence of a date is not evidence of freshness. Defaulting the unknown to the best grade
    is the falsy/missing-value trap this codebase keeps rediscovering."""
    assert mark_quality("quote_ask", None) == "stale"


def test_the_intrinsic_floor_and_the_intrinsic_fallback_are_graded_separately():
    """They mean different things. `floored` says a real quote was WRONG (below intrinsic, so
    stale or crossed); `intrinsic` says there was no quote at all. Collapsing them would lose
    the distinction between a bad quote and a missing one."""
    assert mark_quality("intrinsic_quote_below_floor", 0) == "floored"
    assert mark_quality("intrinsic", None) == "intrinsic"
    assert mark_quality("intrinsic", 0) == "intrinsic"


def test_the_grades_that_count_as_approximate():
    """An OTM option marked at intrinsic is marked at ZERO, which is the largest understatement
    of the three and must never read as a precise valuation."""
    for grade in ("stale", "floored", "intrinsic"):
        assert grade in _APPROXIMATE_MARK_QUALITIES
    for grade in ("fresh", "recent"):
        assert grade not in _APPROXIMATE_MARK_QUALITIES


def test_the_freshness_bands_sit_inside_the_archive_bound():
    """A valuation standard looser than the read bound would grade every readable quote fresh
    and mean nothing."""
    assert _MARK_FRESH_MAX_AGE_DAYS < _MARK_RECENT_MAX_AGE_DAYS < _MAX_ASK_AGE_DAYS


# ── The audit's OTM case, where the intrinsic floor cannot help ──────────────

def test_a_stale_but_above_intrinsic_ask_is_still_used_and_still_flagged():
    """The audit's acceptance case. The quote clears the DA-06 floor, so DA-06 accepts it — and
    should. It is four days old, so its TIME VALUE is unknown, which the floor cannot detect."""
    liab, src = short_option_liability(
        strategy="CASH_SECURED_PUT", strike=100.0, underlying_price=80.0,
        contracts=1, quote_ask=25.0)          # intrinsic is 20.0; 25 clears it
    assert src == "quote_ask"
    assert liab == 2_500.0, "the mark itself must not change — R08 grades, it does not revalue"
    assert mark_quality(src, 4) == "stale"


def test_an_out_of_the_money_option_gets_no_protection_from_the_intrinsic_floor():
    """The audit's specific example: a four-day-old $0.01 ask on an OTM put. Intrinsic is ZERO,
    so any non-negative quote clears the floor and the entire mark is stale time value."""
    liab, src = short_option_liability(
        strategy="CASH_SECURED_PUT", strike=90.0, underlying_price=100.0,
        contracts=1, quote_ask=0.01)
    assert src == "quote_ask", "the floor binds nothing on an OTM option"
    assert liab == 1.0
    assert mark_quality(src, 4) in _APPROXIMATE_MARK_QUALITIES, \
        "the only remaining signal that this mark is not current"


def test_a_quote_below_intrinsic_is_floored_and_says_so():
    liab, src = short_option_liability(
        strategy="CASH_SECURED_PUT", strike=100.0, underlying_price=80.0,
        contracts=1, quote_ask=2.0)
    assert src == "intrinsic_quote_below_floor"
    assert liab == 2_000.0
    assert mark_quality(src, 0) == "floored"


# ── The quote's date must survive ────────────────────────────────────────────

def test_the_ask_lookup_returns_the_session_it_was_quoted_in():
    """The query always SELECTed as_of; it was the RETURN that dropped it."""
    import inspect

    from src.services.options_income_engine import _latest_option_ask
    src = inspect.getsource(_latest_option_ask)
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "return None, None" in code, "a missing quote must still return a two-tuple"
    assert "return float(row.nbbo_ask), quote_date" in code


def test_the_snapshot_computes_the_age_against_its_own_date_not_today():
    """A historical snapshot measuring quote age against the wall clock would grade a quote
    that was fresh AT THE TIME as years stale."""
    assert "_age = (as_of - _quote_date).days if _quote_date else None" in _ENGINE


# ── The underlying's own provenance ──────────────────────────────────────────

def test_the_underlying_source_is_recorded_not_inferred():
    import inspect

    from src.services.options_income_engine import _underlying_price_as_of
    code = inspect.getsource(_underlying_price_as_of)
    for source in ('"live"', '"close"', '"entry_fallback"'):
        assert source in code, f"missing underlying source {source}"


def test_a_historical_snapshot_does_not_reach_for_the_live_price():
    """Mixing a past option quote with today's underlying is a mixed-time valuation, and the
    intrinsic floor computed from it is wrong in both directions.

    REWRITTEN 2026-09-28 (pre-deployment audit). This test used to assert that
    `if as_of >= _today_et():` appeared BEFORE `SELECT close FROM prices` in the source — and it
    passed while that SELECT named a column (`prices.symbol`) which does not exist and a
    timeframe label (`'1d'`) which is not in the enum, so the statement raised on every call and
    the historical branch never executed once. It then fell through to the live price anyway,
    which is the exact behaviour the test claimed to forbid.

    A source-text assertion cannot tell a correct query from an unrunnable one. These run the
    real function against a real database instead — see _r06_db_probe.py for why a subprocess."""
    r = _probe("underlying_historical_uses_archived_close")
    assert r["result"] == [90.0, "close"], \
        "a historical snapshot must use the archived close for that date"


def test_a_historical_snapshot_never_looks_ahead_to_a_later_bar():
    """The archive holds a 2026-09-27 close of 130; a snapshot dated 2026-09-22 must not see
    it. Reaching forward is the same defect as reaching for the live price, one bar closer."""
    r = _probe("underlying_historical_uses_archived_close")
    assert r["result"][0] == 90.0 and r["result"][0] != 130.0


def test_a_historical_date_with_no_archived_close_uses_the_entry_price_not_the_live_one():
    """CORRECTED BEHAVIOUR. The original fell through to today's live quote and labelled it
    "live", silently presenting a current valuation as a historical one. The entry price is at
    least drawn from the position's own history, and `entry_fallback` says the mark is not a
    valuation."""
    r = _probe("underlying_historical_without_a_close_never_uses_live")
    assert r["result"] == [111.0, "entry_fallback"]


def test_todays_snapshot_still_prefers_the_live_quote():
    """The guard must not be so broad that the normal daily path stops using the live price."""
    assert _probe("underlying_today_prefers_live")["result"] == [999.0, "live"]


def test_todays_snapshot_falls_back_to_the_archived_close_when_live_is_missing():
    """A missing live quote for one symbol must not drop the whole position to its entry price
    while a perfectly good close exists."""
    assert _probe("underlying_today_without_live_falls_back_to_close")["result"] == [90.0, "close"]


def test_an_unknown_symbol_degrades_to_the_entry_price_rather_than_raising():
    """The caller values every open position in a loop; raising here would lose the entire
    snapshot for one missing stock row — which is how the broken query hid for a whole session."""
    assert _probe("underlying_unknown_symbol_uses_entry")["result"] == [111.0, "entry_fallback"]


def test_the_price_query_names_columns_that_actually_exist():
    """The defect in one assertion. `prices` carries `stock_id` and joins through `stocks`; it
    has no `symbol` column, and the timeframe enum's label is 'D1', not '1d'."""
    import inspect

    from src.services.options_income_engine import _underlying_price_as_of
    # Comments AND the docstring stripped first. The correction comment in that function QUOTES
    # the broken query it replaced, so a raw search reports the defect as still present. Fifth
    # time this session (R04, R05, the T398 ordering test, R01, here) — prose that legitimately
    # names the thing under test is the single most reliable way to break a source assertion.
    code = _executable_only(inspect.getsource(_underlying_price_as_of))
    assert "JOIN stocks s ON s.id = p.stock_id" in code
    assert "s.symbol = :sym" in code
    assert "p.timeframe = 'D1'" in code
    assert "WHERE symbol = :sym" not in code, "the nonexistent column is back"
    assert "timeframe = '1d'" not in code, "the nonexistent enum label is back"


def test_the_entry_price_fallback_is_labelled_rather_than_silent():
    """Falling back to the entry price reports an UNCHANGED position however far the underlying
    has moved. That may be all that is available; it must never look like a valuation."""
    import inspect

    from src.services.options_income_engine import _underlying_price_as_of
    code = inspect.getsource(_underlying_price_as_of)
    assert 'return float(entry_price), "entry_fallback"' in code


# ── Durability: the evidence travels with the row ────────────────────────────

def test_the_evidence_is_persisted_not_only_logged():
    """THE AUDIT'S BAR: "Readers must reconstruct why an equity value was used without
    consulting ephemeral logs.\""""
    assert "mark_evidence=mark_evidence" in _ENGINE, "new rows must carry it"
    assert "existing.mark_evidence = mark_evidence" in _ENGINE, "so must re-written ones"

    models = (pathlib.Path(__file__).resolve().parents[3]
              / "shared" / "db" / "models.py").read_text()
    assert "mark_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)" in models

    session = (pathlib.Path(__file__).resolve().parents[3]
               / "shared" / "db" / "session.py").read_text()
    assert "ADD COLUMN IF NOT EXISTS mark_evidence JSONB" in session, \
        "create_all() only creates MISSING TABLES — a new column needs its own DDL"


def test_the_evidence_records_every_axis_the_audit_named():
    for key in ("mark_sources", "mark_quality", "underlying_sources",
                "worst_quote_age_days", "approximate_marks", "positions_marked",
                "max_ask_age_days"):
        assert f'"{key}"' in _ENGINE, f"mark evidence does not record {key}"


def test_a_portfolio_with_no_open_positions_still_records_an_empty_evidence_block():
    """"Nothing was marked" is a different and more useful statement than a missing column, and
    a NULL here would be indistinguishable from a row predating the feature."""
    block = _ENGINE[_ENGINE.index("mark_evidence: dict = {"):]
    block = block[:block.index("if open_positions:")]
    assert '"positions_marked": 0' in block
    assert '"approximate_marks": 0' in block


def test_the_column_is_nullable_because_older_rows_genuinely_have_no_evidence():
    """A default would manufacture evidence for rows that have none — the same mistake
    equity_basis deliberately avoided by being nullable rather than backfilled from a date."""
    models = (pathlib.Path(__file__).resolve().parents[3]
              / "shared" / "db" / "models.py").read_text()
    decl = models[models.index("mark_evidence: Mapped"):]
    decl = decl[:decl.index("\n")]
    assert "nullable=True" in decl
    assert "default=" not in decl


# ── Against a real row, not a source-text claim ──────────────────────────────
#
# Everything above the line asserts on the policy and on the wiring. These run the REAL
# _snapshot_income_equity_curve() against a REAL database in a subprocess (this service's
# conftest stubs sqlalchemy wholesale — see _r06_db_probe.py) and read the evidence back OFF
# THE PERSISTED ROW, which is the thing the audit actually asked for.

from tests.test_r06_income_concurrency import _probe  # noqa: E402


def test_a_fresh_quote_is_recorded_on_the_row_as_fresh_and_not_approximate():
    ev = _probe("snapshot_fresh_quote")["mark_evidence"]
    assert ev["mark_quality"] == {"fresh": 1}
    assert ev["mark_sources"] == {"quote_ask": 1}
    assert ev["worst_quote_age_days"] == 0
    assert ev["approximate_marks"] == 0


def test_a_four_day_old_quote_is_recorded_as_stale_and_approximate():
    """THE AUDIT'S PROBE, end to end and durable: the same quote that DA-06 accepts, now
    arriving in the equity row with its age attached."""
    ev = _probe("snapshot_stale_quote")["mark_evidence"]
    assert ev["mark_quality"] == {"stale": 1}
    assert ev["worst_quote_age_days"] == 4
    assert ev["approximate_marks"] == 1


def test_grading_a_mark_stale_does_not_change_the_equity_it_produces():
    """R08 grades; it does not revalue. If these diverged, the fix would have silently restated
    the equity curve — a much larger change than the one being made, and not the one asked
    for."""
    fresh = _probe("snapshot_fresh_quote")
    stale = _probe("snapshot_stale_quote")
    assert fresh["equity"] == stale["equity"]


def test_a_position_with_no_quote_at_all_is_recorded_as_intrinsic():
    ev = _probe("snapshot_no_quote")["mark_evidence"]
    assert ev["mark_sources"] == {"intrinsic": 1}
    assert ev["mark_quality"] == {"intrinsic": 1}
    assert ev["worst_quote_age_days"] is None, "no quote means no age, not age zero"
    assert ev["approximate_marks"] == 1


def test_the_underlying_source_lands_on_the_row_too():
    for scenario in ("snapshot_fresh_quote", "snapshot_stale_quote", "snapshot_no_quote"):
        ev = _probe(scenario)["mark_evidence"]
        assert ev["underlying_sources"] == {"live": 1}, scenario


# ── Displayed as approximate ─────────────────────────────────────────────────

def test_the_api_exposes_the_evidence_and_a_plain_approximate_flag():
    """The audit: "Display approximate/stale valuations as such." A chart cannot do that from a
    number alone, and asking every caller to interpret the evidence block invites each one to
    interpret it differently."""
    api = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "api" / "options_income.py").read_text()
    assert '"mark_evidence": c.mark_evidence' in api
    assert '"is_approximate": bool((c.mark_evidence or {}).get("approximate_marks"))' in api
    assert '"equity_is_approximate"' in api, "the headline equity needs the caveat too"


def test_a_row_with_no_evidence_does_not_read_as_a_clean_valuation():
    """NULL means UNKNOWN. `(None or {}).get(...)` is falsy, so is_approximate is False — which
    is correct only because the evidence block itself is also returned and is None. This pins
    that the block is always exposed alongside the flag, so a reader can tell "no approximate
    marks" from "no idea"."""
    api = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "api" / "options_income.py").read_text()
    curve = api[api.index('"equity_basis": c.equity_basis,'):]
    curve = curve[:curve.index("} for c in curve]")]
    assert '"mark_evidence"' in curve and '"is_approximate"' in curve
