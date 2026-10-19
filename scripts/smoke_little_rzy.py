"""Offline browser acceptance: every API response is intercepted; no mail/model/broker calls."""
import asyncio,base64,json,os
from urllib.parse import urlsplit
from playwright.async_api import async_playwright
from smoke_trading_workspace import overview,prices

async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
  context=await browser.new_context(viewport={'width':1440,'height':1100})
  token='x.'+base64.b64encode(json.dumps({'sub':'fixture','role':'admin','tier':'advanced'}).encode()).decode()+'.x'
  await context.add_init_script('localStorage.setItem("stockai_jwt", '+json.dumps(token)+');')
  preferences={'pattern_email':False,'morning_digest':False,'timeframes':['5m','15m','1h']}
  anchor=lambda i,kind:dict(index=i,price=prices[i]['low' if kind=='low' else 'high'],pivot_time=prices[i]['ts'],confirmed_at=prices[i+7]['ts'],kind=kind)
  report=dict(strategy_version='LITTLE_RZY_INSPIRED_V1',status='research_only',evidence=dict(data_as_of=prices[-1]['ts'],delay='unknown'),
   price_grid=[dict(time=b['ts'],**{k:b[k] for k in ['open','high','low','close']},middle_band=b['close'],upper_band=b['close']+6,lower_band=b['close']-6) for b in prices],
   structures=[dict(id='fixture',direction='long',state='ARMED',anchors=[anchor(20,'low'),anchor(30,'high'),anchor(40,'low')],extreme=dict(time=prices[30]['ts'],price=prices[30]['high']),D=8,target=165,runner=173,sequence_number=1,sequence_confidence='unknown')],events=[],paper_positions=[])
  async def route(r):
   path=urlsplit(r.request.url).path.removeprefix('/api').lstrip('/')
   body=None
   if path=='aggregate/overview/AAPL':body=overview
   elif path=='stocks/rzy/AAPL':body=report
   elif path=='stocks/rzy/preferences':
    if r.request.method=='PUT':preferences.update(r.request.post_data_json)
    body=preferences
   elif path=='stocks/rzy/digest':body=dict(candidates=[],exclusions=[dict(symbol='AAPL',timeframe='5m',reason='stale')],note='Fixture preparation')
   elif path=='stocks/rzy/paper':body=dict(positions=[],note='Separate research ledger')
   elif path=='stocks/latest_prices':body=[]
   elif 'watchlist' in path or path in ('board','notifications'):body=[]
   await r.fulfill(status=200 if body is not None else 503,content_type='application/json',body=json.dumps(body if body is not None else {'detail':'fixture unavailable'}))
  await context.route('**/api/**',route)
  page=await context.new_page();errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  await page.goto(os.environ.get('RZY_PREVIEW_URL','http://127.0.0.1:3032')+'/stock/AAPL',wait_until='networkidle',timeout=90000)
  await page.get_by_role('button',name='Indicators',exact=False).first.click()
  await page.get_by_label('SMA 5',exact=True).check()
  await page.get_by_label('SMA 10',exact=True).check()
  await page.get_by_label('Little RZY-inspired',exact=True).check()
  await page.get_by_role('button',name='Indicators',exact=False).first.click()
  await page.get_by_text('Little RZY-inspired: research_only.',exact=False).wait_for()
  assert 'Overlay withheld' not in await page.locator('body').inner_text()
  await page.screenshot(path='/tmp/little-rzy-desktop.png',full_page=True)
  await page.set_viewport_size({'width':390,'height':844})
  assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
  await page.screenshot(path='/tmp/little-rzy-mobile.png',full_page=True)
  assert not errors,errors
  await browser.close();print('PASS: SMA 5/10, RZY overlay grid/status, desktop/mobile, no browser errors; all APIs mocked')

if __name__=='__main__':asyncio.run(main())
