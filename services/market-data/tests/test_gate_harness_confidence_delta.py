"""Tests for T232-DL-DUALSCORER-DEBT item #2 — threading confidence_delta into gate_harness.py's
replay of _should_enter().

Background: _should_enter() reads confidence_delta directly off signal_data (a top-level key,
not nested in reasons) — set by _scan_for_entries()'s own live query: the most recent PRIOR
Signal row (Signal.ts < sig.ts, same stock+horizon), diffed against the current signal's own
confidence. gate_harness.py's replay_should_enter()/replay_extended_gates() never populated
this key at all (always implicitly None via dict.get() defaulting), silently compressing the
replayed score distribution relative to what a live call actually sees.

_historical_confidence_delta() reconstructs this point-in-time-safely: Signal has a real
per-calendar-day row history (confirmed against production — rows == distinct_days for every
(stock_id, horizon) pair, matching the table's own uq_signals_stock_horizon_day unique index),
so "the prior day's confidence, strictly before this signal's own date" is a real, queryable,
look-ahead-safe fact — unlike sig.reasons, which IS overwritten intraday and cannot be
time-traveled through.

gate_harness.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy itself as a MagicMock) — matches test_gate_harness_extended.py's established
technique: pop the stub, build ONE shared in-memory engine + real models while real sqlalchemy
is active, then restore the stub immediately.
"""
import sys

_STUBBED_MODULES = ("sqlalchemy", "sqlalchemy.orm", "sqlalchemy.dialects", "sqlalchemy.dialects.postgresql", "db")
_saved_stubs = {_mod: sys.modules.pop(_mod, None) for _mod in _STUBBED_MODULES}

import importlib.util
import pathlib
from datetime import date, datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_gh_cd", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_gh_cd"] = _models
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
Market = _models.Market
Exchange = _models.Exchange
SignalHorizon = _models.SignalHorizon
SignalType = _models.SignalType

_GH_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "gate_harness.py"
_GH_SOURCE = _GH_PATH.read_text()


def _extract_historical_confidence_delta():
    start = _GH_SOURCE.index("def _historical_confidence_delta(")
    end = _GH_SOURCE.index("\ndef _fetch_matched_signals(", start)
    func_source = _GH_SOURCE[start:end]
    namespace = {"select": select, "Signal": Signal, "SignalHorizon": SignalHorizon, "Session": Session, "date": date}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source, matching repo convention
    return namespace["_historical_confidence_delta"]


_historical_confidence_delta = _extract_historical_confidence_delta()


def _make_session() -> Session:
    return Session(_ENGINE)


def _insert_stock(session, stock_id, symbol):
    session.add(Stock(id=stock_id, symbol=symbol, market=Market.US, exchange=Exchange.NASDAQ, name="Test Co"))
    session.commit()


_next_signal_id = [1]


def _insert_signal(session, stock_id, horizon, ts, confidence):
    # Signal.id is a BigInteger primary key — SQLite doesn't autoincrement those implicitly
    # (a real Postgres sequence handles it in production); assign explicitly, matching the
    # same pattern already established for Ranking/Price/SignalOutcome in this test suite.
    session.add(Signal(
        id=_next_signal_id[0], stock_id=stock_id, horizon=SignalHorizon(horizon), ts=ts,
        signal=SignalType.BUY, confidence=confidence, bullish_probability=0.6,
    ))
    _next_signal_id[0] += 1
    session.commit()


class TestHistoricalConfidenceDelta:
    def test_computes_the_delta_against_the_most_recent_prior_signal_row(self):
        session = _make_session()
        _insert_stock(session, 20, "CD1")
        _insert_signal(session, 20, "SWING", datetime(2026, 6, 1, tzinfo=timezone.utc), confidence=50.0)
        _insert_signal(session, 20, "SWING", datetime(2026, 6, 15, tzinfo=timezone.utc), confidence=65.0)
        # Replay as-of 2026-06-20: the "current" signal (confidence=70) is a THIRD row, not
        # yet inserted here — this is the day being scored; the prior row must be the 6/15 one.
        result = _historical_confidence_delta(session, 20, "SWING", date(2026, 6, 20), current_confidence=70.0)
        assert result == 5.0  # 70.0 - 65.0
        session.close()

    def test_never_leaks_a_signal_row_dated_strictly_after_the_replayed_signal_date(self):
        """Point-in-time correctness is the whole point of this fix — a Signal row from AFTER
        the date being replayed must never be treated as the "prior" one."""
        session = _make_session()
        _insert_stock(session, 21, "CD2")
        _insert_signal(session, 21, "SWING", datetime(2026, 6, 1, tzinfo=timezone.utc), confidence=40.0)
        # A row dated well AFTER the replayed date — must never leak in as "prior".
        _insert_signal(session, 21, "SWING", datetime(2026, 6, 25, tzinfo=timezone.utc), confidence=99.0)
        result = _historical_confidence_delta(session, 21, "SWING", date(2026, 6, 20), current_confidence=50.0)
        assert result == 10.0  # 50.0 - 40.0, NOT 50.0 - 99.0
        session.close()

    def test_uses_the_strict_less_than_operator_not_less_than_or_equal(self):
        """A same-calendar-day boundary case can't be reliably constructed against SQLite here
        (SQLite lexicographically compares a tz-aware DATETIME string against a bare DATE
        string as always-greater regardless of `<` vs `<=` — confirmed directly: both operators
        produced IDENTICAL results for every same-day fixture tried, a genuine SQLite-vs-
        Postgres semantic gap, not a property this function's own correctness can be judged by
        here). Falls back to a direct source-text check of the real query, matching this
        repo's established precedent for exactly this class of untestable-via-SQLite boundary —
        the live _scan_for_entries() query itself uses `Signal.ts < sig.ts` (strict), which this
        function must mirror exactly."""
        import pathlib
        gh_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "gate_harness.py"
        source = gh_path.read_text()
        start = source.index("def _historical_confidence_delta(")
        end = source.index("\ndef _fetch_matched_signals(", start)
        body = source[start:end]
        assert "Signal.ts < signal_date" in body
        assert "Signal.ts <= signal_date" not in body

    def test_returns_none_when_no_prior_signal_row_exists(self):
        """A symbol's very first signal for this horizon has no prior day to diff against —
        must degrade to None, not fabricate a delta of 0 (which would silently mean "flat
        trajectory" instead of "trajectory unknown")."""
        session = _make_session()
        _insert_stock(session, 22, "CD3")
        result = _historical_confidence_delta(session, 22, "SWING", date(2026, 6, 20), current_confidence=70.0)
        assert result is None
        session.close()

    def test_returns_none_when_current_confidence_itself_is_none(self):
        session = _make_session()
        _insert_stock(session, 23, "CD4")
        _insert_signal(session, 23, "SWING", datetime(2026, 6, 1, tzinfo=timezone.utc), confidence=40.0)
        result = _historical_confidence_delta(session, 23, "SWING", date(2026, 6, 20), current_confidence=None)
        assert result is None
        session.close()

    def test_only_matches_the_same_horizon_not_a_different_style(self):
        """A LONG-horizon prior signal must never be used to compute a SWING-horizon delta —
        the two are scored under completely different regimes/thresholds."""
        session = _make_session()
        _insert_stock(session, 24, "CD5")
        _insert_signal(session, 24, "LONG", datetime(2026, 6, 1, tzinfo=timezone.utc), confidence=10.0)
        _insert_signal(session, 24, "SWING", datetime(2026, 6, 10, tzinfo=timezone.utc), confidence=55.0)
        result = _historical_confidence_delta(session, 24, "SWING", date(2026, 6, 20), current_confidence=70.0)
        assert result == 15.0  # 70.0 - 55.0 (the SWING row), NOT 70.0 - 10.0 (the LONG row)
        session.close()

    def test_only_matches_the_same_stock_not_a_different_symbol(self):
        session = _make_session()
        _insert_stock(session, 25, "CD6A")
        _insert_stock(session, 26, "CD6B")
        _insert_signal(session, 25, "SWING", datetime(2026, 6, 1, tzinfo=timezone.utc), confidence=10.0)
        _insert_signal(session, 26, "SWING", datetime(2026, 6, 10, tzinfo=timezone.utc), confidence=55.0)
        result = _historical_confidence_delta(session, 26, "SWING", date(2026, 6, 20), current_confidence=70.0)
        assert result == 15.0  # 70.0 - 55.0 (stock 26's own row), NOT 70.0 - 10.0 (stock 25's row)
        session.close()

    def test_result_is_rounded_to_one_decimal_matching_the_live_computation(self):
        session = _make_session()
        _insert_stock(session, 27, "CD7")
        _insert_signal(session, 27, "SWING", datetime(2026, 6, 1, tzinfo=timezone.utc), confidence=33.333)
        result = _historical_confidence_delta(session, 27, "SWING", date(2026, 6, 20), current_confidence=50.111)
        assert result == round(50.111 - 33.333, 1)
        session.close()


# ── replay_should_enter()/replay_extended_gates() wiring — source-text regression checks ──────
#
# Matches test_gate_harness_extended.py's established proportionate-testing precedent: the
# full replay functions are too heavy (DB fan-out, ATR, game-plan construction) to drive
# end-to-end here — these guard the exact SHAPE of the fix instead.

def test_replay_should_enter_computes_confidence_delta_before_building_signal_data():
    start = _GH_SOURCE.index("def replay_should_enter(")
    end = _GH_SOURCE.index("\ndef ", start + 1)
    body = _GH_SOURCE[start:end]
    assert "_historical_confidence_delta(" in body
    assert '"confidence_delta": confidence_delta' in body


def test_replay_extended_gates_computes_confidence_delta_before_building_signal_data():
    start = _GH_SOURCE.index("def replay_extended_gates(")
    end = _GH_SOURCE.index("\ndef ", start + 1)
    body = _GH_SOURCE[start:end]
    assert "_historical_confidence_delta(" in body
    assert '"confidence_delta": confidence_delta' in body


def test_module_docstring_discloses_the_live_regime_gap_is_permanent_not_an_oversight():
    """This is the one gap that CANNOT be fixed (no historical regime persistence exists) — the
    module's own docstring must say so explicitly, not leave a future reader to assume it was
    silently patched over."""
    assert "live_regime" in _GH_SOURCE
    assert "no historical persistence" in _GH_SOURCE or "no time-series" in _GH_SOURCE


def test_walk_forward_notes_disclose_the_fallback_only_scope():
    """Both walk-forward functions' own `note` field must tell a human reading promoted=True
    that this harness only tunes the DE-outage fallback path, not the live primary one."""
    assert "decision_engine_mode='primary'" in _GH_SOURCE
    assert _GH_SOURCE.count("does NOT tune the live primary trading") == 2
