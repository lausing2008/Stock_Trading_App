import { expect, it } from 'vitest';
import { RZY_EVENT_LABEL, rzyLinePoints, rzyPivotLabel, rzyPriceGridMatches, rzyStructuresToDraw, rzyTime, type RzyEvent, type RzyStructure } from './rzy';
const times=['2026-10-16T13:30:00Z','2026-10-16T13:35:00Z','2026-10-16T13:40:00Z','2026-10-16T13:45:00Z'];
const s: RzyStructure = { id:'fixture',direction:'long',state:'ARMED',anchors:[{index:100,price:10,pivot_time:times[0],confirmed_at:times[3],kind:'low'},{index:101,price:15,pivot_time:times[1],confirmed_at:times[3],kind:'high'},{index:102,price:12,pivot_time:times[2],confirmed_at:times[3],kind:'low'}],extreme:{time:times[1],price:15},D:4,target:19,runner:23,sequence_number:1,sequence_confidence:'unknown' };
it('draws the frozen line with trading-bar indices and refuses a missing candle grid',()=>{
 expect(rzyLinePoints(s,times).map(p=>p.value)).toEqual([10,11,12,13]);
 expect(rzyLinePoints(s,[times[0],times[2],times[3]])).toEqual([]);
 expect(rzyLinePoints(s,[times[1],times[2]])).toEqual([]);
});
it('treats naive stored timestamps as UTC independently of browser timezone',()=>{
 expect(rzyTime('2026-10-16T13:30:00')).toBe(rzyTime(times[0]));
 expect(rzyTime('2026-10-16T09:30:00-04:00')).toBe(rzyTime(times[0]));
 expect(rzyTime('2026-10-16')).toBe(Date.parse('2026-10-16T00:00:00Z'));
});
it('withholds an overlay when raw/adjusted OHLC differ, including wick-only differences',()=>{
 const grid=[{time:times[0],open:10,high:12,low:9,close:11}];
 const chart=[{ts:'2026-10-16T13:30:00',open:10,high:12,low:9,close:11}];
 expect(rzyPriceGridMatches(grid,chart)).toBe(true);
 expect(rzyPriceGridMatches(grid,[{...chart[0],high:24}])).toBe(false);
 expect(rzyPriceGridMatches(grid,[{...chart[0],close:22}])).toBe(false);
 expect(rzyPriceGridMatches([],chart)).toBe(false);
});
it('tolerates storage-noise restatements but not a basis change',()=>{
 const grid=[{time:times[0],open:100,high:102,low:99,close:101}];
 const noisy=[{ts:'2026-10-16T13:30:00',open:100.0001,high:102,low:99,close:100.99995}];
 expect(rzyPriceGridMatches(grid,noisy)).toBe(true);
 expect(rzyPriceGridMatches(grid,[{...noisy[0],close:101*0.995}])).toBe(false);
});

it('clips an ended structure at its ending bar instead of extending it to the right edge',()=>{
 expect(rzyLinePoints(s,times,times[2]).map(p=>p.value)).toEqual([10,11,12]);
 expect(rzyLinePoints(s,times,null)).toHaveLength(4);
});
it('draws live structures and only the latest armed-then-ended ones with a known end',()=>{
 const st=(id:string,state:string)=>({...s,id,state});
 const ev=(structure_id:string,event_type:string,t=times[3]):RzyEvent=>({event_id:structure_id+event_type,structure_id,event_type,position_action:'WATCH_ONLY',signal_time:t,bar_time:t});
 const structures=[st('old1','INVALIDATED'),st('old2','EXPIRED'),st('old3','COMPLETED'),st('never','REJECTED'),st('noend','INVALIDATED'),st('live','ARMED')];
 const events=[ev('old1','ARMED'),ev('old1','INVALIDATED'),ev('old2','ARMED'),ev('old2','EXPIRED'),ev('old3','ARMED'),ev('old3','COMPLETED',times[2]),ev('never','REJECTED'),ev('noend','ARMED'),ev('live','ARMED')];
 const drawn=rzyStructuresToDraw(structures,events);
 expect(drawn.map(d=>[d.structure.id,d.live,d.until])).toEqual([['old2',false,times[3]],['old3',false,times[2]],['live',true,null]]);
});

it('keeps a cancelled paper entry and its trigger as visible research history (RZA-08)',()=>{
 const st={...s,id:'c',state:'REJECTED'};
 const ev=(event_type:string,t:string):RzyEvent=>({event_id:event_type,structure_id:'c',event_type,position_action:'WATCH_ONLY',signal_time:t,bar_time:t});
 const drawn=rzyStructuresToDraw([st],[ev('ARMED',times[1]),ev('TRIGGERED',times[2]),ev('ENTRY_CANCELLED',times[3])]);
 expect(drawn.map(d=>[d.structure.id,d.live,d.until])).toEqual([['c',false,times[3]]]);
 expect(RZY_EVENT_LABEL.ENTRY_CANCELLED).toBe('no fill');
});
it('labels pivots with their confirmation time',()=>{
 expect(rzyPivotLabel('A','2026-10-19T16:05:00+00:00',true)).toBe('A ✓16:05');
 expect(rzyPivotLabel('C','2026-10-19T00:00:00+00:00',false)).toBe('C ✓10-19');
});
