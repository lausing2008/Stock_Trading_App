"""Offline, controlled probes of the pending remediation at 0e72d3a.

Run from any directory: python <this-file> > <evidence.json>.
Extracts actual functions/AST blocks; stubs external inputs explicitly. Does not
load saved models, contact services, place orders, or change application files.
SQLite probes establish SQL/SQLAlchemy behavior, not PostgreSQL concurrency.
Assertions confirm the reviewed defect, so these are audit reproductions rather
than regression tests to retain after a fix.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace as NS, ModuleType
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[3]
INCOME = "services/market-data/src/services/options_income_engine.py"
TRAINER = "services/ml-prediction/src/training/trainer.py"
META = "services/ml-prediction/src/training/meta_trainer.py"
AUTH = "services/market-data/src/api/auth.py"
JWT = "shared/common/jwt_auth.py"


def tree(path):
    return ast.parse((ROOT / path).read_text())


def function(path, name, env):
    node = copy.deepcopy(next(n for n in tree(path).body
                              if isinstance(n, ast.FunctionDef) and n.name == name))
    node.decorator_list = []
    node.returns = None
    for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
        arg.annotation = None
    node.args.defaults = [ast.Constant(None) for _ in node.args.defaults]
    node.args.kw_defaults = [ast.Constant(None) if n is not None else None
                             for n in node.args.kw_defaults]
    module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
    exec(compile(module, str(ROOT / path), "exec"), env)
    return env[name]


def block(path, start, end, env):
    # Select whole AST statements, by their real source boundaries.
    nodes = [copy.deepcopy(n) for n in ast.walk(tree(path))
             if isinstance(n, ast.stmt) and start <= n.lineno and n.end_lineno <= end]
    top = [n for n in nodes if not any(m is not n and m.lineno <= n.lineno
           and m.end_lineno >= n.end_lineno and (m.lineno, m.end_lineno) !=
           (n.lineno, n.end_lineno) for m in nodes)]
    exec(compile(ast.fix_missing_locations(ast.Module(body=sorted(top, key=lambda n:n.lineno),
                 type_ignores=[])), str(ROOT / path), "exec"), env)


def probe_price_query():
    spec = importlib.util.spec_from_file_location("audit28_models", ROOT / "shared/db/models.py")
    models = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = models
    spec.loader.exec_module(models)
    engine = create_engine("sqlite://")
    models.Stock.__table__.create(engine)
    models.Price.__table__.create(engine)
    env = {"text": text, "_today_et": lambda: date(2026, 9, 28)}
    fn = function(INCOME, "_underlying_price_as_of", env)
    with engine.connect() as conn:
        try:
            fn(conn, "TEST", date(2026, 9, 25), {}, 100)
            raise AssertionError("Expected actual SQL to reject the nonexistent column")
        except Exception as exc:
            assert "no such column: symbol" in str(exc), str(exc)
            error = str(exc).splitlines()[0]
    absent = MagicMock()
    absent.execute.return_value.first.return_value = None
    historical = fn(absent, "TEST", date(2026, 9, 25), {"TEST": 120}, 100)
    assert historical == (120.0, "live")
    return {"sql_error": error, "actual_columns": list(models.Price.__table__.columns.keys()),
            "enum_labels": models.Price.timeframe.type.enums,
            "historical_no_row_fallback": historical}


def probe_resweep():
    env = {"Path": Path, "os": os, "_settings": NS(model_dir="/offline"), "log": MagicMock()}
    function(TRAINER, "_compute_oos_suppression", env)
    fn = function(TRAINER, "resweep_oos_suppression", env)
    bundle = {"oos_suppressed": True, "metrics": {"evaluation_valid": False,
              "embargo_shortfall": 10, "threshold_evaluation_mode": "in_sample",
              "cv_auc_mean": .65, "recall": .6, "precision": .7, "overfit_gap": .02}}
    joblib = ModuleType("joblib")
    joblib.load = lambda _: bundle
    with patch.dict(sys.modules, {"joblib": joblib}), patch("glob.glob", return_value=["/offline/TEST/model.joblib"]):
        result = fn(dry_run=True)
    assert result["newly_unsuppressed"][0]["now"] is False
    assert bundle["oos_suppressed"] is True  # dry run did not mutate input
    return result


def probe_settlement_finality():
    s = MagicMock()
    s.__enter__.return_value = s
    provider = ModuleType("audit28.paper_trading_engine")
    provider._fetch_live_prices = MagicMock(return_value={"TEST": 100.10})
    env = {"__package__": "audit28", "SessionLocal": lambda: s, "select": MagicMock(),
           "Stock": MagicMock(), "Price": MagicMock(), "TimeFrame": NS(D1="D1"),
           "func": MagicMock(), "_SETTLEMENT_CORROBORATION_TOL": .005}
    fn = function(INCOME, "_corroborate_settlement_close", env)
    econ = function(INCOME, "settle_position_economics", env)
    with patch.dict(sys.modules, {"audit28": ModuleType("audit28"),
                                 "audit28.paper_trading_engine": provider}):
        s.execute.return_value.scalar_one_or_none.return_value = "TEST"
        s.execute.return_value.scalar.return_value = datetime(2026, 9, 28)
        later = fn(1, date(2026, 9, 25), 99.90)
        assert later == (True, "superseded_by_later_session")
        assert provider._fetch_live_prices.call_count == 0
        s.execute.return_value.scalar.return_value = datetime(2026, 9, 25)
        nearby = fn(1, date(2026, 9, 25), 99.90)
        assert nearby == (True, "corroborated_by_live_quote")
    args = dict(strategy="CASH_SECURED_PUT", strike=100, premium_collected=100,
                contracts=1, underlying_entry_price=105)
    stale = econ(**args, close_price=99.90)
    final = econ(**args, close_price=100.10)
    assert stale["assigned"] and not final["assigned"]
    return {"later_bar": later, "within_tolerance": nearby,
            "stored_close_economics": stale, "hypothetical_final_close_economics": final}


def probe_auth():
    redis = MagicMock()
    redis.get.return_value = "200"
    env = {"get_redis": lambda: redis, "_USER_REVOKE_PREFIX": "audit:", "_SERVICE_CLAIM": "svc"}
    revoked = function(JWT, "_user_revoked", env)
    payload = {"sub": "audit_admin", "jti": "offline", "iat": 100}
    assert revoked(payload) is True
    assert revoked({**payload, "iat": 200}) is False
    account = NS(username="audit_admin", is_active=True, role="ADMIN")
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = account
    env.update(jwt=NS(decode=lambda *a, **k: payload), _settings=NS(jwt_secret="offline"),
               ALGORITHM="HS256", JWTError=ValueError, _is_blacklisted=lambda _: False,
               select=MagicMock(), User=MagicMock(), HTTPException=RuntimeError)
    fn = function(AUTH, "get_current_user", env)
    accepted = fn(NS(credentials="controlled-decoded-token"), session)
    assert accepted is account
    return {"shared_validator_revoked": True, "local_auth_still_accepts": True,
            "same_second_token_revoked": False, "scope": "controlled decoded JWT; no HTTP request"}


def probe_migration_transaction():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE intents (id INTEGER)"))
        conn.execute(text("INSERT INTO intents VALUES (1), (1)"))
    with engine.begin() as conn:
        try:
            conn.execute(text("CREATE UNIQUE INDEX intent_unique ON intents(id)"))
        except Exception:
            conn.rollback()  # Same transaction operation as _seed_admin's handler.
        try:
            conn.execute(text("SELECT 1"))
            raise AssertionError("Expected closed transaction context")
        except Exception as exc:
            assert type(exc).__name__ == "InvalidRequestError", str(exc)
            message = str(exc)
    env = {"_settings": NS(admin_password=""), "engine": MagicMock()}
    seed = function("shared/db/session.py", "_seed_admin", env)
    seed()
    assert env["engine"].begin.call_count == 0
    return {"after_rollback_error": message, "no_admin_password_enters_migration": False}


def probe_label_dates():
    signal = date(2026, 9, 14)
    env = {"_HORIZON_BY_STYLE": {"SWING": 10}, "style": "SWING", "_td": timedelta,
           "outcomes": [NS(signal_date=signal, exit_date=date(2026, 9, 24),
                           ts_evaluated=datetime(2026, 9, 24, 20))]}
    block(TRAINER, 490, 499, env)
    df = pd.DataFrame({"ts": pd.bdate_range(signal, periods=15), "close": np.arange(100, 115)})
    actual_label_end = df.ts.iloc[10].date()  # forward close.shift(-10) target's endpoint
    cutoff = date(2026, 9, 25)
    env.update(_avail=np.array([env["avail_map"][signal]], dtype=object), _cutoff=cutoff, np=np)
    block(TRAINER, 977, 977, env)
    assert env["_admissible"][0] and actual_label_end > cutoff
    dedup = {"pd": pd, "df": df, "X": pd.DataFrame({"x": range(10)}, index=range(3,13)),
             "y_dir": pd.Series(range(10), index=range(3,13)),
             "y_ret": pd.Series(range(10), index=range(3,13)),
             "_keep": np.array([False] + [True] * 9)}
    expected_first_date = df.ts.iloc[4].date()
    block(TRAINER, 735, 737, dedup)
    block(TRAINER, 852, 853, dedup)
    reported_first_date = dedup["X_dates_for_split"][0].date()
    assert reported_first_date != expected_first_date
    dates = list(pd.bdate_range("2026-01-01", periods=100).date)
    meta_env = {"_val_idx": np.arange(80,100), "record_dates": dates}
    block(META, 420, 425, meta_env)
    final_start = dates[meta_env["_final_idx"][0]]
    es_latest_label = dates[meta_env["_es_idx"][-1]] + timedelta(days=10)
    assert es_latest_label >= final_start
    return {"R01_computed_available": str(env["avail_map"][signal]),
            "R01_actual_target_bar_date": str(actual_label_end), "training_cutoff": str(cutoff),
            "dedup_first_row_actual_date": str(expected_first_date),
            "dedup_first_row_reported_date": str(reported_first_date),
            "future_target_admitted": True, "meta_final_start": str(final_start),
            "meta_latest_early_stop_label_available": str(es_latest_label),
            "meta_split_keeps_overlapping_label": True}


def probe_settlement_lost_update():
    class Field:
        def __le__(self, other): return True
    portfolio = NS(id=1, current_cash=1000)
    position = NS(id=1, stock_id=1, symbol="TEST", strategy="CASH_SECURED_PUT",
                  strike=5, total_premium_collected=10, contracts=1, underlying_entry_price=6,
                  collateral_reserved=500, expiry=date(2026,9,25), stage="open")
    committed = {"cash": 1000}
    def quote(*args):
        committed["cash"] -= 900  # Controlled interleaving: another transaction's entry debit.
        return 6, position.expiry
    session = MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value = [position]
    session.commit.side_effect = lambda: committed.update(cash=portfolio.current_cash)
    env = {"select": MagicMock(), "OptionsIncomePosition": NS(portfolio_id=1, stage="open", expiry=Field()),
           "_settlement_close": quote, "log": MagicMock()}
    function(INCOME, "settle_position_economics", env)
    settle = function(INCOME, "settle_expired_positions", env)
    n = settle(session, portfolio, date(2026,9,28))
    assert n == 1 and committed["cash"] == 1500
    return {"cash_written": committed["cash"], "cash_including_other_committed_debit": 600,
            "scope": "actual settlement function with controlled interleaving; not a PostgreSQL race test"}


if __name__ == "__main__":
    results = {name: fn() for name, fn in [
        ("price_query", probe_price_query), ("suppression_resweep", probe_resweep),
        ("settlement_finality", probe_settlement_finality), ("auth", probe_auth),
        ("migration_transaction", probe_migration_transaction), ("label_dates", probe_label_dates),
        ("settlement_lost_update", probe_settlement_lost_update)]}
    print(json.dumps(results, indent=2))
