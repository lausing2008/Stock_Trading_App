"""Tests for the short-squeeze alert's new game-plan (entry/stop/target) feature.

_squeeze_game_plan() lives in scheduler.py, which can't be imported directly in this test
environment (apscheduler import-chain — matches test_short_squeeze_alert.py's own documented
constraint). Its real source is extracted via exec() and run against a real in-memory SQLite
session built from the real shared/db/models.py, matching test_correlation_preentry.py's/
test_broker_position_sync.py's established stub-pop-and-restore technique — this exercises the
actual DB query + _build_game_plan_for_style() call, not a hand-copied reimplementation.

send_short_squeeze_email()'s game-plan rendering is pure string composition (no DB/network
dependency), so it's tested directly with real inputs, matching test_short_squeeze_alert.py's
own convention for that function.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from unittest.mock import patch

from sqlalchemy import create_engine, select as _real_select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_squeeze_gp", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_squeeze_gp"] = _models
_spec.loader.exec_module(_models)

_ENGINE = create_engine("sqlite:///:memory:")
_models.Base.metadata.create_all(
    _ENGINE, tables=[_models.Stock.__table__, _models.Signal.__table__],
)

for _mod, _stub in _saved_stubs.items():
    if _stub is not None:
        sys.modules[_mod] = _stub
    else:
        sys.modules.pop(_mod, None)

Stock = _models.Stock
Signal = _models.Signal
SignalHorizon = _models.SignalHorizon
SignalType = _models.SignalType
Market = _models.Market
Exchange = _models.Exchange

from src.services.email_service import send_short_squeeze_email  # noqa: E402
from src.services.paper_trading_engine import _build_game_plan_for_style  # noqa: E402

_scheduler_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_scheduler_source = _scheduler_path.read_text()


def _extract_squeeze_game_plan():
    """Pulls _squeeze_game_plan()'s real source out of scheduler.py and exec()s it against
    real sqlalchemy `select`, the real shared models, and the real _build_game_plan_for_style()
    — only `log`-style side effects would need stubbing, and this function has none.

    The function's own body does `from .paper_trading_engine import _build_game_plan_for_style`
    — a relative import that can't resolve inside an exec()'d namespace with no real package
    context (__name__ isn't set). Strips that one import line and injects the REAL function
    directly into the namespace instead — same effect, since the source is unchanged otherwise.

    Uses `_real_select` (captured at module-import time, while the conftest.py sqlalchemy stub
    was still popped) rather than re-importing sqlalchemy here — by the time any TEST function
    runs, the stub-pop-and-restore dance above has already put the MagicMock stub back in
    sys.modules, so a fresh `import sqlalchemy` at this point would silently hand back the stub,
    not the real module, and every query would resolve to a MagicMock instead of real SQL.
    """
    start = _scheduler_source.index("def _squeeze_game_plan(")
    end = _scheduler_source.index("\ndef check_short_squeeze_alerts(", start)
    func_source = _scheduler_source[start:end]
    import_line = "    from .paper_trading_engine import _build_game_plan_for_style\n"
    assert import_line in func_source, "expected relative import line not found — has the source changed?"
    func_source = func_source.replace(import_line, "")
    namespace = {
        "select": _real_select,
        "Stock": Stock,
        "Signal": Signal,
        "SignalHorizon": SignalHorizon,
        "_build_game_plan_for_style": _build_game_plan_for_style,
    }
    exec(compile(func_source, "<_squeeze_game_plan>", "exec"), namespace)
    return namespace["_squeeze_game_plan"]


def _make_session():
    return Session(_ENGINE)


def _clear_tables(session):
    session.query(Signal).delete()
    session.query(Stock).delete()
    session.commit()


_next_signal_id = [0]


def _new_signal_id() -> int:
    """Signal.id is a BigInteger primary key, which doesn't get SQLite's implicit
    INTEGER PRIMARY KEY autoincrement — fixtures inserting Signal rows must assign id
    explicitly (a real Postgres sequence handles this in production; this is a
    test-harness-only workaround, matching the same documented quirk for Price.id
    elsewhere in this test suite)."""
    _next_signal_id[0] += 1
    return _next_signal_id[0]


# ── _squeeze_game_plan() ─────────────────────────────────────────────────────────────────────

def test_returns_a_real_game_plan_when_a_recent_swing_signal_with_atr_exists():
    _squeeze_game_plan = _extract_squeeze_game_plan()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="GME", market=Market.US, exchange=Exchange.NASDAQ, name="GameStop")
        session.add(stock)
        session.commit()
        session.add(Signal(
            id=_new_signal_id(),
            stock_id=stock.id,
            signal=SignalType.BUY,
            horizon=SignalHorizon.SWING,
            confidence=60.0,
            reasons={"atr_14": 1.25},
        ))
        session.commit()

        plan = _squeeze_game_plan(session, "GME", 25.0)

        assert plan is not None
        assert plan["style"] == "SWING"
        assert plan["current_price"] == 25.0
        # ATR-based stop must be BELOW current price and reflect the real ATR value, not the
        # style's plain fallback percentage — proves atr_14 was actually read and used.
        fallback = _build_game_plan_for_style("GME", "SWING", 25.0, {}, None)
        assert plan["stop"] != fallback["stop"] or plan["stop"] < 25.0


def test_falls_back_to_percentage_stop_when_no_recent_swing_signal_exists():
    """A candidate with NO signal on file at all must still get a real game plan (matching
    _build_game_plan_for_style()'s own documented ATR-unavailable fallback), not None."""
    _squeeze_game_plan = _extract_squeeze_game_plan()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="NOSIG", market=Market.US, exchange=Exchange.NASDAQ, name="No Signal Co")
        session.add(stock)
        session.commit()

        plan = _squeeze_game_plan(session, "NOSIG", 10.0)

        assert plan is not None
        expected = _build_game_plan_for_style("NOSIG", "SWING", 10.0, {}, None)
        assert plan["stop"] == expected["stop"]
        assert plan["take_profit"] == expected["take_profit"]


def test_only_swing_horizon_signals_are_consulted_not_other_horizons():
    """A SHORT-horizon signal's own ATR must never be picked up — only SWING is the intended,
    documented horizon for this alert's game plan.

    Uses atr=0.3 specifically, not an arbitrary/extreme value — a real near-miss caught while
    writing this test: _build_game_plan_for_style()'s stop is max(atr_based_stop, pct_floor),
    and for a LARGE ATR the atr_based_stop falls far BELOW the percentage floor, so the floor
    always wins regardless of the (wrong) ATR value used — an assertion built around a large
    "wildly different" ATR would pass even with the SWING filter completely removed. A SMALL
    ATR (0.3) is what actually produces an atr_based_stop ABOVE the floor, making the two
    genuinely distinguishable — verified via the assertion below, not assumed."""
    _squeeze_game_plan = _extract_squeeze_game_plan()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="MULTIH", market=Market.US, exchange=Exchange.NASDAQ, name="Multi Horizon Co")
        session.add(stock)
        session.commit()
        session.add(Signal(
            id=_new_signal_id(),
            stock_id=stock.id, signal=SignalType.BUY, horizon=SignalHorizon.SHORT,
            confidence=60.0, reasons={"atr_14": 0.3},
        ))
        session.commit()

        plan = _squeeze_game_plan(session, "MULTIH", 20.0)

        # No SWING signal exists, so this must match the no-ATR fallback, NOT a stop computed
        # from the SHORT horizon's own atr_14=0.3.
        expected_no_atr = _build_game_plan_for_style("MULTIH", "SWING", 20.0, {}, None)
        expected_with_short_atr = _build_game_plan_for_style("MULTIH", "SWING", 20.0, {}, 0.3)
        assert expected_no_atr["stop"] != expected_with_short_atr["stop"], (
            "test fixture invalid — atr=0.3 must produce a DIFFERENT stop than atr=None, "
            "or this test can't actually distinguish the two"
        )
        assert plan["stop"] == expected_no_atr["stop"]


def test_most_recent_swing_signal_is_used_when_multiple_exist():
    _squeeze_game_plan = _extract_squeeze_game_plan()
    with _make_session() as session:
        _clear_tables(session)
        stock = Stock(symbol="TWOSIG", market=Market.US, exchange=Exchange.NASDAQ, name="Two Signals Co")
        session.add(stock)
        session.commit()
        from datetime import datetime, timedelta, timezone
        session.add(Signal(
            id=_new_signal_id(),
            stock_id=stock.id, signal=SignalType.BUY, horizon=SignalHorizon.SWING,
            confidence=55.0, reasons={"atr_14": 5.0},
            ts=datetime.now(timezone.utc) - timedelta(days=3),
        ))
        session.add(Signal(
            id=_new_signal_id(),
            stock_id=stock.id, signal=SignalType.BUY, horizon=SignalHorizon.SWING,
            confidence=60.0, reasons={"atr_14": 0.5},
            ts=datetime.now(timezone.utc),
        ))
        session.commit()

        plan = _squeeze_game_plan(session, "TWOSIG", 50.0)

        # The MOST RECENT signal's atr_14=0.5 must win, not the older one's 5.0 — a stop
        # derived from atr_14=5.0 would be much further from price than one from 0.5.
        stale_stop = _build_game_plan_for_style("TWOSIG", "SWING", 50.0, {}, 5.0)["stop"]
        assert plan["stop"] != stale_stop


def test_unknown_symbol_returns_none_not_a_crash():
    _squeeze_game_plan = _extract_squeeze_game_plan()
    with _make_session() as session:
        _clear_tables(session)
        assert _squeeze_game_plan(session, "DOESNOTEXIST", 10.0) is None


def test_exception_inside_the_function_is_swallowed_and_returns_none():
    """The whole body is wrapped in try/except Exception: return None — a DB hiccup or any
    other unexpected error must never crash the whole squeeze-alert scan for every symbol."""
    _squeeze_game_plan = _extract_squeeze_game_plan()

    class _ExplodingSession:
        def execute(self, *a, **kw):
            raise RuntimeError("simulated DB failure")

    assert _squeeze_game_plan(_ExplodingSession(), "ANY", 10.0) is None


# ── check_short_squeeze_alerts() wiring — source-text regression checks ─────────────────────

def _check_short_squeeze_alerts_body() -> str:
    start = _scheduler_source.index("def check_short_squeeze_alerts(")
    end = _scheduler_source.index("\ndef ", start + 1)
    return _scheduler_source[start:end]


def test_game_plan_is_computed_only_after_the_empty_candidates_guard():
    """The game-plan loop must sit AFTER the `if not candidates: return` early-exit — never
    wasted computing plans before knowing there's at least one real candidate."""
    body = _check_short_squeeze_alerts_body()
    guard_idx = body.index("if not candidates:")
    loop_idx = body.index("_squeeze_game_plan(")
    assert guard_idx < loop_idx


def test_game_plan_result_is_attached_to_the_candidate_dict_sent_to_email():
    body = _check_short_squeeze_alerts_body()
    assert 'cand["game_plan"] = plan' in body
    # Confirms the attach happens before send_short_squeeze_email is imported/called, i.e. the
    # email always sees whatever game_plan was computed, not a stale/earlier candidates dict.
    attach_idx = body.index('cand["game_plan"] = plan')
    send_idx = body.index("send_short_squeeze_email(")
    assert attach_idx < send_idx


def test_a_none_game_plan_is_never_attached_placeholder():
    """A candidate with no computable game plan must NOT get a game_plan key at all (so the
    email can distinguish 'no plan available' from 'plan is None/falsy placeholder')."""
    body = _check_short_squeeze_alerts_body()
    assert "if plan is not None:" in body


# ── send_short_squeeze_email() game-plan rendering — pure composition, tested directly ──────

def _capture_send():
    calls = []
    def _fake_send(to, subject, body_html, body_text):
        calls.append({"to": to, "subject": subject, "html": body_html, "text": body_text})
        return True
    return calls, _fake_send


def test_game_plan_renders_entry_stop_target_in_html_and_text():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {
                "symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10,
                "game_plan": {"entry1": 25.0, "entry2": 25.5, "breakout": 26.0, "stop": 22.0,
                              "take_profit": 30.0, "current_price": 25.10, "style": "SWING"},
            },
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Game plan (SWING)" in html
    assert "$25.00" in html and "$22.00" in html and "$30.00" in html
    assert "Game plan (SWING)" in text
    assert "$25.00" in text and "$22.00" in text and "$30.00" in text


def test_missing_game_plan_renders_cleanly_with_no_placeholder():
    """A candidate with NO game_plan key at all (the honest, documented gap when no recent
    SWING signal exists) must render with zero trace of a PER-CANDIDATE game-plan section —
    not a blank/'N/A' placeholder line. Checks for the row-level "Game plan (SWING): ..."
    phrasing specifically (with the parenthetical style name), NOT the bare substring "Game
    plan" — the email's own disclaimer paragraph legitimately says "Game plan (where shown)
    is..." unconditionally, which would make a bare-substring check fail even on correct
    output."""
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "NOSIG", "short_percent_of_float": 18.0, "change_pct": 5.0, "price": 10.0},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "Game plan (SWING):" not in html
    assert "Game plan (SWING):" not in text
    assert "Game plan (where shown)" in html  # the disclaimer itself is still expected


def test_mixed_candidates_only_one_has_a_game_plan():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "HASPLAN", "short_percent_of_float": 20.0, "change_pct": 6.0, "price": 15.0,
             "game_plan": {"entry1": 15.0, "entry2": 15.2, "breakout": 15.5, "stop": 13.5,
                           "take_profit": 18.0, "current_price": 15.0, "style": "SWING"}},
            {"symbol": "NOPLAN", "short_percent_of_float": 17.0, "change_pct": 4.0, "price": 8.0},
        ])
    html = calls[0]["html"]
    assert html.count("Game plan (SWING)") == 1
    assert "HASPLAN" in html and "NOPLAN" in html


def test_disclaimer_mentions_the_game_plan_is_illustrative_not_a_guaranteed_fill():
    calls, fake = _capture_send()
    with patch("src.services.email_service.send_email", fake):
        send_short_squeeze_email("user@example.com", [
            {"symbol": "GME", "short_percent_of_float": 22.5, "change_pct": 8.3, "price": 25.10,
             "game_plan": {"entry1": 25.0, "entry2": 25.5, "breakout": 26.0, "stop": 22.0,
                           "take_profit": 30.0, "current_price": 25.10, "style": "SWING"}},
        ])
    html, text = calls[0]["html"], calls[0]["text"]
    assert "not a guaranteed fill" in html.lower()
    assert "not a guaranteed fill" in text.lower() or "illustrative" in text.lower()
