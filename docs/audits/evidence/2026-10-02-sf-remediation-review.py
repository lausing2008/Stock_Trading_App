"""Local acceptance checks and residual witnesses for 10016977; no external I/O.

Run from the repository root. Residual assertions document remaining defects,
not the behavior a release acceptance suite should require.
"""
import ast
import json
import runpy
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Column, DateTime, Integer, String, create_engine, select, update
from sqlalchemy.orm import Session, declarative_base

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / 'services/market-data/src/services'


def run():
    m = runpy.run_path(str(SRC / 'options_strategies.py'))
    kw = dict(current_price=100., stop_loss=95., take_profit=105., signal='BUY',
              put_rows=[], put_expiry=None, call_expiry='2026-11-06', shares=1.,
              iv_rank=80., today=date(2026, 10, 2),
              call_rows=[dict(strike=105., bid=2., ask=2.2)])
    normal = m['build_strategy_matrix'](**kw)
    assert normal['recommendation']['primary'] == 'long_call'
    expired = m['build_strategy_matrix'](**{**kw, 'call_expiry': '2026-10-01'})
    assert expired['recommendation']['primary'] is None
    fallback = m['build_strategy_matrix'](**{**kw, 'call_rows': [
        dict(strike=100., bid=12., ask=2.), dict(strike=105., bid=2., ask=2.2)]})
    assert fallback['recommendation']['primary'] == 'covered_call'
    malformed = m['build_strategy_matrix'](**{**kw, 'call_expiry': 'not-a-date'})
    assert malformed['recommendation']['primary'] == 'long_call'
    assert malformed['singles']['long_call']['legs'][0]['days_to_expiry'] is None

    Base = declarative_base()

    class Trade(Base):
        __tablename__ = 'trades'
        id = Column(Integer, primary_key=True)
        portfolio_id = Column(Integer)
        stage = Column(String)
        broker_order_id = Column(String)
        broker_submission_state = Column(String)
        broker_submission_path = Column(String)
        broker_submit_attempts = Column(Integer)
        broker_submitted_at = Column(DateTime)

    ns = dict(PaperTrade=Trade, select=select, update=update, datetime=datetime,
              RETRYABLE=('pending', 'not_attempted', 'rejected'),
              SUBMITTING='submitting', MAX_SUBMIT_ATTEMPTS=3)
    path = SRC / 'broker_submission.py'
    nodes = [n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef)
             and n.name in {'_eligibility', 'claimable', 'begin_submission'}]
    assert len(nodes) == 3
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    cases = [dict(stage='closed'), dict(broker_order_id='existing'),
             dict(broker_submission_path='inline'), dict(broker_submit_attempts=3),
             dict(broker_submission_state='unknown')]
    results = []
    for change in cases:
        with Session(engine) as s:
            s.add(Trade(id=1, portfolio_id=1, stage='open', broker_submission_state='pending',
                        broker_submission_path='deferred', broker_submit_attempts=0))
            s.commit()
            selected = ns['claimable'](s)[0]
            # Preserve stale ORM selection while changing the database predicate.
            s.execute(update(Trade).where(Trade.id == 1).values(**change)
                      .execution_options(synchronize_session=False))
            claimed = ns['begin_submission'](s, selected, now=datetime(2026, 10, 2))
            assert not claimed
            results.append(dict(change=change, claimed=claimed))
            s.delete(selected)
            s.commit()
    engine.dispose()
    return dict(broker_sequential_acceptance=results,
                normal_one_share=normal['recommendation']['primary'],
                expired_primary=expired['recommendation']['primary'],
                fallback_residual=fallback['recommendation'],
                malformed_expiry_residual=malformed['singles']['long_call']['legs'][0])


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
