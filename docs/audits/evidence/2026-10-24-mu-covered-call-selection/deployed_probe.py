"""Read-only replay of the user's reference/target through deployed computation code."""
import json
from datetime import date
from sqlalchemy import text
from db import SessionLocal
from src.api.routes import compute_options_game_plan, _options_chain_rows, _uw_option_chain
with SessionLocal() as s:
    s.execute(text('SET TRANSACTION READ ONLY'))
    s.execute(text("SET LOCAL statement_timeout='15s'"))
    source = s.execute(text("SELECT max(as_of) FROM option_chain_history WHERE symbol='MU'")).scalar()
    chain = _uw_option_chain(s, 'MU', '2026-11-20', as_of=source)
    calls = _options_chain_rows(chain.calls, 'MU')
    plan = compute_options_game_plan(current_price=877.55, stop_loss=850,
        take_profit=1561.91, signal=None, put_expiries=[], put_rows=[],
        call_expiries=['2026-11-20'], call_rows=calls, today=date(2026,10,24), shares=100)
    matrix = plan['strategy_matrix']
    review = matrix['candidate_review']['covered_call']
    assert plan['covered_call']['strike'] == 880
    assert matrix['singles']['covered_call']['legs'][0]['strike'] == 880
    assert review['anchor'] == 877.55 and review['anchor_basis'] == 'reference_spot'
    alternatives = [a['legs'][0]['strike'] for a in review['alternatives']]
    assert all(abs(k-877.55)<30 for k in alternatives)
    print('MU_DEPLOYED_REPLAY='+json.dumps({'source_session':str(source),'expiry':'2026-11-20',
        'replay_date':'2026-10-24','user_reference_price':877.55,'separate_stock_forecast':1561.91,
        'source_call_rows':len(calls),'legacy_strike':plan['covered_call']['strike'],
        'matrix_strike':matrix['singles']['covered_call']['legs'][0]['strike'],
        'anchor_basis':review['anchor_basis'],'alternative_strikes':alternatives,
        'listing_verification':review['listing_verification'],
        'scope':'Read-only deployed-code replay, not authenticated production UI or executable broker prices'}))
    s.rollback()
