import React,{useState} from 'react';
import {parseChartCommand,type ChartCommand,type ChartSnapshot} from '@/lib/chartCommands';
export default function ChartCommandBar({symbol,run}:{symbol:string;run:(c:ChartCommand,s:string)=>Promise<ChartSnapshot>}){
  const [text,setText]=useState(''),[status,setStatus]=useState(''),[busy,setBusy]=useState(false);
  async function apply(command:ChartCommand|null){if(!command){setStatus('Supported examples: show RSI, hide MACD, show 1Y, set 5m, undo chart.');return;}setBusy(true);setStatus('Applying…');try{const s=await run(command,symbol);setStatus(`Chart acknowledged ${s.symbol}: ${s.range}.`);setText('');}catch(e){setStatus(e instanceof Error?e.message:'Chart unavailable');}finally{setBusy(false);}}
  return <form onSubmit={e=>{e.preventDefault();if(!busy)void apply(parseChartCommand(text));}} style={{display:'flex',gap:8,flexWrap:'wrap',padding:8}}>
    <label>Chart command <input value={text} onChange={e=>setText(e.target.value)} placeholder="show RSI / show 1Y"/></label>
    <button disabled={busy||!text.trim()}>Apply to chart</button><button type="button" disabled={busy} onClick={()=>apply({kind:'undo'})}>Undo chart command</button>
    <span role="status">{status}</span>
  </form>;
}
