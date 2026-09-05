"""AUD-BACKTEST-SURVIVORSHIP: the backtester had no concept of delisting.

§F.3 of the REVISED 2026-09-04 audit prompt asks for a survivorship-bias audit of existing
backtests — explicitly NOT blocked, unlike the walk-forward harness. The finding: strategy-engine
contained ZERO references to `delisted`. The engine reads price bars only, so a delisted symbol
backtests cleanly right up to its final bar and simply omits the delisting outcome — inflating
results in exactly the case where the real-world result was a total or near-total loss.

Currently LATENT, not active: the universe holds 0 delisted stocks of 193, so the omission has
no present effect. That is precisely why it is worth fixing now — before the first delisting
arrives, not after.

Deliberately a FLAG, not a hard block: refusing to backtest is the wrong default for a research
tool, and a caller who knowingly wants pre-delisting history should still get it. The flag makes
the caveat impossible to miss instead of invisible.
"""
import pathlib

_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py").read_text()


def _backtest_fn() -> str:
    start = _SOURCE.index("def backtest(")
    return _SOURCE[start:_SOURCE.index("\n@router.", start)]


def test_stock_model_is_imported():
    """The lookup needs Stock; a missing import would NameError at request time."""
    assert "from db import Backtest, Stock, Strategy, TimeFrame, get_session" in _SOURCE


def test_delisted_status_is_looked_up():
    fn = _backtest_fn()
    assert "select(Stock.delisted).where(Stock.symbol == body.symbol.upper())" in fn


def test_lookup_uses_the_existing_request_session():
    """Opening a second SessionLocal inside a request-scoped handler would leak connections."""
    fn = _backtest_fn()
    assert "session.execute(" in fn
    assert "SessionLocal" not in fn


def test_symbol_is_upper_cased_for_the_lookup():
    """Stock.symbol is stored upper-case; a lower-case body.symbol would silently miss."""
    assert "body.symbol.upper()" in _backtest_fn()


def test_lookup_fails_open():
    """A DB hiccup must never block a real backtest — the flag is advisory, not a gate."""
    fn = _backtest_fn()
    idx = fn.index("_delisted = bool(")
    tail = fn[idx:idx + 400]
    assert "except Exception" in tail
    assert "_delisted = False" in tail


def test_flag_is_returned_on_every_response():
    """Always present, so a consumer can rely on the key rather than inferring absence."""
    assert '"symbol_delisted": _delisted' in _SOURCE


def test_delisting_does_not_hard_block_the_backtest():
    """A flag, not a refusal — pre-delisting history is legitimate research data."""
    fn = _backtest_fn()
    idx = fn.index("_delisted = bool(")
    tail = fn[idx:idx + 600]
    assert "raise HTTPException" not in tail.split("if df.empty")[0]


def test_lookup_happens_before_the_engine_runs():
    fn = _backtest_fn()
    assert fn.index("_delisted") < fn.index("engine.run(")
