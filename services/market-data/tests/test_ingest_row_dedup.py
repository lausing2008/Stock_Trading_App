"""Tests for BUG-INGEST-CARDINALITYVIOLATION — the row-deduplication step in
ingest_symbol() (services/market-data/src/services/ingestion.py) that runs immediately
before the bulk pg_insert(Price)...on_conflict_do_update() call.

Found live in production: real, repeated psycopg2.errors.CardinalityViolation failures
("ON CONFLICT DO UPDATE command cannot affect row a second time") ingesting GDX's daily bars
— confirmed via the actual failing SQL parameters that 20 rows in a single INSERT batch all
carried the identical (stock_id, ts, timeframe) key. Every deliberate, deterministic
reproduction attempt against the real yfinance API for the same symbol/window/timeframe came
back clean, pointing at a rare, non-deterministic upstream duplication (yfinance/curl_cffi
under this app's real concurrent ThreadPoolExecutor load) rather than a bug in this app's own
request construction — but regardless of root cause, Postgres's ON CONFLICT clause can never
resolve two rows in the SAME statement mapping to the same conflict target, so the batch-level
defense (deduplicate before the conflict target matters) is the correct, proportionate fix
independent of whatever upstream condition produces the duplicates.

The dedup block itself is pure (plain dict/list logic, no DB/network dependency) and is
extracted via source-text exec(), matching this repo's established technique for testing a
narrow slice of a much larger, heavily-DB-coupled function without needing the full
stub-pop-and-restore machinery test_delisting_detection.py/test_broker_position_sync.py use for
functions that need the real ORM models.
"""
import pathlib

_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "ingestion.py").read_text()

import textwrap

_start = _SOURCE.index("\n        # BUG-INGEST-CARDINALITYVIOLATION:") + 1
_end = _SOURCE.index("\n\n        stmt = pg_insert(Price).values(rows)")
_BLOCK = textwrap.dedent(_SOURCE[_start:_end])


def _run_dedup(rows: list[dict], symbol: str = "TEST", timeframe: str = "1d") -> tuple[list[dict], list[str]]:
    """Executes the real extracted dedup block against a caller-supplied `rows` list, with a
    fake `log` capturing any warning calls so the dedup-triggered case can be asserted on too."""
    warnings: list[str] = []

    class _FakeLog:
        def warning(self, event, **kwargs):
            warnings.append(event)

    namespace = {"rows": list(rows), "symbol": symbol, "timeframe": timeframe, "log": _FakeLog()}
    exec(_BLOCK, namespace)
    return namespace["rows"], warnings


def _row(stock_id=1, ts="2026-08-13", timeframe="D1", close=100.0):
    return {"stock_id": stock_id, "ts": ts, "timeframe": timeframe, "close": close}


class TestIngestRowDedup:
    def test_no_duplicates_leaves_rows_unchanged(self):
        rows = [_row(ts="2026-08-11"), _row(ts="2026-08-12"), _row(ts="2026-08-13")]
        result, warnings = _run_dedup(rows)
        assert result == rows
        assert warnings == []

    def test_exact_duplicate_key_is_collapsed_to_one_row(self):
        """The real production bug: 20 identical (stock_id, ts, timeframe) rows in one batch."""
        rows = [_row(ts="2026-08-13") for _ in range(20)]
        result, warnings = _run_dedup(rows)
        assert len(result) == 1
        assert warnings == ["ingest.duplicate_rows_deduped"]

    def test_keeps_the_last_occurrence_not_the_first(self):
        """If the same (stock_id, ts, timeframe) key genuinely appears twice with different
        field values (e.g. a late price correction), the LATER row in the batch should win —
        matching how a legitimate re-ingest of the same date is expected to behave."""
        rows = [_row(ts="2026-08-13", close=100.0), _row(ts="2026-08-13", close=105.5)]
        result, _ = _run_dedup(rows)
        assert len(result) == 1
        assert result[0]["close"] == 105.5

    def test_different_stock_id_is_not_deduped_even_with_the_same_ts_and_timeframe(self):
        rows = [_row(stock_id=1, ts="2026-08-13"), _row(stock_id=2, ts="2026-08-13")]
        result, warnings = _run_dedup(rows)
        assert len(result) == 2
        assert warnings == []

    def test_different_timeframe_is_not_deduped_even_with_the_same_stock_id_and_ts(self):
        rows = [_row(ts="2026-08-13", timeframe="D1"), _row(ts="2026-08-13", timeframe="W1")]
        result, warnings = _run_dedup(rows)
        assert len(result) == 2
        assert warnings == []

    def test_empty_rows_list_is_a_no_op(self):
        result, warnings = _run_dedup([])
        assert result == []
        assert warnings == []

    def test_mixed_batch_only_dedups_the_actual_duplicate_subset(self):
        rows = [
            _row(ts="2026-08-11"),
            _row(ts="2026-08-12"),
            _row(ts="2026-08-13", close=100.0),
            _row(ts="2026-08-13", close=101.0),  # duplicate of the row above
        ]
        result, warnings = _run_dedup(rows)
        assert len(result) == 3
        assert warnings == ["ingest.duplicate_rows_deduped"]
        aug13 = [r for r in result if r["ts"] == "2026-08-13"][0]
        assert aug13["close"] == 101.0
