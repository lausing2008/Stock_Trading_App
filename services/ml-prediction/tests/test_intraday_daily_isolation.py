"""Intraday Stage C daily isolation, ml-prediction side (design §10 coverage matrix).

trainer.py needs lightgbm/xgboost, which this local suite lacks, so: the map's keys are read
from its source, meta_trainer is loaded with a stub trainer exactly as test_meta_trainer.py
does, and the route behaviour (422 for a non-daily style) is tested in the production image by
test_f04_publication_nightly_path_container.py::test_routes_refuse_intraday_and_unknown_styles.
"""
import ast
import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock

from common.horizons import DAILY_HORIZONS

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_horizon_by_style_is_pinned_to_the_daily_horizons():
    """key set (from source): adding INTRADAY here would make nightly train-all train it on
    daily bars, and meta_trainer would import it."""
    tree = ast.parse((SRC / "training" / "trainer.py").read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign)
                and getattr(n.target, "id", None) == "_HORIZON_BY_STYLE")
    assert {k.value for k in node.value.keys} == set(DAILY_HORIZONS)


def test_no_route_defaults_a_horizon_for_an_unknown_style_code_presence():
    """code presence: every style->horizon lookup in the routes goes through the strict helper."""
    src = (SRC / "api" / "routes.py").read_text()
    assert "_HORIZON_BY_STYLE.get(" not in src
    assert src.count("_daily_horizon_days(") >= 9          # definition + 8 call sites


def _load_meta_trainer():
    fake_trainer = MagicMock()
    fake_trainer._HORIZON_BY_STYLE = {"SHORT": 5, "SWING": 10, "LONG": 20, "GROWTH": 10}
    sys.modules.setdefault("src.training", MagicMock())
    sys.modules["src.training.trainer"] = fake_trainer
    spec = importlib.util.spec_from_file_location("meta_trainer_isolation", SRC / "training" / "meta_trainer.py")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "src.training"
    spec.loader.exec_module(mod)
    return mod


def test_meta_trainer_reads_daily_outcomes_only():
    """captured SQL + bound parameters: the raw SQL meta-model read restricts horizon to the
    daily set, so an INTRADAY outcome could neither train it nor crowd daily rows out of the
    20,000-row limit. Code presence on PostgreSQL (the enum cannot hold INTRADAY)."""
    from unittest.mock import patch
    mt = _load_meta_trainer()
    captured = []
    db = MagicMock()
    db.execute.return_value.fetchall.return_value = []
    # train_meta_model does `from sqlalchemy import text` inside the function; this suite's
    # conftest stubs sqlalchemy, so the SQL string is captured where it is handed over.
    with patch.object(sys.modules["sqlalchemy"], "text", side_effect=lambda sql: captured.append(sql) or sql):
        assert mt.train_meta_model(db=db)["trained"] is False
    sql = next(s for s in captured if "FROM signal_outcomes" in s)
    assert "CAST(so.horizon AS text) = ANY(:daily_horizons)" in sql
    call = next(c for c in db.execute.call_args_list if c.args and c.args[0] == sql)
    assert call.args[1] == {"daily_horizons": list(DAILY_HORIZONS)}
