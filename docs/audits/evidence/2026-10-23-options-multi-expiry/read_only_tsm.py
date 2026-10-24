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
 from src.services.options_strategies import covered_call_expiry_search
 original=plan['strategy_matrix']['candidate_review']['covered_call']
 def read(expiry):
  chain=_uw_option_chain(s,'TSM',expiry,as_of=source)
  assert str(chain.as_of)==str(source)
  return _options_chain_rows(chain.calls,'TSM')
 reports={}
 for holding in (14,60,120):
  review=covered_call_expiry_search(original=original,expiries=exps,today=today,
    holding_days=holding,current_price=price,read_rows=read)
  assert len(review['searched_expiries'])<=4
  assert not review['failed_expiries']
  assert all(leg['days_to_expiry']>=holding+2 for alt in review['alternatives'] for leg in alt['legs'])
  assert review['original']==original['original']
  reports[str(holding)]={k:review[k] for k in ('method_version','holding_days','eligible_expiry_count','searched_expiries','completed_expiries','failed_expiries','evaluated_count','screened_count')}
  reports[str(holding)]['alternatives']=[{'expiry':a['legs'][0]['expiry'],'strike':a['legs'][0]['strike'],'credit_before_fees':a['net_per_contract']} for a in review['alternatives']]
 result.update(source_session=str(source),today=str(today),original_call_expiry=ce,current_price=price,holding_searches=reports)
 s.rollback()
print('TSM_MULTI_EXPIRY_JSON='+json.dumps(result,sort_keys=True))
