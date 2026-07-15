"""Regression test for T247-STRATEGYENGINE-STALECOMMENT.

routes.py's delete_strategy() previously had a stale comment claiming the Strategy.backtests
SQLAlchemy relationship lacked cascade="delete-orphan", used to justify a manual
`session.query(Backtest).filter(...).delete()` before `session.delete(s)`. The model actually
already declares the cascade — the manual delete was redundant, and the stale comment
misdescribed why, which could mislead a future edit into removing the real ORM cascade.

This test guards the actual invariant the (now-removed) manual delete existed to protect:
Strategy.backtests must have a real delete-orphan cascade, so `session.delete(strategy)` alone
is sufficient and never hits the FK NOT NULL constraint the original comment warned about.

Loads shared/db/models.py directly via importlib — db/__init__.py triggers a real psycopg2
engine connection attempt (session.py's create_engine()), which isn't available/needed here;
models.py itself has no such side effect.
"""
import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
_cfg = sys.modules["common.config"]
_cfg.get_settings = MagicMock(return_value=MagicMock())

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
# Must register in sys.modules BEFORE exec_module — SQLAlchemy's declarative mapper resolves
# `Mapped[...]` string annotations against sys.modules[module.__name__] while the class bodies
# are still executing, not after.
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)


def test_strategy_backtests_relationship_has_delete_orphan_cascade():
    """The exact invariant the removed manual pre-delete existed to work around: Strategy's
    ORM relationship to Backtest must cascade deletes on its own."""
    cascade = _models.Strategy.backtests.property.cascade
    assert "delete" in cascade
    assert "delete-orphan" in cascade
