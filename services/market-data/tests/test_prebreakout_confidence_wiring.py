"""Source-text regression checks for T264-SHORTSQUEEZE-PREBREAKOUT-CONFIDENCE's wiring inside
check_prebreakout_alerts() itself — the candidate loop that calls _fetch_ml_price_direction()/
_prebreakout_calibration_for_band() per candidate and threads both results through to
_record_prebreakout_alert_outcome(). check_prebreakout_alerts() is a full integration path
(DB session, Redis, live HTTP calls to ml-prediction, email sending) with no existing
behavioral test of its own (see test_alerts_env_gate.py for its job-registration coverage
instead) — matching this repo's established convention for scheduler.py functions of this
shape, these are direct source-text checks against the real file, not a hand-copied
reimplementation that could silently drift from it.
"""
import pathlib

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _function_body(name: str, end_marker: str) -> str:
    start = _scheduler_source.index(f"def {name}(")
    end = _scheduler_source.index(end_marker, start)
    return _scheduler_source[start:end]


def test_calibration_buckets_are_built_once_per_cycle_before_the_candidate_loop():
    """AUD-SQUEEZE250725-PERF4.3 wrapped the raw builder call in _cached_calibration_buckets()
    (a 5-min Redis cache, matching short_squeeze/gamma_unwind's own new caching) — the
    underlying _build_prebreakout_calibration(session) call must still happen (now inside a
    lambda passed to the cache wrapper), and still before the candidate loop."""
    body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    cal_build_idx = body.index("cal_buckets = _cached_calibration_buckets(")
    assert "_build_prebreakout_calibration(session)" in body
    candidates_dict_idx = body.index("candidates: dict[str, dict] = {}")
    assert cal_build_idx < candidates_dict_idx, (
        "cal_buckets must be built ONCE, before the per-symbol candidate loop starts — "
        "building it inside the loop would re-query the DB once per candidate for no reason"
    )


def test_ml_price_direction_is_fetched_per_candidate_inside_the_loop():
    body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    assert "ml_confidence, ml_model_version = _fetch_ml_price_direction(symbol)" in body


def test_calibration_lookup_is_called_per_candidate_with_that_candidates_own_short_interest():
    body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    assert "cal_win_rate, cal_count = _prebreakout_calibration_for_band(cal_buckets, spf_pct)" in body


def test_candidate_dict_carries_all_four_new_fields():
    body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    for field in (
        '"ml_price_direction_confidence": ml_confidence',
        '"ml_price_direction_model_version": ml_model_version',
        '"calibrated_win_rate": cal_win_rate',
        '"calibrated_win_rate_count": cal_count',
    ):
        assert field in body, f"candidate dict is missing {field!r}"


def test_record_call_site_passes_all_four_new_fields_through():
    body = _function_body("check_prebreakout_alerts", "\n\ndef _record_prebreakout_alert_outcome(")
    call_idx = body.index("_record_prebreakout_alert_outcome(\n")
    call_text = body[call_idx : call_idx + 600]
    for field in (
        'cand["ml_price_direction_confidence"]', 'cand["ml_price_direction_model_version"]',
        'cand["calibrated_win_rate"]', 'cand["calibrated_win_rate_count"]',
    ):
        assert field in call_text, f"_record_prebreakout_alert_outcome() call site is missing {field!r}"


def test_record_prebreakout_alert_outcome_signature_accepts_the_four_new_params():
    start = _scheduler_source.index("def _record_prebreakout_alert_outcome(")
    end = _scheduler_source.index("\n) -> None:", start)
    signature = _scheduler_source[start:end]
    for param in (
        "ml_price_direction_confidence: float | None = None",
        "ml_price_direction_model_version: str | None = None",
        "calibrated_win_rate: float | None = None",
        "calibrated_win_rate_count: int | None = None",
    ):
        assert param in signature, f"_record_prebreakout_alert_outcome()'s signature is missing {param!r}"


def test_record_prebreakout_alert_outcome_passes_the_four_new_params_into_the_orm_row():
    start = _scheduler_source.index("def _record_prebreakout_alert_outcome(")
    end = _scheduler_source.index("\n\n\n_GAMMA_UNWIND_LOCK_KEY", start)
    body = _scheduler_source[start:end]
    for assignment in (
        "ml_price_direction_confidence=ml_price_direction_confidence",
        "ml_price_direction_model_version=ml_price_direction_model_version",
        "calibrated_win_rate=calibrated_win_rate",
        "calibrated_win_rate_count=calibrated_win_rate_count",
    ):
        assert assignment in body, f"PreBreakoutAlertOutcome(...) construction is missing {assignment!r}"
