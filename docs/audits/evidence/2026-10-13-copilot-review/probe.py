"""Offline independent review probe. Run from repository root; always uses disposable SQLite and scripted models."""
import os, runpy, contextlib, io, json, asyncio
from pathlib import Path
os.environ.pop('STOCKAI_INTEL_DB', None) # Always the harness's disposable SQLite database.
p = Path.cwd() / 'services/research-engine/tests/_copilot_probe.py'
with contextlib.redirect_stdout(io.StringIO()):
    h = runpy.run_path(str(p))
globals().update({k:h[k] for k in ('reset','Session','NOW','build_packet','G','C','St','L','turn','run','ScriptedModel','GOOD','CopilotMessage')})
result={}
reset(symbol='0700.HK')
with Session() as s: packet=build_packet(s,'0700.HK',now=NOW)
result['unsupported_numbers']={t:G.check({'answer':t},packet).accepted for t in (
    'The target is twenty dollars.', 'The target is 0700 dollars.',
    'The target is 0700.HK dollars.', 'The price will double.', 'The target is $999.')}
reset()
os.environ['COPILOT_DAILY_ANSWERS_PER_USER']='1'
async def quota():
    entered=0
    release=asyncio.Event()
    async def model(*args):
        nonlocal entered
        entered+=1
        if entered==3: release.set()
        await asyncio.wait_for(release.wait(),2)
        return L.LlmReply(text=json.dumps(GOOD),model='scripted',usage={'input_tokens':10,'output_tokens':10})
    out=await asyncio.gather(*(C.answer(turn(rid=f'quota-{i:08d}'),session_factory=Session,llm_call=model) for i in range(3)),return_exceptions=True)
    return {'daily_limit':1,'model_calls':entered,'completed':sum(isinstance(x,dict) for x in out)}
result['concurrent_quota']=run(quota())
os.environ.pop('COPILOT_DAILY_ANSWERS_PER_USER')
reset()
first=run(C.answer(turn(question='Original question'),session_factory=Session,llm_call=ScriptedModel(GOOD)))
reused=run(C.answer(turn(question='A different question',symbol='UNKNOWN',conv=999999),session_factory=Session,llm_call=ScriptedModel()))
result['reused_id_different_payload']={'replayed':reused['replayed'],'same_answer':reused['message']['id']==first['message']['id']}
reset()
try: run(C.answer(turn(question='Original question'),session_factory=Session,llm_call=ScriptedModel(L.LlmError('failure'))))
except C.CopilotError: pass
model=ScriptedModel(GOOD)
run(C.answer(turn(question='Different retry question'),session_factory=Session,llm_call=model))
with Session() as s:
    stored=s.query(CopilotMessage).filter_by(role='user').one().content
result['failed_retry_payload']={'stored_question':stored,'model_received_different_question':'Different retry question' in model.calls[0][-1]['content']}
reset()
os.environ['COPILOT_DAILY_ANSWERS_PER_USER']='1'
first=run(C.answer(turn(),session_factory=Session,llm_call=ScriptedModel(GOOD)))
with Session() as s:
    St.delete_conversation(s,1,first['conversation_id']); s.commit()
second=run(C.answer(turn(rid='after-delete-0001'),session_factory=Session,llm_call=ScriptedModel(GOOD)))
result['quota_after_delete']={'daily_limit':1,'second_answer_accepted':second['message']['status']=='grounded'}
os.environ.pop('COPILOT_DAILY_ANSWERS_PER_USER')
reset()
from datetime import timedelta
with Session() as s:
    for offset in (30,29):
        when=NOW-timedelta(days=offset)
        f,e,m,c=h['IG'].stock_outlook(s,symbol='TESTCO',now=when)
        h['IS'].save(s,f,e,m,c,generated_at=when)
    s.commit()
    packet=build_packet(s,'TESTCO',now=NOW)
    result['old_report']={'generated_at':packet.fields['report'].value['generated_at'],
        'packet_time':NOW.isoformat(),'report_state':packet.fields['report'].state.value,
        'changes_state':packet.fields['report_changes'].state.value}
reset()
with Session() as s:
    packet=build_packet(s,'TESTCO',now=NOW)
    refs=C._evidence_refs(packet,['trend.sma50'])
    result['sma50_provenance']={'bars_used':50,'packet_evidence_records':len(packet.fields['trend'].evidence_ids),
                              'returned_evidence_records':len(refs[0]['evidence'])}
    s.delete(s.get(h['Price'],3)); s.commit()
    packet=build_packet(s,'TESTCO',now=NOW)
    result['missing_bar_return']={'state':packet.quantities['return.5s'].state,
       'meaning':packet.quantities['return.5s'].meaning,
       'endpoint_dates':[packet.book.records[eid]['observed_period'] for eid in packet.fields['return_5s'].evidence_ids]}
print(json.dumps(result,indent=2))
