"""One-off public delayed reference; not a licensed app feed integration or broker check."""
import json, urllib.request
from datetime import datetime, timezone
url='https://cdn.cboe.com/api/global/delayed_quotes/options/MU.json'
with urllib.request.urlopen(url,timeout=15) as r:
 payload=json.load(r)
data=payload.get('data',{})
rows=data.get('options',[])
calls=[r['option'] for r in rows if str(r.get('option','')).startswith('MU261120C')]
strikes=sorted(int(o[-8:])/1000 for o in calls)
print(json.dumps({'source':url,'checked_at_utc':datetime.now(timezone.utc).isoformat(),
 'provider_timestamp_as_reported':payload.get('timestamp'),'timestamp_timezone':'not supplied in timestamp string',
 'reference_price':data.get('current_price'),'total_contracts':len(rows),
 'expiry':'2026-11-20','standard_root_call_count':len(calls),'min_strike':min(strikes),'max_strike':max(strikes),
 'near_price_strikes':[s for s in strikes if 855<=s<=900],
 'specific_contracts':{o:o in calls for o in ('MU261120C01560000','MU261120C01580000','MU261120C00880000')},
 'scope':'Independent delayed-chain presence only; not a broker quote, executable availability guarantee or deliverable verification'},indent=2))
