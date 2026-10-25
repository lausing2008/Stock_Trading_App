"""Operator verification: real provider capture/readback, no user positions or orders.

Invokes deployed handlers directly in a separate process, so HTTP authentication is not
exercised here. Route guard coverage is separate. The only new data is a ten-minute quote cache.
"""
import json
from sqlalchemy import text
from db import SessionLocal
from src.api import routes
with SessionLocal() as session:
    session.execute(text('SET TRANSACTION READ ONLY'))
    session.execute(text("SET LOCAL statement_timeout='15s'"))
    session.execute(text('SELECT 1'))
    assert session.in_transaction()
    captured=routes.refresh_options_snapshot('MU',expiry=None,session=session,_user=None)
    assert captured['available'],captured
    assert not session.in_transaction(), 'auth/read transaction held across external capture'
meta=captured['snapshot'];token=meta['id']
# This process only: prove that subsequent calculations cannot read archived chains.
def no_archive(*args,**kwargs):raise AssertionError('archive read in snapshot mode')
routes._uw_option_chain=no_archive
plan=routes.get_snapshot_game_plan('MU',token,stop_loss=850,take_profit=1561.91,
    shares=100,holding_days=14,min_dte=14,max_dte=60,min_abs_delta=None,max_abs_delta=None,_user=None)
assert plan['option_snapshot']['id']==token
leg=plan['strategy_matrix']['singles']['covered_call']['legs'][0]
chain=routes.get_snapshot_chain('MU',token,leg['expiry'],_user=None)
row=next(r for r in chain['calls'] if r['option_symbol']==leg['option_symbol'])
assert chain['option_snapshot']['id']==token
assert abs(leg['price_per_share']-(row['bid']+row['ask'])/2)<=.00500001
assert plan['current_price']==meta['stock_reference']
assert abs(leg['strike']-plan['current_price'])<plan['current_price']*.01
reviews=plan['strategy_matrix']['candidate_review']
assert len(reviews)==4 and all(r['snapshot_id']==token for r in reviews.values())
assert all(r['listing_verification']=='provider_current_contracts' for r in reviews.values())
print('DEPLOYED_CURRENT_SNAPSHOT='+json.dumps({'metadata':meta,'selected_contract':leg['option_symbol'],
    'selected_strike':leg['strike'],'expiry':leg['expiry'],'plan_premium_per_share':leg['price_per_share'],
    'snapshot_bid':row['bid'],'snapshot_ask':row['ask'],'contract_delta':leg.get('delta'),
    'reported_last_trade':leg.get('last_tape_time'),'all_four_searches_same_snapshot':True,
    'auth_read_transaction_released':True,'archive_reads_disabled_for_recalculation':True,
    'scope':'Direct deployed-handler capture/readback, not authenticated browser acceptance, broker confirmation or expected-return validation'},default=str))
