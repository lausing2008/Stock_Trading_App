"""Read bounded retained Docker logs; output aggregates only, never recipients or subjects."""
import subprocess,json,collections,re
r=subprocess.run(['docker','logs','--since','2026-09-01T00:00:00Z','--until','2026-10-01T00:00:00Z','--tail','60000','--timestamps','stockai-market-data-1'],capture_output=True,text=True,timeout=45)
events=collections.Counter();reasons=collections.Counter();hours=collections.Counter();times=[];parsed=0
for line in (r.stdout+'\n'+r.stderr).splitlines():
 m=re.match(r'^(\S+)\s+(.*)$',line)
 if not m:continue
 ts,msg=m.groups()
 try:d=json.loads(msg)
 except (ValueError,TypeError):continue
 if not isinstance(d,dict):continue
 ev=str(d.get('event',''));parsed+=1;times.append(ts)
 if ev.startswith(('paper.','signal_alert.','scheduler.paper','email.','options_flow_alert.')):
  events[ev]+=1
  if ev=='email.sent':hours[ts[:13]]+=1
  if ev.startswith(('paper.','signal_alert.')):
   reason=d.get('reason') or d.get('blocked') or d.get('reasons') or d.get('failed')
   if reason:reasons[(ev,json.dumps(reason,sort_keys=True))]+=1
print(json.dumps({'returncode':r.returncode,'tail_limit':60000,'parsed_json_lines':parsed,'first':min(times) if times else None,'last':max(times) if times else None,'events':dict(events.most_common()),'reason_counts':[{'event':k[0],'reason':k[1],'count':v} for k,v in reasons.most_common(65)],'all_email_acceptances_by_utc_hour':dict(sorted(hours.items()))},indent=2))
