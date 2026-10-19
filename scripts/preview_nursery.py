"""Export production Nursery components from a disposable example database."""
import argparse
import asyncio
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils import db as storage, gear_art, nursery
from utils.nursery_catalog import RESEARCH_COST, research_key
from utils.nursery_view import NurseryView


RENDERER = r"""
(() => {
  const root = document.getElementById('cb-nursery');
  const data = __NURSERY_DATA__;
  const art = __ART_DATA__;
  const names = {eggs:'Egg grades and origins', pool:'Possible companions', incubations:'Passive incubators', offer:'Saved hatchling choice', research:'Family research', reveal:'A companion arrives'};
  const picker = root.querySelector('#cj-screen');
  const card = root.querySelector('.cj-card');
  const note = root.querySelector('.cj-note');
  let current = 'eggs', egg = 'rare_egg', poolPage = 0;
  let incubation = String(data.primary_incubation), researchEgg = data.offer_egg;
  const researchTargets = {};
  const message = 'Preview of real Discord controls · sample trainer · purchases and hatches happen in Discord.';
  for (const [value, label] of Object.entries(names)) {
    const option = document.createElement('option'); option.value = value; option.textContent = label; picker.append(option);
  }
  function html(value) { const span = document.createElement('span'); span.textContent = String(value); return span.innerHTML; }
  function markdown(value) {
    return html(value).replace(/&lt;t:(\d+):R&gt;/g, (_, seconds) => {
      const minutes = Math.round((Number(seconds) - data.preview_now) / 60);
      return minutes > 0 ? 'in ' + minutes + ' minutes' : 'ready';
    }).replace(/\\([_~*`])/g, '$1').split('\n').map(line => {
      if (line.startsWith('### ')) return '<h3>' + line.slice(4) + '</h3>';
      if (line.startsWith('## ')) return '<h2>' + line.slice(3) + '</h2>';
      return line.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>') + '<br>';
    }).join('');
  }
  function action(id) {
    const parts = id.split(':').slice(2);
    if (/^\d+$/.test(parts[0])) parts.shift();
    return parts.join(':');
  }
  function show(page, status = message) { if (!names[page]) return; current = page; render(); note.textContent = status; }
  function buttonAction(key) {
    if (key.startsWith('page:')) return show(key.slice(5) === 'recent' ? 'reveal' : key.slice(5));
    if (key === 'pool') { poolPage = 0; return show('pool'); }
    if (key === 'offer') return show('offer');
    if (key === 'research' || key === 'research_offer') { researchEgg = key === 'research_offer' ? data.offer_egg : egg; return show('research'); }
    if (key === 'refresh') return show(current);
    if (key.startsWith('pool_page:')) { poolPage += key.endsWith('prev') ? -1 : 1; return show('pool'); }
    const operations = {buy:'Buying spends the displayed gold after a fresh inventory and level check.', hatch:'Hatching consumes one owned egg and saves the companion before its reveal.', incubate:'Incubation consumes one owned egg and reserves an available incubator.', quantity:'Discord opens a quantity prompt capped at 25 owned eggs.', care:'Optional care adds starting bond; the deadline stays fixed.', research_hatch:'Confirmation spends one eligible egg and 8 points for the selected grade-floor companion.'};
    note.textContent = key.startsWith('claim:') ? 'Choosing in Discord saves one companion from this exact offer; it cannot be rerolled by reopening.' : (operations[key] || 'This control uses your saved trainer in Discord.');
  }
  function selection(key, value) {
    if (key === 'egg') { egg = value; poolPage = 0; return show('eggs'); }
    if (key === 'incubation') { incubation = value; return show('incubations'); }
    if (key === 'target') { researchTargets[researchEgg] = value; return show('research', 'Research target selected in this preview. Confirmation and payment happen in Discord.'); }
  }
  function component(item, parent) {
    if (item.type === 17) { for (const child of item.components) component(child, parent); return; }
    if (item.type === 10) { const text = document.createElement('div'); text.className = 'cj-text'; text.innerHTML = markdown(item.content); parent.append(text); return; }
    if (item.type === 1) { const row = document.createElement('div'); row.className = 'cj-row'; for (const child of item.components) component(child, row); parent.append(row); return; }
    if (item.type === 2) { const button = document.createElement('button'); button.type = 'button'; button.textContent = item.label; button.disabled = !!item.disabled; button.dataset.style = item.style; button.onclick = () => buttonAction(action(item.custom_id)); parent.append(button); return; }
    if (item.type === 3) {
      const select = document.createElement('select'); select.setAttribute('aria-label', item.placeholder || 'Choose an option'); select.disabled = !!item.disabled;
      const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = item.placeholder || 'Choose an option'; placeholder.disabled = true; select.append(placeholder);
      for (const entry of item.options) { const option = document.createElement('option'); option.value = entry.value; option.textContent = entry.label; option.selected = !!entry.default; select.append(option); }
      if (!item.options.some(option => option.default)) select.value = '';
      select.onchange = () => selection(action(item.custom_id), select.value); parent.append(select); return;
    }
    if (item.type === 9) {
      const section = document.createElement('div'); section.className = 'cj-section'; const text = document.createElement('div'); for (const child of item.components) component(child, text); section.append(text);
      const image = document.createElement('img'); image.src = art[item.accessory.media.url.replace('attachment://', '')] || ''; image.alt = item.accessory.description || 'Companion portrait'; section.append(image); parent.append(section);
    }
  }
  function payload() {
    if (current === 'eggs') return data.eggs[egg].eggs;
    if (current === 'pool') { const pages = data.eggs[egg].pool; poolPage = Math.max(0, Math.min(poolPage, pages.length - 1)); return pages[poolPage]; }
    if (current === 'incubations') return data.incubations[incubation] || data.pages.incubations;
    if (current === 'research') { const group = data.research[researchEgg]; return group?.targets[researchTargets[researchEgg]] || group?.default || data.pages.research; }
    return data.pages[current];
  }
  function render() { card.replaceChildren(); for (const item of payload()) component(item, card); picker.value = current; }
  picker.onchange = () => show(picker.value);
  root.nurseryPreview = {show, getState: () => ({page: current, egg, poolPage})};
  show('eggs'); root.dataset.ready = 'true';
})();
"""


async def export(output):
    output.mkdir(parents=True, exist_ok=True)
    portraits = output / 'portraits'
    portraits.mkdir(exist_ok=True)
    validation = []
    with tempfile.TemporaryDirectory(prefix='chibibeasts-nursery-preview-') as directory:
        temporary_db = str(Path(directory) / 'preview.db')
        with patch.object(storage, 'DB_PATH', temporary_db), ExitStack() as stack:
            # No production database or Discord connection is used anywhere.
            await storage.init_db()
            await storage.get_or_create_player(42, 'Nursery preview')
            await storage.add_beast_to_player(42, storage.get_beast_data('twine'), starter=True)
            await storage.update_player(42, level=20, gold=12000)
            for egg_id, quantity in (('rare_egg', 3), ('sprout_pod', 3), ('cavern_core', 2), ('dewdrop_bulb', 1)):
                await storage.add_item(42, egg_id, quantity)
            async with storage.connect() as db:
                await db.executemany('INSERT INTO nursery_research VALUES(?,?,?)',
                                     [(42, research_key(key), RESEARCH_COST) for key in ('rare_egg', 'cavern_core')])
                await db.commit()
            now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
            active = await nursery.incubate(42, 'sprout_pod', request_key='preview-active', now=now - timedelta(minutes=15))
            ready = await nursery.incubate(42, 'cavern_core', request_key='preview-ready', now=now - timedelta(hours=5))
            offer = await nursery.prepare_offer(42, ready['id'], now=now, rng=random.Random(2))
            state = await nursery.snapshot(42, now=now)
            view = NurseryView(42, state, egg_id='rare_egg')
            stack.callback(view.stop)
            art = {}

            async def save(name):
                view.render()
                assert view.content_length() <= 4000 and view.total_children_count <= 40
                for filename, species in view.portraits.items():
                    if filename not in art:
                        rendered = await gear_art.render_portrait(species)
                        if rendered is None:
                            raise RuntimeError(f'No local portrait for {species}')
                        (portraits / filename).write_bytes(rendered.data)
                        art[filename] = 'portraits/' + filename
                validation.append(dict(page=name, characters=view.content_length(), components=view.total_children_count))
                return view.to_components()

            pages = {}
            for page in ('eggs', 'pool', 'incubations'):
                view.page = page
                pages[page] = await save(page)
            view.offer, view.page = offer, 'offer'
            pages['offer'] = await save('offer')
            view.egg_id, view.page, view.research_offer = offer['egg_id'], 'research', True
            view.targets = await nursery.research_targets(42, view.egg_id)
            view.target = view.targets[0]['id'] if view.targets else None
            pages['research'] = await save('research')

            egg_pages, research_pages, incubation_pages = {}, {}, {}
            for egg in state['eggs']:
                view.egg_id, view.page = egg['id'], 'eggs'
                egg_pages[egg['id']] = dict(eggs=await save('eggs:' + egg['id']), pool=[])
                view.page = 'pool'
                for page_index in range(max(1, (len(egg['candidates']) + 9) // 10)):
                    view.pool_page = page_index
                    egg_pages[egg['id']]['pool'].append(await save(f"pool:{egg['id']}:{page_index}"))
                if egg['research'] >= RESEARCH_COST:
                    view.page, view.research_offer = 'research', bool(egg['incubation_hours'])
                    view.targets = await nursery.research_targets(42, egg['id'])
                    view.target = view.targets[0]['id'] if view.targets else None
                    research_pages[egg['id']] = dict(default=await save('research:' + egg['id']), targets={})
                    for target in view.targets:
                        view.target = target['id']
                        research_pages[egg['id']]['targets'][target['id']] = await save(f"research:{egg['id']}:{target['id']}")
            for incubation in (active, ready):
                view.page, view.incubation_id = 'incubations', incubation['id']
                incubation_pages[str(incubation['id'])] = await save('incubation:' + str(incubation['id']))

            grant = await nursery.claim_offer(42, offer['id'], 0, request_key='preview-claim')
            view.state = await nursery.snapshot(42, now=now)
            view.grants, view.page, view.research_offer = [grant], 'reveal', False
            pages['reveal'] = await save('reveal')
            data = dict(pages=pages, eggs=egg_pages, research=research_pages, incubations=incubation_pages,
                        primary_incubation=ready['id'], offer_egg=offer['egg_id'], preview_now=now.timestamp())
    (output / 'components.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    template = (ROOT / 'scripts' / 'journal-preview-template.html').read_text(encoding='utf-8')
    fragment = template.split('<script>', 1)[0].replace('cb-field-journal', 'cb-nursery').replace('ChibiBeasts Field Journal preview', 'ChibiBeasts Nursery preview')
    script = RENDERER.replace('__NURSERY_DATA__', json.dumps(data, ensure_ascii=False).replace('</', '<\\/'))
    script = script.replace('__ART_DATA__', json.dumps(art))
    document = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>ChibiBeasts Nursery preview</title><style>html{color-scheme:dark}body{margin:0;padding:24px 12px;background:#202126}main{max-width:920px;margin:auto}h2{font-size:22px;line-height:1.3;margin:0 0 8px} @media(max-width:420px){body{padding:12px 8px}}</style></head><body><main>'
                + fragment + '<script>' + script + '</script></main></body></html>')
    (output / 'nursery.html').write_text(document, encoding='utf-8')
    report = dict(status='passed', scope='Real production LayoutView components and local portraits; disposable example DB; no live Discord actions.',
                  primary_pages=list(pages), total_payloads=len(validation), largest_text_count=max(row['characters'] for row in validation),
                  largest_component_count=max(row['components'] for row in validation), portraits=len(art), checks=validation)
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'checks'}))
    return output / 'nursery.html'


async def capture(path, output):
    from playwright.async_api import async_playwright
    errors, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1100, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(path.resolve().as_uri())
        await page.wait_for_selector('#cb-nursery[data-ready="true"]')
        for width, label in ((1100, 'desktop'), (390, 'mobile'), (320, 'narrow')):
            await page.set_viewport_size(dict(width=width, height=1000))
            for screen in ('eggs', 'pool', 'incubations', 'offer', 'research', 'reveal'):
                await page.locator('#cj-screen').select_option(screen)
                await page.wait_for_function('Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                await page.screenshot(path=str(output / f'{label}-{screen}.png'), full_page=True)
                checks.append(dict(width=width, page=screen, no_overflow=True, all_portraits_loaded=True))
        await page.set_viewport_size(dict(width=390, height=1000))
        await page.locator('#cj-screen').select_option('eggs')
        await page.get_by_role('combobox', name='Choose an egg grade and origin').select_option('dewdrop_bulb')
        assert 'Dewdrop Bulb' in await page.locator('.cj-card').inner_text()
        await page.get_by_role('button', name='Possible companions', exact=True).click()
        assert 'Possible companions' in await page.locator('.cj-card').inner_text()
        await page.locator('#cj-screen').select_option('incubations')
        select = page.get_by_role('combobox', name='Choose an incubating egg')
        options = await select.locator('option[value]').evaluate_all('(options) => options.map(o => o.value).filter(Boolean)')
        for incubation in options:
            await select.select_option(incubation)
            select = page.get_by_role('combobox', name='Choose an incubating egg')
        await page.locator('#cj-screen').select_option('research')
        target_select = page.get_by_role('combobox', name='Choose a companion to research')
        targets = await target_select.locator('option[value]').evaluate_all('(options) => options.filter(o => o.value).map(o => ({value:o.value,label:o.textContent}))')
        for target in targets:
            await page.get_by_role('combobox', name='Choose a companion to research').select_option(target['value'])
            assert 'Selected companion: ' + target['label'] in await page.locator('.cj-card').inner_text()
        await browser.close()
    assert not errors, errors
    (output / 'browser-validation.json').write_text(json.dumps(dict(status='passed', javascript_errors=errors, checks=checks,
        catalog_selection=True, pool_navigation=True, incubation_selection=True, research_selection=True), indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} viewport/page combinations, no overflow or missing portraits.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'nursery-preview')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--tool-path', type=Path)
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    path = await export(args.output)
    if args.capture:
        await capture(path, args.output)


if __name__ == '__main__':
    asyncio.run(main())
