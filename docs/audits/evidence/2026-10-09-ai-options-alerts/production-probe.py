import json, sys
from datetime import datetime, timezone
from sqlalchemy import text
from shared.db import SessionLocal
sys.path.insert(0, '/app')
from src.services.flow_outcome_eligibility import partition
from shared.common.market_calendar import is_trading_day

queries = {
 'stale_latest': """WITH latest AS (SELECT DISTINCT ON(stock_id,horizon) * FROM signals ORDER BY stock_id,horizon,ts DESC) SELECT st.symbol,s.horizon,s.ts,s.signal,s.reasons->>'stale_price_warning' AS stale_warning FROM latest s JOIN stocks st ON st.id=s.stock_id WHERE s.ts<CURRENT_DATE-3 ORDER BY 1,2""",
 'ai_bad_timing': "SELECT symbol,horizon,signal_date,entry_date,exit_date,hold_days FROM signal_outcomes WHERE entry_date<=signal_date ORDER BY signal_date DESC",
 'ai_model_provenance': """WITH latest AS (SELECT DISTINCT ON(stock_id,horizon) * FROM signals ORDER BY stock_id,horizon,ts DESC) SELECT st.symbol,s.horizon,s.reasons->>'ml_model' AS model,s.reasons->>'ml_test_auc' AS auc,s.reasons->>'ml_probability' AS probability,s.reasons->>'ml_weight' AS weight,s.reasons->>'ml_oos_suppressed' AS suppressed FROM latest s JOIN stocks st ON st.id=s.stock_id WHERE st.symbol IN ('MU','NVDA') ORDER BY 1,2""",
 'signal_outcomes_recent30': """SELECT st.market,o.horizon,o.signal_direction,count(*) n,count(DISTINCT o.signal_date) dates,round(100.0*avg(o.is_correct::int),2) hit_rate,round((100*avg(o.pct_return))::numeric,3) mean_raw_return FROM signal_outcomes o JOIN stocks st ON st.id=o.stock_id WHERE o.signal_date>=CURRENT_DATE-30 AND o.is_correct IS NOT NULL GROUP BY 1,2,3 ORDER BY 1,2,3""",
 'coverage_empty': "SELECT status,count(*) n FROM option_chain_coverage WHERE rows_received=0 GROUP BY status",
 'clock': 'SELECT now() AS database_time',
 'latest_signals': """WITH latest AS (SELECT DISTINCT ON (stock_id,horizon) * FROM signals ORDER BY stock_id,horizon,ts DESC)
 SELECT st.market,s.horizon,count(*) n,min(s.ts) oldest,max(s.ts) newest,
 count(*) FILTER(WHERE s.signal='BUY') buys,
 count(*) FILTER(WHERE (s.reasons->>'ml_weight')::float>0) ml_contributing,
 count(*) FILTER(WHERE s.reasons->>'ml_oos_suppressed'='true') ml_suppressed,
 count(*) FILTER(WHERE s.reasons->>'data_stale'='true') data_stale
 FROM latest s JOIN stocks st ON st.id=s.stock_id GROUP BY 1,2 ORDER BY 1,2""",
 'current_strength': """WITH latest AS (SELECT DISTINCT ON (stock_id,horizon) * FROM signals ORDER BY stock_id,horizon,ts DESC)
 SELECT st.market,s.horizon,count(*) n,round(avg(s.confidence)::numeric,2) avg_strength,
 round(percentile_cont(.5) WITHIN GROUP(ORDER BY s.confidence)::numeric,2) median_strength,
 count(*) FILTER(WHERE s.confidence<20) under20,count(*) FILTER(WHERE s.confidence>=40) over40,
 round(avg((s.reasons->>'fused_pre_compression')::float)::numeric,4) avg_pre,
 round(avg(s.bullish_probability)::numeric,4) avg_final
 FROM latest s JOIN stocks st ON st.id=s.stock_id GROUP BY 1,2 ORDER BY 1,2""",
 'current_reducers': """WITH latest AS (SELECT DISTINCT ON (stock_id,horizon) * FROM signals ORDER BY stock_id,horizon,ts DESC)
 SELECT st.market,s.horizon,count(*) n,
 count(*) FILTER(WHERE s.reasons->>'ml_oos_suppressed'='true') low_oos,
 count(*) FILTER(WHERE s.reasons->>'ml_ta_conflict'='true') ml_ta_conflict,
 count(*) FILTER(WHERE s.reasons->>'weekly_gate_fired'='true') weekly_gate,
 count(*) FILTER(WHERE s.reasons->>'adx_compression'='true') adx,
 count(*) FILTER(WHERE s.reasons->>'high_vol_compression'='true') high_vol,
 count(*) FILTER(WHERE s.reasons->>'compression_cap_applied'='true') cap_applied,
 count(*) FILTER(WHERE s.reasons->>'insufficient_history_warning'='true') short_history,
 count(*) FILTER(WHERE s.reasons->>'stale_price_warning'='true') stale
 FROM latest s JOIN stocks st ON st.id=s.stock_id GROUP BY 1,2 ORDER BY 1,2""",
 'signal_outcomes_180d': """SELECT st.market,o.horizon,o.signal_direction,count(*) total,count(o.is_correct) resolved,
 count(DISTINCT o.signal_date) dates,count(DISTINCT o.stock_id) symbols,
 round(100.0*avg(o.is_correct::int),2) hit_rate,
 round((100*avg(o.pct_return))::numeric,3) mean_raw_return,
 count(*) FILTER(WHERE o.skip_reason IS NOT NULL) censored,
 count(*) FILTER(WHERE o.entry_date<=o.signal_date) entry_not_later
 FROM signal_outcomes o JOIN stocks st ON st.id=o.stock_id
 WHERE o.signal_date>=CURRENT_DATE-180 GROUP BY 1,2,3 ORDER BY 1,2,3""",
 'signal_strength_bands': """SELECT horizon,signal_direction,CASE WHEN confidence<20 THEN '0-20' WHEN confidence<40 THEN '20-40' WHEN confidence<60 THEN '40-60' ELSE '60+' END band,
 count(*) n,count(DISTINCT signal_date) dates,round(100.0*avg(is_correct::int),2) hit_rate
 FROM signal_outcomes WHERE signal_date>=CURRENT_DATE-180 AND is_correct IS NOT NULL GROUP BY 1,2,3 ORDER BY 1,2,3""",
 'notification_states': """SELECT alert_type,state,count(*) n FROM notification_outbox GROUP BY 1,2 ORDER BY 1,2""",
 'strategy_ledger': """SELECT 'captures' kind,count(*) n FROM option_strategy_captures UNION ALL SELECT 'resolutions',count(*) FROM option_strategy_resolutions""",
 'squeezes': """SELECT alert_type,count(*) total,count(is_correct_10d) resolved,count(DISTINCT fired_date) dates,round(100.0*avg(is_correct_10d::int),2) hit_rate_10d FROM squeeze_alert_outcomes GROUP BY 1""",
 'chain_coverage': "SELECT status,count(*) n,min(as_of),max(as_of) FROM option_chain_coverage GROUP BY 1",
}
out={'retrieved_at':datetime.now(timezone.utc).isoformat()}
with SessionLocal() as s:
 for name,query in queries.items():
  try:
   s.execute(text('SET TRANSACTION READ ONLY'))
   s.execute(text("SET LOCAL statement_timeout='25s'"))
   out[name]=[dict(r) for r in s.execute(text(query)).mappings()]
  except Exception as exc: out[name]={'error':str(exc).split('\n')[0]}
  finally: s.rollback()
 s.execute(text('SET TRANSACTION READ ONLY'))
 rows=[dict(r) for r in s.execute(text('SELECT direction,symbol,fired_date,expiry,entry_date,is_correct_10d AS is_correct,return_10d AS ret FROM options_flow_alert_outcomes WHERE is_correct_10d IS NOT NULL')).mappings()]
 out['flow_eligible_10d']=[]
 for direction in ('bullish','bearish'):
  part=partition([r for r in rows if r['direction']==direction],is_trading_day=is_trading_day)
  eligible=part.pop('eligible'); part.pop('excluded',None)
  clusters={}
  for r in eligible: clusters.setdefault((r['symbol'],r['fired_date']),[]).append(r)
  out['flow_eligible_10d'].append({'direction':direction,**part,
    'hit_rate':sum(r['is_correct'] for r in eligible)/len(eligible) if eligible else None,
    'distinct_dates':len({r['fired_date'] for r in eligible}),
    'distinct_symbols':len({r['symbol'] for r in eligible}),
    'symbol_date_clusters':len(clusters),
    'equal_cluster_hit_rate':sum(sum(r['is_correct'] for r in rs)/len(rs) for rs in clusters.values())/len(clusters) if clusters else None,
    'largest_cluster':max(map(len,clusters.values()),default=0)})
 s.rollback()
print(json.dumps(out,default=str,indent=2))

