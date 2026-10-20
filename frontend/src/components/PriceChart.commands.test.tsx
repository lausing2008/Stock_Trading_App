// @vitest-environment jsdom
import React,{createRef} from 'react';
import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import type {ChartController} from '@/lib/chartCommands';
const mocks=vi.hoisted(()=>({fit:vi.fn(),set:vi.fn(),get:vi.fn(()=>({from:10,to:30})),remove:vi.fn(),fetch:vi.fn(),rzy:vi.fn((..._args:unknown[])=>Promise.resolve({strategy_version:'V1',status:'research_only',structures:[],events:[],paper_positions:[],price_grid:[]}))}));
vi.mock('lightweight-charts',()=>({LineStyle:{Solid:0,Dashed:2,Dotted:1},createChart:()=>{
 const series={setData:vi.fn(),setMarkers:vi.fn(),createPriceLine:vi.fn(),priceToCoordinate:()=>20,coordinateToPrice:()=>100,attachPrimitive:vi.fn(),applyOptions:vi.fn()};
 return {addCandlestickSeries:()=>series,addLineSeries:()=>series,addHistogramSeries:()=>series,remove:mocks.remove,
  applyOptions:vi.fn(),subscribeCrosshairMove:vi.fn(),subscribeClick:vi.fn(),unsubscribeClick:vi.fn(),priceScale:()=>({applyOptions:vi.fn()}),
  timeScale:()=>({fitContent:mocks.fit,getVisibleLogicalRange:mocks.get,setVisibleLogicalRange:mocks.set,subscribeVisibleTimeRangeChange:vi.fn(),subscribeVisibleLogicalRangeChange:vi.fn(),timeToCoordinate:()=>10})};
}}));
vi.mock('@/lib/api',()=>({api:{getPrices:(...args:unknown[])=>mocks.fetch(...args),rzyReview:(...args:unknown[])=>mocks.rzy(...args)}}));
import PriceChart from './PriceChart';
const prices=Array.from({length:70},(_,i)=>({ts:new Date(Date.UTC(2026,6,i+1)).toISOString(),open:100,high:101,low:99,close:100,volume:1000}));
beforeEach(()=>{vi.clearAllMocks();localStorage.clear();vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});});
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
