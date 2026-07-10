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

FEATURE COMPLETENESS (2026-07-10 follow-up): the first version of this module fed
position_scaling_gate two of its 11 features as hardcoded constants (realized_vol_percentile
always 0.5, sector_correlation always 0.0) with a "not available for offline mining" note.
A constant feature has zero variance and can never be split on by a gradient-boosted tree —
that dead weight has to go somewhere, and a real-data smoke test showed it landing on
current_drawdown_pct (45% importance), which fails position_scaling_gate.py's own sign-off
check for "did the model just re-learn naive averaging down." Both are now computed for
real: realized_vol_percentile from each stock's own trailing 20d realized-vol history,
percentile-ranked against its trailing 1yr distribution (see
_build_realized_vol_percentile_series); sector_correlation from the correlation between the
stock's trailing 60d returns and its sector peers' average returns over the same window (see
_sector_correlation_at/_sector_peer_returns). existing_position_pct_of_portfolio remains a
placeholder (0.05) — it is inherently unknowable for a hypothetical mined position with no
real portfolio sizing context, unlike the other two which were just uncomputed, not
uncomputable.

SAMPLE SIZE (2026-07-10, later same day): fixing the two placeholders above only reduced
current_drawdown_pct's dominance to 38.5% (from 45%) at the original SWING-only mining scope
(236 events). A diagnostic investigation (barrier-width sweep, richer raw features like
bullish_probability/RSI/thesis-staleness) found that at 236 events, every non-drawdown
feature combined scored AUC ~0.50-0.55 (barely above chance) regardless of what was tried —
not a feature-engineering problem, a SAMPLE SIZE problem. Mining across all 4 horizons this
app tracks (SWING/SHORT/LONG/GROWTH are independently-computed real BUY-signal streams, not
duplicates of one signal — see signals.py's _STYLE_PROFILES) instead of SWING alone produced
1213 events across 111 stocks, at which point the other features finally showed real,
walk-forward-validated predictive power (all-features AUC 0.936 vs. drawdown-alone 0.888;
current_drawdown_pct's importance fell to 36%). mine_all_horizons()/mine_and_report() now
mine all 4 horizons by default — see the T241-MINING-ALLHORIZONS note below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

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
    realized_vol_percentile: float = 0.5
    sector_correlation: float = 0.0


def _regime_favorable_near(regime_snapshots: pd.DataFrame, event_date, window_days: int = 10) -> bool:
    """Approximate regime_is_favorable for a historical date using the nearest real
    PaperTrade.market_regime_at_entry snapshot AT OR BEFORE that date, within `window_days`.
    No live regime-engine call — this module is offline/historical by design. Defaults to
    False (not favorable) when no qualifying snapshot exists, matching a conservative
    "unknown = don't assume favorable" stance rather than fabricating an optimistic default.

    T241-AUDIT-WALKFORWARD-VALIDITY (found 2026-07-10 via audit): this previously used
    `.abs()` on the date delta, which let a regime snapshot recorded UP TO window_days AFTER
    the event be used to fill in that event's regime feature. Market regime in the days
    following an event correlates with the event's own forward return — the very thing
    being predicted — so using a future snapshot leaked outcome information into a training
    feature. It also created a train/live mismatch: compute_live_features_for_position()
    always uses the CURRENT regime (never a future one), so the offline training feature and
    the live inference feature were computed under different rules. Restricted to
    on-or-before snapshots only, matching what's actually knowable at the event's own time.
    """
    if regime_snapshots.empty:
        return False
    event_ts = pd.Timestamp(event_date)
    eligible = regime_snapshots[regime_snapshots["entry_date"] <= event_ts]
    if eligible.empty:
        return False
    deltas = event_ts - eligible["entry_date"]
    nearest_idx = deltas.idxmin()
    if deltas.loc[nearest_idx] > timedelta(days=window_days):
        return False
    state = eligible.loc[nearest_idx, "market_regime_at_entry"]
    return state in ("bull", "neutral")


def _build_atr_series(price_df: pd.DataFrame) -> pd.Series:
    """price_df: columns ['ts','high','low','close'], ascending by ts. Returns an ATR series
    aligned to price_df's index, using the same Wilder's-smoothing implementation the rest
    of the app uses (shared/common/indicators.py) — not a standalone reimplementation.
    """
    return _canon_atr(price_df["high"], price_df["low"], price_df["close"], period=_ATR_WINDOW)


_REALIZED_VOL_WINDOW = 20     # trailing window for the "current" realized vol reading
_REALIZED_VOL_LOOKBACK_DAYS = 365  # history window the percentile is ranked against
_SECTOR_CORR_WINDOW = 60      # trailing days of returns used for the correlation


def _build_realized_vol_percentile_series(price_df: pd.DataFrame) -> pd.Series:
    """Rolling 20d realized vol (std of daily log returns), then each value's percentile
    rank against its own trailing 1yr history — a genuinely regime-aware measure of "is
    this stock unusually volatile right now for ITSELF," not a fixed cross-sectional cutoff.
    Returns a series aligned to price_df's index; NaN until enough history exists for a
    full lookback window.
    """
    returns = np.log(price_df["close"] / price_df["close"].shift(1))
    realized_vol = returns.rolling(_REALIZED_VOL_WINDOW).std()

    def _pct_rank(window: pd.Series) -> float:
        current = window.iloc[-1]
        if pd.isna(current):
            return np.nan
        history = window.dropna()
        if len(history) < _REALIZED_VOL_WINDOW:  # not enough resolved vol readings yet
            return np.nan
        return float((history <= current).mean())

    return realized_vol.rolling(_REALIZED_VOL_LOOKBACK_DAYS, min_periods=_REALIZED_VOL_WINDOW * 2).apply(
        _pct_rank, raw=False,
    )


def _sector_peer_returns(session: Session, sector: str | None, exclude_stock_id: int | None = None) -> pd.DataFrame:
    """Daily close-to-close returns for active stocks in the same sector, wide format
    (columns = stock_id, index = ts). Used to build a same-sector "peer average" return
    series for sector_correlation. Returns an empty DataFrame if sector is None or has no
    members with price history — callers must handle that (see _sector_correlation_at).

    T241-AUDIT-WALKFORWARD-VALIDITY (found 2026-07-10 via audit): exclude_stock_id is now
    OPTIONAL and, when the caller is going to cache this result across multiple stocks in
    the same sector (as mine_candidate_events() does — see _peer_returns_cache), should be
    left as None so the cached DataFrame contains every sector member. The self-exclusion
    then happens per-stock at USE time (see _sector_correlation_at's exclude_stock_id param)
    instead of being baked into a shared, per-sector cache entry. Baking the exclusion in at
    cache-build time was the actual bug: only the FIRST stock processed in a sector correctly
    excluded itself — every subsequent same-sector stock reused that same cached DataFrame,
    which still excluded only the first stock, meaning its OWN returns were included in its
    own "peer average" and inflated its sector_correlation reading.
    """
    from db import Price, Stock, TimeFrame

    if not sector:
        return pd.DataFrame()
    query = select(Stock.id).where(Stock.sector == sector, Stock.active.is_(True))
    if exclude_stock_id is not None:
        query = query.where(Stock.id != exclude_stock_id)
    peer_ids = [sid for (sid,) in session.execute(query).all()]
    if not peer_ids:
        return pd.DataFrame()

    rows = session.execute(
        select(Price.stock_id, Price.ts, Price.close)
        .where(Price.stock_id.in_(peer_ids), Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.asc())
    ).all()
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows, columns=["stock_id", "ts", "close"])
    df["ts"] = pd.to_datetime(df["ts"])
    wide = df.pivot_table(index="ts", columns="stock_id", values="close")
    return wide.pct_change(fill_method=None)


def _sector_correlation_at(
    stock_returns: pd.Series,  # daily pct-change returns, indexed by ts, for the stock itself
    peer_returns: pd.DataFrame,  # wide daily returns for sector peers, indexed by ts (columns = stock_id)
    ts: pd.Timestamp,
    exclude_stock_id: int | None = None,
) -> float:
    """Correlation between the stock's own trailing-window daily returns and its sector
    peers' AVERAGE daily returns over the same window, ending at (or just before) `ts`. High
    correlation means the stock's recent moves track its sector closely (a sector-wide move,
    not idiosyncratic); low/negative correlation means the stock is moving independently of
    its sector. Returns 0.0 (no evidence either way) if there isn't enough peer data —
    matches compute_features_for_event's existing "0.0 = no signal" convention for this field.

    exclude_stock_id: if peer_returns was built from an UNFILTERED per-sector cache (every
    sector member's column, not pre-excluded — see _sector_peer_returns), pass the current
    stock's own id here to drop its column before averaging, so a stock never counts its own
    returns as part of its own "peer average." Safe to omit if peer_returns was already
    built with that stock excluded at query time.
    """
    if peer_returns.empty:
        return 0.0
    if exclude_stock_id is not None and exclude_stock_id in peer_returns.columns:
        peer_returns = peer_returns.drop(columns=[exclude_stock_id])
    if peer_returns.empty:
        return 0.0
    peer_avg = peer_returns.mean(axis=1)
    window_end = stock_returns.index[stock_returns.index <= ts]
    if len(window_end) < _SECTOR_CORR_WINDOW:
        return 0.0
    window_idx = window_end[-_SECTOR_CORR_WINDOW:]
    stock_window = stock_returns.loc[window_idx]
    peer_window = peer_avg.reindex(window_idx)
    aligned = pd.concat([stock_window, peer_window], axis=1).dropna()
    if len(aligned) < _SECTOR_CORR_WINDOW // 2:  # need a real majority of the window, not a handful of points
        return 0.0
    corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
    return float(corr) if not pd.isna(corr) else 0.0


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

    # Sector peer-return series are expensive to build (a DB query + pivot per sector) and
    # are shared by every stock in that sector — cache per sector rather than recomputing
    # once per stock. Cached UNFILTERED (every sector member's column, no exclusion) so the
    # same cache entry is valid for every stock in the sector; each stock's OWN column is
    # dropped at use time via _sector_correlation_at's exclude_stock_id, not baked into the
    # cached DataFrame — see _sector_peer_returns' docstring for the bug this fixes.
    _peer_returns_cache: dict[str, pd.DataFrame] = {}

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
        vol_pctile_series = _build_realized_vol_percentile_series(price_df)
        stock_returns = price_df.set_index("ts")["close"].pct_change()

        stock_symbol, stock_sector = session.execute(
            select(Stock.symbol, Stock.sector).where(Stock.id == stock_id)
        ).one()
        if stock_sector not in _peer_returns_cache:
            _peer_returns_cache[stock_sector] = _sector_peer_returns(session, stock_sector)
        peer_returns = _peer_returns_cache[stock_sector]

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
            vol_pctile = _series_at(vol_pctile_series, price_df, ts)
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
                realized_vol_percentile=vol_pctile if vol_pctile is not None else 0.5,
                sector_correlation=_sector_correlation_at(stock_returns, peer_returns, ts, exclude_stock_id=stock_id),
            ))
            events_this_stock += 1
            # The hypothetical position grows with each mined candidate treated as if it
            # were taken — matches build_labeled_dataset()'s existing_tranches shape, and
            # gives later candidates on the same stock a realistic, evolving cost basis
            # rather than always comparing against the very first entry.
            tranches.append((ts, candidate_price, 100.0))
            last_conf = float(conf)

    return candidates


def _series_at(series: pd.Series, price_df: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Look up a price_df-aligned series' value at or immediately before `ts`. Generic
    lookup shared by ATR, realized-vol-percentile, and any other per-bar series computed
    over the same price_df — not specific to any one indicator.
    """
    idx = price_df.index[price_df["ts"] <= ts]
    if len(idx) == 0:
        return None
    pos = idx[-1]
    val = series.loc[pos]
    return None if pd.isna(val) else float(val)


def _atr_at(atr_series: pd.Series, price_df: pd.DataFrame, ts: pd.Timestamp) -> float | None:
    """Look up the ATR value at or immediately before `ts` in the aligned series."""
    return _series_at(atr_series, price_df, ts)


def compute_live_features_for_position(
    session: Session,
    stock_id: int,
    symbol: str,
    sector: str | None,
    current_price: float,
    weighted_avg_cost_basis: float,
    primary_signal_confidence: float,
    signal_confidence_at_last_entry: float,
    regime_is_favorable: bool,
    volume_zscore: float,
    support_level: float | None,
    days_since_last_entry: int,
    existing_position_pct_of_portfolio: float,
    num_prior_adds: int,
) -> pd.Series | None:
    """T241 Phase 5: the LIVE counterpart to build_feature_matrix() — assembles one feature
    row for an already-open position, right now, instead of a historical candidate event.
    Reuses the exact same realized_vol_percentile/sector_correlation/ATR computations the
    offline mining module uses (_build_realized_vol_percentile_series, _sector_correlation_at,
    _sector_peer_returns, _series_at) so a live shadow-mode prediction and an offline-mined
    training example are computed identically — no second, drifting implementation of the
    same math.

    Returns None if there isn't enough real price history to compute a trustworthy ATR/vol
    reading (mirrors mine_candidate_events()'s own _MIN_PRICE_HISTORY_BARS gate) — callers
    must treat None as "skip this candidate for now," not silently substitute a placeholder.
    """
    from db import Price, TimeFrame

    price_rows = session.execute(
        select(Price.ts, Price.high, Price.low, Price.close)
        .where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.asc())
    ).all()
    if len(price_rows) < _MIN_PRICE_HISTORY_BARS:
        return None

    price_df = pd.DataFrame(price_rows, columns=["ts", "high", "low", "close"])
    price_df["ts"] = pd.to_datetime(price_df["ts"])
    now = price_df["ts"].iloc[-1]  # most recent bar as-of time; live callers have no future data anyway

    atr_series = _build_atr_series(price_df)
    atr = _series_at(atr_series, price_df, now)
    if atr is None or atr <= 0:
        return None

    vol_pctile_series = _build_realized_vol_percentile_series(price_df)
    vol_pctile = _series_at(vol_pctile_series, price_df, now)

    stock_returns = price_df.set_index("ts")["close"].pct_change()
    peer_returns = _sector_peer_returns(session, sector, stock_id)
    sector_corr = _sector_correlation_at(stock_returns, peer_returns, now)

    return compute_features_for_event(
        primary_signal_confidence=primary_signal_confidence,
        signal_confidence_at_last_entry=signal_confidence_at_last_entry,
        current_price=current_price,
        weighted_avg_cost_basis=weighted_avg_cost_basis,
        regime_is_favorable=regime_is_favorable,
        realized_vol_percentile=vol_pctile if vol_pctile is not None else 0.5,
        volume_zscore=volume_zscore,
        sector_correlation=sector_corr,
        days_since_last_entry=days_since_last_entry,
        existing_position_pct_of_portfolio=existing_position_pct_of_portfolio,
        num_prior_adds=num_prior_adds,
        support_level=support_level if support_level is not None else current_price * 0.97,
        atr=atr,
    )


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
            realized_vol_percentile=cand.realized_vol_percentile,
            volume_zscore=cand.volume_zscore,
            sector_correlation=cand.sector_correlation,
            days_since_last_entry=cand.days_since_last_entry,
            existing_position_pct_of_portfolio=0.05,  # inherently unknowable for a hypothetical mined position — see module docstring caveat
            num_prior_adds=cand.num_prior_adds,
            support_level=cand.support_level if cand.support_level is not None else current_price * 0.97,
            atr=cand.atr_at_event,
        ))
    X = pd.DataFrame(rows)[FEATURE_COLUMNS]
    y = labeled["label_add_was_correct"].reset_index(drop=True)
    ret = labeled["realized_return_with_add"].reset_index(drop=True)
    return X, y, ret


# T241-MINING-ALLHORIZONS: mining a single horizon (originally SWING only) produced only
# 236 candidate events — enough to run the pipeline end-to-end, but too few for the model
# to reliably learn anything beyond current_drawdown_pct (verified 2026-07-10: at 236
# events, every non-drawdown feature combined scored AUC ~0.50-0.55, barely above chance,
# regardless of feature engineering or barrier-width tuning). Mining all 4 horizons this
# app tracks (SWING/SHORT/LONG/GROWTH — each is a real, independently-computed BUY-signal
# stream per signals.py's _STYLE_PROFILES, not a duplicate view of the same signal) with a
# higher per-stock cap produced 1213 events across 111 stocks — at that sample size,
# regime/sector/volatility features finally show real, walk-forward-validated predictive
# power (all-features AUC 0.936 vs. drawdown-alone 0.888; current_drawdown_pct's feature
# importance share fell to 36% from 45%, no longer dominant). This is now the mining
# default; the original SWING-only single-horizon path remains available via
# mine_candidate_events() directly for callers that want to scope to one horizon.
_ALL_MINING_HORIZONS = ["SWING", "SHORT", "LONG", "GROWTH"]
_DEFAULT_MAX_EVENTS_PER_STOCK = 50


def mine_all_horizons(
    session: Session,
    stock_ids: list[int],
    barrier_cfg: BarrierConfig,
    horizons: list[str] | None = None,
    max_events_per_stock: int = _DEFAULT_MAX_EVENTS_PER_STOCK,
) -> list[MinedCandidate]:
    """Mine candidate events across every trading horizon (SWING/SHORT/LONG/GROWTH by
    default) and concatenate them — see the module-level note above for why this is the
    default over mining a single horizon.

    T241-AUDIT-WALKFORWARD-VALIDITY (found 2026-07-10 via audit): mine_candidate_events()
    returns each horizon's candidates in per-stock chronological order, but concatenating
    horizon-by-horizon (all of SWING for every stock, then all of SHORT, ...) produces a
    list that is horizon-major / stock-major, NOT chronological overall.
    position_scaling_gate.walk_forward_train() explicitly documents that its caller must
    pass chronologically-sorted data — "this function does not re-sort" — but no caller
    ever sorted it, so every walk-forward "train" fold could contain events that occurred
    chronologically AFTER events in the "validation" fold (real temporal leakage), and the
    same stock/date pair mined under multiple horizons could land as near-duplicate rows
    on both sides of a split. Sorting here, at the single point every training caller goes
    through, fixes both training entry points (train_and_save_position_scaling_gate and any
    future caller) without requiring each one to remember to sort itself.
    """
    horizons = horizons or _ALL_MINING_HORIZONS
    candidates: list[MinedCandidate] = []
    for horizon in horizons:
        candidates.extend(mine_candidate_events(
            session, stock_ids, barrier_cfg, horizon=horizon, max_events_per_stock=max_events_per_stock,
        ))
    candidates.sort(key=lambda c: c.event_timestamp)
    return candidates


def mine_and_report(session: Session, barrier_cfg: BarrierConfig | None = None) -> dict:
    """Convenience entry point: mine every active stock's candidate events across all
    trading horizons and return a summary dict (counts + a label_balance_report once
    labeled). Does not train or persist anything — a thin orchestration wrapper for a
    one-off or scheduled mining run.
    """
    from db import Stock

    from .triple_barrier_labeling import build_labeled_dataset, label_balance_report

    barrier_cfg = barrier_cfg or BarrierConfig()
    stock_ids = [sid for (sid,) in session.execute(
        select(Stock.id).where(Stock.active.is_(True))
    ).all()]

    candidates = mine_all_horizons(session, stock_ids, barrier_cfg)
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


def train_and_save_position_scaling_gate(
    session: Session,
    save_path: str,
    barrier_cfg: BarrierConfig | None = None,
) -> dict:
    """T241 Phase 5: mine -> label -> walk-forward train -> save, in one call. This is the
    function a scheduled retrain job calls; it does NOT touch any live trading decision by
    itself — saving a new model file only takes effect once something explicitly loads it
    (see paper_trading_engine.py's position-scaling shadow-mode block).

    Returns a summary dict (candidate/label counts, walk-forward report, feature
    importances) so a caller can log or alert on training results without needing to
    inspect the saved file separately.
    """
    from .position_scaling_gate import walk_forward_report, walk_forward_train

    barrier_cfg = barrier_cfg or BarrierConfig()
    from db import Stock
    stock_ids = [sid for (sid,) in session.execute(
        select(Stock.id).where(Stock.active.is_(True))
    ).all()]

    candidates = mine_all_horizons(session, stock_ids, barrier_cfg)
    if not candidates:
        return {"trained": False, "reason": "no candidate events mined"}

    price_history = _load_price_history(session, {c.symbol for c in candidates})
    candidate_df = candidates_to_dataframe(candidates)

    from .triple_barrier_labeling import build_labeled_dataset
    labeled = build_labeled_dataset(candidate_df, price_history, barrier_cfg)
    if labeled.empty:
        return {"trained": False, "reason": "no events survived labeling"}

    X, y, ret = build_feature_matrix(candidates, labeled)
    fold_results, final_gate = walk_forward_train(X, y, ret, n_splits=5, min_samples_per_split=15)
    report = walk_forward_report(fold_results)

    importances = final_gate.feature_importances().to_dict()
    # T241-AUDIT-WALKFORWARD-VALIDITY (found 2026-07-10 via audit): the drift-check
    # previously compared live shadow verdicts' mean act_probability against the model's
    # act_threshold (0.55) — an arbitrary decision boundary, not a real distributional
    # baseline. A calibrated model's mean predicted probability sits near its training
    # label's base rate, not near the threshold, so that comparison could false-alarm
    # every week (if the real base rate differs meaningfully from 0.55) or fail to catch
    # real drift. Store the model's own mean predicted probability ON ITS TRAINING SET here
    # so scheduler.py's drift check has a real baseline to compare live verdicts against.
    training_mean_act_probability = round(float(final_gate.model.predict_proba(X[FEATURE_COLUMNS].values)[:, 1].mean()), 4)
    final_gate.save(save_path, metadata={
        "n_candidates": len(candidates),
        "n_stocks": len({c.symbol for c in candidates}),
        "walk_forward_report": report,
        "feature_importances": importances,
        "training_mean_act_probability": training_mean_act_probability,
    })

    return {
        "trained": True,
        "n_candidates": len(candidates),
        "n_stocks": len({c.symbol for c in candidates}),
        "walk_forward_report": report,
        "feature_importances": importances,
        "training_mean_act_probability": training_mean_act_probability,
        "saved_to": save_path,
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
