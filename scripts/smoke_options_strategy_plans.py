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
  singles={}
  names={'long_call':'Long Call','protective_put':'Protective Put','covered_call':'Covered Call','cash_secured_put':'Cash-Secured Put'}
  for key,name in names.items():
   sell=key in ('covered_call','cash_secured_put')
   singles[key]=dict(name=name,direction='bullish',net='credit' if sell else 'debit',legs=[dict(action='sell' if sell else 'buy',right='call' if key in ('long_call','covered_call') else 'put',strike=150,price_per_share=2,cost_per_contract=200,expiry='2099-12-20',days_to_expiry=30,spread_pct=10,price_source='quote_mid')],net_per_share=2,net_per_contract=200,max_loss_per_contract=14800 if sell else 200,max_profit_per_contract=200 if sell else None,breakeven=152,breakeven_move_pct=1.33,requires_shares=key in ('covered_call','protective_put'),collateral_per_contract=15000 if sell else 200,what_it_does='Illustrative contract comparison',use_when='When the objective and available capital fit')
  options=dict(symbol='AAPL',available=True,current_price=150,protective_put=None,covered_call=None,chain_as_of=datetime.now().isoformat(),strategy_matrix=dict(singles=singles,combos={},recommendation=dict(primary=None,reason=''),iv_rank=None,iv_regime='unknown'))
  async def route(r):
   path=urlsplit(r.request.url).path.removeprefix('/api').lstrip('/')
   if 'overview' in r.request.url: print('OVERVIEW_REQUEST',r.request.url,flush=True)
   body=None
   if path=='stocks/AAPL/options-game-plan': body=options
   elif path=='aggregate/overview/AAPL': body=overview
   elif path=='copilot/plan/AAPL': body=plan
   elif path=='stocks/latest_prices': body=[dict(symbol='AAPL',price=151.2,change_pct=1.5,prev_close=149)]
   elif path=='stocks/fear_greed': body=dict(score=59.5,rating='Greed',source='CNN',updated_at='2026-10-16T20:00:00',previous_close=58,previous_1_week=52,previous_1_month=54,previous_1_year=48)
   elif path.startswith('news/') or 'watchlist' in path or path=='board' or path=='notifications': body=[]
   if body is None: await r.fulfill(status=503,content_type='application/json',body='{"detail":"Not loaded in design fixture"}')
   else: await r.fulfill(status=200,content_type='application/json',body=json.dumps(body))
  await context.route('**/api/**',route)
  page=await context.new_page()
  page.set_default_timeout(15000)
  errors=[]
  page.on('pageerror',lambda error:(errors.append(str(error)),print('BROWSER_ERROR',str(error),flush=True)))
  await page.goto('http://127.0.0.1:3031/stock/AAPL',wait_until='networkidle',timeout=90000)
  await page.get_by_role('button',name='Options',exact=True).click()
  panel=page.get_by_role('region',name='Four common option strategies',exact=True)
  await panel.wait_for()
  for name in names.values(): assert await panel.get_by_text(name,exact=True).is_visible()
  await panel.get_by_label('Objective',exact=True).select_option('upside')
  await panel.get_by_label('Option premium-loss budget ($)',exact=True).fill('400')
  await panel.get_by_text('Preferred strategy to research: Long Call',exact=True).wait_for()
  await panel.get_by_text('Playbook — before entry, while open & exit',exact=True).first.click()
  assert await panel.get_by_text('Entry status: WAIT',exact=False).is_visible()
  await page.screenshot(path='/tmp/options-strategy-desktop.png',full_page=True)
  await panel.get_by_label('Objective',exact=True).select_option('income')
  await panel.get_by_label('Unencumbered shares',exact=True).fill('99')
  await panel.get_by_label('I accept buying or selling 100 shares at the listed strike if assigned.',exact=True).check()
  assert not await panel.get_by_text('Preferred strategy to research:',exact=False).count()
  await panel.get_by_label('Unencumbered shares',exact=True).fill('100')
  await panel.get_by_text('Preferred strategy to research: Covered Call',exact=True).wait_for()
  await panel.get_by_label('Objective',exact=True).select_option('bearish')
  await panel.get_by_text('Preferred strategy to research: Protective Put',exact=True).wait_for()
  await panel.get_by_label('Unencumbered shares',exact=True).fill('0')
  await panel.get_by_text('Wait / no strategy preference yet',exact=True).wait_for()
  await page.set_viewport_size(dict(width=390,height=844))
  await page.screenshot(path='/tmp/options-strategy-mobile.png',full_page=True)
  width=await page.evaluate('({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth})')
  assert width['scroll']<=width['width']+1,width
  assert not errors,errors
  print('OPTIONS_BROWSER_PASSED four plans, objective/budget preference, coverage boundary, bearish hedge, missing holdings wait, phone width; no runtime errors')
  await browser.close()
asyncio.run(main())
