"""Independent C1-C5 follow-up cases; disposable SQLite and scripted model, no paid calls.
Run from the repository root. STOCKAI_INTEL_DB is cleared only in this process.
"""
import os, runpy, contextlib, io, json
from pathlib import Path
os.environ.pop('STOCKAI_INTEL_DB',None)
with contextlib.redirect_stdout(io.StringIO()):
    h=runpy.run_path(str(Path('services/research-engine/tests/_copilot_plan_probe.py').resolve()))
I,G=h['I'],h['G']; h['reset']()
r={'options':[],'risk':[],'forecasts':[]}
for q in ('I have 2 AAPL January calls at 50. What should I do?',
          'I bought long calls at 50. What should I do?',
          'I bought 2 AAPL January calls at 50. What should I do?',
          'I bought 100 shares at 95, are the SWING calls still BUY?'):
    it=I.parse_position(q); p=h['pkt'](q)
    r['options'].append({'question':q,'parsed':vars(it),'rules':sorted(k for k in p.quantities if k.startswith('rule.')),
                         'fallback_rules':[k for k in G.fallback_keys(p) if k.startswith('rule.')]})
for q in ('I want to buy. I can lose 10%',
          'I want to buy. I can lose 10%, account 50000',
          'I want to buy, risk $10k',
          'I want to buy, risk $500, actually risk $1000',
          'I want to buy, risk $1.2.3k',
          'I bought 10 shares at 95, my stop is 5%'):
    it=I.parse_position(q);p=h['pkt'](q)
    r['risk'].append({'question':q,'parsed':vars(it),'sizing':h['disp'](p,'rule.e6'),'exit':h['disp'](p,'rule.h6')})
p=h['pkt']('forecast?')
for t in ('There is no doubt the stock will rise.',
          'I do not own shares and the stock will rise.',
          'If you are wondering about next week, the stock will rise.',
          'I cannot say whether it will rise.'):
    out=G.check({'answer':t+' {{call.swing}}','limitations':[]},p)
    r['forecasts'].append({'draft':t,'accepted':out.accepted,'reasons':out.reasons})
print(json.dumps(r,indent=2,default=str))
