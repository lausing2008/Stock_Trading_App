"""Tests for AUD-OPTIONS4-GAMEPLANBATCH's GET /stocks/options-game-plan/batch route
(get_options_game_plan_batch() in routes.py).

get_options_game_plan_batch() can't be exercised end-to-end in this test environment (needs a
real DB session), matching test_short_squeeze_score_route_wiring.py's established source-text-
extraction technique for this exact class of function.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_SOURCE = _routes_path.read_text()


def _function_body() -> str:
    start = _SOURCE.index("def get_options_game_plan_batch(")
    end = _SOURCE.index('\n\n# ── Per-symbol Relative Strength', start)
    return _SOURCE[start:end]


def test_gated_behind_advanced_tier():
    """Same Advanced-tier gate as the interactive /{symbol}/options-game-plan route — never
    looser for the batch surface."""
    start = _SOURCE.index("@router.get(\"/options-game-plan/batch\")")
    signature_end = _SOURCE.index("):\n", start)
    signature = _SOURCE[start:signature_end]
    assert "Depends(get_advanced_user)" in signature


def test_reads_the_daily_batch_snapshot_never_a_live_fetch():
    """The whole point of this endpoint — must read get_latest_options_game_plan() (a DB read),
    never trigger a live yfinance options-chain fetch inline for a batch of symbols."""
    body = _function_body()
    assert "get_latest_options_game_plan(session, stock_id)" in body
    assert "yf.Ticker" not in body
    assert "compute_options_game_plan_snapshot(" not in body
    assert "compute_options_game_plan(" not in body


def test_unknown_symbol_returns_available_false_not_a_crash():
    body = _function_body()
    assert '"unknown_symbol"' in body


def test_missing_snapshot_returns_available_false_with_a_real_reason():
    """A symbol outside the bounded EOD set, or whose job hasn't run since it became a BUY
    candidate, must return an honest reason — never a fabricated plan."""
    body = _function_body()
    assert '"no_snapshot"' in body


def test_empty_symbols_list_returns_empty_results_not_an_error():
    body = _function_body()
    assert "if not sym_list:" in body
    assert 'return {"results": {}}' in body


def test_result_surfaces_expected_move_and_iv_rank_fields():
    """AUD-IVRANK: expected_move_pct/expected_move_dte/iv_rank_1y were computed and persisted
    by the snapshot job but never actually surfaced through this batch route -- a real gap,
    not just a missing iv_rank_1y field. All 3 must be read straight from the snapshot row."""
    body = _function_body()
    assert '"expected_move_pct": snap.expected_move_pct' in body
    assert '"expected_move_dte": snap.expected_move_dte' in body
    assert '"iv_rank_1y": snap.iv_rank_1y' in body


def test_legs_surface_real_per_contract_greeks():
    """AUD-GREEKS: delta/gamma/theta/vega/vanna/charm for the exact selected strike must be
    nested inside protective_put/covered_call, not omitted from the response."""
    body = _function_body()
    assert '"delta": snap.put_delta' in body
    assert '"gamma": snap.put_gamma' in body
    assert '"theta": snap.put_theta' in body
    assert '"vega": snap.put_vega' in body
    assert '"vanna": snap.put_vanna' in body
    assert '"charm": snap.put_charm' in body
    assert '"delta": snap.call_delta' in body
    assert '"gamma": snap.call_gamma' in body
    assert '"theta": snap.call_theta' in body
    assert '"vega": snap.call_vega' in body
    assert '"vanna": snap.call_vanna' in body
    assert '"charm": snap.call_charm' in body


def test_route_path_is_a_literal_segment_not_shadowed_by_the_symbol_path_param():
    """AUD-ROUTERORDER class regression guard: /options-game-plan/batch's first path segment
    is the literal 'options-game-plan', never colliding with the sibling /{symbol}/options-
    game-plan route regardless of registration order (FastAPI matches literal-vs-param per
    segment, so this can only ever collide with a real symbol literally named
    'options-game-plan', which cannot happen) — this test just pins the route string itself so
    a future refactor can't accidentally rename it into a real collision."""
    assert '@router.get("/options-game-plan/batch")' in _SOURCE


def test_imports_options_game_plan_snapshot_via_the_correct_relative_path():
    """AUD-GAMEPLANBATCH-WRONGIMPORT: this route lives in src/api/routes.py, but
    options_game_plan_snapshot.py lives in src/services/ — a bare `from
    .options_game_plan_snapshot import ...` (relative to src/api/) raises ModuleNotFoundError
    at request time, 500ing every real call to this route. Confirmed live in production: this
    exact bug meant the Options Game Plan screener column/email section had never actually
    returned real data, despite the EOD batch job (scheduler.py, a DIFFERENT file whose own
    correctly-relative `.options_game_plan_snapshot` import never exercised this bug) writing
    real snapshots successfully every day. Must import via `..services.options_game_plan_snapshot`,
    matching every other src/api/routes.py cross-package import into src/services/ (e.g.
    `from ..services.paper_trading_engine import ...`, `from ..services.ingestion import ...`)."""
    body = _function_body()
    assert "\n    from ..services.options_game_plan_snapshot import get_latest_options_game_plan" in body
    assert "\n    from .options_game_plan_snapshot import" not in body
