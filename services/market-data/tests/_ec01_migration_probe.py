"""Child process for test_ec01_one_shot_migration.py — runs the REAL migration machinery
against a REAL database.

WHY A SUBPROCESS. market-data's conftest stubs `sqlalchemy` outright, so inside the suite no
statement can reach a database. EC-01 is entirely about what happens to ROWS when the same
startup path runs twice, which is not a question a mock can answer.

This loads the actual `shared/db/session.py` with its engine pointed at an on-disk SQLite
database, and calls the actual `_apply_one_shot_migrations()`. The ledger DDL, the
INSERT ... ON CONFLICT DO NOTHING claim and the UPDATE are all the production statements.

Usage: python3 _ec01_migration_probe.py <scenario>
"""
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

_HERE = pathlib.Path(__file__).resolve()
_ROOT = _HERE.parents[3]

from sqlalchemy import create_engine, text          # the REAL sqlalchemy
from sqlalchemy.orm import sessionmaker

_DB = pathlib.Path(tempfile.mkdtemp()) / "ec01.db"
_URL = f"sqlite:///{_DB}"

# session.py resolves get_settings() and builds its engine at import time. Stub the config so
# the engine lands on SQLite, and stub .models so importing it does not drag in the full model
# graph — this probe declares only the one table the migration touches.
_cfg = types.ModuleType("common.config")
_cfg.get_settings = lambda: types.SimpleNamespace(database_url=_URL, admin_password=None)
sys.modules.setdefault("common", types.ModuleType("common"))
sys.modules["common.config"] = _cfg

_models = types.ModuleType("db_models_stub")
_models.Base = MagicMock()
sys.modules["shared.db.models"] = _models


def _load_session_module():
    path = _ROOT / "shared" / "db" / "session.py"
    spec = importlib.util.spec_from_file_location("ec01_session", path)
    mod = importlib.util.module_from_spec(spec)
    # session.py does `from .models import Base`; with no package context that relative import
    # fails, so give the loaded module a package whose `.models` is the stub above.
    pkg = types.ModuleType("ec01_pkg")
    pkg.__path__ = []
    pkg.models = _models
    sys.modules["ec01_pkg"] = pkg
    sys.modules["ec01_pkg.models"] = _models
    mod.__package__ = "ec01_pkg"
    spec.loader.exec_module(mod)
    return mod


_SCHEMA = """
CREATE TABLE price_alerts (
    id INTEGER PRIMARY KEY,
    symbol TEXT,
    triggered BOOLEAN,
    triggered_at TIMESTAMP,
    last_sent_at TIMESTAMP
)
"""

# Before the watermark (2026-09-28) — a genuinely ambiguous legacy row.
LEGACY_AT = "2026-09-21 13:31:02"
# After it — a real failed send, produced by the retry mechanism doing its job.
NEW_FAIL_AT = "2026-09-29 12:00:00"


def _rows(engine):
    with engine.begin() as conn:
        return [
            {"id": r[0], "symbol": r[1], "triggered_at": str(r[2]), "last_sent_at": None if r[3] is None else str(r[3])}
            for r in conn.execute(text(
                "SELECT id, symbol, triggered_at, last_sent_at FROM price_alerts ORDER BY id"
            )).all()
        ]


def _ledger(engine):
    with engine.begin() as conn:
        try:
            return [r[0] for r in conn.execute(text("SELECT name FROM applied_migrations ORDER BY name")).all()]
        except Exception:
            return None


def main():
    scenario = sys.argv[1]
    sess = _load_session_module()
    engine = sess.engine

    with engine.begin() as conn:
        conn.execute(text(_SCHEMA))
        conn.execute(text(
            "INSERT INTO price_alerts (id, symbol, triggered, triggered_at, last_sent_at) "
            "VALUES (1, 'LEGACY', 1, :t, NULL)"), {"t": LEGACY_AT})

    out = {}
    # First startup: the legacy row is closed out.
    sess._apply_one_shot_migrations()
    out["after_first_init"] = _rows(engine)
    out["ledger_after_first"] = _ledger(engine)

    # Now a NEW failed send appears — exactly what the retry mechanism produces.
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO price_alerts (id, symbol, triggered, triggered_at, last_sent_at) "
            "VALUES (2, 'PENDING', 1, :t, NULL)"), {"t": NEW_FAIL_AT})

    if scenario == "restart":
        # A service restarts. This is the path that used to consume the pending row.
        sess._apply_one_shot_migrations()
        out["after_restart"] = _rows(engine)
        out["ledger_after_restart"] = _ledger(engine)
    elif scenario == "many_restarts":
        for _ in range(5):
            sess._apply_one_shot_migrations()
        out["after_restart"] = _rows(engine)
        out["ledger_after_restart"] = _ledger(engine)
    elif scenario == "late_legacy":
        # Isolates the LEDGER from the watermark. A second pre-watermark row appears after the
        # migration has already run; only the ledger can stop the statement touching it, because
        # the watermark clause would happily match it. Constructed to separate the two guards,
        # not offered as a realistic data pattern — a pre-watermark row does not normally appear
        # after the fact. Without this, removing the ledger entirely passes every other test,
        # because the watermark alone covers the realistic case.
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO price_alerts (id, symbol, triggered, triggered_at, last_sent_at) "
                "VALUES (3, 'LATE_LEGACY', 1, :t, NULL)"), {"t": LEGACY_AT})
        sess._apply_one_shot_migrations()
        out["after_restart"] = _rows(engine)
        out["ledger_after_restart"] = _ledger(engine)
    elif scenario == "ledger_lost":
        # The watermark is the SECOND, independent guard. Drop the ledger entirely and confirm
        # the pending row still survives — a defence-in-depth check, not the primary mechanism.
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE applied_migrations"))
        sess._apply_one_shot_migrations()
        out["after_restart"] = _rows(engine)
        out["ledger_after_restart"] = _ledger(engine)

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
