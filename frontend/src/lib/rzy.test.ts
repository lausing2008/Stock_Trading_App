import { expect, it } from 'vitest';
import { rzyLinePoints, rzyPriceGridMatches, rzyTime, type RzyStructure } from './rzy';
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
