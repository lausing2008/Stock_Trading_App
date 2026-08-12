"""Tests for BUG-SIGNALS-UNBOUNDED-GROWTH: _purge_old_data() never pruned the signals table.

Found while investigating whether the BUG-REASONSJSON-NAN fix left anything to clean up in
production — it didn't (Postgres's strict jsonb cast meant no NaN row ever wrote
successfully), but the investigation surfaced a real, separate, previously-undocumented
growth issue: signals is NOT fixed-size despite an earlier memory note claiming it stays
"constant size regardless of how long the system runs". Tier 71 (2026-06-21) switched the
upsert's unique index to a DAY-SCOPED one (uq_signals_stock_horizon_day, on (stock_id,
horizon, date_trunc('day', ts))) — a real, intentional design (feeds signal_history()'s
sparkline and the daily chart's own signal-transition markers) — but nothing was ever
registered to prune rows once they age past what any real consumer can read.
signal_history()'s own `days` query param caps at le=365, so a row past 365 days is
provably unreachable by any real caller; that's the exact boundary matched here.

_purge_old_data() can't be imported directly in this test environment — scheduler.py's
import chain pulls in apscheduler and other unstubbed modules (see test_premarket_brief.py's
docstring for the established reasoning) — covered via source-text regression checks,
matching test_premarket_gappers.py's established pattern for this exact class of function.
"""
import pathlib

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _purge_old_data_body() -> str:
    start = _SCHEDULER_SOURCE.index("def _purge_old_data(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def test_purge_old_data_deletes_signals_older_than_365_days():
    """The real fix — a DELETE FROM signals statement with the exact 365-day cutoff matching
    signal_history()'s own le=365 ceiling, the boundary already proven unreachable by any
    real consumer."""
    body = _purge_old_data_body()
    assert "DELETE FROM signals WHERE ts < NOW() - INTERVAL '365 days'" in body


def test_purge_old_data_still_deletes_the_two_pre_existing_targets():
    """Regression guard — the new signals DELETE must be ADDITIVE, not a replacement of the
    two targets this function already covered (M5 bars, signal_outcomes)."""
    body = _purge_old_data_body()
    assert "DELETE FROM prices WHERE timeframe='M5' AND ts < NOW() - INTERVAL '90 days'" in body
    assert "DELETE FROM signal_outcomes WHERE ts_evaluated < NOW() - INTERVAL '400 days'" in body


def test_purge_old_data_logs_the_new_signals_deleted_count():
    """The done log line must report the new deletion count too — a silent purge that never
    surfaces its own row count in logs is the same class of invisibility this repo's own
    established discipline (see the module's other purge-adjacent logging) tries to avoid."""
    body = _purge_old_data_body()
    log_idx = body.index('log.info(\n                "scheduler.purge_done"')
    log_call_end = body.index(")", log_idx)
    log_call = body[log_idx:log_call_end]
    assert "signals_deleted=ressig.rowcount" in log_call


def test_purge_old_data_commits_all_three_deletes_in_one_transaction():
    """The new DELETE must run inside the SAME session/commit as the other two — a separate
    session.commit() call would risk a partial purge (e.g. M5 bars pruned but signals left
    untouched) if the process died between two commits."""
    body = _purge_old_data_body()
    commit_count = body.count("session.commit()")
    assert commit_count == 1
    # And the new delete must be positioned BEFORE that single commit, not after.
    commit_idx = body.index("session.commit()")
    sig_delete_idx = body.index("DELETE FROM signals")
    assert sig_delete_idx < commit_idx
