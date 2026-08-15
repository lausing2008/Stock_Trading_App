"""T264-SHORTSQUEEZE-PREBREAKOUT: builds a labeled historical training set for "will a
coiling, high-short-interest stock go on to a sustained breakout within N days" — the
trainable target the user asked for: "predict the short sell not able to recover... before it
starts to breakout."

Uses 3+ real years of daily Price history (this app's own deepest data source) rather than
options-chain data (only ~2 weeks of history exists anywhere in this app — see
docs/KNOWN_LIMITATIONS.md-style honesty discipline applied here: a supervised model trained on
2 weeks of data would be fitting noise, not a real pattern). Options positioning is folded in
separately, at INFERENCE time only, as a confidence modifier — never as a training feature,
since there isn't enough historical options data to validate it as one.

Label definition (agreed with the user before writing this):
  POSITIVE if, within the next _BREAKOUT_WINDOW_DAYS trading days, price:
    1. closes above its own trailing 20-day high (a genuine breakout, not just "up today"), AND
    2. HOLDS above that level for _BREAKOUT_HOLD_DAYS consecutive days after breaking out
       (excludes a poke-and-reject fake breakout), AND
    3. the breakout day's own volume cleared _BREAKOUT_MIN_RVOL x its 20-day average (a
       volume-confirmed move, not a low-conviction drift above the level).
  NEGATIVE if the qualifying (coiling + high-short-interest) precondition held but no
  breakout meeting all 3 conditions occurred in the window.

Candidate (unlabeled) precondition, evaluated point-in-time for every historical trading day:
  - short_percent_of_float >= the live alert's own _SQUEEZE_MIN_SHORT_FLOAT threshold, as of
    the most recent WEEKLY FundamentalsSnapshot on or before that day (merge_asof, backward —
    the same point-in-time join convention ml-prediction's own builder.py already established
    for exactly this kind of weekly-snapshot join, avoiding lookahead).
  - detect_price_compression() (price_compression.py, this service's own independent port of
    technical-analysis's identical function) reports is_compressed=True as of that day, using
    only bars up to and including that day (never a later bar).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import FundamentalsSnapshot, Price, Stock, TimeFrame

from ..services.price_compression import detect_price_compression

_BREAKOUT_WINDOW_DAYS = 10     # matches the user's own "before it starts to breakout" framing — a near-term window
_BREAKOUT_HOLD_DAYS = 3        # consecutive days holding above the breakout level to exclude a poke-and-reject
_BREAKOUT_MIN_RVOL = 1.5       # the breakout day's own volume vs. its 20-day average
_SQUEEZE_MIN_SHORT_FLOAT = 15.0  # matches scheduler.py's own _SQUEEZE_MIN_SHORT_FLOAT exactly — see note below
_MIN_HISTORY_BARS = 146  # detect_price_compression()'s own floor (126-day lookback + 20-day warmup)


@dataclass
class LabeledPreBreakoutRow:
    stock_id: int
    symbol: str
    as_of: date
    short_percent_of_float: float
    bb_width_pctile: float
    atr_pctile: float
    volume_dried_up: bool | None
    label: bool  # True = a qualifying breakout occurred within the window


@dataclass
class PreBreakoutDatasetResult:
    rows: list[LabeledPreBreakoutRow] = field(default_factory=list)
    n_symbols_scanned: int = 0
    n_candidate_days: int = 0
    n_positive: int = 0


def _find_qualifying_breakout(bars: pd.DataFrame, start_idx: int) -> bool:
    """bars must be sorted ascending by date, indexed 0..n-1. start_idx is the LAST bar of the
    candidate (coiling) day — the breakout, if any, is searched for in bars AFTER start_idx,
    within _BREAKOUT_WINDOW_DAYS trading days.

    A "trailing 20-day high" at each candidate breakout bar is computed using only bars UP TO
    AND INCLUDING that bar (df["high"].rolling(20)), never a future bar — this is look-ahead-
    safe by construction since the rolling window only ever looks backward from each row.
    """
    n = len(bars)
    window_end_idx = min(start_idx + _BREAKOUT_WINDOW_DAYS, n - 1)
    high20 = bars["high"].rolling(20, min_periods=20).max()
    avg_vol20 = bars["volume"].rolling(20, min_periods=20).mean()

    for i in range(start_idx + 1, window_end_idx + 1):
        prior_high20 = high20.iloc[i - 1]
        if pd.isna(prior_high20):
            continue
        if bars["close"].iloc[i] <= prior_high20:
            continue
        avg_vol = avg_vol20.iloc[i - 1]
        if pd.isna(avg_vol) or avg_vol <= 0:
            continue
        rvol = bars["volume"].iloc[i] / avg_vol
        if rvol < _BREAKOUT_MIN_RVOL:
            continue
        # Volume-confirmed breakout bar found at i — now check the hold condition: every one
        # of the next _BREAKOUT_HOLD_DAYS bars (that exist within the df) must stay above the
        # SAME breakout level (prior_high20), not just "above whatever that day's own trailing
        # high happens to be" — a poke-and-reject that recovers to a NEW local high a few days
        # later must not be miscounted as a hold of the ORIGINAL breakout.
        hold_end = min(i + _BREAKOUT_HOLD_DAYS, n - 1)
        if hold_end < i + 1:
            continue  # not enough bars left in this df to confirm a hold — treat as unconfirmed, not a label
        held = all(bars["close"].iloc[j] > prior_high20 for j in range(i + 1, hold_end + 1))
        if held:
            return True
    return False


def build_prebreakout_dataset(session: Session, lookback_days: int = 1095) -> PreBreakoutDatasetResult:
    """Scans every stock with real FundamentalsSnapshot short-interest history, finds every
    historical trading day where the coiling + high-short-interest precondition held, and
    labels each one True/False per the module's own documented breakout definition.

    lookback_days defaults to 1095 (~3 years) — this app's own real daily Price depth (see the
    T264-SHORTSQUEEZE-PREBREAKOUT design investigation: 118,184 D1 rows across 178 symbols
    spanning 2023-04-21 to present). Symbols/dates outside FundamentalsSnapshot's own much
    shorter real history (short-interest snapshots only exist from 2026-07-05 onward as of this
    writing) are naturally excluded by the merge_asof join itself — no separate date-range
    guard is needed for that half of the precondition.
    """
    cutoff = date.today() - timedelta(days=lookback_days)

    snap_rows = session.execute(
        select(FundamentalsSnapshot.symbol, FundamentalsSnapshot.snapshot_date, FundamentalsSnapshot.short_percent_of_float)
        .where(FundamentalsSnapshot.short_percent_of_float.is_not(None))
        .order_by(FundamentalsSnapshot.symbol, FundamentalsSnapshot.snapshot_date)
    ).all()
    if not snap_rows:
        return PreBreakoutDatasetResult()

    snaps_by_symbol: dict[str, list[tuple[date, float]]] = {}
    for sym, snap_date, spf in snap_rows:
        snaps_by_symbol.setdefault(sym, []).append((snap_date, spf))

    symbols = sorted(snaps_by_symbol.keys())
    stock_rows = session.execute(select(Stock.id, Stock.symbol).where(Stock.symbol.in_(symbols))).all()
    stock_id_by_symbol = {sym: sid for sid, sym in stock_rows}

    result = PreBreakoutDatasetResult()
    for sym in symbols:
        stock_id = stock_id_by_symbol.get(sym)
        if stock_id is None:
            continue
        result.n_symbols_scanned += 1

        price_rows = session.execute(
            select(Price.ts, Price.close, Price.high, Price.low, Price.volume)
            .where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1, Price.ts >= cutoff)
            .order_by(Price.ts)
        ).all()
        if len(price_rows) < _MIN_HISTORY_BARS:
            continue
        bars = pd.DataFrame(price_rows, columns=["ts", "close", "high", "low", "volume"])
        bars["date"] = bars["ts"].apply(lambda t: t.date() if hasattr(t, "date") else t)
        bars["close"] = bars["close"].astype(float)
        bars["high"] = bars["high"].astype(float)
        bars["low"] = bars["low"].astype(float)
        bars["volume"] = bars["volume"].astype(float)

        # Point-in-time short-interest via merge_asof (backward) — the exact convention
        # ml-prediction's own builder.py already uses for the identical class of weekly-
        # snapshot-onto-daily-bars join (T228-POINT-IN-TIME-FUNDAMENTALS).
        snap_df = pd.DataFrame(snaps_by_symbol[sym], columns=["snapshot_date", "short_percent_of_float"])
        snap_df["snapshot_date"] = pd.to_datetime(snap_df["snapshot_date"])
        snap_df = snap_df.sort_values("snapshot_date").reset_index(drop=True)
        bars_dt = bars.copy()
        bars_dt["date_dt"] = pd.to_datetime(bars_dt["date"])
        merged = pd.merge_asof(
            bars_dt, snap_df, left_on="date_dt", right_on="snapshot_date", direction="backward",
        )

        for i in range(_MIN_HISTORY_BARS, len(bars)):
            spf = merged["short_percent_of_float"].iloc[i]
            if pd.isna(spf) or spf * 100 < _SQUEEZE_MIN_SHORT_FLOAT:
                continue
            window_bars = bars.iloc[: i + 1]
            compression = detect_price_compression(
                window_bars["close"], window_bars["high"], window_bars["low"], window_bars["volume"],
            )
            if not compression["is_compressed"]:
                continue

            result.n_candidate_days += 1
            label = _find_qualifying_breakout(bars, i)
            if label:
                result.n_positive += 1
            result.rows.append(LabeledPreBreakoutRow(
                stock_id=stock_id, symbol=sym, as_of=bars["date"].iloc[i],
                short_percent_of_float=float(spf), bb_width_pctile=compression["bb_width_pctile"],
                atr_pctile=compression["atr_pctile"], volume_dried_up=compression["volume_dried_up"],
                label=label,
            ))

    return result
