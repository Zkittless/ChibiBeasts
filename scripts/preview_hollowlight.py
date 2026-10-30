"""Export actual Carnival journal components and capture desktop/mobile QA."""
import argparse
import asyncio
from contextlib import ExitStack
import importlib
import json
from pathlib import Path
import random
import re
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import adventure_party, db as storage, expeditions, gear_art, hollowlight, journal_adventure, journal_log, journal_state
from utils.journal import JournalView


async def export(output):
    output.mkdir(parents=True, exist_ok=True)
    assets = output / 'assets'
    assets.mkdir(exist_ok=True)
    pages, art, checks = {}, {}, []
    with tempfile.TemporaryDirectory(prefix='chibi-carnival-preview-') as directory, ExitStack() as stack:
        for name in ('utils.db', 'utils.progress', 'cogs.battle', 'cogs.world', 'cogs.ranked', 'cogs.utilities', 'utils.sanctuary'):
            module = importlib.import_module(name)
            if hasattr(module, 'DB_PATH'):
                stack.enter_context(patch.object(module, 'DB_PATH', str(Path(directory) / 'preview.db')))
        await storage.init_db()
        await storage.get_or_create_player(42, 'Carnival preview')
        await storage.update_player(42, level=30, gold=3250)
        members = [await storage.add_beast_to_player(42, {**storage.get_beast_data(species), 'level': 20}, starter=index == 0)
                   for index, species in enumerate(('twine', 'gloop', 'barkley'))]
        await adventure_party.set_slot(42, members[1], 1)
        await adventure_party.set_slot(42, members[2], 2)
        async with storage.connect() as db:
            await db.execute('INSERT INTO hollowlight_wallets VALUES(?,?,220,220)', (42, hollowlight.EVENT_ID))
            await db.commit()
        view = JournalView(42, await journal_state.snapshot(42), page='adventure', realm='glacial')

        async def save(key):
            view.state = await journal_state.snapshot(42)
            view.render()
            assert view.content_length() <= 4000 and view.total_children_count <= 40
            if view.header.name not in art:
                (assets / view.header.name).write_bytes(view.header.read_bytes())
                art[view.header.name] = 'assets/' + view.header.name
            if view.journey_media and view.journey_media.name not in art:
                (assets / view.journey_media.name).write_bytes(view.journey_media.data)
                art[view.journey_media.name] = 'assets/' + view.journey_media.name
            for filename, appearance in view.portraits.items():
                if filename not in art:
                    portrait = await gear_art.render_portrait(*appearance)
                    if portrait is None:
                        raise RuntimeError(f'Missing Carnival preview portrait: {appearance}')
                    (assets / filename).write_bytes(portrait.data)
                    art[filename] = 'assets/' + filename
            pages[key] = view.to_components()
            checks.append(dict(page=key, characters=view.content_length(), components=view.total_children_count))

        try:
            await save('selection')
            view.carnival_selected = True
            await save('destination')
            view.page = 'carnival'
            await save('welcome')
            gift = await hollowlight.claim_gift(42, request_key='preview-gift')
            view.carnival_egg = gift['egg']['id']
            view.notice = 'Welcome back! Your Halloween egg, recovery supplies and Lantern Embers are ready.'
            await save('gift')
            view.notice = ''
            view.carnival_offer = hollowlight.GHOST_UNLOCK
            await save('offer')
            view.carnival_confirmation = (hollowlight.GHOST_UNLOCK, 30)
            await save('confirm')
            await hollowlight.redeem(42, hollowlight.GHOST_UNLOCK, request_key='preview-costume', quoted_cost=30)
            view.carnival_confirmation = None
            view.notice = 'Ghost Costume · 2026 redeemed for 30 Lantern Embers.'
            await save('owned')
            egg = await hollowlight.redeem(42, 'wickwing_egg', request_key='preview-egg', quoted_cost=50)
            view.carnival_egg, view.notice = egg['egg']['id'], ''
            await save('eggs')
            result = await hollowlight.hatch_egg(42, egg['egg']['id'], request_key='preview-hatch', rng=random.Random(17))
            view.carnival_reveal, view.carnival_egg = result, None
            view.notice = '**Wickwing hatched!** · Lv.20 · Rare'
            await save('reveal')
            view.carnival_reveal, view.notice = None, ''
            await hollowlight.redeem(42, hollowlight.TITLE_UNLOCK, request_key='preview-title', quoted_cost=20)
            await hollowlight.set_title(42, True, request_key='preview-title-equip')
            await save('title')
            view.trip = await expeditions.begin(42, 'hollowlight', adventure_version=3, rng=random.Random(17))
            view.page = 'adventure'
            await journal_adventure.prepare_still(view)
            await save('battle')
            view.page = 'battle_log'
            await journal_log.load(view)
            await save('history')
            async with storage.connect() as db:
                await db.execute('UPDATE hollowlight_events SET starts_at=1,ends_at=2,redeem_until=3')
                await db.commit()
            view.page = 'carnival'
            view.carnival_egg = gift['egg']['id']
            await save('closed')
        finally:
            view.stop()

    (output / 'components.json').write_text(json.dumps(pages, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    template = (ROOT / 'scripts' / 'journal-preview-template.html').read_text(encoding='utf-8')
    names = dict(selection='Choose a destination', destination='Carnival selected', welcome='Welcome-back gift',
                 gift='Gift claimed · egg saved', offer='Review a cosmetic', confirm='Confirm exact price', owned='Costume unlocked',
                 eggs='Choose a saved egg', reveal='Hatch reveal', title='Owned title', battle='Carnival battle', history='Battle log', closed='After the Carnival closes')
    template = re.sub(r'  const names = .*?;', lambda _: '  const names = ' + json.dumps(names) + ';', template, count=1)
    template = template.replace("let current = 'home';", "let current = 'selection';")
    start, end = template.index('  function buttonAction('), template.index('  function component(')
    template = template[:start] + """
  function buttonAction(key) {
    if (key === 'page:carnival') return show('welcome');
    if (key === 'page:adventure') return show('destination');
    if (key === 'page:battle_log') return show('history');
    if (key === 'carnival_gift') return show('gift');
    if (key === 'carnival_review') return show('confirm');
    if (key === 'carnival_cancel') return show('offer');
    if (key.startsWith('carnival_buy:')) return show('owned');
    if (key.startsWith('carnival_hatch:')) return show('reveal');
    if (key === 'begin') return show('battle');
    note.textContent = 'This control uses your saved trainer in Discord. This preview never writes to a live account.';
  }
  function selection(key, value) {
    if (key === 'realm' && value === 'hollowlight') return show('destination');
    if (key === 'carnival_offer') return show('offer');
    if (key === 'carnival_egg') return show('eggs');
    note.textContent = 'Selection preview · no live game action.';
  }
""" + template[end:]
    template = template.replace("return html(value).replace", r"return html(value).replace(/&lt;t:(\d+):([df])&gt;/g, (_, s, f) => new Date(Number(s) * 1000).toLocaleString('en-GB', f === 'd' ? {dateStyle:'medium',timeZone:'UTC'} : {dateStyle:'medium',timeStyle:'short',timeZone:'UTC'}) + ' UTC').replace")
    template = template.replace('__JOURNAL_DATA__', json.dumps(pages, ensure_ascii=False).replace('</', '<\\/')).replace('__ART_DATA__', json.dumps(art))
    style = '<style>html,#cb-field-journal{color-scheme:dark}body{margin:0;padding:24px 12px;background:#202126;color:#dbdee1}main{max-width:820px;margin:auto}header{max-width:640px;margin:0 auto 20px;font:14px/1.5 Arial,sans-serif}h1{font-size:25px;margin:0 0 8px}header p{color:#b5bac1;margin:0}@media(max-width:420px){body{padding:16px 8px}}</style>'
    document = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hollowlight Carnival · Journal preview</title>' + style + '</head><body><main><header><h1>The Hollowlight Carnival</h1><p>Actual Discord journal components · disposable example trainer · no live purchases</p></header>' + template + '</main></body></html>'
    path = output / 'hollowlight.html'
    path.write_text(document, encoding='utf-8')
    report = dict(status='passed', scope='Actual production LayoutView payloads and packaged artwork; disposable SQLite example; no live Discord actions.',
                  pages=list(pages), largest_text_count=max(row['characters'] for row in checks),
                  largest_component_count=max(row['components'] for row in checks), checks=checks)
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'checks'}))
    return path, list(pages)


async def capture(path, output, screens):
    from playwright.async_api import async_playwright
    errors, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport=dict(width=1100, height=1000), device_scale_factor=1, color_scheme='dark')
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(path.resolve().as_uri())
        await page.wait_for_selector('#cb-field-journal[data-ready="true"]')
        for width, label in ((1100, 'desktop'), (390, 'mobile'), (320, 'narrow')):
            await page.set_viewport_size(dict(width=width, height=1000))
            for screen in screens:
                await page.locator('#cj-screen').select_option(screen)
                await page.wait_for_function('Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, screen)
                await page.screenshot(path=str(output / f'{label}-{screen}.png'), full_page=True)
                checks.append(dict(width=width, page=screen, no_overflow=True, all_art_loaded=True))
        await page.locator('#cj-screen').select_option('welcome')
        await page.get_by_role('button', name='Claim welcome gift', exact=True).click()
        assert 'Welcome gift claimed' in await page.locator('.cj-card').inner_text()
        await page.get_by_role('combobox', name='Choose an egg or exclusive cosmetic').select_option('ghost_costume_2026')
        await page.get_by_role('button', name='Review purchase', exact=True).click()
        assert 'Confirm · 30 Embers' in await page.locator('.cj-card').inner_text()
        await page.get_by_role('button', name='Confirm · 30 Embers', exact=True).click()
        assert 'Dress companions · Gear' in await page.locator('.cj-card').inner_text()
        await browser.close()
    assert not errors, errors
    (output / 'browser-validation.json').write_text(json.dumps(dict(status='passed', javascript_errors=errors, checks=checks,
        gift_navigation=True, review_confirm_navigation=True), indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} viewport/page combinations; no overflow or missing artwork.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / 'output' / 'hollowlight' / 'ui-preview')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--tool-path', type=Path)
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    path, screens = await export(args.output)
    if args.capture:
        await capture(path, args.output, screens)


if __name__ == '__main__':
    asyncio.run(main())
