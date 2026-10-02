"""Read-only local defect witnesses; NOT release acceptance tests or production evidence.

Executes real pure options code and AST-extracted broker functions against an isolated
SQLite database. SQLite establishes the sequential stale-selection defect, not PostgreSQL
concurrency guarantees. Run from any directory with SQLAlchemy installed.
"""
import ast
import json
import runpy
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import Column, DateTime, Integer, String, create_engine, select, update
from sqlalchemy.orm import declarative_base, Session

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "services/market-data/src"


def extract(path, names, namespace):
    tree = ast.parse(path.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)


def main():
    mod = runpy.run_path(str(SRC / "services/options_strategies.py"))
    row = dict(strike=105.0, bid=2.0, ask=2.2, last_price=2.1, iv=.4, oi=100)
    args = dict(current_price=100., stop_loss=95., take_profit=105., signal="BUY",
                put_rows=[], put_expiry=None, call_rows=[row], call_expiry="2026-11-06",
                shares=1., iv_rank=80., today=date(2026, 10, 2))
    one_share = mod["build_strategy_matrix"](**args)
    assert one_share["recommendation"]["primary"] == "covered_call"
    last_only = mod["build_strategy_matrix"](**{**args, "call_rows": [{**row, "bid": 0., "ask": 0.}]})
    leg = last_only["singles"]["covered_call"]["legs"][0]
    assert leg["price_source"] == "last_trade"
    assert last_only["recommendation"]["primary"] == "covered_call"
    expired = mod["build_strategy_matrix"](**{**args, "call_expiry": "2026-10-01"})
    assert expired["singles"]["covered_call"]["legs"][0]["days_to_expiry"] == -1

    # The route's additive matrix import is intentionally unavailable in this extraction;
    # its caught import failure has no effect on the original leg whose pricing is tested.
    ns = dict(date=date, datetime=datetime, log=SimpleNamespace(warning=lambda *a, **k: None),
              __name__="audit_route_probe", _OPTIONS_GAME_PLAN_MIN_PUT_DTE=25,
              _OPTIONS_GAME_PLAN_MAX_PUT_DTE=60, _OPTIONS_GAME_PLAN_MIN_CALL_DTE=14,
              _OPTIONS_GAME_PLAN_MAX_CALL_DTE=45)
    extract(SRC / "api/routes.py", {"compute_options_game_plan", "_nearest_expiry_in_dte_window", "_nearest_strike"}, ns)
    crossed = {**row, "bid": 12., "ask": 2.}
    legacy = ns["compute_options_game_plan"](**{k: v for k, v in args.items() if k not in {"put_expiry", "call_expiry"}},
        put_expiries=[], call_expiries=[args["call_expiry"]])
    legacy_crossed = ns["compute_options_game_plan"](**{**{k: v for k, v in args.items() if k not in {"put_expiry", "call_expiry"}}, "call_rows": [crossed]},
        put_expiries=[], call_expiries=[args["call_expiry"]])
    assert legacy_crossed["covered_call"]["mid_price"] == 7.
    assert mod["_mid"](crossed) is None

    Base = declarative_base()

    class Trade(Base):
        __tablename__ = "paper_trades"
        id = Column(Integer, primary_key=True)
        portfolio_id = Column(Integer)
        stage = Column(String)
        broker_submission_state = Column(String)
        broker_submission_path = Column(String)
        broker_submit_attempts = Column(Integer)
        broker_order_id = Column(String)
        broker_submitted_at = Column(DateTime)

    ns = dict(PaperTrade=Trade, select=select, update=update, datetime=datetime,
              RETRYABLE=("pending", "not_attempted", "rejected"), SUBMITTING="submitting", MAX_SUBMIT_ATTEMPTS=3)
    extract(SRC / "services/broker_submission.py", {"claimable", "begin_submission"}, ns)
    with tempfile.TemporaryDirectory() as tmp:
        engine = create_engine("sqlite:///" + tmp + "/probe.db")
        Base.metadata.create_all(engine)
        with Session(engine) as s:
            s.add(Trade(id=1, portfolio_id=1, stage="open", broker_submission_state="pending",
                        broker_submission_path="deferred", broker_submit_attempts=0))
            s.commit()
        with Session(engine, expire_on_commit=False) as dispatcher:
            selected = ns["claimable"](dispatcher)[0]
            dispatcher.commit()
            with Session(engine) as closer:
                closer.execute(update(Trade).where(Trade.id == 1).values(stage="closed"))
                closer.commit()
            claimed = ns["begin_submission"](dispatcher, selected, now=datetime(2026, 10, 2))
            dispatcher.commit()
            assert claimed and selected.stage == "closed" and selected.broker_submission_state == "submitting"
            broker = dict(claimed=claimed, stage=selected.stage, state=selected.broker_submission_state)
        engine.dispose()
    return dict(scope="local synthetic witnesses; no production access or writes",
                one_share_primary=one_share["recommendation"], last_only_primary=last_only["recommendation"]["primary"],
                last_only_leg=leg, expired_primary=expired["recommendation"]["primary"],
                expired_dte=-1, crossed_quote_legacy_mid=legacy_crossed["covered_call"]["mid_price"],
                crossed_quote_matrix_mid=mod["_mid"](crossed), closed_trade_claim=broker)


if __name__ == "__main__":
    print(json.dumps(main(), indent=2))
