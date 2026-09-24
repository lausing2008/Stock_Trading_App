"""Offline diagnostic reproductions for the September 23 audit.

Run from any directory with the project's numpy/pandas/scipy dependencies:
    python docs/audits/evidence/2026-09-23-deep-audit-reproductions.py

Executes selected current source with controlled inputs; no app startup, network,
database, model training, order placement, or file writes. Assertions describe
the audited defects, NOT the desired regression-test behavior after fixes.
"""
from __future__ import annotations

import ast
import copy
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
REPORT = {}


def tree(path):
    return ast.parse((ROOT / path).read_text())


def run_nodes(nodes, namespace):
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *copy.deepcopy(nodes)], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, "<audited-source>", "exec"), namespace)
    return namespace


def load(path, names, namespace):
    nodes = [n for n in tree(path).body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert len(nodes) == len(names), (path, names)
    return run_nodes(nodes, namespace)


def backtest_terminal_cost():
    ns = load("services/strategy-engine/src/backtest/engine.py", {"BacktestResult", "BacktestEngine"}, {
        "__name__": __name__, "np": np, "pd": pd, "dataclass": dataclass, "field": field,
        # Isolate execution/accounting from unrelated indicator calculations.
        "compute_features": lambda df: df.copy(),
        "evaluate_rule": lambda rule, df: df[rule["column"]],
    })
    frame = pd.DataFrame({"ts": pd.date_range("2026-01-01", periods=4), "close": [100.0]*4,
                          "enter": [True]*4, "exit": [False]*4})
    engine = ns["BacktestEngine"](fee_bps=5, slippage_bps=2)
    result = engine.run(frame, {"column":"enter"}, {"column":"exit"})
    reported = result.equity_curve[-1]["equity"] - 1
    ledger = result.trades[0]["ret"]
    assert reported > ledger + 0.0006
    frame["enter"] = [False, False, True, False]
    last = engine.run(frame, {"column":"enter"}, {"column":"exit"})
    assert last.total_return == 0 and last.trades[0]["ret"] < 0
    REPORT["terminal_cost"] = {"curve_return":reported, "trade_return":ledger,
                                "last_bar_curve_return":last.total_return,
                                "last_bar_trade_return":last.trades[0]["ret"]}


def training_boundary():
    fn = next(n for n in tree("services/ml-prediction/src/training/trainer.py").body
              if isinstance(n, ast.FunctionDef) and n.name == "train_model")
    names = {"split_es", "split_cal", "_embargo", "_embargo_es", "_embargo_cal", "X_train", "X_es", "X_cal", "X_test"}
    nodes = [n for n in fn.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets)]
    ns = run_nodes(nodes, {"X":pd.DataFrame({"v":range(200)}), "horizon":20, "split_train":140})
    assert ns["_embargo_cal"] == 0
    last_cal_label_end = int(ns["X_cal"].index[-1]) + 20
    first_test_feature = int(ns["X_test"].index[0])
    assert last_cal_label_end > first_test_feature
    REPORT["training_boundary"] = {"calibration_last_label_end_bar":last_cal_label_end,
                                    "test_first_feature_bar":first_test_feature, "gap":ns["_embargo_cal"]}


def meta_record_order():
    # Execute the actual grouping and per-symbol sorting statements, instrumenting
    # record append with dates rather than running expensive feature/model code.
    fn = next(n for n in tree("services/ml-prediction/src/training/meta_trainer.py").body
              if isinstance(n, ast.FunctionDef) and n.name == "train_meta_model")
    grouping = next(n for n in fn.body if isinstance(n, ast.For) and ast.unparse(n.target) == "row"
                    and ast.unparse(n.iter) == "rows")
    symbol_loop = next(n for n in fn.body if isinstance(n, ast.For) and ast.unparse(n.iter) == "symbol_rows.items()")
    probe = copy.deepcopy(symbol_loop)
    probe.body = [copy.deepcopy(symbol_loop.body[0]), *ast.parse("ordered.extend((symbol, r.signal_date) for r in sym_rows_sorted)").body]
    from collections import defaultdict
    rows = [SimpleNamespace(symbol=s, signal_date=date(2026,9,d)) for d in [4,3,2,1] for s in "ABCDE"]
    ns = run_nodes([grouping, probe], {"rows":rows,"symbol_rows":defaultdict(list),"ordered":[],"sorted":sorted})
    ordered = ns["ordered"]; split = int(len(ordered)*0.8)
    train_dates = [d for _,d in ordered[:split]]; validation_dates = [d for _,d in ordered[split:]]
    assert max(train_dates) > min(validation_dates)
    REPORT["meta_record_order"] = {"train_latest":str(max(train_dates)),"validation_earliest":str(min(validation_dates)),
                                    "validation_symbols":sorted({s for s,_ in ordered[split:]})}


def stale_option_mark():
    ns = load("services/market-data/src/services/options_income_engine.py", {"short_option_liability"}, {})
    mark, source = ns["short_option_liability"](strategy="CASH_SECURED_PUT",strike=100,
                          underlying_price=80,contracts=1,quote_ask=2)
    fallback, _ = ns["short_option_liability"](strategy="CASH_SECURED_PUT",strike=100,
                          underlying_price=80,contracts=1,quote_ask=None)
    assert mark == 200 and fallback == 2000
    REPORT["stale_option_mark"] = {"stale_ask_liability":mark,"current_intrinsic":fallback,"source":source}


def lock_ownership():
    class RedisFake:
        value = None
        def set(self, key, value, **kwargs):
            self.value = value
            return True
        def delete(self, key):
            self.value = None
    redis = RedisFake()
    def work():
        # A's lease expires while work runs; B then successfully acquires it.
        redis.value = "worker-B-new-lease"
        return {"ok":True}
    ns = load("services/market-data/src/services/options_income_engine.py", {"run_options_income_step"}, {
        "_get_income_redis":lambda:redis, "_INCOME_STEP_LOCK_KEY":"test", "_INCOME_STEP_LOCK_TTL":1800,
        "_run_options_income_step_locked":work, "log":MagicMock(),
    })
    ns["run_options_income_step"]()
    assert redis.value is None
    REPORT["lock_ownership"] = {"worker_B_lease_deleted_by_A":True}


def premature_settlement():
    orm = MagicMock()
    query = MagicMock()
    session = MagicMock()
    session.execute.return_value.first.return_value = SimpleNamespace(close=101)
    ns = load("services/market-data/src/services/options_income_engine.py", {"_settlement_close", "settle_position_economics"}, {
        "expected_settlement_session":lambda expiry:expiry, "select":lambda *args:query,
        "Price":orm,"TimeFrame":orm,"func":orm,
    })
    expiry = date(2026,9,25)
    observed, _ = ns["_settlement_close"](session, 1, expiry)
    # Caller is the documented manual run-step path on expiry morning. A mutable
    # same-date D1 close of 101 is accepted; no time/finality input is inspected.
    intraday = ns["settle_position_economics"](strategy="CASH_SECURED_PUT",strike=100,
                    premium_collected=100,contracts=1,underlying_entry_price=100,close_price=observed)
    final = ns["settle_position_economics"](strategy="CASH_SECURED_PUT",strike=100,
                    premium_collected=100,contracts=1,underlying_entry_price=100,close_price=90)
    assert intraday["pnl"] == 100 and final["pnl"] == -900
    REPORT["premature_settlement"] = {"accepted_partial_close":observed,"premature_pnl":intraday["pnl"],"final_close_pnl":final["pnl"]}


def allocation_metrics():
    ns = load("services/portfolio-optimizer/src/optimizers/methods.py", {"PortfolioWeights", "_metrics", "ai_allocation"}, {
        "__name__":__name__,"np":np,"pd":pd,"dataclass":dataclass,"field":field,"RISK_FREE":0.04,"log":MagicMock(),
        # Deterministic annual estimates; actual allocation and metric code runs.
        "_prepare":lambda returns:(np.array([0.10]), np.array([[0.04]])),
    })
    returns = pd.DataFrame({"A":[0.01,-0.01,0.02,-0.005]})
    out = ns["ai_allocation"](returns, {"A":80})
    assert out.weights == {"A":0.95} and out.cash == 0.05
    assert out.expected_vol == 0.2
    REPORT["allocation_metrics"] = {"weights":out.weights,"cash":out.cash,"reported_vol":out.expected_vol,
                                     "vol_of_returned_allocation":0.95*0.2,
                                     "reported_return":out.expected_return}


def csp_yield_denominator():
    ns = load("services/market-data/src/services/options_income_engine.py", {"_score_contract"}, {})
    out = ns["_score_contract"](strategy="CASH_SECURED_PUT",strike=90,premium_bid=1,current_price=100,dte=30)
    collateral_yield = out["premium_per_contract"] / out["collateral_required"] * (365 / 30) * 100
    assert out["annualized_yield_pct"] == 12.17 and round(collateral_yield,2) == 13.52
    REPORT["csp_yield"] = {"reported_annualized_pct":out["annualized_yield_pct"],
                            "annualized_premium_on_reserved_cash_pct":round(collateral_yield,2)}


def broker_poll_commit():
    t1 = SimpleNamespace(portfolio_id=1,symbol="A",broker_exit_order_id="one",exit_price=101,
                         entry_price=100,shares=10,entry_shares=10,realized_pnl=0,broker_exit_fill_confirmed=False)
    t2 = SimpleNamespace(portfolio_id=1,symbol="B",broker_exit_order_id="two",exit_price=101,
                         entry_price=100,shares=10,entry_shares=10,realized_pnl=0,broker_exit_fill_confirmed=False)
    port = SimpleNamespace(id=1,current_cash=1000)
    session = MagicMock()
    q1,q2 = MagicMock(),MagicMock()
    q1.scalars.return_value.all.return_value = [t1,t2]
    q2.scalars.return_value.all.return_value = [port]
    session.execute.side_effect = [q1,q2]
    committed=[]
    session.commit.side_effect = lambda:committed.append((t1.broker_exit_fill_confirmed,t2.broker_exit_fill_confirmed))
    broker = MagicMock()
    broker.get_order.side_effect = [SimpleNamespace(status="filled",filled_avg_price=102),
                                   SimpleNamespace(status="filled",filled_avg_price=101)]
    ns = load("services/market-data/src/services/paper_trading_engine.py", {"poll_broker_exit_fills"}, {
        "PaperTrade":MagicMock(),"PaperPortfolio":MagicMock(),"select":MagicMock(),"log":MagicMock(),
        "SessionLocal":lambda:session,
        "_get_portfolio_broker":lambda *args:broker,"_handle_broker_error_if_token_rejected":lambda *args:False,
    })
    ns["poll_broker_exit_fills"]()
    assert session.close.call_count == 1
    assert t2.broker_exit_fill_confirmed and committed[-1] == (True,False)
    REPORT["broker_poll_commit"] = {"committed_confirmations":committed[-1],"in_memory_confirmations":[True,True]}


def model_mutation_authorization():
    fn = next(n for n in tree("services/ml-prediction/src/api/routes.py").body
              if isinstance(n,ast.FunctionDef) and n.name == "train")
    fn.decorator_list=[]
    tasks = MagicMock()
    sentinel_train = lambda *args,**kwargs:None
    ns = run_nodes([fn], {"Depends":lambda f:None,"get_current_username":lambda:None,
                          "list_models":lambda:["xgboost"],"_HORIZON_BY_STYLE":{"SWING":10},
                          "train_model":sentinel_train})
    result=ns["train"](SimpleNamespace(model="xgboost",symbol="AAPL",style="SWING",horizon=5),tasks,"ordinary-user")
    assert result["status"] == "scheduled" and tasks.add_task.call_count == 1
    # No background task is executed; ordinary verified identity is sufficient at this handler.
    REPORT["model_mutation_authorization"] = {"non_admin_handler_result":result["status"],"scheduled_tasks":tasks.add_task.call_count}


def meta_inference_contract():
    import sys
    from types import ModuleType
    captured={}
    stub=ModuleType("audit_runtime.meta_trainer")
    def predict_meta(**kwargs):
        captured.update(kwargs)
        return None
    stub.predict_meta=predict_meta
    sys.modules[stub.__name__]=stub
    try:
        def prediction(symbol,model,horizon,style):
            prob={"xgboost":0.7,"lightgbm":0.6,"random_forest":0.5}[model]
            return {"bullish_probability":prob,"confidence":abs(prob-0.5)*200,
                    "metrics":{"auc":0.55,"buy_threshold":0.5},"oos_suppressed":False}
        ns=load("services/ml-prediction/src/training/trainer.py",{"predict_latest_ensemble_three"},{
            "__package__":"audit_runtime","predict_latest":prediction,
            "_artifact_path":lambda *args:SimpleNamespace(exists=lambda:True),
            "_load_sector_and_market_cap":lambda symbol:("Technology",1e10),"log":MagicMock(),
        })
        ns["predict_latest_ensemble_three"]("AAPL",10,"SWING")
        assert abs(captured["ta_score"]-0.605)<1e-9 and captured["fused_prob"]==0.7
        assert "direction" not in captured
        REPORT["meta_inference_contract"]={"ta_score_received":captured["ta_score"],
                "fused_prob_received":captured["fused_prob"],"direction_explicitly_supplied":False}
    finally:
        del sys.modules[stub.__name__]


def news_flag_clear():
    session=MagicMock()
    session.execute.return_value.scalars.return_value.all.return_value=[]
    session.execute.return_value.rowcount=1
    session_factory=MagicMock()
    session_factory.return_value.__enter__.return_value=session
    clear=MagicMock()
    ns=load("services/news-intelligence/src/services/storage.py",{"persist_news_items"},{
        "SessionLocal":session_factory,"select":MagicMock(),"RealtimeNewsItem":MagicMock(),
        "pg_insert":MagicMock(),"get_admin_ai_key":lambda provider:"offline-test-placeholder",
        "classify_in_batches":lambda *args:[{"sentiment_score":30,"sentiment_label":"negative",
                                            "is_material":False,"category":"other"}],
        "_current_hot_sentiment":lambda symbol:"negative","_clear_hot":clear,
        "_mark_hot":MagicMock(),"log":MagicMock(),
    })
    ns["persist_news_items"]([{"url":"https://example.invalid/test","symbols":["AAPL"],
         "headline":"Unrelated minor negative story", "published_at":"2026-09-22T10:00:00Z"}],"test",symbol_mode="tagged")
    assert clear.call_count==1
    REPORT["news_flag_clear"]={"nonmaterial_negative_story_clears_existing_material_negative_flag":True}


if __name__ == "__main__":
    for test in [backtest_terminal_cost, training_boundary, meta_record_order, stale_option_mark,
                 lock_ownership, premature_settlement, allocation_metrics, csp_yield_denominator,
                 broker_poll_commit, model_mutation_authorization, meta_inference_contract, news_flag_clear]:
        test()
    print(json.dumps(REPORT, indent=2))
