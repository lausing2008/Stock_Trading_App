"""AUD-OBS-MIDNIGHTANCHOR — the session walk must not return weekends.

`is_trading_day` resolves an instant into the venue's local calendar, and a naive midnight
lands on the previous local day once converted. Walking back from midnight shifted the whole
20-session window by one and returned Saturdays as sessions: for a 2026-10-08 anchor it asked
for 2026-09-12, 09-19, 09-26 and 10-03 — all weekends — while the four real sessions were
absent, so `assess` saw a date mismatch and reported the technical bucket as not_collected.
Found by diffing the requested dates against the stored bars, not by a test.
"""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

import importlib.util
_spec = importlib.util.spec_from_file_location(
    "obs_routes_cal", Path(__file__).resolve().parents[3] / "shared/common/market_calendar.py")
_cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cal)


from intel_reports.evidence_buckets import sessions_back as _real  # noqa: E402


def _sessions_back(venue, anchor, n):
    """THE REAL FUNCTION, imported — not a copy.

    An earlier version of this file re-implemented it, because it lived in the route module
    which the service conftest makes unimportable. That copy then could not catch an edit which
    deleted `out = []` from the original: every test here passed while the deployed endpoint
    raised NameError on its first call.
    """
    return _real(venue, anchor, n, is_trading_day=_cal.is_trading_day)


def test_no_session_falls_on_a_weekend():
    for anchor in (datetime(2026, 10, 8), datetime(2026, 10, 8, 23, 59),
                   datetime(2026, 6, 1), datetime(2026, 1, 5)):
        for d in _sessions_back("US", anchor, 21):
            assert date.fromisoformat(d).weekday() < 5, f"{d} from anchor {anchor} is a weekend"


def test_the_window_is_the_requested_length_and_strictly_before_the_anchor():
    anchor = datetime(2026, 10, 8)
    out = _sessions_back("US", anchor, 21)
    assert len(out) == 21 and out == sorted(out)
    assert date.fromisoformat(out[-1]) < anchor.date()


def test_a_midnight_anchor_gives_the_same_window_as_a_midday_one():
    """The defect: these disagreed, and midnight was the one in the route."""
    assert (_sessions_back("US", datetime(2026, 10, 8, 0, 0), 21)
            == _sessions_back("US", datetime(2026, 10, 8, 12, 0), 21))


def test_hk_sessions_are_also_weekdays():
    """`is_hk_trading_day` calls .astimezone() on whatever it is given, and a NAIVE datetime is
    interpreted as HOST LOCAL TIME. On the UTC-8 machine this was written on, naive Sunday
    12:00 resolved to Monday in Hong Kong and weekends came back as sessions. Passing an aware
    UTC instant makes the answer independent of the machine."""
    for d in _sessions_back("HK", datetime(2026, 10, 8), 21):
        assert date.fromisoformat(d).weekday() < 5, d


def test_the_window_does_not_depend_on_the_host_timezone():
    import os, time
    before = _sessions_back("HK", datetime(2026, 10, 8), 21)
    old = os.environ.get("TZ")
    try:
        for tz in ("UTC", "America/Los_Angeles", "Asia/Hong_Kong"):
            os.environ["TZ"] = tz
            time.tzset()
            assert _sessions_back("HK", datetime(2026, 10, 8), 21) == before, tz
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


# ---- AUD-OBS-ADDCOLUMN ----------------------------------------------------------------------
# create_all() creates MISSING TABLES, never missing columns. `observation_outcomes` was created
# by an earlier deploy, so three columns added to the model afterwards never reached the live
# table and every SELECT raised UndefinedColumn. The model and the migration must stay in step.

def test_every_outcome_column_added_after_creation_has_a_migration():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    models = (root / "shared/db/models.py").read_text()
    session_py = (root / "shared/db/session.py").read_text()
    block = models[models.index("class ObservationOutcome"):]
    block = block[:block.index("__table_args__")]
    import re
    cols = set(re.findall(r"^\s{4}(\w+):\s*Mapped", block, re.M))
    # These three were added after the table existed in production.
    for late in ("benchmark_entry_return", "attempts", "superseded_state",
                 "resolver_fingerprint", "superseded_by_id"):
        assert late in cols, f"{late} missing from the model"
        assert f"ADD COLUMN IF NOT EXISTS {late}" in session_py, \
            f"{late} is on the model but has no ALTER — create_all() will not add it"


def test_the_outcome_unique_constraint_change_is_migrated_not_only_declared():
    """`create_all()` does not alter an EXISTING table's constraints either. Without the swap
    the corrected-resolver insert collides with the old two-column constraint in production
    while passing every test, because the test database is created fresh from the model."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[3]
    models = (root / "shared/db/models.py").read_text()
    session_py = (root / "shared/db/session.py").read_text()
    assert 'name="uq_outcome_observation_horizon_resolver"' in models
    assert "DROP CONSTRAINT IF EXISTS uq_outcome_observation_horizon" in session_py
    assert "uq_outcome_observation_horizon_resolver" in session_py
    # Pre-existing rows must be labelled, not left NULL: a NULL would make the unique index
    # non-enforcing for exactly the rows a correction is about to supersede.
    assert "unrecorded-pre-fingerprint" in session_py


# =============================================================================================
# AUD-OBS-SESSIONBOUNDS / AUD-OBS-ADJWINDOW / AUD-OBS-CONTRACT (2026-10-08)
#
# Three defects that each decided whether an outcome figure is publishable:
#   1. the walk admitted a date once 12:00 UTC had passed — 08:00 ET, BEFORE the US open — and
#      always started on the next CALENDAR day, so a midnight cutoff silently skipped a whole
#      session and imposed an unstated one-session delayed entry;
#   2. the caller asserted `adjustment_consistent=True` from a check over the SETUP window,
#      which says nothing about the outcome window it is used for;
#   3. the fingerprint covered `resolve` and two constants, so correcting either of the above
#      could leave it unchanged — and immutable resolved rows would again block the correction.
# =============================================================================================
import sys as _sys
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path as _P

_ROOT = _P(__file__).resolve().parents[3]
for _p in (str(_ROOT / "shared"), str(_ROOT / "services" / "research-engine" / "src")):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

# The service conftest stubs `common` as a MagicMock, so `common.market_calendar` cannot be
# imported here — the module is loaded from its own file, exactly as the direction-screen
# adapter test does. `_cal` above is that same module, already loaded at the top of this file.
_itd, _sb = _cal.is_trading_day, _cal.session_bounds
from intel_reports import evidence_buckets as _EB  # noqa: E402


# ---- DEMONSTRATION 1: before the US close, today's session is excluded ----------------------

def test_before_the_us_close_todays_session_is_excluded():
    """2026-06-01 opens 13:30 UTC and closes 20:00 UTC.

    The old rule admitted it at 12:00 UTC — ninety minutes before the bell, when the daily bar
    did not exist at all — and kept admitting it right through the session while the bar was
    still forming.
    """
    anchor = _dt(2026, 5, 29, 21, tzinfo=_tz.utc)  # after Friday's close
    for hhmm, expect_june1 in ((11, False), (13, False), (19, False), (20, True), (23, True)):
        got = _EB.sessions_forward("US", anchor, 1, is_trading_day=_itd,
                                   not_after=_dt(2026, 6, 1, hhmm, tzinfo=_tz.utc),
                                   session_bounds=_sb)
        assert (got == ["2026-06-01"]) is expect_june1, (
            f"at {hhmm:02d}:00 UTC on 2026-06-01 the session is "
            f"{'complete' if expect_june1 else 'not complete'}; got {got}")


def test_an_incomplete_session_truncates_rather_than_reaching_past_it():
    """Nothing later can be complete either, so the window must END, not skip the gap."""
    got = _EB.sessions_forward("US", _dt(2026, 5, 29, 21, tzinfo=_tz.utc), 5,
                               is_trading_day=_itd,
                               not_after=_dt(2026, 6, 3, 15, tzinfo=_tz.utc), session_bounds=_sb)
    assert got == ["2026-06-01", "2026-06-02"], got


def test_sessions_back_requires_the_close_to_have_happened():
    """The reference price is a COMPLETED close. Mid-session on 2026-06-01, the latest
    completed US session is 2026-05-29 — not 2026-06-01, whose bar is still forming."""
    mid = _dt(2026, 6, 1, 17, tzinfo=_tz.utc)
    assert _EB.sessions_back("US", mid, 1, is_trading_day=_itd, session_bounds=_sb) \
        == ["2026-05-29"]
    after = _dt(2026, 6, 1, 20, tzinfo=_tz.utc)
    assert _EB.sessions_back("US", after, 1, is_trading_day=_itd, session_bounds=_sb) \
        == ["2026-06-01"]


# ---- DEMONSTRATION 2: the first eligible session follows the recorded convention ------------

def test_a_midnight_cutoff_does_not_skip_that_days_session():
    """THE DEFECT, stated as a case. An observation formed at 00:00 on 2026-06-01 could have
    been acted on in 2026-06-01's session, which opens 13.5 hours later. The old walk started
    at the next CALENDAR day and returned 2026-06-02, a delayed entry nobody had declared."""
    got = _EB.sessions_forward("US", _dt(2026, 6, 1, 0, 0), 2, is_trading_day=_itd,
                               not_after=_dt(2026, 7, 1), session_bounds=_sb)
    assert got[0] == "2026-06-01", f"the cutoff's own session was skipped: {got}"
    assert got == ["2026-06-01", "2026-06-02"]


def test_a_cutoff_inside_a_session_excludes_that_session():
    """The other half of the same rule: a session already trading when the observation was
    formed could not have been entered from its open, so it is not the first eligible one."""
    got = _EB.sessions_forward("US", _dt(2026, 6, 1, 15, 0), 1, is_trading_day=_itd,
                               not_after=_dt(2026, 7, 1), session_bounds=_sb)
    assert got == ["2026-06-02"], got


def test_eligibility_is_decided_by_the_open_not_the_calendar_date():
    """A cutoff one minute before the bell still admits that session; one minute after does
    not. A calendar-day rule cannot express this, which is why it got the convention wrong."""
    before = _EB.sessions_forward("US", _dt(2026, 6, 1, 13, 29), 1, is_trading_day=_itd,
                                  not_after=_dt(2026, 7, 1), session_bounds=_sb)
    after = _EB.sessions_forward("US", _dt(2026, 6, 1, 13, 31), 1, is_trading_day=_itd,
                                 not_after=_dt(2026, 7, 1), session_bounds=_sb)
    assert before == ["2026-06-01"] and after == ["2026-06-02"], (before, after)


# ---- DEMONSTRATION 3: a corporate action in the window requires adjustment evidence ---------

def _bars(pairs):
    return [{"date": d, "close": c, "adj_close": a} for d, c, a in pairs]


_W = ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"]


def _bars(pairs):
    return [{"date": d, "close": c, "adj_close": a} for d, c, a in pairs]


def _cov(source="yfinance", method=None, frm="2026-01-01", to="2026-12-31"):
    return {"source": source, "method": method or _EB.ADJUSTMENT_METHOD,
            "covers_from": frm, "covers_to": to, "retrieved_at": "2026-10-08T00:00:00"}


def _act(kind, ex, **kw):
    return {"action_type": kind, "ex_date": ex, "source": "yfinance",
            "retrieved_at": "2026-10-08T00:00:00", **kw}


# ---- ACCEPTANCE 1: A SPLIT ------------------------------------------------------------------

def test_a_split_is_handled_not_classified_unresolvable():
    """A 2-for-1 on 06-03 halves the quoted close. With a sourced record the window RESOLVES on
    a split-adjusted basis — ordinary corporate actions must not be permanently unresolvable."""
    got = _EB.adjustment_evidence(
        {"stock": _bars([("2026-06-01", 100.0, None), ("2026-06-02", 102.0, None),
                         ("2026-06-03", 51.5, None), ("2026-06-04", 52.0, None)])},
        basis=_EB.SPLIT_ADJUSTED_PRICE,
        actions={"stock": [_act("split", "2026-06-03", split_ratio=2.0)]},
        coverage={"stock": _cov()})
    assert got["consistent"] is True, got["reason"]
    f = got["factors"]["stock"]
    assert f["2026-06-04"] == 1.0 and f["2026-06-03"] == 1.0, "post-split sessions are the basis"
    assert f["2026-06-02"] == 0.5 and f["2026-06-01"] == 0.5, "pre-split closes are halved"
    # The whole point: on this basis the move is +2%, not the -49% a raw close would show.
    adj = _EB.apply_adjustment({"2026-06-01": 100.0, "2026-06-04": 52.0}, f)
    assert round(adj["2026-06-04"] / adj["2026-06-01"] - 1, 4) == 0.04
    assert got["evidence"]["stock"]["verified_by"] == "sourced_action_history"


def test_a_split_without_a_recorded_ratio_is_unverified_not_guessed():
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W])}, basis=_EB.SPLIT_ADJUSTED_PRICE,
        actions={"stock": [_act("split", "2026-06-03")]}, coverage={"stock": _cov()})
    assert got["consistent"] is None and "no recorded ratio" in got["reason"]


# ---- ACCEPTANCE 2: A DIVIDEND ---------------------------------------------------------------

def test_a_cash_dividend_does_not_block_a_price_return_but_is_disclosed():
    """A distribution is EXCLUDED BY DEFINITION from a split-adjusted price return. Blocking on
    it would make every dividend payer permanently unresolvable; ignoring it silently would
    understate the holder's outcome. It resolves, and says what it left out."""
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W])}, basis=_EB.SPLIT_ADJUSTED_PRICE,
        actions={"stock": [_act("cash_dividend", "2026-06-03", cash_amount=0.75,
                                currency="USD")]},
        coverage={"stock": _cov()})
    assert got["consistent"] is True
    assert got["factors"]["stock"]["2026-06-01"] == 1.0, "a dividend changes no share count"
    assert any("EXCLUDED" in d and "0.7500" in d for d in got["disclosures"]), got["disclosures"]


def test_the_same_dividend_is_included_under_total_return():
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W])}, basis=_EB.TOTAL_RETURN,
        actions={"stock": [_act("cash_dividend", "2026-06-03", cash_amount=0.75)]},
        coverage={"stock": _cov()})
    assert got["consistent"] is True
    assert any("included at the recorded cash amount" in d for d in got["disclosures"])


def test_a_total_return_without_the_amount_is_unverified():
    """The two bases are not interchangeable: the SAME evidence supports one and not the other."""
    acts = {"stock": [_act("cash_dividend", "2026-06-03")]}
    series = {"stock": _bars([(d, 100.0, None) for d in _W])}
    assert _EB.adjustment_evidence(series, basis=_EB.SPLIT_ADJUSTED_PRICE, actions=acts,
                                   coverage={"stock": _cov()})["consistent"] is True
    tot = _EB.adjustment_evidence(series, basis=_EB.TOTAL_RETURN, actions=acts,
                                  coverage={"stock": _cov()})
    assert tot["consistent"] is None and "no recorded amount" in tot["reason"]


def test_raw_price_is_refused_whenever_any_action_occurred():
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W])}, basis=_EB.RAW_PRICE,
        actions={"stock": [_act("cash_dividend", "2026-06-03", cash_amount=0.75)]},
        coverage={"stock": _cov()})
    assert got["consistent"] is None and "do not share one basis" in got["reason"]


# ---- ACCEPTANCE 3: A BENCHMARK-ONLY ACTION --------------------------------------------------

def test_a_benchmark_only_action_is_adjusted_on_the_benchmark_alone():
    """An excess return built on a sound stock basis and an unsound benchmark basis is still
    wrong. The adjustment must land on the benchmark and must NOT touch the stock."""
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W]),
         "benchmark": _bars([("2026-06-01", 400.0, None), ("2026-06-02", 404.0, None),
                             ("2026-06-03", 101.0, None), ("2026-06-04", 102.0, None)])},
        basis=_EB.SPLIT_ADJUSTED_PRICE,
        actions={"benchmark": [_act("split", "2026-06-03", split_ratio=4.0)]},
        coverage={"stock": _cov(), "benchmark": _cov()})
    assert got["consistent"] is True
    assert got["factors"]["benchmark"]["2026-06-01"] == 0.25
    assert got["factors"]["stock"]["2026-06-01"] == 1.0, "the stock must not be restated"
    assert [a["instrument"] for a in got["actions"]] == ["benchmark"]


def test_an_unverifiable_benchmark_blocks_the_window_even_if_the_stock_is_clean():
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W]),
         "benchmark": _bars([(d, 400.0, None) for d in _W])},
        basis=_EB.SPLIT_ADJUSTED_PRICE,
        actions={"stock": []}, coverage={"stock": _cov()})  # benchmark: no coverage, no adj
    assert got["consistent"] is None
    assert "benchmark" in got["reason"]


# ---- ACCEPTANCE 4: MISSING EVIDENCE ---------------------------------------------------------

def test_missing_adj_close_with_no_action_history_is_unverified():
    """The MU case exactly: no adj_close and nobody has collected the actions."""
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W])}, basis=_EB.SPLIT_ADJUSTED_PRICE)
    assert got["consistent"] is None
    assert "no corporate-action history covers this window" in got["reason"]
    assert "no adj_close" in got["reason"]


def test_missing_adj_close_is_NOT_unusable_when_a_sourced_history_covers_it():
    """REQUIREMENT 1. The absence of a provider column must not be the end of the question."""
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W])}, basis=_EB.SPLIT_ADJUSTED_PRICE,
        actions={"stock": []}, coverage={"stock": _cov()})
    assert got["consistent"] is True, got["reason"]
    assert got["evidence"]["stock"]["verified_by"] == "sourced_action_history"
    assert "no corporate action in window" in got["reason"]


def test_an_action_history_that_does_not_span_the_window_does_not_cover_it():
    """Partial coverage is not coverage: an action could sit in the uncovered part."""
    got = _EB.adjustment_evidence(
        {"stock": _bars([(d, 100.0, None) for d in _W])}, basis=_EB.SPLIT_ADJUSTED_PRICE,
        actions={"stock": []}, coverage={"stock": _cov(frm="2026-06-02", to="2026-12-31")})
    assert got["consistent"] is None


def test_a_flat_provider_factor_establishes_only_that_nothing_happened():
    """It is used for the one thing it can honestly support, and labelled as such."""
    got = _EB.adjustment_evidence(
        {"stock": _bars([("2026-06-01", 100.0, 95.0), ("2026-06-02", 102.0, 96.9),
                         ("2026-06-03", 104.0, 98.8), ("2026-06-04", 106.0, 100.7)])},
        basis=_EB.SPLIT_ADJUSTED_PRICE)
    assert got["consistent"] is True
    assert got["evidence"]["stock"]["verified_by"] == "provider_adjustment_factor"
    assert "does not describe the provider's methodology" in got["evidence"]["stock"]["note"]


def test_a_moving_provider_factor_cannot_be_decomposed_into_a_basis():
    """REQUIREMENT 2. A split and a dividend move the blended factor the same way, so the
    factor alone can never say which basis it supports."""
    got = _EB.adjustment_evidence(
        {"stock": _bars([("2026-06-01", 100.0, 95.0), ("2026-06-02", 102.0, 96.9),
                         ("2026-06-03", 51.5, 48.9), ("2026-06-04", 52.0, 52.0)])},
        basis=_EB.SPLIT_ADJUSTED_PRICE)
    assert got["consistent"] is None
    assert "cannot be decomposed" in got["reason"]


def test_the_three_bases_are_distinct_and_a_provider_close_is_not_one_of_them():
    assert len(set(_EB.RETURN_BASES)) == 3
    assert "adj_close" not in _EB.RETURN_BASES and "adjusted_close" not in _EB.RETURN_BASES


def test_the_checked_window_includes_the_reference_session():
    """An action between the reference close and the first measured session corrupts the
    descriptive return just as badly as one in the middle, so the route prepends it."""
    import pathlib as _pl
    src = (_ROOT / "services/research-engine/src/api/observation_routes.py").read_text()
    seg = src[src.index("window = ("):src.index("adj = EB.adjustment_evidence")]
    assert 'body["latest_session"]' in seg and "+ fwd" in seg
    assert "_adjustment_bars(session, bench.id, window)" in src, \
        "the benchmark must be checked over the same window"


def test_the_route_no_longer_asserts_consistency_from_the_setup_window():
    """COMMENTS STRIPPED FIRST. The first version of this check matched the explanatory comment
    that QUOTES the removed `adjustment_consistent=True` — a test passing on its own prose, a
    trap this codebase has sprung several times before."""
    import ast
    src = (_ROOT / "services/research-engine/src/api/observation_routes.py").read_text()
    code = ast.unparse(ast.parse(src))
    assert "adjustment_consistent=True" not in code, \
        "the caller must not assert what it has not verified over the OUTCOME window"
    assert "adjustment_consistent=adj['consistent']" in code


# ---- DEMONSTRATION 4: the contract covers every outcome-changing dependency -----------------

import pytest  # noqa: E402


@pytest.fixture()
def real_calendar(monkeypatch):
    """`outcome_contract()` reads the calendar through `common`, which the service conftest
    stubs as a MagicMock — `inspect.getsource` on a mock attribute raises. Point the stub at
    the module already loaded from its own file at the top of this test file."""
    import sys as s_
    stub = s_.modules.get("common")
    if stub is not None:
        monkeypatch.setattr(stub, "market_calendar", _cal, raising=False)
    monkeypatch.setitem(s_.modules, "common.market_calendar", _cal)
    return _cal


def test_the_contract_covers_every_outcome_changing_dependency(real_calendar):
    """ADDING A DEPENDENCY TO THE RESOLUTION PATH MEANS ADDING IT HERE.

    The first fingerprint hashed `resolve` and two constants. Session selection, price
    selection and adjustment verification all change resolved figures, so a correction to any
    of them could land with the fingerprint unchanged — and an immutable resolved row would
    then block the corrected figures from ever being written.
    """
    from intel_reports.observations import outcome_contract
    got = outcome_contract()
    for key in ("policy", "execution", "resolve", "sessions_forward", "sessions_back",
                "session_bounds", "session_hours", "holidays", "closes_by_date",
                "adjustment_evidence", "adjustment_tolerance"):
        assert key in got, f"{key} can change an outcome figure and is outside the contract"


def test_changing_any_contributing_function_moves_the_fingerprint(real_calendar):
    """Each dependency is checked individually: a contract that merely CONTAINS a key but does
    not digest its behaviour would pass the test above and fail at its job."""
    import copy
    from intel_reports.observations import outcome_contract
    from intel_reports.evidence_buckets import digest
    base = outcome_contract()
    for key in base:
        mutated = copy.deepcopy(base)
        mutated[key] = str(mutated[key]) + "  # changed"
        assert digest(mutated) != digest(base), f"{key} is carried but not digested"


def test_the_return_basis_column_fits_every_declared_basis():
    """It did not. `return_basis` was VARCHAR(16) and `split_adjusted_price` is 20 characters,
    so the first pilot resolve computed a correct outcome and then raised
    StringDataRightTruncation on INSERT — a failure mode no unit test sees, because the test
    database is built from the model and the value only overflows once a basis is long enough.
    """
    import re
    models = (_ROOT / "shared/db/models.py").read_text()
    block = models[models.index("class ObservationOutcome"):]
    m = re.search(r"return_basis:.*?String\((\d+)\)", block, re.S)
    assert m, "return_basis column not found"
    longest = max(len(b) for b in _EB.RETURN_BASES)
    assert int(m.group(1)) >= longest, (
        f"the longest declared basis is {longest} characters and the column holds "
        f"{m.group(1)}")
    session_py = (_ROOT / "shared/db/session.py").read_text()
    assert "ALTER COLUMN return_basis TYPE" in session_py, \
        "widening a column on an existing table needs a migration, like adding one"
