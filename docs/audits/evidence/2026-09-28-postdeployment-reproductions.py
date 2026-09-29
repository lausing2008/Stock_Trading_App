"""Offline follow-up at b5332a3; no network, saved-model loads, or production writes.

Execute with Python from any directory. Actual functions are extracted by name;
external dependencies are supplied explicitly. The settlement interleaving uses
real repository Stock/Price tables on SQLite and separate SQLAlchemy sessions.
It is a deterministic read-order reproduction, not a PostgreSQL load test.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pandas as pd
from sqlalchemy import create_engine, select, func, text, update
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[3]
INCOME = "services/market-data/src/services/options_income_engine.py"
TRAINER = "services/ml-prediction/src/training/trainer.py"


def load_function(path, name, env):
    node = copy.deepcopy(next(n for n in ast.parse((ROOT/path).read_text()).body
                             if isinstance(n, ast.FunctionDef) and n.name == name))
    node.decorator_list = []
    # Keep executable function bodies and defaults; defer annotation evaluation.
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(ROOT/path), "exec"), env)
    return env[name]


def label_endpoint():
    fn = load_function(TRAINER, "label_end_date", {"pd": pd, "timedelta": timedelta})
    start = date(2026,9,14)
    # A valid daily-bar sequence with three missing sessions (gaps or a suspension).
    # The target builder shifts by rows, so its tenth future bar remains exact.
    bars = pd.bdate_range(start, periods=20)
    bars = bars[~bars.isin(pd.to_datetime(["2026-09-16", "2026-09-17", "2026-09-18"]))]
    calculated, actual, cutoff = fn(start, 10), bars[10].date(), date(2026,9,30)
    assert calculated <= cutoff < actual
    return {"signal_date":str(start), "bars":10, "helper_available":str(calculated),
            "actual_target_bar":str(actual), "training_cutoff":str(cutoff),
            "future_target_would_be_admitted":True,
            "scope":"controlled sparse bar history, not an observed production label"}


def prices_and_settlement():
    spec = importlib.util.spec_from_file_location("audit_post_models", ROOT/"shared/db/models.py")
    models = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = models
    spec.loader.exec_module(models)
    expiry = date(2026,9,25)
    with tempfile.TemporaryDirectory(prefix="stockai-settlement-audit-") as tmp:
        engine = create_engine("sqlite:///"+str(Path(tmp)/"audit.db"))
        models.Stock.__table__.create(engine)
        models.Price.__table__.create(engine)
        stock, price = models.Stock.__table__, models.Price.__table__
        with engine.begin() as conn:
            conn.execute(stock.insert().values(id=1, symbol="TEST", market=models.Market.US,
                         exchange=models.Exchange.NASDAQ, name="Audit"))
            conn.execute(price.insert().values(id=1,stock_id=1,ts=datetime(2026,9,25),
                         timeframe=models.TimeFrame.D1,open=99.9,high=100,low=99,close=99.9,volume=100))
        env = {"__package__":"post_audit", "Stock":models.Stock,"Price":models.Price,
               "TimeFrame":models.TimeFrame,"select":select,"func":func,"text":text,
               "expected_settlement_session":lambda d:d,"settlement_session_is_final":lambda d:True,
               "_SETTLEMENT_CORROBORATION_TOL":.005,"log":MagicMock(),"_today_et":lambda:date(2026,9,28)}
        underlying = load_function(INCOME,"_underlying_price_as_of",env)
        with Session(engine) as session:
            historic = underlying(session,"TEST",expiry,{"TEST":120},105)
            missing = underlying(session,"ABSENT",expiry,{"ABSENT":120},105)
        assert historic == (99.9,"close") and missing == (105.,"entry_fallback")
        load_function(INCOME,"_corroborate_settlement_close",env)
        settle = load_function(INCOME,"_settlement_close",env)
        refresh_count = 0
        def later_reader():
            nonlocal refresh_count
            # This runs AFTER _settlement_close has fetched/stored 99.90, BEFORE
            # its corroborator opens a separate session. Emulates one successful
            # atomic ingest which refreshes Friday AND inserts Monday.
            with engine.begin() as conn:
                conn.execute(update(price).where(price.c.id==1).values(close=100.1, high=100.1))
                conn.execute(price.insert().values(id=2,stock_id=1,ts=datetime(2026,9,28),
                             timeframe=models.TimeFrame.D1,open=101,high=101,low=101,close=101,volume=100))
            refresh_count += 1
            return Session(engine)
        env["SessionLocal"] = later_reader
        provider = ModuleType("post_audit.paper_trading_engine")
        provider._fetch_live_prices = MagicMock(return_value={"TEST":100.1})
        with patch.dict(sys.modules,{"post_audit":ModuleType("post_audit"),
                                     "post_audit.paper_trading_engine":provider}):
            with Session(engine) as session:
                returned = settle(session,1,expiry)
        with Session(engine) as session:
            refreshed = session.execute(select(models.Price.close).where(models.Price.id==1)).scalar_one()
        assert refresh_count == 1 and refreshed == 100.1 and returned == (99.9,expiry)
        assert provider._fetch_live_prices.call_count == 0
        economics = load_function(INCOME,"settle_position_economics",env)
        args = dict(strategy="CASH_SECURED_PUT",strike=100,premium_collected=100,contracts=1,underlying_entry_price=105)
        return {"historical_lookup_fixed":historic,"missing_historical_never_uses_live":missing,
                "atomic_ingest_refreshed_expiry_close":refreshed,"accepted_settlement_close":returned[0],
                "live_quote_calls":provider._fetch_live_prices.call_count,
                "accepted_price_economics":economics(**args,close_price=returned[0]),
                "refreshed_price_economics":economics(**args,close_price=refreshed)}


if __name__ == "__main__":
    print(json.dumps({"label_endpoint":label_endpoint(), "prices_and_settlement":prices_and_settlement()},indent=2))
