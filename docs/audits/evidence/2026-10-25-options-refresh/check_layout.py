import asyncio,json
from pathlib import Path
from playwright.async_api import async_playwright
async def main():
 rows=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  for width in (1449,1024,390):
   page=await browser.new_page(viewport={'width':width,'height':1000},device_scale_factor=1)
   await page.goto(Path('/tmp/options-refresh-preview.html').as_uri())
   value=await page.evaluate('''() => ({viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,
     selectWidth:document.querySelector('select').getBoundingClientRect().width,
     captureLabel:document.body.innerText.includes('Latest available provider capture'),
     explicitClockLimit:document.body.innerText.includes('not a verified quote timestamp'),
     explicitScope:document.body.innerText.includes('Scope: all provider expirations')})''')
   assert value['documentWidth']<=width+1 and value['selectWidth']<width,value
   assert value['captureLabel'] and value['explicitClockLimit'] and value['explicitScope'],value
   await page.screenshot(path=f'/tmp/options-refresh-{width}.png',full_page=True)
   rows.append(value);await page.close()
  await browser.close()
 Path('/tmp/options-refresh-browser.json').write_text(json.dumps(rows,indent=2)+'\n')
 print(json.dumps(rows))
asyncio.run(main())
