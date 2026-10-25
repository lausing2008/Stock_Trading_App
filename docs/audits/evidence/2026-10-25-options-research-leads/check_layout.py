import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright


async def main():
    results = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path='/usr/bin/google-chrome', headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage'],
        )
        for width in (1449, 1024, 390):
            page = await browser.new_page(viewport={'width': width, 'height': 1050}, device_scale_factor=1)
            await page.goto(Path('/tmp/options-leads-preview.html').as_uri())
            result = await page.evaluate('''() => ({
              width: innerWidth, pageWidth: document.documentElement.scrollWidth,
              cards: document.querySelectorAll('.option-research-lead').length,
              leads: [...document.querySelectorAll('.option-research-lead-title span')].map(e => e.textContent),
              evidenceCollapsed: [...document.querySelectorAll('details')].filter(e =>
                /Inspect dated signal|Inspect event timing/.test(e.querySelector('summary')?.textContent || '')).every(e => !e.open),
              cardOverflow: [...document.querySelectorAll('.option-research-lead')].some(e => e.scrollWidth > e.clientWidth + 1)
            })''')
            assert result['pageWidth'] <= width + 1, result
            assert result['cards'] == 4 and result['leads'].count('Leading screen fit') == 4, result
            assert not result['cardOverflow'], result
            await page.screenshot(path=f'docs/audits/evidence/2026-10-25-options-research-leads/desk-{width}.png', full_page=True)
            results.append(result)
            await page.close()
        await browser.close()
    output = Path('docs/audits/evidence/2026-10-25-options-research-leads/layout.json')
    output.write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results))


asyncio.run(main())
