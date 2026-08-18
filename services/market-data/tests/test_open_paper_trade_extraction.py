"""Regression tests for T286-CONDITIONAL-ORDER's extraction of _open_paper_trade() out of
_scan_for_entries()'s own candidate loop.

The extraction was VERBATIM (same variable names, same order of checks, same skip semantics —
each original `continue` became a `return None, "<skip_reason>"`) so that a conditional order's
"buy" action can call the exact same position-sizing/opening logic an organic entry uses,
instead of a second, independently-maintained copy. These tests exercise the extracted
function directly with real, hand-built fixture objects (matching test_should_enter_de_parity.py's
own precedent of calling paper_trading_engine.py functions directly — sqlalchemy/db are stubbed
as MagicMock in this test environment, which never raises on attribute access, so a plain
namespace-style fixture object works fine for the fields _open_paper_trade() actually reads).
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services.paper_trading_engine import _open_paper_trade


class _FakePaperTrade:
    """A real, attribute-capturing stand-in for db.PaperTrade — the module's own PaperTrade
    name is a MagicMock class in this test environment (db is stubbed wholesale, since it
    pulls in real sqlalchemy/psycopg2), so `PaperTrade(**kwargs).some_field` can never assert
    a real value against the kwargs actually passed. Patching the module's PaperTrade
    reference to this class instead makes every constructor kwarg a real, assertable
    attribute — the same "patch the module's own name, not the mock" technique already
    established elsewhere in this test suite for exactly this class of gap."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _stock(symbol="AAPL", sector="Technology", market="US"):
    return SimpleNamespace(id=1, symbol=symbol, sector=sector, market=market)


def _signal(confidence=60.0, reasons=None):
    return SimpleNamespace(id=42, confidence=confidence, reasons=reasons or {})


def _ranking(score=70.0):
    return SimpleNamespace(score=score)


def _portfolio(current_cash=100_000.0, broker_connection_id=None):
    return SimpleNamespace(id=1, current_cash=current_cash, broker_connection_id=broker_connection_id)


def _base_kwargs(**overrides):
    kwargs = dict(
        session=SimpleNamespace(add=lambda x: None),
        portfolio=_portfolio(),
        stock=_stock(),
        sig=_signal(),
        ranking=_ranking(),
        live_price=100.0,
        game_plan={"stop": 95.0, "take_profit": 115.0},
        score=6,
        notes=[],
        gate_source="de",
        cfg={
            "risk_per_trade_pct": 0.01, "max_position_pct": 0.10, "max_loss_per_trade_pct": 0.02,
            "min_entry_score": 4, "research_gating_enabled": False, "max_sector_pct": 0.25,
            "max_sector_positions": 3, "min_position_value": 200.0, "max_open_risk_pct": 0.12,
        },
        style="SWING",
        equity=100_000.0,
        regime_size_mult=1.0,
        live_regime=None,
        live_prices={},
        prefetched_open=[],
        atr=2.0,
    )
    kwargs.update(overrides)
    return kwargs


def _call(**overrides):
    return _open_paper_trade(**_base_kwargs(**overrides))


class _FakePlaceBrokerEntry:
    called = False

    def __call__(self, *a, **kw):
        _FakePlaceBrokerEntry.called = True


@pytest.fixture(autouse=True)
def _no_research_http(monkeypatch):
    """research_gating_enabled=False in every fixture cfg above already skips the HTTP call,
    but patch httpx defensively too so a future fixture that forgets this flag can't make a
    real network call during tests."""
    import httpx as _httpx
    monkeypatch.setattr(_httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(ConnectionError("no network in tests")))


@pytest.fixture(autouse=True)
def _real_papertrade_class(monkeypatch):
    """See _FakePaperTrade's own docstring — makes constructor kwargs real, assertable
    attributes instead of opaque MagicMock attribute accesses."""
    import src.services.paper_trading_engine as pte
    monkeypatch.setattr(pte, "PaperTrade", _FakePaperTrade)


def test_a_normal_candidate_opens_a_trade_with_no_skip_reason():
    trade, skip_reason = _call()
    assert skip_reason is None
    assert trade is not None
    assert trade.symbol == "AAPL"
    assert trade.stop_loss == 95.0
    assert trade.take_profit == 115.0


def test_shares_computed_from_risk_dollar_over_stop_distance():
    """Hand-computed expectation, corrected for BOTH real multipliers this scenario actually
    hits (a mistake caught by the test itself failing on first run, not assumed correct):
    risk_per_trade_pct=0.01, equity=100_000 -> risk_base=1000. confidence=60 hits the
    >=50 branch -> confidence_size_mult=1.25 (NOT 1.0 — sig_conf=60 clears the 50 threshold).
    score=4 exactly at min_entry_score -> score_size_mult=0.75. risk_dollar = 1000*1.25*0.75
    = 937.5, stop_distance=5 -> shares = 187.5 BEFORE the max_position_pct cap. max_pos =
    equity(100_000) * max_position_pct(0.10) = 10_000; at live_price=100, 187.5 shares would
    be worth $18,750 > $10,000, so the cap fires: shares = 10_000 / 100 = 100.0 exactly."""
    trade, skip_reason = _call(score=4)  # exactly at min_entry_score -> score_size_mult floor of 0.75
    assert skip_reason is None
    assert trade.shares == pytest.approx(100.0, abs=0.5)


def test_stop_distance_non_positive_skips_with_reason():
    trade, skip_reason = _call(game_plan={"stop": 100.0, "take_profit": 115.0})
    assert trade is None
    assert skip_reason == "invalid_stop_distance"


def test_avoid_research_recommendation_hard_gates_even_when_enabled(monkeypatch):
    """_svc_token() (JWT construction) genuinely fails against this test environment's mocked
    settings object — patched here so the research-fetch's own try/except doesn't silently
    swallow the whole block before ever reaching the mocked httpx.get() below. Caught by this
    test itself failing on first run (the gate never fired because _research_rec stayed "",
    not because the gate logic was wrong) — a real, pre-existing test-environment limitation,
    not a bug in the extraction."""
    import httpx as _httpx
    import src.services.paper_trading_engine as pte

    class _FakeResp:
        status_code = 200
        def json(self):
            return {"recommendation": "AVOID", "overall_score": 30}

    monkeypatch.setattr(_httpx, "get", lambda *a, **kw: _FakeResp())
    monkeypatch.setattr(pte, "_svc_token", lambda: "fake-token")
    trade, skip_reason = _call(cfg={**_base_kwargs()["cfg"], "research_gating_enabled": True})
    assert trade is None
    assert skip_reason == "research_gate"


def test_min_position_value_floor_skips_a_too_small_position():
    trade, skip_reason = _call(
        equity=1_000.0,  # tiny equity -> tiny risk_dollar -> tiny position_value
        cfg={**_base_kwargs()["cfg"], "min_position_value": 5000.0},
    )
    assert trade is None
    assert skip_reason == "min_position"


def test_open_risk_cap_skips_when_aggregate_risk_would_exceed_the_limit():
    existing_trade = SimpleNamespace(symbol="MSFT", entry_price=200.0, current_stop=180.0, shares=1000.0, current_price=200.0)
    existing_stock = SimpleNamespace(sector="Technology")
    trade, skip_reason = _call(
        equity=5_000.0,  # small equity relative to the pre-existing open risk below
        prefetched_open=[(existing_trade, existing_stock)],
        cfg={**_base_kwargs()["cfg"], "max_open_risk_pct": 0.01},
    )
    assert trade is None
    assert skip_reason == "open_risk_cap"


def test_sector_concentration_cap_skips_when_the_new_position_would_breach_it():
    """max_open_risk_pct raised to a level the existing MSFT position's own open risk
    ($20,000 = (200-180)*1000 shares, 20% of equity) does NOT trip on its own — isolating
    this test to the sector-cap condition specifically, not an incidental open-risk-cap trip
    that would mask which gate is actually being tested."""
    existing_trade = SimpleNamespace(symbol="MSFT", entry_price=200.0, current_stop=180.0, shares=1000.0, current_price=200.0)
    existing_stock = SimpleNamespace(sector="Technology")
    trade, skip_reason = _call(
        prefetched_open=[(existing_trade, existing_stock)],
        cfg={**_base_kwargs()["cfg"], "max_sector_pct": 0.001, "max_open_risk_pct": 0.50},
    )
    assert trade is None
    assert skip_reason == "sector_cap"


def test_sector_position_count_cap_skips_when_already_at_the_limit():
    same_sector_trades = [
        (SimpleNamespace(symbol=f"SYM{i}", entry_price=50.0, current_stop=45.0, shares=10.0, current_price=50.0), _stock(symbol=f"SYM{i}"))
        for i in range(3)
    ]
    trade, skip_reason = _call(
        prefetched_open=same_sector_trades,
        cfg={**_base_kwargs()["cfg"], "max_sector_positions": 3},
    )
    assert trade is None
    assert skip_reason == "sector_count_cap"


def test_insufficient_cash_skips_the_trade():
    trade, skip_reason = _call(portfolio=_portfolio(current_cash=1.0))
    assert trade is None
    assert skip_reason == "insufficient_cash"


def test_hk_market_rounds_shares_down_to_a_whole_board_lot():
    trade, skip_reason = _call(
        stock=_stock(market="HK"),
        cfg={**_base_kwargs()["cfg"], "market": "HK"},
        equity=200_000.0,
    )
    assert skip_reason is None
    assert trade.shares == int(trade.shares)  # a whole number of shares, no fractional lot


def test_broker_entry_is_placed_when_portfolio_has_a_broker_connection():
    with patch("src.services.paper_trading_engine._place_broker_entry") as mock_place:
        trade, skip_reason = _call(portfolio=_portfolio(broker_connection_id=7))
        assert skip_reason is None
        mock_place.assert_called_once()


def test_broker_entry_is_not_placed_without_a_broker_connection():
    with patch("src.services.paper_trading_engine._place_broker_entry") as mock_place:
        trade, skip_reason = _call(portfolio=_portfolio(broker_connection_id=None))
        assert skip_reason is None
        mock_place.assert_not_called()


def test_kscore_at_entry_comes_from_the_passed_in_ranking():
    trade, skip_reason = _call(ranking=_ranking(score=88.5))
    assert skip_reason is None
    assert trade.kscore_at_entry == 88.5


def test_kscore_at_entry_is_none_without_a_ranking():
    trade, skip_reason = _call(ranking=None)
    assert skip_reason is None
    assert trade.kscore_at_entry is None
