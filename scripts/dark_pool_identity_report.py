#!/usr/bin/env python3
"""Bounded read-only diagnostic: aggregate retained variants of ambiguous provider IDs.

Run inside market-data with its existing environment. No IDs, payloads or credentials
leave the database query. This does not infer an exchange correction sequence.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone

QUERY = """
WITH selected AS (
    SELECT symbol, tracking_id, count(*) OVER () AS population
    FROM dark_pool_events_v2
    WHERE ambiguous IS TRUE AND observed_at >= :cutoff
      AND (CAST(:symbol AS text) IS NULL OR symbol = :symbol)
    ORDER BY observed_at DESC, symbol, tracking_id
    LIMIT :limit
), variants AS (
    SELECT s.symbol, s.tracking_id, s.population, count(*) AS versions,
      count(DISTINCT (v.payload->>'executed_at', v.payload->>'price', v.payload->>'size',
                      v.payload->>'premium', v.payload->>'canceled', v.payload->>'venue')) AS trade_variants,
      count(DISTINCT (v.payload->>'nbbo_bid', v.payload->>'nbbo_ask')) AS quote_variants,
      count(DISTINCT v.payload->>'price') AS prices,
      count(DISTINCT v.payload->>'size') AS sizes,
      count(DISTINCT v.payload->>'executed_at') AS times,
      count(DISTINCT v.payload->>'canceled') AS cancellations
    FROM selected s JOIN dark_pool_event_versions_v2 v
      ON v.symbol = s.symbol AND v.tracking_id = s.tracking_id
    GROUP BY s.symbol, s.tracking_id, s.population
)
SELECT coalesce(max(population), 0) AS ambiguous_ids_in_window,
       count(*) AS ids_reviewed, coalesce(sum(versions), 0) AS retained_versions_reviewed,
       count(*) FILTER (WHERE trade_variants = 1 AND quote_variants > 1) AS quote_only_variant_ids,
       count(*) FILTER (WHERE trade_variants > 1) AS trade_field_variant_ids,
       count(*) FILTER (WHERE prices > 1) AS price_variant_ids,
       count(*) FILTER (WHERE sizes > 1) AS size_variant_ids,
       count(*) FILTER (WHERE times > 1) AS execution_time_variant_ids,
       count(*) FILTER (WHERE cancellations > 1) AS cancellation_variant_ids
FROM variants
"""


def report(session, *, symbol=None, days=21, limit=500, now=None):
    from sqlalchemy import text
    if not 1 <= days <= 30 or not 1 <= limit <= 2000:
        raise ValueError('days must be 1..30 and limit 1..2000')
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).astimezone(timezone.utc).replace(tzinfo=None)
    row = session.execute(text(QUERY), {'symbol': symbol.upper() if symbol else None,
                                      'cutoff': cutoff, 'limit': limit}).mappings().one()
    counts = {key: int(value) for key, value in row.items()}
    return {'status': 'ok', 'counts': counts, 'days': days, 'limit': limit,
            'limited': counts['ambiguous_ids_in_window'] > counts['ids_reviewed'],
            'scope': 'all retained versions of selected currently ambiguous IDs; not a reconstruction of a single capture',
            'interpretation': 'Quote-only variation does not establish the provider ID semantics. Quarantine is unchanged.',
            'reported_at': now.isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol')
    parser.add_argument('--days', type=int, default=21)
    parser.add_argument('--limit', type=int, default=500)
    args = parser.parse_args()
    from db import SessionLocal
    from sqlalchemy import text
    try:
        with SessionLocal() as session:
            session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            session.execute(text("SET LOCAL statement_timeout = '10s'"))
            try:
                result = report(session, **vars(args))
            finally:
                session.rollback()
        print(json.dumps(result, sort_keys=True))
    except Exception as exc:
        # Never print a DB URL or a DBAPI exception containing connection details.
        print(json.dumps({'status': 'query_failed', 'error_type': type(exc).__name__}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
