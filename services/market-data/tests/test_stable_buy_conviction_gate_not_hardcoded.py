"""Regression test for the conviction-gate-neutralization bug found in the 2026-09-06 deep audit
(Finding A / priority item #1).

scheduler.py's check_signal_alerts(), in its `prev == current` ("stable BUY, nothing changed since
last cycle") branch, used to compute `all_pass` via _is_conviction_buy() and then discard it,
hardcoding `True` into _store_conviction()'s `sent` parameter:

    all_pass, _tier, passed, failed = _is_conviction_buy(...)
    _store_conviction(alert.symbol, style, True, passed, failed, current, sent_at=db_sent_at)

Both real consumers of the persisted `conv_gate:{symbol}:{style}` Redis value gate strictly on
`sent is False`:
  - decision-engine/src/api/core/hard_rejects.py:599
  - market-data/src/services/paper_trading_engine.py:5778 (analogous read)

Since check_signal_alerts() runs 5x/day and the transition-into-BUY branch (which correctly writes
`sent=False` on conviction failure) only fires ONCE per BUY streak, any BUY older than one refresh
cycle took the `prev == current` branch on every subsequent run — permanently overwriting a failing
gate back to `sent=True`. That silently disabled the conviction gate (including its AUD-CHASE-ROC10
and AUD232-BUY-FROM-TOP enforcement) for the entire remaining lifetime of any stable BUY signal.

This test verifies _store_conviction() persists the REAL passed-in value, both when the gate passes
and when it fails — the second case is exactly what the bug hardcoded away.
"""
import json

import pytest


class _FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.fixture
def fake_redis(monkeypatch):
    import src.services.scheduler as sch

    fr = _FakeRedis()
    monkeypatch.setattr(sch, "_get_redis", lambda: fr)
    return fr


def test_store_conviction_persists_true_when_gate_passes(fake_redis):
    import src.services.scheduler as sch

    sch._store_conviction("AAPL", "SWING", True, ["RSI", "MACD"], [], "BUY")
    data = json.loads(fake_redis.store["conv_gate:AAPL:SWING"])
    assert data["sent"] is True


def test_store_conviction_persists_false_when_gate_fails(fake_redis):
    """The exact case the bug destroyed: a stable BUY whose conviction gate is now failing must
    have `sent=False` persisted, not silently overwritten to True."""
    import src.services.scheduler as sch

    sch._store_conviction(
        "AAPL", "SWING", False, ["RSI"], ["OBV", "roc_10 too hot"], "BUY",
    )
    data = json.loads(fake_redis.store["conv_gate:AAPL:SWING"])
    assert data["sent"] is False


def test_stable_buy_refresh_call_site_passes_computed_all_pass_not_a_literal():
    """Source-level guard: the specific call inside the `prev == current` / `current == "BUY"`
    stable-refresh branch must pass the *variable* `all_pass` (computed from _is_conviction_buy)
    as _store_conviction's 3rd positional argument, never a hardcoded literal. This is the exact
    regression shape of the original bug and would not be caught by unit-testing
    _store_conviction() in isolation, since that function behaves correctly for either input —
    the bug was entirely in what the CALLER passed to it.
    """
    import pathlib

    src_path = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
    )
    source = src_path.read_text()

    anchor = 'if current == "BUY":\n                        sig_data = signal_details.get(key) or {}'
    idx = source.index(anchor)
    # The _store_conviction(...) call within this branch, up to its closing paren.
    call_start = source.index("_store_conviction(", idx)
    call_end = source.index(")", call_start)
    call_text = source[call_start:call_end]

    assert "_store_conviction(alert.symbol, style, all_pass," in call_text, (
        "The stable-BUY refresh branch must pass the computed `all_pass` value into "
        "_store_conviction(), not a hardcoded True/False literal — that literal is exactly what "
        "silently disabled the conviction gate for every BUY older than one refresh cycle."
    )
