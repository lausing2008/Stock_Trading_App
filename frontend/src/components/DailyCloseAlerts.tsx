import React,{useEffect,useRef,useState} from 'react';
import {workflowRequest} from '@/lib/workspaceApi';
import {newRequestId,type PlanLevel} from '@/lib/copilot';
type CloseAlert={id:number;symbol:string;condition:string;threshold:number;active:boolean;observation?:{state:string;reason?:string}};
export default function DailyCloseAlerts({symbol,currency,levels=[],asOf}:{symbol:string;currency?:string|null;levels?:PlanLevel[];asOf?:string|null}){
  const [rows,setRows]=useState<CloseAlert[]>([]),[value,setValue]=useState(''),[condition,setCondition]=useState('below');
  const [error,setError]=useState(''),[draft,setDraft]=useState<{symbol:string;condition:string;threshold:number;origin:'typed'|'candidate';request_id:string}|null>(null),[busy,setBusy]=useState(false);
  const [origin,setOrigin]=useState<'typed'|'candidate'>('typed');
  const generation=useRef(0);
  useEffect(()=>{const n=++generation.current;setDraft(null);setValue('');setError('');setRows([]);
    workflowRequest<CloseAlert[]>('/close-alerts').then(x=>{if(generation.current===n)setRows(x.filter(a=>a.symbol===symbol));}).catch(e=>{if(generation.current===n)setError(e.message);});
    return()=>{generation.current++;};},[symbol]);
  async function save(){if(!draft)return;const n=generation.current;setBusy(true);setError('');
    try{const a=await workflowRequest<CloseAlert>('/close-alerts',{method:'POST',body:JSON.stringify(draft)});if(n===generation.current){setRows(r=>[a,...r.filter(x=>x.id!==a.id)]);setDraft(null);}}
    catch(e){if(n===generation.current)setError(e instanceof Error?e.message:'Could not save');}finally{if(n===generation.current)setBusy(false);}}
  return <section aria-label="Daily-close alerts" style={{padding:12,border:'1px solid #334155',borderRadius:8}}>
    <h4>Daily-close monitoring</h4><p>One-shot in-app notification after a completed regular session and a fresh provider daily bar. A missing bar delays the check. No order is placed and no stop is maintained.</p>
    {levels.some(l=>l.state==='OK')&&<label>Use a plan level ({asOf??'stored snapshot'}) <select aria-label="Daily-close plan level" defaultValue="" onChange={e=>{const l=levels.find(x=>x.key===e.target.value&&x.state==='OK');if(l){setValue(String(l.price));setOrigin('candidate');setDraft(null);}}}><option value="">Choose a fixed snapshot level</option>{levels.filter(l=>l.state==='OK').map(l=><option key={l.key} value={l.key}>{l.label}: {l.price}</option>)}</select></label>}
    <label>Daily close <select value={condition} onChange={e=>{setCondition(e.target.value);setDraft(null);}}><option value="below">at or below</option><option value="above">at or above</option></select></label>{' '}
    <label>Fixed level ({currency??'stock currency'}) <input type="number" min="0" step="any" value={value} onChange={e=>{setValue(e.target.value);setOrigin('typed');setDraft(null);}}/></label>
    <button type="button" disabled={busy||!Number.isFinite(Number(value))||Number(value)<=0} onClick={()=>setDraft({symbol,condition,threshold:Number(value),origin,request_id:newRequestId()})}>Review close alert</button>
    {draft&&<div><p>Notify me in-app once if {draft.symbol} closes {draft.condition==='below'?'at or below':'at or above'} {draft.threshold} {currency}. This fixed level will not follow a moving average. Only sessions closing after enrollment qualify.</p><button type="button" disabled={busy} onClick={save}>{busy?'Saving…':'Confirm daily-close alert'}</button><button type="button" disabled={busy} onClick={()=>setDraft(null)}>Cancel</button></div>}
    {error&&<p role="alert">{error}</p>}
    <ul>{rows.map(a=><li key={a.id}>{a.symbol} {a.condition} {a.threshold}: {a.active?(a.observation?.state??'waiting'):a.observation?.state==='triggered'?'triggered':'disabled'} {a.observation?.reason}
      {a.active&&<button type="button" onClick={async()=>{const n=generation.current;try{await workflowRequest(`/close-alerts/${a.id}`,{method:'DELETE'});if(n===generation.current)setRows(r=>r.map(x=>x.id===a.id?{...x,active:false}:x));}catch(e){if(n===generation.current)setError(e instanceof Error?e.message:'Could not disable');}}}>Disable</button>}</li>)}</ul>
  </section>;
}
