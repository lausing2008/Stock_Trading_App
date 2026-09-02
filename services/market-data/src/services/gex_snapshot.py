"""MPE-10: end-of-day real gamma-exposure (GEX) snapshot persistence.

get_gex_levels() (services/market-data/src/services/unusual_whales.py) is LIVE-ONLY with no
history — nothing persists it, so a future GEX feature-ablation group (MPE-04) or a "how has
this stock's gamma structure evolved" read would have no data to draw on. This module wraps
that existing function and persists one row per stock per day into GexSnapshot, matching
options_flow_snapshot.py's own established persistence pattern (bounded symbol set, upsert on
(stock_id, as_of), one commit per batch job) — deliberately NOT re-deriving the GEX math itself
(unlike options_flow_snapshot.py, which independently re-derives cp_ratio/sentiment because no
reusable function existed for it) — get_gex_levels() is already a plain, reusable function this
module can call directly with zero duplication risk.

Gated entirely behind unusual_whales.is_available() — real GEX has no free-tier fallback at
all, so this job is simply a no-op with no UW subscription active.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from db import GexSnapshot
from sqlalchemy.dialects.postgresql import insert as pg_insert

import structlog

log = structlog.get_logger()


def upsert_gex_snapshot(
    session, stock_id: int, levels, underlying_close: float | None, as_of: date | None = None,
) -> None:
    """Upsert one GexSnapshot row from a real GexLevels result. Idempotent via ON CONFLICT DO
    UPDATE on (stock_id, as_of) — safe to re-run for the same day without creating duplicate
    rows. Does NOT commit — the caller (the EOD batch job) commits once after the whole batch,
    matching options_flow_snapshot.py's own convention of one commit per batch rather than
    per-row.
    """
    as_of = as_of or datetime.now(timezone.utc).date()
    values = dict(
        stock_id=stock_id,
        as_of=as_of,
        call_wall=levels.call_wall,
        put_wall=levels.put_wall,
        gamma_flip=levels.gamma_flip,
        gamma_magnet=levels.gamma_magnet,
        underlying_close=underlying_close,
    )
    stmt = pg_insert(GexSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["stock_id", "as_of"],
        set_={k: v for k, v in values.items() if k not in ("stock_id", "as_of")},
    )
    session.execute(stmt)
