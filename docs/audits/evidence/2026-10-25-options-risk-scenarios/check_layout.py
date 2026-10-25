import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 rows=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  for width in (1449,1024,390):
   page=await browser.new_page(viewport={'width':width,'height':1100},device_scale_factor=1)
   await page.goto(Path('/tmp/options-risk-preview.html').as_uri())
   for summary in await page.get_by_text('Why the loss is large — expiry scenarios',exact=True).all():await summary.click()
   for summary in await page.get_by_text('Expiry payoff illustration',exact=True).all():await summary.click()
   metrics=await page.evaluate('''() => ({viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
      scenarios:document.querySelectorAll('table[aria-label$="expiry scenarios"]').length,
      graphs:document.querySelectorAll('svg[aria-label="Payoff at expiry"]').length,
      scrollers:[...document.querySelectorAll('[aria-label$="scenario table"]')].map(e=>({client:e.clientWidth,content:e.scrollWidth})),
      scopedLabels:document.body.textContent.includes('Max stock + call loss')&&document.body.textContent.includes('Max put / assignment loss')})''')
   assert metrics['documentWidth']<=width+1 and metrics['scenarios']==2 and metrics['graphs']==4 and metrics['scopedLabels'],metrics
   region=page.get_by_role('region',name='Covered Call scenario table',exact=True)
   await region.focus()
   for _ in range(8):await page.keyboard.press('ArrowRight')
   await page.wait_for_timeout(300)
   metrics['keyboardScrollLeft']=await region.evaluate('(e)=>e.scrollLeft')
   assert metrics['keyboardScrollLeft']>0,metrics
   await region.evaluate('(e)=>{e.scrollLeft=0}')
   await page.screenshot(path=f'/tmp/options-risk-{width}.png',full_page=True)
   if width==390: await page.get_by_role('region',name='Covered Call expiry risk',exact=True).screenshot(path='/tmp/options-risk-mobile-detail.png')
   rows.append(metrics);await page.close()
  await browser.close()
 Path('/tmp/options-risk-browser.json').write_text(json.dumps(rows,indent=2)+'\n')
 print(json.dumps(rows))
asyncio.run(main())
