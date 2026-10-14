"""Offline phase-2 review counterexamples. Run from repository root; no provider calls."""
import os, runpy, contextlib, io, json
from pathlib import Path
os.environ.pop('STOCKAI_INTEL_DB', None)
with contextlib.redirect_stdout(io.StringIO()):
    h=runpy.run_path(str(Path('services/research-engine/tests/_copilot_probe.py').resolve()))
from src.copilot import screen as SC, drafts as D
C=h['C']; Session=h['Session']; run=h['run']; reset=h['reset']; Model=h['ScriptedModel']; turn=h['turn']
r={}
cases=[('K-Score below 70','<filter name="min_score">70</filter>'),
       ('Show 10 stocks with K-Score above 70','<filter name="min_confidence">10</filter>'),
       ('Technology stocks, excluding BUY signals','<filter name="signal">BUY</filter>')]
r['screen_validation']=[{'question':q,'accepted_params':SC.validate(SC.parse(raw),q,['Technology']).params} for q,raw in cases]
reset()
with Session() as s: p=h['build_packet'](s,'TESTCO',now=h['NOW'])
r['draft_validation']=[]
for q,d in [('Alert me if it drops 10%',{'kind':'price_alert','condition':'below','threshold':'10'}),
            ('Alert me below 100',{'kind':'price_alert','condition':'above','threshold':'100'}),
            ('Alert me below its 50-bar average',{'kind':'price_alert','condition':'below','threshold':'{{kscore.fair_price}}'})]:
    v=D.validate([d],packet=p,question=q)
    r['draft_validation'].append({'question':q,'drafts':v.drafts,'dropped':v.dropped})

def make(market,rid='phase2-review-1',two=False):
    req=turn(rid=rid,question='Alert me below 100 and above 150')
    req.market=market
    text='<answer>Review the proposed alerts.</answer><draft kind="price_alert" condition="below" threshold="100"/>'
    if two: text+='<draft kind="price_alert" condition="above" threshold="150"/>'
    return run(C.answer(req,session_factory=Session,llm_call=Model(text)))

class LostAck(h['FakeMarket']):
    async def create_alert(self,body):
        await super().create_alert(body)
        raise D.MarketError(502,'response lost after committing')
reset(); market=LostAck(); out=make(market); mid=out['message']['id']; did=out['message']['drafts'][0]['id']
errors=[]
for _ in range(2):
    try: run(C.confirm_draft(Session,'alice',mid,did,market))
    except C.CopilotError as e: errors.append({'status':e.status,'message':e.message})
r['lost_save_ack']={'errors':errors,'alerts_created':len(market.alerts_),'writes_attempted':market.calls.count('create_alert')}
reset(); market=h['FakeMarket'](); out=make(market,two=True); results=[]
for d in out['message']['drafts']:
    try: results.append(run(C.confirm_draft(Session,'alice',out['message']['id'],d['id'],market))['draft']['status'])
    except C.CopilotError as e: results.append({'status':e.status,'message':e.message})
r['two_sibling_drafts']={'results':results,'alerts_created':len(market.alerts_)}

class PrefsMarket(h['FakeMarket']):
    enabled=False
    async def watchlist_preview(self,symbol):
        p=await super().watchlist_preview(symbol)
        p.update(email_on_file=True,signal_emails_enabled=self.enabled,effect='Emails ON' if self.enabled else 'Emails OFF')
        return p
m=PrefsMarket(); d={'kind':'watchlist_add','arguments':{'symbol':'TESTCO'}}
a=run(D.state_and_effects(m,d)); m.enabled=True; b=run(D.state_and_effects(m,d))
r['changed_email_effect']={'before':a[1],'after':b[1],'same_state_version':a[0]==b[0]}
print(json.dumps(r,indent=2))
