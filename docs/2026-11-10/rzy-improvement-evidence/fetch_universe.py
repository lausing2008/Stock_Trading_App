"""Universe for the independent RZY research data: the platform's stored US symbols plus the
current S&P 500 and S&P 400 constituents (Wikipedia). Current constituents only, so the set is
survivorship-biased; the random-timing twins are drawn from the same symbols and absorb drift."""
import io, json, sys, urllib.request
import pandas as pd
out = sys.argv[1]; platform = json.load(open(sys.argv[2]))
def table(url):
    html = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (research script)'}), timeout=30).read().decode()
    return pd.read_html(io.StringIO(html))[0]
sp500 = sorted(table('https://en.wikipedia.org/wiki/List_of_S%26P_500_companies')['Symbol'].str.replace('.', '-', regex=False))
sp400 = sorted(table('https://en.wikipedia.org/wiki/List_of_S%26P_400_companies')['Symbol'].str.replace('.', '-', regex=False))
json.dump(dict(platform=platform, sp500=sp500, sp400=sp400), open(out, 'w'), indent=0)
print(len(platform), len(sp500), len(sp400), len(set(platform) | set(sp500) | set(sp400)))
