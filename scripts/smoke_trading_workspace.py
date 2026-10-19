import asyncio,json,base64,math
from urllib.parse import urlsplit
from datetime import datetime,timedelta
from pathlib import Path
from playwright.async_api import async_playwright
prices=[]
for i in range(90):
 c=140+i*.12+math.sin(i/5)*3
 prices.append(dict(ts=(datetime(2026,6,1)+timedelta(days=i)).isoformat(),open=c-1,high=c+2,low=c-2,close=c,volume=1000000+i*1000))
setup=dict(id='pullback',key='setup.pullback',title='Pullback entry',state='conditional',entry=149,stop=145,targets=[dict(price=157,label='2R objective',reward_risk=2),dict(price=161,label='Stretch objective',reward_risk=3)],risk_per_share=4,risk_budget=400,shares=100,shares_basis='risk_budget',capital=14900,cash_at_stop=-400,trigger='If a daily close holds above 149, consider entry after confirmation.',invalidation='If a daily close breaks 145, exit or reassess the setup.',earnings='Check the next earnings date before entering.',risk_note='Sizing does not check buying power.',reasons=[],recovery_levels=[],evidence_ids=[])
plan=dict(symbol='AAPL',packet_version=7,packet_fingerprint='preview-fixture',evidence={},limitations=[],plan=dict(kind='entry',currency='USD',price_as_of='2026-10-16T20:00:00',unsupported=False,position=None,position_refusals=[],unavailable={},parameters=dict(note='Fixture evidence for design preview.'),groups=dict(rules=[],calls=[],records=[]),levels=[dict(key='entry',price=149,label='Entry',role='support',state='OK',evidence_ids=[])],setups=[setup,dict(setup,id='breakout',title='Breakout entry',entry=153,stop=149,targets=[dict(price=161,label='2R objective',reward_risk=2),dict(price=165,label='3R objective',reward_risk=3)])]))
overview=dict(price=dict(symbol='AAPL',name='Apple · layout preview with sample data',market='US',exchange='NASDAQ',sector='Technology',currency='USD'),prices=prices,indicators=None,patterns=None,levels=dict(support_resistance=[dict(price=149,kind='support',strength=3),dict(price=157,kind='resistance',strength=2)],fibonacci={}),signal=dict(signal='HOLD',confidence=.57,bullish_probability=.57,reasons={},ts='2026-10-16T20:00:00',horizon='SWING'),ranking=dict(score=62,technical=60,momentum=65,value=55,growth=70,volatility=60,fair_price=160),fundamentals=None)
async def main():
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
  context=await browser.new_context(viewport=dict(width=1440,height=1100),device_scale_factor=1)
  token='x.'+base64.b64encode(json.dumps(dict(sub='layout-preview',role='admin',tier='advanced',exp=4102444800)).encode()).decode()+'.x'
  await context.add_init_script('localStorage.setItem("stockai_jwt", '+json.dumps(token)+');')
  async def route(r):
   path=urlsplit(r.request.url).path.removeprefix('/api').lstrip('/')
   if 'overview' in r.request.url: print('OVERVIEW_REQUEST',r.request.url,flush=True)
   body=None
   if path=='aggregate/overview/AAPL': body=overview
   elif path=='copilot/plan/AAPL': body=plan
   elif path=='stocks/latest_prices': body=[dict(symbol='AAPL',price=151.2,change_pct=1.5,prev_close=149)]
   elif path=='stocks/fear_greed': body=dict(score=59.5,rating='Greed',source='CNN',updated_at='2026-10-16T20:00:00',previous_close=58,previous_1_week=52,previous_1_month=54,previous_1_year=48)
   elif path.startswith('news/') or 'watchlist' in path or path=='board' or path=='notifications': body=[]
   if body is None: await r.fulfill(status=503,content_type='application/json',body='{"detail":"Not loaded in design fixture"}')
   else: await r.fulfill(status=200,content_type='application/json',body=json.dumps(body))
  await context.route('**/api/**',route)
  page=await context.new_page()
  errors=[]
  page.on('pageerror',lambda error:(errors.append(str(error)),print('BROWSER_ERROR',str(error),flush=True)))
  await page.goto('http://127.0.0.1:3018/stock/AAPL',wait_until='networkidle',timeout=90000)
  await page.get_by_role('button',name='Build plan',exact=True).wait_for()
  await page.get_by_label('risk',exact=True).fill('400')
  await page.get_by_role('button',name='Build plan',exact=True).click()
  await page.get_by_label('Pullback entry',exact=True).wait_for()
  chart=page.get_by_role('region',name='Price chart widget',exact=True)
  dock=page.get_by_role('region',name='Game Plan widget',exact=True)
  width=await page.get_by_role('region',name='Stock workspace',exact=True).evaluate('(e)=>e.clientWidth')
  a,b=await chart.bounding_box(),await dock.bounding_box()
  assert abs(a['width']-width)<2 and abs(b['width']-width)<2,(a,b,width)
  assert abs(b['y']-(a['y']+a['height']))<3,(a,b)
  await page.get_by_label('Setup to save',exact=True).select_option('breakout')
  await page.get_by_label('Breakout entry',exact=True).wait_for()
  await page.get_by_role('button',name='Highlight TP1 on chart',exact=True).click()
  await page.get_by_text('Chart highlight: TP1',exact=False).wait_for()
  assert '161' in await page.locator('.chart-plan-highlight').first.inner_text()
  await page.get_by_role('tab',name='Technicals',exact=True).click()
  assert await page.get_by_role('button',name='Support & resistance',exact=True).is_visible()
  assert not await page.get_by_role('region',name='AI signals widget',exact=True).is_visible()
  await page.get_by_role('tab',name='Company',exact=True).click()
  await page.get_by_role('button',name='Dividends',exact=True).click()
  await page.get_by_text('Dividend data unavailable. Close and reopen to retry.',exact=True).wait_for()
  await page.get_by_role('tab',name='Signals',exact=True).click()
  assert await page.get_by_label('risk',exact=True).input_value()=='400'
  assert await page.get_by_label('Breakout entry',exact=True).is_visible()
  await page.get_by_role('button',name='LLM alternative plan',exact=True).click()
  await page.locator('.chart-plan-highlight').wait_for(state='detached')
  assert await page.get_by_role('button',name='Generate LLM alternative',exact=True).is_visible()
  await page.get_by_role('button',name='Rules-based plan',exact=True).click()
  await page.evaluate('window.scrollTo(0,0)')
  await page.screenshot(path='/tmp/trading-workspace-desktop.png',full_page=True)
  await page.get_by_role('button',name='Focus mode',exact=True).click()
  assert await page.evaluate('document.body.style.overflow')=='hidden'
  assert not await page.get_by_role('tab',name='Signals',exact=True).is_visible()
  await page.screenshot(path='/tmp/trading-workspace-focus.png')
  await page.keyboard.press('Escape')
  assert await page.get_by_role('tab',name='Signals',exact=True).is_visible()
  assert await page.evaluate('document.body.style.overflow')==''
  await page.set_viewport_size(dict(width=390,height=844))
  await page.evaluate('window.scrollTo(0,0)')
  await page.screenshot(path='/tmp/trading-workspace-mobile.png',full_page=True)
  whole=await page.evaluate('({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
  assert whole['scroll']<=whole['width']+1,whole
  assert not errors,errors
  print('TRADING_BROWSER_PASSED full-width joined chart/plan, scenario focus, tabs, input retention, Focus/Escape, phone width; no runtime errors')
  await browser.close()
if __name__ == '__main__':
 asyncio.run(main())
