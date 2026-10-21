#!/usr/bin/env python3
"""Replay an explicit completed-bar JSON/CSV archive, with costs and chronological comparisons."""
import argparse,csv,json,sys
from pathlib import Path
sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'services/market-data'),str(Path(__file__).resolve().parents[1]/'shared')]
from src.services.little_rzy import Config
from src.services.rzy_backtest import compare


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input',type=Path);p.add_argument('--symbol',required=True);p.add_argument('--timeframe',choices=['5m','15m','1h','1d'],required=True)
    p.add_argument('--config',type=Path);p.add_argument('--output',type=Path,required=True);p.add_argument('--folds',type=int,default=3)
    args=p.parse_args()
    if args.input.suffix.lower()=='.json': bars=json.loads(args.input.read_text())
    else:
        with args.input.open() as f: bars=list(csv.DictReader(f))
        for b in bars:
            for key in ('open','high','low','close','volume','borrow_rate_annual'):
                if b.get(key) not in (None,''): b[key]=float(b[key])
                elif key=='borrow_rate_annual': b[key]=None
            for key in ('complete','execution_eligible','shortable'): b[key]=str(b.get(key,'')).lower()=='true'
    cfg=Config(**(json.loads(args.config.read_text()) if args.config else {}))
    report=compare(args.symbol,args.timeframe,bars,cfg,args.folds)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print('Wrote',args.output,'— inspect sample counts, cancellations, uncertainty and unresolved trades before interpreting results.')

if __name__=='__main__': main()
