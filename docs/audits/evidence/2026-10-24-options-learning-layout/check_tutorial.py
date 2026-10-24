import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 results=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  for width in (1449,1024,390):
   page=await browser.new_page(viewport={'width':width,'height':1100},device_scale_factor=1)
   await page.goto(Path('/tmp/options-tutorial-preview.html').as_uri())
   await page.get_by_role('link',name='8. Read off-exchange prints',exact=True).click()
   assert page.url.endswith('#dark-pool')
   await page.locator('#practice summary').click()
   metrics=await page.evaluate('''() => ({viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
     tocLinks:document.querySelectorAll('nav[aria-label="Tutorial topics"] a').length,
     tableOpen:document.querySelector('#practice details').open,
     sampleRows:document.querySelectorAll('#practice tbody tr').length})''')
   assert metrics['documentWidth']<=width+1,metrics
   assert metrics['tocLinks']==12 and metrics['sampleRows']==3 and metrics['tableOpen'],metrics
   await page.evaluate('scrollTo(0,0)')
   await page.screenshot(path=f'/tmp/options-tutorial-{width}.png',full_page=True)
   await page.locator('#practice').screenshot(path=f'/tmp/options-tutorial-practice-{width}.png')
   results.append(metrics)
   await page.close()
  await browser.close()
 Path('/tmp/options-tutorial-browser.json').write_text(json.dumps(results,indent=2)+'\n')
 print(json.dumps(results))
asyncio.run(main())
