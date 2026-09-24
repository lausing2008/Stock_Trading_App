"""AUD-SMARTMONEY: tests for get_smart_money_leaderboard().

WHAT THESE TESTS PROTECT. This endpoint powers a "who should I follow" report, which is the
single easiest kind of report on this platform to make dishonest by accident. Two specific
properties keep it honest, and both are cheap to break in a way that LOOKS like an improvement:

  1. **Entry is the DISCLOSURE date, never the trade date.** Congressional filings lag the trade
     by a median of 40 days. On this platform's own data the same purchases return +4.70% over
     21 days measured from the trade date but +2.89% from disclosure. Swapping the correlated
     price subquery onto `trade_date` would raise every number on the page by roughly 1.8pp and
     look like a win, while advertising a return no reader could ever have reached. Same class
     of error as quoting post-earnings drift that includes the untradeable overnight gap.

  2. **Rows whose feed omits buy/sell must never be counted as followable.** 7,691 of 9,453 rows
     arrive with `transaction_type = 'unknown'`, including all 2,036 of the most active name's.
     A disclosure there may be a SALE. They are surfaced under `direction_unknown` — a deliberate
     choice, since "tracked but not actionable" is more useful than absence — and the risk is
     precisely that a later refactor folds them into the ranked list to make it look fuller.

The sample floor is the third: `sample_is_adequate` is what stops a one-buy +12% print being read
as a track record, and it follows this service's own get_impact_direction_accuracy() convention.

The Python half is exercised behaviourally against a stubbed session. The one structural
assertion below is on SQL SHAPE (which date column the price join correlates on) and pins no
numeric literal — see AUD-T401-SOURCETEXTTESTS for why that distinction matters.
"""
import inspect
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from src.services import congress as C

# The test suite stubs sqlalchemy, so `text()` returns a MagicMock and the executed statement
# carries no readable SQL. The query shape is therefore read from the shipped source of this one
# function — scoped to its body, never the whole module, so an unrelated query elsewhere cannot
# satisfy the assertion by accident.
_SMART_MONEY_SRC = inspect.getsource(C.get_smart_money_leaderboard)


def _row(name, n, avg_pct, pct_up=50.0, party="D", chamber="House",
         latest=date(2026, 8, 1)):
    return SimpleNamespace(
        politician_name=name, party=party, chamber=chamber, n=n,
        avg_pct=avg_pct, pct_up=pct_up, latest_disclosure=latest,
    )


def _unknown(name, n, on_tracked, latest=date(2026, 7, 31)):
    return SimpleNamespace(
        politician_name=name, n=n, on_tracked=on_tracked, latest=latest,
    )


class _FakeSession:
    """Serves the function's queries in the order it issues them: trader rows (.all()), the
    no-direction row COUNT (.scalar()), then the direction_unknown rows (.all()).

    Each queued entry is consumed by whichever accessor the code uses, so adding a query
    without updating this queue fails loudly rather than silently returning the wrong result
    set — which is exactly what happened when the count query was introduced."""

    def __init__(self, trader_rows, unknown_rows, unknown_count=0):
        self._queue = [trader_rows, unknown_count, unknown_rows]
        self.params_seen = []

    def execute(self, sql, params=None):
        self.params_seen.append(params or {})
        value = self._queue.pop(0)
        return SimpleNamespace(all=lambda: value, scalar=lambda: value)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(trader_rows, unknown_rows=(), unknown_count=0, **kwargs):
    sess = _FakeSession(list(trader_rows), list(unknown_rows), unknown_count)
    with patch.object(C, "SessionLocal", lambda: sess):
        return C.get_smart_money_leaderboard(**kwargs), sess


# ── The sample floor ──────────────────────────────────────────────────────────

def test_trader_at_the_floor_is_adequate_and_one_below_is_not():
    result, _ = _run([_row("At Floor", 8, 5.0), _row("Below", 7, 99.0)], min_trades=8)
    by_name = {t["name"]: t for t in result["traders"]}
    assert by_name["At Floor"]["sample_is_adequate"] is True
    assert by_name["Below"]["sample_is_adequate"] is False


def test_a_spectacular_return_on_a_tiny_sample_is_reported_but_not_counted_as_followable():
    """The exact failure this floor exists for: one buy, +99%, top of the sort order."""
    result, _ = _run([_row("Lucky", 1, 99.0), _row("Real", 20, 3.0)], min_trades=8)
    assert result["n_followable"] == 1
    names_followable = [t["name"] for t in result["traders"] if t["sample_is_adequate"]]
    assert names_followable == ["Real"]
    # ...but it is still VISIBLE, so a reader can see the leaders sit among small samples.
    assert "Lucky" in [t["name"] for t in result["traders"]]


def test_n_followable_counts_only_adequate_rows_not_all_returned_rows():
    rows = [_row(f"P{i}", 2, 1.0) for i in range(10)] + [_row("Solid", 12, 1.0)]
    result, _ = _run(rows, min_trades=8)
    assert len(result["traders"]) == 11
    assert result["n_followable"] == 1


def test_a_caller_supplied_floor_is_what_decides_adequacy():
    rows = [_row("Mid", 5, 1.0)]
    assert _run(rows, min_trades=8)[0]["traders"][0]["sample_is_adequate"] is False
    assert _run(rows, min_trades=4)[0]["traders"][0]["sample_is_adequate"] is True


def test_the_floor_actually_used_is_reported_back_so_the_ui_cannot_state_a_different_one():
    result, _ = _run([_row("A", 9, 1.0)], min_trades=9)
    assert result["min_trades_for_adequacy"] == 9


# ── direction_unknown must never leak into the ranking ────────────────────────

def test_direction_unknown_names_are_surfaced_but_never_appear_among_traders():
    """A name whose feed omits buy/sell cannot be followed at all — its disclosure may be a
    SALE. It is listed so the reader knows the person is tracked, and must stay out of both
    `traders` and `n_followable`."""
    result, _ = _run(
        [_row("Followable", 10, 4.0)],
        [_unknown("Donald J Trump", 2036, 248)],
    )
    assert [t["name"] for t in result["traders"]] == ["Followable"]
    assert result["n_followable"] == 1
    assert result["direction_unknown"][0]["name"] == "Donald J Trump"
    assert result["direction_unknown"][0]["n_records"] == 2036
    assert result["direction_unknown"][0]["on_tracked_stock"] == 248


def test_a_direction_unknown_entry_carries_no_return_field_at_all():
    """There is no honest return to show for a row of unknown direction, so the shape must not
    offer a slot a UI could fill with a zero or a dash that reads as 'flat'."""
    result, _ = _run([], [_unknown("Unknown Person", 100, 10)])
    entry = result["direction_unknown"][0]
    assert set(entry) == {"name", "n_records", "on_tracked_stock", "latest_trade"}


def test_direction_unknown_is_empty_rather_than_absent_when_every_feed_has_direction():
    result, _ = _run([_row("A", 10, 1.0)], [])
    assert result["direction_unknown"] == []


# ── Entry basis: the property that decides whether the numbers are reachable ──

def test_entry_basis_is_declared_as_the_disclosure_date():
    result, _ = _run([_row("A", 10, 1.0)])
    assert result["entry_basis"] == "disclosure_date"


def test_the_price_join_correlates_on_disclosure_date_and_never_on_trade_date():
    """AUD-T401-SOURCETEXTTESTS: a SQL-SHAPE assertion pinning no numeric literal. Which date
    column the price join keys on is not a tunable threshold — it is the difference between
    +3.73% (unreachable, measured from the trade) and +3.15% (what a reader could actually have
    captured), and a swap would silently inflate the whole report.

    This test EARNED ITS KEEP during AUD-SMARTMONEY-PERF: rewriting the correlated subqueries
    into a DISTINCT ON join changed the predicate's alias, and this assertion went red rather
    than letting an unverified join shape ship. Written against the join as it now stands; the
    negative assertion is the half that actually guards the property, and it is alias-agnostic
    on purpose."""
    assert "p.d >= cd.disclosure_date" in _SMART_MONEY_SRC
    # Whatever the aliasing, no price join in this query may key on the trade date.
    assert ">= c.trade_date" not in _SMART_MONEY_SRC
    assert ">= cd.trade_date" not in _SMART_MONEY_SRC


def test_only_purchases_are_averaged_into_a_follow_return():
    """A disclosed SALE is a different decision; averaging it in would measure something with
    no interpretation. Shape assertion, no literal pinned."""
    assert "c.transaction_type ILIKE '%purchase%'" in _SMART_MONEY_SRC


def test_the_horizon_reported_is_the_horizon_bound_into_the_query():
    """The number on the column header must be the one the LEAD() window actually used —
    reporting a 21-day header over a 5-day measurement is a silent, unfalsifiable lie."""
    _, sess = _run([_row("A", 10, 1.0)], [_unknown("U", 1, 0)])
    result, _ = _run([_row("A", 10, 1.0)])
    assert result["horizon_days"] == C._SMART_MONEY_HORIZON_BARS
    assert sess.params_seen[0]["horizon"] == result["horizon_days"]


# ── Serialisation ─────────────────────────────────────────────────────────────

def test_a_losing_trader_is_returned_rather_than_filtered_out():
    """A leaderboard showing only winners hides the spread, and the spread is what tells a
    reader whether the top is skill or the tail of a small sample."""
    result, _ = _run([_row("Winner", 23, 5.26), _row("Loser", 8, -5.18, pct_up=12.0)])
    assert [t["name"] for t in result["traders"]] == ["Winner", "Loser"]
    assert result["traders"][1]["avg_21d_pct"] == -5.18
    assert result["traders"][1]["sample_is_adequate"] is True


def test_blank_party_and_chamber_become_null_rather_than_an_empty_string():
    result, _ = _run([_row("A", 10, 1.0, party="", chamber="")])
    assert result["traders"][0]["party"] is None
    assert result["traders"][0]["chamber"] is None


def test_dates_are_serialised_as_iso_strings_and_a_missing_one_stays_null():
    result, _ = _run(
        [_row("A", 10, 1.0, latest=date(2026, 8, 7)), _row("B", 10, 1.0, latest=None)],
        [_unknown("U", 1, 0, latest=None)],
    )
    assert result["traders"][0]["latest_disclosure"] == "2026-08-07"
    assert result["traders"][1]["latest_disclosure"] is None
    assert result["direction_unknown"][0]["latest_trade"] is None


def test_an_empty_dataset_returns_the_full_shape_rather_than_a_bare_empty_list():
    """A UI that gets `[]` cannot tell 'nobody qualifies' from 'the query broke'."""
    result, _ = _run([], [])
    assert result["traders"] == []
    assert result["n_followable"] == 0
    assert result["entry_basis"] == "disclosure_date"
    assert result["caveats"]


def test_the_disclosure_lag_caveat_is_always_present_even_with_no_data():
    """This is the one caveat a reader must not be able to miss, so it cannot be conditional
    on there being rows to qualify."""
    result, _ = _run([], [])
    joined = " ".join(result["caveats"]).lower()
    assert "disclosure" in joined
    assert "trade date" in joined


def test_the_no_direction_row_count_in_the_caveats_is_measured_not_hardcoded():
    """The caveat used to state "7,691 of 9,453 rows" as literal text. After the field-mapping
    repair that figure became 776, and a hardcoded string would have gone on asserting the old
    one indefinitely — a number in user-facing copy that no longer describes the data."""
    result, _ = _run([_row("A", 10, 1.0)], [_unknown("U", 5, 1)], unknown_count=776)
    assert any("776" in c for c in result["caveats"])
    assert not any("7,691" in c for c in result["caveats"])
