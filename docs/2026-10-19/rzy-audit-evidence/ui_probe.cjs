const fs=require('fs'), vm=require('vm'), path=require('path');
const root=path.resolve(__dirname,'../../..'), ts=require(path.join(root,'frontend/node_modules/typescript'));
const src=fs.readFileSync(path.join(root,'frontend/src/lib/rzy.ts'),'utf8');
const m={exports:{}};vm.runInNewContext(ts.transpileModule(src,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,{module:m,exports:m.exports});
const ev=(event_type,time)=>({event_type,structure_id:'sample',signal_time:time,bar_time:time});
const events=[ev('ARMED','2026-10-19T14:00:00Z'),ev('TRIGGERED','2026-10-19T14:05:00Z'),ev('ENTRY_CANCELLED','2026-10-19T14:10:00Z')];
const after=m.exports.rzyStructuresToDraw([{id:'sample',state:'REJECTED'}],events);
console.log(JSON.stringify({triggered_structures_drawn:m.exports.rzyStructuresToDraw([{id:'sample',state:'TRIGGERED'}],events.slice(0,2)).length,after_standing_cancellation_structures_drawn:after.length,prior_trigger_marker_allowed:after.some(d=>d.structure.id==='sample'),cancellation_has_label:Boolean(m.exports.RZY_EVENT_LABEL.ENTRY_CANCELLED)},null,2));
