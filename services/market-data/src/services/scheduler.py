"""Market-aware scheduler — refreshes prices, rankings, signals, and ML models
around trading hours for US (NYSE/NASDAQ) and HK (HKEX) markets.

Schedule overview
─────────────────
Three phases per market day, plus a weekly deep-clean:

  Open burst   — every 5 min for 20 min around the open.  Catches gap opens,
                 early momentum, and first-bar signal updates.

  Regular hrs  — every 10 min through the session.  Prices, rankings,22
                 momentum, and signals are all pure local math (TA + XGBoost,
                 no external API cost), so 10-min cadence is safe and free.

  Close burst  — every 5 min for 45 min around the close.  Ensures the final
                 bar is captured as it settles, and signals reflect end-of-day
                 momentum.

  Post-close   — one shot after the final bar is confirmed; also triggers the
                 nightly ML retrain so tomorrow's signals use fresh weights.

  Weekly full refresh (Sunday 14:00 PST) — force re-ingests 3 years of daily
                 bars for all active stocks before the HK Monday open.  Clears
                 any yfinance data drift that accumulates across the week.
                 After ingestion + signals, triggers one Optuna tune_all run
                 (60 trials per symbol, ~2–4 h) so Monday's signals use the
                 best per-symbol hyperparams.

Detailed times (all times local to the market timezone; DST handled automatically)
──────────────────────────────────────────────────────────────────────────────────

  US (America/New_York):
    09:25 09:30 09:35 09:40 09:45           open burst   (every 5 min)
    10:00 10:10 10:20 10:30 10:40 10:50
    11:00 11:10 11:20 11:30 11:40 11:50
    12:00 12:10 12:20 12:30 12:40 12:50
    13:00 13:10 13:20 13:30 13:40 13:50
    14:00 14:10 14:20 14:30 14:40 14:50
    15:00                                   regular hrs  (every 10 min)
    15:30 15:35 15:40 15:45 15:50 15:55
    16:00 16:05 16:10 16:15                 close burst  (every 5 min)
    16:30                                   post-close   (+ ML retrain)

  HK (Asia/Hong_Kong, UTC+8, no DST):
    09:25 09:30 09:35 09:40 09:45           open burst   (every 5 min)
    10:00 10:10 10:20 10:30 10:40 10:50
    11:00 11:10 11:20 11:30 11:40 11:50
    12:00 12:10 12:20 12:30 12:40 12:50
    13:00 13:10 13:20 13:30 13:40 13:50
    14:00 14:10 14:20 14:30 14:40 14:50
    15:00                                   regular hrs  (every 10 min)
    15:30 15:35 15:40 15:45 15:50 15:55
    16:00 16:05 16:10 16:15                 close burst  (every 5 min)
    16:30                                   post-close   (+ ML retrain)

  Weekly (America/Los_Angeles):
    Sunday 14:00                            full force re-ingest all stocks
    Sunday ~14:10–14:20 (after ingest)      Optuna tune_all (~2–4 h, background)
    Sunday ~14:10–14:20 (after ingest)      calibrate_ta_weights (SA-5, ~30s)

yfinance rate-limit notes
─────────────────────────
  • All ingests use yf.download(symbols_list) — one batch call regardless of
    stock count, so the effective call rate stays well under 500/day.
  • The weekly full refresh is the only job that passes force=True (deletes all
    rows then re-fetches 3 years).  Daily jobs fetch only the latest bars.
"""
from __future__ import annotations

import httpx
import json
import redis as redis_lib
import time
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from common.config import get_settings
from common.logging import get_logger
from db import AlertCondition, PaperPortfolio, PaperTrade, Price, PriceAlert, Ranking, Signal, SignalAlert, SessionLocal, SignalHorizon, SignalType, Stock, TimeFrame, User, Watchlist, WatchlistItem


from .ingestion import ingest_universe
from .email_service import send_morning_digest_email, send_price_alert_email, send_signal_alert_email, send_paper_portfolio_digest_email, send_broker_reauth_email, send_webhook_notification
from .paper_trading_engine import get_last_regime, paper_trading_step, snapshot_equity_curve, ensure_portfolio_exists
from ..api.routes import refresh_live_price_cache

log = get_logger("scheduler")
_settings = get_settings()
_scheduler: BackgroundScheduler | None = None
_redis: redis_lib.Redis | None = None

# Cache a service-to-service JWT so scheduler can call auth-protected internal endpoints.
_service_token_cache: str | None = None
_service_token_exp: float = 0.0  # epoch seconds when the cached token expires


def _service_token() -> str:
    """Return a JWT for scheduler → internal service calls. Refreshes 7 days before expiry."""
    import time as _time_mod
    global _service_token_cache, _service_token_exp
    if _service_token_cache and _time_mod.time() < _service_token_exp - 7 * 86400:
        return _service_token_cache
    try:
        from jose import jwt as _jwt
        import uuid
        exp = datetime.now(timezone.utc) + timedelta(days=365)
        payload = {
            "sub": "scheduler",
            "jti": str(uuid.uuid4()),
            "exp": exp,
        }
        _service_token_cache = _jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")
        _service_token_exp = exp.timestamp()
        return _service_token_cache
    except Exception as exc:
        log.error("scheduler.service_token_failed", error=str(exc))
        return ""


def _get_redis() -> redis_lib.Redis:
    global _redis
    if _redis is None:
        _redis = redis_lib.Redis.from_url(_settings.redis_url, decode_responses=True)
    return _redis


def _record_job_status(job_name: str, status: str, duration_s: float, error: str | None = None) -> None:
    """Write job completion status to Redis for the admin health monitor (TTL 14 days)."""
    try:
        _get_redis().setex(
            f"scheduler:job:{job_name}",
            86400 * 14,
            json.dumps({
                "job": job_name,
                "status": status,
                "last_run": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(duration_s, 1),
                "error": error,
            }),
        )
    except Exception:
        pass


def _store_conviction(symbol: str, style: str, sent: bool, passed: list, failed: list, signal: str, sent_at: str | None = None, conviction_tier: str | None = None) -> None:
    try:
        r = _get_redis()
        now = datetime.now(timezone.utc).isoformat()
        # Preserve existing sent_at if not explicitly provided (stable BUY refresh path)
        if sent_at is None and sent:
            try:
                existing = r.get(f"conv_gate:{symbol}:{style}")
                if existing:
                    sent_at = json.loads(existing).get("sent_at")
            except Exception:
                pass
        # Derive conviction_tier from passed/failed if not explicitly provided
        if conviction_tier is None:
            _SOFT = ("OBV", "ADX", "ML probability", "MACD")
            soft_f = [f for f in failed if any(kw in f for kw in _SOFT)]
            hard_f = [f for f in failed if f not in soft_f]
            if len(failed) == 0:
                conviction_tier = "full"
            elif len(hard_f) == 0 and len(soft_f) == 1:
                conviction_tier = "near"
            else:
                conviction_tier = "failed"
        r.setex(
            f"conv_gate:{symbol}:{style}",
            86400,  # 1-day TTL — expires with the trading day so stale conviction data doesn't persist
            json.dumps({
                "sent": sent,
                "passed": passed,
                "failed": failed,
                "signal": signal,
                "ts": now,
                "sent_at": sent_at,
                "conviction_tier": conviction_tier,  # "full" | "near" | "failed"
                "gate_score": f"{len(passed)}/{len(passed) + len(failed)}",
            }),
        )
    except Exception:
        pass


# ── HK Public Holiday Calendar (HKEX market closure dates) ──────────────────
# Source: HKEX official holiday list. Extend each year before January.
# Format: frozenset of (year, month, day) tuples.
_HK_HOLIDAYS: frozenset[tuple[int, int, int]] = frozenset([
    # 2025
    (2025, 1, 1),   # New Year's Day
    (2025, 1, 29),  # Lunar New Year's Eve
    (2025, 1, 30),  # Lunar New Year Day 1
    (2025, 1, 31),  # Lunar New Year Day 2
    (2025, 2, 3),   # Lunar New Year Day 4 (make-up, day after Sat)
    (2025, 4, 4),   # Ching Ming Festival
    (2025, 4, 18),  # Good Friday
    (2025, 4, 21),  # Easter Monday
    (2025, 5, 1),   # Labour Day
    (2025, 5, 5),   # Buddha's Birthday
    (2025, 6, 2),   # Tuen Ng Festival
    (2025, 7, 1),   # HKSAR Establishment Day
    (2025, 10, 1),  # National Day
    (2025, 10, 7),  # Chung Yeung Festival
    (2025, 12, 25), # Christmas Day
    (2025, 12, 26), # Boxing Day
    # 2026
    (2026, 1, 1),   # New Year's Day
    (2026, 2, 17),  # Lunar New Year Day 1
    (2026, 2, 18),  # Lunar New Year Day 2
    (2026, 2, 19),  # Lunar New Year Day 3
    (2026, 2, 20),  # Lunar New Year Day 4 (make-up)
    (2026, 4, 3),   # Ching Ming Festival + Good Friday (both fall on Apr 3, 2026)
    (2026, 4, 6),   # Easter Monday
    (2026, 5, 1),   # Labour Day
    (2026, 5, 25),  # Buddha's Birthday
    (2026, 6, 19),  # Tuen Ng Festival
    (2026, 7, 1),   # HKSAR Establishment Day
    (2026, 10, 1),  # National Day
    (2026, 10, 26), # Chung Yeung Festival
    (2026, 12, 25), # Christmas Day
    (2026, 12, 28), # Boxing Day observed (Mon after Sat+Sun Christmas)
])


def _is_hk_holiday(dt: datetime | None = None) -> bool:
    """Return True if today is a HKEX public holiday (market is closed)."""
    d = (dt or datetime.now(timezone.utc)).astimezone(
        __import__("zoneinfo").ZoneInfo("Asia/Hong_Kong")
    )
    return (d.year, d.month, d.day) in _HK_HOLIDAYS


# ── NYSE Public Holiday Calendar ──────────────────────────────────────────────
# Source: NYSE official holiday schedule. Extend each year before January.
_NYSE_HOLIDAYS: frozenset[tuple[int, int, int]] = frozenset([
    # 2025
    (2025, 1, 1),   # New Year's Day
    (2025, 1, 20),  # MLK Day
    (2025, 2, 17),  # Presidents' Day
    (2025, 4, 18),  # Good Friday
    (2025, 5, 26),  # Memorial Day
    (2025, 6, 19),  # Juneteenth
    (2025, 7, 4),   # Independence Day
    (2025, 9, 1),   # Labor Day
    (2025, 11, 27), # Thanksgiving
    (2025, 12, 25), # Christmas
    # 2026
    (2026, 1, 1),   # New Year's Day
    (2026, 1, 19),  # MLK Day
    (2026, 2, 16),  # Presidents' Day
    (2026, 4, 3),   # Good Friday
    (2026, 5, 25),  # Memorial Day
    (2026, 6, 19),  # Juneteenth
    (2026, 7, 3),   # Independence Day observed (Jul 4 is Saturday)
    (2026, 9, 7),   # Labor Day
    (2026, 11, 26), # Thanksgiving
    (2026, 12, 25), # Christmas
])


def _is_us_trading_day(dt: datetime | None = None) -> bool:
    """Return True if today is a NYSE trading day (weekday and not a holiday)."""
    d = (dt or datetime.now(timezone.utc)).astimezone(
        __import__("zoneinfo").ZoneInfo("America/New_York")
    )
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return (d.year, d.month, d.day) not in _NYSE_HOLIDAYS


def _symbols_for(market: str) -> list[str]:
    """Return all active stock symbols for the given market ('US' or 'HK')."""
    with SessionLocal() as session:
        return list(
            session.execute(
                select(Stock.symbol).where(Stock.active.is_(True), Stock.market == market)
            ).scalars()
        )


_REDIS_REFRESH_FAILED_KEY = "market:refresh_failed"

def _post(url: str, **kwargs) -> None:
    """Fire-and-forget POST to an internal service.

    DP-4: Retries up to 3 times with exponential backoff (5s / 15s / 45s).
    After all retries fail, logs at ERROR.
    (BUG-8: no longer sets market:refresh_failed — stale data is handled per-symbol via price freshness check.)
    """
    delays = [3, 8, 20]  # kept short — scheduler thread pool has limited slots
    # Inject service-to-service auth token so endpoints protected by get_current_username work.
    headers = kwargs.pop("headers", {})
    tok = _service_token()
    if tok:
        headers = {"Authorization": f"Bearer {tok}", **headers}
    # Propagate a correlation ID so all downstream service logs can be joined.
    if "X-Request-ID" not in headers:
        import uuid as _uuid
        headers["X-Request-ID"] = str(_uuid.uuid4())
    kwargs["headers"] = headers
    last_exc: Exception | None = None
    for attempt, delay in enumerate(delays, start=1):
        try:
            with httpx.Client(timeout=15) as client:
                client.post(url, **kwargs)
            return  # success
        except Exception as exc:
            last_exc = exc
            if attempt < len(delays):
                log.warning("scheduler.http_retry", url=url, attempt=attempt, error=str(exc))
                time.sleep(delay)

    log.error("scheduler.http_failed", url=url, attempts=len(delays), error=str(last_exc))


_AUTO_RESEARCH_TOP_N = 5        # max BUY signals to trigger per refresh cycle
_AUTO_RESEARCH_MIN_CONF = 65.0  # only trigger for signals with confidence >= 65%


def _auto_trigger_research(market: str) -> None:
    """Fire background research trigger for the top-N BUY signals that lack a fresh report.

    The research engine's /trigger endpoint has a 6-hour cooldown — it skips
    automatically if a report was generated recently, so it is safe to call on
    every refresh cycle without risk of repeated expensive AI requests.
    """
    with SessionLocal() as session:
        rows = session.execute(
            select(Stock.symbol, Signal.confidence)
            .join(Signal, Signal.stock_id == Stock.id)
            .where(
                Stock.market == market,
                Signal.signal == "BUY",
                Signal.confidence >= _AUTO_RESEARCH_MIN_CONF,
            )
            .order_by(Signal.confidence.desc())
            .limit(_AUTO_RESEARCH_TOP_N)
        ).all()

    if not rows:
        return

    triggered = []
    for sym, conf in rows:
        try:
            with httpx.Client(timeout=5) as client:
                r = client.post(
                    f"{_settings.research_engine_url}/research/{sym}/trigger",
                )
            status = r.json().get("status", "?") if r.status_code == 202 else r.status_code
        except Exception as exc:
            status = str(exc)
        triggered.append({"symbol": sym, "conf": round(conf, 1), "status": status})

    log.info("scheduler.auto_research_triggered", market=market, symbols=triggered)


def _refresh_market(market: str, *, post_close: bool = False) -> None:
    """Run one full refresh cycle for the given market.

    Steps (in order):
      1. ingest_universe  — fetch latest daily OHLCV bars from yfinance → DB
      2. /rankings/refresh — ranking-engine recalculates K-Scores for the market
      3. /signals/refresh  — signal-engine regenerates buy/sell signals
      4. /ml/train_all     — (post_close only) retrain ML models on the day's data
      5. check_signal_alerts / check_technical_alerts — fire any triggered alerts

    Called by every scheduled job (open burst, regular, close burst, post-close).
    post_close=True is only set by the 16:30 job after the final bar has settled.
    """
    if market == "HK" and _is_hk_holiday():
        log.info("scheduler.skip", market="HK", reason="hk_public_holiday")
        return
    if market == "US" and not _is_us_trading_day():
        log.info("scheduler.skip", market="US", reason="nyse_holiday")
        return

    symbols = _symbols_for(market)
    if not symbols:
        log.info("scheduler.skip", market=market, reason="no_symbols")
        return

    log.info("scheduler.refresh_start", market=market, count=len(symbols), post_close=post_close)
    _t0 = time.monotonic()
    _job_key = f"{market.lower()}_post_close" if post_close else f"{market.lower()}_refresh"

    # Stage 1: Ingest — isolated so a yfinance blip doesn't kill alerts/paper trading
    _ingest_ok = True
    try:
        ingest_universe(symbols, "1d")
    except Exception as _ie:
        _ingest_ok = False
        log.error("scheduler.ingest_failed", market=market, error=str(_ie), exc_info=True)

    # TIER94: Keep sector ETF prices fresh for sector_rs ML features (active=False so not in symbols)
    if market == "US":
        _SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLY", "XLU", "XLI", "XLB", "XLC", "XLRE", "SPY"]
        try:
            ingest_universe(_SECTOR_ETFS, "1d")
        except Exception as _etf_exc:
            log.warning("scheduler.sector_etf_ingest_failed", error=str(_etf_exc))

    # Stage 2: Rankings + signals — runs even if ingest partially failed (uses last good bar)
    try:
        _post(f"{_settings.ranking_engine_url}/rankings/refresh", params={"market": market})
        _post(f"{_settings.signal_engine_url}/signals/refresh", params={"market": market})

        if post_close:
            _post(f"{_settings.ml_prediction_url}/ml/train_all_horizons")
            # Evaluate any BUY/SELL signals whose hold window has now expired.
            _post(f"{_settings.signal_engine_url}/signals/outcomes/evaluate")
            # Stale model guard: if tune_all hasn't run in >21 days, trigger it now.
            # Normally runs weekly on Sunday; this catches missed weeks (container restarts, errors).
            try:
                _tune_status_raw = _get_redis().get("scheduler:job:tune_all_sent")
                if _tune_status_raw:
                    _tune_last = json.loads(_tune_status_raw).get("last_run")
                    if _tune_last:
                        _days_stale = (datetime.now(timezone.utc) - datetime.fromisoformat(_tune_last)).days
                        if _days_stale > 21:
                            log.warning("scheduler.tune_all_stale_retrigger", days_since=_days_stale)
                            _post(f"{_settings.ml_prediction_url}/ml/tune_all")
                            _record_job_status("tune_all_sent", "ok", 0.0)
            except Exception as _ta_e:
                log.warning("scheduler.tune_all_age_check_failed", error=str(_ta_e))
    except Exception as _re:
        log.error("scheduler.rankings_signals_failed", market=market, error=str(_re), exc_info=True)

    # Stage 2.5: Auto-research — trigger background research for top BUY signals without fresh reports
    try:
        _auto_trigger_research(market)
    except Exception as _are:
        log.warning("scheduler.auto_research_failed", market=market, error=str(_are))

    # Stage 3: Alerts — always runs regardless of ingest or signal failures
    try:
        check_signal_alerts()
        check_technical_alerts()
    except Exception as _ae:
        log.error("scheduler.alerts_failed", error=str(_ae), exc_info=True)

    # Stage 4: Paper trading — runs for both US and HK markets
    if market in ("US", "HK") and _settings.enable_paper_trading:
        _pt0 = time.monotonic()
        try:
            _run_paper_trading_step(label="refresh_market")
            _record_job_status("paper_trading", "ok", time.monotonic() - _pt0)
        except Exception as _pte:
            log.error("scheduler.paper_trading_step_failed", error=str(_pte), exc_info=True)
            _record_job_status("paper_trading", "error", time.monotonic() - _pt0, str(_pte))
        if post_close:
            try:
                snapshot_equity_curve()
            except Exception as _sec:
                log.error("scheduler.snapshot_equity_curve_failed", error=str(_sec), exc_info=True)

    if _ingest_ok:
        _record_job_status(_job_key, "ok", time.monotonic() - _t0)
        try:
            _get_redis().delete(_REDIS_REFRESH_FAILED_KEY)
        except Exception:
            pass
    else:
        _record_job_status(_job_key, "error", time.monotonic() - _t0, "ingest_failed")
    log.info("scheduler.refresh_done", market=market, post_close=post_close, ingest_ok=_ingest_ok)


_BULLISH_TRANSITIONS = {
    ("SELL", "HOLD"), ("SELL", "WAIT"), ("SELL", "BUY"),
    ("WAIT", "HOLD"), ("WAIT", "BUY"),
    ("HOLD", "BUY"),
}
# Fired regardless of analyst rating — always send exit warnings.
# Covers deterioration from any bullish state (BUY, HOLD, WAIT) downward.
_BEARISH_TRANSITIONS = {
    ("BUY",  "HOLD"), ("BUY",  "WAIT"), ("BUY",  "SELL"),
    ("HOLD", "WAIT"), ("HOLD", "SELL"),
    ("WAIT", "SELL"),
}
_BULLISH_ANALYST = {"buy", "strong_buy", "strongbuy", "outperform"}

# Conviction thresholds — ALL five layers must clear for a BUY email to fire.
# Bearish/exit alerts bypass these — you always want the exit warning.
# SA-12: regime-adaptive thresholds — tighten the gate in bear/high-vol markets.
_REGIME_THRESHOLDS: dict[str, dict] = {
    "bull":     {"ml": 0.65, "confluence": 70, "confidence": 58, "tier": "bull"},
    "neutral":  {"ml": 0.70, "confluence": 75, "confidence": 60, "tier": "neutral"},
    "high_vol": {"ml": 0.78, "confluence": 82, "confidence": 68, "tier": "high_vol"},
    "bear":     {"ml": 0.78, "confluence": 82, "confidence": 68, "tier": "bear"},
    "unknown":  {"ml": 0.70, "confluence": 75, "confidence": 60, "tier": "neutral"},
}
# Legacy fallbacks (used outside conviction gate — keep for non-BUY path)
_MIN_CONFIDENCE  = 60.0
_MIN_CONFLUENCE  = 75


def _get_current_regime() -> str:
    """Read the cached market regime from Redis. Returns 'bull', 'bear', 'high_vol', or 'unknown'."""
    try:
        data = _get_redis().get("stockai:fear_greed")
        if data:
            d = json.loads(data)
            sp500 = d.get("sp500_regime", "unknown")
            fg = d.get("score")
            if sp500 == "bear":
                return "bear"
            if fg is not None and float(fg) < 30:
                return "high_vol"
            if sp500 == "bull":
                return "bull"
    except Exception:
        pass
    return "unknown"


def _confluence_score_full(
    signal: str,
    confidence: float,
    kscore: float,
    technical: float,
    momentum: float,
    rec_mean: float | None,
) -> int:
    """Mirror of frontend confluenceScoreFull() — 5-factor weighted score 0-100.

    Weights: AI signal×conf 30%, K-Score 25%, Analyst 20%, Technical 15%, Momentum 10%.
    rec_mean is the yfinance recommendationMean: 1.0 = Strong Buy, 5.0 = Sell.
    """
    ai_dir = 100 if signal == "BUY" else 50 if signal == "HOLD" else 25 if signal == "WAIT" else 0
    ai = ai_dir * confidence / 100
    analyst = max(0.0, min(100.0, (5.0 - rec_mean) / 4.0 * 100.0)) if rec_mean is not None else 50.0
    return round(
        ai         * 0.30 +
        kscore     * 0.25 +
        analyst    * 0.20 +
        technical  * 0.15 +
        momentum   * 0.10,
    )


def _is_conviction_buy(signal_data: dict, kscore: float | None = None, regime: str | None = None, rankings_api_ok: bool = True) -> tuple[bool, str, list[str], list[str]]:
    """Check all conviction layers for a BUY signal across all 4 framework layers.

    Returns (all_passed, conviction_tier, passed_layers, failed_layers).
    conviction_tier: "full" (all pass) | "near" (1 soft fail: OBV or ADX) | "failed" (hard fail).

    Layer 1 — Fundamental filter   : Analyst bullish (checked separately before call)
    Layer 2 — Conviction score     : K-Score ≥ 55
    Layer 3 — Timing trigger       : AI Signal = BUY (checked separately)
    Layer 4 — Technical confirmation:
        4a. Uptrend structure       — SMA50 > SMA200 AND price > SMA50
        4b. Entry timing            — RSI 45-65 AND Stoch RSI recovering from oversold
        4c. MACD momentum           — histogram positive+rising OR zero-line crossover
        4d. Volume confirms         — OBV bullish
        4e. Trend has real strength — ADX > 25 (signals reliable only in trending market)
    Layer 5 — ML confirms TA       : ML probability > regime-adaptive threshold, but only
                                     when the ML model actually contributed to the signal
                                     (ml_weight > 0). If the model was a no-op (AUC < 0.50)
                                     the gate soft-passes ML — consistent with signal generation.

    Regime is read from the stored signal's reasons dict (the regime at generation time),
    so conviction and signal generation always operate in the same market context.

    Disqualifiers (false-BUY flags from FEATURES.md — block even if all layers pass):
        • Bearish RSI divergence (price rising but momentum fading)
        • Stoch RSI overbought (RSI itself overextended)
    """
    reasons = signal_data.get("reasons") or {}

    # Use the regime stored in the signal's reasons (same context as signal generation).
    # Explicit `regime` override kept for backward-compat with callers that pass it.
    effective_regime = regime if regime is not None else reasons.get("market_regime", "unknown")
    passed: list[str] = []
    failed: list[str] = []

    # Layer 2 — K-Score conviction (≥ 55 = positive territory)
    if kscore is None:
        if rankings_api_ok:
            failed.append("K-Score unavailable (not yet ranked) — cannot verify conviction")
        else:
            failed.append("K-Score unavailable (rankings API down) — cannot verify conviction")
    elif kscore >= 55:
        passed.append(f"K-Score: {kscore:.0f} — conviction positive")
    else:
        failed.append(f"K-Score {kscore:.0f} below 55 — weak fundamental/momentum case")

    # Style-aware flag — used by Layer 4a and 4b
    style = signal_data.get("horizon", "SWING")

    # Layer 4a — Uptrend structure (GROWTH-aware; double-bottom neckline break = automatic pass)
    double_bottom_confirmed = (
        bool(reasons.get("double_bottom_neckline_broken")) and
        "double_bottom" in (reasons.get("active_patterns") or [])
    )
    if double_bottom_confirmed:
        passed.append("Double bottom neckline break confirmed — pattern reversal overrides golden-cross requirement")
    elif style == "GROWTH":
        # GROWTH exemption: SMA20>SMA50 momentum is sufficient; golden cross (SMA50>SMA200) not required
        if reasons.get("trend_above_sma50"):
            passed.append("GROWTH uptrend: price above SMA50 (golden-cross not required for GROWTH style)")
        else:
            failed.append("Uptrend structure not aligned (price below SMA50)")
    else:
        if reasons.get("sma50_above_sma200") and reasons.get("trend_above_sma50"):
            passed.append("Uptrend: SMA50 > SMA200, price > SMA50")
        else:
            failed.append("Uptrend structure not aligned (SMA50/SMA200/price)")

    # Layer 4b — Entry timing: RSI in healthy range
    # RSI upper bound extended to 72 (vs old 65): RSI 65-72 is healthy momentum,
    # not overextended. Stoch overbought (>80) is already caught by the disqualifier
    # below and is more precise than requiring stoch to recover from oversold — that
    # old requirement only fired 1-2 days after a stoch cross and silently blocked
    # the vast majority of valid BUY setups in normal trending conditions.
    rsi = reasons.get("rsi")
    stoch_k = float(reasons.get("stoch_rsi_k") or 50)
    if style == "GROWTH":
        rsi_ok = rsi is not None and 50.0 <= float(rsi) <= 85.0
        rsi_range_label = "50-85"
    else:
        rsi_ok = rsi is not None and 45.0 <= float(rsi) <= 72.0
        rsi_range_label = "45-72"
    if rsi_ok:
        passed.append(f"Entry timing: RSI {float(rsi):.0f}, within {rsi_range_label} for {style}")
    else:
        rsi_str = f"RSI {float(rsi):.0f} outside {rsi_range_label}" if rsi is not None else "RSI unavailable"
        failed.append(f"Entry timing: {rsi_str}")

    # Layer 4c — MACD momentum confirmed
    # Pass if histogram is positive, rising (momentum building even if still negative),
    # OR a zero-line crossover just occurred. Fail only when histogram is negative AND
    # falling — clearly deteriorating momentum. Treated as soft failure so a single
    # lagging-indicator miss does not veto a high-conviction TA+ML alignment.
    macd_hist = float(reasons.get("macd_hist") or 0)
    macd_rising = bool(reasons.get("macd_rising"))
    macd_zero_cross = bool(reasons.get("macd_zero_cross_up"))
    if macd_zero_cross:
        passed.append("MACD: zero-line crossover")
    elif macd_hist > 0:
        passed.append("MACD: histogram positive")
    elif macd_rising:
        passed.append("MACD: histogram rising (momentum building)")
    else:
        failed.append("MACD: momentum fading (histogram negative and falling)")

    # Layer 4d — Volume confirms direction
    if reasons.get("obv_trend_bullish"):
        passed.append("OBV: volume confirming price direction")
    else:
        failed.append("OBV: volume not confirming direction")

    # Layer 4e — ADX: trend has real strength (signals unreliable in choppy market)
    if reasons.get("adx_trending"):
        adx = reasons.get("adx", 0)
        passed.append(f"ADX {float(adx):.0f}: trend confirmed, signals reliable")
    else:
        adx = reasons.get("adx", 0)
        failed.append(f"ADX {float(adx):.0f} < 25: market choppy, signals unreliable")

    # Layer 5 — ML confirms TA (regime-adaptive threshold, AUC-aware — SA-12)
    # Single source of truth: use the regime AND ml_weight stored in the signal's reasons
    # so this gate is always consistent with how the signal was generated.
    thresholds  = _REGIME_THRESHOLDS.get(effective_regime, _REGIME_THRESHOLDS["unknown"])
    ml_threshold = thresholds["ml"]
    tier_label   = thresholds["tier"]
    ml_prob      = reasons.get("ml_probability")
    ml_weight    = float(reasons.get("ml_weight") or 0.0)
    if ml_prob is None:
        # No model at all — signal is TA-only (soft pass)
        passed.append(f"ML: no model trained yet — TA-only signal (soft pass) [{tier_label} regime]")
    elif ml_weight == 0.0:
        # Model trained but AUC < 0.50 (inverse/random) — signal-engine assigned zero weight;
        # gate mirrors that: model had no say, so don't penalise here (soft pass)
        passed.append(f"ML: model AUC below random — zero weight in fusion, gate skipped (soft pass) [{tier_label} regime]")
    elif float(ml_prob) > ml_threshold:
        passed.append(f"ML: {float(ml_prob) * 100:.0f}% bullish probability (threshold {ml_threshold * 100:.0f}% for {tier_label} regime)")
    else:
        failed.append(f"ML probability {float(ml_prob) * 100:.0f}% below {ml_threshold * 100:.0f}% threshold ({tier_label} regime)")

    # Disqualifiers — false-BUY flags that block regardless of layer scores
    if reasons.get("rsi_divergence") == "bearish":
        failed.append("Bearish RSI divergence: price rising but momentum fading — high false-BUY risk")
    if bool(reasons.get("stoch_rsi_overbought")):
        failed.append("Stoch RSI overbought: RSI itself overextended — pullback risk elevated")

    # CB-4: Near-conviction tier — allow 1 soft failure (OBV, ADX, ML, or MACD) to still send.
    # MACD is a lagging indicator; when all other layers (TA structure, RSI, ML, K-Score)
    # align bullish, a single MACD lag should not hard-block the alert.
    _SOFT_LAYER_KEYWORDS = ("OBV", "ADX", "ML probability", "MACD")
    soft_failed = [f for f in failed if any(kw in f for kw in _SOFT_LAYER_KEYWORDS)]
    hard_failed = [f for f in failed if f not in soft_failed]

    if len(failed) == 0:
        conviction_tier = "full"
    elif len(hard_failed) == 0 and len(soft_failed) == 1:
        conviction_tier = "near"
    else:
        conviction_tier = "failed"

    all_passed = conviction_tier in ("full", "near")
    return all_passed, conviction_tier, passed, failed


_STYLE_PARAMS: dict[str, dict] = {
    # SHORT: 1–5 day momentum trade — tight entries and stop, modest target
    "SHORT": {
        "entry1_pct":   0.995,   # -0.5%
        "entry2_pct":   0.985,   # -1.5%
        "breakout_pct": 1.010,   # +1%
        "stop_pct":     0.970,   # -3%
        "default_tp_pct": 1.05,  # +5% default target
        "entry1_label": "tight entry — short-term momentum play",
        "entry2_label": "secondary entry on minor intraday dip",
        "stop_label":   "tight stop — short-term trade invalidated on 3% breach",
        "tp_fallback":  "+5% quick target for short-term momentum trade",
        "horizon_note": "Short-term trade (1–5 days) — prioritise execution speed over perfect fill",
    },
    # SWING: 5–30 day swing trade — original balanced levels
    "SWING": {
        "entry1_pct":   0.985,   # -1.5%
        "entry2_pct":   0.965,   # -3.5%
        "breakout_pct": 1.020,   # +2%
        "stop_pct":     0.945,   # -5.5%
        "default_tp_pct": 1.12,  # +12% default target
        "entry1_label": "near-term support — scale in on weakness",
        "entry2_label": "secondary support — averaging down level",
        "stop_label":   "daily close below invalidates bullish swing setup",
        "tp_fallback":  "+12% from current, near next resistance",
        "horizon_note": "Swing trade (5–30 days) — hold through normal volatility",
    },
    # LONG: 30–365 day position trade — wider entries/stop, larger target
    "LONG": {
        "entry1_pct":   0.980,   # -2%
        "entry2_pct":   0.950,   # -5%
        "breakout_pct": 1.030,   # +3%
        "stop_pct":     0.900,   # -10%
        "default_tp_pct": 1.25,  # +25% default target
        "entry1_label": "initial position — build on weakness over days/weeks",
        "entry2_label": "add-to level — deeper pullback absorption zone",
        "stop_label":   "wide stop allows normal volatility; weekly close below invalidates thesis",
        "tp_fallback":  "+25% medium-term target (position trade)",
        "horizon_note": "Position trade (1–12 months) — manage around earnings; size for volatility",
    },
    # GROWTH: 30–90 day momentum/hypergrowth trade — wide stop, large target.
    # These stocks move big: NVDA, PLTR, IONQ, CRWD, NET, DDOG etc.
    # RSI 70–85 is momentum confirmation, NOT overbought for growth names.
    # No SMA50>SMA200 requirement — growth stocks consolidate below 200MA.
    "GROWTH": {
        "entry1_pct":   0.975,   # -2.5% — momentum pullback entry
        "entry2_pct":   0.940,   # -6.0% — deeper dip / scale-in level
        "breakout_pct": 1.035,   # +3.5% — breakout from base/consolidation
        "stop_pct":     0.880,   # -12%  — wide stop; growth stocks are volatile
        "default_tp_pct": 1.35,  # +35% default target (growth names move)
        "entry1_label": "momentum pullback — growth stocks dip before continuation",
        "entry2_label": "deeper pullback entry — scale in; strong hands accumulating",
        "stop_label":   "wide stop accommodates normal growth-stock volatility (12%)",
        "tp_fallback":  "+35% growth target — AI/tech/momentum name; hold for the move",
        "horizon_note": "Growth/momentum trade (30–90 days) — hold through volatility; trail stop on gains",
    },
}


def _get_symbol_market(session, symbol: str) -> str:
    """Return 'HK' or 'US' for the given symbol, defaulting to 'US' if not found."""
    try:
        row = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
        return (row.market or "US") if row else "US"
    except Exception:
        return "US"


def _build_game_plan(
    symbol: str,
    signal_data: dict,
    fundamentals: dict | None,
    style: str = "SWING",
) -> dict | None:
    """Build a rule-based game plan tailored to the user's trading style (SHORT/SWING/LONG)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="60d")
        if hist.empty:
            return None
        current_price = float(hist["Close"].iloc[-1])

        params = _STYLE_PARAMS.get(style.upper(), _STYLE_PARAMS["SWING"])
        reasons = signal_data.get("reasons", {})

        # Derive entry levels from technical structure
        above_sma50 = reasons.get("trend_above_sma50", False)
        sma50_above_sma200 = reasons.get("sma50_above_sma200", False)
        rsi = reasons.get("rsi")
        bb_pct_b = reasons.get("bb_pct_b")

        step = _round_step(current_price)
        entry1   = round(current_price * params["entry1_pct"]   / step) * step
        entry2   = round(current_price * params["entry2_pct"]   / step) * step
        breakout = round(current_price * params["breakout_pct"] / step) * step
        stop     = round(current_price * params["stop_pct"]     / step) * step

        # Take profit: analyst target (only if meaningfully above current) else style default
        target_price = (fundamentals or {}).get("target_price")
        min_tp_pct = params["default_tp_pct"]
        if target_price and float(target_price) > current_price * min(1.03, min_tp_pct * 0.8):
            take_profit = float(target_price)
            tp_note = "analyst mean price target"
        else:
            take_profit = round(current_price * min_tp_pct / step) * step
            tp_note = params["tp_fallback"]

        # Entry rationale: technical hints override style defaults
        if rsi is not None and float(rsi) < 45:
            e1_note = f"RSI {float(rsi):.0f} — oversold recovery zone"
            e2_note = "oversold extension — scale in on deeper dip"
        elif bb_pct_b is not None and float(bb_pct_b) < 0.4:
            e1_note = "lower Bollinger band support region"
            e2_note = "near lower band — strong mean-reversion level"
        elif above_sma50:
            e1_note = "pullback to SMA50 support zone"
            e2_note = "deeper pullback — maintain SMA50 as key level"
        else:
            e1_note = params["entry1_label"]
            e2_note = params["entry2_label"]

        breakout_note = "breakout above resistance on volume — momentum confirmed"
        stop_note = params["stop_label"]
        if sma50_above_sma200 and style.upper() != "SHORT":
            stop_note = "daily close below signals golden-cross breakdown"

        # Earnings catalyst / risk
        next_earnings = (fundamentals or {}).get("next_earnings_date")
        days_to_earnings = (fundamentals or {}).get("days_to_earnings")
        earnings_line = ""
        if next_earnings:
            d = days_to_earnings or "?"
            earnings_line = f"No earnings until {next_earnings} ({d}d) — clean runway" if (days_to_earnings or 99) > 10 else f"⚠ Earnings {next_earnings} ({d}d) — position size accordingly"

        catalysts = [c for c in [
            earnings_line or None,
            "Analyst consensus bullish — upgrade potential if momentum holds" if (fundamentals or {}).get("recommendation", "").lower() in ("buy", "strong_buy") else None,
            "SMA50 > SMA200 golden-cross structure intact" if sma50_above_sma200 else None,
            f"RSI {float(rsi):.0f} — recovering from oversold territory" if rsi is not None and float(rsi) < 50 else None,
            "MACD histogram rising — short-term momentum confirming" if reasons.get("macd_rising") else None,
            "OBV trend up — volume confirming price direction" if reasons.get("obv_trend_bullish") else None,
        ] if c is not None][:3]
        if not catalysts:
            catalysts = ["AI signal + analyst consensus aligned", "Technical structure improving", "Volume trend supporting move"]

        regime = reasons.get("market_regime", "unknown")
        risk = (
            "Broad market bear regime active — higher false-signal rate; reduce size"
            if regime == "bear"
            else f"Earnings in {days_to_earnings}d — binary event risk; consider waiting for print" if days_to_earnings and int(days_to_earnings) <= 10
            else "Broader market sell-off would override stock-specific signal regardless of fundamentals"
        )

        return {
            "entry1": entry1, "entry1_note": e1_note,
            "entry2": entry2, "entry2_note": e2_note,
            "breakout": breakout, "breakout_note": breakout_note,
            "stop": stop, "stop_note": stop_note,
            "take_profit": take_profit, "take_profit_note": tp_note,
            "catalysts": catalysts,
            "risk": risk,
            "current_price": current_price,
            "horizon_note": params["horizon_note"],
            "style": style.upper(),
        }
    except Exception as exc:
        log.warning("game_plan.build_failed", symbol=symbol, error=str(exc))
        return None


def _round_step(price: float) -> float:
    """Return a sensible rounding step for a given price."""
    if price >= 1000: return 5.0
    if price >= 100:  return 0.5
    if price >= 10:   return 0.1
    if price >= 1:    return 0.05
    return 0.01


# DP-1: track consecutive email failures per alert to prevent infinite retry loops
_alert_fail_counts: dict[int, int] = {}


_SIGNAL_ALERT_LOCK_KEY = "stockai:lock:check_signal_alerts"
_SIGNAL_ALERT_LOCK_TTL = 120  # seconds — prevents concurrent runs from US+HK scheduler overlap

_PRICE_ALERT_LOCK_KEY = "stockai:lock:check_price_alerts"
_PRICE_ALERT_LOCK_TTL = 55  # seconds — alert checker runs every 60s; 55s prevents overlap

_PAPER_TRADING_LOCK_KEY = "stockai:lock:paper_trading_step"
_PAPER_TRADING_LOCK_TTL = 90  # seconds — typical step runs in 20-40s; 90s prevents double-exec


def _run_paper_trading_step(label: str = "refresh") -> None:
    """Run paper_trading_step() with a distributed Redis lock.

    Both _refresh_market() and _refresh_5m() call paper_trading_step(). During the
    15:30–16:15 ET close burst, both fire within the same minute, creating a race where
    two concurrent executions can each observe the same open positions and double-credit
    cash on exit. The SET NX EX lock ensures only one execution runs at a time.
    """
    acquired = False
    try:
        acquired = bool(_get_redis().set(_PAPER_TRADING_LOCK_KEY, label, nx=True, ex=_PAPER_TRADING_LOCK_TTL))
        if not acquired:
            log.info("paper.step_skipped_locked", label=label, reason="another run in progress")
            return
    except Exception:
        pass  # Redis unavailable — allow through; double-execution is unlikely without it
    try:
        paper_trading_step()
        # Poll pending broker orders for actual fills (no-op if no broker-linked portfolios)
        try:
            from services.paper_trading_engine import poll_broker_order_fills
            poll_broker_order_fills()
        except Exception as _bpe:
            log.warning("broker.poll_step_failed", error=str(_bpe))
    finally:
        if acquired:
            try:
                _get_redis().delete(_PAPER_TRADING_LOCK_KEY)
            except Exception:
                pass


def check_signal_alerts() -> None:
    """Fire conviction BUY alerts when all 5 layers align; fire exit warnings unconditionally.

    Five-layer conviction gate (BUY transitions only):
      1. Signal transitions to BUY
      2. AI confidence >= 60%
      3. Analyst consensus = buy / strong_buy / outperform
      4+5. K-Score + Technical + Momentum sub-scores via confluence >= 75

    Bearish/exit transitions (BUY→HOLD/WAIT/SELL) bypass the gate — exit
    warnings are always sent regardless of scores.
    """
    # Distributed lock: US + HK refreshes both call this function. NX+EX ensures only one
    # run executes at a time — the second caller skips rather than sending duplicate emails.
    try:
        acquired = _get_redis().set(_SIGNAL_ALERT_LOCK_KEY, "1", nx=True, ex=_SIGNAL_ALERT_LOCK_TTL)
        if not acquired:
            log.info("signal_alert.skipped_locked", reason="another run in progress")
            return
    except Exception:
        pass  # Redis unavailable — allow through; deduplication falls back to DB last_signal
    try:
        with SessionLocal() as session:
            alerts = session.execute(
                select(SignalAlert).options(selectinload(SignalAlert.user))
            ).scalars().all()
            if not alerts:
                return

            # SCHED-6: Prune stale fail-count entries for deleted alert IDs to prevent unbounded growth.
            active_ids = {a.id for a in alerts}
            stale_ids = [k for k in list(_alert_fail_counts) if k not in active_ids]
            for k in stale_ids:
                _alert_fail_counts.pop(k, None)

            symbols = list({a.symbol for a in alerts})

            # DP-3: Build per-symbol price freshness map; skip symbols with stale bars.
            # Use 4-day window to accommodate weekends (Fri close → Mon alert run = 3 calendar days).
            fresh_symbols: set[str] = set()
            stale_cutoff = datetime.now(timezone.utc) - timedelta(days=4)
            try:
                price_rows = session.execute(
                    select(Stock.symbol, Price.ts)
                    .join(Price, Price.stock_id == Stock.id)
                    .where(Stock.symbol.in_(symbols))
                    .order_by(Stock.symbol, Price.ts.desc())
                ).all()
                seen: set[str] = set()
                for sym, ts in price_rows:
                    if sym in seen:
                        continue
                    seen.add(sym)
                    ts_aware = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
                    if ts_aware >= stale_cutoff:
                        fresh_symbols.add(sym)
                    else:
                        log.warning("signal_alert.skipped_stale", symbol=sym,
                                    last_bar=ts.isoformat(), stale_cutoff=stale_cutoff.isoformat())
            except Exception as exc:
                log.warning("signal_alert.freshness_check_failed", error=str(exc))
                fresh_symbols = set(symbols)  # fall through on DB error

            # If freshness check returned nothing (empty DB / no prices yet), allow all symbols
            # through rather than silently suppressing every alert.
            if not fresh_symbols and symbols:
                log.warning("signal_alert.freshness_no_prices",
                            note="No price bars found for any alert symbol — assuming fresh to avoid silent blackout",
                            symbol_count=len(symbols))
                fresh_symbols = set(symbols)

            _ALL_HORIZONS = ["SHORT", "SWING", "LONG", "GROWTH"]

            # Build the set of (symbol, horizon) pairs to fetch.
            # Alerts now carry an explicit horizon; for require_consensus alerts we also
            # need all 4 horizons per symbol to count how many agree.
            consensus_symbols = {a.symbol for a in alerts if getattr(a, "require_consensus", False)}
            style_sym_pairs: set[tuple[str, str]] = set()
            for a in alerts:
                style_sym_pairs.add((a.symbol, getattr(a, "horizon", "SWING")))
            for sym in consensus_symbols:
                for h in _ALL_HORIZONS:
                    style_sym_pairs.add((sym, h))

            # Fetch current signals per unique (symbol, horizon) pair.
            # live=False: read stored DB signal — consistent with signal filter page.
            # live=True (old default) caused intraday oscillation for threshold-boundary
            # stocks: the signal would flip BUY↔HOLD on every 1-minute check.
            # AUD19-PERF1: 45s wall-clock budget prevents blocking APScheduler thread pool.
            _SIGNAL_BUDGET_S = 45.0
            _alert_t0 = time.monotonic()
            signals: dict[tuple[str, str], str] = {}
            signal_details: dict[tuple[str, str], dict] = {}
            _skipped_signals = 0
            for sym, style in style_sym_pairs:
                if time.monotonic() - _alert_t0 > _SIGNAL_BUDGET_S:
                    _skipped_signals += 1
                    continue
                try:
                    r = httpx.get(
                        f"{_settings.signal_engine_url}/signals/{sym}",
                        params={"style": style, "live": "false"}, timeout=10,
                    )
                    if r.status_code == 200:
                        payload = r.json()
                        signals[(sym, style)] = payload.get("signal", "")
                        signal_details[(sym, style)] = payload
                except Exception:
                    pass
            if _skipped_signals:
                log.warning("signal_alert.budget_exceeded_signals",
                            skipped=_skipped_signals, budget_s=_SIGNAL_BUDGET_S)

            # Fetch analyst ratings + fundamentals (rec_mean, earnings, insider data)
            _FUND_BUDGET_S = 45.0
            _fund_t0 = time.monotonic()
            analyst_ratings: dict[str, str] = {}
            fundamentals_cache: dict[str, dict] = {}
            _skipped_fundamentals = 0
            for sym in symbols:
                if time.monotonic() - _fund_t0 > _FUND_BUDGET_S:
                    _skipped_fundamentals += 1
                    continue
                try:
                    r = httpx.get(f"{_settings.market_data_url}/stocks/{sym}/fundamentals", timeout=10)
                    if r.status_code == 200:
                        payload = r.json()
                        analyst_ratings[sym] = (payload.get("recommendation") or "").lower()
                        fundamentals_cache[sym] = payload
                except Exception:
                    pass
            if _skipped_fundamentals:
                log.warning("signal_alert.budget_exceeded_fundamentals",
                            skipped=_skipped_fundamentals, budget_s=_FUND_BUDGET_S)

            # Fetch K-Scores in one bulk call for Layer 2 conviction check
            kscores: dict[str, float] = {}
            rankings_api_ok: bool = False
            try:
                r = httpx.get(f"{_settings.ranking_engine_url}/rankings", timeout=15)
                if r.status_code == 200:
                    rankings_api_ok = True
                    for row in r.json().get("rankings", []):
                        if row.get("score") is not None:
                            kscores[row["symbol"]] = float(row["score"])
            except Exception:
                pass

            # Fetch 90d per-symbol outcomes in one call for WR badge in alert emails
            sym_wr_map: dict[str, tuple[float, int]] = {}
            try:
                wr_r = httpx.get(
                    f"{_settings.signal_engine_url}/signals/outcomes/summary",
                    params={"days": "90"}, timeout=10,
                )
                if wr_r.status_code == 200:
                    for _s in wr_r.json().get("by_symbol", []):
                        if (_s.get("count") or 0) >= 3:
                            sym_wr_map[_s["symbol"]] = (float(_s.get("win_rate") or 0), _s["count"])
            except Exception:
                pass

            # Current live regime — used only for non-BUY confidence gate (lighter path).
            # The BUY conviction gate now reads regime from each signal's stored reasons dict
            # so gate and signal generation always operate in the same market context.
            current_regime = _get_current_regime()
            regime_thresholds = _REGIME_THRESHOLDS.get(current_regime, _REGIME_THRESHOLDS["unknown"])

            fired = 0
            for alert in alerts:
                # DP-3: skip if price data is stale
                if alert.symbol not in fresh_symbols:
                    continue

                style = getattr(alert, "horizon", "SWING")
                key = (alert.symbol, style)
                current = signals.get(key)
                if not current:
                    continue

                prev = alert.last_signal

                if prev == current:
                    # Refresh conviction status for stable BUY stocks every minute.
                    if current == "BUY":
                        sig_data = signal_details.get(key) or {}
                        all_pass, _tier, passed, failed = _is_conviction_buy(
                            sig_data, kscore=kscores.get(alert.symbol), rankings_api_ok=rankings_api_ok
                        )
                        db_sent_at = alert.last_sent_at.isoformat() if alert.last_sent_at else None
                        _store_conviction(alert.symbol, style, True, passed, failed, current, sent_at=db_sent_at)
                    continue

                # Treat None→BUY as a bullish transition (stock was already at BUY
                # when the alert was first created; prev=None since no prior state).
                is_bullish = (prev, current) in _BULLISH_TRANSITIONS or (prev is None and current == "BUY")
                is_bearish = (prev, current) in _BEARISH_TRANSITIONS

                # "buy_only" mode: only notify on transitions directly to/from BUY
                if getattr(alert, "alert_mode", "all") == "buy_only":
                    is_bullish = is_bullish and current == "BUY"
                    is_bearish = is_bearish and prev == "BUY"

                if not is_bullish and not is_bearish:
                    # Neutral or unrecognised transition — just advance the stored state.
                    alert.last_signal = current
                    _store_conviction(alert.symbol, style, False, [], [f"Signal is {current} — gate only runs on BUY transitions"], current)
                    continue

                # Consensus gate: skip if fewer than 2 horizons agree on the new direction.
                if getattr(alert, "require_consensus", False):
                    agreeing = sum(
                        1 for h in _ALL_HORIZONS
                        if signals.get((alert.symbol, h)) == current
                    )
                    if agreeing < 2:
                        log.info(
                            "signal_alert.skipped_consensus",
                            symbol=alert.symbol, horizon=style,
                            current=current, agreeing=agreeing,
                        )
                        continue  # last_signal NOT updated — retried next run

                # Both bullish and bearish state advances happen only after successful email
                # send (see `if email_ok` below), so a failed send can be retried next run.

                conviction_passed: list[str] | None = None
                conviction_tier: str = "full"  # default; overwritten by _is_conviction_buy on BUY path
                near_conviction = False
                near_conviction_failed: list[str] = []
                if is_bullish:
                    sig_data = signal_details.get(key) or {}
                    confidence = float(sig_data.get("confidence") or 0)

                    if current == "BUY":
                        # Full 4-layer conviction gate (CB-4: "near" tier allows 1 soft fail)
                        all_pass, conviction_tier, passed, failed = _is_conviction_buy(
                            sig_data, kscore=kscores.get(alert.symbol), rankings_api_ok=rankings_api_ok
                        )
                        if not all_pass:
                            log.info(
                                "signal_alert.skipped", symbol=alert.symbol,
                                reason="conviction_layers_failed", failed=failed,
                                regime=current_regime,
                            )
                            _store_conviction(alert.symbol, style, False, passed, failed, current, conviction_tier=conviction_tier)
                            continue  # last_signal NOT updated — retried next run
                        conviction_passed = passed
                        near_conviction = conviction_tier == "near"
                        near_conviction_failed = [f for f in failed if near_conviction]
                        sig_regime = (signal_details.get(key) or {}).get("reasons", {}).get("market_regime", "unknown")
                        log.info(
                            "signal_alert.conviction_met", symbol=alert.symbol,
                            tier=conviction_tier, passed=passed, regime=sig_regime,
                        )
                    else:
                        # Non-BUY bullish improvement (e.g. WAIT→HOLD) — lighter gate:
                        # analyst bullish + regime-aware minimum confidence (SA-12)
                        analyst_ok = analyst_ratings.get(alert.symbol, "") in _BULLISH_ANALYST
                        min_conf = regime_thresholds["confidence"]
                        if not analyst_ok or confidence < min_conf:
                            log.info(
                                "signal_alert.skipped", symbol=alert.symbol,
                                reason="analyst_or_confidence",
                                analyst=analyst_ratings.get(alert.symbol, ""),
                                confidence=confidence,
                            )
                            continue  # last_signal NOT updated — retried next run

                # Same-direction cooldown: if we already sent this exact direction within
                # the last 4 hours, advance state but skip the email. Prevents BUY→HOLD→BUY
                # oscillation spam when a stock is sitting right at the threshold boundary.
                _SAME_DIR_COOLDOWN_HRS = 2
                if alert.last_sent_at is not None:
                    sent_ago = datetime.now(timezone.utc) - alert.last_sent_at.replace(tzinfo=timezone.utc) if alert.last_sent_at.tzinfo is None else datetime.now(timezone.utc) - alert.last_sent_at
                    if sent_ago.total_seconds() < _SAME_DIR_COOLDOWN_HRS * 3600:
                        # Allow BUY→SELL (genuine reversal) regardless of cooldown.
                        is_reversal = (prev == "BUY" and current == "SELL") or (prev == "SELL" and current == "BUY")
                        if not is_reversal:
                            log.info("signal_alert.skipped_cooldown", symbol=alert.symbol,
                                     prev=prev, current=current,
                                     sent_ago_min=round(sent_ago.total_seconds() / 60, 1))
                            # Do NOT advance last_signal — if we set it to current now, the
                            # next run sees prev==current and never detects the transition.
                            # Keep last_signal at prev so the email fires once cooldown expires.
                            continue

                # Guard: no email address → log and advance state to avoid infinite retry
                effective_email = (alert.email or "").strip() or (
                    (alert.user.email or "") if alert.user else ""
                )
                if not effective_email:
                    log.warning("signal_alert.skipped", symbol=alert.symbol, reason="no_email_address")
                    alert.last_signal = current
                    continue

                # DE gate: for BUY transitions, confirm Decision Engine agrees before emailing.
                # Fail-open (allow alert) if DE is unreachable — never block on infrastructure failure.
                if is_bullish and current == "BUY":
                    try:
                        de_r = httpx.post(
                            f"{_settings.decision_engine_url}/decide/{alert.symbol}",
                            json={"style": style, "market": _get_symbol_market(session, alert.symbol)},
                            headers={"Authorization": f"Bearer {_service_token()}"},
                            timeout=3.0,
                        )
                        if de_r.status_code == 200:
                            de_verdict = de_r.json().get("verdict", "SKIP")
                            if de_verdict not in ("BUY", "SCALE"):
                                log.info(
                                    "signal_alert.skipped_de_gate",
                                    symbol=alert.symbol,
                                    de_verdict=de_verdict,
                                    reason="DE does not agree with BUY — suppressing alert",
                                )
                                # Do NOT advance last_signal — retry next run in case DE changes.
                                continue
                            log.info("signal_alert.de_gate_passed", symbol=alert.symbol, de_verdict=de_verdict)
                    except Exception as _de_exc:
                        log.debug("signal_alert.de_gate_error", symbol=alert.symbol, error=str(_de_exc),
                                  note="DE unreachable — fail-open, allowing alert")

                # Build game plan for BUY transitions, tailored to the user's trading style
                game_plan = None
                if current == "BUY":
                    game_plan = _build_game_plan(
                        alert.symbol,
                        signal_details.get(key, {}),
                        fundamentals_cache.get(alert.symbol),
                        style=style,
                    )

                email_ok = send_signal_alert_email(
                    to=effective_email,
                    symbol=alert.symbol,
                    prev_signal=prev,
                    new_signal=current,
                    analyst=analyst_ratings.get(alert.symbol, ""),
                    signal_data=signal_details.get(key, {}),
                    fundamentals=fundamentals_cache.get(alert.symbol),
                    game_plan=game_plan,
                    conviction_layers=conviction_passed,
                    near_conviction=near_conviction,
                    near_conviction_failed=near_conviction_failed,
                    horizon=style,
                    win_rate_90d=sym_wr_map.get(alert.symbol),
                )
                if email_ok:
                    alert.last_signal = current  # advance state only after successful send
                    now_utc = datetime.now(timezone.utc)
                    alert.last_sent_at = now_utc   # persist so Redis restarts don't lose sent_at
                    fired += 1
                    _alert_fail_counts.pop(alert.id, None)  # reset failure counter
                    log.info("signal_alert.fired", symbol=alert.symbol, prev=prev, current=current, style=style)
                    _store_conviction(alert.symbol, style, True, conviction_passed or [], [], current,
                                      sent_at=now_utc.isoformat(), conviction_tier=conviction_tier or "full")
                    # T230-ALERTING-SLACK-DISCORD: also deliver via webhook if user has one configured
                    webhook = getattr(alert.user, "notification_webhook", None)
                    if webhook:
                        send_webhook_notification(
                            webhook,
                            title=f"{alert.symbol} signal: {prev} → {current}",
                            message=analyst_ratings.get(alert.symbol, ""),
                            color=0x22c55e if current == "BUY" else 0xef4444,
                        )
                else:
                    # DP-1: cap retries to prevent infinite loop on broken email config
                    _alert_fail_counts[alert.id] = _alert_fail_counts.get(alert.id, 0) + 1
                    if _alert_fail_counts[alert.id] >= 5:
                        log.error(
                            "signal_alert.email_retry_limit",
                            symbol=alert.symbol, retries=5,
                            note="advancing state to stop retry loop; check SMTP config",
                        )
                        alert.last_signal = current  # force-advance so next run sees new state
                        _alert_fail_counts.pop(alert.id, None)

            session.commit()
            if fired:
                log.info("signal_alert.check_done", fired=fired)

            # T230-ALERTING-EARNINGS-PROXIMITY: send earnings reminder for watchlist stocks
            try:
                user_symbols: dict[int, set[str]] = {}
                for a in alerts:
                    user_symbols.setdefault(a.user_id, set()).add(a.symbol)
                _rc = _get_redis()
                for uid, syms in user_symbols.items():
                    u_obj = next((a.user for a in alerts if a.user_id == uid), None)
                    if not u_obj or not u_obj.email:
                        continue
                    for sym in syms:
                        fund = fundamentals_cache.get(sym) or {}
                        dte = fund.get("days_to_earnings")
                        if dte is None:
                            continue
                        try:
                            dte_int = int(dte)
                        except (TypeError, ValueError):
                            continue
                        if dte_int not in (1, 2, 3, 5):
                            continue
                        redis_key = f"stockai:earnings_remind:{uid}:{sym}:{dte_int}"
                        try:
                            if _rc and _rc.exists(redis_key):
                                continue
                        except Exception:
                            pass
                        subject = f"⏰ Earnings in {dte_int}d: {sym}"
                        body_text = f"{sym} reports earnings in {dte_int} day(s). Review your position and manage risk before the print."
                        from .email_service import send_email
                        if send_email(u_obj.email, subject, f"<p>{body_text}</p>", body_text):
                            try:
                                _rc and _rc.setex(redis_key, 72000, "1")  # 20-hour TTL
                            except Exception:
                                pass
                            log.info("signal_alert.earnings_reminder_sent",
                                     symbol=sym, days=dte_int, user=u_obj.username)
            except Exception as exc:
                log.warning("signal_alert.earnings_reminder_error", error=str(exc))
    except Exception as exc:
        log.error("signal_alert.check_error", error=str(exc))
    finally:
        try:
            _get_redis().delete(_SIGNAL_ALERT_LOCK_KEY)
        except Exception:
            pass


def _fire_webhook(url: str, payload: dict) -> None:
    """POST a HMAC-SHA256-signed alert payload to a webhook URL. Best-effort."""
    import hashlib, hmac as _hmac, json as _json
    try:
        body = _json.dumps(payload, default=str)
        secret = get_settings().jwt_secret.encode()
        sig = _hmac.new(secret, body.encode(), hashlib.sha256).hexdigest()
        with httpx.Client(timeout=5) as c:
            c.post(url, content=body, headers={"Content-Type": "application/json", "X-Signature": f"sha256={sig}"})
    except Exception as exc:
        log.warning("webhook.failed", url=url, error=str(exc))


def check_price_alerts() -> None:
    """Check all untriggered alerts against latest live prices and fire emails."""
    try:
        acquired = _get_redis().set(_PRICE_ALERT_LOCK_KEY, "1", nx=True, ex=_PRICE_ALERT_LOCK_TTL)
        if not acquired:
            log.info("price_alert.skipped_locked", reason="another run in progress")
            return
    except Exception:
        pass  # Redis unavailable — allow through; rare double-send risk accepted
    try:
        import yfinance as yf
        with SessionLocal() as session:
            alerts = session.execute(
                select(PriceAlert).where(PriceAlert.triggered.is_(False))
            ).scalars().all()
            if not alerts:
                return

            # Fetch live prices for all unique symbols at once
            symbols = list({a.symbol for a in alerts})
            tickers = yf.Tickers(" ".join(symbols))
            prices: dict[str, float] = {}
            for sym in symbols:
                try:
                    p = tickers.tickers[sym].fast_info.last_price
                    if p:
                        prices[sym] = float(p)
                except Exception:
                    pass

            fired = 0
            pending_emails: list[dict] = []
            pending_webhooks: list[tuple[str, dict]] = []
            for alert in alerts:
                price = prices.get(alert.symbol)
                if price is None:
                    continue
                should_trigger = (
                    (alert.condition.value == "above" and price >= alert.threshold) or
                    (alert.condition.value == "below" and price <= alert.threshold)
                )
                if not should_trigger:
                    continue

                alert.triggered = True
                alert.triggered_at = datetime.now(timezone.utc)
                fired += 1
                log.info("alert.triggered", symbol=alert.symbol, price=price, threshold=alert.threshold)

                if alert.email:
                    pending_emails.append(dict(
                        to=alert.email, symbol=alert.symbol,
                        condition=alert.condition.value,
                        threshold=alert.threshold, price=price, note=alert.note,
                    ))
                if alert.webhook_url:
                    pending_webhooks.append((alert.webhook_url, dict(
                        symbol=alert.symbol, condition=alert.condition.value,
                        threshold=alert.threshold, price=price, note=alert.note,
                    )))

            # Commit triggered flags BEFORE sending emails so a crash between
            # commit and send causes a missed email rather than a duplicate.
            if fired:
                session.commit()
                log.info("alert.check_done", fired=fired, checked=len(alerts))

            for kwargs in pending_emails:
                if not send_price_alert_email(**kwargs):
                    log.warning("alert.email_failed", symbol=kwargs["symbol"], email=kwargs["to"])
            for url, payload in pending_webhooks:
                _fire_webhook(url, payload)

            # T230-ALERTING-PORTFOLIO-ALERTS: notify users when a paper position is down ≥ 5%.
            try:
                import yfinance as _yf2
                open_trades = session.execute(
                    select(PaperTrade).where(PaperTrade.exit_price.is_(None))
                ).scalars().all()
                if open_trades:
                    trade_syms = list({t.symbol for t in open_trades})
                    tickers = _yf2.Tickers(" ".join(trade_syms))
                    _rc = _get_redis()
                    for trade in open_trades:
                        try:
                            cur_px = tickers.tickers[trade.symbol].fast_info.last_price
                        except Exception:
                            continue
                        if not cur_px or not trade.entry_price or trade.entry_price <= 0:
                            continue
                        pct = cur_px / trade.entry_price - 1
                        if pct > -0.05:
                            continue
                        redis_key = f"stockai:pos_alert:{trade.portfolio_id}:{trade.symbol}"
                        try:
                            if _rc and _rc.exists(redis_key):
                                continue
                        except Exception:
                            pass
                        portfolio = session.get(PaperPortfolio, trade.portfolio_id)
                        owner_email = (portfolio.config or {}).get("owner_email") if portfolio else None
                        if not owner_email:
                            continue
                        send_price_alert_email(
                            to=owner_email,
                            symbol=trade.symbol,
                            condition="below",
                            threshold=trade.entry_price * 0.95,
                            price=cur_px,
                            note=f"Position down {abs(pct)*100:.1f}% from entry {trade.entry_price:.2f}",
                        )
                        try:
                            _rc and _rc.setex(redis_key, 86400, "1")  # 24-hour TTL
                        except Exception:
                            pass
                        log.info("alert.position_drawdown_sent",
                                 symbol=trade.symbol, pct=round(pct * 100, 1), email=owner_email)
            except Exception as _pe:
                log.warning("alert.position_drawdown_error", error=str(_pe))
    except Exception as exc:
        log.error("alert.check_error", error=str(exc))
    finally:
        try:
            _get_redis().delete(_PRICE_ALERT_LOCK_KEY)
        except Exception:
            pass


def check_technical_alerts() -> None:
    """Check EMA crossover and 52-week high/low alerts using DB price history.

    Runs after each market refresh (when fresh daily bars are ingested).
    EMA period is stored in the threshold field (20, 50, or 200).
    52-week conditions store 0 in threshold.

    Recurring alerts (recurring=True) re-fire every time the pattern is
    detected, subject to a per-pattern cooldown to prevent spam.
    """
    import pandas as pd
    from sqlalchemy import or_

    _TECHNICAL = {
        AlertCondition.CROSS_ABOVE_EMA,
        AlertCondition.CROSS_BELOW_EMA,
        AlertCondition.NEW_52WK_HIGH,
        AlertCondition.NEW_52WK_LOW,
        AlertCondition.GOLDEN_CROSS,
        AlertCondition.DEATH_CROSS,
        AlertCondition.MACD_BULLISH_CROSS,
        AlertCondition.RSI_OVERSOLD_BOUNCE,
        AlertCondition.DOUBLE_BOTTOM,
        AlertCondition.BREAKOUT,
        AlertCondition.VOLUME_SPIKE,
        AlertCondition.PCT_BELOW_52WK_HIGH,
    }

    # (no cooldown dict needed — same-day dedup is sufficient; see _already_fired_today)

    try:
        with SessionLocal() as session:
            # Include one-shot (triggered=False) AND recurring alerts
            alerts = session.execute(
                select(PriceAlert).where(
                    PriceAlert.condition.in_(_TECHNICAL),
                    or_(
                        PriceAlert.triggered.is_(False),
                        PriceAlert.recurring.is_(True),
                    ),
                )
            ).scalars().all()
            if not alerts:
                return

            now = datetime.now(timezone.utc)
            today = now.date()

            # For recurring alerts, skip if already fired today.
            # Patterns use daily bars — the same bar is re-read every 5 min,
            # so without this dedup the alert would fire all day on the same crossing.
            def _already_fired_today(alert: PriceAlert) -> bool:
                if not alert.recurring or alert.last_sent_at is None:
                    return False
                sent = alert.last_sent_at
                if sent.tzinfo is None:
                    sent = sent.replace(tzinfo=timezone.utc)
                return sent.date() >= today

            alerts = [a for a in alerts if not _already_fired_today(a)]
            if not alerts:
                return

            # Fetch 260 bars per unique symbol (enough for EMA200 + 52-week + MACD + RSI)
            symbols = list({a.symbol for a in alerts})
            prices_by_sym: dict[str, pd.Series] = {}
            volumes_by_sym: dict[str, pd.Series] = {}
            for sym in symbols:
                try:
                    stock = session.execute(
                        select(Stock).where(Stock.symbol == sym)
                    ).scalar_one_or_none()
                    if not stock:
                        continue
                    rows = session.execute(
                        select(Price.ts, Price.close, Price.volume)
                        .where(Price.stock_id == stock.id, Price.timeframe == "D1")
                        .order_by(Price.ts.asc())
                        .limit(260)
                    ).all()
                    if len(rows) < 3:
                        continue
                    prices_by_sym[sym] = pd.Series(
                        [float(r.close) for r in rows]
                    )
                    volumes_by_sym[sym] = pd.Series(
                        [float(r.volume) for r in rows]
                    )
                except Exception as exc:
                    log.warning("tech_alert.price_error", symbol=sym, error=str(exc))

            fired = 0
            pending_emails: list[dict] = []
            pending_webhooks: list[tuple[str, dict]] = []
            for alert in alerts:
                close = prices_by_sym.get(alert.symbol)
                if close is None:
                    continue
                volume = volumes_by_sym.get(alert.symbol, pd.Series(dtype=float))
                cond = alert.condition

                try:
                    if cond in (AlertCondition.CROSS_ABOVE_EMA, AlertCondition.CROSS_BELOW_EMA):
                        period = int(alert.threshold)  # 20, 50, or 200
                        if len(close) < period:
                            continue
                        ema = close.ewm(span=period, adjust=False).mean()
                        prev_above = close.iloc[-2] > ema.iloc[-2]
                        curr_above = close.iloc[-1] > ema.iloc[-1]
                        crossed = (
                            (cond == AlertCondition.CROSS_ABOVE_EMA and not prev_above and curr_above) or
                            (cond == AlertCondition.CROSS_BELOW_EMA and prev_above and not curr_above)
                        )
                        if not crossed:
                            continue
                        direction = "crossed above" if cond == AlertCondition.CROSS_ABOVE_EMA else "crossed below"
                        cond_label = f"{direction} EMA{period} ({ema.iloc[-1]:.2f})"
                        threshold_val = float(ema.iloc[-1])

                    elif cond == AlertCondition.NEW_52WK_HIGH:
                        if len(close) < 2:
                            continue
                        high_52 = float(close.iloc[:-1].tail(252).max())
                        if float(close.iloc[-1]) <= high_52:
                            continue
                        cond_label = f"hit a new 52-week high (prev high {high_52:.2f})"
                        threshold_val = high_52

                    elif cond == AlertCondition.NEW_52WK_LOW:
                        if len(close) < 2:
                            continue
                        low_52 = float(close.iloc[:-1].tail(252).min())
                        if float(close.iloc[-1]) >= low_52:
                            continue
                        cond_label = f"hit a new 52-week low (prev low {low_52:.2f})"
                        threshold_val = low_52

                    elif cond in (AlertCondition.GOLDEN_CROSS, AlertCondition.DEATH_CROSS):
                        if len(close) < 200:
                            continue
                        ema50 = close.ewm(span=50, adjust=False).mean()
                        ema200 = close.ewm(span=200, adjust=False).mean()
                        prev_above = ema50.iloc[-2] > ema200.iloc[-2]
                        curr_above = ema50.iloc[-1] > ema200.iloc[-1]
                        if cond == AlertCondition.GOLDEN_CROSS:
                            if prev_above or not curr_above:
                                continue
                            cond_label = f"Golden Cross — EMA50 ({ema50.iloc[-1]:.2f}) crossed above EMA200 ({ema200.iloc[-1]:.2f})"
                        else:
                            if not prev_above or curr_above:
                                continue
                            cond_label = f"Death Cross — EMA50 ({ema50.iloc[-1]:.2f}) crossed below EMA200 ({ema200.iloc[-1]:.2f})"
                        threshold_val = float(ema50.iloc[-1])

                    elif cond == AlertCondition.MACD_BULLISH_CROSS:
                        if len(close) < 35:
                            continue
                        ema12 = close.ewm(span=12, adjust=False).mean()
                        ema26 = close.ewm(span=26, adjust=False).mean()
                        macd = ema12 - ema26
                        sig = macd.ewm(span=9, adjust=False).mean()
                        if not (macd.iloc[-2] < sig.iloc[-2] and macd.iloc[-1] >= sig.iloc[-1]):
                            continue
                        cond_label = f"MACD Bullish Cross — MACD ({macd.iloc[-1]:.3f}) crossed above signal ({sig.iloc[-1]:.3f})"
                        threshold_val = float(sig.iloc[-1])

                    elif cond == AlertCondition.RSI_OVERSOLD_BOUNCE:
                        if len(close) < 16:
                            continue
                        delta = close.diff()
                        gain = delta.clip(lower=0).rolling(14).mean()
                        loss = (-delta.clip(upper=0)).rolling(14).mean()
                        rs = gain / loss.replace(0, float("nan"))
                        rsi = 100 - (100 / (1 + rs))
                        prev_rsi = float(rsi.iloc[-2]) if not pd.isna(rsi.iloc[-2]) else None
                        curr_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None
                        if prev_rsi is None or curr_rsi is None or prev_rsi >= 30 or curr_rsi < 30:
                            continue
                        cond_label = f"RSI Oversold Bounce — RSI recovered from {prev_rsi:.1f} to {curr_rsi:.1f} (above 30)"
                        threshold_val = curr_rsi

                    elif cond == AlertCondition.DOUBLE_BOTTOM:
                        if len(close) < 20:
                            continue
                        window = close.tail(60).values
                        minima: list[tuple[int, float]] = []
                        for i in range(2, len(window) - 2):
                            if all(window[i] <= window[j] for j in range(i - 2, i + 3) if j != i):
                                minima.append((i, float(window[i])))
                        if len(minima) < 2:
                            continue
                        b1_idx, b1_val = minima[-2]
                        b2_idx, b2_val = minima[-1]
                        lower = min(b1_val, b2_val)
                        if lower <= 0 or abs(b1_val - b2_val) / lower > 0.03 or b2_idx <= b1_idx + 3:
                            continue
                        # Second bottom must be within the last 10 bars — otherwise
                        # the same old pattern stays in the 60-bar window for weeks
                        if b2_idx < len(window) - 10:
                            continue
                        peak = float(max(window[b1_idx:b2_idx + 1]))
                        if peak < lower * 1.05 or float(close.iloc[-1]) <= lower * 1.01:
                            continue
                        cond_label = f"Double Bottom (W-pattern) — two troughs near ${lower:.2f}, price now ${float(close.iloc[-1]):.2f}"
                        threshold_val = lower

                    elif cond == AlertCondition.BREAKOUT:
                        if len(close) < 21:
                            continue
                        high_20 = float(close.iloc[-21:-1].max())
                        avg_vol = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else 0.0
                        curr_price = float(close.iloc[-1])
                        curr_vol = float(volume.iloc[-1]) if len(volume) > 0 else 0.0
                        if curr_price <= high_20 or avg_vol <= 0 or curr_vol < avg_vol * 1.4:
                            continue
                        cond_label = f"Volume Breakout — closed ${curr_price:.2f} above 20-day high ${high_20:.2f} with {curr_vol/avg_vol:.1f}x volume"
                        threshold_val = high_20

                    elif cond == AlertCondition.VOLUME_SPIKE:
                        if len(volume) < 21:
                            continue
                        avg_vol = float(volume.iloc[-21:-1].mean())
                        if avg_vol <= 0:
                            continue
                        multiplier = float(alert.threshold) if alert.threshold > 0 else 3.0
                        today_vol = float(volume.iloc[-1])
                        if today_vol < avg_vol * multiplier:
                            continue
                        curr_price = float(close.iloc[-1])
                        cond_label = f"Volume spike — {today_vol/avg_vol:.1f}× above 20-day average ({int(today_vol):,} vs avg {int(avg_vol):,})"
                        threshold_val = avg_vol * multiplier

                    elif cond == AlertCondition.PCT_BELOW_52WK_HIGH:
                        if len(close) < 2:
                            continue
                        high_52 = float(close.iloc[:-1].tail(252).max())
                        curr_price = float(close.iloc[-1])
                        if high_52 <= 0:
                            continue
                        pct_below = (high_52 - curr_price) / high_52 * 100
                        target_pct = float(alert.threshold) if alert.threshold > 0 else 10.0
                        if pct_below < target_pct:
                            continue
                        cond_label = f"Now {pct_below:.1f}% below 52-week high of {high_52:.2f} (current {curr_price:.2f})"
                        threshold_val = high_52

                    else:
                        continue

                    fire_time = datetime.now(timezone.utc)
                    if alert.recurring:
                        # Recurring: stamp last_sent_at but leave triggered=False so it stays active
                        alert.last_sent_at = fire_time
                        alert.triggered_at = fire_time
                    else:
                        # One-shot: mark done permanently
                        alert.triggered = True
                        alert.triggered_at = fire_time
                    fired += 1
                    log.info("tech_alert.triggered", symbol=alert.symbol, condition=cond_label, recurring=alert.recurring)

                    if alert.email:
                        pending_emails.append(dict(
                            to=alert.email,
                            symbol=alert.symbol,
                            condition=cond_label,
                            threshold=threshold_val,
                            price=float(close.iloc[-1]),
                            note=alert.note,
                        ))
                    if alert.webhook_url:
                        pending_webhooks.append((alert.webhook_url, dict(
                            symbol=alert.symbol, condition=cond_label,
                            threshold=threshold_val, price=float(close.iloc[-1]),
                            note=alert.note,
                        )))

                except Exception as exc:
                    log.warning("tech_alert.check_error", symbol=alert.symbol, error=str(exc))

            if fired:
                session.commit()
                log.info("tech_alert.check_done", fired=fired)

            for kwargs in pending_emails:
                if not send_price_alert_email(**kwargs):
                    log.warning("tech_alert.email_failed", symbol=kwargs["symbol"], email=kwargs["to"])
            for url, payload in pending_webhooks:
                _fire_webhook(url, payload)

    except Exception as exc:
        log.error("tech_alert.error", error=str(exc))


def _refresh_fundamentals_batch(symbols: list[str]) -> None:
    """Fetch fresh fundamentals for every symbol and persist to DB.

    Called from _weekly_full_refresh() after the daily ingest so the ML
    retrain on Sunday night uses up-to-date revenue_growth, ROE, short_ratio
    etc. Rate-limited to ~3 requests/second to stay within yfinance limits.
    Each call is best-effort: failures are logged and skipped, not fatal.
    """
    _t0 = time.monotonic()
    ok = failed = 0
    tok = _service_token()
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    for sym in symbols:
        try:
            r = httpx.get(
                f"{_settings.market_data_url}/stocks/{sym}/fundamentals",
                headers=headers,
                timeout=15,
            )
            if r.status_code == 200:
                ok += 1
            else:
                log.warning("scheduler.fund_batch.skip", symbol=sym, status=r.status_code)
                failed += 1
        except Exception as exc:
            log.warning("scheduler.fund_batch.error", symbol=sym, error=str(exc))
            failed += 1
        time.sleep(0.33)  # ~3 req/s — stay under yfinance rate limit

    elapsed = time.monotonic() - _t0
    log.info("scheduler.fund_batch_done", ok=ok, failed=failed, elapsed_s=round(elapsed))
    _record_job_status("fundamentals_batch", "ok" if failed == 0 else "partial", elapsed)


def _weekly_full_refresh() -> None:
    """Force re-ingest 3 years of daily bars for every active stock.

    Runs Sunday 14:00 PST — roughly 19 hours before HK Monday open — so both
    markets start the week with clean, gap-free price history.  Triggers a
    full rankings + signals refresh once ingestion completes, then kicks off
    the Optuna tune_all job so Monday's signals use freshly tuned hyperparams.
    tune_all runs in the background inside the ml-prediction container (~2–4 h).
    """
    _t0 = time.monotonic()
    all_symbols: list[str] = []
    try:
        all_symbols = _symbols_for("US") + _symbols_for("HK")
        if not all_symbols:
            log.info("scheduler.weekly_refresh.skip", reason="no_symbols")
            _record_job_status("weekly_refresh", "skipped: no symbols", 0.0)
            return
        log.info("scheduler.weekly_refresh_start", count=len(all_symbols))
        ingest_universe(all_symbols, "1d", force=True)
        _post(f"{_settings.ranking_engine_url}/rankings/refresh", params={"market": "US"})
        _post(f"{_settings.ranking_engine_url}/rankings/refresh", params={"market": "HK"})
        # M-8: split by market to isolate failures and avoid OOM on single bulk refresh
        _post(f"{_settings.signal_engine_url}/signals/refresh", params={"market": "US"})
        _post(f"{_settings.signal_engine_url}/signals/refresh", params={"market": "HK"})
        _record_job_status("weekly_refresh", "ok", time.monotonic() - _t0)
        log.info("scheduler.weekly_refresh_done", count=len(all_symbols))
    except Exception as exc:
        log.error("scheduler.weekly_refresh_failed", error=str(exc))
        _record_job_status("weekly_refresh", "error", time.monotonic() - _t0, str(exc))

    # ML-FUND-2: refresh fundamentals for all symbols so Sunday's ML retrain
    # uses up-to-date revenue_growth, ROE, short_ratio, etc.  Runs after the
    # price ingest (daily bars must exist before fundamentals are useful).
    # ~46 seconds for 138 symbols at 3 req/s — completes well before tune_all starts.
    if all_symbols:
        log.info("scheduler.fund_batch_start", count=len(all_symbols))
        _refresh_fundamentals_batch(all_symbols)

    # Kick off Optuna hyperparameter tuning for all symbols.
    # Runs as a background task in ml-prediction — returns immediately, tunes for ~2–4 h.
    # Best params are saved per-symbol JSON and used by all subsequent daily retrains.
    log.info("scheduler.tune_all_start")
    _post(f"{_settings.ml_prediction_url}/ml/tune_all")
    _record_job_status("tune_all_sent", "ok", 0.0)

    # SA-5: calibrate TA weights from signal outcome history.
    # Fits logistic regression on TA features vs is_correct; writes ta_weights.json.
    # Runs after tune_all kick-off (both are fire-and-forget; no ordering dependency).
    log.info("scheduler.calibrate_ta_weights_start")
    _post(f"{_settings.signal_engine_url}/signals/calibrate_ta_weights")
    _record_job_status("calibrate_ta_weights_sent", "ok", 0.0)

    # AL-3: calibrate conviction layer weights from signal_outcomes.
    # Fits logistic regression on reason boolean flags; writes conviction_weights.json.
    log.info("scheduler.calibrate_conviction_weights_start")
    _post(f"{_settings.signal_engine_url}/signals/calibrate_conviction_weights")
    _record_job_status("calibrate_conviction_weights_sent", "ok", 0.0)

    # Tier 79: auto-apply empirically-optimal buy thresholds from live outcomes data.
    # Writes per-horizon thresholds to Redis; signal generator reads them live.
    log.info("scheduler.calibrate_signal_thresholds_start")
    _post(f"{_settings.signal_engine_url}/signals/outcomes/calibrate/apply")
    _record_job_status("calibrate_signal_thresholds_sent", "ok", 0.0)

    # Tier 85: sweep style-specific gate params (ml_weight_cap, adx_min, breadth_compression)
    # against live outcomes data. Writes optimal values to Redis per-style; signal generator
    # reads them via _get_style_tuned_param() — falls back to hardcoded defaults when absent.
    log.info("scheduler.tune_style_profiles_start")
    _post(f"{_settings.signal_engine_url}/signals/tune_style_profiles")
    _record_job_status("tune_style_profiles_sent", "ok", 0.0)

    # PT-3: calibrate entry factor weights from closed paper trades.
    # Fits logistic regression on (rr_ratio, confidence, entry_score, kscore) vs win/loss.
    # Called directly (not via HTTP) because the service token has no DB user record.
    try:
        log.info("scheduler.calibrate_entry_weights_start")
        from ..api.paper_portfolio import calibrate_entry_weights as _cal_entry
        result = _cal_entry()
        status = "ok" if "error" not in result else result["error"]
        _record_job_status("calibrate_entry_weights", status, 0.0)
    except Exception as _exc:
        log.error("scheduler.calibrate_entry_weights_failed", error=str(_exc))
        _record_job_status("calibrate_entry_weights", "error", 0.0, str(_exc))

    # AL-1: train RL Q-function on closed paper trades (Ridge regression → pct_return).
    # Requires ≥50 trades. Saves policy to /data/models/rl_policy.json.
    try:
        log.info("scheduler.rl_agent_train_start")
        from .rl_agent import run_rl_training as _rl_train
        rl_result = _rl_train()
        if "skipped" in rl_result:
            rl_status = f"skipped: {rl_result['skipped']}"
        elif "error" in rl_result:
            rl_status = rl_result["error"]
        else:
            rl_status = "ok"
        _record_job_status("rl_agent_train", rl_status, 0.0)
    except Exception as _exc:
        log.error("scheduler.rl_agent_train_failed", error=str(_exc))
        _record_job_status("rl_agent_train", "error", 0.0, str(_exc))


def _retrain_meta_model() -> None:
    """T89: Monthly cross-symbol meta-learning model retraining.

    Trains on all signal_outcomes with is_correct set — improves cold-start
    priors for new symbols and adds diversity to the ensemble as more outcomes
    accumulate. Fire-and-forget POST to ml-prediction (runs as background task).
    """
    _t0 = time.monotonic()
    try:
        log.info("meta_model.retrain_trigger")
        _post(f"{_settings.ml_prediction_url}/ml/train_meta")
        elapsed = time.monotonic() - _t0
        log.info("meta_model.retrain_triggered", elapsed_s=round(elapsed, 1))
        _record_job_status("meta_model_retrain", "ok", elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - _t0
        log.error("meta_model.retrain_failed", error=str(exc), exc_info=True)
        _record_job_status("meta_model_retrain", "error", elapsed, str(exc))


def _ingest_edgar_8k() -> None:
    """T208: Trigger SEC EDGAR 8-K filing ingest via event-intelligence service.

    Runs once daily at 17:30 ET (1.5h after US close) so all 8-K filings
    from the trading day are available before end-of-day signal review.
    The event-intelligence service handles stock universe lookup, CIK resolution,
    and rate-limiting (0.15s/CIK to stay under SEC's 10 req/s fair-use policy).
    HK stocks are skipped automatically inside the ingest function.
    """
    _t0 = time.monotonic()
    try:
        log.info("edgar.ingest_trigger")
        _post(f"{_settings.event_intelligence_url}/events/sync/8k")
        elapsed = time.monotonic() - _t0
        log.info("edgar.ingest_triggered", elapsed_s=round(elapsed, 1))
        _record_job_status("edgar_8k_ingest", "ok", elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - _t0
        log.error("edgar.ingest_failed", error=str(exc), exc_info=True)
        _record_job_status("edgar_8k_ingest", "error", elapsed, str(exc))


def _ingest_hk_connect_flows() -> None:
    """T209: Fetch HKEX Stock Connect southbound flows for all active HK stocks.

    Runs once daily at 17:00 HKT — approximately 1 hour after HK market close
    so HKEX has time to publish the day's trading data.  Runs only on weekdays
    (HKEX does not publish flow data on weekends or holidays).
    Fail-safe: any single-stock failure is logged and skipped; other stocks
    continue.  The job records its status to Redis for the admin health monitor.
    """
    if _is_hk_holiday():
        log.info("hk_connect.skip", reason="hk_public_holiday")
        return

    _t0 = time.monotonic()
    try:
        from .hk_connect import ingest_southbound_flows
        hk_symbols = _symbols_for("HK")
        if not hk_symbols:
            log.info("hk_connect.skip", reason="no_hk_symbols")
            _record_job_status("hk_connect_flows", "skipped: no HK symbols", 0.0)
            return

        log.info("hk_connect.ingest_start", symbol_count=len(hk_symbols))
        with SessionLocal() as session:
            result = ingest_southbound_flows(session, hk_symbols)

        elapsed = time.monotonic() - _t0
        status = "ok" if result["failed"] == 0 else "partial"
        _record_job_status("hk_connect_flows", status, elapsed)
        log.info(
            "hk_connect.ingest_done",
            processed=result["processed"],
            stored=result["stored"],
            failed=result["failed"],
            elapsed_s=round(elapsed, 1),
        )
    except Exception as exc:
        elapsed = time.monotonic() - _t0
        _record_job_status("hk_connect_flows", "error", elapsed, str(exc))
        log.error("hk_connect.ingest_failed", error=str(exc), exc_info=True)


def _snapshot_fundamentals() -> None:
    """T220-F: Weekly fundamentals snapshot for earnings revision momentum tracking.

    Captures recommendation_mean, revenue_growth, earnings_growth, return_on_equity
    from the fundamentals table. Used to compute 8-week revision momentum.
    """
    from datetime import datetime, timezone, date
    from db import SessionLocal
    from sqlalchemy import text
    _t0 = time.monotonic()
    try:
        with SessionLocal() as sess:
            today = date.today().isoformat()
            # Copy latest fundamentals row per symbol into snapshot (idempotent via ON CONFLICT DO NOTHING)
            result = sess.execute(text("""
                INSERT INTO fundamentals_snapshot
                    (symbol, snapshot_date, recommendation_mean, eps_estimate,
                     revenue_growth, earnings_growth, return_on_equity)
                SELECT s.symbol, :today, f.recommendation_mean, NULL,
                       f.revenue_growth, f.earnings_growth, f.return_on_equity
                FROM fundamentals f
                JOIN stocks s ON s.id = f.stock_id
                WHERE s.delisted = false
                ON CONFLICT (symbol, snapshot_date) DO NOTHING
            """), {"today": today})
            sess.commit()
            n = result.rowcount
        elapsed = time.monotonic() - _t0
        _record_job_status("fundamentals_snapshot", "ok", elapsed)
        log.info("scheduler.fundamentals_snapshot_complete", snapshots=n)
    except Exception as exc:
        elapsed = time.monotonic() - _t0
        _record_job_status("fundamentals_snapshot", "error", elapsed, str(exc))
        log.error("scheduler.fundamentals_snapshot_failed", error=str(exc))


def _compute_sector_rotation() -> None:
    """T220-G: Compute sector K-Score momentum (this week vs 4 weeks ago) and cache in Redis."""
    import json as _json
    from db import SessionLocal
    from sqlalchemy import text as _text_sr
    _t0 = time.monotonic()
    try:
        with SessionLocal() as sess:
            rows = sess.execute(_text_sr("""
                SELECT s.sector,
                       AVG(CASE WHEN r.as_of >= NOW() - INTERVAL '14 days' THEN r.score END) as recent_kscore,
                       AVG(CASE WHEN r.as_of >= NOW() - INTERVAL '42 days' AND r.as_of < NOW() - INTERVAL '28 days' THEN r.score END) as prior_kscore,
                       COUNT(DISTINCT CASE WHEN r.as_of >= NOW() - INTERVAL '14 days' THEN s.id END) as n_recent
                FROM rankings r
                JOIN stocks s ON s.id = r.stock_id
                WHERE s.sector IS NOT NULL AND s.market = 'US'
                GROUP BY s.sector
                HAVING COUNT(DISTINCT CASE WHEN r.as_of >= NOW() - INTERVAL '14 days' THEN s.id END) >= 3
            """)).fetchall()

        rotation = {}
        for row in rows:
            if row.recent_kscore is None or row.prior_kscore is None:
                rotation[row.sector] = {"momentum": 0, "recent": None, "prior": None}
                continue
            delta = float(row.recent_kscore) - float(row.prior_kscore)
            momentum = 1 if delta > 3 else (-1 if delta < -3 else 0)
            rotation[row.sector] = {
                "momentum": momentum,
                "recent_kscore": round(float(row.recent_kscore), 1),
                "prior_kscore": round(float(row.prior_kscore), 1),
                "delta": round(delta, 1),
            }

        _get_redis().setex("stockai:sector_rotation", 86400 * 3, _json.dumps(rotation))
        elapsed = time.monotonic() - _t0
        _record_job_status("sector_rotation", "ok", elapsed)
        log.info("scheduler.sector_rotation_complete", sectors=len(rotation))
    except Exception as exc:
        elapsed = time.monotonic() - _t0
        _record_job_status("sector_rotation", "error", elapsed, str(exc))
        log.error("scheduler.sector_rotation_failed", error=str(exc))


def _purge_old_data() -> None:
    """Delete rows older than 90 days from intraday price bars and signal outcomes.

    5-minute intraday bars (prices WHERE timeframe='M5') grow ~3.5M rows/year.
    After 90 days they have no analytical value — all signals use daily bars.
    signal_outcomes older than 1 year are also pruned; the rolling 90-day window
    is sufficient for accuracy tracking and factor analysis.
    Runs weekly (Sunday pre-full-refresh) to keep the tables lean.
    """
    from sqlalchemy import text as _text
    try:
        with SessionLocal() as session:
            res5m = session.execute(
                _text("DELETE FROM prices WHERE timeframe='M5' AND ts < NOW() - INTERVAL '90 days'")
            )
            resout = session.execute(
                _text("DELETE FROM signal_outcomes WHERE ts_evaluated < NOW() - INTERVAL '400 days'")
            )
            session.commit()
            log.info(
                "scheduler.purge_done",
                m5_bars_deleted=res5m.rowcount,
                signal_outcomes_deleted=resout.rowcount,
            )
    except Exception as exc:
        log.error("scheduler.purge_failed", error=str(exc), exc_info=True)


def _check_short_intraday_triggers(market: str) -> None:
    """TIER83: Trigger SHORT signal refresh when intraday move exceeds 1.5× ATR.

    Runs after every 5m bar ingest. Finds active stocks with a SHORT BUY signal
    today, checks if the intraday price move (|current_close / first_bar_close - 1|)
    exceeds 1.5× the stored atr_14_pct from the latest signal reasons.

    When triggered, calls GET /signals/{symbol}?live=True&persist=True&style=SHORT
    on the signal engine to recompute and persist a fresh SHORT signal.

    Rate-limited via Redis: max 1 trigger per symbol per 60 minutes, max 3 per day.
    Fail-open: any exception is caught and logged so the 5m ingest is never blocked.
    """
    from datetime import date as _date, datetime as _dt

    today = _date.today()
    # market hours guard: US 09:30–16:00 ET, HK 09:30–16:00 HKT
    if market == "US":
        now_et = _dt.now(ZoneInfo("America/New_York"))
        if not ((now_et.hour > 9 or (now_et.hour == 9 and now_et.minute >= 30)) and now_et.hour < 16):
            return
    elif market == "HK":
        now_hk = _dt.now(ZoneInfo("Asia/Hong_Kong"))
        if not ((now_hk.hour > 9 or (now_hk.hour == 9 and now_hk.minute >= 30)) and now_hk.hour < 16):
            return

    try:
        r = redis_lib.Redis.from_url(_settings.redis_url, decode_responses=True)
    except Exception:
        return

    try:
        with SessionLocal() as session:
            # Get stocks with a SHORT BUY signal as of today
            start_of_day = _dt.combine(today, _dt.min.time()).replace(tzinfo=timezone.utc)
            short_signals = session.execute(
                select(Stock.symbol, Stock.id, Signal.reasons)
                .join(Signal, Signal.stock_id == Stock.id)
                .where(
                    Stock.market == market.upper(),
                    Stock.active.is_(True),
                    Signal.signal == SignalType.BUY,
                    Signal.horizon == SignalHorizon.SHORT,
                    Signal.ts >= start_of_day,
                )
                .order_by(Stock.id, Signal.ts.desc())
                .distinct(Stock.id)
            ).all()

            if not short_signals:
                return

            stock_ids = {row.id for row in short_signals}

            # Batch-fetch today's 5m bars for these stocks in one query
            bars_rows = session.execute(
                select(Price.stock_id, Price.close, Price.ts)
                .where(
                    Price.stock_id.in_(stock_ids),
                    Price.timeframe == TimeFrame.M5,
                    Price.ts >= start_of_day,
                )
                .order_by(Price.stock_id, Price.ts)
            ).all()

            # Group bars by stock_id
            bars_by_stock: dict[int, list] = {}
            for row in bars_rows:
                bars_by_stock.setdefault(row.stock_id, []).append(float(row.close))

        triggers: list[str] = []
        for sig_row in short_signals:
            sym = sig_row.symbol
            stock_id = sig_row.id
            reasons = sig_row.reasons or {}
            atr_pct = reasons.get("atr_14_pct") if isinstance(reasons, dict) else None
            if not atr_pct or atr_pct <= 0:
                # Fallback: use 1.5% as proxy ATR for stocks missing atr data
                atr_pct = 0.015

            bars = bars_by_stock.get(stock_id, [])
            if len(bars) < 3:
                continue  # not enough intraday data yet

            day_open = bars[0]
            current_price = bars[-1]
            if day_open <= 0:
                continue

            intraday_move = abs(current_price - day_open) / day_open
            threshold = 1.5 * atr_pct

            if intraday_move < threshold:
                continue

            # Rate-limit: 1 trigger per symbol per 60 min, max 3 per day
            hourly_key = f"stockai:short_trigger:{sym}:hourly"
            daily_key  = f"stockai:short_trigger:{sym}:{today}"
            if r.exists(hourly_key):
                continue
            daily_count = int(r.get(daily_key) or 0)
            if daily_count >= 3:
                continue

            # Mark rate limits before the HTTP call to prevent duplicate concurrent triggers
            r.setex(hourly_key, 3600, "1")
            r.setex(daily_key, 86400, str(daily_count + 1))
            triggers.append(sym)
            log.info("short_intraday_trigger.detected",
                     symbol=sym, market=market,
                     intraday_move_pct=round(intraday_move * 100, 2),
                     atr_pct=round(atr_pct * 100, 2),
                     threshold_pct=round(threshold * 100, 2))

        # Fire signal refreshes outside the DB session
        for sym in triggers:
            try:
                url = f"{_settings.signal_engine_url}/signals/{sym}?live=true&persist=true&style=SHORT"
                resp = httpx.get(url, timeout=15.0)
                log.info("short_intraday_trigger.refreshed",
                         symbol=sym, status=resp.status_code)
            except Exception as exc:
                log.warning("short_intraday_trigger.refresh_failed",
                            symbol=sym, error=str(exc))

    except Exception as exc:
        log.warning("short_intraday_trigger.error", market=market, error=str(exc))


def _refresh_5m(market: str) -> None:
    """Ingest the latest 5-minute bars and run a paper trading monitor cycle.

    Runs every 5 minutes during regular market hours. The 5m ingest updates
    intraday candles; the paper trading step then monitors open positions with
    fresh live prices so stops, trailing stops, and SELL exits are checked every
    5 minutes instead of every 10 during regular hours.
    Rankings and signals are NOT updated here (they use daily bars only).
    """
    if market == "HK" and _is_hk_holiday():
        return
    if market == "US" and not _is_us_trading_day():
        return
    symbols = _symbols_for(market)
    if not symbols:
        return
    log.info("scheduler.5m_ingest_start", market=market, count=len(symbols))
    try:
        ingest_universe(symbols, "5m")
        log.info("scheduler.5m_ingest_done", market=market, count=len(symbols))
    except Exception as exc:
        log.error("scheduler.5m_ingest_failed", market=market, error=str(exc))

    # PT-5M: paper trading position monitor runs after every 5m bar ingest.
    # Entry scan reads the latest BUY signal from DB, refreshed by _refresh_market.
    if market in ("US", "HK") and _settings.enable_paper_trading:
        _pt0 = time.monotonic()
        try:
            _run_paper_trading_step(label="refresh_5m")
            _record_job_status(f"paper_trading_5m_{market.lower()}", "ok", time.monotonic() - _pt0)
        except Exception as _pte:
            log.error("scheduler.paper_trading_5m_failed", market=market, error=str(_pte), exc_info=True)
            _record_job_status(f"paper_trading_5m_{market.lower()}", "error", time.monotonic() - _pt0, str(_pte))

    # TIER83: check if any SHORT-style stocks have crossed the intraday ATR trigger
    _check_short_intraday_triggers(market)


def _check_broker_auth() -> None:
    """Check all active broker connections for expired OAuth tokens.

    Runs at 08:30 ET each trading day — before market open. If any connection's
    tokens are rejected (ETrade expires daily at midnight ET), marks it unauthorized
    and emails the user a fresh authorize URL so they can re-auth before trading starts.
    """
    _t0 = time.monotonic()
    try:
        from db import SessionLocal
        from db.models import BrokerConnection, User
        from sqlalchemy import select
        from ..api.broker import _decrypt_config, _encrypt_config
        from ..services.broker import get_broker
        checked = expired = 0
        with SessionLocal() as s:
            conns = s.execute(
                select(BrokerConnection).where(BrokerConnection.is_active == True)  # noqa: E712
            ).scalars().all()
            for conn in conns:
                checked += 1
                try:
                    cfg = _decrypt_config(conn.config)
                    broker = get_broker(conn.broker_type, cfg)
                    broker.get_account()  # lightweight health check
                    # Token is valid — ensure is_authorized flag is set
                    if not conn.is_authorized:
                        conn.is_authorized = True
                        s.commit()
                except Exception as _err:
                    err_str = str(_err).lower()
                    if "token_rejected" in err_str or "401" in err_str or "unauthorized" in err_str:
                        expired += 1
                        conn.is_authorized = False
                        s.commit()
                        # Generate a fresh authorize URL and email the user
                        try:
                            cfg2 = _decrypt_config(conn.config)
                            broker2 = get_broker(conn.broker_type, cfg2)
                            auth_url = broker2.start_oauth()
                            # start_oauth() stores request_token into cfg2 — persist it
                            conn.config = _encrypt_config(cfg2)
                            s.commit()
                            # Find the connection owner
                            user = s.get(User, conn.user_id)
                            email = user.email if user else None
                            if email:
                                send_broker_reauth_email(email, conn.name, auth_url)
                                log.info("broker.auth_expired_notified",
                                         conn=conn.name, user=user.username if user else "?")
                        except Exception as _notify_err:
                            log.error("broker.auth_notify_failed", conn=conn.name, error=str(_notify_err))
        elapsed = time.monotonic() - _t0
        _record_job_status("broker_auth_check", "ok", elapsed)
        log.info("broker.auth_check_done", checked=checked, expired=expired, elapsed=round(elapsed, 2))
    except Exception as exc:
        elapsed = time.monotonic() - _t0
        _record_job_status("broker_auth_check", "error", elapsed, str(exc))
        log.error("broker.auth_check_failed", error=str(exc), exc_info=True)


def send_morning_digest(markets: list | None = None) -> None:
    """Compile and email the combined daily digest for all markets — one email per day.

    Sections:
      1. Market regime (SPY / VIX classification from last paper trading step)
      2. Top 5 SWING + Top 5 GROWTH opportunities for each market (HK then US)
      3. Open paper positions (all portfolios, all markets, with yesterday's close P&L)
      4. Pattern alerts triggered since yesterday

    Called once per day at 08:50 HKT (before HKEX open, evening the prior day for ET).
    """
    if markets is None:
        markets = ["HK", "US"]
    _t0 = time.monotonic()
    try:
        regime = get_last_regime()
        date_str = datetime.now(timezone.utc).strftime("%a, %b %-d")

        with SessionLocal() as session:
            # ── Recipients ────────────────────────────────────────────────────
            users = session.execute(
                select(User).where(User.email.isnot(None), User.email != "")
            ).scalars().all()
            if not users:
                log.info("morning_digest.no_recipients")
                return

            # ── Top 5 opportunities per horizon (SWING + GROWTH) ─────────────
            latest_rank_subq = (
                select(Ranking.stock_id, func.max(Ranking.as_of).label("max_as_of"))
                .group_by(Ranking.stock_id)
                .subquery()
            )
            latest_price_subq2 = (
                select(Price.stock_id, func.max(Price.ts).label("max_ts"))
                .where(Price.timeframe == "D1")
                .group_by(Price.stock_id)
                .subquery()
            )

            def _top5_for_horizon(horizon: str, mkt: str) -> list[dict]:
                sig_subq = (
                    select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
                    .where(Signal.horizon == horizon)
                    .group_by(Signal.stock_id)
                    .subquery()
                )
                stmt = (
                    select(Stock, Ranking, Signal)
                    .join(Ranking, Stock.id == Ranking.stock_id)
                    .join(latest_rank_subq,
                          (Ranking.stock_id == latest_rank_subq.c.stock_id) &
                          (Ranking.as_of == latest_rank_subq.c.max_as_of))
                    .outerjoin(sig_subq, Stock.id == sig_subq.c.stock_id)
                    .outerjoin(Signal,
                               (Signal.stock_id == sig_subq.c.stock_id) &
                               (Signal.ts == sig_subq.c.max_ts) &
                               (Signal.horizon == horizon))
                    .where(Stock.active.is_(True), Stock.market == mkt.upper())
                    .order_by(Ranking.score.desc())
                    .limit(50)
                )
                rows = session.execute(stmt).all()
                # Prefer BUY-signal stocks; fall back to highest K-Score
                buy_rows = [r for r in rows if r[2] and r[2].signal == "BUY"]
                other_rows = [r for r in rows if not (r[2] and r[2].signal == "BUY")]
                top_rows = (buy_rows + other_rows)[:5]

                symbols = [r[0].symbol for r in top_rows]
                price_map: dict[str, float] = {}
                if symbols:
                    prows = session.execute(
                        select(Stock.symbol, Price.close)
                        .join(Stock, Price.stock_id == Stock.id)
                        .join(latest_price_subq2,
                              (Price.stock_id == latest_price_subq2.c.stock_id) &
                              (Price.ts == latest_price_subq2.c.max_ts))
                        .where(Stock.symbol.in_(symbols), Price.timeframe == "D1")
                    ).all()
                    price_map = {sym: float(close) for sym, close in prows}

                def _reason_bullets(r: dict) -> list[str]:
                    """Pick up to 3 short reason bullets from a signal reasons dict."""
                    bullets: list[str] = []
                    rsi = r.get("rsi")
                    if rsi is not None:
                        rv = float(rsi)
                        label = "oversold" if rv <= 35 else "momentum" if rv >= 65 else ""
                        bullets.append(f"RSI {rv:.0f}" + (f" — {label}" if label else ""))
                    if r.get("sma50_above_sma200") and r.get("trend_above_sma50"):
                        bullets.append("Uptrend: above SMA50 + golden cross")
                    elif r.get("trend_above_sma50"):
                        bullets.append("Above SMA50")
                    if r.get("macd_zero_cross_up"):
                        bullets.append("MACD zero-line crossup")
                    elif r.get("macd_rising"):
                        bullets.append("MACD histogram rising")
                    if "double_bottom" in (r.get("active_patterns") or []):
                        bullets.append("Double bottom pattern")
                    if "breakout" in (r.get("active_patterns") or []):
                        bullets.append("Volume breakout")
                    ml_prob = r.get("ml_probability")
                    ml_auc = r.get("ml_test_auc")
                    if ml_prob and float(ml_prob) >= 0.65 and ml_auc and float(ml_auc) >= 0.60:
                        bullets.append(f"ML {float(ml_prob)*100:.0f}% bullish (AUC {float(ml_auc):.2f})")
                    # T174: catalyst conviction bullet (insider/congress)
                    _ins = r.get("insider_score")
                    _cat = r.get("catalyst_score")
                    if _ins is not None and float(_ins) > 60:
                        bullets.append(f"Insider buying (score {float(_ins):.0f})")
                    if _cat is not None and float(_cat) >= 60:
                        bullets.append(f"Catalyst signal (score {float(_cat):.0f})")
                    return bullets[:3]

                result = []
                for stock, ranking, signal in top_rows:
                    ml_prob = None
                    reasons_bullets: list[str] = []
                    days_to_earnings: int | None = None
                    if signal and signal.reasons:
                        try:
                            ml_prob = float(signal.reasons.get("ml_probability") or 0) or None
                        except (TypeError, ValueError):
                            ml_prob = None
                        reasons_bullets = _reason_bullets(signal.reasons)
                        dte = signal.reasons.get("days_to_earnings")
                        if dte is not None:
                            try:
                                days_to_earnings = int(dte)
                            except (TypeError, ValueError):
                                pass
                    result.append({
                        "symbol":           stock.symbol,
                        "name":             stock.name or "",
                        "score":            float(ranking.score) if ranking.score is not None else None,
                        "signal":           signal.signal if signal else None,
                        "confidence":       float(signal.confidence) if signal and signal.confidence is not None else None,
                        "ml_prob":          ml_prob,
                        "sector":           stock.sector or "",
                        "market":           stock.market.value if stock.market else "",
                        "price":            price_map.get(stock.symbol),
                        "reasons_bullets":  reasons_bullets,
                        "days_to_earnings": days_to_earnings,
                    })
                return result

            # ── Per-market opportunity sections ───────────────────────────────
            market_sections: list[dict] = []
            for _mkt in markets:
                market_sections.append({
                    "market": _mkt,
                    "swing": _top5_for_horizon("SWING", _mkt),
                    "growth": _top5_for_horizon("GROWTH", _mkt),
                })

            # ── Open paper positions — all markets combined ────────────────────
            open_trades = (
                session.execute(select(PaperTrade).where(PaperTrade.stage == "open")).scalars().all()
            )

            # Last daily close per symbol
            open_symbols = list({t.symbol for t in open_trades})
            close_map: dict[str, float] = {}
            if open_symbols:
                c_rows = session.execute(
                    select(Stock.symbol, Price.close)
                    .join(Stock, Price.stock_id == Stock.id)
                    .join(latest_price_subq2,
                          (Price.stock_id == latest_price_subq2.c.stock_id) &
                          (Price.ts == latest_price_subq2.c.max_ts))
                    .where(Stock.symbol.in_(open_symbols), Price.timeframe == "D1")
                ).all()
                close_map = {sym: float(close) for sym, close in c_rows}

            # Current SWING signal per open position symbol
            pos_signal_map: dict[str, str] = {}
            if open_symbols:
                pos_sig_subq = (
                    select(Signal.stock_id, func.max(Signal.ts).label("max_ts"))
                    .where(Signal.horizon == "SWING")
                    .group_by(Signal.stock_id)
                    .subquery()
                )
                pos_sig_rows = session.execute(
                    select(Stock.symbol, Signal.signal)
                    .join(pos_sig_subq, Stock.id == pos_sig_subq.c.stock_id)
                    .join(Signal, (Signal.stock_id == pos_sig_subq.c.stock_id) & (Signal.ts == pos_sig_subq.c.max_ts) & (Signal.horizon == "SWING"))
                    .where(Stock.symbol.in_(open_symbols))
                ).all()
                pos_signal_map = {sym: sig for sym, sig in pos_sig_rows}

            # Build open positions list (PaperPortfolio has no user FK — all users see all positions)
            open_positions_all: list[dict] = []
            for trade in open_trades:
                last_price = close_map.get(trade.symbol) or trade.current_price
                pnl_pct = None
                if last_price and trade.entry_price:
                    pnl_pct = (last_price - trade.entry_price) / trade.entry_price * 100
                stop_dist_pct = None
                if last_price and trade.current_stop:
                    stop_dist_pct = (last_price - trade.current_stop) / last_price * 100
                open_positions_all.append({
                    "symbol":        trade.symbol,
                    "entry_price":   float(trade.entry_price),
                    "last_price":    last_price,
                    "pnl_pct":       pnl_pct,
                    "current_stop":  float(trade.current_stop) if trade.current_stop else None,
                    "stop_dist_pct": stop_dist_pct,
                    "hold_days":     trade.hold_days or 0,
                    "current_signal": pos_signal_map.get(trade.symbol),
                })
            open_positions_all.sort(key=lambda p: p.get("pnl_pct") or 0, reverse=True)

            # ── Pattern alerts triggered since yesterday ──────────────────────
            _PATTERN_CONDITIONS = {
                "golden_cross", "macd_bullish_cross",
                "rsi_oversold_bounce", "double_bottom", "breakout",
            }
            yesterday = datetime.now(timezone.utc) - timedelta(hours=28)
            pa_rows = session.execute(
                select(PriceAlert.symbol, PriceAlert.condition)
                .where(
                    PriceAlert.triggered.is_(True),
                    PriceAlert.triggered_at >= yesterday,
                )
                .distinct()
            ).all()
            pattern_alerts = [
                {"symbol": sym, "condition": str(cond.value if hasattr(cond, "value") else cond)}
                for sym, cond in pa_rows
                if str(cond.value if hasattr(cond, "value") else cond) in _PATTERN_CONDITIONS
            ]

        # ── Signal outcomes summary (30d win rate) ───────────────────────────
        signal_performance: dict = {}
        try:
            tok = _service_token()
            _hdrs = {"Authorization": f"Bearer {tok}"} if tok else {}
            _r = httpx.get(
                f"{_settings.signal_engine_url}/signals/outcomes/summary?days=30",
                headers=_hdrs, timeout=8,
            )
            if _r.status_code == 200:
                _sp = _r.json()
                if _sp.get("total", 0) > 0:
                    signal_performance = {
                        "total": _sp.get("total", 0),
                        "win_rate": _sp.get("overall", {}).get("win_rate"),
                        "avg_return_pct": _sp.get("overall", {}).get("avg_return_pct"),
                        "by_horizon": _sp.get("by_horizon", {}),
                        "by_symbol": _sp.get("by_symbol", []),
                    }
        except Exception:
            pass  # non-fatal — digest sends without performance section

        # ── Send one combined email per recipient ─────────────────────────────
        sent = 0
        for user in users:
            ok = send_morning_digest_email(
                to=user.email,
                date_str=date_str,
                regime=regime,
                market_sections=market_sections,
                open_positions=open_positions_all,
                pattern_alerts=pattern_alerts,
                signal_performance=signal_performance,
            )
            if ok:
                sent += 1

        total_opps = sum(len(s["swing"]) + len(s["growth"]) for s in market_sections)
        _record_job_status("morning_digest_combined", "ok", time.monotonic() - _t0)
        log.info("morning_digest.done", markets=markets, sent=sent, recipients=len(users),
                 opportunities=total_opps, positions=len(open_positions_all))

    except Exception as exc:
        log.error("morning_digest.failed", markets=markets, error=str(exc), exc_info=True)
        _record_job_status("morning_digest_combined", "error", time.monotonic() - _t0, str(exc))


def send_paper_portfolio_digest() -> None:
    """Send after-market portfolio digest email to all users with email configured.

    Runs weekdays at 17:00 ET (1h after US close). Covers all active paper
    portfolios. Shows total return, today's closed trades, open positions.
    """
    from datetime import date as _date
    from sqlalchemy import select as _sel, desc as _desc
    from ..db import SessionLocal
    from ..db.models import User, PaperPortfolio, PaperTrade
    _t0 = time.monotonic()
    try:
        with SessionLocal() as session:
            users = session.execute(_sel(User).where(User.email != None, User.email != "")).scalars().all()  # noqa: E711
            sent = 0
            for user in users:
                if not user.email:
                    continue
                portfolios = session.execute(
                    _sel(PaperPortfolio).where(PaperPortfolio.is_active.is_(True))
                ).scalars().all()
                if not portfolios:
                    continue
                for p in portfolios:
                    from ..api.paper_portfolio import _portfolio_risk_metrics
                    from ..db.models import PaperEquityCurve
                    # Summary metrics
                    curve_rows = session.execute(
                        _sel(PaperEquityCurve).where(PaperEquityCurve.portfolio_id == p.id).order_by(PaperEquityCurve.date)
                    ).scalars().all()
                    risk = _portfolio_risk_metrics(curve_rows)
                    open_trades = session.execute(
                        _sel(PaperTrade).where(PaperTrade.portfolio_id == p.id, PaperTrade.stage == "open")
                    ).scalars().all()
                    today_utc_start = datetime.combine(_date.today(), datetime.min.time())
                    closed_today = session.execute(
                        _sel(PaperTrade).where(
                            PaperTrade.portfolio_id == p.id,
                            PaperTrade.stage == "closed",
                            PaperTrade.exit_time >= today_utc_start,
                        ).order_by(_desc(PaperTrade.exit_time))
                    ).scalars().all()
                    # Include market value of open positions (not just cash)
                    positions_value = sum(
                        float(t.current_price or t.entry_price) * float(t.shares)
                        for t in open_trades if t.shares and t.shares > 0
                    )
                    equity = float(p.current_cash) + positions_value
                    total_return_pct = round((equity / float(p.initial_capital) - 1) * 100, 2)
                    total_pnl = round(equity - float(p.initial_capital), 2)
                    today_closed_list = [
                        {"symbol": t.symbol, "pnl": float(t.pnl or 0), "pnl_pct": float(t.pct_return or 0), "exit_reason": t.exit_reason or ""}
                        for t in closed_today
                    ]
                    top_positions = sorted(
                        [{"symbol": t.symbol, "unrealized_pct": round(((t.current_price or t.entry_price) / float(t.entry_price) - 1) * 100, 2) if t.entry_price else 0.0, "style": t.trading_style or ""} for t in open_trades],
                        key=lambda x: abs(x["unrealized_pct"]), reverse=True
                    )
                    ok = send_paper_portfolio_digest_email(
                        to=user.email,
                        portfolio_name=p.name or f"Portfolio #{p.id}",
                        total_return_pct=total_return_pct,
                        total_pnl=total_pnl,
                        open_count=len(open_trades),
                        today_closed=today_closed_list,
                        top_positions=top_positions,
                        sharpe=risk.get("sharpe"),
                    )
                    if ok:
                        sent += 1
        _record_job_status("paper_portfolio_digest", "ok", time.monotonic() - _t0)
        log.info("scheduler.paper_portfolio_digest_done", sent=sent)
    except Exception as exc:
        _record_job_status("paper_portfolio_digest", "error", time.monotonic() - _t0, str(exc))
        log.error("scheduler.paper_portfolio_digest_failed", error=str(exc), exc_info=True)


def start_scheduler() -> None:
    """Register all APScheduler jobs and start the background scheduler.

    Idempotent — safe to call multiple times; only the first call has any effect.
    All jobs are registered with replace_existing=True so a hot-reload
    (docker restart) won't create duplicate jobs.

    Schedule (per market):
      - Open burst  (9:25–9:45):   every 5 min  — prices + rankings + signals
      - Regular hrs (10:00–15:00): every 5 min  — prices + rankings + signals
      - Close burst (15:30–16:15): every 5 min  — prices + rankings + signals
      - Post-close  (16:30):       once         — above + ML retrain
      - 5m ingest   (9:30–16:00): every 5 min  — intraday bars only (US + HK)
      - Weekly full refresh (Sun 16:00 PST): force re-ingest 3 years
        → then tune_all (Optuna, 60 trials/symbol, ~2–4 h, background)
      - DB purge    (Sun 15:00 PST): delete prices_5m + scheduler_jobs >90 days

    Signal and momentum are pure local math (TA + XGBoost), no external API
    cost, so refreshing every 5 min during regular hours is safe and free.
    ML retrain runs only post-close — retraining on intraday data has no value
    since the model learns from daily bar outcomes.
    Hyperparameter tuning runs once on Sunday so each symbol's best params are
    ready for the week ahead; subsequent daily retrains pick them up automatically.

    Job count: 4 US + 4 HK + 2 5m intraday + 1 weekly full refresh + tune_all
               + 2 morning digests (US + HK) + 1 price alert checker + 1 db purge
               + 1 EDGAR 8-K ingest (T208) = 16.
    """
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")

    # ── US Market (America/New_York — DST handled automatically) ────────────

    _JOB_DEFAULTS = dict(max_instances=1, coalesce=True, misfire_grace_time=60)

    # Open burst: 9:25–9:45 every 5 min
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        CronTrigger(hour=9, minute="25,30,35,40,45", day_of_week="mon-fri", timezone="America/New_York"),
        id="us_open_burst", replace_existing=True, **_JOB_DEFAULTS,
    )
    # Regular hours: every 5 min 10:00–15:00
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        OrTrigger([
            CronTrigger(hour="10,11,12,13,14", minute="0,5,10,15,20,25,30,35,40,45,50,55", day_of_week="mon-fri", timezone="America/New_York"),
            CronTrigger(hour=15, minute=0, day_of_week="mon-fri", timezone="America/New_York"),
        ]),
        id="us_intra", replace_existing=True, **_JOB_DEFAULTS,
    )
    # Close burst: 15:30–16:15 every 5 min
    _scheduler.add_job(
        lambda: _refresh_market("US"),
        OrTrigger([
            CronTrigger(hour=15, minute="30,35,40,45,50,55", day_of_week="mon-fri", timezone="America/New_York"),
            CronTrigger(hour=16, minute="0,5,10,15", day_of_week="mon-fri", timezone="America/New_York"),
        ]),
        id="us_close_burst", replace_existing=True, **_JOB_DEFAULTS,
    )
    # Post-close: final bar confirmed + ML retrain
    _scheduler.add_job(
        lambda: _refresh_market("US", post_close=True),
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_post_close", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── HK Market (Asia/Hong_Kong — UTC+8, no DST) ──────────────────────────

    # Open burst: 9:25–9:45 every 5 min
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        CronTrigger(hour=9, minute="25,30,35,40,45", day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_open_burst", replace_existing=True, **_JOB_DEFAULTS,
    )
    # Regular hours: every 5 min 10:00–11:55 and 13:00–15:00 (skip 12:00–13:00 HKEX lunch)
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        OrTrigger([
            CronTrigger(hour="10,11,13,14", minute="0,5,10,15,20,25,30,35,40,45,50,55", day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
            CronTrigger(hour=15, minute=0, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        ]),
        id="hk_intra", replace_existing=True, **_JOB_DEFAULTS,
    )
    # Close burst: 15:30–16:15 every 5 min (HK market closes 16:00, bar settles by 16:15)
    _scheduler.add_job(
        lambda: _refresh_market("HK"),
        OrTrigger([
            CronTrigger(hour=15, minute="30,35,40,45,50,55", day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
            CronTrigger(hour=16, minute="0,5,10,15", day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        ]),
        id="hk_close_burst", replace_existing=True, **_JOB_DEFAULTS,
    )
    # Post-close: final bar confirmed + ML retrain
    _scheduler.add_job(
        lambda: _refresh_market("HK", post_close=True),
        CronTrigger(hour=16, minute=30, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_post_close", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── Weekly full refresh — Sunday 16:00 PST, before HK Monday open ───────
    _scheduler.add_job(
        _weekly_full_refresh,
        CronTrigger(day_of_week="sun", hour=14, minute=0, timezone="America/Los_Angeles"),
        id="weekly_full_refresh", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── 5-minute intraday bars — US market hours ────────────────────────────
    _scheduler.add_job(
        lambda: _refresh_5m("US"),
        CronTrigger(
            hour="9,10,11,12,13,14,15",
            minute="30,35,40,45,50,55,0,5,10,15,20,25",
            day_of_week="mon-fri",
            timezone="America/New_York",
        ),
        id="us_5m_intraday", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── 5-minute intraday bars — HK market hours (skip 12:00–13:00 lunch break)
    _scheduler.add_job(
        lambda: _refresh_5m("HK"),
        CronTrigger(
            hour="9,10,11,13,14,15",
            minute="30,35,40,45,50,55,0,5,10,15,20,25",
            day_of_week="mon-fri",
            timezone="Asia/Hong_Kong",
        ),
        id="hk_5m_intraday", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── Broker auth check — 08:30 ET (1h before NYSE open) ──────────────────
    # Tests all active broker connections. Emails a re-auth link if tokens expired.
    # E*Trade OAuth tokens expire at midnight ET every day.
    _scheduler.add_job(
        _check_broker_auth,
        CronTrigger(hour=8, minute=30, day_of_week="mon-fri", timezone="America/New_York"),
        id="broker_auth_check", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── Morning digest — combined HK + US, one email per user ────────────────
    # 08:50 HKT = before HKEX open. HK data is fresh; US data is prior-day close.
    _scheduler.add_job(
        send_morning_digest,
        CronTrigger(hour=8, minute=50, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="morning_digest_combined", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── Paper portfolio after-market digest — 17:00 ET (1h after US close) ──
    _scheduler.add_job(
        send_paper_portfolio_digest,
        CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone="America/New_York"),
        id="paper_portfolio_digest", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── Price alert checker — every minute ──────────────────────────────────
    _scheduler.add_job(
        check_price_alerts,
        "interval",
        minutes=1,
        id="price_alert_check",
        replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # ── Live price cache refresh — every minute during market hours ──────────
    # Lightweight: one yf.download() bulk call → Redis write. No DB writes,
    # no ranking/signal computation. Keeps the UI price display current
    # between the 5-minute full refresh cycles.
    # Gated to US+HK combined market hours (09:00–17:00 ET or 09:00–17:00 HKT)
    # so the job is a no-op outside trading hours and doesn't burn API quota.
    def _live_price_refresh_job() -> None:
        now_et = datetime.now(ZoneInfo("America/New_York"))
        now_hk = datetime.now(ZoneInfo("Asia/Hong_Kong"))
        weekday = now_et.weekday()  # Mon=0 … Fri=4
        if weekday >= 5:
            return  # weekend
        us_open = now_et.hour >= 9 and now_et.hour < 17
        hk_open = now_hk.hour >= 9 and now_hk.hour < 17
        if us_open or hk_open:
            refresh_live_price_cache()

    _scheduler.add_job(
        _live_price_refresh_job,
        "interval",
        minutes=1,
        id="live_price_cache_refresh",
        replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # ── Tier 86: Self-healing watchdog — daily 06:10 ET ──────────────────────
    # Monitors 14-day rolling win rates per style; auto-tightens thresholds when
    # win rate drops below 38%; relaxes when no signals fire for 7+ days.
    # Writes to Redis stockai:watchdog:{STYLE}:threshold (7-day TTL).
    # Signal generator reads watchdog key before calibrated key — response is immediate.
    def _watchdog_job():
        _post(f"{_settings.signal_engine_url}/signals/watchdog")
    _scheduler.add_job(
        _watchdog_job,
        CronTrigger(hour=6, minute=10, day_of_week="mon-fri", timezone="America/New_York"),
        id="signal_watchdog_daily", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── DB purge — Sunday 15:00 PST (before weekly full refresh) ────────────
    # Deletes prices_5m and scheduler_jobs rows older than 90 days.
    _scheduler.add_job(
        _purge_old_data,
        CronTrigger(day_of_week="sun", hour=15, minute=0, timezone="America/Los_Angeles"),
        id="db_purge_weekly", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── T89: Monthly meta-learning model retrain — 1st Sunday of each month ──
    # Cross-symbol XGBoost trained on signal_outcomes improves as more data accumulates.
    # Fire-and-forget to ml-prediction background task (~5-15 min depending on volume).
    # CronTrigger day="1-7" + day_of_week="sun" = first Sunday of the month.
    _scheduler.add_job(
        _retrain_meta_model,
        CronTrigger(day_of_week="sun", day="1-7", hour=3, minute=0, timezone="UTC"),
        id="meta_model_monthly_retrain", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── T208: EDGAR 8-K filing ingest — daily 17:30 ET (1.5h after US close) ─
    # Fetches recent 8-K filings for all active US stocks from SEC EDGAR.
    # HK stocks are skipped inside _ingest_edgar_8k (no EDGAR coverage).
    # Results stored in sec_filings table; exposed via /events/8k/{symbol}.
    _scheduler.add_job(
        _ingest_edgar_8k,
        CronTrigger(hour=17, minute=30, day_of_week="mon-fri", timezone="America/New_York"),
        id="edgar_8k_ingest_daily", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── T209: HKEX Stock Connect southbound flow ingest — daily 17:00 HKT ───
    # Fetches mainland→HK net buy/sell turnover per HK stock from HKEX public API.
    # Runs 1h after HK close so HKEX has published the day's data.
    # Stored in hk_connect_flows table; exposed via /stocks/hk-connect-flow/{symbol}.
    # Flow summary enriches HK BUY signal reasons with flow_strength and flow_5d_net_hkd.
    _scheduler.add_job(
        _ingest_hk_connect_flows,
        CronTrigger(hour=17, minute=0, day_of_week="mon-fri", timezone="Asia/Hong_Kong"),
        id="hk_connect_flows_daily", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── T220-G: Sector rotation K-Score momentum — Sunday 16:00 ET ──────────
    # Aggregates K-Score by sector (this week vs 4 weeks ago) → Redis 3-day TTL.
    # Signal engine reads stockai:sector_rotation to add sector_momentum to reasons.
    _scheduler.add_job(
        _compute_sector_rotation,
        CronTrigger(day_of_week="sun", hour=16, minute=0, timezone="America/New_York"),
        id="sector_rotation_weekly", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── T220-F: Fundamentals snapshot — Sunday 16:30 ET (after sector_rotation) ─
    # Takes a weekly snapshot of recommendation_mean + growth metrics from the
    # fundamentals table. Used to compute 8-week earnings revision momentum in ML.
    _scheduler.add_job(
        _snapshot_fundamentals,
        CronTrigger(day_of_week="sun", hour=16, minute=30, timezone="America/New_York"),
        id="fundamentals_snapshot_weekly", replace_existing=True, **_JOB_DEFAULTS,
    )

    # ── One-shot startup run to restore conviction/Redis data after restarts ─
    # check_signal_alerts() is normally called by _run_market_refresh() (5×/day).
    # Running it once at startup (60s delay) repopulates Redis without adding a
    # permanent 1-minute schedule that could race with the full market refresh.
    _scheduler.add_job(
        check_signal_alerts,
        "date",
        run_date=datetime.now(timezone.utc) + timedelta(seconds=60),
        id="signal_alert_startup",
        replace_existing=True,
        max_instances=1,
    )

    # WF-2: ensure the default GROWTH paper portfolio exists on startup.
    # Runs regardless of enable_paper_trading so the UI works in local dev.
    try:
        ensure_portfolio_exists()
    except Exception as _ppe:
        log.error("scheduler.ensure_portfolio_failed", error=str(_ppe), exc_info=True)

    _scheduler.start()
    log.info("scheduler.started", jobs=20)
