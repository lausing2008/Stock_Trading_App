"""AUD-GEXCORROBORATE-UNMEASURED: persist whether real GEX corroborated a gamma alert.

check_gamma_unwind_alerts() has computed GEX corroboration since AUD-GEXCORROBORATE but only
ever DISPLAYED it in the email — it was never stored. So the question that decides whether the
alert can be improved at all ("do GEX-corroborated alerts outperform uncorroborated ones?")
could not be answered from stored data, and the free OI-concentration proxy kept gating every
candidate on faith.

This matters because the proxy is explicitly NOT a real gamma calculation (see the function's
own HONEST LIMITATION docstring): identical open interest AMPLIFIES moves when dealers are
short gamma and DAMPENS them when dealers are long gamma. Only real GEX — gamma_flip in
particular — distinguishes the two. Measured performance is currently poor (gamma_unwind_calls:
24.5% win over 64 resolved outcomes), so knowing whether corroboration separates the winners is
the cheapest next step before building a gamma_flip gate.

Two columns, both nullable:
  gex_corroborated       True/False = evaluated; NULL = never evaluated (pre-fix row, UW
                         disabled, or lookup failure). The distinction is load-bearing —
                         pooling NULL with False would silently count unmeasured rows as
                         negative evidence.
  gex_nearest_level_pct  signed (level - price)/price to the NEAREST GEX level, so a future
                         analysis can re-test a different corroboration band without
                         re-firing every alert.

scheduler.py can't be imported here (apscheduler isn't installed locally), so the wiring is
verified against the real source text, matching this repo's established technique.
"""
import pathlib

_SCHEDULER = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py").read_text()
_MODELS = (pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py").read_text()


def _squeeze_model_block() -> str:
    start = _MODELS.index("class SqueezeAlertOutcome(Base):")
    return _MODELS[start:_MODELS.index("\n\nclass ", start)]


def _gamma_fn() -> str:
    start = _SCHEDULER.index("def check_gamma_unwind_alerts(")
    return _SCHEDULER[start:_SCHEDULER.index("\n\n\ndef ", start)]


# ── model ────────────────────────────────────────────────────────────────────

def test_both_columns_exist_on_the_model():
    block = _squeeze_model_block()
    assert "gex_corroborated: Mapped[bool | None]" in block
    assert "gex_nearest_level_pct: Mapped[float | None]" in block


def test_columns_are_nullable_so_null_can_mean_not_evaluated():
    block = _squeeze_model_block()
    for col in ("gex_corroborated", "gex_nearest_level_pct"):
        line = next(l for l in block.splitlines() if l.strip().startswith(f"{col}:"))
        assert "nullable=True" in line


def test_the_null_vs_false_distinction_is_documented():
    """Not decoration — an analysis that pools NULL with False turns unmeasured rows into
    negative evidence and would understate corroboration's value."""
    block = _squeeze_model_block()
    assert "not evaluated" in block
    assert "distinct from False" in block


# ── recorder ─────────────────────────────────────────────────────────────────

def _recorder() -> str:
    start = _SCHEDULER.index("def _record_squeeze_alert_outcome(")
    return _SCHEDULER[start:_SCHEDULER.index("\n\n\ndef ", start)]


def test_recorder_accepts_both_fields_defaulting_to_none():
    """Default None, never False — short_squeeze/squeeze_ignition callers don't compute GEX at
    all, and defaulting to False would make them look like measured-but-uncorroborated rows."""
    rec = _recorder()
    assert "gex_corroborated: bool | None = None" in rec
    assert "gex_nearest_level_pct: float | None = None" in rec


def test_recorder_persists_both_fields():
    rec = _recorder()
    assert "gex_corroborated=gex_corroborated" in rec
    assert "gex_nearest_level_pct=gex_nearest_level_pct" in rec


# ── the gamma alert actually populates them ──────────────────────────────────

def test_corroboration_is_recorded_as_a_real_boolean_not_only_on_success():
    """The pre-fix code only set the flag when _nearby was non-empty, so a genuine negative
    observation was indistinguishable from never having looked."""
    fn = _gamma_fn()
    assert 'cand["gex_corroborates"] = bool(_nearby)' in fn


def test_unevaluated_candidates_stay_none():
    """`continue` on a None GEX lookup must happen BEFORE the flag is assigned, so those rows
    remain NULL rather than being recorded as False."""
    fn = _gamma_fn()
    continue_idx = fn.index("if _gex is None:")
    assign_idx = fn.index('cand["gex_corroborates"] = bool(_nearby)')
    assert continue_idx < assign_idx


def test_nearest_level_pct_is_signed_and_relative():
    """Signed so 'above/below the level' survives, relative so it is comparable across
    symbols at different price scales."""
    fn = _gamma_fn()
    assert "(_closest - _price) / _price" in fn


def test_nearest_level_uses_min_by_absolute_distance():
    fn = _gamma_fn()
    assert "min(_all_levels, key=lambda lvl: abs(lvl - _price))" in fn


def test_gamma_alert_passes_both_fields_to_the_recorder():
    fn = _gamma_fn()
    assert "gex_corroborated=cand.get(\"gex_corroborates\")" in fn
    assert "gex_nearest_level_pct=cand.get(\"gex_nearest_level_pct\")" in fn


def test_short_squeeze_caller_does_not_pass_gex_fields():
    """short_squeeze has no GEX concept — it must keep recording NULL, not False."""
    start = _SCHEDULER.index("def check_short_squeeze_alerts(")
    fn = _SCHEDULER[start:_SCHEDULER.index("\n\n\ndef ", start)]
    if "_record_squeeze_alert_outcome(" in fn:
        call_idx = fn.index("_record_squeeze_alert_outcome(")
        call = fn[call_idx:call_idx + 400]
        assert "gex_corroborated=" not in call


# ── the continuous value is kept, not just the threshold ─────────────────────

def test_storing_the_raw_distance_is_justified_in_the_model():
    """Storing only the boolean would bake today's _GEX_CORROBORATE_BAND_PCT into history and
    make re-testing a different band impossible without re-firing every alert."""
    block = _squeeze_model_block()
    assert "_GEX_CORROBORATE_BAND_PCT" in block
