"""S&P 500 membership changes (Wikipedia, "Selected changes" table): date, added, removed."""
import io, json, sys, urllib.request
import pandas as pd
html = urllib.request.urlopen(urllib.request.Request('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies', headers={'User-Agent': 'Mozilla/5.0 (research script)'}), timeout=30).read().decode()
tables = pd.read_html(io.StringIO(html))
current = sorted(tables[0]['Symbol'].str.replace('.', '-', regex=False))
ch = tables[1]; ch.columns = ['_'.join(str(x) for x in c) if isinstance(c, tuple) else str(c) for c in ch.columns]
dcol = [c for c in ch.columns if c.lower().startswith('effective date') or c.lower().startswith('date')][0]
acol = [c for c in ch.columns if 'added' in c.lower() and 'ticker' in c.lower()][0]
rcol = [c for c in ch.columns if 'removed' in c.lower() and 'ticker' in c.lower()][0]
rows = []
for d, a, r in zip(ch[dcol], ch[acol], ch[rcol]):
    try:
        day = pd.Timestamp(d).date().isoformat()
    except Exception:
        continue
    clean = lambda x: None if not isinstance(x, str) or not x.strip() else x.strip().replace('.', '-')
    rows.append([day, clean(a), clean(r)])
json.dump(dict(current=current, changes=sorted(rows)), open(sys.argv[1], 'w'), indent=0)
print(len(current), 'current;', len(rows), 'changes from', min(r[0] for r in rows), 'to', max(r[0] for r in rows))
