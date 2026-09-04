"""Tests for AUD-OPTIONS4-GAMEPLANBATCH's compute_options_game_plan_snapshots_eod() — the EOD
Options Game Plan snapshot persistence job — and _bounded_options_flow_symbols() reuse.

scheduler.py can't be imported directly in this test environment (its import chain pulls in
apscheduler) — covered via source-text regression checks, matching test_gex_snapshot.py's own
established precedent for this exact class of function.

options_game_plan_snapshot.py imports `db` (OptionsGamePlanSnapshot) and
`sqlalchemy.dialects.postgresql` at module level for its DB-facing functions — conftest.py
already stubs both as MagicMock for the whole test session, so the module imports cleanly, but
upsert_options_game_plan_snapshot() itself is thin DB glue with nothing to meaningfully
unit-test against a MagicMock session, matching test_gex_snapshot.py's own established
precedent for upsert_gex_snapshot().
"""
import pathlib

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()


def _game_plan_eod_body() -> str:
    start = _SCHEDULER_SOURCE.index("def compute_options_game_plan_snapshots_eod(")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    return _SCHEDULER_SOURCE[start:end]


def test_reuses_the_same_bounded_symbol_set_as_options_flow_and_gex():
    """Must reuse _bounded_options_flow_symbols() exactly — never a wider or independently
    re-derived symbol universe, matching the user's own explicit scoping decision."""
    body = _game_plan_eod_body()
    assert "_bounded_options_flow_symbols(session)" in body


def test_one_symbol_failure_does_not_abort_the_whole_batch():
    """Matches compute_options_flow_snapshots_eod()'s/compute_gex_snapshots_eod()'s own
    per-symbol try/except isolation exactly."""
    body = _game_plan_eod_body()
    assert "except Exception as exc:" in body
    assert 'log.warning("scheduler.options_game_plan_eod.symbol_error"' in body
    assert "failed += 1" in body


def test_rate_limited_with_the_same_inter_symbol_sleep():
    body = _game_plan_eod_body()
    assert "time.sleep(2.0)" in body


def test_job_is_scheduled_after_the_gex_job_same_stagger_discipline():
    """17:30 ET — 15 min after the GEX job's own 17:15 slot, matching the established stagger
    pattern (options-flow 17:00, GEX 17:15) so 3 yfinance-options-chain-touching batch jobs on
    the same bounded symbol set never fire concurrently."""
    idx = _SCHEDULER_SOURCE.index('id="options_game_plan_eod"')
    surrounding = _SCHEDULER_SOURCE[idx - 300:idx + 50]
    assert "hour=17, minute=30" in surrounding


def test_job_is_registered_with_replace_existing_and_job_defaults():
    idx = _SCHEDULER_SOURCE.index('id="options_game_plan_eod"')
    surrounding = _SCHEDULER_SOURCE[idx - 50:idx + 100]
    assert "replace_existing=True" in surrounding
    assert "_JOB_DEFAULTS" in surrounding
