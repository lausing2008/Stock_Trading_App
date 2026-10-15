"""Offline independent re-review. Run from repository root; scripted providers, disposable SQLite."""
import os, runpy, contextlib, io, json
from pathlib import Path
os.environ.pop('STOCKAI_INTEL_DB',None)
with contextlib.redirect_stdout(io.StringIO()):
    h=runpy.run_path(str(Path('services/research-engine/tests/_copilot_probe.py').resolve()))
from src.copilot import intent as I, drafts as D, screen as SC
h['reset']()
with h['Session']() as s: p=h['build_packet'](s,'TESTCO',now=h['NOW'])
r={'alerts':[],'screens':[]}
for q,threshold in [('Alert me below 10%','1'),('Alert me below 50-day average','5'),('Alert me below 1,000','1'),('Alert me below -100','100')]:
    it=I.parse_alert(q)
    v=D.validate([{'kind':'price_alert','condition':'below','threshold':threshold}],packet=p,question=q)
    r['alerts'].append({'question':q,'targets':[vars(t) for t in it.targets],
                        'refusals':it.refusals,'drafts':v.drafts,'dropped':v.dropped})
for q,raw in [('K-Score not above 70','<filter name="min_score">70</filter>'),
              ('K-Score below 70 or growth above 80','<filter name="max_score">70</filter><filter name="min_growth">80</filter>'),
              ('K-Score above 70%','<filter name="min_score">70</filter>')]:
    plan=SC.validate(SC.parse(raw),q,[])
    r['screens'].append({'question':q,'params':plan.params,'unsupported':plan.unsupported,'dropped':plan.dropped})
v=D.validate([{'kind':'price_alert','condition':'below','threshold':'{{trend.sma50}}'}],packet=p,question='Alert me below 50-day average')
r['correct_average_proposal']={'drafts':v.drafts,'dropped':v.dropped}
print(json.dumps(r,indent=2))
