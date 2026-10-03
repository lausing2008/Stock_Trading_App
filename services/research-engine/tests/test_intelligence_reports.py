"""Intelligence reports — the behavioural acceptance, driven through the real generators.

The scenarios live in `_intelligence_pg_probe.py` and run against a real database (PostgreSQL
when STOCKAI_INTEL_DB names one, otherwise a throwaway SQLite file so this suite needs no
server). Each test below asserts one scenario's verdict, so a failure names the property that
broke rather than "the probe failed".

NO SOURCE-TEXT ASSERTIONS. Every property here is established by generating a report, storing
it, and reading what came back.
"""
import json
import pathlib
import subprocess
import sys

import pytest

_PROBE = pathlib.Path(__file__).resolve().parent / "_intelligence_pg_probe.py"


@pytest.fixture(scope="module")
def results():
    proc = subprocess.run([sys.executable, str(_PROBE)], capture_output=True, text=True)
    if proc.returncode != 0:
        pytest.fail(f"probe failed:\n{proc.stdout[-3000:]}\n{proc.stderr[-3000:]}")
    return json.loads(proc.stdout)


def _scenario(results, name):
    assert name in results, f"{name} did not run"
    return results[name]


def test_all_four_report_types_generate_and_render(results):
    r = _scenario(results, "t1_all_four_types_generate")
    for kind in ("market_outlook", "stock_outlook", "pre_earnings", "post_earnings"):
        assert r[kind]["id"], f"{kind} produced no stored report"
        assert r[kind]["fields"] > 5
        assert r[kind]["markdown_lines"] > 10
    assert r["passes"]


def test_thin_data_yields_a_partial_report_not_an_error(results):
    r = _scenario(results, "t7_partial_inputs_still_produce_a_useful_report")
    assert r["status"] == "partial"
    assert r["missing_fields"] > 0
    assert r["every_missing_field_has_a_reason"], "a missing field with no reason is a blank cell"
    assert r["markdown_has_why_section"]


def test_identical_inputs_reuse_the_report_rather_than_duplicating(results):
    r = _scenario(results, "t2_identical_inputs_do_not_duplicate")
    assert r["same_fingerprint"]
    assert r["first_id"] == r["second_id"]
    assert r["rows"] == 1
    assert r["created"] == [True, False]


def test_changed_inputs_create_a_linked_version_and_leave_the_original_intact(results):
    r = _scenario(results, "t3_changed_inputs_create_a_linked_version")
    assert r["v2_version"] == 2
    assert r["supersedes"] == r["v1"]
    assert r["v1_status"] == "superseded"
    assert r["changed_fields"] > 0
    assert r["v1_payload_unchanged"], "a superseded report must keep the payload it was issued with"


def test_a_missing_pre_earnings_baseline_is_recorded_not_reconstructed(results):
    r = _scenario(results, "t4_post_report_without_a_pre_report_says_so")
    assert r["pre_report_id"] is None
    assert r["link_state"] == "UNAVAILABLE"
    assert "not reconstructed" in r["link_reason"].lower()
    assert r["verdict_state"] == "NOT_APPLICABLE"


def test_a_frozen_pre_report_survives_a_later_consensus_revision(results):
    """The accountability guarantee: the post-release report scores against what was written
    BEFORE the release, not against a consensus revised afterwards."""
    r = _scenario(results, "t5_a_frozen_pre_report_survives_and_scores")
    assert r["frozen_payload_unchanged"]
    assert r["post_pre_link"] == r["pre_id"]
    assert r["verdict"]["frozen_consensus_eps"] == 1.50, \
        "scored against the revised 1.95 instead of the frozen 1.50"


def test_a_report_written_after_the_release_cannot_become_the_baseline(results):
    r = _scenario(results, "t6_a_report_written_after_the_release_is_not_a_pre_report")
    assert r["found"] is None


def test_fiscal_period_is_never_asserted_from_the_release_month(results):
    """EarningsEvent.fiscal_quarter is derived from the period-end calendar month and is wrong
    for every non-calendar fiscal year. The report must say UNKNOWN rather than repeat it."""
    r = _scenario(results, "t8_fiscal_period_is_never_asserted_from_the_release_month")
    assert r["state"] == "UNKNOWN"
    assert r["value_is_absent"]


def test_a_zero_estimate_produces_no_percentage_surprise(results):
    r = _scenario(results, "t9_zero_estimate_gives_no_percentage_surprise")
    assert r["normal"]["state"] == "OK"
    assert r["zero_estimate"]["state"] == "NOT_APPLICABLE"
    assert "absolute difference" in r["zero_estimate"]["value"]
    assert r["missing_actual"]["state"] == "UNAVAILABLE"


def test_a_negative_estimate_does_not_invert_the_surprise_sign(results):
    """abs() in the denominator: beating a -0.50 estimate with -0.20 is a POSITIVE surprise."""
    r = _scenario(results, "t9_zero_estimate_gives_no_percentage_surprise")
    assert r["negative_estimate"]["value"]["pct"] > 0


def test_proxies_are_labelled_as_proxies(results):
    r = _scenario(results, "t10_market_report_labels_its_proxies")
    assert "not an index constituent list" in r["breadth_basis"]
    assert r["both_denominators"], \
        "participation and coverage are different ratios and must not share one percentage"
    assert "proxy" in r["benchmark_basis"]
    assert r["volatility_state"] == "UNAVAILABLE", \
        "an unsourced dimension must say so rather than be omitted"


def test_horizons_refuse_rather_than_repeat_one_daily_heuristic(results):
    """IR-03. Three fields that look independently derived but restate one daily rule are three
    times the confidence with none of the evidence."""
    r = _scenario(results, "t11_horizons_refuse_rather_than_repeat_one_heuristic")
    assert set(r["horizon_states"].values()) == {"UNAVAILABLE"}
    assert r["daily_structure_state"] == "OK", "the one real reading must still be reported"
    assert r["signal_field_name"], "the signal field must name what it actually reads"


def test_a_bearish_reading_is_invalidated_by_a_move_up(results):
    """IR-03. Confirmation and invalidation were both written for the constructive case, so a
    below-average reading named its own supporting condition as its invalidation."""
    r = _scenario(results, "t12_a_bearish_reading_is_invalidated_by_a_move_up")
    assert "below" in r["reading"]
    assert r["what_would_change_it"].startswith("a daily close above")


def test_a_historical_cutoff_cannot_consume_later_prices(results):
    """IR-02. Input selection ignored the cutoff, so a 25 September report read the 2 October
    close. Preventing look-ahead is an input-selection rule, not a schema."""
    r = _scenario(results, "t13_a_historical_cutoff_cannot_consume_later_prices")
    assert r["price_ts_used"] <= r["cutoff"]
    assert r["evidence_records"] > 0


def test_every_citation_resolves_and_load_bearing_fields_cite(results):
    """IR-02. Fields carried ids matching no record — a citation that looks checkable and is
    not. Checking only for dangling references is too weak: a field citing nothing passes it."""
    r = _scenario(results, "t14_every_citation_resolves")
    for kind in ("market", "stock", "pre"):
        assert r[kind]["records"] > 0, f"{kind} report stored no evidence at all"
        assert not r[kind]["dangling"], f"{kind} cites {r[kind]['dangling']}"
        assert not r[kind]["load_bearing_without_citation"], \
            f"{kind}: {r[kind]['load_bearing_without_citation']} carry no evidence"


def test_a_stale_price_degrades_what_is_derived_from_it(results):
    """IR-02. The trend read the same bars the price had just been flagged stale for."""
    r = _scenario(results, "t15_a_stale_price_degrades_what_is_derived_from_it")
    assert r["price_state"] == "STALE"
    assert r["trend_state"] == "STALE"


def test_a_pre_report_cannot_be_written_once_the_release_is_known(results):
    """IR-01. `report_date >= today` admitted an event that reported earlier the same day."""
    r = _scenario(results, "t16_a_pre_report_cannot_be_written_once_the_release_is_known")
    assert r["after_release"] != "ALLOWED"
    assert r["on_release_day"] != "ALLOWED", \
        "only a date is stored, so a same-day report cannot be shown to precede the release"


def test_the_surprise_table_and_the_verdict_use_the_same_expectation(results):
    """IR-04. The table read the mutable current estimate while the verdict read the frozen
    one, so one report showed a -11.79% miss beside a verdict of 'above'."""
    r = _scenario(results, "t17_the_surprise_table_uses_the_frozen_expectation")
    assert r["expectation"]["is_frozen"] is True
    assert r["expectation"]["value"] == 1.50
    assert r["surprise_pct"]["pct"] > 0, "the table must measure against the frozen 1.50"
    assert r["revision_field_present"], "a later revision is information, shown separately"
    assert r["verdict"]["thesis_evaluation"] == "not_evaluable", \
        "a beat is a fact about the company, not confirmation of a claim nobody made"
    assert r["stage"] == "FIRST_FLASH", "nothing here reconciles sources"


def test_concurrent_generation_allocates_one_version_each(results):
    """IR-05. save() reads the max version and adds one; two requests can read the same answer.
    Only the database constraint prevents the duplicate."""
    r = _scenario(results, "t18_concurrent_generation_allocates_one_version_each")
    assert not r["errors"], r["errors"]
    assert r["unique_versions"], f"duplicate versions allocated: {r['versions']}"
    assert len(r["versions"]) == 4


def test_an_absent_field_is_not_labelled_an_observed_fact():
    """The statement class describes a CLAIM, and a field with no value makes none.

    Caught on the first real production report: every UNAVAILABLE row rendered with the default
    `observed_fact` class beside it, which labels an absence as something observed — the exact
    mislabelling this contract exists to prevent.
    """
    import importlib.util, pathlib, sys
    root = pathlib.Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location(
        "intel_md_under_test", root / "src" / "intel_reports" / "markdown.py")
    md = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = md
    spec.loader.exec_module(md)

    class R:
        id, version, supersedes_id, pre_report_id = 1, 1, None, None
        report_type, subject_key, market, status = "market_outlook", "market:US", "US", "partial"
        contract_version, policy_version = 1, "1"
        generated_at = cutoff_at = "2026-10-03T07:38:12"
        input_fingerprint, coverage = "abc", {"ok": 1, "total": 2}
        payload = {"fields": {
            "breadth": {"state": "OK", "value": 54.2, "units": "pct",
                        "statement": "deterministic_calculation"},
            "liquidity": {"state": "UNAVAILABLE", "value": None,
                          "reason": "no financial-conditions measure is ingested",
                          "statement": "observed_fact"},
        }}

    out = md.to_markdown(R())
    liquidity_row = next(l for l in out.splitlines() if l.startswith("| liquidity "))
    assert "observed_fact" not in liquidity_row, liquidity_row
    assert "UNAVAILABLE" in liquidity_row
    breadth_row = next(l for l in out.splitlines() if l.startswith("| breadth "))
    assert "deterministic_calculation" in breadth_row, "a real claim keeps its class"


# ── The screenshot round: units, history copy, availability, timezone, coverage ────────────

def test_fractional_returns_are_converted_once(results):
    """MU's stored 0.15382 means +15.38%. It rendered as "0.1538 pct" — a hundredfold error in
    the most quotable number the report carries. The producer returns price/baseline-1 while
    its own docstring calls it "% change", which is where the confusion starts."""
    r = _scenario(results, "t19_fractional_returns_are_converted_once")
    assert abs(r["reported_1d"]["pct"] - 15.38) < 0.01
    assert abs(r["reported_5d"]["pct"] + 1.85) < 0.01
    assert r["units_1d"] == "pct"
    assert "not normalised" in r["reported_1d"]["note"], \
        "the window must be described, not claimed as a normalised release reaction"
    assert " close to " in r["reported_1d"]["window"], \
        "the window names its actual dates; a span word cannot be right for every release"


def test_historical_reaction_summaries_use_the_same_unit(results):
    r = _scenario(results, "t20_historical_reaction_summaries_use_the_same_unit")
    assert abs(r["value"]["median_1d_pct"] - 6.90) < 0.01
    assert abs(r["value"]["max_pct"] - 15.38) < 0.01


def test_a_reused_report_is_not_called_a_first_report(results):
    """The card said "v2 · supersedes #1" while its own comparison said "first report"."""
    r = _scenario(results, "t21_a_reused_report_is_not_called_a_first_report")
    assert r["created"] == [True, False], "the second generation must reuse, not create"
    assert r["reused_diff_first_report"] is False
    assert r["no_baseline_still_not_first"] is False, \
        "a report that supersedes something has history even when the baseline cannot be loaded"


def test_a_bar_date_is_not_an_availability_time(results):
    """A bar timestamped at midnight does not establish that its CLOSE was knowable then."""
    r = _scenario(results, "t22_a_bar_date_is_not_an_availability_time")
    assert r["price_records"] > 0
    assert r["first_available_at"] is None
    assert r["published_at"] is None
    assert r["has_limitation_note"]


def test_the_release_boundary_uses_the_exchange_timezone(results):
    """Naive midnight read as UTC is not conservative in both directions: HK is UTC+8, so
    20:00 UTC on the previous calendar day is already 04:00 on the HK release day."""
    r = _scenario(results, "t23_the_release_boundary_uses_the_exchange_timezone")
    assert r["hk_boundary_utc"] < r["naive_midnight"] < r["us_boundary_utc"]


def test_a_stale_newest_event_is_flagged_not_substituted(results):
    """Asked for MU on 3 October, the report described 24 June — correctly by its own rule,
    because the 30 September release is not ingested. The silence was the defect.

    Driven by MU's REAL release dates. A fixed 115-day threshold did not fire at 101 days, for
    exactly the case it was built for; the issuer's own median gap does.
    """
    r = _scenario(results, "t24_a_stale_newest_event_is_flagged_not_substituted")
    # Within the median the state is UNKNOWN, not OK: nothing has been verified against the
    # issuer's own calendar, so "no warning" must not read as "coverage confirmed".
    assert r["fresh_state"] == "UNKNOWN"
    assert r["mu_state"] == "CONFLICTING"
    assert r["mu_detail"]["age_days"] == 101
    assert r["mu_detail"]["issuer_median_gap_days"] < r["mu_detail"]["age_days"], \
        "the warning must come from the issuer's own cadence, not a fixed calendar guess"
    assert "POSSIBLE COVERAGE GAP" in r["mu_reason"]


# ── Cadence semantics, exact windows, and the official release join ────────────────────────

def test_cadence_is_an_interpretation_not_an_observed_fact(results):
    """A median is not a deadline. Exceeding it suggests a gap worth checking; it does not
    establish that a release happened — and the absence of a warning certifies nothing."""
    r = _scenario(results, "t24_a_stale_newest_event_is_flagged_not_substituted")
    assert r["mu_statement"] == "interpretation", \
        "a cadence inference must never be classed as an observed fact"
    assert r["mu_coverage_state"] == "suspected_gap"
    assert "not proof" in r["mu_reason"]
    assert r["fresh_coverage_state"] == "coverage_unknown", \
        "within the median, coverage is unknown — not verified complete"


def test_the_reaction_window_names_its_real_interval(results):
    """`return_1d` runs from the last close BEFORE the report date to the day AFTER it — two
    close-to-close intervals for a trading-day release, not one."""
    r = _scenario(results, "t25_the_reaction_window_names_its_real_interval")
    assert r["window_dates"]["baseline_date"] == "2026-09-29"
    assert r["window_dates"]["endpoint_1d"] == "2026-10-01"
    assert r["window_dates"]["intervals_1d"] == 2
    assert abs(r["reported"]["pct"] - 21.0) < 0.01, "the legacy value is preserved exactly"
    assert r["reported"]["window"] == "2026-09-29 close to 2026-10-01 close"


def test_the_official_release_is_joined_and_keeps_both_margin_bases(results):
    """Micron's own release distinguishes 86.8% GAAP from 87.0% non-GAAP gross margin.
    Collapsing them loses a distinction the issuer itself drew."""
    r = _scenario(results, "t26_the_official_release_is_joined_by_period")
    assert r["release_state"] == "OK"
    assert "source-confirmed" in r["matched_on"]
    assert r["fiscal_state"] == "OK"
    assert r["gaap_vs_non_gaap_both_present"]
    assert r["cited"], "the release must be citable evidence, not an unreferenced attachment"


def test_a_release_without_an_event_confirms_the_gap(results):
    """The promotion cadence cannot make: a dated official release naming a period the event
    table does not contain is evidence, not an inference about reporting rhythm."""
    r = _scenario(results, "t27_a_release_without_an_event_confirms_the_gap")
    assert r["coverage_state"] == "confirmed_missing_event"
    assert r["statement"] == "observed_fact"
    assert r["orphans"][0]["fiscal_period_end"] == "2026-09-03"
    assert r["cited"]


def test_a_document_join_never_depends_on_an_event_row(results):
    """Resolving by issuer and period rather than event id is what makes the case above
    expressible: MU's September release is real and has no event row."""
    r = _scenario(results, "t28_a_document_join_never_depends_on_an_event_row")
    assert r["event_rows"] == 0
    assert r["document_event_id"] is None
    assert r["found_by_period"] == r["found_by_report_date"]
    assert len(r["found_by_period"]) == 1
