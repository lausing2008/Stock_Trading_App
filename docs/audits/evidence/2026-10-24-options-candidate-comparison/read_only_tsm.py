import json
from datetime import datetime,timezone
from sqlalchemy import text
from db import SessionLocal
from src.api.routes import (_uw_session_expiries,_uw_option_chain,_options_chain_rows,_goal_current_price,_nearest_expiry_in_dte_window,_today_et,compute_options_game_plan)
from src.services.options_scope import current_expiries
from src.services.options_strategies import _mid,_contract_supported
result={'checked_at':datetime.now(timezone.utc).isoformat(),'symbol':'TSM','method':'read_only_stored_chain_reproduction','screenshot_inputs':{'stop_loss':404.07,'take_profit':584.39}}
with SessionLocal() as s:
 s.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
 s.execute(text("SET LOCAL statement_timeout = '10s'"))
 source,exps=_uw_session_expiries(s,'TSM'); today=_today_et();exps=current_expiries(exps,source,symbol='TSM')
 ce=_nearest_expiry_in_dte_window(exps,today,14,45); pe=_nearest_expiry_in_dte_window(exps,today,25,60)
 cc=_uw_option_chain(s,'TSM',ce,as_of=source);pc=_uw_option_chain(s,'TSM',pe,as_of=source)
 calls=_options_chain_rows(cc.calls,'TSM');puts=_options_chain_rows(pc.puts,'TSM')
 price=_goal_current_price(s,'TSM')
 plan=compute_options_game_plan(current_price=price,stop_loss=404.07,take_profit=584.39,signal=None,put_expiries=[pe],put_rows=puts,call_expiries=[ce],call_rows=calls,shares=None,today=today)
 from src.services.options_strategies import covered_call_expiry_search, _covered_call_candidates
 from src.services.options_contract import split_supported
 specs={'covered_call':(calls,ce,584.39,'call'), 'long_call':(calls,ce,price,'call'),
        'protective_put':(puts,pe,404.07,'put'), 'cash_secured_put':(puts,pe,404.07,'put')}
 originals={}
 for key,(rows,expiry,anchor,right) in specs.items():
  originals[key]=_covered_call_candidates(split_supported(rows)[0],anchor,expiry,
    (datetime.fromisoformat(expiry).date()-today).days,today,price,strategy_key=key)
 cache={}
 def read(expiry,right):
  if expiry not in cache:
   chain=_uw_option_chain(s,'TSM',expiry,as_of=source)
   assert str(chain.as_of)==str(source)
   cache[expiry]=chain
  chain=cache[expiry]
  return _options_chain_rows(chain.calls if right=='call' else chain.puts,'TSM')
 comparisons={}
 for key,(rows,expiry,anchor,right) in specs.items():
  review=covered_call_expiry_search(original=originals[key],expiries=exps,today=today,
    holding_days=14,current_price=price,read_rows=lambda e,right=right:read(e,right),strategy_key=key)
  assert not review['failed_expiries'] and review['alternatives']
  comparisons[key]={'original':plan['strategy_matrix']['singles'].get(key),'original_reference':review['original'],
    'original_expiry':expiry,'alternatives':review['alternatives'],'searched_expiries':review['searched_expiries']}
 assert len(cache)<=4
 result.update(source_session=str(source),today=str(today),current_price=price,holding_days=14,
   extra_archive_reads=len(cache),comparisons=comparisons)
 s.rollback()
print('TSM_COMPARISON_JSON='+json.dumps(result,sort_keys=True))
