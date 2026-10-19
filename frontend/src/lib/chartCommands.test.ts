import {describe,it,expect} from 'vitest';
import {parseChartCommand} from './chartCommands';
describe('closed chart command grammar',()=>{
 it('only accepts supported UI changes',()=>{
  expect(parseChartCommand('show SMA 5')).toEqual({kind:'indicator',indicator:'SMA5',enabled:true});
  expect(parseChartCommand('add SMA10')).toEqual({kind:'indicator',indicator:'SMA10',enabled:true});
  expect(parseChartCommand('show RZY')).toEqual({kind:'indicator',indicator:'RZY',enabled:true});
  expect(parseChartCommand('show RSI')).toEqual({kind:'indicator',indicator:'RSI',enabled:true});
  expect(parseChartCommand('hide EMA 200')).toEqual({kind:'indicator',indicator:'EMA200',enabled:false});
  expect(parseChartCommand('show 5M')).toEqual({kind:'range',range:'5m'});
  expect(parseChartCommand('show 1Y')).toEqual({kind:'range',range:'1Y'});
  expect(parseChartCommand('set 15m')).toEqual({kind:'timeframe',timeframe:'15m'});
  expect(parseChartCommand('undo chart')).toEqual({kind:'undo'});
  for(const s of ['buy 100 shares','show RSI and save alert','set 2h','show SMA 77','draw support at 100'])expect(parseChartCommand(s)).toBeNull();
 });
});
