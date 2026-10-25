import json
from sqlalchemy import text
from db import SessionLocal
with SessionLocal() as s:
 s.execute(text('SET TRANSACTION READ ONLY'))
 s.execute(text("SET LOCAL statement_timeout='15s'"))
 d=s.execute(text("SELECT max(as_of) FROM option_chain_history WHERE symbol='MU'")).scalar()
 rows=s.execute(text("SELECT option_symbol,strike,expiry,option_type,nbbo_bid,nbbo_ask,delta FROM option_chain_history WHERE symbol='MU' AND as_of=:d AND expiry='2026-11-20' AND option_type IN ('call','C','c') AND (strike BETWEEN 855 AND 900 OR strike BETWEEN 1540 AND 1580) ORDER BY strike LIMIT 25"),{'d':d}).mappings().all()
 print(json.dumps({'session':str(d),'rows':[dict(r) for r in rows]},default=str))
 s.rollback()
