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
 reports={}
 for holding in (14,60,120):
  cache={}
  def read(expiry,right):
   if expiry not in cache:
    chain=_uw_option_chain(s,'TSM',expiry,as_of=source)
    assert str(chain.as_of)==str(source)
    cache[expiry]=chain
   chain=cache[expiry]
   return _options_chain_rows(chain.calls if right=='call' else chain.puts,'TSM')
  per_strategy={}
  for key,(rows,expiry,anchor,right) in specs.items():
   review=covered_call_expiry_search(original=originals[key],expiries=exps,today=today,
     holding_days=holding,current_price=price,read_rows=lambda e,right=right:read(e,right),strategy_key=key)
   assert len(cache)<=4 and not review['failed_expiries']
   assert review['original']==originals[key]['original']
   assert all(leg['days_to_expiry']>=holding+2 and leg['right']==right for alt in review['alternatives'] for leg in alt['legs'])
   assert all(a['max_loss_per_contract']>=0 for a in review['alternatives'])
   assert review['alternatives'],(holding,key)
   per_strategy[key]={k:review[k] for k in ('method_version','holding_days','eligible_expiry_count','searched_expiries','completed_expiries','failed_expiries','evaluated_count','screened_count')}
   per_strategy[key]['alternatives']=[{'expiry':a['legs'][0]['expiry'],'strike':a['legs'][0]['strike'],
     'action':a['legs'][0]['action'],'right':a['legs'][0]['right'],'net':a['net'],
     'premium_before_fees':a['net_per_contract'],'max_loss_per_contract':a['max_loss_per_contract'],
     'capital_per_contract':a['collateral_per_contract']} for a in review['alternatives']]
  reports[str(holding)]={'additional_archive_reads':len(cache),'strategies':per_strategy}
 result.update(source_session=str(source),today=str(today),original_call_expiry=ce,current_price=price,holding_searches=reports)
 s.rollback()
print('TSM_FOUR_STRATEGY_JSON='+json.dumps(result,sort_keys=True))
