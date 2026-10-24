import json,re,subprocess,urllib.request
expected=subprocess.check_output(['docker','exec','stockai-frontend-1','cat','/app/.next/BUILD_ID'],text=True).strip()
checks=[]
for path,needles in [('/stock/TSM',['Compare candidate costs, risk and liquidity','Sort returned alternatives','Shown spread (rounded)','Original plan']),('/improvements',['OPTIONS-CANDIDATE-COMPARISON-TABLE','Compare returned option candidates by risk, cost and liquidity evidence'])]:
 html=urllib.request.urlopen('https://lausing.com'+path,timeout=30).read().decode()
 data=json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',html).group(1))
 assert data['buildId']==expected
 sources=re.findall(r'<script[^>]+src="([^\"]+)"',html)
 chunks='\n'.join(urllib.request.urlopen('https://lausing.com'+src,timeout=30).read().decode() for src in sources if src.startswith('/_next/'))
 assert all(n in chunks for n in needles),[n for n in needles if n not in chunks]
 checks.append({'path':path,'build_id':expected,'markers_verified':True})
print('SERVED_BUILD='+json.dumps(checks))
