"""AUD-BT-* — the 2026-09-08 backtest-harness audit fixes.

Four findings, three of them ONE failure wearing three hats: **a required input silently
defaulting to a plausible wrong value.** In each case the live caller supplies it, the harness
caller forgot, and the default was itself VALID — so nothing raised, nothing logged, and the
output looked entirely reasonable.

    cfg["market"]           -> "US"      (AUD-BT-HKCFGDEFAULT)
    signal_data["horizon"]  -> "SWING"   (AUD-BT-ALERTHORIZON)

Plus a scale bug that made the only risk-side promotion check unconditionally pass
(AUD-BT-WORSTTRADESCALE), and a split-session bug that put every HK replay in the lunch break
(AUD-BT-HKLUNCHBREAK).

WHY THE EXISTING TESTS MISSED ALL OF THIS — the lesson worth keeping:
  * `test_backtest_scorer_sweep_route.py:36` asserted the source TEXT `'("US", "HK")'` appears.
    It proved HK was *mentioned*, never that it *worked*.
  * `test_gate_harness_extended.py:249` asserted only `hkt.hour == 12` — precisely the weak
    check the sibling US test's own docstring calls insufficient ("rather than just asserting
    hour==12"). The real boundary-math test existed for US and was never mirrored for HK's
    split session.

So the tests below assert BEHAVIOUR against the real market-hours function wherever possible,
not the presence of a string.
"""
import pathlib
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

HARNESS_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/backtest/gate_harness.py"
).read_text()
PROMO_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/backtest/promotion_gate.py"
).read_text()
PT_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/paper_trading_engine.py"
).read_text()
PP_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/api/paper_portfolio.py"
).read_text()
SCHED_SRC = pathlib.Path(
    pathlib.Path(__file__).resolve().parents[1] / "src/services/scheduler.py"
).read_text()


def _fn_src(src: str, name: str) -> str:
    """A function's body, tolerating it being the LAST function in the file.

    `src.index("\ndef ", i)` raises ValueError when nothing follows — replay_alert_gate is the
    final function in gate_harness.py, so the naive form failed against correct code. This repo
    has hit the fixed-window/slice-boundary trap repeatedly; slice to EOF instead.
    """
    i = src.index(f"def {name}")
    nxt = src.find("\ndef ", i + 10)
    return src[i:nxt if nxt != -1 else len(src)]


def _load_entry_as_of():
    """Exec the real _entry_as_of out of source — it is pure (datetime/zoneinfo only)."""
    i = HARNESS_SRC.index("def _entry_as_of")
    body = HARNESS_SRC[i:HARNESS_SRC.index("\ndef ", i + 10)]
    ns: dict = {}
    exec(  # noqa: S102 — one pure function, no imports beyond stdlib
        "from datetime import datetime, date, timezone\nfrom zoneinfo import ZoneInfo\n" + body,
        ns,
    )
    return ns["_entry_as_of"]


_entry_as_of = _load_entry_as_of()

# HKEX's real split session, mirrored from _is_market_hours (paper_trading_engine.py:1176-1180):
#     (09:30 <= t < 12:00) or (13:00 <= t < 16:00)
def _hk_market_hours(dt: datetime) -> bool:
    t = dt.astimezone(ZoneInfo("Asia/Hong_Kong"))
    mins = t.hour * 60 + t.minute
    return (570 <= mins < 720) or (780 <= mins < 960)


def _us_market_hours(dt: datetime) -> bool:
    t = dt.astimezone(ZoneInfo("America/New_York"))
    mins = t.hour * 60 + t.minute
    return 570 <= mins < 960


# ── AUD-BT-HKLUNCHBREAK ──────────────────────────────────────────────────────────────────

def test_hk_replay_instant_is_inside_the_morning_session():
    """THE BUG: 12:00 HKT is the EXCLUSIVE end of the morning session and the afternoon has not
    opened, so it fell in NEITHER window and the first hard gate rejected 100% of HK
    candidates."""
    assert _hk_market_hours(_entry_as_of(date(2026, 9, 4), "HK")) is True


def test_the_old_hk_instant_really_was_broken():
    """Pins the mechanism, so a future reader sees WHY 11:00 and does not "tidy" it back."""
    broken = datetime(2026, 9, 4, 12, 0, tzinfo=ZoneInfo("Asia/Hong_Kong"))
    assert _hk_market_hours(broken) is False, "12:00 HKT must be the lunch break"


def test_us_replay_instant_unchanged_and_still_mid_session():
    """US was always fine — the fix must not disturb it."""
    a = _entry_as_of(date(2026, 9, 4), "US")
    assert a.astimezone(ZoneInfo("America/New_York")).strftime("%H:%M") == "12:00"
    assert _us_market_hours(a) is True


def test_hk_instant_clears_both_session_boundaries_with_margin():
    """Not just 'inside' — comfortably inside, which is what the docstring promises. An instant
    at 09:30 or 11:59 would technically pass while sitting on an edge window."""
    t = _entry_as_of(date(2026, 9, 4), "HK").astimezone(ZoneInfo("Asia/Hong_Kong"))
    mins = t.hour * 60 + t.minute
    assert mins - 570 >= 60, "must be >=1h after the 09:30 open"
    assert 720 - mins >= 30, "must be >=30m before the 12:00 morning close"


def test_both_markets_return_utc_aware_instants():
    for mkt in ("US", "HK"):
        a = _entry_as_of(date(2026, 9, 4), mkt)
        assert a.tzinfo is not None
        assert a.utcoffset().total_seconds() == 0, "must be normalised to UTC"


def test_the_false_docstring_claim_is_gone():
    """The docstring asserted the instant was 'comfortably clear of both the market-hours
    boundary' — true for US, FALSE for HK, and that false claim is why nobody re-checked."""
    fn = _fn_src(HARNESS_SRC, "_entry_as_of")
    assert "AUD-BT-HKLUNCHBREAK" in fn
    assert "lunch break" in fn.lower(), "the mechanism must be named in the source"


# ── AUD-BT-HKCFGDEFAULT ──────────────────────────────────────────────────────────────────

def test_no_harness_route_hand_rolls_a_cfg_merge_any_more():
    """All 9 sites built `{**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}` and passed
    `market` as a SEPARATE argument that never reached the dict."""
    bad = "{**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}"
    assert bad not in PP_SRC, "a harness route is still hand-merging without market"
    assert "{**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(_style, {})}" not in SCHED_SRC


def test_all_eight_paper_portfolio_routes_use_the_helper():
    """Counted exactly: 8 backtest routes. An exact count catches both a missed route and a
    stray extra one."""
    assert PP_SRC.count("resolve_backtest_config(style, market)") == 8


def test_the_weekly_promotion_gate_uses_the_helper():
    """This is the site whose HK half produced NULL results every Sunday for months."""
    i = SCHED_SRC.index('for _market in ("US", "HK"):')
    block = SCHED_SRC[i:i + 400]
    assert "_resolve_bt_cfg(_style, _market)" in block


def test_the_helper_actually_sets_market_and_style():
    """The whole point. Asserted against the real function body, not its name."""
    fn = _fn_src(PT_SRC, "resolve_backtest_config")
    assert '"market": market' in fn
    assert '"trading_style": style' in fn
    assert "resolve_entry_config(base)" in fn, (
        "must route through the live precedence resolver rather than a 4th hand-rolled merge"
    )


def test_the_helper_inherits_hk_overrides_rather_than_re_applying_them():
    """resolve_entry_config() already applies _HK_MARKET_OVERRIDES when market == 'HK'. Only 1
    of the 8 routes used to do that by hand; that branch is now redundant and removed."""
    assert "cfg.update(_HK_MARKET_OVERRIDES)" in PT_SRC, "the resolver must still apply them"
    assert "base_cfg = {**base_cfg, **_HK_MARKET_OVERRIDES}" not in PP_SRC


def test_the_non_backtest_endpoint_was_left_alone():
    """paper_portfolio.py:1784 already set market/style correctly and is NOT a harness route —
    a fix that 'tidied' it would be churn."""
    assert '"trading_style": style, "market": market}' in PP_SRC


# ── AUD-BT-WORSTTRADESCALE ───────────────────────────────────────────────────────────────

_TOL = 10.0  # DEFAULT_MAX_WORST_TRADE_REGRESSION_PCT, in PERCENTAGE POINTS


def _regression_pp(baseline_worst_frac: float, candidate_worst_frac: float) -> float:
    """Mirrors the fixed comparison: fractions converted to pp before differencing."""
    return (baseline_worst_frac * 100.0) - (candidate_worst_frac * 100.0)


def test_the_gate_can_now_actually_reject():
    """THE BUG: `returns` holds FRACTIONS, so `min()` was e.g. -0.5456 (= -54.56%). A
    difference of two fractions is bounded by ~1.0 in practice, against a 10.0 PERCENTAGE-POINT
    tolerance — `regression <= 10.0` was UNCONDITIONALLY TRUE."""
    # A candidate whose worst trade is 20pp worse than baseline must now fail.
    assert _regression_pp(-0.10, -0.30) == pytest.approx(20.0)
    assert _regression_pp(-0.10, -0.30) > _TOL, "a 20pp regression must be rejectable"


def test_the_real_production_values_would_have_been_inert_before():
    """Observed worst-trade values from tune_history, all fractions. Largest achievable
    |regression| among them was ~0.7 — 14x below the tolerance."""
    observed = [-0.5456, -0.4366, -0.1742, -0.1314, -0.0091, -0.0017]
    worst, best = min(observed), max(observed)
    assert abs(best - worst) < 1.0, "precondition: raw fractions span under 1.0"
    # Pre-fix (no conversion) the check could never trip...
    assert (best - worst) <= _TOL
    # ...post-fix the same pair is a real 54pp regression.
    assert _regression_pp(best, worst) > _TOL


def test_a_genuinely_similar_candidate_still_passes():
    """The fix must not make the gate reject everything — that would be the opposite failure."""
    assert _regression_pp(-0.20, -0.22) == pytest.approx(2.0)
    assert _regression_pp(-0.20, -0.22) <= _TOL


def test_an_improvement_passes_as_a_negative_regression():
    """A candidate with a BETTER worst trade must never be blocked."""
    assert _regression_pp(-0.30, -0.10) == pytest.approx(-20.0)
    assert _regression_pp(-0.30, -0.10) <= _TOL


def test_the_conversion_is_present_and_named_in_the_source():
    assert "_FRACTION_TO_PCT" in PROMO_SRC
    assert "min(candidate_val.returns) * _FRACTION_TO_PCT" in PROMO_SRC
    assert "min(baseline_val.returns) * _FRACTION_TO_PCT" in PROMO_SRC


def test_the_false_premise_comment_is_corrected():
    """The constant's comment used to assert 'these are already pct returns'. That claim is
    exactly what made the bug invisible for months."""
    # The phrase survives ONLY inside the AUD-BT-WORSTTRADESCALE comment that quotes it as
    # the historical false claim. What must be gone is it standing as an active assertion —
    # i.e. it must be preceded by "used to claim".
    assert "AUD-BT-WORSTTRADESCALE" in PROMO_SRC
    for line in PROMO_SRC.splitlines():
        if "these are already pct returns" in line:
            assert "used to claim" in line, (
                "the false premise must only appear as a quoted historical claim"
            )
    assert "THEY\n# ARE NOT" in PROMO_SRC or "ARE NOT" in PROMO_SRC


def test_persisted_values_use_the_converted_scale():
    """tune_history.approx_worst_trade_pct was storing 100x-too-small values under a *_pct
    column name. Both persisted fields read from the converted dict."""
    assert 'approx_worst_trade_pct=(worst_trade_check or {}).get("candidate_worst_trade_pct")' in PROMO_SRC
    assert 'baseline_worst_trade_pct=(worst_trade_check or {}).get("baseline_worst_trade_pct")' in PROMO_SRC
    i = PROMO_SRC.index("candidate_worst = min(")
    block = PROMO_SRC[i:PROMO_SRC.index("within_tolerance", i)]
    assert "_FRACTION_TO_PCT" in block, "the dict the DB reads must hold pp, not fractions"


def test_the_sibling_that_got_it_right_is_unchanged():
    """gate_harness's _passes_promotion_margin already converted explicitly. Two functions in
    one promotion path disagreeing on the scale of the same list WAS the bug."""
    assert "* 100  # returns are stored as fractions" in HARNESS_SRC


# ── AUD-BT-ALERTHORIZON ──────────────────────────────────────────────────────────────────

def test_replay_alert_gate_passes_the_horizon():
    """_is_conviction_buy reads `signal_data.get("horizon", "SWING")`, so an absent key made
    EVERY style replay under SWING's rules."""
    fn = _fn_src(HARNESS_SRC, "replay_alert_gate")
    assert '"horizon": style,' in fn


def test_the_default_that_caused_it_still_exists_upstream():
    """The fix is at the CALL SITE, deliberately: _is_conviction_buy's default is relied on by
    other callers, so changing it there would be a wider behavioural change."""
    assert 'signal_data.get("horizon", "SWING")' in SCHED_SRC


def test_growth_and_swing_really_do_differ():
    """If the two styles had identical rules this finding would be cosmetic. They do not —
    GROWTH has both a looser uptrend requirement and a wider RSI band."""
    fn = _fn_src(SCHED_SRC, "_is_conviction_buy")
    assert "GROWTH" in fn, "the gate must branch on style at all"
    assert fn.count("GROWTH") >= 2, "expected the documented 4a/4b GROWTH exemptions"
