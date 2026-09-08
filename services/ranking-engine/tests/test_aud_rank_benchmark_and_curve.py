"""AUD-RANK-* — the Ranking / K-Score audit fixes (6 findings).

The headline finding (AUD-RANK-RSPLACEHOLDER) is the same class as the trading audit's
dead-ML-pillar: a silent fail-OPEN feeding a learner.

    _rs_score(stock_ret, None) -> (50.0, 1.0)      # fabricated "in line with sector"

`^HSI` is not in the `stocks` table, and _etf_20d_return() explicitly skips its DB path for any
"^"-prefixed ticker — so yfinance was the ONLY possible source, and yfinance rate-limits it on
essentially every cycle (28 `etf_return_fetch_failed` events in 72h of production logs).

Measured in production before the fix:
    HK rankings: 1,956 / 3,459 rows (56.5%) had rs_score EXACTLY 50.0
    US rankings:     6 / 9,328 rows ( 0.1%)  <- the control that proves it is not organic

The real damage was downstream and left no error trail: `tune_kscore_weights` measured a factor
that was constant for over half its HK sample, correctly concluded it carried no signal, and
demoted `relative_strength` from its 0.10 default to 0.0501 — the largest relative move of any
factor (every other weight stayed within a few percent of default). The optimizer was working
CORRECTLY on corrupt input.
"""
import pathlib

import pytest

import src.api.routes as routes
from src.scoring.kscore import (
    _VOL_SOFT_FLOOR,
    _WEIGHTS,
    _volatility_score_from_raw,
)

ROUTES_SRC = pathlib.Path(routes.__file__).read_text()


# ── AUD-RANK-RSPLACEHOLDER: fail closed, not open ────────────────────────────────────────

def test_missing_benchmark_returns_None_not_a_fabricated_50():
    """THE CORE FIX. 50.0 was indistinguishable from a genuine 'exactly in line with sector'
    reading, which is why 56.5% of HK rows carried it without anyone noticing."""
    assert routes._rs_score(0.05, None) == (None, None)


def test_a_real_benchmark_still_scores_normally():
    """The fix must not disturb the working path."""
    score, rank = routes._rs_score(0.10, 0.05)
    assert score is not None and rank is not None
    assert 50.0 < score <= 100.0, "outperforming the sector must score above neutral"


def test_underperformance_still_scores_below_neutral():
    score, _ = routes._rs_score(-0.10, 0.05)
    assert score is not None and score < 50.0


def test_exactly_in_line_still_scores_50():
    """50.0 remains a MEANINGFUL value — reachable only by genuinely matching the benchmark.
    That is precisely why the placeholder was so damaging: it forged this exact signal."""
    score, _ = routes._rs_score(0.05, 0.05)
    assert score == pytest.approx(50.0, abs=0.01)


def test_None_is_safe_for_every_downstream_consumer():
    """Failing closed is only correct because None was ALREADY the contract for a too-short
    price history. Pinned so a future change cannot reintroduce a neutral default."""
    assert "return None, None" in ROUTES_SRC
    # _stock_rs already returned (None, None) for < 21 bars, so consumers must handle it.
    fn = ROUTES_SRC[ROUTES_SRC.index("def _stock_rs("):ROUTES_SRC.index("@router.get(\"/sector_rotation\")")]
    assert "return None, None" in fn


def test_rs_score_signature_admits_None():
    """A `-> tuple[float, float]` annotation would be a live lie after this fix."""
    fn = ROUTES_SRC[ROUTES_SRC.index("def _rs_score("):]
    sig = fn[:fn.index('"""')]
    assert "float | None" in sig


# ── AUD-RANK-RSPLACEHOLDER: the HK benchmark must be DB-resolvable ───────────────────────

def test_hk_benchmark_tries_a_db_backed_proxy_before_the_raw_index():
    """^HSI is an INDEX, so _etf_20d_return()'s DB path can never serve it. 2800.HK (Tracker
    Fund of Hong Kong) tracks the same index and IS an ordinary equity, so it can be ingested
    and read from the DB like every sector ETF."""
    assert routes._HK_BENCHMARK_PROXIES[0] == "2800.HK", "the DB-backed proxy must be tried FIRST"
    assert "^HSI" in routes._HK_BENCHMARK_PROXIES, "keep the raw index as a last resort"


def test_the_db_path_is_still_skipped_for_caret_tickers():
    """Pins WHY the proxy is needed — this is the line that made ^HSI yfinance-only."""
    fn = ROUTES_SRC[ROUTES_SRC.index("def _etf_20d_return("):ROUTES_SRC.index("def _prewarm_etf_cache(")]
    assert 'not ticker.startswith("^")' in fn


def test_hk_benchmark_exhaustion_is_logged():
    """A fleet-wide benchmark outage must not be silent — that was the original sin."""
    fn = ROUTES_SRC[ROUTES_SRC.index("def _stock_rs("):ROUTES_SRC.index("@router.get(\"/sector_rotation\")")]
    assert "ranking.hk_benchmark_unavailable" in fn


# ── AUD-RANK-BENCHSTALE: enough bars != current bars ────────────────────────────────────

def test_stale_benchmark_is_rejected_rather_than_used():
    """XLP sat 102 days stale (active=false, silently dropped from ingestion) while still
    producing a real-looking Consumer-Staples relative strength computed entirely from stale
    history. _load_prices' own lookback*2 window still caught those old bars, which is exactly
    how it produced a plausible number."""
    fn = ROUTES_SRC[ROUTES_SRC.index("def _etf_20d_return("):ROUTES_SRC.index("def _prewarm_etf_cache(")]
    assert "_BENCHMARK_MAX_STALE_DAYS" in fn
    assert "ranking.benchmark_stale" in fn


def test_staleness_window_clears_a_holiday_weekend():
    """Narrower than this would reject live benchmarks over any long market closure."""
    assert routes._BENCHMARK_MAX_STALE_DAYS >= 5


def test_staleness_window_would_have_caught_xlp():
    """XLP was 102 days stale. Any threshold this side of ~90 days catches it, but the point of
    the assertion is that the window is far tighter than the failure it is guarding."""
    assert routes._BENCHMARK_MAX_STALE_DAYS < 90


def test_prewarm_reports_unusable_benchmarks():
    """_prewarm_etf_cache() could not previously help with the ^HSI failure AT ALL — it calls
    the same _etf_20d_return(), so a rate-limited fetch just cached None for the whole window."""
    fn = ROUTES_SRC[ROUTES_SRC.index("def _prewarm_etf_cache("):ROUTES_SRC.index("def _rs_score(")]
    assert "ranking.benchmarks_unusable" in fn


# ── AUD-RANK-SECTORLABELS: the map was keyed on a taxonomy the data never uses ──────────

@pytest.mark.parametrize("label,etf", [
    ("Consumer Cyclical", "XLY"),   # yfinance's Consumer Discretionary — 4 stocks
    ("Consumer Defensive", "XLP"),  # yfinance's Consumer Staples — 1 stock
    ("Financial", "XLF"),           # occurs ALONGSIDE "Financial Services" (7) — 2 stocks
    ("Basic Materials", "XLB"),     # yfinance's Materials
    ("Financial Services", "XLF"),  # the pre-existing key must keep working
    ("Technology", "XLK"),
])
def test_real_production_sector_labels_resolve_to_the_right_etf(label, etf):
    """These are the ACTUAL labels in `stocks.sector`, measured in production. The map's
    "Consumer Discretionary"/"Consumer Staples" GICS keys matched NOTHING, so every Consumer
    name silently benchmarked against SPY while appearing correctly classified in the UI."""
    assert routes._resolve_sector_etf(label) == etf


def test_both_financial_variants_map_to_one_etf():
    """The source taxonomy is not even internally consistent — production carries BOTH. They
    must not become two different benchmarks."""
    assert routes._resolve_sector_etf("Financial") == routes._resolve_sector_etf("Financial Services")


def test_unknown_and_empty_sectors_fall_back_to_spy_but_are_logged():
    """18 US stocks have no sector at all. SPY is the honest benchmark there — but
    'benchmarked against SPY by design' and 'because the label did not match' were previously
    indistinguishable."""
    assert routes._resolve_sector_etf(None) == routes._US_FALLBACK
    assert routes._resolve_sector_etf("") == routes._US_FALLBACK
    assert routes._resolve_sector_etf("   ") == routes._US_FALLBACK
    assert routes._resolve_sector_etf("Nonexistent Sector") == routes._US_FALLBACK
    assert "ranking.sector_label_unmapped" in ROUTES_SRC


def test_resolution_tolerates_case_and_whitespace_drift():
    """A future label variant should land on the right ETF, not silently fall through to SPY."""
    assert routes._resolve_sector_etf("  Technology  ") == "XLK"
    assert routes._resolve_sector_etf("technology") == "XLK"
    assert routes._resolve_sector_etf("CONSUMER CYCLICAL") == "XLY"


def test_no_call_site_bypasses_the_resolver():
    """Two call sites existed; a raw `_SECTOR_ETF.get(...)` would silently skip the fix."""
    assert "_SECTOR_ETF.get(" not in ROUTES_SRC, (
        "all lookups must go through _resolve_sector_etf() so label drift is handled once"
    )


# ── AUD-RANK-THINPEERS: cohort fragmentation, not missing fundamentals ──────────────────

def test_cohorts_are_grouped_by_canonical_benchmark_not_raw_label():
    """ROOT CAUSE. _MIN_PEER_GROUP is applied PER METRIC against a sector cohort, and the same
    taxonomy drift fragments the cohorts: "Financial" (2) + "Financial Services" (7) is really
    one 9-member group, but became two buckets that can NEVER clear a 4-member gate on any
    metric. That — not absent fundamentals — is why value/growth were null for 71 and 86 of 250
    rows: 162 of 172 stocks (94%) have a warm fundamentals cache."""
    fn = ROUTES_SRC[ROUTES_SRC.index("by_sector: dict[str, list[str]] = defaultdict(list)"):]
    fn = fn[:fn.index("result: dict[str, dict[str, float]] = {}")]
    assert "_resolve_sector_etf(sector)" in fn


def test_unmapped_sectors_are_not_pooled_into_one_fake_cohort():
    """An unmapped sector resolves to SPY, so keying on the ETF would pool every unclassified
    stock into one large meaningless cross-sector cohort. A thin REAL sector is better than a
    large fake one for percentile ranking."""
    fn = ROUTES_SRC[ROUTES_SRC.index("by_sector: dict[str, list[str]] = defaultdict(list)"):]
    fn = fn[:fn.index("result: dict[str, dict[str, float]] = {}")]
    assert "_US_FALLBACK" in fn, "must detect the SPY fallback and keep those on their raw label"


def test_the_two_null_causes_are_distinguishable_in_logs():
    """compute_kscore() drops value/growth and renormalizes either way — pushing ~32% of the
    weight onto price-derived factors — but 'no fundamentals' and 'cohort too thin' call for
    completely different remedies."""
    assert "ranking.sector_percentile_unavailable" in ROUTES_SRC
    assert "cohort_too_thin" in ROUTES_SRC
    assert "metrics_missing" in ROUTES_SRC


# ── AUD-RANK-CURVEDRIFT: no coherence invariants on tuned curve params ──────────────────

def test_rsi_ladder_inversion_is_rejected():
    """The RSI knobs are an ORDERED ladder that per-key perturbation can silently invert. An
    inverted ladder does not error — it produces a monotonically WRONG technical curve that the
    EV search may still prefer on one train slice."""
    base = {"rsi_low": 30.0, "rsi_mid": 45.0, "rsi_high": 70.0}
    assert routes._kscore_curve_is_valid(base) is True
    assert routes._kscore_curve_is_valid({**base, "rsi_mid": 75.0}) is False, "mid above high"
    assert routes._kscore_curve_is_valid({**base, "rsi_low": 50.0}) is False, "low above mid"


def test_equal_rsi_values_are_rejected_as_not_strictly_ordered():
    base = {"rsi_low": 30.0, "rsi_mid": 30.0, "rsi_high": 70.0}
    assert routes._kscore_curve_is_valid(base) is False


def test_volatility_scale_is_bounded_at_both_ends():
    """Steps are relative to the LIVE value, so promotions ratchet: volatility_scale went
    1500 -> 1200, making the next candidate 1440 (= 1200 x 1.2) and never returning toward the
    default. Both extremes make the factor useless — at 400 nothing saturates, at 4000 nearly
    everything does."""
    lo, hi = routes._KSCORE_CURVE_BOUNDS["volatility_scale"]
    assert routes._kscore_curve_is_valid({"volatility_scale": 1200.0}) is True
    assert routes._kscore_curve_is_valid({"volatility_scale": lo - 1}) is False
    assert routes._kscore_curve_is_valid({"volatility_scale": hi + 1}) is False


def test_bounds_admit_every_current_default():
    """The bounds must reject INCOHERENCE, not aggressiveness. If a shipped default failed its
    own bound, the tuner would be permanently unable to promote anything."""
    from src.scoring.kscore import _CURVE_DEFAULTS
    assert routes._kscore_curve_is_valid(dict(_CURVE_DEFAULTS)) is True
    for key, (lo, hi) in routes._KSCORE_CURVE_BOUNDS.items():
        if key in _CURVE_DEFAULTS:
            assert lo <= _CURVE_DEFAULTS[key] <= hi, f"{key} default sits outside its own bound"


def test_bounds_admit_the_live_promoted_curve():
    """The live Redis curve (volatility_scale already promoted to 1200) must stay valid, or the
    fix would deadlock the tuner exactly the way the calibration watchdog once locked itself
    out."""
    live = {"rsi_low": 30.0, "rsi_mid": 45.0, "rsi_high": 70.0, "score_at_low": 50.0,
            "score_at_mid": 90.0, "score_at_high": 100.0,
            "rsi_overbought_decay_per_point": 2.5, "adx_center": 15.0, "adx_divisor": 25.0,
            "adx_boost_scale": 10.0, "volatility_scale": 1200.0}
    assert routes._kscore_curve_is_valid(live) is True


def test_candidate_generation_filters_invalid_sets():
    src = ROUTES_SRC[ROUTES_SRC.index("def _kscore_curve_candidate_sets("):]
    src = src[:src.index("_KSCORE_CURVE_BOUNDS")]
    assert "_kscore_curve_is_valid" in src


def test_the_merged_curve_is_revalidated_before_persisting():
    """The candidate filter is not sufficient: the MERGED dict is what becomes live for 30 days,
    and it is the only check that also covers a current_curve which drifted out of bounds under
    an older, unguarded promotion."""
    src = ROUTES_SRC[ROUTES_SRC.index("new_curve = {**current_curve, **best_curve}"):]
    guard = src[:src.index("redis_client = None")]
    assert "_kscore_curve_is_valid(new_curve)" in guard
    assert "merged_curve_invalid" in guard
    # It must REJECT, not merely log.
    assert "return {" in guard and '"applied": False' in guard


def test_validity_check_runs_before_the_redis_write():
    """Ordering is load-bearing — validating after the setex would persist the bad value."""
    i_guard = ROUTES_SRC.index("_kscore_curve_is_valid(new_curve)")
    i_write = ROUTES_SRC.index("_KSCORE_CURVE_REDIS_KEY, 30 * 86400")
    assert i_guard < i_write


# ── AUD-RANK-VOLSATURATE: the clip floor was a dead zone ────────────────────────────────

def _vol_at(score_scale: float, vol: float) -> float:
    return _volatility_score_from_raw(vol, cfg={"volatility_scale": score_scale})


def test_high_volatility_no_longer_ties_at_zero():
    """1,638 of 12,787 all-time ranking rows (12.81%) sat at EXACTLY 0. Past the floor the
    factor stopped discriminating: at the live scale of 1200, every stock above 8.33%/day
    realized vol scored identically 0, so the 18% of composite weight riding on volatility
    could not tell a merely-volatile name from a genuinely wild one."""
    a = _vol_at(1200.0, 0.10)   # 10%/day  -> linear -20
    b = _vol_at(1200.0, 0.20)   # 20%/day  -> linear -140
    assert a > b, "more volatile must still score strictly lower"
    assert a != b


def test_strict_monotonicity_across_the_floor_boundary():
    """The join must not introduce a flat spot or a reversal."""
    vols = [0.05, 0.06, 0.07, 0.075, 0.08, 0.09, 0.12, 0.25, 0.5, 1.0]
    scores = [_vol_at(1200.0, v) for v in vols]
    for earlier, later in zip(scores, scores[1:]):
        assert earlier > later, f"not strictly decreasing: {scores}"


def test_the_linear_region_is_completely_unchanged():
    """~87% of rows sit above the floor. volatility_scale must keep its exact meaning there, so
    the fix cannot be a recalibration in disguise."""
    for vol in (0.0, 0.01, 0.02, 0.05):
        assert _vol_at(1200.0, vol) == pytest.approx(100 - vol * 1200.0, abs=1e-9)


def test_calm_stocks_still_cap_at_100():
    assert _vol_at(1200.0, 0.0) == pytest.approx(100.0)
    assert _vol_at(1200.0, -0.01) <= 100.0, "must never exceed the 0-100 contract"


def test_score_stays_inside_0_100_for_extreme_input():
    """The compression is asymptotic — it must approach 0 without reaching or crossing it."""
    for vol in (0.5, 1.0, 5.0, 100.0):
        s = _vol_at(1200.0, vol)
        assert 0.0 < s <= 100.0, f"vol={vol} produced {s}"


def test_the_floor_is_continuous_at_the_join():
    """A discontinuity would put a cliff in the middle of the ranking distribution."""
    scale = 1200.0
    vol_at_floor = (100 - _VOL_SOFT_FLOOR) / scale
    # A step of `eps` in vol moves the score by eps*scale, so the score-space tolerance must be
    # scaled accordingly — otherwise this asserts the step size, not continuity.
    eps = 1e-7
    tol = eps * scale * 10
    assert _vol_at(scale, vol_at_floor - eps) == pytest.approx(_VOL_SOFT_FLOOR, abs=tol)
    assert _vol_at(scale, vol_at_floor + eps) == pytest.approx(_VOL_SOFT_FLOOR, abs=tol)
    # And the two sides must agree with each other, which is the actual continuity claim.
    assert _vol_at(scale, vol_at_floor - eps) - _vol_at(scale, vol_at_floor + eps) < tol


def test_missing_vol_still_returns_neutral():
    """None means 'not computable' (short history), which is a different fact from 'very
    volatile' and must keep its existing neutral handling."""
    assert _volatility_score_from_raw(None) == 50.0


# ── The corrupted weight must be reset, not left learned ────────────────────────────────

def test_relative_strength_default_weight_is_intact():
    """The 0.10 default was never the bug — the Redis OVERRIDE learned from placeholder data
    was (0.0501). Fixing the data does not by itself undo the demotion, so the override has to
    be cleared separately; this pins the value it must fall back to."""
    assert _WEIGHTS["relative_strength"] == 0.10


def test_default_weights_still_sum_to_one():
    assert sum(_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)


# ── Deadlock prevention: found by a pre-existing test failing against my first attempt ──

def test_an_already_invalid_base_does_not_veto_every_candidate():
    """My first version of this fix validated {**base, **candidate} unconditionally, so a base
    that was ALREADY incoherent rejected every candidate — including ones perturbing unrelated
    keys. A pre-existing test (test_a_zero_valued_base_constant_produces_no_candidates_for_that_key)
    caught it. That is precisely the permanent lockout the calibration watchdog once inflicted
    on itself, so it is pinned here rather than only implied."""
    broken = {"rsi_low": 0.0, "rsi_mid": 45.0, "rsi_high": 70.0, "score_at_low": 50.0,
              "score_at_mid": 90.0, "score_at_high": 100.0,
              "rsi_overbought_decay_per_point": 2.5, "adx_center": 15.0, "adx_divisor": 25.0,
              "adx_boost_scale": 10.0, "volatility_scale": 1200.0}
    assert routes._kscore_curve_is_valid(broken) is False, "precondition: base really is invalid"
    cands = routes._kscore_curve_candidate_sets(broken)
    assert any("volatility_scale" in c for c in cands), (
        "an unrelated key must still be tunable while the base is broken — otherwise the "
        "tuner can never climb back out"
    )


def test_an_invalid_current_curve_does_not_veto_promotion_forever():
    """Same reasoning at the promotion gate: the merged-curve check must only block when the
    MERGE is what breaks coherence."""
    src = ROUTES_SRC[ROUTES_SRC.index("new_curve = {**current_curve, **best_curve}"):]
    guard = src[:src.index("redis_client = None")]
    assert "_kscore_curve_is_valid(current_curve) and not _kscore_curve_is_valid(new_curve)" in guard


# ── AUD-RANK-SECTORLABELS: the same map was duplicated in two other services ────────────

def test_paper_trading_engine_sector_map_has_the_yfinance_labels():
    """The bug was duplicated: paper_trading_engine._SECTOR_ETF_MAP drives PT-M1's
    sector-relative-weakness EXIT gate. Without these keys, _batch_sector_rs_lag() mapped those
    stocks to no ETF and the gate silently never evaluated them."""
    _SERVICES = pathlib.Path(__file__).resolve().parents[2]
    pt = _SERVICES / "market-data/src/services/paper_trading_engine.py"
    if not pt.exists():
        pytest.skip("market-data service not present in this checkout")
    src = pt.read_text()
    m = src[src.index("_SECTOR_ETF_MAP: dict[str, str] = {"):]
    m = m[:m.index("}")]
    for label in ("Consumer Cyclical", "Consumer Defensive", "Financial", "Basic Materials"):
        assert f'"{label}"' in m, f"{label} missing from paper_trading_engine's copy"


def test_brinson_aliases_cover_the_yfinance_consumer_labels():
    """brinson_attribution fails CLOSED (unrecognized -> the explicit 'unclassified' bucket),
    so the effect there was lost attribution coverage rather than a wrong benchmark — but every
    real Consumer trade was landing in unclassified and being excluded from the effect sums."""
    _SERVICES = pathlib.Path(__file__).resolve().parents[2]
    b = _SERVICES / "market-data/src/services/brinson_attribution.py"
    if not b.exists():
        pytest.skip("market-data service not present in this checkout")
    src = b.read_text()
    m = src[src.index("_SECTOR_NAME_ALIASES: dict[str, str] = {"):]
    m = m[:m.index("}")]
    for label in ("Consumer Cyclical", "Consumer Defensive", "Basic Materials"):
        assert f'"{label}"' in m, f"{label} missing from brinson's alias map"
