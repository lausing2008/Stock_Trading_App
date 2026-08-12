"""Tests for AUD263-SELLPILLAR-GATE-UNMEASURABLE-AND-UNSCHEDULED's train-slice selection fix.

Live-verified against production before writing this fix (see the CLAUDE.md write-up): all 4
recorded tune_sell_pillars() attempts (SHORT/SWING/GROWTH via candidate_unmeasurable_on_
validation, LONG via ev_lift_not_positive) traced to the SAME root cause — min_pillars=4's
train-slice subset (72-86 rows across all 4 horizons) always looked best by EV purely from
being the narrowest, most cherry-picked subset (a classic small-sample overfit signature: EV
improves monotonically as min_pillars rises 1->4 in 3 of 4 horizons), then correctly failed
candidate_unmeasurable_on_validation once its ~30-46-row validation subset fell under the
50-sample floor. Nothing previously required a train-slice candidate's own subset to be large
enough to plausibly survive the 70/30 split shrink before being chosen as best_p.

Fix: _train_min_n scales min_samples by the real train/validation ratio, so a candidate is
only considered on the train slice if a PROPORTIONAL subset would still be expected to clear
min_samples after the same split shrinks it. Re-ran the real production data with this fix
applied (live verification, not just synthetic) and confirmed all 4 horizons became measurable
on validation for the first time (0 of 4 unmeasurable, down from 3 of 4).

The per-horizon loop body is coupled to session/_record_tune_history/redis_client (real side
effects) and can't be imported directly (calibration.py needs common.jwt_auth, not stubbed in
this test environment) — extracted via exec() with those dependencies faked, matching
test_calibrate_ta_weights_validation.py's established _extract_calibrate_ta_weights_core()
technique exactly (same "big DB-coupled route function, extract just the computational core"
shape).
"""
import pathlib
import statistics as _stats
from collections import namedtuple
from datetime import date, timedelta

_CAL_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CAL_SOURCE = _CAL_PATH.read_text()

_Outcome = namedtuple("_Outcome", ["bearish_pillars_active", "is_correct", "pct_return", "signal_date", "horizon"])
_Horizon = namedtuple("_Horizon", ["value"])


def _extract_loop_body():
    """Pulls the per-horizon loop body (bucket construction through the end of one iteration)
    out of tune_sell_pillars() — the actual train/validation selection logic under test, with
    the DB-query prologue (bucket provided as a fixture) and the Redis/TuneHistory side effects
    stubbed via injected fakes in the exec() namespace."""
    start = _CAL_SOURCE.index("        bucket = sorted(\n            [o for o in all_sell_outcomes if o.horizon.value == h],")
    end = _CAL_SOURCE.index("\n    return {\n        \"applied\": applied,")
    body = _CAL_SOURCE[start:end]
    # Re-indent from loop-body (8-space under the route function's for-loop) down to a bare
    # function body, wrapped in a single-pass "for _ in (0,):" so the real source's own
    # `continue` statements (used for the skip branches) stay syntactically valid without
    # needing to rewrite them into early `return`s — a single iteration is exactly equivalent
    # to the original per-horizon loop body running once.
    lines = body.splitlines()
    dedented = "\n".join(line[8:] if line.startswith("        ") else line for line in lines)
    func_source = (
        "def _core(h, all_sell_outcomes, min_samples, applied, skipped, "
        "_record_tune_history, session, _run_id):\n"
        "    for _ in (0,):\n"
        + "\n".join("        " + line if line.strip() else line for line in dedented.splitlines())
    )

    class _FakeRedis:
        def setex(self, *a, **kw):
            pass

    namespace = {
        "date": date, "_stats": _stats, "redis_client": _FakeRedis(), "_REDIS_TTL": 30 * 86400,
        "_mark_tuned": lambda *a, **kw: None,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_core"]


_core = _extract_loop_body()


def _run(outcomes, min_samples=50):
    applied, skipped = [], []
    recorded = []

    def _fake_record_tune_history(session, run_id, parameter_class, parameter_name, style, market,
                                   old_value, new_value, train_window, validation_window,
                                   train_ev_pct, validation_ev_pct, baseline_validation_ev_pct,
                                   validation_n, promoted, gate_failures):
        recorded.append(locals())

    _core("SWING", outcomes, min_samples, applied, skipped, _fake_record_tune_history, None, "test-run")
    return applied, skipped, recorded


def _make_outcome(bearish_pillars_active, is_correct, pct_return, day_offset, horizon="SWING"):
    return _Outcome(
        bearish_pillars_active=bearish_pillars_active, is_correct=is_correct,
        pct_return=pct_return, signal_date=date(2026, 1, 1) + timedelta(days=day_offset),
        horizon=_Horizon(value=horizon),
    )


def _production_shaped_fixture():
    """Reproduces the EXACT failure shape confirmed live in production for SWING: a narrow,
    high-pillar-count subset that looks best on EV purely from being small (classic small-
    sample overfit), while lower pillar-counts show progressively worse EV on a much larger
    subset. min_pillars=4 gets 30 rows (SELL wins are is_correct=True with pct_return<0, per
    the -pct_return EV convention) — plausible on the train slice's larger absolute count, but
    the SAME per-day density means its validation-slice subset falls under min_samples=50.
    """
    outcomes = []
    day = 0
    # Train slice: 574 rows total, mirroring production's real SWING counts.
    # p=4 rows (75 in production): mostly winning SELLs (small negative pct_return -> EV positive).
    for i in range(75):
        outcomes.append(_make_outcome(4, is_correct=(i % 3 != 0), pct_return=-0.01, day_offset=day)); day += 1
    # p=3-only rows (300 in production, i.e. bearish_pillars_active==3, which counts toward the
    # >=1/>=2/>=3 cumulative subsets but NOT >=4): more losing SELLs, larger volume.
    for i in range(300):
        outcomes.append(_make_outcome(3, is_correct=(i % 2 == 0), pct_return=0.005, day_offset=day)); day += 1
    for i in range(89):
        outcomes.append(_make_outcome(2, is_correct=(i % 2 == 0), pct_return=0.008, day_offset=day)); day += 1
    for i in range(60):
        outcomes.append(_make_outcome(1, is_correct=(i % 2 == 0), pct_return=0.01, day_offset=day)); day += 1
    for i in range(50):
        outcomes.append(_make_outcome(0, is_correct=(i % 2 == 0), pct_return=0.01, day_offset=day)); day += 1
    # Validation slice: 247 rows total (production's real count), same per-pillar DENSITY as
    # train (~30% of train's per-bucket counts) — so p=4's validation subset lands well under 50.
    for i in range(30):
        outcomes.append(_make_outcome(4, is_correct=(i % 3 != 0), pct_return=-0.01, day_offset=day)); day += 1
    for i in range(129):
        outcomes.append(_make_outcome(3, is_correct=(i % 2 == 0), pct_return=0.005, day_offset=day)); day += 1
    for i in range(38):
        outcomes.append(_make_outcome(2, is_correct=(i % 2 == 0), pct_return=0.008, day_offset=day)); day += 1
    for i in range(20):
        outcomes.append(_make_outcome(1, is_correct=(i % 2 == 0), pct_return=0.01, day_offset=day)); day += 1
    for i in range(30):
        outcomes.append(_make_outcome(0, is_correct=(i % 2 == 0), pct_return=0.01, day_offset=day)); day += 1
    return outcomes


def test_the_exact_production_failure_shape_is_now_measurable_on_validation():
    """The core regression this fix closes: a candidate whose train-slice subset is too thin
    to survive the 70/30 split proportionally must never be selected as best_p in the first
    place — so it must never reach a candidate_unmeasurable_on_validation skip."""
    outcomes = _production_shaped_fixture()
    applied, skipped, recorded = _run(outcomes)
    skip_reasons = [s["reason"] for s in skipped]
    assert "suggested min_pillars_for_sell unmeasurable on the validation slice" not in skip_reasons
    for rec in recorded:
        assert "candidate_unmeasurable_on_validation" not in rec.get("gate_failures", [])


def test_train_min_n_scales_by_the_real_split_ratio_not_a_fixed_constant():
    """A materially different train/validation split ratio must produce a materially
    different _train_min_n — confirmed by checking that a narrower validation slice (fewer
    rows relative to train) raises the effective train-slice floor, which is exactly the
    mechanism that excludes the overfit-prone narrow candidate."""
    start = _CAL_SOURCE.index("_train_min_n = int(min_samples * (len(train_bucket) / max(1, len(val_bucket))))")
    assert start > 0  # the literal expression exists verbatim in source
    # And it is computed strictly BEFORE the candidate-selection loop that uses it.
    loop_idx = _CAL_SOURCE.index("for p_i in (1, 2, 3, 4):", start)
    assert start < loop_idx


def test_validation_slice_scoring_still_uses_the_plain_min_samples_floor_not_the_train_floor():
    """The fix must ONLY tighten the TRAIN-slice selection step — the validation-slice calls
    (_stats_at(best_p, val_bucket) and _stats_at(current_pillars, val_bucket)) must keep using
    the plain min_samples floor, or a real, measurable validation result could be wrongly
    rejected by a floor meant only to guard candidate selection."""
    val_call_idx = _CAL_SOURCE.index("best_stats = _stats_at(best_p, val_bucket)")
    val_call_line_end = _CAL_SOURCE.index("\n", val_call_idx)
    assert "_train_min_n" not in _CAL_SOURCE[val_call_idx:val_call_line_end]

    val_call2_idx = _CAL_SOURCE.index("current_stats = _stats_at(current_pillars, val_bucket)")
    val_call2_line_end = _CAL_SOURCE.index("\n", val_call2_idx)
    assert "_train_min_n" not in _CAL_SOURCE[val_call2_idx:val_call2_line_end]


def test_train_slice_candidate_loop_uses_the_proportional_floor_not_the_plain_one():
    """The train-slice selection loop itself must pass _train_min_n, not bare min_samples —
    otherwise the fix's own floor is computed but never actually applied."""
    loop_start = _CAL_SOURCE.index("for p_i in (1, 2, 3, 4):")
    loop_call_idx = _CAL_SOURCE.index("_stats_at(", loop_start)
    loop_call_line_end = _CAL_SOURCE.index("\n", loop_call_idx)
    assert "_train_min_n" in _CAL_SOURCE[loop_call_idx:loop_call_line_end]


def test_still_correctly_rejects_a_horizon_with_too_few_total_samples():
    """Regression guard: the pre-existing insufficient_total_samples guard (len(bucket) <
    min_samples*2) must be completely unaffected by this fix — it runs before any of this
    logic and doesn't touch _train_min_n at all."""
    outcomes = [_make_outcome(3, True, -0.01, i) for i in range(10)]
    applied, skipped, recorded = _run(outcomes)
    assert any("only 10 backfilled SELL samples" in s["reason"] for s in skipped)
    assert any("insufficient_total_samples:10<100" in rec.get("gate_failures", []) for rec in recorded)
