"""Tests for AUD283-MLWEIGHT-RATCHET — calibrate_ml_weight() previously compared its candidate
fusion-weight cap against a hardcoded neutral 0.5 baseline instead of the actual LIVE cap
(prev_cap, already fetched at the top of the real function but only used for
TuneHistory.old_value bookkeeping). A candidate that beat 0.5 but was genuinely WORSE than the
cap already in production could still get promoted — the self-tuning mechanism could silently
walk this parameter in a bad direction indefinitely, with no requirement to ever beat where it
actually is.

calibration.py can't be imported directly in this environment (it needs common.jwt_auth /
FastAPI Depends / db, none for-real-installed here) — the function's real body (from the
`rows`-not-empty check through both return branches) is extracted via exec() and run against
real synthetic `rows`/`price_rows` fixtures, matching test_calibrate_ta_weights_validation.py's
established source-text-extraction convention for exactly this class of Docker-only-dependency
constraint.
"""
import pathlib
from datetime import date, datetime, timedelta, timezone

_CAL_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "calibration.py"
_CAL_SOURCE = _CAL_PATH.read_text()

_OUTCOME_HOLD_DAYS = {"SHORT": 7, "SWING": 14, "LONG": 28, "GROWTH": 14}
_HOLD = _OUTCOME_HOLD_DAYS["SWING"]


class _FakeHorizon:
    def __init__(self, value):
        self.value = value


class _FakeSignal:
    def __init__(self, stock_id, ts, horizon, ml_prob, ta_score):
        self.stock_id = stock_id
        self.ts = ts
        self.horizon = _FakeHorizon(horizon)
        self.reasons = {"ml_probability": ml_prob, "ta_score": ta_score}


class _FakePrice:
    def __init__(self, stock_id, ts, close):
        self.stock_id = stock_id
        self.ts = ts
        self.close = close


def _extract_calibrate_ml_weight_core():
    """Pulls the computational core of calibrate_ml_weight() — from `if not rows:` through the
    end of the function — out of calibration.py, re-wrapped as a standalone function taking
    `rows`/`price_rows`/`prev_cap` as parameters and every side-effecting dependency
    (set_ml_weight_global_cap, _record_tune_history, log) injected as fakes."""
    # Starts right AFTER the real price_rows = session.execute(...) query (which references
    # cutoff/session/select — all real-DB constructs this test bypasses by injecting
    # price_rows directly, already "fetched") — at the point price_rows is first consumed.
    # _pts/_pclose is not unique to this function elsewhere in the file, so anchor the search
    # to start from inside calibrate_ml_weight itself (found via its own @router.post decorator)
    # rather than risk matching an earlier, unrelated function's identical-looking line.
    fn_start = _CAL_SOURCE.index('@router.post("/calibrate_ml_weight")')
    start = _CAL_SOURCE.index("    _pts: dict[int, list] = {}", fn_start)
    end = _CAL_SOURCE.index('\n\n\n@router.post("/calibrate_ta_weights")')
    body = _CAL_SOURCE[start:end]
    dedented = "\n".join(line[4:] if line.startswith("    ") else line for line in body.splitlines())
    func_source = (
        "def _core(rows, price_rows, prev_cap, set_ml_weight_global_cap, _record_tune_history, log):\n"
        + "\n".join("    " + line if line.strip() else line for line in dedented.splitlines())
    )
    namespace = {
        "datetime": datetime, "timezone": timezone, "timedelta": timedelta, "date": date,
        "_OUTCOME_HOLD_DAYS": _OUTCOME_HOLD_DAYS, "bisect": __import__("bisect"),
        "lookback_days": 180,  # only used in the returned dict for display; real value irrelevant here
        "session": None,  # real function's own FastAPI route parameter; only forwarded verbatim
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_core"]


_core = None


def _run_core(rows, price_rows, prev_cap):
    global _core
    if _core is None:
        _core = _extract_calibrate_ml_weight_core()

    recorded = {}

    def _fake_record_tune_history(session, run_id, parameter_class, parameter_name, style, market,
                                   old_value, new_value, train_window, validation_window,
                                   train_ev_pct, validation_ev_pct, baseline_validation_ev_pct,
                                   validation_n, promoted, gate_failures):
        recorded.update(locals())

    applied = {}

    def _fake_set_cap(w):
        applied["weight"] = w

    class _FakeLog:
        def info(self, *a, **kw):
            pass

    result = _core(rows, price_rows, prev_cap, _fake_set_cap, _fake_record_tune_history, _FakeLog())
    return result, recorded, applied


def _make_row(sid, sig_date, ml_prob, ta_score, entry_close, exit_close):
    """One (Signal, symbol) tuple plus its matching entry/exit Price rows — mirrors the real
    query shape (select(Signal, Stock.symbol)) and the real entry lookup convention
    (_first_close_after(sig.stock_id, signal_date), i.e. strictly AFTER signal_date — T+1)."""
    sig = _FakeSignal(sid, datetime.combine(sig_date, datetime.min.time()), "SWING", ml_prob, ta_score)
    prices = [
        _FakePrice(sid, sig_date + timedelta(days=1), entry_close),
        _FakePrice(sid, sig_date + timedelta(days=1 + _HOLD), exit_close),
    ]
    return (sig, "TEST"), prices


def _build_fixture(calib_specs, val_specs):
    """calib_specs/val_specs: list of (ml_prob, ta_score, entry_close, exit_close) tuples.
    Calibration rows get earlier dates (older 70%), validation rows get later dates (newer
    30%) — the real function's own chronological 70/30 split (observations.sort by date, then
    split = int(len * 0.7)) depends on this ordering."""
    rows, price_rows = [], []
    base_date = date(2026, 1, 1)
    sid = 1000
    for i, (ml_prob, ta_score, entry_close, exit_close) in enumerate(calib_specs):
        row, prices = _make_row(sid, base_date + timedelta(days=i), ml_prob, ta_score, entry_close, exit_close)
        rows.append(row)
        price_rows.extend(prices)
        sid += 1
    val_base = base_date + timedelta(days=len(calib_specs) + 30)
    for i, (ml_prob, ta_score, entry_close, exit_close) in enumerate(val_specs):
        row, prices = _make_row(sid, val_base + timedelta(days=i), ml_prob, ta_score, entry_close, exit_close)
        rows.append(row)
        price_rows.extend(prices)
        sid += 1
    return rows, price_rows


# 50 calibration rows: ml_prob=0.9, ta_score=0.1 — under weight=1.0 (pure ML) this fires and
# nets +5%; under weight=0.0 (pure TA) it does NOT fire at all (ta_score=0.1 < 0.5), so the
# calibration-slice search settles on weight=1.0 as "optimal" regardless of what the
# validation slice later shows — the two slices are decision-relevant-independent by
# construction, avoiding the "both comparisons move together" trap.
_CALIB_ML_HEAVY = [(0.9, 0.1, 100.0, 105.0)] * 50


def test_candidate_that_beats_the_live_cap_gets_promoted():
    """A genuine live cap (weight=0.9, mostly-TA) fires on the SAME validation rows but nets a
    WORSE return than the candidate's pure-ML optimum — must promote."""
    # Validation: ml_prob=0.95 (fires under candidate w=1.0, nets +8%), ta_score=0.6 (also
    # clears 0.5 alone, so the w=0.9 live cap ALSO fires: fused=0.9*0.95+0.1*0.6=0.915>0.5) but
    # realizes a worse +2% since we vary entry/exit independently per weight isn't possible in
    # one fixture row — instead give ALL validation rows the same realized return (+8%) so
    # whichever weight fires sees the identical return; the live cap fires too here (its own
    # fused value clears 0.5), so this specific fixture can't separate "which weight is
    # better" — use a DIFFERENT fixture where only the candidate's blend clears 0.5.
    val_specs = [(0.9, 0.2, 100.0, 108.0)] * 20  # fused: w=1.0 -> 0.9 (fires); w=0.3 -> 0.9*0.3+0.2*0.7=0.41 (does NOT fire)
    rows, price_rows = _build_fixture(_CALIB_ML_HEAVY, val_specs)

    result, recorded, applied = _run_core(rows, price_rows, prev_cap=0.3)

    assert result["applied"] is True
    assert applied["weight"] == result["optimal_weight"]
    assert recorded["promoted"] is True
    assert recorded["gate_failures"] == []
    # The live cap (w=0.3) never fires on these validation rows at all -> baseline_ev=0.0,
    # and the candidate's own real, positive return must exceed that.
    assert recorded["baseline_validation_ev_pct"] == 0.0
    assert recorded["validation_ev_pct"] > 0.0


def test_candidate_that_ties_the_real_live_cap_is_correctly_rejected_not_promoted():
    """The exact regression this fix closes, made concrete: a candidate whose validation-slice
    return merely TIES the real live cap's own return (2.05% both) is correctly rejected — the
    gate requires a STRICT improvement (candidate_ev > baseline_ev), not "beats a fabricated
    neutral 0.5." Under the OLD code, this same candidate would have been compared against a
    bare weight=0.5 blend instead of the real cap, very plausibly showing a large apparent
    "lift" over that arbitrary reference point and getting promoted — even though it offers
    zero real improvement over what's already live."""
    calib_specs = [(0.9, 0.05, 100.0, 103.0)] * 50
    val_specs = [(0.9, 0.55, 100.0, 102.0)] * 20
    rows, price_rows = _build_fixture(calib_specs, val_specs)

    # prev_cap=0.9 happens to realize the IDENTICAL validation-slice return as the candidate's
    # own optimal weight on this fixture (both fire and land on the same fused blend outcome).
    result, recorded, applied = _run_core(rows, price_rows, prev_cap=0.9)

    assert result["applied"] is False
    assert recorded["promoted"] is False
    assert recorded["validation_ev_pct"] == recorded["baseline_validation_ev_pct"]
    assert "applied" not in applied


def test_candidate_that_genuinely_beats_a_worse_real_live_cap_on_the_same_fixture_is_promoted():
    """The mirror case, on the IDENTICAL fixture shape as the tie-rejection test above except
    for prev_cap itself: when the real live cap is genuinely worse (not tied), the candidate
    correctly promotes — proving the rejection above was driven by the real comparison, not by
    some unrelated property of the fixture."""
    calib_specs = [(0.9, 0.05, 100.0, 103.0)] * 50
    val_specs = [(0.9, 0.55, 100.0, 102.0)] * 20
    rows, price_rows = _build_fixture(calib_specs, val_specs)

    result, recorded, applied = _run_core(rows, price_rows, prev_cap=0.1)

    assert result["applied"] is True
    assert recorded["promoted"] is True
    assert recorded["validation_ev_pct"] > recorded["baseline_validation_ev_pct"]
    assert applied["weight"] == result["optimal_weight"]


def test_baseline_reflects_the_real_prev_cap_not_a_hardcoded_constant():
    """Direct, unambiguous proof: run the IDENTICAL rows/price_rows through two calls that
    differ ONLY in prev_cap, using a validation slice where different weights produce
    genuinely different fired-sets (and thus different baseline_validation_ev_pct values) —
    if the code still hardcoded 0.5, both calls would report the IDENTICAL baseline regardless
    of prev_cap. Deliberately asserts a real inequality (not an exact literal return value,
    which the real function's own 70/30 chronological split can shift by a row or two at the
    calibration/validation boundary) — the property under test is "the baseline changes when
    prev_cap changes," not a specific hand-computed percentage."""
    # ml_prob=0.9, ta_score=0.2: fused = w*0.9 + (1-w)*0.2 = 0.2 + 0.7w.
    #   w=0.9 -> fused=0.83 (fires, clears 0.5)
    #   w=0.2 -> fused=0.34 (does NOT fire)
    val_specs = [(0.9, 0.2, 100.0, 106.0)] * 20
    rows, price_rows = _build_fixture(_CALIB_ML_HEAVY, val_specs)

    _, recorded_fires, _ = _run_core(rows, price_rows, prev_cap=0.9)
    _, recorded_no_fire, _ = _run_core(rows, price_rows, prev_cap=0.2)

    assert recorded_fires["old_value"]["ml_weight_global_cap"] == 0.9
    assert recorded_no_fire["old_value"]["ml_weight_global_cap"] == 0.2
    # prev_cap=0.9 fires and realizes a real positive return; prev_cap=0.2 never fires at all
    # (fused=0.34 < 0.5 on every validation row) -> baseline_ev is exactly 0.0.
    assert recorded_fires["baseline_validation_ev_pct"] > 0.0
    assert recorded_no_fire["baseline_validation_ev_pct"] == 0.0
    assert recorded_fires["baseline_validation_ev_pct"] != recorded_no_fire["baseline_validation_ev_pct"]


def test_no_baseline_cap_yet_auto_promotes_and_records_why():
    """prev_cap=None (a genuinely fresh deploy, no override ever persisted) must NOT be
    compared against a fabricated 0.5 as if it were real production state — it should
    auto-promote (nothing real to beat, matching ml-prediction's own ev_gate.py precedent for
    the identical "first-ever tune" situation) and record gate_failures explicitly rather than
    silently treating this as an ordinary pass.

    Deliberately engineered so the neutral weight=0.5 fallback ITSELF fires and realizes a
    BETTER return (+10%) than the candidate's own optimal weight (+9.76%) — an earlier version
    of this test used a fixture where the candidate happened to still beat 0.5 anyway, which
    silently passed even after removing the `prev_cap is None` bypass entirely (the exact
    "still passes after sabotage" trap this repo's own testing discipline watches for). This
    version genuinely requires the bypass: without it, this candidate would be REJECTED against
    the neutral fallback, not promoted."""
    val_specs = [(0.9, 0.2, 100.0, 110.0)] * 20
    rows, price_rows = _build_fixture(_CALIB_ML_HEAVY, val_specs)

    result, recorded, applied = _run_core(rows, price_rows, prev_cap=None)

    assert result["applied"] is True
    assert recorded["promoted"] is True
    assert recorded["gate_failures"] == ["no_baseline_cap:first_tune"]
    assert recorded["old_value"]["ml_weight_global_cap"] is None
    # The load-bearing assertion: the candidate's own EV is LOWER than what the neutral 0.5
    # fallback would have realized — promotion only happens because prev_cap is None bypasses
    # that comparison entirely, not because the candidate genuinely won it.
    assert recorded["validation_ev_pct"] < recorded["baseline_validation_ev_pct"]


def test_insufficient_validation_samples_still_records_the_real_prev_cap_as_old_value():
    """A too-thin validation slice must still record the REAL prev_cap in old_value, not a
    fabricated 0.5 — this path is untouched by the fix but is worth guarding, since the same
    old_value= expression is used at every _record_tune_history call site in this function.

    Uses a SMALL total observation count (10 rows, no separate "validation" rows added) rather
    than a large calibration set + a couple of extra rows — the real function's own 70/30
    chronological split is computed over ALL observations together (split =
    int(len(observations) * 0.7)), so a large calibration set plus a tiny explicit validation
    addition does NOT reliably land below MIN_VAL_SAMPLES (a first attempt at this test found
    real spillover rows from the calibration slice crossing the floor from the OTHER side —
    worth noting for future test-writing on this same split logic)."""
    calib_specs = [(0.9, 0.1, 100.0, 105.0)] * 10  # split = int(10*0.7) = 7 -> validation gets 3 rows
    rows, price_rows = _build_fixture(calib_specs, [])

    result, recorded, _ = _run_core(rows, price_rows, prev_cap=0.42)

    assert result["applied"] is False
    assert "validation-slice observations" in result["reason"]
    assert "insufficient_validation_samples" in recorded["gate_failures"][0]
    assert recorded["old_value"]["ml_weight_global_cap"] == 0.42
