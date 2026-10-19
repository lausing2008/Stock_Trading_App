"""Actual indicator endpoint with only the price-loading boundary replaced."""
import pathlib,subprocess,sys

def test_five_and_ten_periods_are_available_and_do_not_repaint():
    root=pathlib.Path(__file__).resolve().parents[3]
    script='''
import sys,types
sys.path.insert(0,ROOT+'/services/technical-analysis')
import pandas as pd
mock=types.ModuleType('db');mock.Price=mock.Stock=mock.TimeFrame=object;mock.get_session=lambda:None
sys.modules['db']=mock
from src.api import routes
frame=pd.DataFrame({'ts':pd.date_range('2026-09-01',periods=40),'open':range(1,41),'high':range(2,42),'low':[x-.5 for x in range(1,41)],'close':range(1,41),'volume':[1000]*40})
routes._load_prices=lambda *args:frame
out=routes.get_indicators('TEST',timeframe='1d',days=400,session=None)
assert out.values['sma_5'][:4]==[None]*4 and out.values['sma_5'][4]==3 and out.values['sma_5'][-1]==38
assert out.values['sma_10'][:9]==[None]*9 and out.values['sma_10'][9]==5.5 and out.values['sma_10'][-1]==35.5
old=out.values['sma_5'][:20]
frame.loc[39,'close']=500
assert routes.get_indicators('TEST',timeframe='1d',days=400,session=None).values['sma_5'][:20]==old
assert 'sma_20' in out.values and 'sma_200' in out.values
'''
    run=subprocess.run([sys.executable,'-c','ROOT='+repr(str(root))+'\n'+script],capture_output=True,text=True,timeout=30)
    assert run.returncode==0,run.stderr[-4000:]
