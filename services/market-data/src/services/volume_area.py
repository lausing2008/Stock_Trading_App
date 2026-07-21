"""T252-VALUE-AREA-BREAKDOWN-ALERT: server-side POC/VAH/VAL, a straight Python port of
frontend/src/lib/volumeProfile.ts's computeVolumeProfile().

This app has no bid/ask or tick-level trade data (see the T249-era investigation into true
footprint charts), so this uses the same standard retail-tool approximation the chart's client-
side version already uses: each bar's volume is distributed evenly across that bar's high-low
price range, then accumulated into a fixed number of price buckets across the profile's overall
range.

Only POC/VAH/VAL are needed by the alert this module exists for (see scheduler.py's
check_value_area_breakdown()) — HVN/LVN/bucket detail is deliberately not ported here since
nothing on the backend consumes it; the chart's own client-side computeVolumeProfile() remains
the source of truth for anything rendered on the chart itself. This is an independent port of
the same documented algorithm, not a shared implementation with volumeProfile.ts — if the value-
area-expansion logic in one is ever changed, check whether the other needs the same change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from db import Price, TimeFrame, VolumeAreaLevel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

DEFAULT_BUCKETS = 24
DEFAULT_VALUE_AREA_PCT = 0.70  # standard 70% value area, matches computeVolumeProfile()'s default
DEFAULT_LOOKBACK_DAYS = 60  # rolling window of daily bars profiled — a "Range VP"-style window,
# not a single session (this alert is meant to catch a multi-day/week breakdown, not an
# intraday one — check_volume_anomalies() already covers same-day abnormal volume separately).


@dataclass
class VolumeAreaResult:
    poc: float
    vah: float
    val: float
    total_volume: float


def compute_value_area(
    bars: list[tuple[float, float, float]],
    num_buckets: int = DEFAULT_BUCKETS,
    value_area_pct: float = DEFAULT_VALUE_AREA_PCT,
) -> VolumeAreaResult | None:
    """Port of computeVolumeProfile()'s bucketing + value-area-expansion logic.

    `bars` is a list of (high, low, volume) tuples — deliberately not a DataFrame; this keeps
    the function pure and trivially unit-testable against the same fixtures the TS test suite
    (volumeProfile.test.ts) uses, without a pandas dependency for what is simple arithmetic.
    """
    if not bars:
        return None

    range_high = max(h for h, _, _ in bars)
    range_low = min(l for _, l, _ in bars)
    if not (range_high > range_low):
        return None  # degenerate (single flat price) — nothing to bucket

    bucket_size = (range_high - range_low) / num_buckets
    volumes = [0.0] * num_buckets

    for high, low, volume in bars:
        if volume <= 0:
            continue
        bar_range = high - low
        first_bucket = max(0, min(num_buckets - 1, int((low - range_low) / bucket_size)))
        last_bucket = max(0, min(num_buckets - 1, int((high - range_low) / bucket_size)))
        if bar_range <= 0 or first_bucket == last_bucket:
            volumes[first_bucket] += volume
            continue
        buckets_touched = last_bucket - first_bucket + 1
        volume_per_bucket = volume / buckets_touched
        for i in range(first_bucket, last_bucket + 1):
            volumes[i] += volume_per_bucket

    total_volume = sum(volumes)
    if total_volume <= 0:
        return None

    bucket_price_low = [range_low + i * bucket_size for i in range(num_buckets)]
    bucket_price_high = [p + bucket_size for p in bucket_price_low]

    # POC: bucket with the most volume
    poc_idx = 0
    for i in range(1, num_buckets):
        if volumes[i] > volumes[poc_idx]:
            poc_idx = i
    poc = (bucket_price_low[poc_idx] + bucket_price_high[poc_idx]) / 2

    # Value area: expand outward from POC, each step adding whichever neighboring bucket
    # (above or below the current area) has more volume, until value_area_pct of total volume
    # is enclosed — the standard VAH/VAL algorithm, identical to computeVolumeProfile()'s.
    lo, hi = poc_idx, poc_idx
    area_volume = volumes[poc_idx]
    while area_volume / total_volume < value_area_pct and (lo > 0 or hi < num_buckets - 1):
        vol_below = volumes[lo - 1] if lo > 0 else -1
        vol_above = volumes[hi + 1] if hi < num_buckets - 1 else -1
        if vol_above >= vol_below:
            hi += 1
            area_volume += volumes[hi]
        else:
            lo -= 1
            area_volume += volumes[lo]
    val = bucket_price_low[lo]
    vah = bucket_price_high[hi]

    return VolumeAreaResult(poc=poc, vah=vah, val=val, total_volume=total_volume)


def compute_value_area_levels_for_stocks(session, stock_ids: list[int], as_of: date | None = None) -> int:
    """Compute and upsert POC/VAH/VAL for each given stock_id, using the last
    DEFAULT_LOOKBACK_DAYS days of daily bars. Returns the count of stocks successfully written.

    Idempotent via ON CONFLICT DO UPDATE on (stock_id, as_of) — safe to re-run for the same day
    (e.g. a retry after a partial failure) without creating duplicate rows.
    """
    if not stock_ids:
        return 0
    as_of = as_of or datetime.now(timezone.utc).date()
    cutoff = datetime.now(timezone.utc) - timedelta(days=DEFAULT_LOOKBACK_DAYS)

    written = 0
    for stock_id in stock_ids:
        rows = session.execute(
            select(Price.high, Price.low, Price.volume).where(
                Price.stock_id == stock_id,
                Price.timeframe == TimeFrame.D1,
                Price.ts >= cutoff,
            )
        ).all()
        bars = [(float(h), float(l), float(v)) for h, l, v in rows if h is not None and l is not None and v is not None]
        result = compute_value_area(bars)
        if result is None:
            continue
        stmt = pg_insert(VolumeAreaLevel).values(
            stock_id=stock_id, as_of=as_of,
            poc=result.poc, vah=result.vah, val=result.val,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_id", "as_of"],
            set_={"poc": result.poc, "vah": result.vah, "val": result.val},
        )
        session.execute(stmt)
        written += 1
    session.commit()
    return written


def get_latest_value_area(session, stock_id: int) -> VolumeAreaLevel | None:
    """Most recent VolumeAreaLevel row for a stock, or None if never computed."""
    return session.execute(
        select(VolumeAreaLevel)
        .where(VolumeAreaLevel.stock_id == stock_id)
        .order_by(VolumeAreaLevel.as_of.desc())
        .limit(1)
    ).scalar_one_or_none()
