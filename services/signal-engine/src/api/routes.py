"""Signal-engine hot-path routes: live signal reads/writes.

T233-ARCH-INSERVICE-SPLITS: extracted from a 6,289-line/35-route routes.py, split into this
file (hot-path signal reads/writes — the routes real trading traffic actually depends on),
calibration.py (self-tuning/calibration mechanisms), and outcomes.py (analytics/backtest/
outcome-evaluation). See shared.py's own module docstring for what lives there and why.
Verbatim extraction — no logic changes; a bug found here was already present before the split.
"""
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from common.jwt_auth import get_current_username
from db import Price, Signal, SignalHorizon, Stock, TimeFrame, get_session

from ..generators import generate_signal, generate_all_signals
from .signals_shared import (
    _calibrated_win_rate, _compute_stability, _get_confidence_calibration,
    _get_redis, _service_token, _settings, _stored_signal_for_style, log,
)

router = APIRouter(prefix="/signals", tags=["signals"])

@router.get("")
def all_latest_signals(
    style: str | None = Query(None, description="Filter by trading style: SHORT, SWING, LONG"),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
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
    _: str = Depends(get_current_username),
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
                    # T237-EI1: congress_score is actually clamped to [-100, 100] in
                    # compute_congress_score (congress.py), NOT non-negative as this comment used
                    # to claim — heavy congressional net selling legitimately produces a negative
                    # score. Only positive thresholds were checked here, silently dropping the
                    # bearish signal for exactly the stocks where lawmakers are net-selling.
                    _cat_adj = 0.0
                    if _insider_s is not None:
                        if _insider_s > 60:    _cat_adj += 0.03   # strong cluster of insider buys
                        elif _insider_s > 30:  _cat_adj += 0.015
                        elif _insider_s < -30: _cat_adj -= 0.03   # heavy insider selling
                        elif _insider_s < -10: _cat_adj -= 0.015
                    if _congress_s is not None:
                        if _congress_s > 50:    _cat_adj += 0.02   # meaningful congress net buying
                        elif _congress_s > 25:  _cat_adj += 0.01
                        elif _congress_s < -50: _cat_adj -= 0.02   # meaningful congress net selling
                        elif _congress_s < -25: _cat_adj -= 0.01
                    if _cat_adj != 0.0 and _ai.bullish_probability is not None:
                        import numpy as _np_cat
                        _ai.bullish_probability = round(
                            float(_np_cat.clip(_ai.bullish_probability + _cat_adj, 0.0, 1.0)), 4
                        )
                        _ai.reasons["catalyst_prob_adj"] = round(_cat_adj, 3)
                        # CRIT-5: re-evaluate signal direction after catalyst nudge so stored
                        # signal type stays consistent with the adjusted probability.
                        try:
                            from ..generators.signals import (
                                _STYLE_PROFILES as _SP_cat,
                                _get_dynamic_buy_threshold as _get_bt_cat,
                                _get_dynamic_sell_threshold as _get_st_cat,
                            )
                            _hor_key = _ai.horizon
                            if _hor_key in _SP_cat:
                                # T237-SIG2: was min(_bt_vals.values()) — the LOOSEST of all 4
                                # regime buy-threshold tiers, regardless of the actual current
                                # regime. A signal generated during a real bear regime (e.g.
                                # SWING buy_threshold=0.68) only needed to clear the unknown-tier
                                # 0.62 to get catalyst-upgraded to BUY — exactly backwards during
                                # the regime that should be most conservative. Use the same
                                # regime-aware threshold functions _decide_style() itself uses.
                                _reg_cat = _ai.reasons.get("market_regime") if _ai.reasons else None
                                _reg_cat = _reg_cat if _reg_cat in ("bull", "high_vol", "bear", "unknown") else "unknown"
                                _dyn_bt = _get_bt_cat(_hor_key, _reg_cat)
                                _min_bt = _dyn_bt if _dyn_bt is not None else _SP_cat[_hor_key]["buy_threshold"][_reg_cat]
                                _dyn_st = _get_st_cat(_hor_key)
                                _sell_t = _dyn_st if _dyn_st is not None else 0.35
                                if _ai.bullish_probability >= _min_bt and _ai.signal == "HOLD":
                                    _ai.signal = "BUY"
                                    _ai.reasons["catalyst_upgraded_signal"] = True
                                elif _ai.bullish_probability <= _sell_t and _ai.signal in ("BUY", "HOLD"):
                                    _ai.signal = "SELL"
                                    _ai.reasons["catalyst_downgraded_signal"] = True
                        except Exception:
                            pass
                        # AUD232-013: .confidence is derived once at generation time from the
                        # pre-nudge fused probability (round(abs(fused-0.5)*200, 2) in signals.py's
                        # _apply_style_signal) and was never recomputed here — so a catalyst nudge
                        # that flips HOLD->BUY/SELL above left the PERSISTED confidence describing
                        # the OLD, pre-nudge probability. Since /signals/{symbol}'s calibrated
                        # win-rate lookup buckets by this same confidence, a signal whose direction
                        # was just flipped by this nudge could show a win-rate annotation computed
                        # for a different probability than the one actually driving the signal.
                        _ai.confidence = round(abs(_ai.bullish_probability - 0.5) * 200, 2)

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
                        except Exception as _rdiv_exc:
                            # Never block signal generation on research calls — but log so a
                            # research-engine outage is distinguishable from "no divergence found".
                            log.debug("divergence_check.failed", symbol=symbol, error=str(_rdiv_exc))
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
                    # T237-EI1: see the identical fix + explanation in signal_for()'s live-computation
                    # path above — congress_score can be genuinely negative, not clamped to >=0.
                    _adj_sf = 0.0
                    if _ins_sf is not None:
                        if _ins_sf > 60:    _adj_sf += 0.03
                        elif _ins_sf > 30:  _adj_sf += 0.015
                        elif _ins_sf < -30: _adj_sf -= 0.03
                        elif _ins_sf < -10: _adj_sf -= 0.015
                    if _cong_sf is not None:
                        if _cong_sf > 50:    _adj_sf += 0.02
                        elif _cong_sf > 25:  _adj_sf += 0.01
                        elif _cong_sf < -50: _adj_sf -= 0.02
                        elif _cong_sf < -25: _adj_sf -= 0.01
                    if _adj_sf != 0.0 and _ai_sf.bullish_probability is not None:
                        _ai_sf.bullish_probability = round(
                            float(max(0.0, min(1.0, _ai_sf.bullish_probability + _adj_sf))), 4
                        )
                        _ai_sf.reasons["catalyst_prob_adj"] = round(_adj_sf, 3)
                        # T237-SIG3: CRIT-5 re-evaluates the signal label after a catalyst nudge
                        # in _bulk_persist() (the scheduled path), but this manual-refresh path
                        # applied the same probability adjustment and never re-derived the label
                        # — a signal whose nudged bullish_probability crossed the buy/sell
                        # threshold was stored and returned with its stale, pre-nudge label.
                        try:
                            from ..generators.signals import (
                                _STYLE_PROFILES as _SP_sf,
                                _get_dynamic_buy_threshold as _get_bt_sf,
                                _get_dynamic_sell_threshold as _get_st_sf,
                            )
                            _hor_key_sf = _ai_sf.horizon
                            if _hor_key_sf in _SP_sf:
                                _reg_sf = _ai_sf.reasons.get("market_regime") if _ai_sf.reasons else None
                                _reg_sf = _reg_sf if _reg_sf in ("bull", "high_vol", "bear", "unknown") else "unknown"
                                _dyn_bt_sf = _get_bt_sf(_hor_key_sf, _reg_sf)
                                _min_bt_sf = _dyn_bt_sf if _dyn_bt_sf is not None else _SP_sf[_hor_key_sf]["buy_threshold"][_reg_sf]
                                _dyn_st_sf = _get_st_sf(_hor_key_sf)
                                _sell_t_sf = _dyn_st_sf if _dyn_st_sf is not None else 0.35
                                if _ai_sf.bullish_probability >= _min_bt_sf and _ai_sf.signal == "HOLD":
                                    _ai_sf.signal = "BUY"
                                    _ai_sf.reasons["catalyst_upgraded_signal"] = True
                                elif _ai_sf.bullish_probability <= _sell_t_sf and _ai_sf.signal in ("BUY", "HOLD"):
                                    _ai_sf.signal = "SELL"
                                    _ai_sf.reasons["catalyst_downgraded_signal"] = True
                        except Exception:
                            pass
                        # AUD232-013: same fix as _bulk_persist() above — recompute confidence
                        # from the nudged bullish_probability so the persisted/returned confidence
                        # matches the signal actually shown, not the stale pre-nudge value.
                        _ai_sf.confidence = round(abs(_ai_sf.bullish_probability - 0.5) * 200, 2)
        except Exception:
            pass  # catalyst enrichment is best-effort; don't block the Refresh

        # T247-SIGNALENGINE-GETPATH-UPSERT-CONFLICT: the previous same-day guard (DI-1) only
        # skipped the insert when the newly-computed signal was IDENTICAL to today's stored
        # value — a plain session.add(Signal(...)) with no ON CONFLICT handling was still used
        # for every other case. If today's stored signal (e.g. from a scheduled _bulk_persist
        # run) differs from the freshly recomputed one — a real price move, or this same
        # function's own catalyst-nudge re-evaluation flipping HOLD->BUY/SELL a few lines above
        # — the insert violates the real unique index uq_signals_stock_horizon_day
        # (stock_id, horizon, date_trunc('day', ts)), raising an unhandled IntegrityError that
        # 500s the whole request and rolls back every horizon's signal in this same commit, not
        # just the one that changed. Use the same INSERT ... ON CONFLICT DO UPDATE upsert
        # _bulk_persist() already uses (CAST() avoids the SQLAlchemy text() ::type binding
        # ambiguity — BUG-6) so this path can never violate the index regardless of whether
        # today's stored value matches.
        for ai in all_sig.values():
            session.execute(
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
