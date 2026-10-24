import asyncio,json,sys
from pathlib import Path
from playwright.async_api import async_playwright
phase=sys.argv[1]
async def main():
 results=[]
 async with async_playwright() as p:
  browser=await p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
  for width in (1449,1024,390):
   page=await browser.new_page(viewport={'width':width,'height':1100},device_scale_factor=1)
   await page.goto(Path('/tmp/options-layout-'+phase+'.html').as_uri())
   result=await page.evaluate('''() => {
    const table=[...document.querySelectorAll('table')].find(t=>t.textContent.includes('Executed (ET)'));
    const card=table.closest('section') || table.parentElement;
    const grid=card.parentElement, box=card.getBoundingClientRect(), tab=table.getBoundingClientRect();
    const scroll=table.closest('.market-pressure-table-scroll');
    return {viewport:innerWidth,documentWidth:document.documentElement.scrollWidth,cardWidth:box.width,gridWidth:grid.getBoundingClientRect().width,
      tableWidth:tab.width,tableEscapesCard:tab.right>box.right+1,cardScrollWidth:card.scrollWidth,cardClientWidth:card.clientWidth,
      internalScroll:scroll?{client:scroll.clientWidth,content:scroll.scrollWidth}:null};
   }''')
   await page.screenshot(path=f'/tmp/options-layout-{phase}-{width}.png',full_page=True)
   if phase=='after':
    assert result['documentWidth']<=width+1,result
    assert abs(result['cardWidth']-result['gridWidth'])<=1,result
    if width>=1024:assert not result['tableEscapesCard'],result
    else:assert result['internalScroll'] and result['internalScroll']['content']>result['internalScroll']['client'],result
   if phase=='after' and width==390:
    scroll=page.get_by_role('region',name='Off-exchange prints table',exact=True)
    await scroll.focus()
    for _ in range(8): await page.keyboard.press('ArrowRight')
    await page.wait_for_timeout(300)
    result['keyboardScrollLeft']=await scroll.evaluate('(element)=>element.scrollLeft')
    assert result['keyboardScrollLeft']>0,result
    await scroll.evaluate('(element)=>element.scrollLeft=element.scrollWidth')
    await page.screenshot(path='/tmp/options-layout-after-390-scrolled.png',full_page=True)
   results.append(result)
   await page.close()
  await browser.close()
 Path('/tmp/options-layout-'+phase+'.json').write_text(json.dumps(results,indent=2)+'\n')
 print(json.dumps(results))
asyncio.run(main())
