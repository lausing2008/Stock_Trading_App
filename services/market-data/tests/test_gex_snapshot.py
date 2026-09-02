"""Tests for MPE-10's compute_gex_snapshots_eod() — the EOD GEX-snapshot persistence job.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler/ingestion.py/paper_trading_engine.py — the same established constraint documented
in test_options_flow_brief_wiring.py/test_premarket_gappers.py) — covered via source-text
regression checks, matching those files' precedent for this exact class of function.

gex_snapshot.py imports `db` (GexSnapshot) and `sqlalchemy.dialects.postgresql` at module level
for its DB-facing functions — conftest.py already stubs both as MagicMock for the whole test
session, so the module imports cleanly, but upsert_gex_snapshot() itself is thin DB glue with
nothing to meaningfully unit-test against a MagicMock session, matching the established
precedent in test_options_flow_snapshot.py for upsert_options_flow_snapshot().
"""
import pathlib

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _gex_eod_body() -> str:
    start = _SCHEDULER_SOURCE.index("def compute_gex_snapshots_eod(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def test_gates_entirely_behind_unusual_whales_is_available():
    """Real GEX has no free-tier fallback at all — the job must be a genuine no-op with no
    UW subscription active, never attempting a fetch."""
    body = _gex_eod_body()
    assert "if not _uw.is_available():" in body
    idx = body.index("if not _uw.is_available():")
    assert "return" in body[idx:idx + 200]


def test_skips_a_symbol_with_no_real_gamma_flip_rather_than_persisting_a_null_row():
    """levels is None (fetch failed / no UW data) OR gamma_flip specifically is None (a
    genuinely thin/no-options symbol) must both skip, matching get_gex_levels()'s own contract
    that a missing gamma_flip means "no real GEX data for this symbol", not zero."""
    body = _gex_eod_body()
    assert "if levels is None or levels.gamma_flip is None:" in body


def test_one_symbol_failure_does_not_abort_the_whole_batch():
    """Matches compute_options_flow_snapshots_eod()'s own per-symbol try/except isolation
    exactly — a transient failure for one symbol must not prevent the rest of the bounded
    symbol set from being persisted."""
    body = _gex_eod_body()
    assert "except Exception as exc:" in body
    assert 'log.warning("scheduler.gex_eod.symbol_error"' in body
    assert "failed += 1" in body


def test_reuses_the_shared_bounded_symbol_set_not_a_separate_selection():
    """The SAME bounded set options-flow already established (PriceAlert-subscribed + top-K by
    K-Score) — never a second, independently-drifting symbol-selection mechanism."""
    body = _gex_eod_body()
    assert "_bounded_options_flow_symbols(session)" in body


def test_underlying_close_is_read_from_the_real_persisted_daily_price_not_a_live_fetch():
    """UW's own GEX response carries no spot-price field at all — this must read the already-
    persisted daily Price row (which the 16:30 ET post-close job already wrote before this
    17:15 ET job runs), never a second live yfinance/UW call just for a price."""
    body = _gex_eod_body()
    assert "Price.timeframe == TimeFrame.D1" in body
    assert "order_by(Price.ts.desc())" in body


def test_has_the_same_inter_symbol_rate_limit_sleep_as_the_sibling_options_flow_job():
    body = _gex_eod_body()
    assert "time.sleep(2.0)" in body


def test_job_is_registered_on_the_schedule_after_the_options_flow_eod_job():
    """15 minutes after options_flow_eod (17:00 ET) — both are UW/yfinance-rate-limit-fragile
    batch jobs and must not fire at the exact same instant."""
    assert 'id="options_flow_eod"' in _SCHEDULER_SOURCE
    assert 'id="gex_eod"' in _SCHEDULER_SOURCE
    idx_options = _SCHEDULER_SOURCE.index('id="options_flow_eod"')
    idx_gex = _SCHEDULER_SOURCE.index('id="gex_eod"')
    assert idx_gex > idx_options
    gex_block = _SCHEDULER_SOURCE[idx_options:idx_gex + 200]
    assert "hour=17, minute=15" in gex_block
