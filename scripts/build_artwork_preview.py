"""Export the production embeds/components into an offline interactive preview."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.starter import Starter, StarterView
from utils.artwork import card_artwork_url
from utils.beast_cards import CodexView, STARTER_ORDER, codex_embed, starter_overview_embed
from utils.db import load_beasts


async def export():
    beasts = load_beasts()
    starters = {key: beasts[key] for key in STARTER_ORDER}
    view = StarterView(Starter(None), 42, 'Trainer', starters)
    data = {'starters': {}, 'codex': {}, 'roster': [{'id': key, 'name': beast['name']} for key, beast in sorted(beasts.items(), key=lambda item: item[1]['name'])]}

    def payload(embed, controls):
        return {'embed': embed.to_dict(), 'components': controls.to_components()}

    data['starters']['overview'] = payload(starter_overview_embed(), view)
    for key in STARTER_ORDER:
        view.selected_id = key
        view._published_art = key
        for details in (False, True):
            view.details_mode = details
            view._update_controls()
            data['starters'][key + ('-details' if details else '')] = payload(view.current_embed(), view)
        view.chosen = True
        view._update_controls()
        chosen = Starter(None).build_starter_embed(starters[key])
        chosen.set_image(url=card_artwork_url(key))
        data['starters'][key + '-chosen'] = payload(chosen, view)
        view.chosen = False
    view.stop()

    for key, beast in beasts.items():
        codex = CodexView(42, beast)
        data['codex'][key] = {}
        for tab in ('overview', 'battle', 'lore'):
            codex.tab = tab
            codex._update_buttons()
            data['codex'][key][tab] = payload(codex_embed(beast, tab), codex)
        codex.stop()

    template = (ROOT / 'scripts' / 'artwork-preview-template.html').read_text(encoding='utf-8')
    encoded = json.dumps(data, ensure_ascii=True).replace('<', '\\u003c')
    output = ROOT / 'docs' / 'artwork-preview.html'
    output.write_text(template.replace('/*__PREVIEW_DATA__*/', encoded), encoding='utf-8')
    print(f'Preview: {output}')
    return output


async def capture(path):
    from playwright.async_api import async_playwright
    output = ROOT / 'docs' / 'artwork-preview'
    output.mkdir(exist_ok=True)
    errors = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1200, 'height': 1100}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(path.as_uri())
        await page.locator('img[src]').evaluate_all('(images) => Promise.all(images.map(img => img.decode()))')
        await page.screenshot(path=str(output / 'desktop.png'), full_page=True)
        await page.locator('#starter-panel').screenshot(path=str(output / 'starter-overview.png'))
        await page.locator('#codex-panel').screenshot(path=str(output / 'codex-twine.png'))
        await page.locator('#starter-select').select_option('twine')
        await page.locator('img[src]').evaluate_all('(images) => Promise.all(images.map(img => img.decode()))')
        await page.locator('#starter-panel').screenshot(path=str(output / 'starter-twine.png'))
        await page.get_by_role('button', name='Moves & stats', exact=True).last.click()
        await page.locator('#codex-panel').screenshot(path=str(output / 'codex-battle.png'))
        await page.set_viewport_size({'width': 390, 'height': 1000})
        await page.screenshot(path=str(output / 'mobile.png'), full_page=True)
        overflow = await page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
        await page.get_by_role('button', name='Light theme', exact=True).click()
        await page.screenshot(path=str(output / 'mobile-light.png'), full_page=True)
        await page.get_by_role('button', name='Dark theme', exact=True).click()
        for key in ('prismite', 'gloop', 'barkley', 'twine'):
            await page.locator('#starter-select').select_option(key)
            await page.locator('#starter-controls').get_by_role('button', name='Moves & stats', exact=True).click()
            overflow = overflow or await page.evaluate('document.documentElement.scrollWidth > window.innerWidth')
        await page.locator('#starter-controls').get_by_role('button', name='Portrait', exact=True).click()
        await page.get_by_role('button', name='Choose Twine', exact=True).click()
        if not await page.get_by_role('button', name='Choose Twine', exact=True).is_disabled():
            raise RuntimeError('Preview confirmation did not close the controls.')
        await page.locator('#starter-panel').screenshot(path=str(output / 'starter-confirmed.png'))
        images = await page.locator('img[src]').evaluate_all('(images) => images.every(img => img.complete && img.naturalWidth > 0)')
        await browser.close()
    if errors or overflow or not images:
        raise RuntimeError(f'Preview QA failed: errors={errors}, overflow={overflow}, images={images}')
    print(f'Screenshots: {output}; no script errors, missing images or mobile horizontal overflow.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--tool-path', type=Path, help='Optional directory containing development-only preview packages.')
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    path = await export()
    if args.capture:
        await capture(path)


if __name__ == '__main__':
    asyncio.run(main())
