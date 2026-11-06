import csv,json,pathlib,datetime,collections
root=pathlib.Path('/tmp/claude-1000/-home-lausing-Documents-MyProjects-Stock-Trading-App/3cd3d131-255e-4d1d-a3c9-a433a44a393d/scratchpad/rzy')
dt=datetime.datetime.fromisoformat
all_candidates=[]; totals=[]
for day in sorted(root.iterdir()):
 def read(n):
  with (day/n).open() as f:return list(csv.DictReader(f))
 revs=collections.defaultdict(list)
 for r in read('bar_revisions.csv'):
  revs[r['symbol'],r['timeframe'],r['ts']].append(r)
 paths={r['occurrence_id']:r for r in read('rzy_alert_path.csv')}
 occs=read('occurrences.csv'); n=0
 for o in occs:
  if o['timeframe']!='5m' or o['observed_in']!='incremental' or not o['signal_time']:continue
  close=dt(o['signal_time']); cutoff=dt(o['first_cutoff'])
  if close.date().isoformat()!=day.name[-10:]:continue
  n+=1; bar=(close-datetime.timedelta(minutes=5)).isoformat()
  rr=sorted(revs[o['symbol'],'5m',bar],key=lambda r:r['fetched_at'])
  later=[r for r in rr if dt(r['fetched_at'])>cutoff and r['magnitude']=='material']
  if not later:continue
  before=[r for r in rr if close<=dt(r['fetched_at'])<=cutoff]
  r=later[0]; p=paths.get(o['occurrence_id'],{})
  all_candidates.append(dict(day=day.name[-10:],**o,prior_postclose_revisions=len(before),revision_at=r['fetched_at'],max_rel_change=r['max_rel_change'],before=r['before'],after=r['after'],delivery=p.get('outcome','not_in_alert_path')))
 totals.append(dict(day=day.name[-10:],incremental_5m_events=n,late_material_revision_candidates=sum(x['day']==day.name[-10:] for x in all_candidates)))
pathlib.Path('/tmp/rzy-step2/candidates.json').write_text(json.dumps(all_candidates,indent=2))
print(json.dumps(totals,indent=2))
for r in all_candidates:print(r['day'],r['symbol'],r['event_type'],r['signal_time'],r['first_cutoff'],r['revision_at'],r['prior_postclose_revisions'],r['delivery'],r['max_rel_change'])
