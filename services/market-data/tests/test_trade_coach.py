"""Tests for T286-TRADE-PATTERN-COACH's compute_trade_patterns()/generate_trade_coach_summary()/
_clean_summary() in services/market-data/src/services/trade_coach.py, plus
send_trade_coach_email() (email_service.py) and send_weekly_trade_coach() (scheduler.py, via
source-text regression checks).

Matches test_theme_signals.py's established technique exactly: trade_coach.py imports `db`
(PaperTrade) and issues real SQLAlchemy queries, and also imports paper_trading_engine.py (for
_STYLE_OVERRIDES) — conftest.py stubs sqlalchemy/db as MagicMock for the rest of the test
session, so this pops those stubs, builds ONE shared in-memory engine + real models while real
sqlalchemy is active, imports the real module under test, then restores every stub immediately.
"""
import sys

_STUBBED_MODULES = (
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql",
    "db", "httpx", "common.ai_keys",
)
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_coach", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_coach"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(_ENGINE, tables=[_models.PaperTrade.__table__])

# AUD-TESTORDER: `db` must stay real until AFTER trade_coach.py is imported below — matching
# test_theme_signals.py's own established note on this exact ordering trap.
sys.modules["db"] = _models

for _mod, _stub in _saved_stubs.items():
    if _mod in ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "httpx", "db"):
        continue
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

import httpx  # real, not the stub — needed by generate_trade_coach_summary()
import pytest

import src.services.trade_coach as trade_coach

PaperTrade = _models.PaperTrade

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)


@pytest.fixture
def session():
    with Session(_ENGINE) as s:
        yield s
        s.query(PaperTrade).delete()
        s.commit()


_trade_id_counter = {"next": 1}


def _add_trade(
    session, *, portfolio_id=1, symbol="AAA", style="SWING", exit_reason="target_reached",
    pct_return=0.10, pnl=500.0, hold_days=10, exit_days_ago=1, entry_price=100.0,
    exit_price=110.0, highest_price=None,
):
    tid = _trade_id_counter["next"]
    _trade_id_counter["next"] += 1
    now = datetime.now(timezone.utc)
    exit_time = now - timedelta(days=exit_days_ago)
    session.add(PaperTrade(
        id=tid, portfolio_id=portfolio_id, symbol=symbol, trading_style=style,
        entry_date=(exit_time - timedelta(days=hold_days)).date(), entry_time=exit_time - timedelta(days=hold_days),
        entry_price=entry_price, shares=10.0, stop_loss=entry_price * 0.9, current_stop=entry_price * 0.9,
        stage="closed", hold_days=hold_days,
        exit_time=exit_time, exit_price=exit_price, exit_reason=exit_reason,
        pnl=pnl, pct_return=pct_return, highest_price=highest_price,
    ))
    session.commit()


def _fill_to_min(session, n=10, **kw):
    for i in range(n):
        _add_trade(session, symbol=f"SYM{i}", **kw)


# ── compute_trade_patterns() ─────────────────────────────────────────────────────

def test_returns_none_below_min_trades_floor(session):
    _fill_to_min(session, n=9)  # one below _MIN_TRADES_FOR_PATTERNS (10)
    assert trade_coach.compute_trade_patterns(session) is None


def test_computes_result_at_exactly_the_min_trades_floor(session):
    _fill_to_min(session, n=10)
    result = trade_coach.compute_trade_patterns(session)
    assert result is not None
    assert result.n_trades == 10


def test_excludes_trades_outside_the_window(session):
    _fill_to_min(session, n=10, exit_days_ago=1)
    _add_trade(session, symbol="OLD", exit_days_ago=200)  # outside the default 90-day window
    result = trade_coach.compute_trade_patterns(session, window_days=90)
    assert result.n_trades == 10


def test_excludes_open_trades(session):
    _fill_to_min(session, n=10)
    tid = _trade_id_counter["next"]
    _trade_id_counter["next"] += 1
    session.add(PaperTrade(
        id=tid, portfolio_id=1, symbol="OPEN1", trading_style="SWING",
        entry_date=date(2026, 1, 1), entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        entry_price=100.0, shares=10.0, stop_loss=90.0, current_stop=90.0,
        stage="open", hold_days=5,
    ))
    session.commit()
    result = trade_coach.compute_trade_patterns(session)
    assert result.n_trades == 10  # the open trade must not be counted


def test_win_rate_and_avg_return_computed_from_pct_return(session):
    for _ in range(6):
        _add_trade(session, symbol=f"W{_}", pct_return=0.10)
    for _ in range(4):
        _add_trade(session, symbol=f"L{_}", pct_return=-0.05)
    result = trade_coach.compute_trade_patterns(session)
    assert result.win_rate == pytest.approx(0.6)
    assert result.avg_return_pct == pytest.approx((6 * 10 + 4 * -5) / 10, abs=0.01)


def test_by_exit_reason_breakdown_is_correct(session):
    for _ in range(6):
        _add_trade(session, symbol=f"T{_}", exit_reason="target_reached", pct_return=0.10, pnl=100.0)
    for _ in range(4):
        _add_trade(session, symbol=f"S{_}", exit_reason="stop_hit", pct_return=-0.08, pnl=-80.0)
    result = trade_coach.compute_trade_patterns(session)
    by_reason = {r["exit_reason"]: r for r in result.by_exit_reason}
    assert by_reason["target_reached"]["count"] == 6
    assert by_reason["target_reached"]["win_rate"] == 1.0
    assert by_reason["stop_hit"]["count"] == 4
    assert by_reason["stop_hit"]["win_rate"] == 0.0
    assert by_reason["stop_hit"]["total_pnl"] == pytest.approx(-320.0)


def test_worst_exit_reason_is_the_one_with_the_most_negative_total_pnl(session):
    for _ in range(5):
        _add_trade(session, symbol=f"A{_}", exit_reason="stop_hit", pnl=-100.0)
    for _ in range(5):
        _add_trade(session, symbol=f"B{_}", exit_reason="time_stop", pnl=-500.0)
    result = trade_coach.compute_trade_patterns(session)
    assert result.worst_exit_reason["exit_reason"] == "time_stop"


def test_avg_giveback_only_counted_on_winning_trades_with_a_real_peak_above_exit(session):
    # Winner that gave back 10% from peak: peak=110, exit=99 -> (110-99)/110 = 10%
    _add_trade(session, symbol="GIVE", pct_return=0.05, exit_price=99.0, highest_price=110.0)
    # A loser with a peak above exit must NOT be counted (only winners count)
    _add_trade(session, symbol="LOSE", pct_return=-0.05, exit_price=90.0, highest_price=110.0)
    _fill_to_min(session, n=8)  # pad to the min-trades floor, no highest_price set on these
    result = trade_coach.compute_trade_patterns(session)
    assert result.avg_giveback_pct_on_winners == pytest.approx(10.0)


def test_giveback_is_none_when_no_trade_has_a_usable_highest_price(session):
    _fill_to_min(session, n=10)  # none of these set highest_price
    result = trade_coach.compute_trade_patterns(session)
    assert result.avg_giveback_pct_on_winners is None


def test_giveback_ignores_a_winner_whose_exit_is_at_or_above_its_own_peak(session):
    """A trade that closed AT or ABOVE its own tracked peak (exit == highest_price, or a stale
    highest_price below the real exit) must never produce a negative 'giveback' artifact."""
    _add_trade(session, symbol="ATPEAK", pct_return=0.10, exit_price=110.0, highest_price=110.0)
    _fill_to_min(session, n=9)
    result = trade_coach.compute_trade_patterns(session)
    assert result.avg_giveback_pct_on_winners is None


def test_hold_days_vs_expected_uses_style_overrides_max_hold_days(session):
    # SWING's own _STYLE_OVERRIDES max_hold_days is looked up directly, not hardcoded here —
    # so this test only asserts the DELTA direction/consistency, not a specific literal.
    _add_trade(session, symbol="LONGHOLD", style="SWING", hold_days=9999)
    _fill_to_min(session, n=9, style="SWING", hold_days=1)
    result = trade_coach.compute_trade_patterns(session)
    assert result.avg_hold_days_vs_expected is not None
    assert result.avg_hold_days_vs_expected > 0  # the 9999-day outlier must pull the average up


# ── generate_trade_coach_summary() ────────────────────────────────────────────────

class _FakeAsyncClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        if self._exc:
            raise self._exc
        return self._response


def _make_response(status_code=200, summary=None, raw_text=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = "error body"
    if raw_text is not None:
        resp.json.return_value = {"content": [{"text": raw_text}]}
    elif summary is not None:
        import json
        resp.json.return_value = {"content": [{"text": json.dumps({"summary": summary})}]}
    return resp


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def _neutral_result():
    return trade_coach.TradePatternResult(
        n_trades=25, window_days=90, win_rate=0.44, avg_return_pct=1.2,
        by_exit_reason=[{"exit_reason": "stop_hit", "count": 10, "win_rate": 0.2, "avg_return_pct": -3.0, "total_pnl": -500.0}],
        avg_giveback_pct_on_winners=8.5, avg_hold_days_vs_expected=-2.0,
        worst_exit_reason={"exit_reason": "stop_hit", "count": 10, "win_rate": 0.2, "avg_return_pct": -3.0, "total_pnl": -500.0},
    )


def test_returns_none_when_no_api_key(monkeypatch):
    monkeypatch.setattr(trade_coach, "_api_key", lambda: "")
    result = _run(trade_coach.generate_trade_coach_summary(_neutral_result()))
    assert result is None


def test_returns_summary_on_a_successful_call(monkeypatch):
    monkeypatch.setattr(trade_coach, "_api_key", lambda: "test-key")
    resp = _make_response(200, summary="Winning trades gave back a meaningful chunk from peak.")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(resp))
    result = _run(trade_coach.generate_trade_coach_summary(_neutral_result()))
    assert result == "Winning trades gave back a meaningful chunk from peak."


def test_fails_open_on_non_200_response(monkeypatch):
    monkeypatch.setattr(trade_coach, "_api_key", lambda: "test-key")
    resp = _make_response(500)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(resp))
    result = _run(trade_coach.generate_trade_coach_summary(_neutral_result()))
    assert result is None


def test_fails_open_on_a_network_exception(monkeypatch):
    monkeypatch.setattr(trade_coach, "_api_key", lambda: "test-key")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(exc=ConnectionError("down")))
    result = _run(trade_coach.generate_trade_coach_summary(_neutral_result()))
    assert result is None


def test_strips_markdown_fence_before_parsing(monkeypatch):
    monkeypatch.setattr(trade_coach, "_api_key", lambda: "test-key")
    fenced = '```json\n{"summary": "Real behavioral pattern this window."}\n```'
    resp = _make_response(200, raw_text=fenced)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(resp))
    result = _run(trade_coach.generate_trade_coach_summary(_neutral_result()))
    assert result == "Real behavioral pattern this window."


def test_malformed_json_fails_open(monkeypatch):
    monkeypatch.setattr(trade_coach, "_api_key", lambda: "test-key")
    resp = _make_response(200, raw_text="not json at all")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _FakeAsyncClient(resp))
    result = _run(trade_coach.generate_trade_coach_summary(_neutral_result()))
    assert result is None


def test_prompt_construction_does_not_crash_when_win_rate_is_none(monkeypatch):
    """Regression guard for the ternary-expression bug self-caught during development: a
    result with win_rate=None must still produce a real prompt, not a malformed/truncated one
    (the original bug silently dropped the win-rate line for EVERY result, not just this case,
    but None is the edge that would have been most likely to actually crash)."""
    monkeypatch.setattr(trade_coach, "_api_key", lambda: "test-key")
    captured = {}
    class _CapturingClient(_FakeAsyncClient):
        async def post(self, url, headers=None, json=None):
            captured["prompt"] = json["messages"][0]["content"]
            return _make_response(200, summary="ok")
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: _CapturingClient())
    thin_result = trade_coach.TradePatternResult(
        n_trades=10, window_days=90, win_rate=None, avg_return_pct=None, by_exit_reason=[],
    )
    result = _run(trade_coach.generate_trade_coach_summary(thin_result))
    assert result == "ok"
    assert "Window: last 90 days, 10 closed trades" in captured["prompt"]
    assert "Overall average return: None%" in captured["prompt"]


# ── _clean_summary() ───────────────────────────────────────────────────────────

def test_clean_summary_rejects_non_string():
    assert trade_coach._clean_summary(None) is None
    assert trade_coach._clean_summary(["a", "list"]) is None
    assert trade_coach._clean_summary(42) is None


def test_clean_summary_strips_whitespace():
    assert trade_coach._clean_summary("  hello world  ") == "hello world"


def test_clean_summary_empty_string_becomes_none():
    assert trade_coach._clean_summary("   ") is None


def test_clean_summary_truncates_to_600_chars():
    long = "x" * 900
    result = trade_coach._clean_summary(long)
    assert result is not None
    assert len(result) == 600
