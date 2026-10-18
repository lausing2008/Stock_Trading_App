import React, {useEffect,useRef,useState} from 'react';
import {getWorkspaceLayout,putWorkspaceLayout,WorkflowError,type CloudLayout} from '@/lib/workspaceApi';
import {getToken} from '@/lib/auth';
import type {WorkspaceLayout} from '@/lib/stockWorkspaceLayout';

/** Opt-in account sync with explicit conflict resolution; never merges away another device. */
export default function WorkspaceSyncControls({storageKey,layout,onLoad}:{storageKey:string;layout:WorkspaceLayout;onLoad:(v:WorkspaceLayout)=>void}) {
  const [enabled,setEnabled]=useState(false),[revision,setRevision]=useState<number|null>(null);
  const [remote,setRemote]=useState<CloudLayout|null>(null),[message,setMessage]=useState('');
  const [retry,setRetry]=useState(0);
  const sessionToken=useRef(getToken());
  function assertAccount(){if(getToken()!==sessionToken.current)throw new WorkflowError('Account changed; reopen the workspace',401);}
  const live=useRef(true),busy=useRef(false),saved=useRef(''),local=useRef(layout);local.current=layout;
  useEffect(()=>{live.current=true;try{setEnabled(localStorage.getItem(`${storageKey}:cloud`)==='1');}catch{};
    const online=()=>{if(!busy.current)setRetry(n=>n+1);};window.addEventListener('online',online);window.addEventListener('focus',online);
    return()=>{live.current=false;window.removeEventListener('online',online);window.removeEventListener('focus',online);};},[storageKey]);
  useEffect(()=>{
    if(!enabled)return;
    let active=true;
    try{assertAccount();}catch{setMessage('Account changed; reopen the workspace.');return;}
    getWorkspaceLayout().then(r=>{
      if(!active||!live.current)return;
      setRevision(r.revision);
      if(r.layout&&JSON.stringify(r.layout)!==JSON.stringify(local.current)){
        if(saved.current&&saved.current===JSON.stringify(local.current)){saved.current=JSON.stringify(r.layout);onLoad(r.layout);setMessage('Account layout refreshed.');}
        else{setRemote(r);setMessage('Account and browser layouts differ. Choose which to keep.');}
      }
      else{saved.current=r.layout?JSON.stringify(r.layout):'';setMessage('Account sync ready.');}
    }).catch(e=>{if(active&&live.current)setMessage(`${e.message}. Browser edits remain local; retry when online.`);});
    return()=>{active=false;};
  },[enabled,retry]);
  useEffect(()=>{
    const encoded=JSON.stringify(layout);
    if(!enabled||revision===null||remote||encoded===saved.current)return;
    const timer=setTimeout(async()=>{
      if(busy.current)return;
      busy.current=true;
      try{
        assertAccount();
        const r=await putWorkspaceLayout(revision,layout);
        if(!live.current)return;
        saved.current=encoded;setRevision(r.revision);setMessage('Layout synced to your account.');
      }catch(e){
        if(!live.current)return;
        if(e instanceof WorkflowError&&e.status===409){
          try{assertAccount();const r=await getWorkspaceLayout();if(live.current){setRemote(r);setMessage('Another device changed the layout. Choose which to keep.');}}
          catch{if(live.current)setMessage('Conflict detected; reconnect and retry before saving.');}
        }else setMessage('Sync unavailable. Browser edits are retained; retry when online.');
      }finally{busy.current=false;}
    },500);
    return()=>clearTimeout(timer);
  },[enabled,revision,remote,layout]);
  return <div style={{fontSize:12}}>
    <label><input type="checkbox" checked={enabled} onChange={e=>{
      const on=e.target.checked;setEnabled(on);setRevision(null);setRemote(null);saved.current='';
      try{localStorage.setItem(`${storageKey}:cloud`,on?'1':'0');}catch{}
    }}/> Sync layout across my devices</label>
    {enabled&&<><p role="status">{message}</p><button type="button" onClick={()=>setRetry(n=>n+1)}>Retry / check account layout</button></>}
    {enabled&&remote&&<div role="alert">
      <p>Sync is paused. Your browser layout remains available for export.</p>
      <button type="button" onClick={()=>{if(remote.layout){saved.current=JSON.stringify(remote.layout);onLoad(remote.layout);}setRevision(remote.revision);setRemote(null);}}>Use account layout</button>
      <button type="button" onClick={()=>{saved.current='';setRevision(remote.revision);setRemote(null);}}>Replace account layout with this browser</button>
    </div>}
  </div>;
}
