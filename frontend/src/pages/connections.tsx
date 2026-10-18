import React,{useEffect,useState} from 'react';
import {getSession} from '@/lib/auth';
import {workflowRequest} from '@/lib/workspaceApi';

type Edge={id:number;source_symbol:string;target_symbol:string;approved:boolean;evidence_url:string;evidence_excerpt:string;mechanism:string;counterargument:string;falsifier:string};
type Card={id:number;symbol:string;status:string;published_at:string|null;baseline:unknown;outcome:unknown;payload:{event:{headline:string;url:string;published_at:string};relationship:Edge;interpretation:string;counterargument:string;falsifier:string;horizon_days:number;supersedes?:number|null}};
export default function Connections(){
  const [admin,setAdmin]=useState(false),[queue,setQueue]=useState(false),[cards,setCards]=useState<Card[]>([]),[edges,setEdges]=useState<Edge[]>([]),[error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(false);
  const today=new Date().toISOString().slice(0,10);
  const [form,setForm]=useState({source_symbol:'',target_symbol:'',relation:'supplier',evidence_url:'',evidence_excerpt:'',valid_from:today,valid_to:today,keywords:'',mechanism:'',counterargument:'',falsifier:'',horizon_days:'10',direction:'watch'});
  useEffect(()=>setAdmin(getSession()?.role==='admin'),[]);
  async function load(review=queue){setError('');try{setCards(await workflowRequest<Card[]>(`/connections?review_queue=${review}`));if(admin)setEdges(await workflowRequest<Edge[]>('/connections/relationships'));}catch(e){setError(e instanceof Error?e.message:'Could not load connections');}}
  useEffect(()=>{void load();},[admin,queue]);
  async function mutate(path:string,body:unknown){setBusy(true);setError('');try{const result=await workflowRequest<{proposed?:number;status?:string;skipped?:{target:string;reason:string}[]}>(path,{method:'POST',body:JSON.stringify(body)});setNotice(result.proposed!==undefined?`${result.proposed} candidates added to review queue.`:[result.status??'Review saved.',...(result.skipped??[]).map(x=>`${x.target}: ${x.reason}`)].join(' '));await load();}catch(e){setError(e instanceof Error?e.message:'Could not save review');}finally{setBusy(false);}}
  return <main style={{maxWidth:1000,margin:'0 auto',padding:24,color:'#cbd5e1'}}>
    <h1>Event connections</h1><p>Reviewed research on how a sourced event may reach another company. Connections are hypotheses, not price forecasts or trading signals. This pilot sends no email and places no orders.</p>
    <a href="/">Back to dashboard</a>{admin&&<label style={{display:'block'}}><input type="checkbox" checked={queue} onChange={e=>setQueue(e.target.checked)}/> Review queue</label>}
    <button onClick={()=>load()}>Refresh research</button>{error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
    {!cards.length&&<p>No {queue?'pending':'published'} connections. A sourced relationship and explicit review are required before publication.</p>}
    {cards.map(c=><article key={c.id} style={{padding:16,border:'1px solid #334155',marginTop:12}}>
      <h2>{c.payload.relationship.source_symbol} → {c.symbol}</h2><p><a href={c.payload.event.url} target="_blank" rel="noopener noreferrer">{c.payload.event.headline}</a> · {c.payload.event.published_at}</p>
      <p><strong>Relationship fact:</strong> {c.payload.relationship.evidence_excerpt} <a href={c.payload.relationship.evidence_url} target="_blank" rel="noopener noreferrer">Relationship source</a></p>
      <p><strong>Possible mechanism:</strong> {c.payload.relationship.mechanism}</p><p>{c.payload.interpretation}</p>
      <p><strong>Counterargument:</strong> {c.payload.counterargument}</p><p><strong>Would invalidate it:</strong> {c.payload.falsifier}</p>
      {c.payload.supersedes&&<button onClick={async()=>{try{const previous=await workflowRequest<Card>(`/connections/${c.payload.supersedes}`);setCards(rows=>rows.some(x=>x.id===previous.id)?rows:[...rows,previous]);}catch(e){setError(e instanceof Error?e.message:'Could not load previous version');}}}>Read previous research version</button>}
      <p>Evaluation horizon: {c.payload.horizon_days} calendar days from publication. Status: {c.status}.</p>
      {c.outcome!=null&&<details><summary>Prospective observation</summary><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(c.outcome,null,2)}</pre></details>}
      <a href={`/stock/${encodeURIComponent(c.symbol)}`}>Open stock analysis</a>
      {admin&&c.status==='pending'&&<><button disabled={busy} onClick={()=>mutate(`/connections/${c.id}/review`,{approve:true})}>Publish reviewed hypothesis</button><button disabled={busy} onClick={()=>mutate(`/connections/${c.id}/review`,{approve:false})}>Reject</button></>}
    </article>)}
    {admin&&<details><summary>Maintain the reviewed relationship catalogue</summary>
      <p>Enter a primary-source relationship and a specific event keyword. Relationship approval and each card's publication are separate reviews. No relationships are preapproved.</p>
      <button disabled={busy} onClick={()=>mutate('/connections/catalog/import',{})}>Import unapproved semiconductor starter catalogue</button>
      <p>The starter sources are AMD and NVIDIA annual filings. Import requires TSM and the target stock to be tracked; read each source before approving. Catalogue review expires January 15, 2027.</p>
      <form onSubmit={e=>{e.preventDefault();void mutate('/connections/relationships',{...form,source_symbol:form.source_symbol.toUpperCase().trim(),target_symbol:form.target_symbol.toUpperCase().trim(),keywords:form.keywords.split(',').map(x=>x.trim()).filter(Boolean),horizon_days:Number(form.horizon_days)});}}>
        {Object.entries(form).map(([k,v])=><label key={k} style={{display:'block',margin:8}}>{k.replace(/_/g,' ')} <input required value={v} type={k.startsWith('valid_')?'date':k==='evidence_url'?'url':k==='horizon_days'?'number':'text'} maxLength={k==='evidence_url'?2048:1500} onChange={e=>setForm(f=>({...f,[k]:e.target.value}))}/></label>)}
        <p>Relation: supplier, customer, competitor or substitute. Direction: up, down or watch.</p><button disabled={busy}>Save unapproved relationship</button>
      </form>
      <ul>{edges.map(e=><li key={e.id}>{e.source_symbol} → {e.target_symbol}: {e.approved?'approved':'unapproved'} <a href={e.evidence_url} target="_blank" rel="noopener noreferrer">Source</a><button disabled={busy} onClick={()=>mutate(`/connections/relationships/${e.id}/review`,{approve:!e.approved})}>{e.approved?'Revoke relationship':'Approve reviewed relationship'}</button></li>)}</ul>
      <button disabled={busy} onClick={()=>mutate('/connections/propose',{})}>Find candidates in recent ingested news</button>
    </details>}
  </main>;
}
