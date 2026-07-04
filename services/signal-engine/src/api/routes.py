from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import json
import os as _os

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from common.config import get_settings
from common.jwt_auth import get_current_username
from common.logging import get_logger
from db import Price, Signal, SignalHorizon, SignalOutcome, SignalType, Stock, TimeFrame, get_session

_settings = get_settings()

# ── Redis cache helper ────────────────────────────────────────────────────────

def _get_redis():
    import redis as redis_lib
    return redis_lib.from_url(_settings.redis_url, decode_responses=True)

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


from ..generators import generate_signal, generate_all_signals
from ..config import get_thresholds, reload as reload_thresholds, loaded_at as thresholds_loaded_at

log = get_logger("signals")

router = APIRouter(prefix="/signals", tags=["signals"])


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


@router.get("")
def all_latest_signals(
    style: str | None = Query(None, description="Filter by trading style: SHORT, SWING, LONG"),
    session: Session = Depends(get_session),
):
    """Return the most recently persisted signal for every active stock.

    Optional ?style=SWING filters to a specific trading horizon.
    If omitted, returns the best available signal per stock (SWING > LONG > GROWTH > SHORT).
    """
    # Preference order when no style specified: SWING wins if available, then LONG, GROWTH, SHORT.
    _STYLE_PREFERENCE = ["SWING", "LONG", "GROWTH", "SHORT"]
    horizon_filter = style.upper() if style else None
    # Subquery: latest ts per (stock_id, horizon)
    latest_subq = (
        select(Signal.stock_id, Signal.horizon, func.max(Signal.ts).label("max_ts"))
        .group_by(Signal.stock_id, Signal.horizon)
        .subquery()
    )
    q = (
        select(Stock.symbol, Signal.stock_id, Signal.signal, Signal.horizon, Signal.confidence, Signal.bullish_probability, Signal.ts)
        .join(Signal, Stock.id == Signal.stock_id)
        .join(latest_subq, (Signal.stock_id == latest_subq.c.stock_id)
              & (Signal.horizon == latest_subq.c.horizon)
              & (Signal.ts == latest_subq.c.max_ts))
        .where(Stock.active.is_(True))
    )
    horizon_enum = None
    if horizon_filter:
        try:
            horizon_enum = SignalHorizon(horizon_filter)
            q = q.where(Signal.horizon == horizon_enum)
        except ValueError:
            pass  # unknown style — return all
    rows = session.execute(q).all()

    # When no style specified, apply preference order (SWING > LONG > GROWTH > SHORT)
    # so each stock contributes at most one row (the best available style).
    if horizon_filter is None:
        _pref_idx = {s: i for i, s in enumerate(_STYLE_PREFERENCE)}
        _best: dict[int, object] = {}
        for row in rows:
            style_val = row.horizon.value if hasattr(row.horizon, "value") else str(row.horizon)
            cur = _best.get(row.stock_id)
            if cur is None:
                _best[row.stock_id] = row
            else:
                cur_style = cur.horizon.value if hasattr(cur.horizon, "value") else str(cur.horizon)
                if _pref_idx.get(style_val, 99) < _pref_idx.get(cur_style, 99):
                    _best[row.stock_id] = row
        rows = list(_best.values())

    # Batch stability: one extra query for all stock_ids at this horizon
    # Fetches ts so we can detect gaps in the streak (non-consecutive days don't count).
    stability_map: dict[int, int] = {}
    if rows and horizon_enum is not None:
        from collections import defaultdict
        stock_ids = list({row.stock_id for row in rows})
        cutoff = datetime.now(timezone.utc) - timedelta(days=35)
        recent = session.execute(
            select(Signal.stock_id, Signal.signal, Signal.ts)
            .where(
                Signal.stock_id.in_(stock_ids),
                Signal.horizon == horizon_enum,
                Signal.ts >= cutoff,
            )
            .order_by(Signal.stock_id, Signal.ts.desc())
        ).all()
        by_stock: dict[int, list[tuple[str, object]]] = defaultdict(list)
        for r in recent:
            by_stock[r.stock_id].append((r.signal.value, r.ts))
        latest_sig_map = {row.stock_id: row.signal.value for row in rows}
        for sid, sig_ts_pairs in by_stock.items():
            cur = latest_sig_map.get(sid, "")
            count = 0
            prev_ts = None
            for sig_val, ts in sig_ts_pairs:
                if sig_val != cur:
                    break
                if prev_ts is not None:
                    gap_days = (prev_ts - ts).days if hasattr(prev_ts, "days") else 0
                    try:
                        gap_days = (prev_ts.date() - ts.date()).days
                    except Exception:
                        gap_days = 1
                    if gap_days > 3:  # gap > 3 calendar days breaks the consecutive streak
                        break
                count += 1
                prev_ts = ts
            stability_map[sid] = count

    return [
        {
            "symbol": row.symbol,
            "signal": row.signal.value,
            "horizon": row.horizon.value,
            "confidence": row.confidence,
            "bullish_probability": row.bullish_probability,
            "ts": row.ts.isoformat() if row.ts else None,
            "stability_days": stability_map.get(row.stock_id, 1),
        }
        for row in rows
    ]


@router.get("/consensus")
def signal_consensus(
    market: str | None = Query(None, description="Filter by market: US or HK"),
    session: Session = Depends(get_session),
):
    """Return the latest signal for every active stock across all 4 horizons in one call.

    Response: { symbol: { SHORT: {signal, confidence, bullish_probability, ts, stability_days},
                           SWING: {...}, LONG: {...}, GROWTH: {...} } }
    Only includes horizons that have a stored signal.
    """
    latest_subq = (
        select(Signal.stock_id, Signal.horizon, func.max(Signal.ts).label("max_ts"))
        .group_by(Signal.stock_id, Signal.horizon)
        .subquery()
    )
    q = (
        select(Stock.symbol, Signal.stock_id, Signal.signal, Signal.horizon,
               Signal.confidence, Signal.bullish_probability, Signal.ts)
        .join(Signal, Stock.id == Signal.stock_id)
        .join(latest_subq, (Signal.stock_id == latest_subq.c.stock_id)
              & (Signal.horizon == latest_subq.c.horizon)
              & (Signal.ts == latest_subq.c.max_ts))
        .where(Stock.active.is_(True))
    )
    if market:
        q = q.where(Stock.market == market.upper())

    rows = session.execute(q).all()
    result: dict[str, dict] = {}
    for row in rows:
        sym = row.symbol
        hor = row.horizon.value
        if sym not in result:
            result[sym] = {}
        result[sym][hor] = {
            "signal": row.signal.value,
            "confidence": row.confidence,
            "bullish_probability": row.bullish_probability,
            "ts": row.ts.isoformat() if row.ts else None,
        }
    return result


@router.post("/refresh")
def refresh_signals(
    tasks: BackgroundTasks,
    market: str | None = None,
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Recompute and persist signals for all active stocks, optionally filtered by market."""
    q = select(Stock.symbol).where(Stock.active.is_(True))
    if market:
        q = q.where(Stock.market == market.upper())
    symbols = list(session.execute(q).scalars())
    try:
        r = _get_redis()
        for key in r.scan_iter("signals:cache:*"):
            r.delete(key)
    except Exception:
        pass
    tasks.add_task(_bulk_persist, symbols)
    return {"status": "scheduled", "count": len(symbols)}


@router.post("/reset")
def reset_signals(tasks: BackgroundTasks, session: Session = Depends(get_session), _: str = Depends(get_current_username)):
    """Wipe all persisted signals then re-persist fresh ones for every active stock."""
    deleted = session.query(Signal).delete()
    session.commit()
    symbols = list(session.execute(select(Stock.symbol).where(Stock.active.is_(True))).scalars())
    try:
        r = _get_redis()
        for key in r.scan_iter("signals:cache:*"):
            r.delete(key)
    except Exception:
        pass
    tasks.add_task(_bulk_persist, symbols)
    log.info("signals.reset", deleted=deleted, repersisting=len(symbols))
    return {"status": "reset", "deleted": deleted, "repersisting": len(symbols)}


def _bulk_persist(symbols: list[str]) -> None:
    from db import SessionLocal
    from sqlalchemy import desc
    _failures: list[tuple[str, str]] = []  # (symbol, error_message)
    for symbol in symbols:
        try:
            all_sig = generate_all_signals(symbol)

            # 40-B: Cross-horizon consensus — annotate each signal with how many other
            # styles also fired BUY for this symbol in this same batch.
            buy_styles = [sk for sk, ai in all_sig.items() if ai.signal == "BUY"]
            for style_key, ai in all_sig.items():
                if ai.reasons is None:
                    ai.reasons = {}
                others = [sk for sk in buy_styles if sk != style_key]
                ai.reasons["cross_style_buys"] = len(others)
                if others:
                    ai.reasons["cross_style_buy_styles"] = others

            # Enrich reasons with catalyst intelligence (once per symbol, fail-silent)
            _catalyst: dict | None = None
            try:
                import httpx as _httpx_cat
                _ta_score = 50.0
                if all_sig:
                    _first_reasons = (next(iter(all_sig.values())).reasons or {})
                    _ta_score = float(_first_reasons.get("ta_score", 50.0))
                _cr = _httpx_cat.get(
                    f"{_settings.event_intelligence_url}/catalyst/{symbol}",
                    params={"technical_score": _ta_score},
                    headers={"Authorization": f"Bearer {_service_token()}"},
                    timeout=2.0,
                )
                if _cr.status_code == 200:
                    _catalyst = _cr.json()
            except Exception:
                pass

            if _catalyst:
                _insider_s  = _catalyst.get("insider_score")
                _congress_s = _catalyst.get("congress_score")
                for _ai in all_sig.values():
                    if _ai.reasons is None:
                        _ai.reasons = {}
                    if _catalyst.get("catalyst_score") is not None:
                        _ai.reasons["catalyst_score"] = round(_catalyst["catalyst_score"], 1)
                    if _insider_s is not None:
                        _ai.reasons["insider_score"] = round(_insider_s, 1)
                    if _congress_s is not None:
                        _ai.reasons["congress_score"] = round(_congress_s, 1)
                    if _catalyst.get("composite_score") is not None:
                        _ai.reasons["composite_score"] = round(_catalyst["composite_score"], 1)

                    # T172-A: wire catalyst scores into fused_prob — small directional nudge
                    # Insider buying/selling is the strongest real-money conviction signal.
                    # Congress score is 0-100 (clamped non-negative in catalyst.py).
                    _cat_adj = 0.0
                    if _insider_s is not None:
                        if _insider_s > 60:    _cat_adj += 0.03   # strong cluster of insider buys
                        elif _insider_s > 30:  _cat_adj += 0.015
                        elif _insider_s < -30: _cat_adj -= 0.03   # heavy insider selling
                        elif _insider_s < -10: _cat_adj -= 0.015
                    if _congress_s is not None:
                        if _congress_s > 50:   _cat_adj += 0.02   # meaningful congress net buying
                        elif _congress_s > 25: _cat_adj += 0.01
                    if _cat_adj != 0.0 and _ai.bullish_probability is not None:
                        import numpy as _np_cat
                        _ai.bullish_probability = round(
                            float(_np_cat.clip(_ai.bullish_probability + _cat_adj, 0.0, 1.0)), 4
                        )
                        _ai.reasons["catalyst_prob_adj"] = round(_cat_adj, 3)
                        # CRIT-5: re-evaluate signal direction after catalyst nudge so stored
                        # signal type stays consistent with the adjusted probability.
                        try:
                            from ..generators.signals import _STYLE_PROFILES as _SP_cat
                            _hor_key = _ai.horizon
                            if _hor_key in _SP_cat:
                                _bt_vals = _SP_cat[_hor_key].get("buy_threshold", {})
                                _min_bt = min(_bt_vals.values()) if _bt_vals else 0.70
                                _sell_t = _SP_cat[_hor_key].get("sell_threshold", 0.35)
                                if _ai.bullish_probability >= _min_bt and _ai.signal == "HOLD":
                                    _ai.signal = "BUY"
                                    _ai.reasons["catalyst_upgraded_signal"] = True
                                elif _ai.bullish_probability <= _sell_t and _ai.signal in ("BUY", "HOLD"):
                                    _ai.signal = "SELL"
                                    _ai.reasons["catalyst_downgraded_signal"] = True
                        except Exception:
                            pass

                    # T220-A/H: Boolean flags for UI chips (threshold-based from scores above)
                    if _insider_s is not None and _insider_s >= 60:
                        _ai.reasons["insider_cluster"] = True
                        _ai.reasons["insider_buy_usd"] = _catalyst.get("insider_buy_usd")  # may be None
                    if _congress_s is not None and _congress_s > 50:
                        _ai.reasons["congress_buy"] = True
                    if _catalyst.get("institutional_score") is not None:
                        _ai.reasons["institutional_score"] = round(float(_catalyst["institutional_score"]), 1)

            with SessionLocal() as s:
                stock = s.query(Stock).filter(Stock.symbol == symbol).one_or_none()
                if not stock:
                    continue

                # F2: load prior confidence for each horizon to compute confidence_delta
                prior_conf: dict[str, float | None] = {}
                try:
                    from sqlalchemy import text as _text2
                    rows = s.execute(_text2("""
                        SELECT CAST(horizon AS text), confidence FROM signals
                        WHERE stock_id = :sid
                        ORDER BY ts DESC
                    """), {"sid": stock.id}).fetchall()
                    seen: set[str] = set()
                    for row in rows:
                        if row[0] not in seen:
                            prior_conf[row[0]] = float(row[1]) if row[1] is not None else None
                            seen.add(row[0])
                except Exception:
                    pass

                # T220-G: Sector rotation — add sector_momentum to reasons
                try:
                    import httpx as _httpx_rot
                    _rot_r = _httpx_rot.get(
                        f"{_settings.market_data_url}/stocks/sector-rotation",
                        headers={"Authorization": f"Bearer {_service_token()}"},
                        timeout=2.0,
                    )
                    if _rot_r.status_code == 200:
                        _rotation = _rot_r.json()
                        _sector = stock.sector
                        if _sector and _sector in _rotation:
                            _sector_momentum = _rotation[_sector].get("momentum", 0)
                            if _sector_momentum != 0:
                                for _ai in all_sig.values():
                                    if _ai.reasons is None:
                                        _ai.reasons = {}
                                    _ai.reasons["sector_momentum"] = _sector_momentum
                except Exception:
                    pass

                # Cache the research summary once per symbol (shared across styles)
                _research_summary: dict | None = None
                _research_fetched = False
                for style_key, ai in all_sig.items():
                    horizon_enum = SignalHorizon(ai.horizon)
                    # F2: annotate confidence_delta before upsert
                    prev = prior_conf.get(ai.horizon)
                    if prev is not None and ai.confidence is not None:
                        delta = round(float(ai.confidence) - float(prev), 1)
                        if ai.reasons is None:
                            ai.reasons = {}
                        ai.reasons["confidence_delta"] = delta
                    # Upsert: one signal row per (stock, horizon, calendar day).
                    # Conflict on the unique index uq_signals_stock_horizon_day
                    # → update in place so signal type changes within a day overwrite rather than grow the table.
                    # Use CAST() instead of ::type to avoid SQLAlchemy named-param
                    # binding ambiguity with PostgreSQL :: cast syntax (BUG-6).
                    s.execute(
                        text("""
                            INSERT INTO signals
                                (stock_id, signal, horizon, confidence, bullish_probability, reasons, source)
                            VALUES
                                (:sid, CAST(:sig AS signaltype), CAST(:hor AS signalhorizon),
                                 :conf, :bp, CAST(:rsns AS jsonb), :src)
                            ON CONFLICT (stock_id, horizon, date_trunc('day', ts))
                            DO UPDATE SET
                                signal              = EXCLUDED.signal,
                                confidence          = EXCLUDED.confidence,
                                bullish_probability = EXCLUDED.bullish_probability,
                                reasons             = EXCLUDED.reasons,
                                source              = EXCLUDED.source,
                                ts                  = NOW()
                        """),
                        dict(
                            sid=stock.id,
                            sig=ai.signal,
                            hor=ai.horizon,
                            conf=ai.confidence,
                            bp=ai.bullish_probability,
                            rsns=json.dumps(ai.reasons),
                            src="signal-engine",
                        ),
                    )

                    # INT-4: auto-trigger research on new BUY signal (fire-and-forget)
                    # INT-7: log divergence if research and signal disagree (per-style)
                    if ai.signal in ("BUY", "STRONG BUY"):
                        try:
                            import httpx as _httpx
                            _url = _settings.research_engine_url
                            if not _research_fetched:
                                # INT-4: trigger background research if stale
                                _httpx.post(f"{_url}/research/{symbol}/trigger", timeout=1.5)
                                # INT-7: fetch summary once; reused for all BUY styles
                                _tok = _service_token()
                                _sr = _httpx.get(
                                    f"{_url}/research/{symbol}/summary",
                                    timeout=1.5,
                                    headers={"Authorization": f"Bearer {_tok}"},
                                )
                                if _sr.status_code == 200:
                                    _research_summary = _sr.json()
                                _research_fetched = True
                            if _research_summary:
                                _rec = _research_summary.get("recommendation", "")
                                _score = float(_research_summary.get("overall_score") or 0)
                                if _rec in ("AVOID", "SELL") or (_rec == "WATCH" and _score < 60):
                                    log.warning(
                                        "signal.research_divergence",
                                        symbol=symbol,
                                        style=style_key,
                                        signal=ai.signal,
                                        signal_conf=round(ai.confidence or 0, 1),
                                        research_rec=_rec,
                                        research_score=_score,
                                    )
                        except Exception:
                            pass  # never block signal generation on research calls
                s.commit()

        except Exception as exc:
            _failures.append((symbol, str(exc)))
            log.warning("signals.refresh.skip", symbol=symbol, error=str(exc))

    if _failures:
        fail_rate = len(_failures) / len(symbols) if symbols else 0.0
        if fail_rate > 0.05:
            log.error(
                "signals.refresh.high_failure_rate",
                total=len(symbols),
                failed=len(_failures),
                fail_rate_pct=round(fail_rate * 100, 1),
                sample_failures=_failures[:5],
            )
        else:
            log.warning(
                "signals.refresh.failures",
                total=len(symbols),
                failed=len(_failures),
                fail_rate_pct=round(fail_rate * 100, 1),
            )


@router.get("/accuracy")
def signal_accuracy(
    lookback_days: int = Query(90, ge=2, le=365),
    symbol: str | None = None,
    market: str | None = Query(None, regex="^(US|HK)$"),
    from_date: str | None = None,
    to_date: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(200, ge=10, le=500),
    session: Session = Depends(get_session),
):
    """Historical accuracy of BUY/SELL signals vs actual price outcomes.

    For each persisted BUY or SELL signal within the lookback window, compares
    the close price on the signal date to the most recent available close price.
    A BUY is 'correct' if price rose; a SELL is 'correct' if it fell.
    Signals need at least 1 day of price history after the signal date to be evaluated.
    Uses bulk price queries + bisect matching instead of per-signal queries.

    Optional from_date / to_date (ISO strings, e.g. "2026-03-01") narrow the
    signal window for walk-forward drill-down without affecting lookback_days.
    """
    import bisect

    if from_date and to_date:
        cutoff = datetime.fromisoformat(from_date).replace(tzinfo=timezone.utc)
        outcome_cutoff = datetime.fromisoformat(to_date).replace(tzinfo=timezone.utc).replace(hour=23, minute=59, second=59)
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    q = (
        select(Signal, Stock.symbol, Stock.name)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal.in_([SignalType.BUY, SignalType.SELL]))
        .order_by(Signal.ts.desc())
    )
    if symbol:
        q = q.where(Stock.symbol == symbol.upper())
    if market:
        q = q.where(Stock.market == market.upper())

    rows = session.execute(q).all()
    if not rows:
        return {"lookback_days": lookback_days, "total_signals": 0, "buy_count": 0,
                "sell_count": 0, "buy_accuracy": None, "sell_accuracy": None,
                "overall_accuracy": None, "avg_buy_return_pct": None,
                "avg_sell_return_pct": None, "profit_factor": None, "signals": []}

    stock_ids = list({sig.stock_id for sig, _, _ in rows})

    # Bulk-fetch all D1 prices for relevant stocks across the full lookback window
    price_since = (cutoff - timedelta(days=5)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= price_since)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    # stock_id → (sorted date list, close list)
    _pts: dict[int, list] = {}
    _pclose: dict[int, list] = {}
    for row in price_rows:
        sid = row.stock_id
        if sid not in _pts:
            _pts[sid] = []
            _pclose[sid] = []
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _pts[sid].append(d)
        _pclose[sid].append(float(row.close))

    def first_close_after(sid: int, after_date):
        """First close STRICTLY after after_date, returns (close, date) or (None, None)."""
        ts_list = _pts.get(sid)
        if not ts_list:
            return None, None
        idx = bisect.bisect_right(ts_list, after_date)
        if idx >= len(ts_list):
            return None, None
        return _pclose[sid][idx], ts_list[idx]

    def most_recent_close(sid: int):
        """Most recent (last) close in the loaded price window, returns (close, date) or (None, None)."""
        ts_list = _pts.get(sid)
        if not ts_list:
            return None, None
        return _pclose[sid][-1], ts_list[-1]

    # Deduplicate: the scheduler runs every ~10 min and inserts repeated signals on
    # the same day. One evaluation per (stock, signal_type, day) is the right unit —
    # we want "was the model correct that day", not 10 identical copies of the same call.
    seen_keys: set[tuple] = set()

    results = []
    for sig, sym, name in rows:
        signal_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        dedup_key = (sig.stock_id, sig.signal, signal_date)
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        # Entry: first close STRICTLY after signal date — avoids same-day look-ahead
        # (old code used price_on_or_before(signal_date+1) which returned Friday's close
        # for Friday signals since signal_date+1 = Saturday is not a trading day)
        entry_close, entry_date = first_close_after(sig.stock_id, signal_date)
        if entry_close is None:
            continue

        # Exit: most recent available close — shows running P&L from entry to today
        # (old code used first_close_after for exit too, making entry == exit → pct=0%)
        exit_close, exit_date = most_recent_close(sig.stock_id)
        if exit_close is None or exit_date is None or exit_date <= signal_date:
            continue
        if entry_close <= 0:
            continue

        pct_change  = (exit_close - entry_close) / entry_close * 100
        signal_type = sig.signal.value
        correct     = (signal_type == "BUY" and pct_change >= 0) or (signal_type == "SELL" and pct_change <= 0)

        results.append({
            "symbol": sym,
            "name": name,
            "signal": signal_type,
            "confidence": round(sig.confidence, 1),
            "bullish_probability": round(sig.bullish_probability, 4) if sig.bullish_probability else None,
            "signal_date": signal_date.isoformat(),
            "entry_price": round(entry_close, 4),
            "exit_price": round(exit_close, 4),
            "pct_change": round(pct_change, 2),
            "correct": correct,
            "days_held": (exit_date - signal_date).days,
        })

    buy_r  = [r for r in results if r["signal"] == "BUY"]
    sell_r = [r for r in results if r["signal"] == "SELL"]

    def _accuracy(items: list) -> float | None:
        return round(sum(1 for i in items if i["correct"]) / len(items) * 100, 1) if items else None

    def _avg_return(items: list) -> float | None:
        return round(sum(i["pct_change"] for i in items) / len(items), 2) if items else None

    def _profit_factor(items: list) -> float | None:
        # Use abs() so correct SELL signals (negative pct_change) count as gains,
        # not as losses — profit factor measures magnitude of wins vs losses.
        wins   = sum(abs(i["pct_change"]) for i in items if i["correct"])
        losses = sum(abs(i["pct_change"]) for i in items if not i["correct"])
        return round(wins / losses, 2) if losses > 0 else None

    offset = (page - 1) * page_size
    page_signals = results[offset: offset + page_size]

    return {
        "lookback_days": lookback_days,
        "total_signals": len(results),
        "buy_count": len(buy_r),
        "sell_count": len(sell_r),
        "buy_accuracy": _accuracy(buy_r),
        "sell_accuracy": _accuracy(sell_r),
        "overall_accuracy": _accuracy(results),
        "avg_buy_return_pct": _avg_return(buy_r),
        "avg_sell_return_pct": _avg_return(sell_r),
        "profit_factor": _profit_factor(results),
        "page": page,
        "page_size": page_size,
        "has_more": offset + page_size < len(results),
        "signals": page_signals,
    }


@router.get("/rolling_accuracy")
def rolling_accuracy(
    window: int = Query(30, ge=7, le=90),
    lookback_days: int = Query(180, ge=60, le=730),
    session: Session = Depends(get_session),
):
    """Rolling accuracy of BUY signals over a sliding window.

    Returns a time-series of {date, accuracy_30d, signal_count} for each day in
    the lookback period where at least `window` evaluated BUY signals exist.
    Also returns a drift_warning flag if the latest window accuracy < 55%.
    """
    import bisect

    # Require 7+ calendar days of forward data so the 5-day exit price exists.
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    rows = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.ts >= cutoff,
            Signal.ts <= outcome_cutoff,
            Signal.signal == SignalType.BUY,
        )
        .order_by(Signal.ts.asc())
    ).all()

    if not rows:
        return {"window": window, "lookback_days": lookback_days, "series": [], "drift_warning": False, "latest_accuracy": None}

    stock_ids = list({sig.stock_id for sig, _ in rows})
    # Fetch prices from cutoff through today so we can compute 5-day forward exits.
    price_since = (cutoff - timedelta(days=2)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids), Price.timeframe == TimeFrame.D1, Price.ts >= price_since)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    _pts: dict[int, list] = {}
    _pclose: dict[int, list] = {}
    for row in price_rows:
        sid = row.stock_id
        if sid not in _pts:
            _pts[sid] = []
            _pclose[sid] = []
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _pts[sid].append(d)
        _pclose[sid].append(float(row.close))

    def first_close_after(sid, after_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, after_date)
        return _pclose[sid][idx] if idx < len(ts_list) else None

    # Build list of evaluated signals using fixed 5-day forward exit (same as main accuracy table).
    # This ensures every signal in the drift series is evaluated over the same holding period.
    evaluated: list[tuple[date, bool]] = []
    seen: set[tuple] = set()
    for sig, sym in rows:
        sig_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, sig_date, sig.horizon)
        if key in seen:
            continue
        seen.add(key)
        entry = first_close_after(sig.stock_id, sig_date)
        exit_target = sig_date + timedelta(days=7)  # 7 calendar days ≈ 5 trading days
        exit_ = first_close_after(sig.stock_id, exit_target)
        if entry is None or exit_ is None or entry <= 0:
            continue
        correct = exit_ > entry
        evaluated.append((sig_date, correct))

    if not evaluated:
        return {"window": window, "lookback_days": lookback_days, "series": [], "drift_warning": False, "latest_accuracy": None}

    # Compute rolling accuracy: for each unique date in the dataset, use the
    # trailing `window` calendar days of evaluated signals ending on that date.
    unique_dates = sorted({d for d, _ in evaluated})
    series = []
    for end_date in unique_dates:
        start_date = end_date - timedelta(days=window - 1)
        window_sigs = [(d, c) for d, c in evaluated if start_date <= d <= end_date]
        if len(window_sigs) < 3:
            continue
        acc = round(sum(1 for _, c in window_sigs if c) / len(window_sigs) * 100, 1)
        series.append({"date": end_date.isoformat(), "accuracy": acc, "signal_count": len(window_sigs)})

    latest_accuracy = series[-1]["accuracy"] if series else None
    drift_warning = latest_accuracy is not None and latest_accuracy < 55.0

    return {
        "window": window,
        "lookback_days": lookback_days,
        "series": series,
        "drift_warning": drift_warning,
        "latest_accuracy": latest_accuracy,
    }


@router.get("/ml-weight-validation")
def ml_weight_validation(
    lookback_days: int = Query(180, ge=30, le=730),
    session: Session = Depends(get_session),
):
    """Empirically sweep ML fusion weights 0→1 to find which blend best predicted price direction.

    For each BUY signal in the lookback window, reads ml_probability and ta_score from the
    reasons JSON, pairs with the actual price outcome, then tries 21 weight values (0.00 to 1.00
    in 0.05 steps). Returns accuracy and avg_return_pct at each weight so the caller can see
    the empirical optimum vs the current formula range (0.40–0.75).
    """
    import bisect

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    rows = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal == SignalType.BUY)
    ).all()

    if not rows:
        return {"lookback_days": lookback_days, "signal_count": 0, "curve": [], "optimal_weight": None}

    stock_ids = list({sig.stock_id for sig, _ in rows})
    price_since = (cutoff - timedelta(days=5)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= price_since)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    _pts: dict[int, list] = {}
    _pclose: dict[int, list] = {}
    for row in price_rows:
        sid = row.stock_id
        if sid not in _pts:
            _pts[sid] = []
            _pclose[sid] = []
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _pts[sid].append(d)
        _pclose[sid].append(float(row.close))

    def _first_close_after(sid, after_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None, None
        idx = bisect.bisect_right(ts_list, after_date)
        if idx >= len(ts_list):
            return None, None
        return _pclose[sid][idx], ts_list[idx]

    def _most_recent_close(sid):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None, None
        return _pclose[sid][-1], ts_list[-1]

    # Build list of (ml_prob, ta_score, pct_change) for signals with complete data
    observations: list[tuple[float, float, float]] = []
    seen: set[tuple] = set()

    for sig, _ in rows:
        signal_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, signal_date)
        if key in seen:
            continue
        seen.add(key)

        reasons = sig.reasons or {}
        ml_prob = reasons.get("ml_probability")
        ta_score = reasons.get("ta_score")
        if ml_prob is None or ta_score is None:
            continue

        entry, entry_date = _first_close_after(sig.stock_id, signal_date)
        exit_p, exit_date = _most_recent_close(sig.stock_id)
        if entry is None or exit_p is None or exit_date is None or exit_date <= signal_date:
            continue
        if entry <= 0:
            continue

        pct = (exit_p - entry) / entry * 100
        observations.append((float(ml_prob), float(ta_score), pct))

    if not observations:
        return {"lookback_days": lookback_days, "signal_count": 0, "curve": [], "optimal_weight": None}

    # Sweep weight from 0.0 to 1.0 in 0.05 steps
    weights = [round(w / 20, 2) for w in range(21)]  # 0.00, 0.05, ..., 1.00
    curve = []
    best_acc = -1.0
    optimal_weight = 0.5

    for w in weights:
        correct = 0
        returns = []
        fired = 0
        for ml_p, ta_s, pct in observations:
            fused = w * ml_p + (1 - w) * ta_s
            if fused > 0.5:
                fired += 1
                if pct > 0:
                    correct += 1
                returns.append(pct)

        acc = round(correct / fired * 100, 1) if fired else None
        avg_ret = round(sum(returns) / len(returns), 2) if returns else None

        curve.append({"weight": w, "accuracy": acc, "avg_return_pct": avg_ret})

        if acc is not None and acc > best_acc:
            best_acc = acc
            optimal_weight = w

    return {
        "lookback_days": lookback_days,
        "signal_count": len(observations),
        "optimal_weight": optimal_weight,
        "optimal_accuracy": round(best_acc, 1),
        "current_formula_range": [0.40, 0.75],
        "curve": curve,
    }


@router.post("/calibrate_ml_weight")
def calibrate_ml_weight(
    lookback_days: int = Query(180, ge=30, le=730),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Find the empirically optimal ML fusion weight and apply it as the global cap.

    Runs the same weight sweep as /ml-weight-validation, searches for the weight with the
    highest BUY accuracy on the calibration (train) slice, then only applies it if it ALSO
    beats a neutral baseline (weight=0.5) on the held-out validation slice — writes to
    ml_weight_override.json and updates the in-process value only when validated.
    Returns the chosen weight (or None if nothing validated) and the full accuracy curve.
    """
    from ..generators.signals import set_ml_weight_global_cap, _ml_weight_global_cap as prev_cap
    import bisect

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    rows = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal == SignalType.BUY)
    ).all()

    if not rows:
        return {"applied": False, "reason": "no_signals", "optimal_weight": None}

    stock_ids = list({sig.stock_id for sig, _ in rows})
    price_since = (cutoff - timedelta(days=5)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= price_since)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    _pts: dict[int, list] = {}
    _pclose: dict[int, list] = {}
    for row in price_rows:
        sid = row.stock_id
        if sid not in _pts:
            _pts[sid] = []
            _pclose[sid] = []
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _pts[sid].append(d)
        _pclose[sid].append(float(row.close))

    def _first_close_at_or_after(sid, target_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_left(ts_list, target_date)
        if idx >= len(ts_list):
            return None
        return _pclose[sid][idx]

    def _first_close_after(sid, after_date):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, after_date)
        if idx >= len(ts_list):
            return None
        return _pclose[sid][idx]

    # T234-ML-WEIGHT-NO-VALIDATION-GATE: exit_p previously used whatever the MOST RECENT close
    # happened to be, mixing holding periods from days to ~180 days (lookback_days) into the same
    # sweep — a signal evaluated the day it fired and one evaluated 6 months later were treated as
    # equally-measured observations. Now uses each signal's own style-specific fixed hold window
    # (_OUTCOME_HOLD_DAYS, the same convention outcomes_calibrate_apply/tune_style_profiles already
    # use), so every observation measures the same kind of thing: return AFTER the horizon this
    # signal was actually meant to be held for.
    observations: list[tuple[float, float, float, object]] = []
    seen: set[tuple] = set()
    for sig, _ in rows:
        signal_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, signal_date)
        if key in seen:
            continue
        seen.add(key)
        reasons = sig.reasons or {}
        ml_prob = reasons.get("ml_probability")
        ta_score = reasons.get("ta_score")
        if ml_prob is None or ta_score is None:
            continue
        ts_list = _pts.get(sig.stock_id)
        if not ts_list:
            continue
        entry = _first_close_after(sig.stock_id, signal_date)
        if entry is None or entry <= 0:
            continue
        hold_days = _OUTCOME_HOLD_DAYS.get(sig.horizon.value, 14)
        target_exit_date = signal_date + timedelta(days=hold_days)
        exit_p = _first_close_at_or_after(sig.stock_id, target_exit_date)
        if exit_p is None:
            continue  # hold window hasn't closed yet — not a resolved observation
        pct = (exit_p - entry) / entry * 100
        observations.append((float(ml_prob), float(ta_score), pct, signal_date))

    if not observations:
        return {"applied": False, "reason": "no_observations", "optimal_weight": None}

    # Sort by date, split older 70% for calibration, newer 30% for validation
    observations.sort(key=lambda x: x[3])
    split = max(1, int(len(observations) * 0.7))
    calib_obs = observations[:split]
    val_obs = observations[split:]

    MIN_VAL_SAMPLES = 15  # same floor already proven in T232-OC3 / T234-SIG-INSAMPLE-GATE-TUNING

    weights = [round(w / 20, 2) for w in range(21)]
    best_acc = -1.0
    optimal_weight = 0.5
    curve = []

    def _accuracy_and_return(obs, w):
        correct = fired = 0
        returns = []
        for ml_p, ta_s, pct, _ in obs:
            fused = w * ml_p + (1 - w) * ta_s
            if fused > 0.5:
                fired += 1
                returns.append(pct)
                if pct > 0:
                    correct += 1
        acc = correct / fired if fired else None
        avg_ret = sum(returns) / len(returns) if returns else None
        return acc, fired, avg_ret

    for w in weights:
        # Select weight using calibration set only
        calib_acc, _, _ = _accuracy_and_return(calib_obs, w)
        if calib_acc is not None and calib_acc > best_acc:
            best_acc = calib_acc
            optimal_weight = w

        # Curve accuracy shown on validation set (display only, same as before)
        v_acc, v_fired, v_avg_ret = _accuracy_and_return(val_obs, w)
        curve.append({
            "weight": w,
            "accuracy": round(v_acc * 100, 1) if v_acc is not None else None,
            "avg_return_pct": round(v_avg_ret, 2) if v_avg_ret is not None else None,
        })

    # T234-ML-WEIGHT-NO-VALIDATION-GATE: only apply optimal_weight if it ALSO beats a neutral
    # baseline (0.5 — equal TA/ML blend) on the validation slice the search never saw. Previously
    # set_ml_weight_global_cap() ran unconditionally regardless of what validation showed.
    if len(val_obs) < MIN_VAL_SAMPLES:
        return {
            "applied": False,
            "reason": f"only {len(val_obs)} validation-slice observations (need {MIN_VAL_SAMPLES})",
            "optimal_weight": optimal_weight,
            "signal_count": len(observations),
            "lookback_days": lookback_days,
            "curve": curve,
        }

    candidate_acc, candidate_fired, candidate_avg_ret = _accuracy_and_return(val_obs, optimal_weight)
    baseline_acc, baseline_fired, baseline_avg_ret = _accuracy_and_return(val_obs, 0.5)

    candidate_ev = (candidate_avg_ret or 0.0)
    baseline_ev = (baseline_avg_ret or 0.0)
    validated = (
        candidate_fired >= MIN_VAL_SAMPLES
        and candidate_acc is not None
        and candidate_ev > baseline_ev
    )

    if not validated:
        return {
            "applied": False,
            "reason": "candidate weight did not beat the 0.5 baseline on the validation slice",
            "optimal_weight": optimal_weight,
            "candidate_validation_ev_pct": round(candidate_ev, 2) if candidate_fired else None,
            "baseline_validation_ev_pct": round(baseline_ev, 2) if baseline_fired else None,
            "signal_count": len(observations),
            "lookback_days": lookback_days,
            "curve": curve,
        }

    set_ml_weight_global_cap(optimal_weight)
    log.info("calibrate_ml_weight: applied cap=%.2f (val_acc=%.1f%%, val_ev=%.2f%%, n=%d, lookback=%dd)",
             optimal_weight, (candidate_acc or 0.0) * 100, candidate_ev, len(observations), lookback_days)

    return {
        "applied": True,
        "optimal_weight": optimal_weight,
        "optimal_accuracy": round((candidate_acc or 0.0) * 100, 1),
        "candidate_validation_ev_pct": round(candidate_ev, 2),
        "baseline_validation_ev_pct": round(baseline_ev, 2),
        "signal_count": len(observations),
        "lookback_days": lookback_days,
        "previous_cap": prev_cap,
        "curve": curve,
    }


@router.get("/factor-exposure")
def factor_exposure(
    lookback_days: int = Query(90, ge=7, le=365),
    session: Session = Depends(get_session),
):
    """Factor tilt analysis of BUY signals — compares factor values for correct vs wrong calls.

    Extracts numeric factors from the reasons JSON of each BUY signal, pairs with
    price outcome (did price rise after signal?), then returns per-factor averages
    split by correct / wrong so the caller can see which factor dimensions correlate
    with successful signals.
    """
    import bisect

    cache_key = f"signals:cache:factor_exposure:{lookback_days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=1)

    rows = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Signal.ts >= cutoff, Signal.ts <= outcome_cutoff)
        .where(Signal.signal == SignalType.BUY)
    ).all()

    if not rows:
        return {"lookback_days": lookback_days, "signal_count": 0, "factors": []}

    stock_ids = list({sig.stock_id for sig, _ in rows})
    price_since = (cutoff - timedelta(days=5)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= price_since)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    _pts: dict[int, list] = {}
    _pclose: dict[int, list] = {}
    for row in price_rows:
        sid = row.stock_id
        if sid not in _pts:
            _pts[sid] = []
            _pclose[sid] = []
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _pts[sid].append(d)
        _pclose[sid].append(float(row.close))

    def price_on_or_before(sid: int, d):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, d) - 1
        return _pclose[sid][idx] if idx >= 0 else None

    def _first_close_after_fe(sid: int, after_date):
        """Return the first close strictly after after_date (no lookahead)."""
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, after_date)
        return _pclose[sid][idx] if idx < len(ts_list) else None

    def most_recent_close_fe(sid: int):
        ts_list = _pts.get(sid)
        if not ts_list:
            return None
        return _pclose[sid][-1]

    # factor key → (label, neutral baseline, display scale)
    FACTORS = [
        ("rsi",             "RSI",             50.0,  100.0),
        ("adx",             "ADX",             20.0,  100.0),
        ("volume_z",        "Volume Z",         0.0,    3.0),
        ("ml_probability",  "ML Probability",   0.5,    1.0),
        ("news_sentiment",  "News Sentiment",  50.0,  100.0),
        ("ta_score",        "TA Score",         0.5,    1.0),
    ]

    correct_vals: dict[str, list[float]] = {f[0]: [] for f in FACTORS}
    wrong_vals:   dict[str, list[float]] = {f[0]: [] for f in FACTORS}
    seen: set[tuple] = set()
    total = 0

    for sig, _ in rows:
        signal_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, signal_date)
        if key in seen:
            continue
        seen.add(key)

        reasons = sig.reasons or {}
        entry = _first_close_after_fe(sig.stock_id, signal_date)
        if entry is None or entry <= 0:
            continue
        exit_p = most_recent_close_fe(sig.stock_id)
        if exit_p is None:
            continue

        total += 1
        correct = exit_p > entry

        for fname, _, _, _ in FACTORS:
            raw = reasons.get(fname)
            if raw is None:
                continue
            try:
                v = float(raw)
                (correct_vals if correct else wrong_vals)[fname].append(v)
            except (TypeError, ValueError):
                pass

    def _avg(lst: list[float]):
        return round(sum(lst) / len(lst), 4) if lst else None

    factors = []
    for fname, label, baseline, scale in FACTORS:
        c_avg = _avg(correct_vals[fname])
        w_avg = _avg(wrong_vals[fname])
        # deviation_pct: how far from neutral baseline as % of the scale range
        def _dev(v):
            if v is None:
                return None
            return round((v - baseline) / scale * 100, 1)
        factors.append({
            "key": fname,
            "label": label,
            "baseline": baseline,
            "scale": scale,
            "correct_avg": c_avg,
            "wrong_avg": w_avg,
            "correct_dev_pct": _dev(c_avg),
            "wrong_dev_pct": _dev(w_avg),
            "correct_count": len(correct_vals[fname]),
            "wrong_count": len(wrong_vals[fname]),
        })

    result = {"lookback_days": lookback_days, "signal_count": total, "factors": factors}
    _cache_set(cache_key, result)
    return result


@router.get("/trade_performance")
def trade_performance(
    lookback_days: int = Query(180, ge=7, le=730),
    symbol: str | None = None,
    horizon: str = Query("SWING", regex="^(SHORT|SWING|LONG|GROWTH)$"),
    market: str | None = Query(None, regex="^(US|HK)$", description="Filter to one market"),
    wait_exits: bool = Query(False, description="Treat same-horizon WAIT as exit (exits when momentum fades)"),
    max_hold_days: int | None = Query(None, ge=1, le=365, description="Force-close after N days. Defaults: SHORT=7, SWING=25, LONG=90"),
    min_confidence: float = Query(0.0, ge=0, le=100, description="Only include BUY signals with confidence >= this value"),
    session: Session = Depends(get_session),
):
    """BUY → SELL/WAIT trade-pair performance over a lookback window.

    Filters by horizon (SHORT/SWING/LONG) so exits are only matched within the
    same trading style — no cross-contamination between horizons.

    Exit rules (applied in priority order):
      1. SELL signal (always an exit)
      2. WAIT signal when wait_exits=True (exits on fading momentum, same horizon)
      3. max_hold_days time-stop (defaults: SHORT=7, SWING=25, LONG=90)
      4. Latest price if no exit found (open trade)

    Open trades (no exit found) use the latest available price.
    """
    import bisect
    from collections import defaultdict

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    horizon_enum = SignalHorizon(horizon)

    # Style-appropriate default max hold periods (prevents SHORT trades drifting for months)
    _default_max_hold = {"SHORT": 7, "SWING": 25, "LONG": 90}
    effective_max_hold: int = max_hold_days if max_hold_days is not None else _default_max_hold[horizon]

    # 1. All BUY signals in the window for the requested horizon
    q = (
        select(Signal, Stock.symbol, Stock.name)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(Stock.active.is_(True))
        .where(Signal.ts >= cutoff)
        .where(Signal.signal == SignalType.BUY)
        .where(Signal.horizon == horizon_enum)
        .order_by(Stock.symbol, Signal.ts)
    )
    if symbol:
        q = q.where(Stock.symbol == symbol.upper())
    if market:
        q = q.where(Stock.market == market.upper())
    if min_confidence > 0:
        q = q.where(Signal.confidence >= min_confidence)
    buy_rows = session.execute(q).all()

    if not buy_rows:
        return {"lookback_days": lookback_days, "closed_trades": 0, "open_trades": 0,
                "win_rate": None, "avg_return_pct": None, "avg_win_pct": None,
                "avg_loss_pct": None, "profit_factor": None, "avg_hold_days": None,
                "by_symbol": [], "trades": []}

    stock_ids = list({sig.stock_id for sig, _, _ in buy_rows})

    # 2. Exit signals — SELL always exits; WAIT exits when wait_exits=True.
    # Both are filtered by the same horizon to prevent cross-style contamination
    # (the old phantom-0-day bug was SHORT=BUY + SWING=WAIT in the same batch).
    exit_signal_filter = (
        Signal.signal.in_([SignalType.SELL, SignalType.WAIT])
        if wait_exits
        else Signal.signal == SignalType.SELL
    )
    exit_rows = session.execute(
        select(Signal.stock_id, Signal.ts, Signal.signal)
        .where(Signal.stock_id.in_(stock_ids))
        .where(exit_signal_filter)
        .where(Signal.horizon == horizon_enum)
        .order_by(Signal.stock_id, Signal.ts)
    ).all()

    # stock_id → (sorted ts list, signal value list)
    _exit_ts: dict[int, list] = defaultdict(list)
    _exit_val: dict[int, list] = defaultdict(list)
    for row in exit_rows:
        _exit_ts[row.stock_id].append(row.ts)
        _exit_val[row.stock_id].append(row.signal.value)

    # 3. All D1 prices for those stocks from just before the lookback window to today
    since_date = (cutoff - timedelta(days=7)).date()
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(stock_ids))
        .where(Price.timeframe == TimeFrame.D1)
        .where(Price.ts >= since_date)
        .order_by(Price.stock_id, Price.ts)
    ).all()

    # stock_id → (sorted date list, close list)
    # Normalise Price.ts to date — driver may return datetime or date depending on schema
    _price_ts: dict[int, list] = defaultdict(list)
    _price_close: dict[int, list] = defaultdict(list)
    for row in price_rows:
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _price_ts[row.stock_id].append(d)
        _price_close[row.stock_id].append(float(row.close))

    def price_on_or_before(sid: int, d) -> float | None:
        ts_list = _price_ts.get(sid)
        if not ts_list:
            return None
        idx = bisect.bisect_right(ts_list, d) - 1
        return _price_close[sid][idx] if idx >= 0 else None

    def latest_price(sid: int):
        ts_list = _price_ts.get(sid)
        if not ts_list:
            return None, None
        return _price_close[sid][-1], ts_list[-1]

    def next_exit(sid: int, after_ts):
        ts_list = _exit_ts.get(sid)
        if not ts_list:
            return None, None
        idx = bisect.bisect_right(ts_list, after_ts)
        if idx >= len(ts_list):
            return None, None
        return ts_list[idx], _exit_val[sid][idx]

    # 4. Pair each BUY with its exit — pure Python, no more per-signal queries.
    # Two dedup guards prevent duplicate trades from intraday scheduler refreshes:
    #   last_exit_ts — blocks BUYs before the previous closed trade's exit timestamp.
    #   in_open_trade — blocks new BUYs while an open (unclosed) position already exists.
    last_exit_ts: dict[int, object] = {}  # stock_id → exit ts of last closed trade
    in_open_trade: set[int] = set()       # stock_ids already represented by an open trade

    trades = []
    for sig, sym, name in buy_rows:
        sid = sig.stock_id
        # Guard 1: BUY arrived before the last closed trade's exit — duplicate refresh.
        if sid in last_exit_ts and sig.ts <= last_exit_ts[sid]:
            continue
        # Guard 2: We're already tracking an open position for this stock.
        if sid in in_open_trade:
            continue

        # Entry on the first trading day with actual price data after the signal date.
        # price_on_or_before(signal_date + 1 calendar day) was wrong for Friday signals:
        # signal_date + 1 = Saturday → price_on_or_before returns Friday's close (lookahead).
        _sid_ts = _price_ts.get(sid, [])
        _entry_idx = bisect.bisect_right(_sid_ts, sig.ts.date())
        if _entry_idx >= len(_sid_ts):
            continue
        entry_date = _sid_ts[_entry_idx]
        entry_price = _price_close[sid][_entry_idx]

        exit_ts, exit_signal_val = next_exit(sid, sig.ts)

        # Apply max-hold time-stop: if no exit or exit is beyond the limit, cut at max_hold_days.
        # This prevents SHORT (1-5d) trades from drifting for weeks with no exit.
        max_exit_date = entry_date + timedelta(days=effective_max_hold)
        if exit_ts is not None:
            signal_exit_date = exit_ts.date() + timedelta(days=1)
            if signal_exit_date <= max_exit_date:
                # Normal signal exit within the hold window
                exit_date  = signal_exit_date
                exit_price = price_on_or_before(sid, exit_date)
                status     = "closed"
                last_exit_ts[sid] = exit_ts
            else:
                # Signal exit is beyond max hold — apply time-stop instead
                exit_date       = max_exit_date
                exit_price      = price_on_or_before(sid, exit_date)
                exit_signal_val = f"TIME({effective_max_hold}d)"
                status          = "closed"
                last_exit_ts[sid] = exit_ts  # still mark so we don't re-enter
        else:
            # No exit signal found — apply time-stop if position has exceeded limit
            today = datetime.now(timezone.utc).date()
            if today >= max_exit_date:
                # Time-stop triggered
                exit_date       = max_exit_date
                exit_price      = price_on_or_before(sid, exit_date)
                exit_signal_val = f"TIME({effective_max_hold}d)"
                status          = "closed"
            else:
                # Still within hold window — open position, use latest price
                exit_price, exit_ts_raw = latest_price(sid)
                if exit_price is None:
                    continue
                exit_date       = exit_ts_raw.date() if isinstance(exit_ts_raw, datetime) else exit_ts_raw
                exit_signal_val = "OPEN"
                status          = "open"
                in_open_trade.add(sid)

        if exit_price is None or entry_price <= 0:
            continue

        pct       = (exit_price - entry_price) / entry_price * 100
        hold_days = (exit_date - entry_date).days

        trades.append({
            "symbol":           sym,
            "name":             name,
            "status":           status,
            "entry_date":       entry_date.isoformat(),
            "exit_date":        exit_date.isoformat(),
            "entry_price":      round(entry_price, 4),
            "exit_price":       round(exit_price, 4),
            "pct_return":       round(pct, 2),
            "hold_days":        hold_days,
            "win":              pct > 0,
            "exit_signal":      exit_signal_val,
            "entry_confidence": round(sig.confidence, 1),
        })

    import math, statistics as _stats

    closed = [t for t in trades if t["status"] == "closed"]
    open_t = [t for t in trades if t["status"] == "open"]
    wins   = [t for t in closed if t["win"]]
    losses = [t for t in closed if not t["win"]]

    gross_wins   = sum(t["pct_return"] for t in wins)
    gross_losses = abs(sum(t["pct_return"] for t in losses))

    by_sym: dict = defaultdict(lambda: {"trades": 0, "wins": 0, "total_return": 0.0, "hold_days": 0})
    for t in closed:
        s = t["symbol"]
        by_sym[s]["trades"]       += 1
        by_sym[s]["wins"]         += int(t["win"])
        by_sym[s]["total_return"] += t["pct_return"]
        by_sym[s]["hold_days"]    += t["hold_days"]
    symbol_summary = [
        {
            "symbol":        s,
            "trades":        v["trades"],
            "win_rate":      round(v["wins"] / v["trades"] * 100, 1),
            "avg_return":    round(v["total_return"] / v["trades"], 2),
            "avg_hold_days": round(v["hold_days"] / v["trades"], 1),
        }
        for s, v in sorted(by_sym.items())
    ]

    # ── Equity curve (closed trades compounded in entry-date order) ──────────
    sorted_closed = sorted(closed, key=lambda t: t["entry_date"])
    equity = 1.0
    equity_curve: list = []
    if sorted_closed:
        equity_curve.append({"date": sorted_closed[0]["entry_date"], "equity": 1.0})
    for t in sorted_closed:
        equity *= 1 + t["pct_return"] / 100
        equity_curve.append({"date": t["exit_date"], "equity": round(equity, 4)})

    total_return = round((equity - 1) * 100, 2) if sorted_closed else None

    # ── Sharpe ratio (per-trade returns, annualised) ─────────────────────────
    sharpe = None
    if len(closed) >= 2:
        returns = [t["pct_return"] for t in closed]
        avg_hd  = max(sum(t["hold_days"] for t in closed) / len(closed), 1)
        mean_r  = _stats.mean(returns)
        std_r   = _stats.stdev(returns)
        if std_r > 0:
            sharpe = round(mean_r / std_r * math.sqrt(252 / avg_hd), 2)

    # ── Max drawdown from equity curve ───────────────────────────────────────
    max_drawdown = None
    if equity_curve:
        peak = 1.0
        worst = 0.0
        for pt in equity_curve:
            if pt["equity"] > peak:
                peak = pt["equity"]
            dd = (pt["equity"] - peak) / peak * 100
            if dd < worst:
                worst = dd
        max_drawdown = round(worst, 2)  # negative e.g. -12.3

    # ── Calmar ratio ─────────────────────────────────────────────────────────
    calmar = None
    if total_return is not None and max_drawdown is not None and max_drawdown < 0 and sorted_closed:
        first_d   = date.fromisoformat(sorted_closed[0]["entry_date"])
        last_d    = date.fromisoformat(sorted_closed[-1]["exit_date"])
        total_days = (last_d - first_d).days
        if total_days > 0:
            ann_ret = total_return / total_days * 252
            calmar  = round(ann_ret / abs(max_drawdown), 2)

    # ── SPY benchmark return over the same date range ────────────────────────
    spy_return = None
    if sorted_closed:
        first_d = date.fromisoformat(sorted_closed[0]["entry_date"])
        last_d  = date.fromisoformat(sorted_closed[-1]["exit_date"])
        spy_stock = session.query(Stock).filter(Stock.symbol == "SPY").one_or_none()
        if spy_stock:
            spy_prices = session.execute(
                select(Price.ts, Price.close)
                .where(Price.stock_id == spy_stock.id)
                .where(Price.timeframe == TimeFrame.D1)
                .where(Price.ts >= first_d)
                .where(Price.ts <= last_d)
                .order_by(Price.ts)
            ).all()
            if len(spy_prices) >= 2:
                s0 = float(spy_prices[0].close)
                s1 = float(spy_prices[-1].close)
                spy_return = round((s1 - s0) / s0 * 100, 2)

    return {
        "lookback_days":  lookback_days,
        "closed_trades":  len(closed),
        "open_trades":    len(open_t),
        "win_rate":       round(len(wins) / len(closed) * 100, 1) if closed else None,
        "avg_return_pct": round(sum(t["pct_return"] for t in closed) / len(closed), 2) if closed else None,
        "avg_win_pct":    round(gross_wins / len(wins), 2) if wins else None,
        "avg_loss_pct":   round(-gross_losses / len(losses), 2) if losses else None,
        "profit_factor":  round(gross_wins / gross_losses, 2) if gross_losses > 0 else None,
        "avg_hold_days":  round(sum(t["hold_days"] for t in closed) / len(closed), 1) if closed else None,
        "total_return":   total_return,
        "sharpe":         sharpe,
        "max_drawdown":   max_drawdown,
        "calmar":         calmar,
        "spy_return":     spy_return,
        "equity_curve":   equity_curve,
        "by_symbol":      symbol_summary,
        "trades":         trades,
    }


@router.get("/suppressed")
def suppressed_signals(
    style: str = Query("SWING", description="Trading style: SHORT, SWING, LONG"),
    market: str | None = Query(None, description="Filter by market: US or HK"),
    session: Session = Depends(get_session),
):
    """All active stocks with their latest signal and full suppression condition breakdown.

    Returns each stock's most recent signal plus all filter states extracted from
    the reasons JSON, so the UI can show which conditions are suppressing each signal.
    Sorted by suppression_count descending, then bullish_probability descending.
    """
    horizon_filter = style.upper()

    latest_subq = (
        select(Signal.stock_id, Signal.horizon, func.max(Signal.ts).label("max_ts"))
        .group_by(Signal.stock_id, Signal.horizon)
        .subquery()
    )

    q = (
        select(
            Stock.symbol, Stock.name, Stock.market,
            Signal.stock_id, Signal.signal, Signal.horizon, Signal.confidence,
            Signal.bullish_probability, Signal.ts, Signal.reasons,
        )
        .join(Signal, Stock.id == Signal.stock_id)
        .join(
            latest_subq,
            (Signal.stock_id == latest_subq.c.stock_id)
            & (Signal.horizon == latest_subq.c.horizon)
            & (Signal.ts == latest_subq.c.max_ts),
        )
        .where(Stock.active.is_(True))
    )

    try:
        q = q.where(Signal.horizon == SignalHorizon(horizon_filter))
    except ValueError:
        pass

    if market:
        q = q.where(Stock.market == market.upper())

    rows = session.execute(q).all()

    # Fetch conviction gate results from market-data Redis cache
    conviction_data: dict = {}
    try:
        import httpx as _httpx
        cr = _httpx.get(f"{_settings.market_data_url}/stocks/conviction", timeout=4)
        if cr.status_code == 200:
            conviction_data = cr.json()
    except Exception:
        pass

    results = []

    for row in rows:
        r = row.reasons or {}

        conditions = {
            "weekly_gate":          bool(r.get("weekly_gate_fired", False)),
            # weekly_alignment=None means no weekly history — not a misalignment, skip filter
            "weekly_misalignment":  r.get("weekly_alignment") is False,
            "adx_choppy":           bool(r.get("adx_compression", False)),
            "high_vol_regime":      bool(r.get("high_vol_compression", False)),
            "low_breadth":          bool(r.get("breadth_compression", False)),
            "earnings_caution":     r.get("earnings_warning") in ("caution", "note", "watch"),
            "earnings_level":       r.get("earnings_warning"),
            "negative_news":        r.get("news_sentiment_flag") in ("strongly_negative", "negative"),
            "news_level":           r.get("news_sentiment_flag"),
            "rs_lagging":           r.get("rs_flag") == "lagging_sector",
            "bearish_options":      r.get("options_flag") in ("elevated_put_volume", "slightly_elevated_puts"),
            "options_level":        r.get("options_flag"),
            "stale_data":           bool(r.get("stale_price_warning", False)),
            "insufficient_history": bool(r.get("insufficient_history_warning", False)),
            "compression_cap":      bool(r.get("compression_cap_applied", False)),
        }

        suppression_count = sum(
            1 for k, v in conditions.items()
            if k not in ("earnings_level", "news_level", "options_level") and v is True
        )

        conv = conviction_data.get(f"{row.symbol}:{horizon_filter}")
        results.append({
            "symbol":              row.symbol,
            "name":                row.name,
            "market":              row.market,
            "signal":              row.signal.value,
            "horizon":             row.horizon.value,
            "confidence":          round(row.confidence, 1),
            "bullish_probability": round(row.bullish_probability, 4) if row.bullish_probability else None,
            "ts":                  row.ts.isoformat() if row.ts else None,
            "conditions":          conditions,
            "suppression_count":   suppression_count,
            "market_regime":       r.get("market_regime"),
            "weekly_rsi":          r.get("weekly_rsi"),
            "weekly_trend":        r.get("weekly_trend"),
            "rsi":                 r.get("rsi"),
            "adx":                 r.get("adx"),
            "breadth_pct":         r.get("breadth_pct"),
            "days_to_earnings":    r.get("days_to_earnings"),
            "news_sentiment":      r.get("news_sentiment"),
            "rs_score":            r.get("rs_score"),
            "conviction":          conv,
            # SA-19 pillar scores — 0-1 per dimension; None if signal pre-dates SA-19
            "pillar_trend":        r.get("pillar_trend"),
            "pillar_momentum":     r.get("pillar_momentum"),
            "pillar_volume":       r.get("pillar_volume"),
            "pillar_structure":    r.get("pillar_structure"),
            "pillars_active":      r.get("independent_pillars_active"),
            # T175/T181: catalyst intelligence scores (from event-intelligence, stored in reasons by _bulk_persist)
            "insider_score":       r.get("insider_score"),
            "congress_score":      r.get("congress_score"),
            "catalyst_score":      r.get("catalyst_score"),
            "catalyst_prob_adj":   r.get("catalyst_prob_adj"),
        })

    # Compute days_active per condition — how many consecutive days each flag has been True.
    # Bulk-load the last 90 days of signals for all stocks in the result set.
    stock_ids = [row.stock_id for row in rows]
    cutoff_90 = datetime.now(timezone.utc) - timedelta(days=90)
    try:
        _horizon_enum = SignalHorizon(horizon_filter)
        hist_rows = session.execute(
            select(Signal.stock_id, Signal.ts, Signal.reasons)
            .where(
                Signal.stock_id.in_(stock_ids),
                Signal.horizon == _horizon_enum,
                Signal.ts >= cutoff_90,
            )
            .order_by(Signal.stock_id, Signal.ts.desc())
        ).all() if rows else []
    except ValueError:
        hist_rows = []

    # Group history by stock_id
    from collections import defaultdict
    hist_by_stock: dict[int, list] = defaultdict(list)
    for h in hist_rows:
        hist_by_stock[h.stock_id].append(h)

    _CONDITION_KEYS = [
        "weekly_gate", "weekly_misalignment", "adx_choppy", "high_vol_regime",
        "low_breadth", "earnings_caution", "negative_news", "rs_lagging",
        "bearish_options", "stale_data", "insufficient_history", "compression_cap",
    ]

    def _extract_conditions(reasons: dict) -> dict[str, bool]:
        return {
            "weekly_gate":          bool(reasons.get("weekly_gate_fired", False)),
            "weekly_misalignment":  reasons.get("weekly_alignment") is False,
            "adx_choppy":           bool(reasons.get("adx_compression", False)),
            "high_vol_regime":      bool(reasons.get("high_vol_compression", False)),
            "low_breadth":          bool(reasons.get("breadth_compression", False)),
            "earnings_caution":     reasons.get("earnings_warning") in ("caution", "note", "watch"),
            "negative_news":        reasons.get("news_sentiment_flag") in ("strongly_negative", "negative"),
            "rs_lagging":           reasons.get("rs_flag") == "lagging_sector",
            "bearish_options":      reasons.get("options_flag") in ("elevated_put_volume", "slightly_elevated_puts"),
            "stale_data":           bool(reasons.get("stale_price_warning", False)),
            "insufficient_history": bool(reasons.get("insufficient_history_warning", False)),
            "compression_cap":      bool(reasons.get("compression_cap_applied", False)),
        }

    def _days_active(stock_id: int) -> dict[str, int]:
        """Walk back through signal history; count consecutive days each condition is True."""
        history = hist_by_stock.get(stock_id, [])
        streak: dict[str, int] = {k: 0 for k in _CONDITION_KEYS}
        if not history:
            return streak
        # history is ordered ts desc; walk from most recent, stop streak when condition flips to False
        active: dict[str, bool] = {k: True for k in _CONDITION_KEYS}
        prev_ts: datetime | None = None
        for h in history:
            conds = _extract_conditions(h.reasons or {})
            ts = h.ts.replace(tzinfo=timezone.utc) if h.ts.tzinfo is None else h.ts
            # Gap > 2 calendar days (more than a weekend) resets all active streaks
            if prev_ts is not None and (prev_ts - ts).days > 2:
                for k in _CONDITION_KEYS:
                    active[k] = False
            for k in _CONDITION_KEYS:
                if not active[k]:
                    continue
                if conds.get(k, False):
                    streak[k] += 1
                else:
                    active[k] = False
            prev_ts = ts
        return streak

    # Attach days_active to each result
    days_active_by_symbol: dict[str, dict[str, int]] = {}
    for row in rows:
        days_active_by_symbol[row.symbol] = _days_active(row.stock_id)

    for item in results:
        item["days_active"] = days_active_by_symbol.get(item["symbol"], {})

    results.sort(key=lambda x: (-x["suppression_count"], -(x["bullish_probability"] or 0)))
    return results


@router.get("/filter_audit")
def filter_audit(
    lookback_days: int = Query(180, ge=30, le=730),
    style: str = Query("SWING", regex="^(SHORT|SWING|LONG|GROWTH)$"),
    hold_days: int = Query(10, ge=1, le=60, description="Days after signal to measure outcome"),
    session: Session = Depends(get_session),
):
    """Correlate active suppression filter count with actual trade win rate.

    For every BUY signal in the lookback window, counts how many suppression
    filters were active at signal time (from reasons JSON), then looks up the
    actual price return hold_days later.  Returns win-rate breakdown by filter
    count so you can see whether heavily-filtered signals genuinely perform worse.
    """
    cache_key = f"signals:cache:filter_audit:{lookback_days}:{style}:{hold_days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    since = date.today() - timedelta(days=lookback_days)
    try:
        horizon_enum = SignalHorizon(style.upper())
    except ValueError:
        horizon_enum = SignalHorizon.SWING

    rows = session.execute(
        select(Signal.ts, Signal.reasons, Signal.stock_id, Stock.symbol)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.signal == SignalType.BUY,
            Signal.horizon == horizon_enum,
            Signal.ts >= since,
            Signal.reasons.isnot(None),
        )
        .order_by(Signal.ts)
    ).all()

    SUPPRESSION_BOOLEAN = [
        "weekly_gate_fired", "adx_compression",
        "high_vol_compression", "breadth_compression",
        "stale_price_warning", "insufficient_history_warning",
    ]
    SUPPRESSION_NAMED = {
        "weekly_alignment":    lambda v: v is False,
        "earnings_warning":    lambda v: v in ("caution", "note", "watch"),
        "news_sentiment_flag": lambda v: v in ("strongly_negative", "negative"),
        "rs_flag":             lambda v: v == "lagging_sector",
        "options_flag":        lambda v: v in ("elevated_put_volume", "slightly_elevated_puts"),
    }

    stock_ids = list({r.stock_id for r in rows})
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= since,
            Price.ts <= date.today(),
        )
        .order_by(Price.stock_id, Price.ts)
    ).all()

    from collections import defaultdict
    prices_by_stock: dict[int, list[tuple]] = defaultdict(list)
    for p in price_rows:
        # Convert datetime → date so _nearest_price can compare against date objects
        _d = p.ts.date() if hasattr(p.ts, "date") else p.ts
        prices_by_stock[p.stock_id].append((_d, float(p.close)))

    def _nearest_price(stock_id: int, target: date) -> float | None:
        candidates = prices_by_stock.get(stock_id, [])
        future = [(abs((d - target).days), c) for d, c in candidates if d >= target]
        return min(future, key=lambda x: x[0])[1] if future else None

    from collections import defaultdict as _dd
    buckets: dict[int, list[float]] = _dd(list)
    per_trade = []

    for row in rows:
        r = row.reasons or {}
        count = sum(1 for k in SUPPRESSION_BOOLEAN if r.get(k))
        count += sum(1 for k, test in SUPPRESSION_NAMED.items() if test(r.get(k)))

        signal_date = row.ts if isinstance(row.ts, date) else row.ts.date()
        exit_date   = signal_date + timedelta(days=hold_days)
        entry_price = _nearest_price(row.stock_id, signal_date)
        exit_price  = _nearest_price(row.stock_id, exit_date)

        if entry_price and exit_price and entry_price > 0:
            ret = (exit_price - entry_price) / entry_price
            buckets[count].append(ret)
            per_trade.append({
                "symbol":       row.symbol,
                "signal_date":  signal_date.isoformat(),
                "filter_count": count,
                "return_pct":   round(ret * 100, 2),
                "win":          ret > 0,
            })

    summary = []
    for fc in sorted(buckets):
        rets = buckets[fc]
        wins = sum(1 for r in rets if r > 0)
        summary.append({
            "filter_count":     fc,
            "trade_count":      len(rets),
            "win_rate_pct":     round(wins / len(rets) * 100, 1) if rets else None,
            "avg_return_pct":   round(sum(rets) / len(rets) * 100, 2) if rets else None,
            "median_return_pct": round(float(sorted(rets)[len(rets) // 2]) * 100, 2) if rets else None,
        })

    # Per-filter win rate: for each flag compare win rate when active vs inactive.
    # edge_pct negative = filter correctly suppresses weaker signals (good).
    # edge_pct positive = filter incorrectly suppresses stronger signals (harmful).
    all_filter_names = list(SUPPRESSION_BOOLEAN) + list(SUPPRESSION_NAMED.keys())
    filter_buckets: dict[str, dict[str, list[float]]] = {f: {"active": [], "inactive": []} for f in all_filter_names}

    for row in rows:
        r = row.reasons or {}
        filter_flags: dict[str, bool] = {}
        for k in SUPPRESSION_BOOLEAN:
            filter_flags[k] = bool(r.get(k))
        for k, test in SUPPRESSION_NAMED.items():
            filter_flags[k] = test(r.get(k))

        signal_date = row.ts if isinstance(row.ts, date) else row.ts.date()
        exit_date   = signal_date + timedelta(days=hold_days)
        entry_price = _nearest_price(row.stock_id, signal_date)
        exit_price  = _nearest_price(row.stock_id, exit_date)
        if not (entry_price and exit_price and entry_price > 0):
            continue
        ret = (exit_price - entry_price) / entry_price
        for fname, is_active in filter_flags.items():
            bucket = "active" if is_active else "inactive"
            filter_buckets[fname][bucket].append(ret)

    by_filter = []
    for fname in all_filter_names:
        act = filter_buckets[fname]["active"]
        inact = filter_buckets[fname]["inactive"]
        act_wr   = round(sum(1 for r in act   if r > 0) / len(act)   * 100, 1) if act   else None
        inact_wr = round(sum(1 for r in inact if r > 0) / len(inact) * 100, 1) if inact else None
        act_avg   = round(sum(act)   / len(act)   * 100, 2) if act   else None
        inact_avg = round(sum(inact) / len(inact) * 100, 2) if inact else None
        edge = round((act_wr or 0) - (inact_wr or 0), 1)  # negative = filter correctly suppresses bad trades
        by_filter.append({
            "filter":           fname,
            "n_active":         len(act),
            "n_inactive":       len(inact),
            "win_rate_active":  act_wr,
            "win_rate_inactive": inact_wr,
            "avg_return_active":  act_avg,
            "avg_return_inactive": inact_avg,
            "edge_pct": edge,  # negative means filter correctly blocks worse signals; positive means filter is harmful
            "verdict": "harmful" if edge > 5 else ("weak" if edge > -3 else "predictive"),
        })
    by_filter.sort(key=lambda x: x["edge_pct"])  # most predictive (most negative) first

    n_signals = len(rows)
    n_with_returns = len(per_trade)
    overall_wr = round(sum(1 for t in per_trade if t["win"]) / n_with_returns * 100, 1) if n_with_returns else None
    result = {
        "lookback_days":          lookback_days,
        "style":                  style,
        "hold_days":              hold_days,
        "n_buy_signals_found":    n_signals,
        "n_with_return_data":     n_with_returns,
        "overall_win_rate_pct":   overall_wr,
        "note": "n_with_return_data < n_buy_signals_found when exit date is in the future or price data is missing.",
        "by_filter_count":        summary,
        "by_filter_name":         by_filter,
        "trades":                 per_trade,
    }
    _cache_set(cache_key, result)
    return result


@router.post("/calibrate_ta_weights")
def calibrate_ta_weights(
    lookback_days: int = Query(365, ge=60, le=730),
    hold_days: int = Query(10, ge=3, le=30),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Fit logistic regression on historical BUY signals to derive data-driven TA weights.

    Reads the last `lookback_days` of BUY signals, extracts TA boolean features from the
    stored reasons JSON, looks up actual price returns over `hold_days`, then fits a logistic
    regression model. The resulting coefficients (clipped to [0, ∞]) become the new TA weights
    and are written to ta_weights.json next to the ML models directory.

    Returns the fitted weights and in-sample accuracy for review.
    """
    import json
    from pathlib import Path

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
    except ImportError:
        raise HTTPException(status_code=500, detail="scikit-learn not installed in signal-engine")

    from ..generators.signals import _TA_WEIGHTS_DEFAULT, _TA_WEIGHTS_PATH, set_ta_weights

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    rows = session.execute(
        select(Signal.ts, Signal.reasons, Signal.stock_id)
        .where(Signal.signal == SignalType.BUY, Signal.ts >= cutoff)
        .order_by(Signal.ts)
    ).all()

    if len(rows) < 50:
        raise HTTPException(status_code=400, detail=f"Need ≥50 BUY signals, found {len(rows)}")

    # TA boolean feature names (positive weights only — penalties excluded from regression)
    TA_FEATURES = [
        "above_sma50", "sma50_above_sma200", "golden_cross_event",
        "rsi_sweet_spot", "rsi_mild_oversold", "rsi_mild_overbought",
        "stoch_oversold", "stoch_cross_up",
        "macd_strong", "macd_positive", "macd_zero_cross_up",
        "bb_mid_zone", "price_above_vwap",
        "bullish_trend", "obv_trend_bullish", "volume_surge",
    ]

    # Map feature name → extractor from stored reasons JSON.
    # Keys must match what signals.py stores, not the weight-dict names.
    REASONS_MAP = {
        "above_sma50":            lambda r: bool(r.get("trend_above_sma50")),
        "sma50_above_sma200":     lambda r: bool(r.get("sma50_above_sma200")),
        "golden_cross_event":     lambda r: bool(r.get("golden_cross_event")),
        "rsi_sweet_spot":         lambda r: 45 < (r.get("rsi") or 0) < 65,
        "rsi_mild_oversold":      lambda r: 35 < (r.get("rsi") or 0) <= 45,
        "rsi_mild_overbought":    lambda r: 65 <= (r.get("rsi") or 0) < 72,
        "stoch_oversold":         lambda r: bool(r.get("stoch_rsi_oversold")),
        "stoch_cross_up":         lambda r: bool(r.get("stoch_rsi_cross_up")),
        "macd_strong":            lambda r: (r.get("macd_hist") or 0) > 0 and bool(r.get("macd_hist_expanding")),
        "macd_positive":          lambda r: (r.get("macd_hist") or 0) > 0 and not bool(r.get("macd_hist_expanding")),
        "macd_zero_cross_up":     lambda r: bool(r.get("macd_zero_cross_up")),
        "bb_mid_zone":            lambda r: 0.2 < (r.get("bb_pct_b") or 0) < 0.8,
        "price_above_vwap":       lambda r: r.get("price_above_vwap") is True,
        "bullish_trend":          lambda r: bool(r.get("adx_bullish")),
        "obv_trend_bullish":      lambda r: bool(r.get("obv_trend_bullish")),
        "volume_surge":           lambda r: (r.get("volume_z") or 0) > 0.5,
    }

    import bisect

    # Bulk-load all D1 prices for involved stocks — avoids N+1 queries in loop
    stock_ids = list({row.stock_id for row in rows})
    min_ts = min(row.ts for row in rows)
    max_ts_needed = datetime.now(timezone.utc) + timedelta(days=hold_days + 10)
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= min_ts,
            Price.ts <= max_ts_needed,
        )
        .order_by(Price.stock_id, Price.ts)
    ).all()
    # Build per-stock sorted list of (ts_naive_date, close)
    from collections import defaultdict
    _price_map: dict[int, list[tuple]] = defaultdict(list)
    for pr in price_rows:
        ts_date = pr.ts.date() if hasattr(pr.ts, "date") else pr.ts
        _price_map[pr.stock_id].append((ts_date, float(pr.close)))

    def _lookup_price(stock_id: int, on_or_after: "date") -> "float | None":
        bucket = _price_map.get(stock_id, [])
        if not bucket:
            return None
        dates = [b[0] for b in bucket]
        idx = bisect.bisect_left(dates, on_or_after)
        if idx >= len(bucket):
            return None
        return bucket[idx][1]

    X_rows, y_rows, skipped = [], [], 0
    for row in rows:
        try:
            reasons = json.loads(row.reasons) if isinstance(row.reasons, str) else (row.reasons or {})
        except Exception:
            skipped += 1
            continue

        signal_date = row.ts.date() if hasattr(row.ts, "date") else row.ts
        entry_price_row = _lookup_price(row.stock_id, signal_date)
        exit_price_row = _lookup_price(row.stock_id, signal_date + timedelta(days=hold_days))

        if entry_price_row is None or exit_price_row is None:
            skipped += 1
            continue

        fwd_ret = exit_price_row / entry_price_row - 1
        y_rows.append(1 if fwd_ret > 0 else 0)
        X_rows.append([float(REASONS_MAP[f](reasons)) for f in TA_FEATURES])

    if len(X_rows) < 30:
        raise HTTPException(status_code=400, detail=f"Only {len(X_rows)} usable rows after price lookup (skipped {skipped})")

    X = np.array(X_rows)
    y = np.array(y_rows)
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    clf = LogisticRegression(max_iter=500, C=1.0, random_state=42)

    from sklearn.model_selection import TimeSeriesSplit, cross_val_score
    cv_scores = cross_val_score(clf, X_scaled, y, cv=TimeSeriesSplit(n_splits=5), scoring="accuracy")
    accuracy = float(np.mean(cv_scores))

    # Fit on full data to derive the production weights
    clf.fit(X_scaled, y)
    coefs = clf.coef_[0]

    # Map coefficients → weight dict (clip negatives to 0 for positive-weight features)
    fitted = {feat: float(max(0.0, coef)) for feat, coef in zip(TA_FEATURES, coefs)}

    # Rescale so the sum of positive weights equals the sum of defaults (preserve scale)
    default_sum = sum(_TA_WEIGHTS_DEFAULT[k] for k in TA_FEATURES if k in _TA_WEIGHTS_DEFAULT)
    fitted_sum  = sum(fitted.values()) or 1.0
    scale_factor = default_sum / fitted_sum
    fitted_scaled = {k: round(v * scale_factor, 4) for k, v in fitted.items()}

    # Merge with defaults: keep penalty weights from defaults unchanged
    new_weights = dict(_TA_WEIGHTS_DEFAULT)
    new_weights.update(fitted_scaled)

    Path(_TA_WEIGHTS_PATH).parent.mkdir(parents=True, exist_ok=True)
    _tmp = Path(_TA_WEIGHTS_PATH).with_suffix(".tmp")
    _tmp.write_text(json.dumps(new_weights, indent=2))
    _os.replace(str(_tmp), str(_TA_WEIGHTS_PATH))
    # T228: also persist to Redis so weights survive Docker rebuilds (90-day TTL)
    try:
        _get_redis().setex("stockai:ta_weights", 90 * 86400, json.dumps(new_weights))
    except Exception:
        pass
    # T232-SIG6: the persistence writes above only affect the NEXT process restart unless the
    # in-process globals are also refreshed here — this used to be the entire bug (calibration
    # reported success but the running process kept scoring signals against the old weights
    # until it happened to restart for an unrelated reason).
    set_ta_weights(new_weights)
    log.info("calibrate_ta_weights: wrote %s (accuracy=%.3f, n=%d)", _TA_WEIGHTS_PATH, accuracy, len(X_rows))

    return {
        "status":           "ok",
        "n_signals":        len(rows),
        "n_usable":         len(X_rows),
        "n_skipped":        skipped,
        "in_sample_accuracy": round(accuracy, 4),
        "weights":          new_weights,
    }


@router.get("/admin/config")
def get_signal_config(_: str = Depends(get_current_username)):
    """Return current signal_thresholds.json values + last-loaded timestamp."""
    return {"thresholds": get_thresholds(), "loaded_at": thresholds_loaded_at()}


@router.post("/admin/reload_config")
def reload_signal_config(_: str = Depends(get_current_username)):
    """Hot-reload signal_thresholds.json without restarting the container."""
    try:
        result = reload_thresholds()
        return {"ok": True, **result}
    except Exception as exc:
        raise HTTPException(500, f"Failed to reload config: {exc}")


@router.post("/calibrate_conviction_weights")
def calibrate_conviction_weights(
    lookback_days: int = Query(365, ge=90, le=730),
    min_count: int = Query(10),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """AL-3: Fit logistic regression on conviction layer flags from signal_outcomes.

    For each boolean reason flag, computes edge = presence_in_winners − presence_in_losers.
    Writes conviction_weights.json with per-flag accuracy and edge data.
    Flags with accuracy < 52% are marked as noise layers.
    """
    import json
    from pathlib import Path

    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        raise HTTPException(500, "scikit-learn not installed")

    from ..generators.signals import _CONVICTION_WEIGHTS_PATH

    cutoff = date.today() - timedelta(days=lookback_days)

    rows = session.execute(
        select(SignalOutcome.is_correct, Signal.reasons)
        .join(Signal, Signal.id == SignalOutcome.signal_id)
        .where(
            SignalOutcome.signal_direction == "BUY",
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            Signal.reasons.is_not(None),
        )
    ).all()

    if len(rows) < 30:
        raise HTTPException(400, f"Need ≥30 evaluated BUY outcomes, found {len(rows)}")

    n_win = sum(1 for r in rows if r.is_correct)
    n_los = sum(1 for r in rows if not r.is_correct)
    key_wins: dict[str, int] = {}
    key_los: dict[str, int] = {}

    for r in rows:
        reasons = r.reasons or {}
        bucket = key_wins if r.is_correct else key_los
        for k, v in reasons.items():
            if isinstance(v, bool) and v:
                bucket[k] = bucket.get(k, 0) + 1

    all_keys = set(key_wins) | set(key_los)
    layer_stats: dict[str, dict] = {}
    for k in all_keys:
        wc = key_wins.get(k, 0)
        lc = key_los.get(k, 0)
        if wc + lc < min_count:
            continue
        wp = wc / n_win if n_win > 0 else 0.0
        lp = lc / n_los if n_los > 0 else 0.0
        accuracy = wc / (wc + lc) if (wc + lc) > 0 else 0.5
        layer_stats[k] = {
            "win_pct": round(wp * 100, 1),
            "los_pct": round(lp * 100, 1),
            "edge_pct": round((wp - lp) * 100, 1),
            "accuracy": round(accuracy * 100, 1),
            "is_noise": accuracy < 0.52,
            "win_count": wc,
            "los_count": lc,
        }

    # Fit logistic regression for coefficient-based weights
    features = sorted(layer_stats.keys())
    if len(features) >= 3 and len(rows) >= 50:
        X = np.array([[int(bool((r.reasons or {}).get(f))) for f in features] for r in rows])
        y = np.array([int(r.is_correct) for r in rows])
        try:
            lr = LogisticRegression(max_iter=500, C=1.0, random_state=42)
            lr.fit(X, y)
            for feat, coef in zip(features, lr.coef_[0]):
                if feat in layer_stats:
                    layer_stats[feat]["logistic_coef"] = round(float(coef), 4)
        except Exception:
            pass

    payload = {
        "as_of": date.today().isoformat(),
        "lookback_days": lookback_days,
        "total_winners": n_win,
        "total_losers": n_los,
        "layer_count": len(layer_stats),
        "noise_count": sum(1 for s in layer_stats.values() if s["is_noise"]),
        "layers": layer_stats,
        "edge_pct": {k: v["edge_pct"] for k, v in layer_stats.items()},
    }

    try:
        Path(_CONVICTION_WEIGHTS_PATH).parent.mkdir(parents=True, exist_ok=True)
        Path(_CONVICTION_WEIGHTS_PATH).write_text(json.dumps(payload, indent=2))
    except Exception as exc:
        log.warning("conviction_weights.write_failed", error=str(exc))
    # T228: also persist to Redis so weights survive Docker rebuilds (90-day TTL)
    try:
        _get_redis().setex("stockai:conviction_weights", 90 * 86400, json.dumps(payload))
    except Exception:
        pass

    log.info("conviction_weights.calibrated", layers=len(layer_stats), noise=payload["noise_count"])
    return payload


@router.get("/walkforward")
def walkforward_backtest(
    train_days: int = Query(180, ge=30, le=365),
    test_days: int = Query(30, ge=7, le=90),
    lookback_days: int = Query(365, ge=60, le=730),
    hold_days: int = Query(5, ge=1, le=30),
    session: Session = Depends(get_session),
):
    """Walk-forward out-of-sample backtest using persisted signals.

    Divides the lookback period into non-overlapping test windows of test_days each.
    Signals generated during each window are evaluated against prices hold_days
    later — strictly after the signal date, with no look-ahead. Each window
    corresponds to a period where the model was trained on earlier data and tested
    on genuinely unseen future bars.

    Returns per-window accuracy, equity curve, Sharpe, max drawdown, and an optional
    SPY benchmark curve for comparison.
    """
    import bisect
    import math

    import httpx
    import numpy as np

    outcome_cutoff = datetime.now(timezone.utc) - timedelta(days=hold_days + 1)
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    rows = session.execute(
        select(Signal, Stock.symbol, Stock.market)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.ts >= cutoff,
            Signal.ts <= outcome_cutoff,
            Signal.signal == SignalType.BUY,
            Stock.active.is_(True),
        )
        .order_by(Signal.ts.asc())
    ).all()

    if not rows:
        return _wf_empty(train_days, test_days, lookback_days, hold_days)

    stock_ids = list({sig.stock_id for sig, _, _ in rows})
    price_since = (cutoff - timedelta(days=10)).date()

    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= price_since,
        )
        .order_by(Price.stock_id, Price.ts)
    ).all()

    _pts: dict[int, list] = {}
    _pclose: dict[int, list] = {}
    for row in price_rows:
        sid = row.stock_id
        if sid not in _pts:
            _pts[sid] = []
            _pclose[sid] = []
        d = row.ts.date() if isinstance(row.ts, datetime) else row.ts
        _pts[sid].append(d)
        _pclose[sid].append(float(row.close))

    def entry_exit(sid: int, sig_date):
        ts_list = _pts.get(sid, [])
        if not ts_list:
            return None, None
        entry_idx = bisect.bisect_right(ts_list, sig_date)
        if entry_idx >= len(ts_list):
            return None, None
        entry_p = _pclose[sid][entry_idx]
        exit_idx = entry_idx + hold_days
        exit_p = _pclose[sid][exit_idx] if exit_idx < len(ts_list) else _pclose[sid][-1]
        if ts_list[-1] <= sig_date:
            return entry_p, None
        return entry_p, exit_p

    seen: set[tuple] = set()
    evaluated: list[tuple] = []  # (sig_date, return_pct)
    market_counts: dict[str, int] = {}

    for sig, sym, market in rows:
        sig_date = sig.ts.date() if isinstance(sig.ts, datetime) else sig.ts
        key = (sig.stock_id, sig_date, sig.horizon)
        if key in seen:
            continue
        seen.add(key)
        market_counts[market] = market_counts.get(market, 0) + 1

        entry_p, exit_p = entry_exit(sig.stock_id, sig_date)
        if entry_p is None or exit_p is None or entry_p <= 0:
            continue

        ret_pct = (exit_p - entry_p) / entry_p * 100
        evaluated.append((sig_date, ret_pct))

    if not evaluated:
        return _wf_empty(train_days, test_days, lookback_days, hold_days)

    # Divide into non-overlapping test windows
    all_dates = [d for d, _ in evaluated]
    window_start = min(all_dates)
    window_end_limit = max(all_dates)

    windows = []
    while window_start <= window_end_limit:
        wend = window_start + timedelta(days=test_days - 1)
        wsigs = [(d, r) for d, r in evaluated if window_start <= d <= wend]
        if len(wsigs) >= 3:
            n = len(wsigs)
            n_correct = sum(1 for _, r in wsigs if r > 0)
            avg_ret = sum(r for _, r in wsigs) / n
            windows.append({
                "start": window_start.isoformat(),
                "end": wend.isoformat(),
                "n_signals": n,
                "n_correct": n_correct,
                "accuracy": round(n_correct / n * 100, 1),
                "avg_return_pct": round(avg_ret, 2),
            })
        window_start = wend + timedelta(days=1)

    if not windows:
        return _wf_empty(train_days, test_days, lookback_days, hold_days)

    # Equity curve — compound per-window average returns
    equity = 1.0
    for w in windows:
        equity *= (1 + w["avg_return_pct"] / 100)
        w["equity"] = round(equity, 4)

    # Sharpe (annualised from per-window returns)
    rets = np.array([w["avg_return_pct"] for w in windows])
    periods_per_year = 252 / test_days
    sharpe = float(rets.mean() / rets.std() * math.sqrt(periods_per_year)) if rets.std() > 0 else 0.0

    # Max drawdown
    eq_arr = np.array([w["equity"] for w in windows])
    peak = np.maximum.accumulate(eq_arr)
    max_dd = float(abs(((eq_arr - peak) / peak).min())) if len(eq_arr) > 1 else 0.0

    overall_n = sum(w["n_signals"] for w in windows)
    overall_correct = sum(w["n_correct"] for w in windows)
    total_return_pct = round((equity - 1) * 100, 2)
    profitable_windows = sum(1 for w in windows if w["avg_return_pct"] > 0)

    # Benchmark: prefer SPY for US-majority portfolios, else ^HSI
    hk_majority = market_counts.get("HK", 0) > market_counts.get("US", 0)
    bench_sym = "^HSI" if hk_majority else "SPY"
    benchmark = _wf_benchmark(bench_sym, cutoff.date(), windows)

    return {
        "train_days": train_days,
        "test_days": test_days,
        "lookback_days": lookback_days,
        "hold_days": hold_days,
        "windows": windows,
        "total_windows": len(windows),
        "profitable_windows": profitable_windows,
        "signal_count": overall_n,
        "overall_accuracy": round(overall_correct / overall_n * 100, 1) if overall_n else None,
        "avg_return_pct": round(float(rets.mean()), 2),
        "total_return_pct": total_return_pct,
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(max_dd * 100, 2),
        "benchmark": benchmark,
    }


def _wf_empty(train_days, test_days, lookback_days, hold_days):
    return {
        "train_days": train_days, "test_days": test_days,
        "lookback_days": lookback_days, "hold_days": hold_days,
        "windows": [], "total_windows": 0, "profitable_windows": 0,
        "signal_count": 0, "overall_accuracy": None, "avg_return_pct": None,
        "total_return_pct": None, "sharpe": None, "max_drawdown": None,
        "benchmark": None,
    }


def _wf_benchmark(symbol: str, start: date, windows: list[dict]) -> dict | None:
    import httpx
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/prices"
        with httpx.Client(timeout=10) as c:
            r = c.get(url, params={"timeframe": "1d", "start": start.isoformat(), "limit": 1000},
                      headers={"Authorization": f"Bearer {_service_token()}"})
            if r.status_code != 200:
                return None
        data = r.json()
        if not data:
            return None
        prices_by_date = {row["ts"][:10]: float(row["close"]) for row in data}
        sorted_dates = sorted(prices_by_date)

        start_price = None
        for d in sorted_dates:
            if d >= start.isoformat():
                start_price = prices_by_date[d]
                break
        if start_price is None or start_price <= 0:
            return None

        bench_windows = []
        for w in windows:
            wend = w["end"]
            end_price = None
            for d in sorted_dates:
                if d <= wend:
                    end_price = prices_by_date[d]
            if end_price is not None:
                bench_windows.append({
                    "end": wend,
                    "equity": round(end_price / start_price, 4),
                    "cumulative_return_pct": round((end_price / start_price - 1) * 100, 2),
                })

        if not bench_windows:
            return None
        return {
            "symbol": symbol,
            "windows": bench_windows,
            "total_return_pct": bench_windows[-1]["cumulative_return_pct"],
        }
    except Exception:
        return None


@router.get("/recent_changes")
def recent_signal_changes(
    symbols: str = Query(..., description="Comma-separated symbols to check"),
    hours: int = Query(48, ge=1, le=168),
    session: Session = Depends(get_session),
):
    """Return recent signal direction changes for the given symbols.

    For each symbol+horizon pair, compares the two most recent stored signals
    within the time window. Returns entries where the signal flipped, newest first.
    """
    sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()][:50]
    if not sym_list:
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    stocks = session.execute(
        select(Stock.id, Stock.symbol, Stock.name)
        .where(Stock.symbol.in_(sym_list))
    ).all()
    stock_ids = [r.id for r in stocks]
    symbol_map = {r.id: r.symbol for r in stocks}
    name_map = {r.id: r.name for r in stocks}

    if not stock_ids:
        return []

    from sqlalchemy import func as _func
    rn_subq = (
        select(
            Signal.stock_id,
            Signal.horizon,
            Signal.signal,
            Signal.ts,
            Signal.confidence,
            Signal.bullish_probability,
            _func.row_number().over(
                partition_by=[Signal.stock_id, Signal.horizon],
                order_by=Signal.ts.desc(),
            ).label("rn"),
        )
        .where(
            Signal.stock_id.in_(stock_ids),
            Signal.ts >= cutoff,
        )
        .subquery()
    )

    rows = session.execute(
        select(rn_subq).where(rn_subq.c.rn <= 2)
    ).all()

    from collections import defaultdict as _dd
    groups: dict[tuple, list] = _dd(list)
    for r in rows:
        groups[(r.stock_id, r.horizon)].append(r)

    changes = []
    for (sid, horizon), pair in groups.items():
        if len(pair) < 2:
            continue
        pair.sort(key=lambda r: r.ts, reverse=True)
        latest, prev = pair[0], pair[1]
        if latest.signal == prev.signal:
            continue
        changes.append({
            "symbol": symbol_map[sid],
            "name": name_map[sid],
            "horizon": horizon.value if hasattr(horizon, "value") else str(horizon),
            "from_signal": prev.signal.value if hasattr(prev.signal, "value") else str(prev.signal),
            "to_signal": latest.signal.value if hasattr(latest.signal, "value") else str(latest.signal),
            "ts": latest.ts.isoformat(),
            "confidence": round(float(latest.confidence), 1),
            "bullish_probability": round(float(latest.bullish_probability), 3) if latest.bullish_probability is not None else None,
            "prev_ts": prev.ts.isoformat(),
        })

    changes.sort(key=lambda c: c["ts"], reverse=True)
    return changes


@router.get("/{symbol}/history")
def signal_history(
    symbol: str,
    style: str = Query("SWING", description="Trading style: SHORT, SWING, LONG, GROWTH"),
    days: int = Query(60, ge=7, le=365),
    session: Session = Depends(get_session),
):
    """Historical signal confidence trend for a single symbol.

    Returns up to `days` days of stored signals ordered oldest → newest.
    Used by the stock detail page sparkline.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        horizon = SignalHorizon(style.upper())
    except ValueError:
        horizon = SignalHorizon.SWING

    stock = session.execute(
        select(Stock).where(Stock.symbol == symbol.upper())
    ).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Symbol {symbol} not found")

    rows = session.execute(
        select(Signal.ts, Signal.signal, Signal.confidence, Signal.bullish_probability)
        .where(
            Signal.stock_id == stock.id,
            Signal.horizon == horizon,
            Signal.ts >= cutoff,
        )
        .order_by(Signal.ts.asc())
    ).all()

    return [
        {
            "ts": r.ts.isoformat() if r.ts else None,
            "signal": r.signal.value,
            "confidence": round(r.confidence, 1),
            "bullish_probability": round(r.bullish_probability, 4) if r.bullish_probability else None,
        }
        for r in rows
    ]


@router.get("/{symbol}/patterns")
def detect_patterns(
    symbol: str,
    session: Session = Depends(get_session),
):
    """Detect active technical chart patterns for a symbol.

    Returns patterns detected within the last 3-5 bars so the UI shows
    live "about to move" badges. Patterns checked: golden_cross,
    macd_bullish_cross, rsi_oversold_bounce, double_bottom, breakout.
    """
    import pandas as pd

    stock = session.execute(
        select(Stock).where(Stock.symbol == symbol.upper())
    ).scalar_one_or_none()
    if not stock:
        raise HTTPException(404, f"Stock {symbol} not found")

    rows = session.execute(
        select(Price.ts, Price.close, Price.high, Price.low, Price.volume)
        .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.asc())
        .limit(260)
    ).all()

    if len(rows) < 30:
        return {"symbol": symbol.upper(), "patterns": [], "as_of": datetime.now(timezone.utc).isoformat() + "Z"}

    close = pd.Series([float(r.close) for r in rows])
    volume = pd.Series([float(r.volume) for r in rows])

    patterns: list[dict] = []

    def _add(name: str, label: str, description: str, bullish: bool) -> None:
        patterns.append({"name": name, "label": label, "description": description, "bullish": bullish})

    # 1. Golden Cross / Death Cross — EMA50 vs EMA200
    # Guards: (a) verify EMA50 is STILL above EMA200 before showing golden cross badge —
    # otherwise a cross that fired 4 days ago but has already reversed keeps showing as bullish.
    # (b) Add spread-velocity context: "spread narrowing" is an early reversal warning.
    if len(close) >= 200:
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()
        currently_golden = bool(ema50.iloc[-1] > ema200.iloc[-1])

        gc_fired = dc_fired = False
        for i in range(max(-5, -len(close) + 1), 0):
            if ema50.iloc[i - 1] < ema200.iloc[i - 1] and ema50.iloc[i] >= ema200.iloc[i]:
                gc_fired = True
                break
            if ema50.iloc[i - 1] > ema200.iloc[i - 1] and ema50.iloc[i] <= ema200.iloc[i]:
                dc_fired = True
                break

        spread_now = float(ema50.iloc[-1] - ema200.iloc[-1])
        spread_5d  = float(ema50.iloc[-6] - ema200.iloc[-6]) if len(close) >= 7 else spread_now
        gc_expanding = spread_now > spread_5d

        if gc_fired and currently_golden:
            suffix = " • spread expanding" if gc_expanding else " • spread narrowing ⚠"
            _add("golden_cross", f"Golden Cross{suffix}",
                 f"EMA50 crossed above EMA200 ({ema200.iloc[-1]:.2f})", True)
        elif dc_fired and not currently_golden:
            _add("death_cross", "Death Cross",
                 f"EMA50 crossed below EMA200 ({ema200.iloc[-1]:.2f})", False)
        elif currently_golden and not gc_expanding:
            # In golden territory but spread narrowing — early warning before death cross
            _add("gc_narrowing", "GC Spread Narrowing ⚠",
                 f"EMA50 above EMA200 but gap shrinking — momentum fading ({ema200.iloc[-1]:.2f})", False)

    # 2. MACD Cross — verify MACD is still above signal before showing bullish badge
    # Also detect histogram fading: positive MACD but slope declining (momentum exhaustion).
    if len(close) >= 35:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line = ema12 - ema26
        sig_line = macd_line.ewm(span=9, adjust=False).mean()
        hist = macd_line - sig_line
        currently_bull_macd = bool(macd_line.iloc[-1] > sig_line.iloc[-1])
        hist_slope = float(hist.iloc[-1] - hist.iloc[-3]) if len(hist.dropna()) >= 4 else 0.0
        hist_fading = bool(hist.iloc[-1] > 0 and hist_slope < 0)

        bull_cross = bear_cross = False
        for i in range(max(-3, -len(close) + 1), 0):
            if macd_line.iloc[i - 1] < sig_line.iloc[i - 1] and macd_line.iloc[i] >= sig_line.iloc[i]:
                bull_cross = True
                break
            if macd_line.iloc[i - 1] > sig_line.iloc[i - 1] and macd_line.iloc[i] <= sig_line.iloc[i]:
                bear_cross = True
                break

        if bull_cross and currently_bull_macd:
            if hist_fading:
                _add("macd_bullish_cross", "MACD Cross ↑ • hist fading ⚠",
                     f"MACD crossed signal but momentum slowing (slope {hist_slope:.4f})", True)
            else:
                _add("macd_bullish_cross", "MACD Cross ↑",
                     f"MACD crossed above signal ({sig_line.iloc[-1]:.3f})", True)
        elif bear_cross and not currently_bull_macd:
            _add("macd_bear_cross", "MACD Cross ↓",
                 f"MACD crossed below signal ({sig_line.iloc[-1]:.3f})", False)
        elif currently_bull_macd and hist_fading:
            # No recent cross but histogram positive and fading — surfaces the exhaustion signal
            _add("macd_fading", "MACD Hist Fading ⚠",
                 f"MACD positive but momentum slowing (3-bar slope {hist_slope:.4f})", False)

    # 3. RSI Oversold Bounce — RSI crossed above 30 within last 3 bars
    if len(close) >= 16:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        rsi = 100 - (100 / (1 + rs))
        for i in range(max(-3, -len(close) + 1), 0):
            prev_v = rsi.iloc[i - 1]
            curr_v = rsi.iloc[i]
            if pd.notna(prev_v) and pd.notna(curr_v) and prev_v < 30 and curr_v >= 30:
                _add("rsi_oversold_bounce", "RSI Bounce", f"RSI recovered from oversold (now {rsi.iloc[-1]:.1f})", True)
                break

    # 4. Double Bottom — two troughs within 3% in last 60 bars, separated by 5%+ peak
    if len(close) >= 20:
        window = close.tail(60).values
        minima: list[tuple[int, float]] = []
        for i in range(2, len(window) - 2):
            if all(window[i] <= window[j] for j in range(i - 2, i + 3) if j != i):
                minima.append((i, float(window[i])))
        if len(minima) >= 2:
            b1_idx, b1_val = minima[-2]
            b2_idx, b2_val = minima[-1]
            lower = min(b1_val, b2_val)
            if lower > 0 and abs(b1_val - b2_val) / lower <= 0.03 and b2_idx > b1_idx + 3:
                peak = float(max(window[b1_idx:b2_idx + 1]))
                if peak >= lower * 1.05 and float(close.iloc[-1]) > lower * 1.01:
                    _add("double_bottom", "Double Bottom", f"W-pattern: two troughs near ${lower:.2f}", True)

    # 5. Volume Breakout — close above 20-day high with elevated volume (1.4x avg)
    if len(close) >= 21:
        high_20 = float(close.iloc[-21:-1].max())
        avg_vol = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else 0.0
        if float(close.iloc[-1]) > high_20 and avg_vol > 0 and float(volume.iloc[-1]) >= avg_vol * 1.4:
            _add("breakout", "Volume Breakout", f"Closed above 20-day high (${high_20:.2f}) on elevated volume", True)

    return {
        "symbol": symbol.upper(),
        "patterns": patterns,
        "as_of": datetime.now(timezone.utc).isoformat() + "Z",
    }


@router.get("/outcomes/summary")
def outcomes_summary(
    horizon: str | None = Query(None, description="SHORT | SWING | LONG"),
    days: int = Query(90, description="Look-back window in calendar days"),
    market: str | None = Query(None, description="US | HK — filter by stock market"),
    symbol: str | None = Query(None, description="Filter to a single symbol (e.g. AAPL)"),
    session: Session = Depends(get_session),
):
    """Return win-rate and return stats from the signal_outcomes table.

    Groups results by confidence band (0-40, 40-55, 55-70, 70-85, 85+) so you
    can verify that higher-confidence signals actually win more often.
    """
    import statistics

    cutoff = date.today() - timedelta(days=days)

    q = select(SignalOutcome).where(
        SignalOutcome.signal_date >= cutoff,
        SignalOutcome.is_correct.is_not(None),
    )
    if horizon:
        try:
            q = q.where(SignalOutcome.horizon == SignalHorizon(horizon.upper()))
        except ValueError:
            raise HTTPException(400, f"Unknown horizon: {horizon}")
    _needs_stock_join = market or symbol
    if _needs_stock_join:
        q = q.join(Stock, Stock.id == SignalOutcome.stock_id)
        if market:
            q = q.where(Stock.market == market.upper())
        if symbol:
            q = q.where(Stock.symbol == symbol.upper())

    outcomes = session.execute(q).scalars().all()

    # T232-OC6: count censored outcomes (hold window closed, price permanently missing —
    # delisting/halt) in the same window/filters, so win rates can be reported alongside
    # the fraction of outcomes that were excluded rather than silently vanishing.
    censored_q = select(func.count()).select_from(SignalOutcome).where(
        SignalOutcome.signal_date >= cutoff,
        SignalOutcome.skip_reason.is_not(None),
    )
    if horizon:
        censored_q = censored_q.where(SignalOutcome.horizon == SignalHorizon(horizon.upper()))
    if _needs_stock_join:
        censored_q = censored_q.join(Stock, Stock.id == SignalOutcome.stock_id)
        if market:
            censored_q = censored_q.where(Stock.market == market.upper())
        if symbol:
            censored_q = censored_q.where(Stock.symbol == symbol.upper())
    censored_count = session.execute(censored_q).scalar_one()

    if not outcomes:
        return {"total": 0, "censored": censored_count, "message": "No evaluated outcomes yet in this window"}

    # Overall stats
    wins = [o for o in outcomes if o.is_correct]
    returns = [o.pct_return for o in outcomes if o.pct_return is not None]

    # By confidence band
    bands = [
        (0, 40, "0-40"),
        (40, 55, "40-55"),
        (55, 70, "55-70"),
        (70, 85, "70-85"),
        (85, 101, "85+"),
    ]
    band_stats = []
    for lo, hi, label in bands:
        bucket = [o for o in outcomes if lo <= o.confidence < hi]
        if not bucket:
            continue
        bucket_wins = sum(1 for o in bucket if o.is_correct)
        bucket_returns = [o.pct_return for o in bucket if o.pct_return is not None]
        band_stats.append({
            "band": label,
            "count": len(bucket),
            "win_rate": round(bucket_wins / len(bucket), 3),
            "avg_return_pct": round(statistics.mean(bucket_returns) * 100, 2) if bucket_returns else None,
        })

    # By horizon (if not filtered)
    horizon_stats = {}
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        hbucket = [o for o in outcomes if o.horizon.value == h]
        if not hbucket:
            continue
        hreturns = [o.pct_return for o in hbucket if o.pct_return is not None]
        horizon_stats[h] = {
            "count": len(hbucket),
            "win_rate": round(sum(1 for o in hbucket if o.is_correct) / len(hbucket), 3),
            "avg_return_pct": round(statistics.mean(hreturns) * 100, 2) if hreturns else None,
        }

    # By market regime
    regime_stats = {}
    for o in outcomes:
        reg = o.market_regime or "unknown"
        if reg not in regime_stats:
            regime_stats[reg] = {"count": 0, "wins": 0, "returns": []}
        regime_stats[reg]["count"] += 1
        if o.is_correct:
            regime_stats[reg]["wins"] += 1
        if o.pct_return is not None:
            regime_stats[reg]["returns"].append(o.pct_return)
    regime_summary = {
        reg: {
            "count": v["count"],
            "win_rate": round(v["wins"] / v["count"], 3),
            "avg_return_pct": round(statistics.mean(v["returns"]) * 100, 2) if v["returns"] else None,
        }
        for reg, v in regime_stats.items()
    }

    # INT-8: Research alignment breakdown — how does research agreement affect win rate?
    _ALIGNED_RECS   = {"BUY", "STRONG BUY", "STRONG_BUY"}
    _PARTIAL_RECS   = {"WATCH"}
    _DIVERGENT_RECS = {"AVOID", "SELL"}
    research_groups: dict[str, dict] = {
        "aligned": {"count": 0, "wins": 0, "returns": []},
        "partial":  {"count": 0, "wins": 0, "returns": []},
        "divergent": {"count": 0, "wins": 0, "returns": []},
        "no_research": {"count": 0, "wins": 0, "returns": []},
    }
    for o in outcomes:
        rec = (o.research_rec or "").upper().strip()
        if rec in _ALIGNED_RECS:
            grp = "aligned"
        elif rec in _PARTIAL_RECS:
            grp = "partial"
        elif rec in _DIVERGENT_RECS:
            grp = "divergent"
        else:
            grp = "no_research"
        research_groups[grp]["count"] += 1
        if o.is_correct:
            research_groups[grp]["wins"] += 1
        if o.pct_return is not None:
            research_groups[grp]["returns"].append(o.pct_return)

    research_summary = {
        grp: {
            "count": v["count"],
            "win_rate": round(v["wins"] / v["count"], 3) if v["count"] else None,
            "avg_return_pct": round(statistics.mean(v["returns"]) * 100, 2) if v["returns"] else None,
        }
        for grp, v in research_groups.items()
        if v["count"] > 0
    }

    # Multi-window win rates (INT-8)
    def _window_stats(outcomes, attr_correct, attr_return):
        vals = [(getattr(o, attr_correct), getattr(o, attr_return)) for o in outcomes
                if getattr(o, attr_correct) is not None]
        if not vals:
            return None
        wr = sum(1 for c, _ in vals if c) / len(vals)
        rets = [r for _, r in vals if r is not None]
        return {
            "count": len(vals),
            "win_rate": round(wr, 3),
            "avg_return_pct": round(statistics.mean(rets) * 100, 2) if rets else None,
        }

    multi_window = {
        "5d":  _window_stats(outcomes, "is_correct_5d",  "return_5d"),
        "10d": _window_stats(outcomes, "is_correct_10d", "return_10d"),
        "20d": _window_stats(outcomes, "is_correct_20d", "return_20d"),
    }

    # BUY vs SELL win rate by horizon — reveals directional bias in signal accuracy
    direction_stats: dict = {}
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        for direction in ("BUY", "SELL"):
            bucket = [o for o in outcomes if o.horizon.value == h and o.signal_direction == direction]
            if not bucket:
                continue
            bucket_returns = [o.pct_return for o in bucket if o.pct_return is not None]
            direction_stats[f"{h}/{direction}"] = {
                "count": len(bucket),
                "win_rate": round(sum(1 for o in bucket if o.is_correct) / len(bucket), 3),
                "avg_return_pct": round(statistics.mean(bucket_returns) * 100, 2) if bucket_returns else None,
            }

    # By market (US vs HK) — T223-SIGNAL-WINRATE-API: surfaces cross-market win rate difference
    market_ids = list({o.stock_id for o in outcomes})
    _market_map: dict[int, str] = {}
    if market_ids:
        _mkt_rows = session.execute(
            select(Stock.id, Stock.market).where(Stock.id.in_(market_ids))
        ).all()
        _market_map = {r.id: r.market for r in _mkt_rows}

    market_stats: dict[str, dict] = {}
    for o in outcomes:
        mkt = _market_map.get(o.stock_id, "US")
        if mkt not in market_stats:
            market_stats[mkt] = {"count": 0, "wins": 0, "returns": []}
        market_stats[mkt]["count"] += 1
        if o.is_correct:
            market_stats[mkt]["wins"] += 1
        if o.pct_return is not None:
            market_stats[mkt]["returns"].append(o.pct_return)
    by_market = {
        mkt: {
            "count": v["count"],
            "win_rate": round(v["wins"] / v["count"], 3),
            "avg_return_pct": round(statistics.mean(v["returns"]) * 100, 2) if v["returns"] else None,
        }
        for mkt, v in market_stats.items()
    }

    signal_dates = [o.signal_date for o in outcomes if o.signal_date is not None]
    date_range = {
        "oldest": min(signal_dates).isoformat() if signal_dates else None,
        "newest": max(signal_dates).isoformat() if signal_dates else None,
    }

    # Per-symbol breakdown — fetch symbol names in one query
    stock_ids = list({o.stock_id for o in outcomes})
    symbol_map: dict[int, str] = {}
    if stock_ids:
        rows = session.execute(select(Stock.id, Stock.symbol).where(Stock.id.in_(stock_ids))).all()
        symbol_map = {r.id: r.symbol for r in rows}

    sym_groups: dict[str, dict] = {}
    for o in outcomes:
        sym = symbol_map.get(o.stock_id, f"id:{o.stock_id}")
        if sym not in sym_groups:
            sym_groups[sym] = {"count": 0, "wins": 0, "returns": []}
        sym_groups[sym]["count"] += 1
        if o.is_correct:
            sym_groups[sym]["wins"] += 1
        if o.pct_return is not None:
            sym_groups[sym]["returns"].append(o.pct_return)

    by_symbol = sorted(
        [
            {
                "symbol": sym,
                "count": v["count"],
                "win_rate": round(v["wins"] / v["count"], 3),
                "avg_return_pct": round(statistics.mean(v["returns"]) * 100, 2) if v["returns"] else None,
                "wins": v["wins"],
                "losses": v["count"] - v["wins"],
            }
            for sym, v in sym_groups.items()
            if v["count"] >= 2
        ],
        key=lambda x: -(x["avg_return_pct"] or -999),
    )

    return {
        "total": len(outcomes),
        "censored": censored_count,
        "days_lookback": days,
        "date_range": date_range,
        "overall": {
            "win_rate": round(len(wins) / len(outcomes), 3),
            "avg_return_pct": round(statistics.mean(returns) * 100, 2) if returns else None,
            "median_return_pct": round(statistics.median(returns) * 100, 2) if returns else None,
        },
        "by_confidence_band": band_stats,
        "by_horizon": horizon_stats,
        "by_market": by_market,
        "by_direction": direction_stats,
        "by_market_regime": regime_summary,
        "by_research_alignment": research_summary,
        "by_window": multi_window,
        "by_symbol": by_symbol,
    }


@router.get("/outcomes/calibration")
def outcomes_calibration(
    days: int = Query(180, ge=30, le=365, description="Look-back window in calendar days"),
    session: Session = Depends(get_session),
):
    """Calibration curve data for the reliability diagram.

    For each horizon × confidence band combination, returns the actual win rate
    vs expected (midpoint of the band). Used to assess whether confidence scores
    are well-calibrated and to recommend minimum confidence thresholds.
    """
    import statistics
    cutoff = date.today() - timedelta(days=days)

    outcomes = session.execute(
        select(SignalOutcome)
        .where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    if not outcomes:
        return {"total": 0, "horizons": [], "overall": {}, "message": "No evaluated BUY outcomes yet"}

    bands = [
        (50, 60, "50-60", 55.0),
        (60, 65, "60-65", 62.5),
        (65, 70, "65-70", 67.5),
        (70, 75, "70-75", 72.5),
        (75, 80, "75-80", 77.5),
        (80, 101, "80+", 85.0),
    ]

    horizons = ["SHORT", "SWING", "LONG", "GROWTH"]
    horizon_stats = []

    for hor in horizons:
        hor_outcomes = [o for o in outcomes if o.horizon == hor or o.horizon == SignalHorizon(hor)]
        if not hor_outcomes:
            continue

        band_data = []
        for lo, hi, label, midpoint in bands:
            bucket = [o for o in hor_outcomes if lo <= o.confidence < hi]
            if len(bucket) < 3:
                continue
            wins = sum(1 for o in bucket if o.is_correct)
            rets = [o.pct_return for o in bucket if o.pct_return is not None]
            band_data.append({
                "band": label,
                "midpoint": midpoint,
                "count": len(bucket),
                "win_rate": round(wins / len(bucket), 3),
                "win_rate_pct": round(wins / len(bucket) * 100, 1),
                "avg_return_pct": round(statistics.mean(rets) * 100, 2) if rets else None,
                "calibration_gap": round((wins / len(bucket)) - (midpoint / 100), 3),
            })

        if not band_data:
            continue

        # Suggest min_confidence: lowest band with win_rate >= 0.52
        suggested_min = None
        for bd in sorted(band_data, key=lambda x: x["midpoint"]):
            if bd["win_rate"] >= 0.52 and bd["count"] >= 5:
                suggested_min = bd["midpoint"] - 5  # use band start
                break

        hor_wins = sum(1 for o in hor_outcomes if o.is_correct)
        hor_rets = [o.pct_return for o in hor_outcomes if o.pct_return is not None]
        horizon_stats.append({
            "horizon": hor,
            "total": len(hor_outcomes),
            "win_rate_pct": round(hor_wins / len(hor_outcomes) * 100, 1),
            "avg_return_pct": round(statistics.mean(hor_rets) * 100, 2) if hor_rets else None,
            "suggested_min_confidence": suggested_min,
            "bands": band_data,
        })

    # Overall
    all_wins = sum(1 for o in outcomes if o.is_correct)
    all_rets = [o.pct_return for o in outcomes if o.pct_return is not None]

    return {
        "total": len(outcomes),
        "days": days,
        "overall": {
            "win_rate_pct": round(all_wins / len(outcomes) * 100, 1),
            "avg_return_pct": round(statistics.mean(all_rets) * 100, 2) if all_rets else None,
        },
        "horizons": horizon_stats,
    }


@router.get("/outcomes/calibrate")
def outcomes_calibrate(
    days: int = Query(180, description="Look-back window in calendar days"),
    min_samples: int = Query(15, description="Minimum signals required to suggest a threshold"),
    session: Session = Depends(get_session),
):
    """Sweep confidence thresholds per horizon to find the empirically optimal buy_threshold.

    For each horizon × BUY, finds the confidence level (0-100 scale) that maximises
    expected_value = win_rate × avg_return, subject to n >= min_samples.
    Compares the suggested threshold against the current hardcoded thresholds in
    _STYLE_PROFILES so you can see whether signal tuning is needed.
    """
    import statistics as _stats
    from ..generators.signals import _STYLE_PROFILES

    # Current bull-regime thresholds, fused-probability scale (T232-CAL1: sweep/report on the
    # same scale _decide_style actually compares against — previously this used a 0-100
    # confidence scale here while POST /apply wrote a misinterpreted 0-100 value too).
    CURRENT: dict[str, float] = {
        h: _STYLE_PROFILES[h]["buy_threshold"]["bull"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }

    cutoff = date.today() - timedelta(days=days)
    all_outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    # T232-OC3: walk-forward split — mirrors the fix in POST /outcomes/calibrate/apply so this
    # preview endpoint reports the SAME methodology that actually gets applied, instead of a
    # more optimistic in-sample number that would disagree with what apply's response shows.
    calibrations = []
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        bucket = sorted(
            [o for o in all_outcomes if o.horizon.value == h],
            key=lambda o: o.signal_date,
        )
        current_t = CURRENT.get(h, 0.65)

        def _stats_at(threshold: float, samples: list) -> dict | None:
            sub = [o for o in samples if o.fused_prob is not None and o.fused_prob >= threshold]
            if len(sub) < min_samples:
                return None
            wins = sum(1 for o in sub if o.is_correct)
            rets = [o.pct_return for o in sub if o.pct_return is not None]
            acc = wins / len(sub)
            avg_ret = _stats.mean(rets) if rets else 0.0
            # T232-OC4: avg_ret is already the mean return across ALL trades (wins and
            # losses) in `sub` — it already IS the expected value per trade. Multiplying by
            # acc (win rate) again double-counts win probability, understating true EV.
            return {
                "n": len(sub),
                "win_rate": round(acc, 3),
                "avg_return_pct": round(avg_ret * 100, 2),
                "expected_value_pct": round(avg_ret * 100, 2),
            }

        if len(bucket) < min_samples * 2:
            calibrations.append({
                "horizon": h,
                "current_threshold": current_t,
                "suggested_threshold": None,
                "n_total": len(bucket),
                "note": f"Insufficient data (need ≥{min_samples * 2} evaluated BUY outcomes for a valid train/validation split)",
            })
            continue

        split = max(1, int(len(bucket) * 0.7))
        train_bucket = bucket[:split]
        val_bucket = bucket[split:]

        # Search on the train slice only.
        best_ev = -999.0
        best_t: float | None = None
        for t_i in range(55, 86):
            st = _stats_at(t_i / 100.0, train_bucket)
            if st is not None and st["expected_value_pct"] > best_ev:
                best_ev = st["expected_value_pct"]
                best_t = t_i / 100.0

        # Report stats on the validation slice — data the search never saw.
        best_stats = _stats_at(best_t, val_bucket) if best_t is not None else None
        at_current = _stats_at(current_t, val_bucket)
        ev_lift = None
        if best_stats and at_current:
            ev_lift = round(best_stats["expected_value_pct"] - at_current["expected_value_pct"], 2)

        calibrations.append({
            "horizon": h,
            "current_threshold": current_t,
            "suggested_threshold": round(best_t, 2) if best_t else None,
            "ev_lift_pct": ev_lift,
            "n_total": len(bucket),
            "train_n": len(train_bucket),
            "validation_n": len(val_bucket),
            "at_current_threshold": at_current,
            "at_suggested_threshold": best_stats,
        })

    return {
        "days": days,
        "min_samples": min_samples,
        "calibrations": calibrations,
    }


@router.post("/outcomes/calibrate/apply")
def outcomes_calibrate_apply(
    days: int = Query(180, description="Look-back window in calendar days"),
    min_samples: int = Query(50, description="Minimum signals required to apply a new threshold"),
    min_ev_lift: float = Query(0.1, description="Minimum expected-value lift (%) before applying"),
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Apply empirically-optimal buy/sell thresholds to Redis so signal generator picks them up live.

    T232-CAL1/CAL3 fix: sweeps and writes directly on the fused-probability (0-1) scale that
    _decide_style actually compares against — previously this swept SignalOutcome.confidence
    (a 0-100 distance-from-neutral scale) and wrote best_t/100, which was silently misapplied
    as a fused-probability threshold (confidence 62 ≡ fused 0.81, was written+read as 0.62).

    Reads the same calibration data as GET /outcomes/calibrate and, for each horizon
    where the suggested threshold has a positive EV lift and sufficient sample size,
    writes `stockai:signal_thresholds:{HORIZON}` to Redis with a 30-day TTL. The value
    written is a delta from the hardcoded bull baseline, applied per-regime by
    _get_dynamic_buy_threshold (T232-CAL2) rather than overriding all regimes with one flat
    number, and is bounds-checked before being written (defense in depth alongside the
    reader-side clamp).

    The signal generator reads these keys at signal decision time (falls back to the
    hardcoded _STYLE_PROFILES values if absent).  Run this weekly via the scheduler.
    """
    import statistics as _stats
    from ..generators.signals import _STYLE_PROFILES

    # Bull-regime buy thresholds — source of truth is _STYLE_PROFILES (T232-SIG12: no more
    # independently-drifting hardcoded copies).
    CURRENT: dict[str, float] = {
        h: _STYLE_PROFILES[h]["buy_threshold"]["bull"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }
    _BUY_BOUNDS = (0.55, 0.85)
    _SELL_BOUNDS = (0.15, 0.45)

    cutoff = date.today() - timedelta(days=days)
    all_outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    applied: list[dict] = []
    skipped: list[dict] = []
    redis_client = _get_redis()
    _REDIS_TTL = 30 * 86400  # 30 days

    # T232-OC3 / T233-SELFIMPROVE-PHASE1: the threshold search used to sweep 31 overlapping
    # cumulative subsets of ONE sample and take the argmax — an in-sample search evaluated on
    # the exact data it was fit to. At min_samples=50 the win-rate standard error is still ~7pp,
    # so an unvalidated argmax over 31 correlated subsets is prone to surfacing an upward-biased
    # fluke as "optimal" (the same failure mode that produced the CAL-1 incident documented
    # elsewhere in this report). Fixed with a genuine walk-forward split: search for the best
    # threshold on the OLDER 70% of the window (train), then only apply it if the EV lift ALSO
    # holds up on the NEWER, never-searched 30% (validation) — mirrors the chronological
    # train/validation split calibrate_ml_weight() already uses correctly for the sibling
    # ML-weight calibration.
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        bucket = sorted(
            [o for o in all_outcomes if o.horizon.value == h],
            key=lambda o: o.signal_date,
        )
        current_t = CURRENT.get(h, 0.65)  # fused-probability scale

        if len(bucket) < min_samples * 2:
            # Need enough for BOTH a train slice and a validation slice to each independently
            # clear min_samples — otherwise the split itself produces two under-powered halves.
            skipped.append({"horizon": h, "reason": f"only {len(bucket)} samples (need {min_samples * 2} for a valid train/validation split)"})
            continue

        split = max(1, int(len(bucket) * 0.7))
        train_bucket = bucket[:split]
        val_bucket = bucket[split:]

        def _stats_at(threshold: float, samples: list) -> dict | None:
            sub = [o for o in samples if o.fused_prob is not None and o.fused_prob >= threshold]
            if len(sub) < min_samples:
                return None
            wins = sum(1 for o in sub if o.is_correct)
            rets = [o.pct_return for o in sub if o.pct_return is not None]
            acc = wins / len(sub)
            avg_ret = _stats.mean(rets) if rets else 0.0
            # T232-OC4: avg_ret already IS the expected value (mean return across all trades
            # in `sub`, wins and losses) — multiplying by acc double-counts win probability.
            ev = avg_ret * 100
            return {"n": len(sub), "win_rate": round(acc, 3), "ev_pct": round(ev, 2)}

        # Search for the best threshold on the TRAIN slice only.
        best_ev = -999.0
        best_t: float | None = None
        for t_i in range(55, 86):
            t = t_i / 100.0
            st = _stats_at(t, train_bucket)
            if st is not None and st["ev_pct"] > best_ev:
                best_ev = st["ev_pct"]
                best_t = t

        if best_t is None:
            skipped.append({"horizon": h, "reason": "no threshold met EV/sample criteria on the train slice"})
            continue

        # Validate: both the suggested threshold and the current baseline must be independently
        # measurable on the VALIDATION slice — a candidate that never sees this data.
        best_stats = _stats_at(best_t, val_bucket)
        current_stats = _stats_at(current_t, val_bucket)

        if best_stats is None:
            skipped.append({"horizon": h, "reason": "suggested threshold unmeasurable on the validation slice (insufficient samples)"})
            continue

        if current_stats is None:
            # T232-OC3: no honest baseline measurable at the current threshold — do not assume
            # EV 0 (that overstates lift and applies too eagerly). Skip instead.
            skipped.append({"horizon": h, "reason": "baseline threshold unmeasurable on the validation slice (insufficient samples)"})
            continue

        ev_lift = round(best_stats["ev_pct"] - current_stats["ev_pct"], 2)

        # T232-OC3-FOLLOWUP: never apply a threshold with negative validated EV lift, regardless
        # of how large the threshold shift is — see the SELL-side comment above for the live
        # incident (a large shift previously bypassed the lift check entirely via the old
        # `ev_lift < min AND shift < 3pt` AND-logic).
        if ev_lift < 0:
            skipped.append({
                "horizon": h,
                "reason": f"validation-slice EV lift {ev_lift}% is negative — never apply a worse threshold",
                "suggested": best_t,
                "current": current_t,
            })
            continue

        if ev_lift < min_ev_lift and abs(best_t - current_t) < 0.03:
            skipped.append({
                "horizon": h,
                "reason": f"validation-slice EV lift {ev_lift}% below min {min_ev_lift}% and threshold shift <3pt",
                "suggested": best_t,
                "current": current_t,
            })
            continue

        if not (_BUY_BOUNDS[0] <= best_t <= _BUY_BOUNDS[1]):
            skipped.append({"horizon": h, "reason": f"suggested {best_t} outside sane bounds {_BUY_BOUNDS}"})
            continue

        # Write to Redis — signal generator reads this at decision time (fused-probability scale)
        redis_key = f"stockai:signal_thresholds:{h}"
        redis_client.setex(redis_key, _REDIS_TTL, str(round(best_t, 4)))
        applied.append({
            "horizon": h,
            "previous_threshold": current_t,
            "new_threshold": round(best_t, 4),
            "ev_lift_pct": ev_lift,
            "train_n": len(train_bucket),
            "validation_stats": best_stats,
        })

    # T228-SELL-CALIBRATION (T232-CAL3 fix): sweep SELL threshold per horizon.
    # For SELL, LOWER fused_prob = stronger conviction (confidence = (0.5-fused)*200), so the
    # sweep selects fused_prob <= t — the mirror image of the BUY sweep above — and uses signed
    # SELL profit (a SELL is profitable when price falls, i.e. -pct_return), not abs().
    sell_outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "SELL",
        )
    ).scalars().all()

    sell_applied: list[dict] = []
    sell_skipped: list[dict] = []
    _CURRENT_SELL = 0.35  # fused-probability scale, matches the hardcoded fallback in signals.py

    # T232-OC3: same walk-forward fix as the BUY sweep above — train on the older 70%,
    # validate the chosen threshold's EV lift on the newer, never-searched 30%.
    for h in ("SHORT", "SWING", "LONG", "GROWTH"):
        s_bucket = sorted(
            [o for o in sell_outcomes if o.horizon.value == h],
            key=lambda o: o.signal_date,
        )

        if len(s_bucket) < min_samples * 2:
            sell_skipped.append({"horizon": h, "reason": f"only {len(s_bucket)} SELL samples (need {min_samples * 2} for a valid train/validation split)"})
            continue

        s_split = max(1, int(len(s_bucket) * 0.7))
        s_train_bucket = s_bucket[:s_split]
        s_val_bucket = s_bucket[s_split:]

        def _sell_stats_at(threshold: float, samples: list) -> dict | None:
            sub = [o for o in samples if o.fused_prob is not None and o.fused_prob <= threshold]
            if len(sub) < min_samples:
                return None
            wins = sum(1 for o in sub if o.is_correct)
            rets = [-o.pct_return for o in sub if o.pct_return is not None]  # signed: SELL wins on price decline
            acc = wins / len(sub)
            avg_ret = _stats.mean(rets) if rets else 0.0
            # T232-OC4: avg_ret already IS the expected value — see fix note above.
            ev = avg_ret * 100
            return {"n": len(sub), "win_rate": round(acc, 3), "ev_pct": round(ev, 2)}

        s_best_ev = -999.0
        s_best_t: float | None = None
        for t_i in range(15, 41):
            t = t_i / 100.0
            st = _sell_stats_at(t, s_train_bucket)
            if st is not None and st["ev_pct"] > s_best_ev:
                s_best_ev = st["ev_pct"]
                s_best_t = t

        if s_best_t is None:
            sell_skipped.append({"horizon": h, "reason": "no SELL threshold met criteria on the train slice"})
            continue

        s_best_stats = _sell_stats_at(s_best_t, s_val_bucket)
        s_current_stats = _sell_stats_at(_CURRENT_SELL, s_val_bucket)

        if s_best_stats is None:
            sell_skipped.append({"horizon": h, "reason": "suggested SELL threshold unmeasurable on the validation slice"})
            continue

        if s_current_stats is None:
            sell_skipped.append({"horizon": h, "reason": "SELL baseline threshold unmeasurable on the validation slice"})
            continue

        s_ev_lift = round(s_best_stats["ev_pct"] - s_current_stats["ev_pct"], 2)

        # T232-OC3-FOLLOWUP: this used to be `ev_lift < min_ev_lift AND shift < 3pt` — an AND
        # meant a large threshold shift could bypass the EV check entirely even with NEGATIVE
        # validated lift (caught live: a run applied SELL:GROWTH 0.35->0.30 with a validated
        # ev_lift of -0.01%, because the 5pt shift satisfied "not small" while the lift check
        # was skipped). Never apply a threshold with negative validated EV lift regardless of
        # shift size; the small-shift-plus-small-lift skip is now a separate, narrower check.
        if s_ev_lift < 0:
            sell_skipped.append({
                "horizon": h, "direction": "SELL",
                "reason": f"validation-slice EV lift {s_ev_lift}% is negative — never apply a worse threshold",
                "suggested": s_best_t,
            })
            continue
        if s_ev_lift < min_ev_lift and abs(s_best_t - _CURRENT_SELL) < 0.03:
            sell_skipped.append({
                "horizon": h, "direction": "SELL",
                "reason": f"validation-slice EV lift {s_ev_lift}% below min and shift <3pt",
                "suggested": s_best_t,
            })
            continue

        if not (_SELL_BOUNDS[0] <= s_best_t <= _SELL_BOUNDS[1]):
            sell_skipped.append({"horizon": h, "reason": f"suggested {s_best_t} outside sane bounds {_SELL_BOUNDS}"})
            continue

        redis_client.setex(f"stockai:signal_thresholds:SELL:{h}", _REDIS_TTL, str(round(s_best_t, 4)))
        sell_applied.append({
            "horizon": h,
            "direction": "SELL",
            "previous_threshold": _CURRENT_SELL,
            "new_threshold": round(s_best_t, 4),
            "ev_lift_pct": s_ev_lift,
            "train_n": len(s_train_bucket),
            "validation_stats": s_best_stats,
        })

    return {
        "buy_applied": applied,
        "buy_skipped": skipped,
        "sell_applied": sell_applied,
        "sell_skipped": sell_skipped,
        "redis_ttl_days": 30,
    }


@router.post("/tune_style_profiles")
def tune_style_profiles(
    days: int = Query(120, description="Look-back window in calendar days"),
    min_samples: int = Query(10, description="Minimum outcomes required per bucket"),
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Sweep style-specific gate parameters against live signal_outcomes and apply optimal values.

    For each style × parameter combination, groups outcomes by the relevant field in
    signal.reasons, finds the value that maximises expected-value (win_rate × avg_return),
    and writes it to Redis (stockai:style_tune:{STYLE}:{param}, 30-day TTL).

    Parameters tuned:
      - ml_weight_cap: optimal maximum ML fusion weight per style
      - adx_min: optimal ADX minimum threshold below which signals are compressed
      - high_vol_compression: whether high-vol compression is helping or hurting
      - breadth_compression: whether breadth compression threshold is calibrated

    Signal generator reads these from Redis via _get_style_tuned_param().
    Run weekly (Sunday) alongside TA and conviction weight calibration.
    """
    import statistics as _stats

    cutoff = date.today() - timedelta(days=days)
    outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.signal_direction == "BUY",
        )
    ).scalars().all()

    # Fetch reasons JSON for each outcome's signal
    signal_ids = [o.signal_id for o in outcomes if o.signal_id]
    signals_map: dict[int, dict] = {}
    if signal_ids:
        rows = session.execute(
            select(Signal.id, Signal.reasons).where(Signal.id.in_(signal_ids))
        ).all()
        for row in rows:
            if row.reasons:
                signals_map[row.id] = row.reasons

    redis_client = _get_redis()
    _REDIS_TTL = 30 * 86400
    applied: list[dict] = []
    skipped: list[dict] = []

    def _ev_at(subset):
        if not subset:
            return None
        wins = sum(1 for o in subset if o.is_correct)
        rets = [o.pct_return for o in subset if o.pct_return is not None]
        acc = wins / len(subset)
        avg_ret = _stats.mean(rets) if rets else 0.0
        # T232-OC4: avg_ret already IS the expected value — see fix note near the OC3
        # calibration functions above. Multiplying by acc double-counts win probability.
        return avg_ret * 100, acc, avg_ret

    # T234-SIG-INSAMPLE-GATE-TUNING: this function used to sweep every candidate directly
    # against the full sample and apply whatever scored best — an in-sample argmax with no
    # validation, the exact failure mode outcomes_calibrate_apply's own comments document as
    # the cause of a prior live incident (CAL-1). Now uses the same chronological 70/30
    # train/validation split as that sibling endpoint: search for the best candidate on the
    # OLDER 70% (train), then only apply it if it ALSO shows a real edge on the NEWER, never-
    # searched 30% (validation) — a candidate that only looks good in-sample won't survive this.
    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        style_outcomes = sorted(
            [o for o in outcomes if o.horizon.value == style],
            key=lambda o: o.signal_date,
        )
        if len(style_outcomes) < min_samples * 4:
            # need enough for train AND validation to each independently clear min_samples * 2
            # (min_samples * 2 was already this function's own per-sweep floor before this fix)
            skipped.append({"style": style, "reason": f"only {len(style_outcomes)} outcomes (need {min_samples * 4} for a valid train/validation split)"})
            continue

        style_with_reasons = [
            (o, signals_map.get(o.signal_id, {}))
            for o in style_outcomes
            if o.signal_id and o.signal_id in signals_map
        ]
        if len(style_with_reasons) < min_samples * 2:
            skipped.append({"style": style, "reason": f"only {len(style_with_reasons)} outcomes with reasons JSON"})
            continue

        split = max(1, int(len(style_with_reasons) * 0.7))
        train_sr = style_with_reasons[:split]
        val_sr = style_with_reasons[split:]

        # ── ml_weight_cap: sweep 0.15–0.75, find cap where EV is maximised on TRAIN,
        #    then require the SAME cap to beat the effectively-uncapped baseline on VALIDATION ──
        best_ml_ev, best_ml_cap = -999.0, None
        for cap_int in range(15, 76, 5):
            cap = cap_int / 100.0
            sub = [o for o, r in train_sr if r.get("ml_weight", 0) <= cap + 0.05]
            if len(sub) < min_samples:
                continue
            ev_result = _ev_at(sub)
            if ev_result and ev_result[0] > best_ml_ev:
                best_ml_ev = ev_result[0]
                best_ml_cap = cap

        if best_ml_cap is not None:
            val_sub = [o for o, r in val_sr if r.get("ml_weight", 0) <= best_ml_cap + 0.05]
            baseline_sub = [o for o, r in val_sr]  # uncapped baseline: every validation outcome
            val_result = _ev_at(val_sub)
            baseline_result = _ev_at(baseline_sub)
            if val_result and baseline_result and len(val_sub) >= min_samples and val_result[0] > baseline_result[0]:
                redis_client.setex(f"stockai:style_tune:{style}:ml_weight_cap", _REDIS_TTL, str(round(best_ml_cap, 2)))
                applied.append({"style": style, "param": "ml_weight_cap", "value": best_ml_cap,
                                "train_ev_pct": round(best_ml_ev, 2), "validation_ev_pct": round(val_result[0], 2),
                                "validation_baseline_ev_pct": round(baseline_result[0], 2)})
            else:
                skipped.append({"style": style, "param": "ml_weight_cap",
                                "reason": "did not beat baseline (or insufficient samples) on the validation slice",
                                "train_best_cap": best_ml_cap})

        # ── adx_min: find ADX level below which accuracy < 45% on TRAIN, then confirm the
        #    same threshold still shows below/above separation on VALIDATION ──
        adx_train = [(o, r) for o, r in train_sr if r.get("adx") is not None]
        adx_val = [(o, r) for o, r in val_sr if r.get("adx") is not None]
        if len(adx_train) >= min_samples:
            best_adx = None
            for adx_thresh in range(10, 40, 2):
                below = [o for o, r in adx_train if r.get("adx", 99) < adx_thresh]
                above = [o for o, r in adx_train if r.get("adx", 0) >= adx_thresh]
                if len(below) < min_samples or len(above) < min_samples:
                    continue
                below_acc = sum(1 for o in below if o.is_correct) / len(below)
                above_acc = sum(1 for o in above if o.is_correct) / len(above)
                if below_acc < 0.45 and above_acc > below_acc + 0.05:
                    best_adx = adx_thresh
                    break
            if best_adx is not None:
                val_below = [o for o, r in adx_val if r.get("adx", 99) < best_adx]
                val_above = [o for o, r in adx_val if r.get("adx", 0) >= best_adx]
                if len(val_below) >= min_samples // 2 and len(val_above) >= min_samples // 2:
                    val_below_acc = sum(1 for o in val_below if o.is_correct) / len(val_below)
                    val_above_acc = sum(1 for o in val_above if o.is_correct) / len(val_above)
                    if val_below_acc < val_above_acc:
                        redis_client.setex(f"stockai:style_tune:{style}:adx_min", _REDIS_TTL, str(best_adx))
                        applied.append({"style": style, "param": "adx_min", "value": best_adx,
                                        "validation_below_acc": round(val_below_acc, 3),
                                        "validation_above_acc": round(val_above_acc, 3)})
                    else:
                        skipped.append({"style": style, "param": "adx_min",
                                        "reason": "below/above separation did not replicate on validation slice",
                                        "train_threshold": best_adx})
                else:
                    skipped.append({"style": style, "param": "adx_min",
                                    "reason": "insufficient validation-slice samples to confirm train threshold",
                                    "train_threshold": best_adx})

        # ── breadth_compression: verify compression is justified on TRAIN (breadth<40
        #    underperforms), then confirm the same direction holds on VALIDATION ──
        breadth_train = [(o, r) for o, r in train_sr if r.get("breadth_pct") is not None]
        breadth_val = [(o, r) for o, r in val_sr if r.get("breadth_pct") is not None]
        if len(breadth_train) >= min_samples:
            low_breadth  = [o for o, r in breadth_train if r.get("breadth_pct", 100) < 40]
            high_breadth = [o for o, r in breadth_train if r.get("breadth_pct", 0) >= 40]
            if len(low_breadth) >= min_samples // 2 and len(high_breadth) >= min_samples // 2:
                lb_acc = sum(1 for o in low_breadth if o.is_correct) / len(low_breadth)
                hb_acc = sum(1 for o in high_breadth if o.is_correct) / len(high_breadth)
                val_low  = [o for o, r in breadth_val if r.get("breadth_pct", 100) < 40]
                val_high = [o for o, r in breadth_val if r.get("breadth_pct", 0) >= 40]
                _val_ok = len(val_low) >= min_samples // 4 and len(val_high) >= min_samples // 4
                if lb_acc < hb_acc - 0.08:
                    if _val_ok:
                        val_lb_acc = sum(1 for o in val_low if o.is_correct) / len(val_low)
                        val_hb_acc = sum(1 for o in val_high if o.is_correct) / len(val_high)
                        if val_lb_acc < val_hb_acc:
                            new_bc = 0.88  # tighter than default 0.90
                            redis_client.setex(f"stockai:style_tune:{style}:breadth_compression", _REDIS_TTL, str(new_bc))
                            applied.append({"style": style, "param": "breadth_compression", "value": new_bc,
                                            "train_low_acc": round(lb_acc, 3), "train_high_acc": round(hb_acc, 3),
                                            "validation_low_acc": round(val_lb_acc, 3), "validation_high_acc": round(val_hb_acc, 3)})
                        else:
                            skipped.append({"style": style, "param": "breadth_compression",
                                            "reason": "low-breadth underperformance did not replicate on validation slice"})
                    else:
                        skipped.append({"style": style, "param": "breadth_compression",
                                        "reason": "insufficient validation-slice samples to confirm train finding"})
                elif lb_acc > hb_acc - 0.02:
                    # Breadth not predictive on train — restore default without needing validation
                    new_bc = 0.95
                    redis_client.setex(f"stockai:style_tune:{style}:breadth_compression", _REDIS_TTL, str(new_bc))
                    applied.append({"style": style, "param": "breadth_compression", "value": new_bc,
                                    "note": "low-breadth underperformance not significant on train slice"})

    return {"applied": applied, "skipped": skipped, "n_outcomes_analyzed": len(outcomes), "redis_ttl_days": 30}


@router.post("/watchdog")
def signal_watchdog(
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Self-healing threshold watchdog: monitor rolling win rates and auto-adjust.

    Checks the last 14-day rolling win rate per style. If win rate drops below 38%,
    applies an emergency threshold tightening (+0.03). If signal count drops to zero
    for 7+ consecutive days, relaxes the threshold by 0.02 (floor: hardcoded default).

    Writes to stockai:watchdog:{STYLE}:threshold (Redis, 7-day TTL) — this key is
    read by _get_dynamic_buy_threshold() BEFORE the calibrated key, ensuring the
    watchdog's response is immediate.

    Caps adjustments at 3 tightenings before requiring a manual review (prevents
    the system from silencing itself completely).

    Schedule: daily (06:00 ET) from market-data scheduler.
    """
    from ..generators.signals import _STYLE_PROFILES

    _14D = date.today() - timedelta(days=14)
    _7D  = date.today() - timedelta(days=7)
    _REDIS_TTL_7D = 7 * 86400
    _MAX_TIGHTEN = 3

    # Bull-regime thresholds as floors — source of truth is _STYLE_PROFILES (T232-SIG12).
    _DEFAULT_THRESHOLDS = {
        h: _STYLE_PROFILES[h]["buy_threshold"]["bull"] for h in ("SHORT", "SWING", "LONG", "GROWTH")
    }

    redis_client = _get_redis()
    actions: list[dict] = []
    status: list[dict] = []

    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        # 14-day outcomes
        outcomes_14d = session.execute(
            select(SignalOutcome).where(
                SignalOutcome.signal_date >= _14D,
                SignalOutcome.is_correct.is_not(None),
                SignalOutcome.signal_direction == "BUY",
                SignalOutcome.horizon == SignalHorizon[style],
            )
        ).scalars().all()

        # 7-day signal count (regardless of evaluation status)
        signals_7d = session.execute(
            select(func.count(Signal.id)).where(
                Signal.ts >= _7D,
                Signal.signal == SignalType.BUY,
                Signal.horizon == SignalHorizon[style],
            )
        ).scalar() or 0

        win_rate_14d = None
        if outcomes_14d:
            wins = sum(1 for o in outcomes_14d if o.is_correct)
            win_rate_14d = wins / len(outcomes_14d)

        # Current watchdog adjustment
        current_key = f"stockai:watchdog:{style}:threshold"
        tighten_count_key = f"stockai:watchdog:{style}:tighten_count"
        current_adj = redis_client.get(current_key)
        tighten_count = int(redis_client.get(tighten_count_key) or 0)

        floor_threshold = _DEFAULT_THRESHOLDS.get(style, 0.65)

        action = None
        if win_rate_14d is not None and win_rate_14d < 0.38 and len(outcomes_14d) >= 5:
            if tighten_count >= _MAX_TIGHTEN:
                action = "max_tighten_reached_manual_review_needed"
                actions.append({"style": style, "action": action, "win_rate_14d": round(win_rate_14d, 3)})
            else:
                # Tighten by 0.03 from the current adjustment (or calibrated base)
                current_val = float(current_adj) if current_adj else (
                    float(redis_client.get(f"stockai:signal_thresholds:{style}") or 0) or floor_threshold
                )
                new_val = min(current_val + 0.03, floor_threshold + 0.12)  # max +12pp above floor
                redis_client.setex(current_key, _REDIS_TTL_7D, str(round(new_val, 4)))
                redis_client.setex(tighten_count_key, _REDIS_TTL_7D, str(tighten_count + 1))
                action = "tightened"
                actions.append({"style": style, "action": action, "from": round(current_val, 4),
                                 "to": round(new_val, 4), "win_rate_14d": round(win_rate_14d, 3),
                                 "tighten_count": tighten_count + 1})

        elif signals_7d == 0 and current_adj:
            # No signals for 7 days — the threshold may be too tight; relax
            current_val = float(current_adj)
            if current_val > floor_threshold + 0.01:
                new_val = max(current_val - 0.02, floor_threshold)
                redis_client.setex(current_key, _REDIS_TTL_7D, str(round(new_val, 4)))
                redis_client.delete(tighten_count_key)  # reset tighten count on relax
                action = "relaxed"
                actions.append({"style": style, "action": action, "from": round(current_val, 4),
                                 "to": round(new_val, 4), "signals_7d": signals_7d})

        status.append({
            "style": style,
            "win_rate_14d": round(win_rate_14d, 3) if win_rate_14d is not None else None,
            "n_outcomes_14d": len(outcomes_14d),
            "signals_7d": signals_7d,
            "current_watchdog_threshold": float(current_adj) if current_adj else None,
            "tighten_count": tighten_count,
            "action": action,
        })

    return {"actions": actions, "status": status}


@router.get("/tune_status")
def tune_status(
    _: str = Depends(get_current_username),
    session: Session = Depends(get_session),
):
    """Read-only snapshot of all self-tuning system state (TIER88).

    Returns per-style: hardcoded defaults, Redis overrides (watchdog/calibrated/
    auto-tuner), effective values (priority: watchdog > calibrated > default),
    14-day rolling win rate, 7-day BUY signal count, and watchdog state.
    No side effects — safe to poll from the frontend.
    """
    from ..generators.signals import _STYLE_PROFILES

    redis_client = _get_redis()
    _14D = date.today() - timedelta(days=14)
    _7D  = date.today() - timedelta(days=7)

    styles_out: dict = {}
    for style in ("SHORT", "SWING", "LONG", "GROWTH"):
        p = _STYLE_PROFILES[style]

        # Read all Redis overrides
        watchdog_threshold     = _redis_get_float(f"stockai:watchdog:{style}:threshold")
        calibrated_threshold   = _redis_get_float(f"stockai:signal_thresholds:{style}")
        ml_weight_cap_tuned    = _redis_get_float(f"stockai:style_tune:{style}:ml_weight_cap")
        adx_min_tuned          = _redis_get_float(f"stockai:style_tune:{style}:adx_min")
        breadth_comp_tuned     = _redis_get_float(f"stockai:style_tune:{style}:breadth_compression")
        tighten_count          = int(redis_client.get(f"stockai:watchdog:{style}:tighten_count") or 0)

        # Effective values — priority: watchdog > calibrated > hardcoded
        eff_threshold = watchdog_threshold or calibrated_threshold or p["buy_threshold"]["bull"]
        eff_ml_cap    = ml_weight_cap_tuned if ml_weight_cap_tuned is not None else p["ml_weight_cap"]
        eff_adx_min   = adx_min_tuned       if adx_min_tuned is not None       else p.get("adx_min")
        eff_breadth   = breadth_comp_tuned  if breadth_comp_tuned is not None  else p.get("breadth_compression")

        # 14-day win rate
        outcomes_14d = session.execute(
            select(SignalOutcome).where(
                SignalOutcome.signal_date >= _14D,
                SignalOutcome.is_correct.is_not(None),
                SignalOutcome.signal_direction == "BUY",
                SignalOutcome.horizon == SignalHorizon[style],
            )
        ).scalars().all()

        win_rate_14d: float | None = None
        if outcomes_14d:
            wins = sum(1 for o in outcomes_14d if o.is_correct)
            win_rate_14d = round(wins / len(outcomes_14d), 3)

        # 7-day BUY signal count
        signals_7d = session.execute(
            select(func.count(Signal.id)).where(
                Signal.ts >= _7D,
                Signal.signal == SignalType.BUY,
                Signal.horizon == SignalHorizon[style],
            )
        ).scalar() or 0

        # Watchdog status label
        if watchdog_threshold is not None:
            watchdog_status = "max_tighten_review" if tighten_count >= 3 else f"tightened_{tighten_count}x"
        else:
            watchdog_status = "nominal"

        styles_out[style] = {
            "defaults": {
                "buy_threshold_bull": p["buy_threshold"]["bull"],
                "ml_weight_cap": p["ml_weight_cap"],
                "adx_min": p.get("adx_min"),
                "breadth_compression": p.get("breadth_compression"),
            },
            "redis_overrides": {
                "watchdog_threshold": watchdog_threshold,
                "calibrated_threshold": calibrated_threshold,
                "ml_weight_cap": ml_weight_cap_tuned,
                "adx_min": adx_min_tuned,
                "breadth_compression": breadth_comp_tuned,
            },
            "effective": {
                "buy_threshold_bull": round(eff_threshold, 4),
                "ml_weight_cap": round(eff_ml_cap, 4),
                "adx_min": round(eff_adx_min, 1) if eff_adx_min is not None else None,
                "breadth_compression": round(eff_breadth, 3) if eff_breadth is not None else None,
            },
            "performance": {
                "win_rate_14d": win_rate_14d,
                "n_outcomes_14d": len(outcomes_14d),
                "signals_7d": signals_7d,
            },
            "watchdog": {
                "status": watchdog_status,
                "tighten_count": tighten_count,
                "current_threshold": watchdog_threshold,
            },
        }

    return {
        "as_of": date.today().isoformat(),
        "config_loaded_at": thresholds_loaded_at(),
        "styles": styles_out,
    }


_DECAY_DAYS = [1, 2, 3, 5, 7, 10, 15, 20, 30]


@router.get("/alpha_decay")
def alpha_decay(
    horizon: str = Query("SWING"),
    lookback_days: int = Query(365, ge=30, le=730),
    regime: str | None = Query(None),
    session: Session = Depends(get_session),
):
    """TM-2: Average cumulative return after BUY signals at each day offset.

    Uses signal_outcomes joined to daily prices to compute returns at 1, 2, 3,
    5, 7, 10, 15, 20, and 30 calendar days after the entry date.  Returns p25/
    p75 bands and the empirically optimal hold day (peak average return).
    """
    from bisect import bisect_left
    from collections import defaultdict

    cutoff = date.today() - timedelta(days=lookback_days)

    try:
        hz = SignalHorizon(horizon.upper())
    except ValueError:
        raise HTTPException(400, f"Unknown horizon: {horizon}")

    q = select(SignalOutcome).where(
        SignalOutcome.signal_date >= cutoff,
        SignalOutcome.signal_direction == "BUY",
        SignalOutcome.horizon == hz,
        SignalOutcome.entry_price.is_not(None),
        SignalOutcome.entry_date.is_not(None),
    )
    if regime:
        q = q.where(SignalOutcome.market_regime == regime)

    outcomes = session.execute(q).scalars().all()

    if not outcomes:
        return {
            "horizon": horizon.upper(), "signal_count": 0,
            "lookback_days": lookback_days,
            "optimal_hold_days": None, "optimal_return_pct": None,
            "curve": [],
        }

    stock_ids = {o.stock_id for o in outcomes}
    min_entry = min(o.entry_date for o in outcomes)
    max_entry = max(o.entry_date for o in outcomes)
    price_end = max_entry + timedelta(days=37)

    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close).where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= datetime.combine(min_entry, datetime.min.time()),
            Price.ts <= datetime.combine(price_end, datetime.max.time()),
        ).order_by(Price.stock_id, Price.ts)
    ).all()

    price_map: dict[int, list] = defaultdict(list)
    for stock_id, ts, close in price_rows:
        price_map[stock_id].append((ts.date(), close))

    def price_on_or_after(stock_id: int, target: date) -> float | None:
        bars = price_map.get(stock_id)
        if not bars:
            return None
        dates = [b[0] for b in bars]
        idx = bisect_left(dates, target)
        for i in range(idx, min(idx + 6, len(bars))):
            if (bars[i][0] - target).days <= 5:
                return bars[i][1]
        return None

    day_returns: dict[int, list] = {d: [] for d in _DECAY_DAYS}
    for o in outcomes:
        for td in _DECAY_DAYS:
            p = price_on_or_after(o.stock_id, o.entry_date + timedelta(days=td))
            if p and o.entry_price and o.entry_price > 0:
                day_returns[td].append((p / o.entry_price - 1) * 100)

    curve = []
    for d in _DECAY_DAYS:
        rets = sorted(day_returns[d])
        n = len(rets)
        if n == 0:
            curve.append({"day": d, "avg_return_pct": None, "p25": None, "p75": None, "n": 0})
            continue
        avg = sum(rets) / n
        curve.append({
            "day": d,
            "avg_return_pct": round(avg, 2),
            "p25": round(rets[max(0, int(n * 0.25) - 1)], 2),
            "p75": round(rets[min(n - 1, int(n * 0.75))], 2),
            "n": n,
        })

    best = max((c for c in curve if c["avg_return_pct"] is not None),
               key=lambda c: c["avg_return_pct"], default=None)

    return {
        "horizon": horizon.upper(),
        "signal_count": len(outcomes),
        "lookback_days": lookback_days,
        "optimal_hold_days": best["day"] if best else None,
        "optimal_return_pct": best["avg_return_pct"] if best else None,
        "curve": curve,
    }


@router.get("/information_coefficient")
def information_coefficient(
    horizon: str = Query("SWING"),
    lookback_days: int = Query(365, ge=90, le=730),
    session: Session = Depends(get_session),
):
    """TM-3: Monthly IC — Spearman rank correlation between fused_prob rank and
    actual return rank.  IC > 0.05 is good; IC_IR (mean/std) > 0.5 is excellent.
    """
    import statistics

    cutoff = date.today() - timedelta(days=lookback_days)
    try:
        hz = SignalHorizon(horizon.upper())
    except ValueError:
        raise HTTPException(400, f"Unknown horizon: {horizon}")

    outcomes = session.execute(
        select(SignalOutcome).where(
            SignalOutcome.horizon == hz,
            SignalOutcome.signal_direction == "BUY",
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.fused_prob.is_not(None),
            SignalOutcome.pct_return.is_not(None),
        )
    ).scalars().all()

    from collections import defaultdict
    monthly: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for o in outcomes:
        monthly[o.signal_date.strftime("%Y-%m")].append(
            (float(o.fused_prob), float(o.pct_return))
        )

    def _rank(lst: list[float]) -> list[float]:
        order = sorted(range(len(lst)), key=lambda i: lst[i])
        ranks = [0.0] * len(lst)
        for r, i in enumerate(order):
            ranks[i] = float(r + 1)
        return ranks

    series = []
    for month in sorted(monthly):
        pairs = monthly[month]
        if len(pairs) < 5:
            continue
        probs = [p[0] for p in pairs]
        rets = [p[1] for p in pairs]
        rp = _rank(probs)
        rr = _rank(rets)
        n = len(rp)
        mp, mr = sum(rp) / n, sum(rr) / n
        cov = sum((a - mp) * (b - mr) for a, b in zip(rp, rr)) / n
        sp = (sum((a - mp) ** 2 for a in rp) / n) ** 0.5
        sr = (sum((b - mr) ** 2 for b in rr) / n) ** 0.5
        ic = cov / (sp * sr) if sp > 0 and sr > 0 else 0.0
        series.append({"month": month, "ic": round(ic, 4), "n": n})

    if not series:
        return {
            "horizon": horizon, "lookback_days": lookback_days,
            "monthly_ic": [], "ic_mean": None, "ic_std": None,
            "ic_ir": None, "total_periods": 0,
            "message": "Not enough data — at least 5 BUY outcomes per month required",
        }

    ics = [s["ic"] for s in series]
    ic_mean = statistics.mean(ics)
    ic_std = statistics.stdev(ics) if len(ics) > 1 else 0.0
    ic_ir = round(ic_mean / ic_std, 3) if ic_std > 0 else None

    return {
        "horizon": horizon,
        "lookback_days": lookback_days,
        "monthly_ic": series,
        "ic_mean": round(ic_mean, 4),
        "ic_std": round(ic_std, 4),
        "ic_ir": ic_ir,
        "total_periods": len(series),
        "quality": "excellent" if ic_mean > 0.05 else "good" if ic_mean > 0.02 else "poor",
    }


@router.get("/factor_attribution")
def factor_attribution(
    horizon: str = Query("SWING"),
    lookback_days: int = Query(365, ge=90, le=730),
    min_count: int = Query(10),
    session: Session = Depends(get_session),
):
    """TM-4: For each boolean reason flag, compute presence in winners vs losers.
    Edge = win_pct - los_pct.  Positive edge = factor predicts wins; negative = noise.
    """
    cutoff = date.today() - timedelta(days=lookback_days)
    try:
        hz = SignalHorizon(horizon.upper())
    except ValueError:
        raise HTTPException(400, f"Unknown horizon: {horizon}")

    rows = session.execute(
        select(SignalOutcome.is_correct, Signal.reasons)
        .join(Signal, Signal.id == SignalOutcome.signal_id)
        .where(
            SignalOutcome.horizon == hz,
            SignalOutcome.signal_direction == "BUY",
            SignalOutcome.signal_date >= cutoff,
            SignalOutcome.is_correct.is_not(None),
            Signal.reasons.is_not(None),
        )
    ).all()

    if not rows:
        return {
            "factors": [], "total_winners": 0, "total_losers": 0,
            "message": "No evaluated outcomes with reason data yet",
        }

    n_win = sum(1 for r in rows if r.is_correct)
    n_los = sum(1 for r in rows if not r.is_correct)
    key_wins: dict[str, int] = {}
    key_los: dict[str, int] = {}

    for r in rows:
        reasons = r.reasons or {}
        bucket = key_wins if r.is_correct else key_los
        for k, v in reasons.items():
            if isinstance(v, bool) and v:
                bucket[k] = bucket.get(k, 0) + 1

    all_keys = set(key_wins) | set(key_los)
    factors = []
    for k in all_keys:
        wc = key_wins.get(k, 0)
        lc = key_los.get(k, 0)
        if wc + lc < min_count:
            continue
        wp = wc / n_win if n_win > 0 else 0.0
        lp = lc / n_los if n_los > 0 else 0.0
        factors.append({
            "factor": k,
            "win_pct": round(wp * 100, 1),
            "los_pct": round(lp * 100, 1),
            "edge": round((wp - lp) * 100, 1),
            "win_count": wc,
            "los_count": lc,
        })

    factors.sort(key=lambda x: x["edge"], reverse=True)

    return {
        "horizon": horizon,
        "lookback_days": lookback_days,
        "total_winners": n_win,
        "total_losers": n_los,
        "factors": factors,
    }


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


@router.get("/confidence-calibration")
def confidence_calibration_map(
    refresh: bool = Query(False, description="Force rebuild from DB, bypassing Redis cache"),
    session: Session = Depends(get_session),
):
    """Return actual win rate by (horizon, direction, market, confidence band) from the
    last 180 days of signal_outcomes.

    T223/T232-OC5: Makes signal confidence auditable, keyed narrowly enough that the
    comparison is meaningful. Confidence is direction-agnostic, and BUY/SELL, different
    horizons, and US/HK have documented divergent base rates — pooling them into a single
    band-only win rate mixed populations that shouldn't be compared. Keys are
    "HORIZON|DIRECTION|MARKET|BAND" (market-specific, preferred) or "HORIZON|DIRECTION|BAND"
    (pooled across markets, used when the market-specific bucket doesn't reach the
    min-count of 30). Use this to compare confidence bands within the same
    horizon+direction(+market) and tune entry filters accordingly — comparing across
    different horizons/directions/markets is exactly the mistake this keying prevents.

    T232-OC5: this route MUST be registered before /{symbol} below — FastAPI matches
    routes in registration order, and /{symbol} would otherwise swallow this path,
    treating "confidence-calibration" as a stock symbol (this bug existed from when the
    route was first added and made the endpoint completely unreachable).
    """
    if refresh:
        try:
            _get_redis().delete(_CONF_CAL_CACHE_KEY)
        except Exception:
            pass
    cal = _get_confidence_calibration(session)
    if not cal:
        return {"message": f"Insufficient signal_outcomes data (need >={_CONF_CAL_MIN_COUNT} evaluated outcomes per bucket)", "buckets": {}}
    return {
        "buckets": cal,
        "note": "win_rate = fraction of signals in this (horizon, direction[, market]) confidence band that were correct within the hold window",
        "min_count": _CONF_CAL_MIN_COUNT,
        "lookback_days": 180,
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


@router.post("/outcomes/evaluate")
def evaluate_signal_outcomes(session: Session = Depends(get_session), _: str = Depends(get_current_username)):
    """Evaluate closed signal outcomes and persist them to signal_outcomes.

    For each BUY/SELL signal whose hold window has expired:
    - Entry price = first D1 close on or after signal date
    - Exit price  = first D1 close on or after entry_date + hold_window_days
    - pct_return  = (exit - entry) / entry
    - is_correct  = price went up for BUY, down for SELL

    INT-8: Also fills multi-window columns (price_5d/10d/20d, return_5d/10d/20d,
    is_correct_5d/10d/20d) and research_rec/research_score at evaluation time.
    Phase 2 of the same run updates existing outcome rows where window columns
    are NULL but sufficient time has now passed.

    Safe to re-run — already-evaluated signals (by UNIQUE signal_id) are skipped.
    Called automatically by the scheduler post-close.
    """
    from datetime import time as _time
    import bisect
    from collections import defaultdict
    import httpx as _httpx
    from sqlalchemy import or_

    today = date.today()
    # T232-SIG10: consider both tables — SELL's shortest window (5d SHORT) is smaller than
    # BUY's shortest (7d SHORT), so the candidate-signal cutoff must use whichever is smaller
    # or SELL signals eligible under their own shorter window would be filtered out too early.
    min_hold = min(min(_OUTCOME_HOLD_DAYS.values()), min(_SELL_OUTCOME_HOLD_DAYS.values()))
    cutoff = today - timedelta(days=min_hold)

    # IDs already in signal_outcomes — skip re-evaluation by signal_id
    evaluated_ids: set[int] = set(session.execute(
        select(SignalOutcome.signal_id)
    ).scalars().all())

    # Also track (stock_id, horizon, signal_date) to prevent duplicates from
    # multiple same-day signal refreshes producing multiple outcome rows.
    evaluated_sighd: set[tuple] = set(
        session.execute(
            select(SignalOutcome.stock_id, SignalOutcome.horizon, SignalOutcome.signal_date)
        ).all()
    )

    # BUY and SELL signals old enough that at least SHORT window could be closed
    pending_signals = session.execute(
        select(Signal, Stock.symbol)
        .join(Stock, Stock.id == Signal.stock_id)
        .where(
            Signal.signal.in_([SignalType.BUY, SignalType.SELL]),
            Signal.ts <= datetime.combine(cutoff, _time.max),
        )
        .order_by(Signal.ts)
    ).all()

    # Bulk-load D1 prices — always extend window to 20d for INT-8 multi-window
    pending_stock_ids = list({sig.stock_id for sig, _ in pending_signals})
    price_min_ts = min((sig.ts for sig, _ in pending_signals), default=datetime.now())
    price_max_ts = datetime.now() + timedelta(days=30)
    bulk_prices: list = []
    if pending_stock_ids:
        bulk_prices = session.execute(
            select(Price.stock_id, Price.ts, Price.close)
            .where(
                Price.stock_id.in_(pending_stock_ids),
                Price.timeframe == TimeFrame.D1,
                Price.ts >= price_min_ts,
                Price.ts <= price_max_ts,
            )
            .order_by(Price.stock_id, Price.ts)
        ).all()

    _outcome_price_map: dict[int, list[tuple]] = defaultdict(list)
    for pr in bulk_prices:
        pr_date = pr.ts.date() if hasattr(pr.ts, "date") else pr.ts
        _outcome_price_map[pr.stock_id].append((pr_date, float(pr.close)))

    def _lookup_outcome_price(stock_id: int, on_or_after: "date") -> "tuple | None":
        bucket = _outcome_price_map.get(stock_id, [])
        if not bucket:
            return None
        dates = [b[0] for b in bucket]
        idx = bisect.bisect_left(dates, on_or_after)
        if idx >= len(bucket):
            return None
        return bucket[idx]

    def _window_return(stock_id: int, entry_date: "date", entry_price: float, days: int, signal_direction: str = "BUY"):
        """Return (price, return_pct, is_correct) for a +N-day window, or (None, None, None).

        is_correct: BUY wins when ret clears the cost hurdle; SELL wins when ret falls
        below the negative hurdle (T232-OC4 — see _OUTCOME_WIN_HURDLE_PCT above).
        """
        target = entry_date + timedelta(days=days)
        if target > today:
            return None, None, None
        result = _lookup_outcome_price(stock_id, target)
        if result is None or entry_price <= 0:
            return None, None, None
        _, price = result
        ret = (price - entry_price) / entry_price
        is_correct = ret > _OUTCOME_WIN_HURDLE_PCT if signal_direction == "BUY" else ret < -_OUTCOME_WIN_HURDLE_PCT
        return float(price), ret, is_correct

    # Research recommendation cache — one network fetch per symbol per run
    _research_cache: dict[str, tuple] = {}

    def _fetch_research(symbol: str) -> "tuple[str | None, float | None]":
        if symbol in _research_cache:
            return _research_cache[symbol]
        try:
            _tok = _service_token()
            _r = _httpx.get(
                f"{_settings.research_engine_url}/research/{symbol}/summary",
                headers={"Authorization": f"Bearer {_tok}"},
                timeout=2.0,
            )
            if _r.status_code == 200:
                _d = _r.json()
                result = (_d.get("recommendation"), float(_d.get("overall_score") or 0) or None)
            else:
                result = (None, None)
        except Exception:
            result = (None, None)
        _research_cache[symbol] = result
        return result

    evaluated, skipped_open, skipped_no_price, censored = 0, 0, 0, 0

    for sig, symbol in pending_signals:
        if sig.id in evaluated_ids:
            continue

        horizon = sig.horizon.value
        # T232-SIG10: SELL uses its own shorter hold window — see _SELL_OUTCOME_HOLD_DAYS above.
        hold_days = (
            _SELL_OUTCOME_HOLD_DAYS[horizon] if sig.signal == SignalType.SELL
            else _OUTCOME_HOLD_DAYS[horizon]
        )
        signal_date = sig.ts.date()

        # Skip if another signal_id for the same (stock, horizon, date) was already evaluated.
        # This prevents 5×/day refreshes from creating duplicate outcome rows for the same
        # logical signal event.
        sighd_key = (sig.stock_id, sig.horizon, signal_date)
        if sighd_key in evaluated_sighd:
            continue

        # T+1 entry: use the first close STRICTLY AFTER signal_date so we avoid
        # same-day look-ahead bias (signal was generated after close; realistic
        # fill is the next trading day's open/close).
        entry_result = _lookup_outcome_price(sig.stock_id, signal_date + timedelta(days=1))
        if entry_result is None:
            skipped_no_price += 1
            continue

        entry_date, entry_price = entry_result
        exit_target = entry_date + timedelta(days=hold_days)

        if exit_target > today:
            skipped_open += 1
            continue

        exit_result = _lookup_outcome_price(sig.stock_id, exit_target)
        if exit_result is None:
            # T232-OC6: exit_target has passed but no price bar exists on/after it. Give
            # ordinary ingestion lag a grace window (weekends/holidays plus a buffer) before
            # concluding the price is permanently gone — otherwise a stock that's merely a
            # few days behind on ingestion gets wrongly censored as delisted.
            if today - exit_target > timedelta(days=_OUTCOME_CENSOR_GRACE_DAYS):
                censored += 1
                outcome = SignalOutcome(
                    signal_id=sig.id,
                    stock_id=sig.stock_id,
                    symbol=symbol,
                    horizon=sig.horizon,
                    signal_direction=sig.signal.value,
                    signal_date=signal_date,
                    confidence=sig.confidence,
                    fused_prob=sig.bullish_probability,
                    ta_score=(sig.reasons or {}).get("ta_score"),
                    ml_prob=(sig.reasons or {}).get("ml_probability"),
                    ml_auc=(sig.reasons or {}).get("ml_test_auc"),
                    market_regime=(sig.reasons or {}).get("market_regime"),
                    entry_date=entry_date,
                    entry_price=entry_price,
                    skip_reason="no_exit_price",
                )
                session.add(outcome)
                evaluated_ids.add(sig.id)
                evaluated_sighd.add(sighd_key)
            else:
                skipped_open += 1
            continue

        exit_date, exit_price = exit_result
        if entry_price <= 0:
            skipped_no_price += 1
            continue

        pct_return = (exit_price - entry_price) / entry_price
        hold_days_actual = (exit_date - entry_date).days
        # T232-OC4: require clearing a real cost hurdle, not just a bare zero line — see
        # _OUTCOME_WIN_HURDLE_PCT above for why 0.5% and what's deliberately NOT modeled here.
        is_correct = (
            pct_return > _OUTCOME_WIN_HURDLE_PCT if sig.signal == SignalType.BUY
            else pct_return < -_OUTCOME_WIN_HURDLE_PCT
        )

        # INT-8: multi-window forward returns (pass signal direction so SELL wins on negative returns)
        _sig_dir = sig.signal.value  # "BUY" or "SELL"
        p5, r5, c5   = _window_return(sig.stock_id, entry_date, entry_price, 5,  _sig_dir)
        p10, r10, c10 = _window_return(sig.stock_id, entry_date, entry_price, 10, _sig_dir)
        p20, r20, c20 = _window_return(sig.stock_id, entry_date, entry_price, 20, _sig_dir)
        res_rec, res_score = _fetch_research(symbol)

        reasons = sig.reasons or {}
        outcome = SignalOutcome(
            signal_id=sig.id,
            stock_id=sig.stock_id,
            symbol=symbol,
            horizon=sig.horizon,
            signal_direction=sig.signal.value,
            signal_date=signal_date,
            confidence=sig.confidence,
            fused_prob=sig.bullish_probability,
            ta_score=reasons.get("ta_score"),
            ml_prob=reasons.get("ml_probability"),
            ml_auc=reasons.get("ml_test_auc"),
            market_regime=reasons.get("market_regime"),
            entry_date=entry_date,
            entry_price=entry_price,
            exit_date=exit_date,
            exit_price=exit_price,
            hold_days=hold_days_actual,
            pct_return=pct_return,
            is_correct=is_correct,
            price_5d=p5, return_5d=r5, is_correct_5d=c5,
            price_10d=p10, return_10d=r10, is_correct_10d=c10,
            price_20d=p20, return_20d=r20, is_correct_20d=c20,
            research_rec=res_rec,
            research_score=res_score,
        )
        session.add(outcome)
        evaluated_ids.add(sig.id)
        evaluated_sighd.add(sighd_key)
        evaluated += 1

    session.commit()

    # ── Phase 2: Fill NULL window columns on existing outcome rows ─────────────
    # Outcomes created before INT-8 (or where a window wasn't closed at create time)
    # may have NULL price_5d/10d/20d. Fill them in as the windows mature.
    needs_update = session.execute(
        select(SignalOutcome)
        .where(
            SignalOutcome.entry_date.is_not(None),
            SignalOutcome.entry_price.is_not(None),
            # Include both BUY and SELL outcomes — SELL wins when return < 0
            or_(
                SignalOutcome.price_5d.is_(None),
                SignalOutcome.price_10d.is_(None),
                SignalOutcome.price_20d.is_(None),
            )
        )
        .limit(500)
    ).scalars().all()

    updated = 0
    if needs_update:
        # Extend price map with any stocks not already loaded
        missing_ids = [o.stock_id for o in needs_update if o.stock_id not in _outcome_price_map]
        if missing_ids:
            upd_prices = session.execute(
                select(Price.stock_id, Price.ts, Price.close)
                .where(
                    Price.stock_id.in_(missing_ids),
                    Price.timeframe == TimeFrame.D1,
                )
                .order_by(Price.stock_id, Price.ts)
            ).all()
            for pr in upd_prices:
                pr_date = pr.ts.date() if hasattr(pr.ts, "date") else pr.ts
                _outcome_price_map[pr.stock_id].append((pr_date, float(pr.close)))

        for out in needs_update:
            changed = False
            ep, ed = out.entry_price, out.entry_date
            _out_dir = out.signal_direction or "BUY"  # SELL wins on negative return
            if out.price_5d is None:
                p5, r5, c5 = _window_return(out.stock_id, ed, ep, 5, _out_dir)
                if p5 is not None:
                    out.price_5d, out.return_5d, out.is_correct_5d = p5, r5, c5
                    changed = True
            if out.price_10d is None:
                p10, r10, c10 = _window_return(out.stock_id, ed, ep, 10, _out_dir)
                if p10 is not None:
                    out.price_10d, out.return_10d, out.is_correct_10d = p10, r10, c10
                    changed = True
            if out.price_20d is None:
                p20, r20, c20 = _window_return(out.stock_id, ed, ep, 20, _out_dir)
                if p20 is not None:
                    out.price_20d, out.return_20d, out.is_correct_20d = p20, r20, c20
                    changed = True
            if out.research_rec is None:
                rr, rs = _fetch_research(out.symbol)
                if rr is not None:
                    out.research_rec, out.research_score = rr, rs
                    changed = True
            if changed:
                updated += 1

        session.commit()

    log.info(
        "outcomes.evaluate_done",
        evaluated=evaluated,
        skipped_open=skipped_open,
        skipped_no_price=skipped_no_price,
        censored=censored,
        updated_windows=updated,
    )
    return {
        "evaluated": evaluated,
        "skipped_open": skipped_open,
        "skipped_no_price": skipped_no_price,
        "censored": censored,
        "updated_windows": updated,
    }


@router.get("/gate_backtest")
def gate_backtest(
    lookback_days: int = Query(90, ge=30, le=365),
    style: str = Query("SWING", regex="^(SHORT|SWING|LONG|GROWTH)$"),
    hold_days: int = Query(10, ge=1, le=60),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Compare old vs new conviction gate logic against historical BUY signals.

    Replays _is_conviction_buy with old and new parameters to measure how many
    more signals fire and whether the newly-unblocked signals actually perform well.

    Gate changes evaluated:
      1. MACD condition: old = (hist > 0 AND rising) OR crossover
                         new = hist > 0 OR rising OR crossover
      2. MACD soft tier: old = hard failure (blocks alone)
                         new = soft failure (1 allowed per near-conviction tier)
      3. GROWTH RSI lo:  old = 55  →  new = 50

    Returns per-group win-rate and avg return so you can validate each change.
    """
    cache_key = f"signals:cache:gate_backtest:{lookback_days}:{style}:{hold_days}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    since = date.today() - timedelta(days=lookback_days)
    try:
        horizon_enum = SignalHorizon(style.upper())
    except ValueError:
        horizon_enum = SignalHorizon.SWING

    rows = session.execute(
        select(Signal.ts, Signal.reasons, Signal.stock_id, Stock.symbol, Signal.horizon)
        .join(Stock, Signal.stock_id == Stock.id)
        .where(
            Signal.signal == SignalType.BUY,
            Signal.horizon == horizon_enum,
            Signal.ts >= since,
            Signal.reasons.isnot(None),
        )
        .order_by(Signal.ts)
    ).all()

    _REGIME_ML_THRESH = {"bull": 0.65, "neutral": 0.70, "high_vol": 0.78, "bear": 0.78}

    def _apply_gate(r: dict, horizon: str, new_macd_cond: bool, new_macd_soft: bool, new_growth_rsi: bool):
        """Inline replay of _is_conviction_buy. Returns (passes, tier, list[failed_keys])."""
        failed: list[str] = []

        # K-Score — may not be stored in reasons (fetched by scheduler); treat None as soft-pass
        kscore = r.get("kscore")
        if kscore is not None and float(kscore) < 55:
            failed.append("KScore")

        # 4a — Uptrend structure
        if horizon == "GROWTH":
            if not r.get("trend_above_sma50"):
                failed.append("Uptrend")
        else:
            if not (r.get("sma50_above_sma200") and r.get("trend_above_sma50")):
                failed.append("Uptrend")

        # 4b — RSI range
        rsi = r.get("rsi")
        if rsi is not None:
            rsi_f = float(rsi)
            if horizon == "GROWTH":
                lo = 50.0 if new_growth_rsi else 55.0
                rsi_ok = lo <= rsi_f <= 85.0
            else:
                rsi_ok = 45.0 <= rsi_f <= 72.0
            if not rsi_ok:
                failed.append("RSI")

        # 4c — MACD momentum
        macd_hist = float(r.get("macd_hist") or 0)
        macd_rising = bool(r.get("macd_rising"))
        macd_cross = bool(r.get("macd_zero_cross_up"))
        if new_macd_cond:
            macd_ok = macd_hist > 0 or macd_rising or macd_cross
        else:
            macd_ok = (macd_hist > 0 and macd_rising) or macd_cross
        if not macd_ok:
            failed.append("MACD")

        # 4d — OBV (always soft)
        if not r.get("obv_trend_bullish"):
            failed.append("OBV")

        # 4e — ADX (always soft)
        if not r.get("adx_trending"):
            failed.append("ADX")

        # 5 — ML probability (always soft)
        # T234-SIG-GATEBACKTEST-DRIFT: the real gate (_is_conviction_buy) soft-passes when
        # ml_weight == 0.0 (model trained but AUC < 0.50, so signal-engine assigned it zero
        # fusion weight — "ML had no say, don't penalize on it"). This replica was missing
        # that carve-out and always failed on a threshold miss regardless of ml_weight,
        # scoring some historically-soft-passed signals as ML-gate failures.
        ml_prob = r.get("ml_probability")
        ml_weight = float(r.get("ml_weight") or 0.0)
        if ml_prob is not None and ml_weight != 0.0:
            regime = r.get("market_regime", "unknown")
            thresh = _REGIME_ML_THRESH.get(regime, 0.70)
            if float(ml_prob) <= thresh:
                failed.append("ML")

        # Disqualifiers — always hard
        if r.get("rsi_divergence") == "bearish":
            failed.append("RSI_DIV")
        if r.get("stoch_rsi_overbought"):
            failed.append("STOCH_OB")

        soft_kw = {"OBV", "ADX", "ML"}
        if new_macd_soft:
            soft_kw.add("MACD")
        soft_failed = [f for f in failed if f in soft_kw]
        hard_failed = [f for f in failed if f not in soft_kw]

        if not failed:
            tier = "full"
        elif not hard_failed and len(soft_failed) == 1:
            tier = "near"
        else:
            tier = "failed"
        return tier in ("full", "near"), tier, failed

    # Build price lookup
    stock_ids = list({r.stock_id for r in rows})
    price_rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(
            Price.stock_id.in_(stock_ids),
            Price.timeframe == TimeFrame.D1,
            Price.ts >= since - timedelta(days=10),
        )
        .order_by(Price.stock_id, Price.ts)
    ).all()

    from collections import defaultdict
    prices_by_stock: dict = defaultdict(list)
    for p in price_rows:
        d = p.ts.date() if hasattr(p.ts, "date") else p.ts
        prices_by_stock[p.stock_id].append((d, float(p.close)))

    def _price_at(stock_id: int, target) -> float | None:
        candidates = prices_by_stock.get(stock_id, [])
        future = [(abs((d - target).days), c) for d, c in candidates if d >= target]
        return min(future, key=lambda x: x[0])[1] if future else None

    # Evaluate each signal under old and new gates
    records = []
    for row in rows:
        r = row.reasons or {}
        sig_date = row.ts.date() if hasattr(row.ts, "date") else row.ts
        exit_date = sig_date + timedelta(days=hold_days)
        horizon = row.horizon.value if hasattr(row.horizon, "value") else str(row.horizon)

        entry = _price_at(row.stock_id, sig_date)
        exit_ = _price_at(row.stock_id, exit_date)
        ret = ((exit_ - entry) / entry) if (entry and exit_ and entry > 0) else None

        old_pass, old_tier, old_failed = _apply_gate(r, horizon, new_macd_cond=False, new_macd_soft=False, new_growth_rsi=False)
        new_pass, new_tier, new_failed = _apply_gate(r, horizon, new_macd_cond=True,  new_macd_soft=True,  new_growth_rsi=True)

        # Attribute what change caused the unblock
        change_reasons: list[str] = []
        if not old_pass and new_pass:
            macd_hist = float(r.get("macd_hist") or 0)
            macd_rising = bool(r.get("macd_rising"))
            macd_cross = bool(r.get("macd_zero_cross_up"))
            old_macd_ok = (macd_hist > 0 and macd_rising) or macd_cross
            new_macd_ok = macd_hist > 0 or macd_rising or macd_cross
            if not old_macd_ok and new_macd_ok:
                change_reasons.append("macd_condition_relaxed")
            elif "MACD" in old_failed and "MACD" not in new_failed:
                change_reasons.append("macd_soft_reclassified")
            rsi = r.get("rsi")
            if horizon == "GROWTH" and rsi is not None and 50.0 <= float(rsi) < 55.0:
                change_reasons.append("growth_rsi_50_54")

        records.append({
            "symbol": row.symbol,
            "signal_date": sig_date.isoformat(),
            "old_pass": old_pass, "old_tier": old_tier, "old_failed": old_failed,
            "new_pass": new_pass, "new_tier": new_tier, "new_failed": new_failed,
            "ret_pct": round(ret * 100, 2) if ret is not None else None,
            "win": (ret > 0) if ret is not None else None,
            "change_reasons": change_reasons,
        })

    def _stats(items: list) -> dict:
        with_ret = [x for x in items if x["ret_pct"] is not None]
        wins = [x for x in with_ret if x["win"]]
        return {
            "count": len(items),
            "count_with_returns": len(with_ret),
            "win_rate_pct": round(len(wins) / len(with_ret) * 100, 1) if with_ret else None,
            "avg_return_pct": round(sum(x["ret_pct"] for x in with_ret) / len(with_ret), 2) if with_ret else None,
        }

    old_pass_set = [x for x in records if x["old_pass"]]
    new_pass_set = [x for x in records if x["new_pass"]]
    newly_pass   = [x for x in records if x["new_pass"] and not x["old_pass"]]
    always_fail  = [x for x in records if not x["new_pass"]]

    by_change = {}
    for reason in ("macd_condition_relaxed", "macd_soft_reclassified", "growth_rsi_50_54"):
        grp = [x for x in newly_pass if reason in x["change_reasons"]]
        by_change[reason] = _stats(grp)

    sample = sorted(
        [x for x in newly_pass if x["ret_pct"] is not None],
        key=lambda x: x["ret_pct"], reverse=True,
    )[:20]

    result = {
        "lookback_days": lookback_days,
        "horizon": style,
        "hold_days": hold_days,
        "n_signals_total": len(records),
        "old_gate": _stats(old_pass_set),
        "new_gate": _stats(new_pass_set),
        "newly_unblocked": {
            **_stats(newly_pass),
            "by_change": by_change,
            "note": "win_rate_pct > 50% means newly unblocked signals go up more often than not — change is beneficial",
        },
        "still_blocked": _stats(always_fail),
        "sample_newly_unblocked": sample,
    }
    _cache_set(cache_key, result, ttl=3600)
    return result


# T232-OC5: /{symbol} MUST be registered after every other static-path route in this router.
# FastAPI matches routes in registration order, and a bare /{symbol} catch-all placed earlier
# swallows any later static route with the same prefix depth (e.g. /signals/gate_backtest was
# being treated as symbol="gate_backtest" and 500ing on an invalid stock lookup — completely
# unreachable since it was added). Moved here, after every other GET, so this can never recur
# by accident; if you add a new static GET route to this router, add it ABOVE this line.
@router.get("/{symbol}")
def signal_for(
    symbol: str,
    persist: bool = False,
    live: bool = Query(True, description="False = return latest persisted DB signal (matches signal filter). True = compute fresh (may differ from DB)."),
    style: str | None = Query(None, description="Trading style: SHORT, SWING, LONG, GROWTH. Returns all if omitted."),
    session: Session = Depends(get_session),
):
    """Return signal(s) for a symbol.

    live=False (default on detail page): reads latest stored DB signal — consistent with signal filter.
    live=True + persist=True: recomputes fresh and overwrites DB — used by the Refresh button.
    """
    stock = session.query(Stock).filter(Stock.symbol == symbol).one_or_none()

    if not live and not persist:
        # DB-first path: return stored signals — matches Signal Filter exactly.
        if not stock:
            raise HTTPException(404, f"Stock {symbol} not found")
        all_styles = ["SHORT", "SWING", "LONG", "GROWTH"]
        stored: dict[str, dict] = {}
        for s_key in all_styles:
            d = _stored_signal_for_style(session, stock.id, s_key)
            if d:
                stored[s_key] = d
        if stored:
            # T223/T232-OC5: enrich with calibrated win rate, keyed by (horizon, direction, market)
            _cal_map = _get_confidence_calibration(session)
            if _cal_map:
                for s_key_h, s_data in stored.items():
                    _sig_dir = s_data.get("signal")
                    if _sig_dir not in ("BUY", "SELL"):
                        continue  # calibration only meaningful for directional signals
                    _cwr = _calibrated_win_rate(
                        s_data.get("confidence", 0.0), _cal_map,
                        horizon=s_key_h, direction=_sig_dir,
                        market=stock.market.value if hasattr(stock.market, "value") else stock.market,
                    )
                    if _cwr is not None:
                        if s_data.get("reasons") is None:
                            s_data["reasons"] = {}
                        s_data["reasons"]["calibrated_win_rate"] = _cwr[0]
                        s_data["reasons"]["calibrated_win_rate_count"] = _cwr[1]
            if style:
                s_key = style.upper()
                data = stored.get(s_key)
                if data:
                    return {"symbol": symbol, "source": "db", **data}
            else:
                return {"symbol": symbol, "source": "db", "signals": stored}
        # No stored signals for this stock yet — fall through to live generation
        # and auto-persist so the Signal Filter picks it up on the next query.
        persist = True

    # Live computation (fresh ML/TA — used on Refresh or first-time stock)
    try:
        all_sig = generate_all_signals(symbol)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    if persist and stock:
        # Apply catalyst adjustment (same logic as _bulk_persist) so manual Refresh doesn't
        # overwrite a catalyst-adjusted bullish_probability with the raw generator value.
        try:
            import httpx as _httpx_sf
            _ta_score_sf = 50.0
            if all_sig:
                _ta_score_sf = float((next(iter(all_sig.values())).reasons or {}).get("ta_score", 50.0))
            _cr_sf = _httpx_sf.get(
                f"{_settings.event_intelligence_url}/catalyst/{symbol}",
                params={"technical_score": _ta_score_sf},
                headers={"Authorization": f"Bearer {_service_token()}"},
                timeout=2.0,
            )
            if _cr_sf.status_code == 200:
                _cat_sf = _cr_sf.json()
                _ins_sf = _cat_sf.get("insider_score")
                _cong_sf = _cat_sf.get("congress_score")
                for _ai_sf in all_sig.values():
                    if _ai_sf.reasons is None:
                        _ai_sf.reasons = {}
                    if _cat_sf.get("catalyst_score") is not None:
                        _ai_sf.reasons["catalyst_score"] = round(_cat_sf["catalyst_score"], 1)
                    if _ins_sf is not None:
                        _ai_sf.reasons["insider_score"] = round(_ins_sf, 1)
                    if _cong_sf is not None:
                        _ai_sf.reasons["congress_score"] = round(_cong_sf, 1)
                    _adj_sf = 0.0
                    if _ins_sf is not None:
                        if _ins_sf > 60:    _adj_sf += 0.03
                        elif _ins_sf > 30:  _adj_sf += 0.015
                        elif _ins_sf < -30: _adj_sf -= 0.03
                        elif _ins_sf < -10: _adj_sf -= 0.015
                    if _cong_sf is not None:
                        if _cong_sf > 50:   _adj_sf += 0.02
                        elif _cong_sf > 25: _adj_sf += 0.01
                    if _adj_sf != 0.0 and _ai_sf.bullish_probability is not None:
                        _ai_sf.bullish_probability = round(
                            float(max(0.0, min(1.0, _ai_sf.bullish_probability + _adj_sf))), 4
                        )
                        _ai_sf.reasons["catalyst_prob_adj"] = round(_adj_sf, 3)
        except Exception:
            pass  # catalyst enrichment is best-effort; don't block the Refresh

        today = date.today()
        for ai in all_sig.values():
            horizon_enum = SignalHorizon(ai.horizon)
            # Guard against same-day duplicate: skip if an identical signal was already stored today.
            existing = session.execute(
                select(Signal.signal, Signal.ts)
                .where(Signal.stock_id == stock.id, Signal.horizon == horizon_enum)
                .order_by(Signal.ts.desc())
                .limit(1)
            ).one_or_none()
            if existing is not None and existing[0] == SignalType(ai.signal) and existing[1].date() == today:
                continue
            session.add(Signal(
                stock_id=stock.id,
                signal=SignalType(ai.signal),
                horizon=horizon_enum,
                confidence=ai.confidence,
                bullish_probability=ai.bullish_probability,
                reasons=ai.reasons,
            ))
        session.commit()

    # Inject stability_days into each signal's reasons dict
    if stock:
        for ai in all_sig.values():
            try:
                horiz = SignalHorizon(ai.horizon)
            except ValueError:
                continue
            ai.reasons["stability_days"] = _compute_stability(session, stock.id, horiz, ai.signal)

    # T223/T232-OC5: enrich live signals with calibrated win rate, keyed by (horizon, direction, market)
    _cal_map_live = _get_confidence_calibration(session)
    if _cal_map_live:
        for ai in all_sig.values():
            if ai.signal not in ("BUY", "SELL"):
                continue  # calibration only meaningful for directional signals
            _stock_mkt = None
            if stock is not None:
                _stock_mkt = stock.market.value if hasattr(stock.market, "value") else stock.market
            _cwr = _calibrated_win_rate(
                ai.confidence, _cal_map_live,
                horizon=ai.horizon, direction=ai.signal, market=_stock_mkt,
            )
            if _cwr is not None:
                if ai.reasons is None:
                    ai.reasons = {}
                ai.reasons["calibrated_win_rate"] = _cwr[0]
                ai.reasons["calibrated_win_rate_count"] = _cwr[1]

    if style:
        style_key = style.upper()
        ai = all_sig.get(style_key) or all_sig["SWING"]
        return {"symbol": symbol, "source": "live", **asdict(ai)}

    return {
        "symbol": symbol,
        "source": "live",
        "signals": {k: asdict(v) for k, v in all_sig.items()},
    }
