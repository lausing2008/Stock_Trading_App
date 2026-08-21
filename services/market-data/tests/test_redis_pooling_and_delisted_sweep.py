"""Regression tests for a next-improvement-batch sweep (2026-08-21):

1. Redis-connection-pooling: 8 sites in paper_trading_engine.py/scheduler.py used
   `import redis as _rb; _rb.Redis.from_url(...)` — a raw, unpooled connection per call in
   the hot trading-decision loop (_write_gate_block(), _write_no_entry_summary(),
   _clear_no_entry_summary(), the DE-shadow-comparison logger, the T241-P6 position-scaling
   shadow writer/resolver). This repo's own "closing the loop" Redis-pooling audit explicitly
   verified repo-wide via `grep redis\\.Redis\\.from_url\\|redis\\.from_url\\|...` that zero raw
   constructions remained — but these 8 sites used an `_rb` alias, invisible to that exact
   grep pattern, and were introduced in code written AFTER that audit closed. Fixed to use
   the shared pooled common.redis_client.get_redis() helper, matching every other site in the
   same files.

2. BUG-DELISTED-GENERATION-BLIND: two more real generation-path sites found missing the
   Stock.delisted exclusion this bug class already has 10+ fixed sites for elsewhere —
   admin.py's watchlist-rotation candidate query (a real, live recommendation feed) and its
   index-membership backfill. Also found and fixed 3 MORE sites in paper_trading_engine.py
   itself that the earlier, supposedly-exhaustive sweep missed entirely: _scan_for_entries()'s
   own real BUY-candidate query (the highest-stakes site this bug class could exist at —
   confirmed via direct trace, never touched by any prior sweep), _compute_hk_breadth()'s
   market-wide breadth calculation (feeds regime classification), and paper_trading_step()'s
   watchlist-candidate price pre-fetch.

(A 3rd fix in this same batch — AUD292-SHARPE-VAREPS's own sibling gap in portfolio-optimizer's
_beta() — is tested separately in services/portfolio-optimizer/tests/test_portfolio_risk.py,
since risk.py needs THAT service's own conftest.py stub set, not this one's.)

Both paper_trading_engine.py and admin.py can't be imported directly in this test
environment — source-text regression checks, matching this file's own established pattern.
"""
import pathlib

_PTE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_PTE_SOURCE = _PTE_PATH.read_text()

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SCHEDULER_SOURCE = _SCHEDULER_PATH.read_text()

_ADMIN_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "admin.py"
_ADMIN_SOURCE = _ADMIN_PATH.read_text()


# ── 1. Redis-connection-pooling: the _rb-alias blind spot ───────────────────────────────────

def test_no_raw_redis_import_alias_remains_in_paper_trading_engine():
    assert "import redis as _rb" not in _PTE_SOURCE


def test_no_raw_redis_import_alias_remains_in_scheduler():
    assert "import redis as _rb" not in _SCHEDULER_SOURCE


def test_paper_trading_engine_now_reaches_seven_pooled_get_redis_sites():
    """Confirms all 7 fixed sites in this file now route through the shared pooled helper —
    not just that the raw alias is gone, but that a REAL replacement landed at each site."""
    assert _PTE_SOURCE.count("from common.redis_client import get_redis as _get_pool_redis") >= 7


def test_scheduler_position_scaling_drift_check_uses_the_module_level_get_redis_helper():
    """scheduler.py already has its own module-level _get_redis() wrapper (matching every
    other site in this same file) — the fixed site should reuse it, not re-import
    common.redis_client locally like paper_trading_engine.py's own sites do."""
    start = _SCHEDULER_SOURCE.index("def _check_position_scaling_gate_drift")
    end = _SCHEDULER_SOURCE.index("\ndef ", start + 1)
    body = _SCHEDULER_SOURCE[start:end]
    assert "r = _get_redis()" in body
    assert "import redis" not in body


def test_the_canonical_pooling_audit_grep_still_returns_zero_across_the_repo():
    """Re-runs this repo's own established closing-verification grep pattern (documented in
    CLAUDE.md's Redis-connection-pooling audit history) across every backend service's REAL
    source (src/ only — several test files legitimately reference this exact pattern in their
    own docstrings/comments explaining a mocking setup, which would false-positive a repo-wide
    scan including tests/) — confirms the _rb-alias fix didn't just move the raw construction
    under a different, still-ungrepped name."""
    import subprocess
    services_dir = pathlib.Path(__file__).resolve().parents[3] / "services"
    src_dirs = [str(p) for p in services_dir.glob("*/src") if p.is_dir()]
    result = subprocess.run(
        ["grep", "-rn",
         r"redis\.Redis\.from_url\|redis\.from_url\|redis_lib\.Redis\.from_url\|redis_lib\.from_url",
         *src_dirs, "--include=*.py"],
        capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"raw redis construction(s) found:\n{result.stdout}"


# ── 2. BUG-DELISTED-GENERATION-BLIND: admin.py + 3 more paper_trading_engine.py sites ───────

def test_admin_watchlist_rotation_candidates_excludes_delisted_stocks():
    start = _ADMIN_SOURCE.index("candidate_limit + len(excluded_ids))")
    body = _ADMIN_SOURCE[max(0, start - 400):start]
    assert "Stock.delisted.is_(False)" in body


def test_admin_index_membership_backfill_excludes_delisted_stocks():
    # backfill_index_membership() is the last function in admin.py — no trailing @router/def
    # boundary to search for, just take the rest of the file.
    start = _ADMIN_SOURCE.index("def backfill_index_membership(")
    body = _ADMIN_SOURCE[start:]
    assert "Stock.delisted.is_(False)" in body


def test_scan_for_entries_buy_candidate_query_excludes_delisted_stocks():
    """The single highest-stakes site this bug class could exist at — _scan_for_entries()'s
    own real BUY-candidate query, feeding actual new paper-trade entries. Never touched by any
    prior sweep before this fix."""
    start = _PTE_SOURCE.index("def _scan_for_entries(")
    end = _PTE_SOURCE.index("\ndef ", start + 1)
    body = _PTE_SOURCE[start:end]
    assert "Stock.delisted.is_(False)" in body


def test_compute_hk_breadth_excludes_delisted_stocks():
    start = _PTE_SOURCE.index("def _compute_hk_breadth(")
    end = _PTE_SOURCE.index("\ndef ", start + 1)
    body = _PTE_SOURCE[start:end]
    assert "Stock.delisted.is_(False)" in body


def test_paper_trading_step_watchlist_candidate_prefetch_excludes_delisted_stocks():
    # paper_trading_step() is the last TOP-LEVEL function in the file and contains its own
    # nested inner def (_get_regime_for) — a plain "\ndef " boundary search would match that
    # nested def prematurely rather than the real end of this function, so this takes the
    # rest of the file instead (safe: nothing after this function's own real content matters
    # for this specific check).
    start = _PTE_SOURCE.index("def paper_trading_step(")
    body = _PTE_SOURCE[start:]
    assert "Stock.delisted.is_(False)" in body


def test_candidate_event_mining_deliberately_keeps_delisted_stocks_for_training():
    """The one site that should NOT get this filter — candidate_event_mining.py's ML
    training-data mining deliberately retains delisted stocks (excluding them would introduce
    survivorship bias, an already-documented, deliberately-accepted limitation in this repo's
    own CLAUDE.md). Confirms this file was correctly left untouched by this batch's sweep,
    not silently skipped by mistake."""
    mining_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "candidate_event_mining.py"
    source = mining_path.read_text()
    assert "Stock.delisted" not in source
