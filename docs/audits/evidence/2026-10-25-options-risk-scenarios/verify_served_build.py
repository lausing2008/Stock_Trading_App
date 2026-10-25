import json,re,subprocess,urllib.request
expected=subprocess.check_output(['docker','exec','stockai-frontend-1','cat','/app/.next/BUILD_ID'],text=True).strip()
checks=[]
for path,needles in [('/stock/MU', ['Worst case at stock $0:', 'Max stock + call loss', 'Max put / assignment loss', 'Hypothetical stock price at expiry ($)', 'stock-zero worst case may be outside the chart']), ('/improvements', ['OPTIONS-RISK-BASIS-AND-SCENARIOS']), ('/options-dark-pool-tutorial', ['A new capture clears this local scenario input.'])]:
 html=urllib.request.urlopen('https://lausing.com'+path,timeout=30).read().decode()
 data=json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',html).group(1))
 assert data['buildId']==expected
 sources=re.findall(r'<script[^>]+src="([^\"]+)"',html)
 chunks='\n'.join(urllib.request.urlopen('https://lausing.com'+src,timeout=30).read().decode() for src in sources if src.startswith('/_next/'))
 assert all(n in chunks for n in needles),[n for n in needles if n not in chunks]
 checks.append({'path':path,'build_id':expected,'markers_verified':True})
css_sources=re.findall(r'<link[^>]+href="([^\"]+\.css)"',html)
css='\n'.join(urllib.request.urlopen('https://lausing.com'+src,timeout=30).read().decode() for src in css_sources if src.startswith('/_next/'))
assert all(n in css for n in ('.market-pressure-grid','.market-pressure-wide','.market-pressure-prints-table','720px','.options-tutorial')), css_sources
print('SERVED_BUILD='+json.dumps({'pages': checks, 'responsive_styles_verified':True}))
