"""Signal-engine outcome-evaluation / backfill (write) routes.

T233-ARCH-INSERVICE-SPLITS-2 (2026-08-26): outcomes.py's own read-only analytics/reporting
routes (accuracy, rolling accuracy, factor exposure, trade performance, filter audit,
walk-forward backtest, outcomes summary, alpha decay, signal age decay, information
coefficient, factor attribution, gate backtest) were extracted to analytics.py — this file
now holds only the 3 real WRITE/mutation endpoints: the outcome-evaluation job
(evaluate_signal_outcomes, /outcomes/evaluate), the retro-feedback realized-EV backfill
(/backfill_realized_ev), and the bearish-pillars backfill (/backfill_bearish_pillars).
Verbatim extraction from the prior outcomes.py — no logic changes; a bug found here was
already present before this split (see AUD291-SIGNALENGINE-GODFILES-UNEVALUATED for the
evaluation that led to this split, and outcomes.py's own original module docstring for the
T233-ARCH-INSERVICE-SPLITS history this continues).
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.jwt_auth import get_current_username
from db import Price, Signal, SignalHorizon, SignalOutcome, SignalType, Stock, TimeFrame, TuneHistory, get_session

from .signals_shared import (
    _CONF_CAL_CACHE_KEY, _OUTCOME_CENSOR_GRACE_DAYS, _OUTCOME_HOLD_DAYS,
    _OUTCOME_WIN_HURDLE_PCT, _SELL_OUTCOME_HOLD_DAYS,
    _get_redis, _service_token, _settings, log,
)

router = APIRouter(prefix="/signals", tags=["signals"])

# ── SELFIMPROVE-NO-RETRO-FEEDBACK-LOOP: close the loop on tune_history ────────
# Every calibration mechanism (calibrate_ta_weights, calibrate_ml_weight, calibrate_
# conviction_weights, tune_style_profiles, promotion_gate, signal_watchdog) writes a
# tune_history row recording what it predicted a change would do (validation_ev_pct) — but
# nothing ever checked whether a promoted change ACTUALLY helped in the real trading that
# followed. This function is that check: it backfills realized_ev_pct_after on already-
# promoted rows once enough real SignalOutcome data has accumulated after the change.

_RETRO_MIN_SAMPLES = 50  # same statistical floor calibrate_ta_weights' walk-forward search uses
_RETRO_MIN_WAIT_MULTIPLIER = 3  # wait at least 3x the style's own hold_days before checking —
# one hold_days' worth only guarantees ONE trade cycle has closed, not enough samples to trust
# a win rate; 3x gives room for a genuinely useful sample size to accumulate across multiple
# signals landing over that period, without waiting so long the check becomes irrelevant.


def _retro_ev_for(session: Session, style: str, market: str, since: "date") -> dict | None:
    """Win-rate/EV for SignalOutcome rows in (style, market) with entry_date >= since,
    using the exact same formula every other calibration mechanism in this file uses
    (win_rate = wins/n, ev_pct = mean(pct_return) * 100 — see calibrate_ta_weights'
    _stats_at() for the canonical version this mirrors). Returns None if fewer than
    _RETRO_MIN_SAMPLES outcomes are available — not enough to trust yet, try again next run.
    market="ALL" is this table's own documented convention for "don't filter by market"
    (see _record_tune_history's docstring) — NOT a literal Stock.market value to match.
    """
    query = (
        select(SignalOutcome)
        .where(
            SignalOutcome.horizon == SignalHorizon(style),
            SignalOutcome.entry_date >= since,
            SignalOutcome.is_correct.isnot(None),
            SignalOutcome.pct_return.isnot(None),
        )
    )
    if market != "ALL":
        query = query.join(Stock, Stock.id == SignalOutcome.stock_id).where(Stock.market == market)
    rows = session.execute(query).scalars().all()
    if len(rows) < _RETRO_MIN_SAMPLES:
        return None
    wins = sum(1 for o in rows if o.is_correct)
    win_rate = wins / len(rows)
    # BUG233-RETROEV-SIGNMIX (2026-07-31): this function's own comment above already documents
    # that it deliberately pools BOTH directions' outcomes together — but SELL "wins" on a
    # NEGATIVE pct_return (is_correct = ret < -hurdle for SELL, per evaluate_signal_outcomes),
    # so averaging a SELL row's raw pct_return alongside a BUY row's raw pct_return mixes two
    # opposite sign conventions into one meaningless number. Every sibling SELL-aware EV
    # computation in this codebase (calibration.py's outcomes_calibrate_apply/tune_sell_pillars)
    # already negates pct_return for SELL rows before averaging — this is the one site that
    # hadn't. Live-verified against production: the un-negated aggregate flipped sign on 6 of 8
    # style/market slices tested (e.g. overall: -3.23% mixed vs. +0.34% sign-corrected) — this
    # is the app's only retrospective "did a promoted tuning change actually help" ground truth,
    # so a sign error here misleads exactly the check meant to catch mistuned parameters.
    signed_returns = [
        (-o.pct_return if o.signal_direction == "SELL" else o.pct_return) for o in rows
    ]
    ev_pct = (sum(signed_returns) / len(rows)) * 100
    return {"n": len(rows), "win_rate": round(win_rate, 3), "ev_pct": round(ev_pct, 2)}


@router.post("/backfill_realized_ev")
def backfill_realized_ev(
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Backfill realized_ev_pct_after on promoted tune_history rows old enough that real
    SignalOutcome data has accumulated since the change. Safe to re-run — only considers
    rows where realized_ev_pct_after IS NULL, so an already-checked row is never re-touched
    (each row gets exactly one realized-EV verdict, at whatever point it first clears the
    sample floor, not a constantly-shifting rolling number).
    """
    candidates = session.execute(
        select(TuneHistory).where(
            TuneHistory.promoted.is_(True),
            TuneHistory.realized_ev_pct_after.is_(None),
        )
    ).scalars().all()

    checked = 0
    updated = 0
    skipped_too_soon = 0
    skipped_invalid_style = 0
    now = datetime.now(timezone.utc)

    for row in candidates:
        checked += 1
        # BUY's hold_days (_OUTCOME_HOLD_DAYS) is used even for mechanisms that tune
        # SELL-relevant params — it's the longer, more conservative window of the two, and
        # this retro-check aggregates BOTH directions' outcomes together in _retro_ev_for()
        # regardless (a tune_history row's style has no BUY/SELL split of its own), so a
        # single, deliberately-cautious wait period is simpler and safer than trying to pick
        # per-direction. style="ALL" (ml_fusion_weight, market-pooled mechanisms) has no
        # single style's hold_days to use — fall back to the longest window across all
        # styles as the most conservative wait available.
        hold_days = _OUTCOME_HOLD_DAYS.get(row.style, max(_OUTCOME_HOLD_DAYS.values()))

        row_ts = row.ts if row.ts.tzinfo else row.ts.replace(tzinfo=timezone.utc)
        min_wait_until = row_ts + timedelta(days=hold_days * _RETRO_MIN_WAIT_MULTIPLIER)
        if now < min_wait_until:
            skipped_too_soon += 1
            continue

        if row.style == "ALL":
            # style="ALL" rows (ml_fusion_weight and any other market/style-pooled mechanism)
            # have no single SignalHorizon to query against — SignalOutcome.horizon only has
            # real SHORT/SWING/LONG/GROWTH values, no pooled "ALL" concept of its own.
            # Properly supporting this would mean aggregating across all 4 styles' outcomes
            # instead of one — a real, larger follow-up (tracked, not silently ignored),
            # not attempted in this pass since ml_fusion_weight is genuinely a single global
            # parameter and the per-style mechanisms (the majority of tune_history rows) are
            # the ones this closes the loop for.
            skipped_invalid_style += 1
            continue

        stats = _retro_ev_for(session, row.style, row.market, row_ts.date())
        if stats is None:
            continue  # not enough samples yet — try again next run, don't mark checked-forever

        row.realized_ev_pct_after = stats["ev_pct"]
        row.realized_n_after = stats["n"]
        row.realized_checked_at = now
        updated += 1

    session.commit()
    log.info("backfill_realized_ev: checked=%d updated=%d skipped_too_soon=%d skipped_all_style=%d",
              checked, updated, skipped_too_soon, skipped_invalid_style)
    return {
        "checked": checked, "updated": updated,
        "skipped_too_soon": skipped_too_soon, "skipped_all_style": skipped_invalid_style,
    }




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
    # T232-OC6: Stock.delisted selected alongside symbol via the SAME existing join — no new
    # query — so the censoring branch below can distinguish a confirmed delisting from an
    # ordinary permanent price gap (halt, acquisition) without a per-row extra DB hit.
    pending_signals = session.execute(
        select(Signal, Stock.symbol, Stock.delisted)
        .join(Stock, Stock.id == Signal.stock_id)
        .where(
            Signal.signal.in_([SignalType.BUY, SignalType.SELL]),
            Signal.ts <= datetime.combine(cutoff, _time.max),
        )
        .order_by(Signal.ts)
    ).all()

    # Bulk-load D1 prices — always extend window to 20d for INT-8 multi-window
    pending_stock_ids = list({sig.stock_id for sig, _, _ in pending_signals})
    price_min_ts = min((sig.ts for sig, _, _ in pending_signals), default=datetime.now())
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
        """Returns the first (date, close) bar on or after `on_or_after`, or None if none
        exists — INCLUDING when the nearest available bar is too far past `on_or_after` to be
        a legitimate exit/entry fill (AUD261-CENSORING-NEVER-FIRED).

        bisect_left has no upper bound by itself — a symbol with a long ingestion gap that
        later RESUMES would otherwise return the first bar after the gap, potentially months
        later, as if it were a normal, timely price. The caller (evaluate_signal_outcomes'
        exit-price lookup) would then silently score the outcome against that far-future
        price as a clean exit, rather than correctly censoring it via the ALREADY-CORRECT
        grace-window branch a few lines below — that branch only ever triggers on a bare
        None, so it was never reachable for a "resumed after a gap" symbol specifically
        because this function was too permissive about what counts as "found."
        Reuses the SAME _OUTCOME_CENSOR_GRACE_DAYS constant the caller's own grace-window
        censoring branch already uses, so both halves of "is this price recent enough to
        trust" agree on the same threshold.
        """
        bucket = _outcome_price_map.get(stock_id, [])
        if not bucket:
            return None
        dates = [b[0] for b in bucket]
        idx = bisect.bisect_left(dates, on_or_after)
        if idx >= len(bucket):
            return None
        found_date, found_close = bucket[idx]
        if (found_date - on_or_after).days > _OUTCOME_CENSOR_GRACE_DAYS:
            return None
        return found_date, found_close

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
                # AUD232-019: previously swallowed silently into (None, None) — a slow or
                # erroring research-engine permanently blanked research_rec/research_score for
                # this outcome row unless Phase 2's NULL-column backfill happened to retry it
                # later, with no visible symptom until someone noticed a spike in the
                # "no_research" bucket count. Logging this makes a systemic slowdown visible.
                log.warning("outcomes.research_fetch_non200", symbol=symbol, status=_r.status_code)
                result = (None, None)
        except Exception as _rfe:
            log.warning("outcomes.research_fetch_failed", symbol=symbol, error=str(_rfe))
            result = (None, None)
        _research_cache[symbol] = result
        return result

    evaluated, skipped_open, skipped_no_price, censored, failed = 0, 0, 0, 0, 0
    # T243-DQ6: previously one bulk session.commit() at the very end of the whole loop, with
    # no per-signal try/except — a single IntegrityError anywhere (e.g. a duplicate signal_id
    # from an overlapping/retried request; _post() in scheduler.py retries up to 3x on any
    # timeout, including ReadTimeout from a slow run, and this endpoint has no lock against a
    # second overlapping call) silently discarded EVERY new SignalOutcome row accumulated by
    # that entire run, not just the one colliding row — a real, unexplained gap tracked as
    # TUNE-LONG-EVALUATE-BACKLOG matches this exact failure shape. Commit incrementally so a
    # failure only loses the batch since the last checkpoint, and wrap each signal's own work
    # in its own try/except so one bad row can't take down any other row in the same run.
    _COMMIT_EVERY = 25
    _since_commit = 0

    for sig, symbol, is_delisted in pending_signals:
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

        try:
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
                    # T232-OC6 (revisited 2026-07-28, now that Stock.delisted is a real,
                    # confirmed signal per aud14-survivorship — see docs/KNOWN_LIMITATIONS.md):
                    # a confirmed delisting is scored as a real loss (is_correct=False,
                    # BUY-direction thesis failed / SELL-direction thesis vacuously held —
                    # see below) rather than silently excluded from win-rate math. Distinct
                    # skip_reason ("delisted_loss" vs. the ordinary "no_exit_price") keeps this
                    # auditable/reversible and separately filterable from a genuine unknown
                    # price gap (halt, ingestion hole with no confirmed cause) — is_correct
                    # stays NULL for those, unchanged. Only BUY is scored as a loss: a
                    # delisting after a BUY thesis is unambiguously bad (the position would
                    # have been forced to zero or a forced buyout), but a delisting after a
                    # SELL thesis (thesis was "this will fall") is genuinely ambiguous — the
                    # delisting itself doesn't confirm the SELL was right (could be an
                    # unrelated acquisition at a premium) — so SELL rows keep the prior,
                    # conservative NULL/censored behavior rather than guessing a direction.
                    _is_confirmed_delisting = bool(is_delisted) and sig.signal == SignalType.BUY
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
                        is_correct=(False if _is_confirmed_delisting else None),
                        skip_reason=("delisted_loss" if _is_confirmed_delisting else "no_exit_price"),
                    )
                    # AUD250-SIGNALENGINE-ROLLBACK-EXPIRES-IDENTITY-MAP: flush inside a SAVEPOINT
                    # (begin_nested) rather than deferring to the periodic/end-of-loop commit —
                    # any IntegrityError (e.g. a duplicate signal_id from a raced overlapping
                    # request) now surfaces and rolls back immediately, on ONLY this row's
                    # savepoint, without expiring every other Signal object already loaded in
                    # pending_signals (a plain session.rollback() expires the WHOLE identity map
                    # by default, forcing a silent per-attribute re-SELECT on every later
                    # iteration's sig.xxx access — a real N+1 regression, not just this row).
                    with session.begin_nested():
                        session.add(outcome)
                        session.flush()
                    censored += 1
                    evaluated_ids.add(sig.id)
                    evaluated_sighd.add(sighd_key)
                    _since_commit += 1
                else:
                    skipped_open += 1
                    continue
            else:
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
                # AUD250-SIGNALENGINE-ROLLBACK-EXPIRES-IDENTITY-MAP: see the censored branch
                # above for the full rationale — flush inside its own SAVEPOINT so a failure
                # here rolls back only this row, not the whole session's identity map.
                with session.begin_nested():
                    session.add(outcome)
                    session.flush()
                evaluated_ids.add(sig.id)
                evaluated_sighd.add(sighd_key)
                evaluated += 1
                _since_commit += 1

            if _since_commit >= _COMMIT_EVERY:
                session.commit()
                _since_commit = 0
        except Exception as _eval_exc:
            # A failure here (e.g. IntegrityError from a duplicate signal_id if a retried/
            # overlapping request raced this one) previously rolled back EVERY row accumulated
            # by the entire run's single end-of-loop commit, not just this one signal. Roll
            # back just the uncommitted work since the last checkpoint and move on — at most
            # this signal and up to _COMMIT_EVERY-1 already-processed-but-uncommitted signals
            # are affected, not the whole batch.
            session.rollback()
            failed += 1
            log.warning("outcomes.evaluate_signal_failed", signal_id=sig.id, symbol=symbol,
                        horizon=horizon, error=str(_eval_exc))

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

    # AUD232-003: confidence-calibration's Redis cache (1h TTL) previously had no
    # invalidation tied to this endpoint actually writing new/updated rows — it would
    # rebuild every hour from whatever signal_outcomes data existed, self-consistently,
    # with no signal if THIS job silently stopped running (e.g. the jose-missing-library
    # failure pattern already seen multiple times in this repo). Explicitly invalidate
    # whenever real data changed so the next read rebuilds from fresh rows instead of
    # riding out the rest of the TTL on stale ones.
    if evaluated or updated:
        try:
            _get_redis().delete(_CONF_CAL_CACHE_KEY)
        except Exception:
            pass

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
        "failed": failed,
        "updated_windows": updated,
    }





# ── T232-SIG10-SELLGATE: backfill bearish_pillars_active onto resolved SELL outcomes ──────────
# bearish_pillars_active started accumulating in Signal.reasons on 2026-07-21, but `signals` is
# upsert-per-(stock_id, horizon, day) — reasons gets overwritten on every refresh, not preserved
# as a point-in-time snapshot. Live-verified: of 3120 resolved SELL SignalOutcome rows, only 70
# still carry the field (all dated within days of it shipping) — nowhere near enough for a real
# train/validation sweep, and waiting longer doesn't help since older rows keep losing it on
# their NEXT refresh. The only path to real statistical power is recomputing it directly from
# historical Price rows as-of each signal's own date — _ta_score() is a pure function of one
# OHLCV DataFrame (verified: every bearish-pillar input — death_cross_event, di_minus/di_plus,
# macd_line, k_smooth, obv_trend_bullish, bb_pct_b, vol_z — derives from close/high/low/volume
# alone, nothing live/Redis/network-dependent), so this is fully deterministic and reproducible.
_BACKFILL_MIN_BARS = 220  # SMA200 needs 200 bars; a small buffer above that for warmup effects


def _backfill_bearish_pillars_for_stock(
    session: Session, stock_id: int, signal_dates: list,
) -> dict:
    """For one stock, compute bearish_pillars_active as-of each of its own signal_dates,
    using ONLY Price rows with ts <= that date (point-in-time correct — never leaks a later
    bar into an earlier date's computation, the same class of bug SE-F2 already cost this
    repo a 3,808-row rebuild over). One bulk Price fetch per stock, not one query per row.
    """
    from ..generators.signals import _ta_score
    import pandas as pd

    max_date = max(signal_dates)
    rows = session.execute(
        select(Price.ts, Price.open, Price.high, Price.low, Price.close, Price.volume)
        .where(
            Price.stock_id == stock_id,
            Price.timeframe == TimeFrame.D1,
            Price.ts <= datetime.combine(max_date, datetime.max.time()),
        )
        .order_by(Price.ts)
    ).all()
    if not rows:
        return {}

    full_df = pd.DataFrame(
        [{"ts": r.ts, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume} for r in rows]
    )
    full_df["date"] = full_df["ts"].apply(lambda t: t.date() if hasattr(t, "date") else t)

    result: dict = {}
    for sd in signal_dates:
        # Point-in-time slice: every bar up to and including this signal's own date, never
        # anything after it — the same lookahead-safety discipline _lookup_outcome_price()
        # already applies to entry/exit price lookups elsewhere in this file.
        df_upto = full_df[full_df["date"] <= sd]
        if len(df_upto) < _BACKFILL_MIN_BARS:
            continue
        try:
            _, reasons = _ta_score(df_upto.tail(300))
        except Exception:
            continue
        pillars = reasons.get("bearish_pillars_active")
        if pillars is not None:
            result[sd] = int(pillars)
    return result


@router.post("/backfill_bearish_pillars")
def backfill_bearish_pillars(
    limit: int = Query(2000, ge=1, le=20000, description="Max SignalOutcome rows to backfill this call"),
    session: Session = Depends(get_session),
    _: str = Depends(get_current_username),
):
    """Backfill bearish_pillars_active onto resolved SELL SignalOutcome rows missing it, by
    recomputing _ta_score() against historical Price data as-of each signal's own date. Safe
    to re-run — only considers rows where bearish_pillars_active IS NULL, and batches by
    stock_id (one bulk Price fetch per stock covering every one of that stock's outstanding
    signal_dates) rather than one query per row.
    """
    candidates = session.execute(
        select(SignalOutcome.id, SignalOutcome.stock_id, SignalOutcome.signal_date)
        .where(
            SignalOutcome.signal_direction == "SELL",
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.bearish_pillars_active.is_(None),
        )
        .order_by(SignalOutcome.stock_id)
        .limit(limit)
    ).all()

    by_stock: dict[int, list] = {}
    for row in candidates:
        by_stock.setdefault(row.stock_id, []).append(row)

    updated = 0
    skipped_insufficient_history = 0
    for stock_id, stock_rows in by_stock.items():
        dates = [r.signal_date for r in stock_rows]
        computed = _backfill_bearish_pillars_for_stock(session, stock_id, dates)
        for row in stock_rows:
            pillars = computed.get(row.signal_date)
            if pillars is None:
                skipped_insufficient_history += 1
                continue
            outcome_obj = session.get(SignalOutcome, row.id)
            if outcome_obj is not None:
                outcome_obj.bearish_pillars_active = pillars
                updated += 1
    session.commit()

    remaining = session.execute(
        select(func.count()).select_from(SignalOutcome).where(
            SignalOutcome.signal_direction == "SELL",
            SignalOutcome.is_correct.is_not(None),
            SignalOutcome.bearish_pillars_active.is_(None),
        )
    ).scalar_one()

    return {
        "candidates_considered": len(candidates),
        "stocks_processed": len(by_stock),
        "updated": updated,
        "skipped_insufficient_history": skipped_insufficient_history,
        "remaining_unbackfilled_sell_outcomes": remaining,
    }


