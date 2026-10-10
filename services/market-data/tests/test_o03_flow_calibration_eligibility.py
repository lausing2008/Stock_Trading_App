"""O03 — the flow calibration was computed over contracts that had already expired.

MEASURED (2026-10-08 review, reconfirmed in production while fixing): the 10-day calibration
query selected every non-null hit flag for a direction with no contract-validity filter, and
1,102 of 1,998 selected rows (55.16%) were contracts that had ALREADY EXPIRED when the alert
fired — 516 of 983 bullish, 586 of 1,015 bearish. There was never a trade there to measure.

Recent captures being clean does not repair the history the number is computed over.

The rule is a pure module so these tests execute THE REAL ONE. Two sabotage runs earlier in this
session caught tests of mine re-implementing a rule they were checking, and both passed while
the real code was broken.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "shared"))

from services.flow_outcome_eligibility import (  # noqa: E402
    classify, partition, ELIGIBLE, EXPIRED_BEFORE_ALERT, EXPIRED_BEFORE_ENTRY,
    SAME_DAY_EXPIRY, NO_EXPIRY_RECORDED, EXCLUSION_REASON, EXCLUDED)

FIRED = date(2026, 10, 1)
ENTRY = date(2026, 10, 2)


# ---- the three states, kept distinct ---------------------------------------------------------

def test_a_contract_expired_before_the_alert_is_invalid():
    """THE MEASURED DEFECT. 1,102 rows of this kind were feeding the published win rate."""
    assert classify(date(2026, 9, 30), FIRED, ENTRY) == EXPIRED_BEFORE_ALERT


def test_a_same_day_contract_is_excluded_without_being_called_invalid():
    """It existed and traded on the alert day — legitimate intraday research. The evaluator
    enters at the NEXT session, by which time it is gone, so it cannot measure THIS trade."""
    assert classify(FIRED, FIRED, ENTRY) == SAME_DAY_EXPIRY
    assert EXCLUSION_REASON[SAME_DAY_EXPIRY].startswith("the contract expired on the alert day")
    assert "legitimate intraday research" in EXCLUSION_REASON[SAME_DAY_EXPIRY]


def test_a_contract_expiring_between_alert_and_entry_is_its_own_reason():
    assert classify(ENTRY - __import__("datetime").timedelta(days=0), FIRED, ENTRY) != \
        EXPIRED_BEFORE_ENTRY  # expiry == entry still has a tradeable session
    assert classify(date(2026, 10, 1) + __import__("datetime").timedelta(days=0),
                    date(2026, 9, 28), ENTRY) == EXPIRED_BEFORE_ENTRY


def test_a_live_contract_is_eligible():
    assert classify(date(2026, 11, 20), FIRED, ENTRY) == ELIGIBLE


def test_a_missing_expiry_cannot_establish_validity_either_way():
    assert classify(None, FIRED, ENTRY) == NO_EXPIRY_RECORDED


def test_the_earlier_more_specific_fact_is_the_one_reported():
    """A contract expired before the alert also expired before entry; reporting the vaguer of
    the two would lose the finding."""
    assert classify(date(2026, 9, 1), FIRED, ENTRY) == EXPIRED_BEFORE_ALERT


def test_every_exclusion_reason_is_explained_in_words():
    for reason in EXCLUDED:
        assert reason in EXCLUSION_REASON and len(EXCLUSION_REASON[reason]) > 40


# ---- the partition reports what it removed, not just what survived ---------------------------

def _row(expiry, correct, fired=FIRED, entry=ENTRY):
    return {"expiry": expiry, "fired_date": fired, "entry_date": entry, "is_correct": correct}


def test_the_partition_reports_the_original_count_and_every_exclusion():
    rows = [_row(date(2026, 11, 20), True), _row(date(2026, 11, 20), False),
            _row(date(2026, 9, 30), True), _row(date(2026, 9, 29), True),
            _row(FIRED, True), _row(None, True)]
    got = partition(rows)
    assert got["original_count"] == 6
    assert got["eligible_count"] == 2
    assert got["excluded_total"] == 4
    assert got["excluded_counts"] == {EXPIRED_BEFORE_ALERT: 2, NO_EXPIRY_RECORDED: 1,
                                      SAME_DAY_EXPIRY: 1}
    assert set(got["exclusion_reasons"]) == set(got["excluded_counts"])


def test_the_excluded_rows_are_not_merely_subtracted():
    """A completion report needs ORIGINAL, ELIGIBLE and exclusions BY REASON — a survivor count
    alone cannot say what was removed or why."""
    got = partition([_row(date(2026, 9, 30), True)])
    assert got["original_count"] == 1 and got["eligible_count"] == 0
    assert got["excluded_counts"][EXPIRED_BEFORE_ALERT] == 1


def test_excluding_invalid_rows_changes_the_rate_rather_than_merely_the_count():
    """The whole point. Every excluded row here is a 'win', so filtering them out LOWERS the
    measured rate — which is why leaving them in flattered it."""
    rows = [_row(date(2026, 11, 20), False), _row(date(2026, 11, 20), False),
            _row(date(2026, 9, 30), True), _row(date(2026, 9, 30), True)]
    got = partition(rows)
    unfiltered = sum(1 for r in rows if r["is_correct"]) / len(rows)
    eligible = sum(1 for r in got["eligible"] if r["is_correct"]) / got["eligible_count"]
    assert unfiltered == 0.5 and eligible == 0.0


# ---- the calibration consumes it, and says so ------------------------------------------------

def _calibration_source():
    return (Path(__file__).resolve().parents[1]
            / "src/services/scheduler.py").read_text()


def _cal_fn_body():
    import ast
    src = _calibration_source()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef)
              and n.name == "_build_options_flow_alert_calibration")
    return ast.unparse(fn)


def test_the_calibration_partitions_before_counting():
    body = _cal_fn_body()
    assert "_partition(raw" in body, "the shared rule must do the filtering"
    assert "is_trading_day=_itd" in body, "and with the trading calendar, not dates alone"
    assert "part['eligible']" in body


def test_the_calibration_reports_exclusions_alongside_the_rate():
    body = _cal_fn_body()
    for key in ("'original_count'", "'eligible_count'", "'excluded_total'",
                "'excluded_by_reason'", "'exclusion_reasons'"):
        assert key in body, f"{key} must travel with the rate"


def test_insufficient_eligible_history_is_a_distinct_result_from_no_history():
    """Returning a bare None would make a calibration REMOVED BY FILTERING indistinguishable
    from one that never existed."""
    body = _cal_fn_body()
    assert "'insufficient_eligible_history'" in body
    assert "not a zero win rate" in body


def test_shared_cohort_status_suppresses_small_and_clustered_rates():
    from services.flow_outcome_eligibility import cohort_measurement_status

    small = {"eligible": [{"fired_date": date(2026, 1, day)} for day in range(1, 6)]}
    assert cohort_measurement_status(small)["status"] == "insufficient_eligible_history"
    clustered = {"eligible": [{"fired_date": date(2026, 1, 2)} for _ in range(30)]}
    assert cohort_measurement_status(clustered)["status"] == "clustered_dates"
    measured = {"eligible": [{"fired_date": date(2026, 1, 1 + i % 5)} for i in range(30)]}
    assert cohort_measurement_status(measured)["status"] == "measured"


def test_the_horizon_travels_with_the_rate():
    assert "'horizon': '10d'" in _cal_fn_body(), \
        "a hit rate without its horizon cannot be compared with anything"


def test_the_published_calibration_on_historical_rows_is_not_rewritten():
    """`calibrated_win_rate` on each stored outcome is what THAT alert actually displayed.
    Correcting eligibility must create a new calculation, not revise the record of what was
    shown."""
    body = _cal_fn_body()
    assert "update" not in body.lower().replace("updated", ""), \
        "this function must not write to the outcome rows"
    assert "stays exactly as published" in _calibration_source()


def test_one_computation_feeds_the_stored_row_and_the_email():
    """Requirement: API, email and UI consume the SAME eligible cohort. They do because there
    is exactly one place the rate is produced and one place it is read."""
    import ast
    src = _calibration_source()
    tree = ast.parse(src)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "_build_options_flow_alert_calibration"]
    assert len(calls) == 2, "exactly two calls: one per direction"
    reads = src.count('cal["win_rate"]')
    assert reads == 1, f"the rate must be read in exactly one place, found {reads}"


# ---- an expiry date is not a trading instant --------------------------------------------------

def _cal():
    from common.market_calendar import is_trading_day
    return is_trading_day


def test_a_saturday_dated_contract_stops_trading_on_the_friday():
    """Historically US monthly equity options carried a SATURDAY expiration and stopped trading
    the preceding Friday. Any expiry landing on a weekend or holiday is untradable on its own
    stated date, so the comparison must use the last tradable session."""
    from services.flow_outcome_eligibility import last_tradable_session
    assert last_tradable_session(date(2026, 10, 10), is_trading_day=_cal()) == date(2026, 10, 9)
    # A weekday expiry is its own last session.
    assert last_tradable_session(date(2026, 10, 9), is_trading_day=_cal()) == date(2026, 10, 9)


def test_the_friday_alert_saturday_expiry_case_is_not_eligible():
    """THE REPORTED GAP. Expiry (Sat 10th) is after the alert (Fri 9th) and before Monday's
    entry, so a date-only rule can pass it as a live contract. It stopped trading on the 9th."""
    got = classify(date(2026, 10, 10), date(2026, 10, 9), date(2026, 10, 12),
                   is_trading_day=_cal())
    assert got != ELIGIBLE
    assert got == SAME_DAY_EXPIRY, "its last tradable session IS the alert day"


def test_an_unknown_entry_date_is_timing_unknown_not_eligible():
    """The first version fell through to eligible when no entry date was stored, which assumes
    exactly the thing that cannot be established."""
    from services.flow_outcome_eligibility import TIMING_UNKNOWN
    assert classify(date(2026, 11, 20), date(2026, 10, 9), None,
                    is_trading_day=_cal()) == TIMING_UNKNOWN


def test_a_genuinely_live_contract_is_still_eligible():
    """The control — the stricter rule must not refuse ordinary rows."""
    assert classify(date(2026, 11, 20), date(2026, 10, 9), date(2026, 10, 12),
                    is_trading_day=_cal()) == ELIGIBLE


def test_timing_unknown_has_its_own_reason_and_is_an_exclusion():
    from services.flow_outcome_eligibility import TIMING_UNKNOWN, EXCLUDED, EXCLUSION_REASON
    assert TIMING_UNKNOWN in EXCLUDED
    assert "unknown, not assumed fine" in EXCLUSION_REASON[TIMING_UNKNOWN]


# ---- the calculation is versioned -------------------------------------------------------------

def test_the_partition_records_which_rules_produced_it():
    """Immutable inputs alone cannot reproduce an old result after the RULES change — the same
    expiry and dates yield a different verdict under a later rule set."""
    from services.flow_outcome_eligibility import ELIGIBILITY_VERSION
    got = partition([_row(date(2026, 11, 20), True)], is_trading_day=_cal())
    assert got["eligibility_version"] == ELIGIBILITY_VERSION
    assert ELIGIBILITY_VERSION.startswith("flow-elig-")


def test_the_calibration_PAYLOAD_carries_the_version_not_just_the_partition():
    """It did not. The partition returned `eligibility_version` and the calibration built its
    exclusions dict without it, so every published figure read `None` — verified against
    production, where the field came back null. Testing the partition alone could not see it."""
    body = _cal_fn_body()
    assert "'eligibility_version': part['eligibility_version']" in body


def test_every_exclusion_key_the_partition_produces_reaches_the_payload():
    """The general form of the same defect: a key added to the partition and forgotten here."""
    import ast
    body = _cal_fn_body()
    from services.flow_outcome_eligibility import partition as _p
    produced = set(_p([_row(date(2026, 9, 1), True)], is_trading_day=_cal()))
    carried = {"original_count", "eligible_count", "excluded_total", "eligibility_version"}
    # `eligible` is the cohort itself and `excluded_counts`/`exclusion_reasons` are renamed.
    renamed = {"excluded_counts": "excluded_by_reason",
               "exclusion_reasons": "exclusion_reasons"}
    for key in produced - {"eligible"}:
        name = renamed.get(key, key)
        assert f"'{name}'" in body, f"the partition produces {key} and the payload drops it"
    assert carried <= produced | set(renamed.values())


def test_the_version_history_is_recorded_in_the_module():
    src = (Path(__file__).resolve().parents[1]
           / "src/services/flow_outcome_eligibility.py").read_text()
    assert "v1  expiry vs fired_date" in src and "v2  last TRADABLE session" in src


# ---- the rate is named precisely ---------------------------------------------------------------

def test_the_rate_is_labelled_an_underlying_directional_hit_rate():
    body = _cal_fn_body()
    assert "'measured underlying directional hit rate'" in body
    assert "not a calibrated probability" in body.lower()
    assert "options strategy" in body.lower()


def test_the_window_is_named_calendar_days_because_that_is_what_it_is():
    """`_SQUEEZE_OUTCOME_WINDOWS` is commented "calendar days after entry" and the evaluator
    does `entry_date + timedelta(days=window)`. Ten calendar days is not ten sessions."""
    body = _cal_fn_body()
    assert "'horizon_unit': 'calendar_days'" in body
    sched = _calibration_source()
    assert "calendar days after entry" in sched, "the source convention must still say so"


# ---- API and UI read the same cohort -----------------------------------------------------------

def test_the_admin_performance_endpoint_uses_the_same_eligibility_rule():
    """ONE FUNCTION FEEDING THE STORED ROW AND THE EMAIL DOES NOT COVER THE API. This endpoint
    had its OWN query with no validity filter, so the performance screen and the figure attached
    to an alert were computed over different populations."""
    import ast
    admin = (Path(__file__).resolve().parents[1] / "src/api/admin.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(admin))
              if isinstance(n, ast.FunctionDef) and "options_flow_alert_performance" in n.name)
    body = ast.unparse(fn)
    assert "_partition(" in body, "the API must apply the shared eligibility rule"
    assert "is_trading_day=_itd" in body, "including the trading-calendar comparison"


def test_the_admin_endpoint_reports_its_denominator_and_rule_version():
    import ast
    admin = (Path(__file__).resolve().parents[1] / "src/api/admin.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(admin))
              if isinstance(n, ast.FunctionDef) and "options_flow_alert_performance" in n.name)
    body = ast.unparse(fn)
    for key in ("'original_n'", "'excluded_total'", "'excluded_by_reason'",
                "'eligibility_version'"):
        assert key in body, f"{key} must travel with the rate the UI renders"


def test_exactly_one_module_decides_eligibility():
    """Two implementations are two rules that will disagree."""
    import subprocess
    root = Path(__file__).resolve().parents[3]
    # SOURCE ONLY — a test file naturally mentions the function it tests.
    hits = subprocess.run(
        ["grep", "-rln", "--include=*.py", "def classify(expiry",
         *[str(p) for p in (root / "services").glob("*/src")]],
        capture_output=True, text=True).stdout.split()
    assert len(hits) == 1, f"eligibility is defined in more than one place: {hits}"
