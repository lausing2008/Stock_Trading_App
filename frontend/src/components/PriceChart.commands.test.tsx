// @vitest-environment jsdom
import React,{createRef} from 'react';
import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import type {ChartController} from '@/lib/chartCommands';
const mocks=vi.hoisted(()=>({fit:vi.fn(),set:vi.fn(),get:vi.fn(()=>({from:10,to:30})),remove:vi.fn(),fetch:vi.fn(),lineOpts:[] as Record<string,unknown>[],chartOpts:[] as Record<string,unknown>[],crosshair:[] as ((p:unknown)=>void)[],series:null as unknown,rzy:vi.fn((..._args:unknown[]):Promise<unknown>=>Promise.resolve({strategy_version:'V1',status:'research_only',structures:[],events:[],paper_positions:[],price_grid:[]}))}));
vi.mock('lightweight-charts',()=>({LineStyle:{Solid:0,Dashed:2,Dotted:1},createChart:(_el:unknown,o:Record<string,unknown>)=>{mocks.chartOpts.push(o);
 const series={setData:vi.fn(),setMarkers:vi.fn(),createPriceLine:vi.fn(),priceToCoordinate:()=>20,coordinateToPrice:()=>100,attachPrimitive:vi.fn(),applyOptions:vi.fn()};
 mocks.series=series;
 return {addCandlestickSeries:()=>series,addLineSeries:(o:Record<string,unknown>)=>{mocks.lineOpts.push(o);return series;},addHistogramSeries:()=>series,remove:mocks.remove,
  applyOptions:vi.fn(),subscribeCrosshairMove:(f:(p:unknown)=>void)=>mocks.crosshair.push(f),subscribeClick:vi.fn(),unsubscribeClick:vi.fn(),priceScale:()=>({applyOptions:vi.fn()}),
  timeScale:()=>({fitContent:mocks.fit,getVisibleLogicalRange:mocks.get,setVisibleLogicalRange:mocks.set,subscribeVisibleTimeRangeChange:vi.fn(),subscribeVisibleLogicalRangeChange:vi.fn(),timeToCoordinate:()=>10})};
}}));
vi.mock('@/lib/api',()=>({api:{getPrices:(...args:unknown[])=>mocks.fetch(...args),rzyReview:(...args:unknown[])=>mocks.rzy(...args)}}));
import PriceChart from './PriceChart';
const prices=Array.from({length:70},(_,i)=>({ts:new Date(Date.UTC(2026,6,i+1)).toISOString(),open:100,high:101,low:99,close:100,volume:1000}));
beforeEach(()=>{vi.clearAllMocks();mocks.lineOpts.length=0;mocks.chartOpts.length=0;mocks.crosshair.length=0;localStorage.clear();vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it('acknowledges indicator changes after rendering and preserves the current zoom',async()=>{
 const ref=createRef<ChartController>();render(<PriceChart symbol="TEST" prices={prices} commandRef={ref}/>);
 const initial=mocks.fit.mock.calls.length;
 let applied!:ReturnType<ChartController['applyCommand']>;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'indicator',indicator:'RSI',enabled:true},'TEST');});
 expect((await applied).indicators.RSI).toBe(true);expect(mocks.fit.mock.calls.length).toBe(initial);expect(mocks.set).toHaveBeenCalledWith({from:10,to:30});
 await act(async()=>{applied=ref.current!.applyCommand({kind:'undo'},'TEST');});expect((await applied).indicators.RSI).toBe(false);
 await expect(ref.current!.applyCommand({kind:'range',range:'1Y'},'OTHER')).rejects.toThrow('symbol');
});
it('fits an explicitly changed range and rejects unavailable data rather than relabeling daily bars',async()=>{
 const ref=createRef<ChartController>();render(<PriceChart symbol="TEST" prices={prices} commandRef={ref}/>);
 let applied!:ReturnType<ChartController['applyCommand']>;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'range',range:'1Y'},'TEST');});expect((await applied).range).toBe('1Y');
 expect(mocks.fit.mock.calls.length).toBeGreaterThan(1);
 await expect(ref.current!.applyCommand({kind:'timeframe',timeframe:'15m'},'TEST')).rejects.toThrow('Unsupported');
});

it('acknowledges undo even after manual controls already restored its state',async()=>{
 const ref=createRef<ChartController>();render(<PriceChart symbol="TEST" prices={prices} commandRef={ref}/>);
 let applied!:ReturnType<ChartController['applyCommand']>;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'range',range:'1Y'},'TEST');});await applied;
 fireEvent.click(screen.getByRole('button',{name:'3M'}));
 await act(async()=>{applied=ref.current!.applyCommand({kind:'undo'},'TEST');});
 expect((await applied).range).toBe('3M');
});
it('rejects pending commands on symbol navigation and ignores the old intraday response',async()=>{
 let finish!:(v:typeof prices)=>void;mocks.fetch.mockReturnValue(new Promise(r=>finish=r));
 const ref=createRef<ChartController>();const view=render(<PriceChart symbol="TEST" prices={prices} commandRef={ref}/>);
 let result!:Promise<unknown>;
 await act(async()=>{result=ref.current!.applyCommand({kind:'range',range:'5m'},'TEST').catch(e=>e);});
 view.rerender(<PriceChart symbol="OTHER" prices={prices} commandRef={ref}/>);
 expect(await result).toBeInstanceOf(Error);
 await act(async()=>finish(prices));
 await expect(ref.current!.applyCommand({kind:'indicator',indicator:'RSI',enabled:true},'TEST')).rejects.toThrow('symbol');
});

it('turns on Little RZY, SMA 5/10 and Bollinger Bands on entering 5m, and undo restores the prior view',async()=>{
 mocks.fetch.mockResolvedValue(prices);
 const ref=createRef<ChartController>();render(<PriceChart symbol="TEST" prices={prices} commandRef={ref}/>);
 let applied!:ReturnType<ChartController['applyCommand']>;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'range',range:'5m'},'TEST');});
 const five=await applied;
 expect(five.range).toBe('5m');
 expect([five.indicators.RZY,five.indicators.SMA5,five.indicators.SMA10,five.indicators.BB]).toEqual([true,true,true,true]);
 expect(mocks.rzy).toHaveBeenCalledWith('TEST','5m');
 // Still individually toggleable while staying on 5m.
 await act(async()=>{applied=ref.current!.applyCommand({kind:'indicator',indicator:'BB',enabled:false},'TEST');});
 expect((await applied).indicators.BB).toBe(false);
 await act(async()=>{applied=ref.current!.applyCommand({kind:'undo'},'TEST');});await applied;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'undo'},'TEST');});
 const back=await applied;
 expect(back.range).toBe('3M');expect([back.indicators.RZY,back.indicators.SMA5,back.indicators.SMA10,back.indicators.BB]).toEqual([false,false,false,false]);
});

it('undo back into 5m restores that view\'s own indicators instead of re-applying the defaults',async()=>{
 mocks.fetch.mockResolvedValue(prices);
 const ref=createRef<ChartController>();render(<PriceChart symbol="TEST" prices={prices} commandRef={ref}/>);
 let applied!:ReturnType<ChartController['applyCommand']>;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'range',range:'5m'},'TEST');});await applied;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'indicator',indicator:'BB',enabled:false},'TEST');});await applied;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'range',range:'1Y'},'TEST');});await applied;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'undo'},'TEST');});
 const restored=await applied;
 expect(restored.range).toBe('5m');expect(restored.indicators.BB).toBe(false);expect(restored.indicators.RZY).toBe(true);
});

it('keeps RZY geometry from stretching the price axis and shows RZY values under the cursor',async()=>{
 const grid=prices.map(p=>({time:p.ts,open:p.open,high:p.high,low:p.low,close:p.close,middle_band:100,upper_band:101,lower_band:99}));
 const anchors=[{index:40,price:101,pivot_time:prices[40].ts,confirmed_at:prices[47].ts,kind:'high'},{index:45,price:99,pivot_time:prices[45].ts,confirmed_at:prices[52].ts,kind:'low'},{index:50,price:100.5,pivot_time:prices[50].ts,confirmed_at:prices[57].ts,kind:'high'}];
 // A steep short line that would reach far below the candles by the right edge.
 const live={id:'live',direction:'short',state:'ARMED',anchors:anchors.map((a,i)=>i===2?{...a,price:60}:a),extreme:{time:prices[45].ts,price:99,index:45},D:2,target:97,runner:95,stop:102,sequence_number:1,sequence_confidence:'known'};
 mocks.rzy.mockResolvedValue({strategy_version:'V1',status:'research_only',structures:[live],events:[{event_id:'e',event_type:'ARMED',structure_id:'live',position_action:'WATCH_ONLY',signal_time:prices[57].ts}],paper_positions:[],price_grid:grid});
 const ref=createRef<ChartController>();render(<PriceChart symbol="TEST" prices={prices} timeframe="1d" commandRef={ref}/>);
 let applied!:ReturnType<ChartController['applyCommand']>;
 await act(async()=>{applied=ref.current!.applyCommand({kind:'indicator',indicator:'RZY',enabled:true},'TEST');});await applied;
 await act(async()=>{await new Promise(r=>setTimeout(r,0));});
 const structureLine=mocks.lineOpts.find(o=>o.color==='#fb7185' && o.lineWidth===2);
 expect(structureLine).toBeTruthy();
 expect((structureLine!.autoscaleInfoProvider as ()=>unknown)()).toBeNull();
 await act(async()=>{mocks.crosshair.forEach(f=>f({time:1,seriesData:new Map([[mocks.series,{value:100}]])}));});
 expect(screen.getByText('RZY bands = 100.00 / 100.00 / 100.00')).toBeTruthy();
 expect(screen.getByText('RZY short line = 100.00')).toBeTruthy();
});

it('keeps the visible dates when the chart container is resized, so a narrow first layout cannot squeeze the candles',async()=>{
 render(<PriceChart symbol="TEST" prices={prices}/>);
 await act(async()=>{await new Promise(r=>setTimeout(r,0));});
 expect(mocks.chartOpts.length).toBeGreaterThan(0);
 for(const o of mocks.chartOpts) expect((o.timeScale as {lockVisibleTimeRangeOnResize?:boolean}).lockVisibleTimeRangeOnResize).toBe(true);
});
