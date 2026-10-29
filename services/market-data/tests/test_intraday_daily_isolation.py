"""Intraday Stage C daily isolation, market-data side (design §10 coverage matrix).

behaviour: the watchlist style is validated at the write.
code presence (labelled): the paper-exit RSI read, the data-quality freshness checks and the
watchlist curation loop. scheduler.py/paper_trading_engine.py run under this suite's stubbed
SQLAlchemy, so their SQL is read from source; on PostgreSQL these predicates are defence in
depth behind the enum, which cannot hold INTRADAY.
"""
import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

import sys
from unittest.mock import MagicMock

from common.horizons import DAILY_HORIZONS

sys.modules.setdefault("sqlalchemy.exc", MagicMock())     # conftest stubs sqlalchemy as a non-package
from src.api.watchlist import CreateWatchlistRequest, RenameWatchlistRequest  # noqa: E402

SRC = Path(__file__).resolve().parents[1] / "src" / "services"
DAILY_IN = "CAST(sig.horizon AS text) IN ('SHORT', 'SWING', 'LONG', 'GROWTH')"


@pytest.mark.parametrize("model", [CreateWatchlistRequest, RenameWatchlistRequest])
def test_watchlist_style_must_be_a_daily_horizon(model):
    for bad in ("INTRADAY", "intraday", "SCALP"):
        with pytest.raises(ValidationError):
            model(name="w", trading_style=bad)
    assert model(name="w", trading_style="swing").trading_style == "SWING"
    assert model(name="w", trading_style=None).trading_style is None
    assert model(name="w", trading_style="").trading_style == ""


def _fn(path, name):
    return next(n for n in ast.walk(ast.parse(path.read_text()))
                if isinstance(n, ast.FunctionDef) and n.name == name)


def test_paper_exit_rsi_read_is_daily_only_code_presence():
    fn = _fn(SRC / "paper_trading_engine.py", "_monitor_positions")
    rsi = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "sa_text"
           and "reasons->>'rsi'" in ast.unparse(n)]
    assert len(rsi) == 1 and "CAST(sig.horizon AS text) = ANY(:daily_horizons)" in ast.unparse(rsi[0])
    binds = [v for n in ast.walk(fn) if isinstance(n, ast.Dict)
             for k, v in zip(n.keys, n.values) if getattr(k, "value", None) == "daily_horizons"]
    assert [ast.unparse(b) for b in binds] == ["list(_DAILY_HORIZONS)"]


def test_signal_freshness_checks_ignore_non_daily_rows():
    """The DQ freshness check builds its SQL at import; read the evaluated strings."""
    tree = ast.parse((SRC / "scheduler.py").read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == "_DQ_CHECKS")
    sig = {}
    for d in (n for n in node.value.elts if isinstance(n, ast.Dict)):
        fields = {k.value: v for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant)}
        name = getattr(fields.get("name"), "value", None)
        if name in ("signals_us", "signals_hk"):
            sig[name] = eval(compile(ast.Expression(fields["query"]), "dq", "eval"),  # noqa: S307 - string expr
                             {"_DAILY_HORIZONS": DAILY_HORIZONS})
    assert len(sig) == 2 and all(DAILY_IN in q for q in sig.values())


def test_watchlist_curation_skips_a_non_daily_style_code_presence():
    fn = _fn(SRC / "scheduler.py", "_run_watchlist_auto_rotation")
    loop = next(n for n in ast.walk(fn) if isinstance(n, ast.For) and "watchlists" in ast.unparse(n.iter))
    first = loop.body[0]
    assert isinstance(first, ast.If) and "_is_daily_horizon(style)" in ast.unparse(first.test)
    assert any(isinstance(s, ast.Continue) for s in first.body)


def test_intraday_router_registers_before_the_stocks_catch_all_code_presence():
    """Review follow-up: /stocks/intraday/* shares the gateway's `stocks` prefix with data_router's
    `/stocks/{symbol}...` routes; FastAPI matches in registration order, so it must come first.
    (Live gateway auth and routing are verified at deploy, not here.)"""
    main = (Path(__file__).resolve().parents[1] / "src" / "main.py").read_text()
    call = next(n for n in ast.walk(ast.parse(main)) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "create_app")
    routers = [e.id for kw in call.keywords if kw.arg == "routers" for e in kw.value.elts]
    assert routers.index("intraday_router") < routers.index("data_router")
    api = (Path(__file__).resolve().parents[1] / "src" / "api" / "intraday.py").read_text()
    assert 'prefix="/stocks/intraday"' in api and "Depends(get_admin_user)" in api
