"""T233-ARCH-INSERVICE-SPLITS: shared helpers used across signal-engine's route modules.

routes.py had grown to 6,289 lines / 35 routes, bundling three structurally distinct concerns:
hot-path signal reads/writes (this split's routes.py), self-tuning/calibration mechanisms
(calibration.py), and analytics/backtest/outcome-evaluation (outcomes.py). This module holds
the functions genuinely called from MORE than one of those three — Redis cache helpers, the
service-to-service JWT, the TuneHistory recorder, the confidence-calibration read path (used
by both live signal reads in routes.py and the calibration map endpoint in calibration.py),
and the outcome-window/hurdle constants (used by both calibration sweeps and the outcomes
evaluator) — so they live in one shared place instead of being duplicated or forcing a
circular import between the three route modules.

Verbatim extraction from routes.py — no logic changes. If something here looks wrong, it was
already wrong before this split; the split itself only moved code, it did not change it.
"""
from datetime import date, timedelta
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.config import get_settings
from common.logging import get_logger
from db import Signal, SignalHorizon, SignalOutcome, Stock, TuneHistory

_settings = get_settings()
log = get_logger("signals")

def _get_redis():
    from common.redis_client import get_redis as _get_pool_redis
    return _get_pool_redis()

def _cache_get(key: str):
    try:
        val = _get_redis().get(key)
        return json.loads(val) if val else None
    except Exception:
        return None

def _cache_set(key: str, value, ttl: int = 3600) -> None:
    try:
        _get_redis().setex(key, ttl, json.dumps(value))
    except Exception:
        pass

def _redis_get_float(key: str) -> float | None:
    try:
        val = _get_redis().get(key)
        return float(val) if val is not None else None
    except Exception:
        return None


# ── T233-SELFIMPROVE-PHASE3 extension: shared tune_history recorder ────────────
# See docs/DESIGN_TUNE_HISTORY_EXTENSION_2026-07-06.md for the full scoping. This mirrors
# market-data's promotion_gate.py._write_history but lives here since signal-engine writes
# to the same shared TuneHistory table directly (no cross-service HTTP call needed).

def _record_tune_history(
    session: Session,
    run_id: str,
    parameter_class: str,
    parameter_name: str,
    style: str,
    market: str,
    old_value: dict,
    new_value: dict,
    train_window: tuple[date, date],
    validation_window: tuple[date, date],
    train_ev_pct: float | None,
    validation_ev_pct: float | None,
    baseline_validation_ev_pct: float | None,
    validation_n: int | None,
    promoted: bool,
    gate_failures: list[str],
    triggered_by: str = "manual",
) -> None:
    """Write one tune_history row. Called at the exact point each mechanism already
    decides apply-vs-skip — purely additive recording, no change to any gating logic.
    market="ALL" is the documented convention for mechanisms that pool US+HK signals
    without a market split (see the design doc §2) — not a claim about a specific market.
    """
    session.add(TuneHistory(
        run_id=run_id, parameter_class=parameter_class, parameter_name=parameter_name,
        style=style, market=market, old_value=old_value, new_value=new_value,
        train_window_start=train_window[0], train_window_end=train_window[1],
        validation_window_start=validation_window[0], validation_window_end=validation_window[1],
        train_ev_pct=train_ev_pct, validation_ev_pct=validation_ev_pct,
        baseline_validation_ev_pct=baseline_validation_ev_pct, validation_n=validation_n,
        promoted=promoted, gate_failures=gate_failures, triggered_by=triggered_by,
    ))
    session.commit()


_service_token_cache: str = ""
_service_token_exp: float = 0.0  # epoch seconds when the cached token expires


def _service_token() -> str:
    """Long-lived JWT for signal-engine → internal service calls (sub='signal-engine').
    Refreshes 7 days before expiry so the cached token is never used stale."""
    global _service_token_cache, _service_token_exp
    import time
    from jose import jwt as _jwt
    if _service_token_cache and time.time() < _service_token_exp - 7 * 86400:
        return _service_token_cache
    exp = int(time.time()) + 365 * 86400
    payload = {
        "sub": "signal-engine",
        "exp": exp,
        "jti": str(__import__("uuid").uuid4()),
    }
    _service_token_cache = _jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")
    _service_token_exp = float(exp)
    return _service_token_cache



# ── T223: Confidence calibration — outcome-based win rate lookup ──────────────
# Confidence band → actual win rate from signal_outcomes (Redis-cached, 1h TTL).
# Enriches every signal response so traders know if "70 confidence" means 55% or 65% wins.

_CONF_BANDS: list[tuple[float, float, str]] = [
    (0, 40, "0-40"),
    (40, 55, "40-55"),
    (55, 70, "55-70"),
    (70, 85, "70-85"),
    (85, 101, "85+"),
]
_CONF_CAL_CACHE_KEY = "signal:confidence_calibration"
_CONF_CAL_TTL = 3600  # 1 hour
# T232-OC5: confidence is direction-agnostic, so pooling BUY+SELL, all horizons, and both
# markets into one band mixed populations with documented divergent base rates (SELL 43.7%
# vs BUY 63.3% in production; HK vs US also diverge materially). Calibration is now keyed by
# (horizon, direction, market) first; if that specific bucket doesn't reach the min-count, it
# falls back to (horizon, direction) pooled across markets, which is still far more precise
# than the old fully-pooled map. min-count raised 10 -> 30 (10 gives a ±30pp confidence
# interval — not tight enough for the green/amber UI coloring to mean anything).
_CONF_CAL_MIN_COUNT = 30


def _cal_bucket_key(horizon: str, direction: str, market: str | None, band: str) -> str:
    if market:
        return f"{horizon}|{direction}|{market}|{band}"
    return f"{horizon}|{direction}|{band}"


def _build_confidence_calibration(session: Session) -> dict:
    """Query signal_outcomes and compute win rate per (horizon, direction, market, band).

    Returns a flat dict keyed by "HORIZON|DIRECTION|MARKET|BAND" (market-specific) plus
    "HORIZON|DIRECTION|BAND" fallback entries (pooled across markets, used when the
    market-specific bucket is too thin), each {"win_rate": float, "count": int}, or {}.
    """
    cutoff = date.today() - timedelta(days=180)
    rows = session.execute(
        select(
            SignalOutcome.confidence, SignalOutcome.is_correct,
            SignalOutcome.horizon, SignalOutcome.signal_direction, Stock.market,
        )
        .join(Stock, Stock.id == SignalOutcome.stock_id)
        .where(
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_date >= cutoff,
        )
    ).all()

    buckets: dict[str, dict] = {}
    for lo, hi, band in _CONF_BANDS:
        band_rows = [r for r in rows if lo <= r.confidence < hi]
        if not band_rows:
            continue
        # Market-specific buckets
        by_market: dict[tuple, list] = {}
        by_pooled: dict[tuple, list] = {}
        for r in band_rows:
            horiz = r.horizon.value if hasattr(r.horizon, "value") else r.horizon
            # str, enum.Enum members stringify as "Market.US" via f-string/str(), not "US" —
            # use .value explicitly so the bucket key and API response are the plain string.
            mkt = r.market.value if hasattr(r.market, "value") else r.market
            by_market.setdefault((horiz, r.signal_direction, mkt), []).append(r.is_correct)
            by_pooled.setdefault((horiz, r.signal_direction), []).append(r.is_correct)
        for (horiz, direction, market), outcomes in by_market.items():
            if len(outcomes) >= _CONF_CAL_MIN_COUNT:
                key = _cal_bucket_key(horiz, direction, market, band)
                buckets[key] = {"win_rate": round(sum(outcomes) / len(outcomes), 3), "count": len(outcomes)}
        for (horiz, direction), outcomes in by_pooled.items():
            if len(outcomes) >= _CONF_CAL_MIN_COUNT:
                key = _cal_bucket_key(horiz, direction, None, band)
                buckets[key] = {"win_rate": round(sum(outcomes) / len(outcomes), 3), "count": len(outcomes)}
    return buckets


def _get_confidence_calibration(session: Session) -> dict:
    """Return calibration map from Redis cache; rebuild if stale."""
    cached = _cache_get(_CONF_CAL_CACHE_KEY)
    if cached:
        return cached
    try:
        cal = _build_confidence_calibration(session)
        if cal:
            _cache_set(_CONF_CAL_CACHE_KEY, cal, _CONF_CAL_TTL)
        return cal
    except Exception as exc:
        log.warning("confidence_calibration.build_failed", error=str(exc))
        return {}


def _calibrated_win_rate(
    confidence: float, cal_map: dict, horizon: str | None = None,
    direction: str | None = None, market: str | None = None,
) -> tuple[float, int] | None:
    """Return (win_rate, sample_count) for this confidence/horizon/direction/market;
    None if insufficient data. Falls back from market-specific to horizon+direction-pooled
    when horizon/direction are known but the market-specific bucket doesn't meet min-count.
    Falls back further to confidence-band-only (old pooled behavior) when horizon/direction
    are not supplied by the caller, so existing callers keep working during rollout.
    """
    for lo, hi, band in _CONF_BANDS:
        if not (lo <= confidence < hi):
            continue
        if horizon and direction:
            if market:
                entry = cal_map.get(_cal_bucket_key(horizon, direction, market, band))
                if entry:
                    return entry["win_rate"], entry["count"]
            entry = cal_map.get(_cal_bucket_key(horizon, direction, None, band))
            if entry:
                return entry["win_rate"], entry["count"]
            return None
        # Legacy fallback: no horizon/direction supplied — cannot key precisely.
        return None
    return None


def _compute_stability(session: Session, stock_id: int, horizon: SignalHorizon, current_signal: str, limit: int = 30) -> int:
    """Count consecutive past days the given signal has been persisted in the DB."""
    from sqlalchemy import desc
    sigs = session.execute(
        select(Signal.signal)
        .where(Signal.stock_id == stock_id, Signal.horizon == horizon)
        .order_by(desc(Signal.ts))
        .limit(limit)
    ).scalars().all()
    count = 0
    for sig in sigs:
        if sig.value == current_signal:
            count += 1
        else:
            break
    return count


def _stored_signal_for_style(session: Session, stock_id: int, style_key: str) -> dict | None:
    """Return the latest persisted signal row for this stock+style as a plain dict."""
    from sqlalchemy import desc as _desc
    try:
        horiz = SignalHorizon(style_key)
    except ValueError:
        return None
    row = session.execute(
        select(Signal.signal, Signal.confidence, Signal.bullish_probability, Signal.ts, Signal.reasons)
        .where(Signal.stock_id == stock_id, Signal.horizon == horiz)
        .order_by(_desc(Signal.ts))
        .limit(1)
    ).one_or_none()
    if not row:
        return None
    reasons = dict(row.reasons) if row.reasons else {}
    reasons["stability_days"] = _compute_stability(session, stock_id, horiz, row.signal.value)
    return {
        "signal": row.signal.value,
        "horizon": style_key,
        "confidence": row.confidence,
        "bullish_probability": row.bullish_probability,
        "reasons": reasons,
        "ts": row.ts.isoformat() if row.ts else None,
    }


# ── Signal outcome tracking ────────────────────────────────────────────────────

# Hold window in calendar days per horizon. Approximates actual trading days held.
_OUTCOME_HOLD_DAYS: dict[str, int] = {
    "SHORT":  7,    # ~5 trading days
    "SWING":  14,   # ~10 trading days
    "LONG":   28,   # ~20 trading days
    "GROWTH": 14,   # same window as SWING; growth trades are momentum-based
}

# T232-SIG10: SELL's primary is_correct/pct_return used to be evaluated at the SAME window as
# BUY for each style — but live SignalOutcome data shows SELL accuracy decays sharply with
# horizon (57.6% at 5d, 58.6% at 10d, dropping to 48.5% at 20d as of 2026-07-04), while BUY's
# per-style windows (14-28 calendar days for SWING/GROWTH/LONG) are exactly the range where
# SELL's edge has already eroded. Using a shorter, SELL-specific window for the fields that
# drive reporting/calibration (is_correct, pct_return, exit_date) means the system's own
# accuracy metrics reflect where SELL genuinely has signal, instead of diluting it with a
# window where it doesn't. Deliberately does NOT touch BUY's windows or attempt regime-tiered
# SELL thresholds — regime-tiering was investigated and found unsupported by current data
# (96%+ of SELL outcomes are bull-regime only; near-zero bear/choppy/risk_off samples to
# calibrate against). Values below mirror the horizons where the calendar-day data above shows
# real, validated signal (5-10 calendar days), not a guess.
_SELL_OUTCOME_HOLD_DAYS: dict[str, int] = {
    "SHORT":  5,    # SELL's strongest cohort in live data — keep close to its natural window
    "SWING":  7,    # was 14 — shortened to where accuracy hasn't yet decayed
    "LONG":   10,   # was 28 — LONG SELL sample is thin; 10d is a conservative middle ground
    "GROWTH": 7,    # was 14, same reasoning as SWING
}

# T232-OC6: how many calendar days past exit_target to wait, with still no exit price found,
# before concluding the price is permanently gone (delisting/halt) rather than just an
# ingestion lag. 10 days comfortably covers weekends/holidays plus normal scheduler delay.
_OUTCOME_CENSOR_GRACE_DAYS = 10

# T232-OC4: any positive close-to-close move (pct_return > 0) used to count as a win — a
# +0.01% move after a 14-day hold scored identical to a real +5% winner. This flattered
# reported win rates and fed the same wrong objective into the T232-OC3 calibration sweep.
# Requiring a real cost hurdle instead of a bare zero line means "win" reflects a trade that
# would have cleared round-trip costs, not just closed a cent above entry. Set to 2.5x the
# round-trip entry+exit slippage already modeled in paper_trading_engine.py's
# entry_slippage_pct (0.001 each way = 0.002 round trip) — comfortably above pure transaction
# cost without being so high it starts rejecting genuine small wins. Deliberately does NOT
# model intraday stop-outs (max-adverse-excursion) — that requires picking a stop-loss % to
# check against, but the real paper trading engine uses dynamic/trailing stops rather than one
# fixed distance, and retroactively changing what "win" means would destabilize the OC3
# EV-lift gate and OC5 calibration bands other in-flight work already trusts. Tracked as a
# known limitation for a future pass, not silently skipped — see docs/KNOWN_LIMITATIONS.md.
_OUTCOME_WIN_HURDLE_PCT = 0.005

