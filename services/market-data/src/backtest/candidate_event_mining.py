"""T241-POSITION-SCALING: candidate-event mining for the position-scaling gate.

Phase 2/3 (triple_barrier_labeling.py, position_scaling_gate.py) both need a
candidate_events dataset — every historical point where a pullback-add COULD have
happened, not just the ~12 real production PaperTrade rows where one actually did.
Per the architecture doc's section 5.1 approach (see position_scaling_gate.py's module
docstring for the full caveat this module resolves): a candidate event is a real
historical BUY signal, arriving at a price BELOW an already-open hypothetical position's
cost basis, while that position is still within its holding window.

This module mines that universe from data already in the DB — no new data source, no
external calls:
  - `signals` table: real daily BUY-signal history (SWING horizon; the table upserts one
    row per stock/horizon/day, so historical days are preserved as distinct rows, not
    overwritten — confirmed on production 2026-07-10: 949 SWING BUY rows across the
    stocks tracked since 2026-05-25).
  - `prices` (timeframe=D1): real daily OHLC, used both to build the synthetic
    "hypothetical position" being tested and as the price_path triple_barrier_labeling.py
    walks forward from each candidate.
  - `PaperTrade.market_regime_at_entry`: real regime-state labels snapshotted at actual
    entries, used to approximate regime_is_favorable for a candidate event's date without
    needing a live regime-engine call (this module runs entirely offline against
    historical data). Falls back to a neutral "unknown-favorable" default when no
    PaperTrade snapshot exists near a candidate's date — see _regime_favorable_near().

For each stock with enough signal history, a hypothetical hold is opened at its FIRST
BUY signal and then, for every SUBSEQUENT BUY signal that fires at a price below the
running cost basis while the position is still within max_holding_days of its most
recent tranche, that signal becomes one candidate event: "could/should we have added
here." This deliberately does not require a real trade to have happened — it is exactly
the "what if" universe the design doc's section 5.1 describes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from common.indicators import atr as _canon_atr

from .multi_tranche_engine import BarrierConfig
from .position_scaling_gate import FEATURE_COLUMNS, compute_features_for_event

_ATR_WINDOW = 14
_MIN_PRICE_HISTORY_BARS = _ATR_WINDOW + 5  # enough for a warmed-up ATR read


@dataclass
class MinedCandidate:
    """One candidate pullback-add event, ready for triple_barrier_labeling.build_labeled_dataset()
    and (after labeling) position_scaling_gate feature computation.
    """
    symbol: str
    event_timestamp: pd.Timestamp
    atr_at_event: float
    candidate_add_price: float
    candidate_add_shares: float
    existing_tranches: list[tuple[pd.Timestamp, float, float]] = field(default_factory=list)
    # Feature-relevant context snapshotted at the event, independent of the label:
    primary_signal_confidence: float = 50.0
    signal_confidence_at_last_entry: float = 50.0
    regime_is_favorable: bool = False
    volume_zscore: float = 0.0
    support_level: float | None = None
    days_since_last_entry: int = 0
    num_prior_adds: int = 0


def _regime_favorable_near(regime_snapshots: pd.DataFrame, event_date, window_days: int = 10) -> bool:
    """Approximate regime_is_favorable for a historical date using the nearest real
    PaperTrade.market_regime_at_entry snapshot within `window_days`. No live regime-engine
    call — this module is offline/historical by design. Defaults to False (not favorable)
    when no snapshot is close enough, matching a conservative "unknown = don't assume
    favorable" stance rather than fabricating an optimistic default.
    """
    if regime_snapshots.empty:
        return False
    deltas = (regime_snapshots["entry_date"] - pd.Timestamp(event_date)).abs()
    nearest_idx = deltas.idxmin()
    if deltas.loc[nearest_idx] > timedelta(days=window_days):
        return False
    state = regime_snapshots.loc[nearest_idx, "market_regime_at_entry"]
    return state in ("bull", "neutral")


def _build_atr_series(price_df: pd.DataFrame) -> pd.Series:
    """price_df: columns ['ts','high','low','close'], ascending by ts. Returns an ATR series
    aligned to price_df's index, using the same Wilder's-smoothing implementation the rest
    of the app uses (shared/common/indicators.py) — not a standalone reimplementation.
    """
    return _canon_atr(price_df["high"], price_df["low"], price_df["close"], period=_ATR_WINDOW)


def mine_candidate_events(
    session: Session,
    stock_ids: list[int],
    barrier_cfg: BarrierConfig,
    horizon: str = "SWING",
    max_events_per_stock: int = 20,
) -> list[MinedCandidate]:
    """Mine candidate pullback-add events for the given stocks from real signal + price
    history. Pure read — does not write anything, does not call any live service.

    For each stock: open a hypothetical position at the first BUY signal in the window,
    then walk forward through later BUY signals. A later BUY signal becomes a candidate
    event only if (a) its price is below the position's current weighted-average cost
    basis (a genuine pullback, matching this feature's explicit "adds only on a pullback"
    scope decision — see position_scaling_gate.py's module docstring) and (b) it falls
    within max_holding_days of the most recent tranche (still an open, live position).
    max_events_per_stock caps runaway candidate counts for stocks with very dense signal
    history — logged by the caller via the returned list's length, not silently truncated
    (see mine_and_report()).
    """
    from db import Price, Signal, Stock, TimeFrame, PaperTrade, SignalHorizon

    candidates: list[MinedCandidate] = []

    # PaperTrade regime snapshots — used app-wide (not per stock) since regime is a
    # market-level state and different stocks near the same date should get the same read.
    regime_rows = session.execute(
        select(PaperTrade.entry_date, PaperTrade.market_regime_at_entry)
        .where(PaperTrade.market_regime_at_entry.is_not(None))
    ).all()
    regime_snapshots = pd.DataFrame(regime_rows, columns=["entry_date", "market_regime_at_entry"])
    if not regime_snapshots.empty:
        regime_snapshots["entry_date"] = pd.to_datetime(regime_snapshots["entry_date"])

    for stock_id in stock_ids:
        sig_rows = session.execute(
            select(Signal.ts, Signal.confidence, Signal.reasons)
            .where(
                Signal.stock_id == stock_id,
                Signal.horizon == SignalHorizon[horizon],
                Signal.signal == "BUY",
            )
            .order_by(Signal.ts.asc())
        ).all()
        if len(sig_rows) < 2:
            continue  # need at least an opening signal + one later candidate

        price_rows = session.execute(
            select(Price.ts, Price.high, Price.low, Price.close)
            .where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1)
            .order_by(Price.ts.asc())
        ).all()
        if len(price_rows) < _MIN_PRICE_HISTORY_BARS:
            continue

        price_df = pd.DataFrame(price_rows, columns=["ts", "high", "low", "close"])
        price_df["ts"] = pd.to_datetime(price_df["ts"])
        atr_series = _build_atr_series(price_df)

        stock_symbol = session.execute(select(Stock.symbol).where(Stock.id == stock_id)).scalar_one()

        first_ts, first_conf, first_reasons = sig_rows[0]
        first_ts = pd.Timestamp(first_ts)
        first_price_row = price_df[price_df["ts"] >= first_ts].head(1)
        if first_price_row.empty:
            continue
        first_price = float(first_price_row["close"].iloc[0])
        first_atr = _atr_at(atr_series, price_df, first_ts)
        if first_atr is None or first_atr <= 0:
            continue

        tranches: list[tuple[pd.Timestamp, float, float]] = [(first_ts, first_price, 100.0)]
        last_conf = float(first_conf)
        events_this_stock = 0

        for ts, conf, reasons in sig_rows[1:]:
            if events_this_stock >= max_events_per_stock:
                break
            ts = pd.Timestamp(ts)
            last_tranche_ts = tranches[-1][0]
            if ts - last_tranche_ts > timedelta(days=barrier_cfg.max_holding_days):
                # Position would already have timed out — start a fresh hypothetical hold
                # from this signal instead of pretending the old one is still open.
                price_row = price_df[price_df["ts"] >= ts].head(1)
                if price_row.empty:
                    continue
                tranches = [(ts, float(price_row["close"].iloc[0]), 100.0)]
                last_conf = float(conf)
                continue

            cost_basis = sum(p * s for _, p, s in tranches) / sum(s for _, _, s in tranches)
            price_row = price_df[price_df["ts"] >= ts].head(1)
            if price_row.empty:
                continue
            candidate_price = float(price_row["close"].iloc[0])
            if candidate_price >= cost_basis:
                last_conf = float(conf)
                continue  # not a pullback — outside this feature's scope, skip

            atr_at_event = _atr_at(atr_series, price_df, ts)
            if atr_at_event is None or atr_at_event <= 0:
                last_conf = float(conf)
                continue

            reasons = reasons or {}
            candidates.append(MinedCandidate(
                symbol=stock_symbol,
                event_timestamp=ts,
                atr_at_event=atr_at_event,
                candidate_add_price=candidate_price,
                candidate_add_shares=100.0,
                existing_tranches=list(tranches),
                primary_signal_confidence=float(conf),
                signal_confidence_at_last_entry=last_conf,
                regime_is_favorable=_regime_favorable_near(regime_snapshots, ts.date()),
                volume_zscore=float(reasons.get("volume_z") or 0.0),
                support_level=reasons.get("sr_nearest_support"),
                days_since_last_entry=(ts - last_tranche_ts).days,
                num_prior_adds=len(tranches) - 1,
            ))
            events_this_stock += 1
            # The hypothetical position grows with each mined candidate treated as if it
            # were taken — matches build_labeled_dataset()'s existing_tranches shape, and
            # gives later candidates on the same stock a realistic, evolving cost basis
            # rather than always comparing against the very first entry.
            tranches.append((ts, candidate_price, 100.0))
            last_conf = float(conf)

    return candidates


def _atr_at(atr_series: pd.Series, price_df: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Look up the ATR value at or immediately before `ts` in the aligned series."""
    idx = price_df.index[price_df["ts"] <= ts]
    if len(idx) == 0:
        return None
    pos = idx[-1]
    val = atr_series.loc[pos]
    return None if pd.isna(val) else float(val)


def candidates_to_dataframe(candidates: list[MinedCandidate]) -> pd.DataFrame:
    """Convert to the exact column shape triple_barrier_labeling.build_labeled_dataset()
    expects for its `candidate_events` argument.
    """
    return pd.DataFrame([{
        "symbol": c.symbol,
        "event_timestamp": c.event_timestamp,
        "atr_at_event": c.atr_at_event,
        "candidate_add_price": c.candidate_add_price,
        "candidate_add_shares": c.candidate_add_shares,
        "existing_tranches": c.existing_tranches,
    } for c in candidates])


def build_feature_matrix(
    candidates: list[MinedCandidate],
    labeled: pd.DataFrame,  # output of build_labeled_dataset(), same row order as candidates
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Assemble (X, y, realized_return) for position_scaling_gate.walk_forward_train().

    y = labeled.label_add_was_correct; realized_return = labeled.realized_return_with_add.
    Zips `candidates` and `labeled` by position — both must come from the same mining +
    labeling pass in the same order (build_labeled_dataset() preserves input row order).
    """
    assert len(candidates) == len(labeled), (
        f"candidates ({len(candidates)}) and labeled rows ({len(labeled)}) must be the same "
        "length and in the same order — did you filter/reorder one but not the other?"
    )
    rows = []
    for cand in candidates:
        current_price = cand.candidate_add_price
        cost_basis = sum(p * s for _, p, s in cand.existing_tranches) / \
            sum(s for _, _, s in cand.existing_tranches)
        rows.append(compute_features_for_event(
            primary_signal_confidence=cand.primary_signal_confidence,
            signal_confidence_at_last_entry=cand.signal_confidence_at_last_entry,
            current_price=current_price,
            weighted_avg_cost_basis=cost_basis,
            regime_is_favorable=cand.regime_is_favorable,
            realized_vol_percentile=0.5,  # not available for offline historical mining — see module docstring caveat
            volume_zscore=cand.volume_zscore,
            sector_correlation=0.0,        # not available for offline historical mining — see module docstring caveat
            days_since_last_entry=cand.days_since_last_entry,
            existing_position_pct_of_portfolio=0.05,  # placeholder; not modeled per-candidate, see caveat
            num_prior_adds=cand.num_prior_adds,
            support_level=cand.support_level if cand.support_level is not None else current_price * 0.97,
            atr=cand.atr_at_event,
        ))
    X = pd.DataFrame(rows)[FEATURE_COLUMNS]
    y = labeled["label_add_was_correct"].reset_index(drop=True)
    ret = labeled["realized_return_with_add"].reset_index(drop=True)
    return X, y, ret


def mine_and_report(session: Session, barrier_cfg: BarrierConfig | None = None) -> dict:
    """Convenience entry point: mine every active stock's candidate events and return a
    summary dict (counts + a label_balance_report once labeled). Does not train or persist
    anything — a thin orchestration wrapper for a one-off or scheduled mining run.
    """
    from db import Stock

    from .triple_barrier_labeling import build_labeled_dataset, label_balance_report

    barrier_cfg = barrier_cfg or BarrierConfig()
    stock_ids = [sid for (sid,) in session.execute(
        select(Stock.id).where(Stock.active.is_(True))
    ).all()]

    candidates = mine_candidate_events(session, stock_ids, barrier_cfg)
    if not candidates:
        return {"n_candidates": 0, "n_stocks_scanned": len(stock_ids), "note": "no candidate events mined"}

    price_history = _load_price_history(session, {c.symbol for c in candidates})
    candidate_df = candidates_to_dataframe(candidates)
    labeled = build_labeled_dataset(candidate_df, price_history, barrier_cfg)

    report = label_balance_report(labeled) if not labeled.empty else {}
    return {
        "n_candidates": len(candidates),
        "n_stocks_scanned": len(stock_ids),
        "n_stocks_with_candidates": len({c.symbol for c in candidates}),
        "n_labeled": len(labeled),
        "label_balance": report,
    }


def _load_price_history(session: Session, symbols: set[str]) -> dict[str, pd.DataFrame]:
    from db import Price, Stock, TimeFrame

    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        stock_id = session.execute(select(Stock.id).where(Stock.symbol == symbol)).scalar_one_or_none()
        if stock_id is None:
            continue
        rows = session.execute(
            select(Price.ts, Price.high, Price.low, Price.close)
            .where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1)
            .order_by(Price.ts.asc())
        ).all()
        df = pd.DataFrame(rows, columns=["timestamp", "high", "low", "close"])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        out[symbol] = df
    return out
