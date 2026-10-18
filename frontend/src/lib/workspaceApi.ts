import { parseWorkspaceLayout, type WorkspaceLayout } from './stockWorkspaceLayout';
const base = process.env.NEXT_PUBLIC_API_URL ?? '/api';
export class WorkflowError extends Error { constructor(message:string, public status:number) { super(message); } }
export async function workflowRequest<T>(path:string, init:RequestInit = {}):Promise<T> {
  const token = localStorage.getItem('stockai_jwt')?.trim();
  if (!token) throw new WorkflowError('Sign in to use account features',401);
  const ctl=new AbortController();const timer=setTimeout(()=>ctl.abort(),20000);
  let r:Response;
  try { r = await fetch(`${base}${path}`,{...init,signal:init.signal??ctl.signal,headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`, ...init.headers}}); }
  finally {clearTimeout(timer);}
  if (!r.ok) { const e=await r.json().catch(()=>({}));throw new WorkflowError(e.detail ?? 'Account request failed',r.status); }
  return r.json();
}
export type CloudLayout={revision:number;layout:WorkspaceLayout|null};
export async function getWorkspaceLayout():Promise<CloudLayout> {
  const r=await workflowRequest<CloudLayout>('/workspace/layout');
  return {...r,layout:r.layout ? parseWorkspaceLayout(r.layout):null};
}
export function putWorkspaceLayout(revision:number,layout:WorkspaceLayout) {
  return workflowRequest<CloudLayout>('/workspace/layout',{method:'PUT',body:JSON.stringify({revision,layout:{version:1,...layout}})});
}
