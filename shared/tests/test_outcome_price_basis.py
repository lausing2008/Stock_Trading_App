from datetime import date, datetime
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parents[1] / "common/outcome_price_basis.py"
_spec = importlib.util.spec_from_file_location("outcome_price_basis_under_test", _path)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
resolve_window = _module.resolve_window
VERIFIED, PROVISIONAL, UNVERIFIED = _module.VERIFIED, _module.PROVISIONAL, _module.UNVERIFIED
classify_observation_window = _module.classify_observation_window
UNADJUSTED_NO_KNOWN_ACTION, KNOWN_CORRUPT = _module.UNADJUSTED_NO_KNOWN_ACTION, _module.KNOWN_CORRUPT
ADJUSTED = _module.ADJUSTED


def _coverage(kind="source_guarantee"):
    return {"evidenced_from": date(2026, 1, 1), "evidenced_to": date(2026, 12, 31),
            "completeness_basis": kind, "retrieved_at": datetime(2026, 12, 31)}


def test_split_is_applied_to_pre_split_prices_only():
    bars = [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 52.0)]
    actions = [{"action_type": "split", "ex_date": date(2026, 1, 5), "split_ratio": 2.0}]
    got = resolve_window(bars, actions, _coverage(), start=bars[0][0], end=bars[-1][0])
    assert got["prices"] == [(date(2026, 1, 2), 50.0), (date(2026, 1, 5), 52.0)]
    assert got["evidence_status"] == VERIFIED


def test_response_only_computes_but_is_provisional():
    bars = [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 101.0)]
    got = resolve_window(bars, [], _coverage("response_only"),
                         start=bars[0][0], end=bars[-1][0])
    assert got["prices"] == bars and got["evidence_status"] == PROVISIONAL
    assert "no documented completeness guarantee" in got["reason"]


def test_missing_span_evidence_is_unverified_and_produces_no_adjusted_prices():
    bars = [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 101.0)]
    got = resolve_window(bars, [], None, start=bars[0][0], end=bars[-1][0])
    assert got["prices"] == [] and got["evidence_status"] == UNVERIFIED


def test_a_share_action_without_ratio_never_becomes_a_clean_return():
    bars = [(date(2026, 1, 2), 100.0), (date(2026, 1, 5), 52.0)]
    actions = [{"action_type": "split", "ex_date": date(2026, 1, 5), "split_ratio": None}]
    got = resolve_window(bars, actions, _coverage(), start=bars[0][0], end=bars[-1][0])
    assert got["prices"] == [] and got["evidence_status"] == UNVERIFIED


# ── freshness is not completeness ────────────────────────────────────────────
#
# These are two different limits on the SAME result, with two different remedies, and the
# distinction is easy to lose because both land on "not verified". Re-fetching fixes the first
# and cannot touch the second. Pinned here so a refresh campaign can never be mistaken for a
# path to `verified`.

best_achievable_status = _module.best_achievable_status


def test_a_stale_span_is_reported_as_a_FRESHNESS_limit():
    """The source was simply never asked about these dates. Re-fetching fixes it."""
    bars = [(date(2026, 6, 1), 100.0), (date(2026, 6, 2), 98.0)]
    narrow = {"evidenced_from": date(2026, 1, 1), "evidenced_to": date(2026, 5, 1),
              "completeness_basis": "source_guarantee", "retrieved_at": datetime(2026, 5, 1)}
    out = resolve_window(bars, [], narrow, start=date(2026, 6, 1), end=date(2026, 6, 2))
    assert out["evidence_status"] == UNVERIFIED
    assert out["limiting_factor"] == "coverage_span"


def test_a_missing_guarantee_is_reported_as_a_COMPLETENESS_limit():
    """The span IS covered. Re-fetching changes nothing: the source still does not promise its
    list is exhaustive, so the result is provisional and stays provisional."""
    bars = [(date(2026, 6, 1), 100.0), (date(2026, 6, 2), 98.0)]
    out = resolve_window(bars, [], _coverage("response_only"),
                         start=date(2026, 6, 1), end=date(2026, 6, 2))
    assert out["evidence_status"] == PROVISIONAL
    assert out["limiting_factor"] == "source_completeness"


def test_the_two_limits_are_never_reported_as_the_same_thing():
    """The guard against the conflation itself: a stale span and a missing guarantee must not
    collapse onto one factor, because only one of them is worth sending a refresh job for."""
    bars = [(date(2026, 6, 1), 100.0), (date(2026, 6, 2), 98.0)]
    stale = resolve_window(bars, [], {"evidenced_from": date(2026, 1, 1),
                                      "evidenced_to": date(2026, 5, 1),
                                      "completeness_basis": "response_only",
                                      "retrieved_at": datetime(2026, 5, 1)},
                           start=date(2026, 6, 1), end=date(2026, 6, 2))
    covered = resolve_window(bars, [], _coverage("response_only"),
                             start=date(2026, 6, 1), end=date(2026, 6, 2))
    assert stale["limiting_factor"] != covered["limiting_factor"]


def test_an_unappliable_action_is_its_own_limit():
    """Neither freshness nor completeness: the action is known and cannot be applied."""
    bars = [(date(2026, 6, 1), 100.0), (date(2026, 6, 2), 50.0)]
    actions = [{"action_type": "split", "ex_date": date(2026, 6, 2), "split_ratio": None}]
    out = resolve_window(bars, actions, _coverage(), start=date(2026, 6, 1), end=date(2026, 6, 2))
    assert out["limiting_factor"] == "unapplied_action"


def test_the_ceiling_is_a_property_of_the_source_not_of_the_window():
    assert best_achievable_status("response_only") == PROVISIONAL
    assert best_achievable_status("source_guarantee") == VERIFIED
    assert best_achievable_status(None) == PROVISIONAL, "an unknown basis is not a guarantee"


# ── AUD-PRICERISK-DIVIDEND (2026-10-13) ──────────────────────────────────────

def test_a_cash_dividend_does_not_mark_a_stale_window_corrupt():
    """AVGO, 2026-10: a $0.65 dividend made its whole decline 'unmeasurable'."""
    bars = [(date(2026, 10, 1), 376.0), (date(2026, 10, 9), 355.0)]
    div = {"symbol": "AVGO", "ex_date": date(2026, 10, 5), "action_type": "cash_dividend",
           "cash_amount": 0.65}
    resolved = resolve_window(bars, [div], None, start=bars[0][0], end=bars[-1][0])
    out = classify_observation_window(resolved, [div], start=bars[0][0], end=bars[-1][0])
    assert out["usable"] is True and out["adjustment"] == UNADJUSTED_NO_KNOWN_ACTION
    assert out["evidence_status"] == "unverified"


def test_a_cash_dividend_is_NAMED_not_silently_dropped():
    bars = [(date(2026, 10, 1), 100.0), (date(2026, 10, 9), 99.0)]
    div = {"ex_date": date(2026, 10, 5), "action_type": "cash_dividend", "cash_amount": 1.0}
    resolved = resolve_window(bars, [div], None, start=bars[0][0], end=bars[-1][0])
    out = classify_observation_window(resolved, [div], start=bars[0][0], end=bars[-1][0])
    assert out["cash_dividends"] == [{"ex_date": date(2026, 10, 5), "cash_amount": 1.0}]
    assert "cash dividend" in out["reason"]


def test_a_split_in_a_stale_window_is_still_corrupt():
    bars = [(date(2026, 10, 1), 100.0), (date(2026, 10, 9), 50.0)]
    split = {"ex_date": date(2026, 10, 5), "action_type": "split", "split_ratio": 2.0}
    resolved = resolve_window(bars, [split], None, start=bars[0][0], end=bars[-1][0])
    out = classify_observation_window(resolved, [split], start=bars[0][0], end=bars[-1][0])
    assert out["usable"] is False and out["adjustment"] == KNOWN_CORRUPT


def test_a_spinoff_in_a_stale_window_stays_corrupt():
    """Review 2026-10-13: the first cut blocklisted share-count types, so this was usable."""
    bars = [(date(2026, 10, 1), 100.0), (date(2026, 10, 9), 70.0)]
    for kind in ("spinoff", "rights_issue", "something_new"):
        act = {"ex_date": date(2026, 10, 5), "action_type": kind}
        resolved = resolve_window(bars, [act], None, start=bars[0][0], end=bars[-1][0])
        out = classify_observation_window(resolved, [act], start=bars[0][0], end=bars[-1][0])
        assert out["usable"] is False and out["adjustment"] == KNOWN_CORRUPT, kind


def test_a_spinoff_in_a_COVERED_window_is_not_passed_off_as_adjusted():
    bars = [(date(2026, 10, 1), 100.0), (date(2026, 10, 9), 70.0)]
    act = {"ex_date": date(2026, 10, 5), "action_type": "spinoff"}
    resolved = resolve_window(bars, [act], _coverage(), start=bars[0][0], end=bars[-1][0])
    assert resolved["prices"] == [] and resolved["limiting_factor"] == "unapplied_action"
    out = classify_observation_window(resolved, [act], start=bars[0][0], end=bars[-1][0])
    assert out["usable"] is False


def test_a_cash_dividend_in_a_covered_window_is_still_adjusted_and_named():
    bars = [(date(2026, 10, 1), 100.0), (date(2026, 10, 9), 99.0)]
    div = {"ex_date": date(2026, 10, 5), "action_type": "cash_dividend", "cash_amount": 1.0}
    resolved = resolve_window(bars, [div], _coverage(), start=bars[0][0], end=bars[-1][0])
    out = classify_observation_window(resolved, [div], start=bars[0][0], end=bars[-1][0])
    assert out["usable"] is True and out["adjustment"] == ADJUSTED
    assert out["cash_dividends"][0]["cash_amount"] == 1.0
