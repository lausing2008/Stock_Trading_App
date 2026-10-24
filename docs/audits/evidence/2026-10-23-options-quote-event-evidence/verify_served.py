import json,re,subprocess,urllib.request
expected=subprocess.check_output(['docker','exec','stockai-frontend-1','cat','/app/.next/BUILD_ID'],text=True).strip()
checks=[]
for path,needles in [('/stock/TSM',['Quote and event evidence','Calendar retrieved over 24 hours ago','Require a current calendar record with reported next earnings after expiration','original preset contracts unavailable']),('/improvements',['OPTIONS-QUOTE-EVENT-EVIDENCE','Quote provenance and reported-event timing for option comparisons'])]:
 html=urllib.request.urlopen('https://lausing.com'+path,timeout=30).read().decode()
 data=json.loads(re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',html).group(1))
 assert data['buildId']==expected
 sources=re.findall(r'<script[^>]+src="([^\"]+)"',html)
 chunks='\n'.join(urllib.request.urlopen('https://lausing.com'+src,timeout=30).read().decode() for src in sources if src.startswith('/_next/'))
 assert all(n in chunks for n in needles),[n for n in needles if n not in chunks]
 checks.append({'path':path,'build_id':expected,'markers_verified':True})
print('SERVED_BUILD='+json.dumps(checks))
