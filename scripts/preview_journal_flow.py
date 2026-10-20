"""Export real team pages and the encounter-to-journal layout for offline QA.

Uses a disposable example database and packaged artwork. No Discord login,
production account, combat reward or network action is involved.
"""
import argparse
import asyncio
import json
from pathlib import Path
import random
import sys
import tempfile
from unittest.mock import patch

import aiosqlite

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cogs.battle import build_pve_beast_state
from utils import adventure_party, battle_art, db as storage, expeditions, gear_art, journal_state, journal_team
from utils.journal import JournalView
from utils.journal_team_art import render_team


RENDERER = r"""
(() => {
  const root = document.getElementById('cb-journal-flow');
  const data = __FLOW_DATA__, art = __ART_DATA__;
  const names = {selection:'Choose an expedition', returned:'After returning home', battle_log:'Battle log', log_detail:'Completed trip recap', pending_log:'Trip still in progress', team:'Team overview', edit_reserve:'Edit a reserve', frozen:'Team saved for an expedition', encounter:'A creature on the trail', during_battle:'During the encounter', continued:'After the encounter'};
  const picker = root.querySelector('#cj-screen'), stage = root.querySelector('.cj-stage'), note = root.querySelector('.cj-note');
  let current = 'selection', role = '2', candidate = data.example_candidate;
  const message = 'Real Discord component payloads · example trainer · no live actions.';
  for (const [value, label] of Object.entries(names)) { const option = document.createElement('option'); option.value = value; option.textContent = label; picker.append(option); }
  function html(value) { const span = document.createElement('span'); span.textContent = String(value); return span.innerHTML; }
  function markdown(value) { return html(value).replace(/&lt;t:(\d+):([df])&gt;/g, (_, stamp, format) => new Date(Number(stamp) * 1000).toLocaleString('en-GB', format === 'd' ? {dateStyle:'medium', timeZone:'UTC'} : {dateStyle:'medium', timeStyle:'short', timeZone:'UTC'}) + ' UTC').replace(/\\([_~*`])/g, '$1').split('\n').map(line => line.startsWith('### ') ? '<h3>' + line.slice(4) + '</h3>' : line.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>') + '<br>').join(''); }
  function action(id) { const parts = id.split(':').slice(2); if (/^\d+$/.test(parts[parts.length - 1])) parts.pop(); return parts.join(':'); }
  function show(page, status = message) { if (!names[page]) return; current = page; render(); note.textContent = status; }
  function buttonAction(key) {
    if (key === 'page:team') return show('team');
    if (key === 'page:adventure') return show(current === 'pending_log' ? 'continued' : 'returned');
    if (key === 'page:battle_log' || key === 'log_back') return show('battle_log');
    if (key === 'begin') return show('encounter');
    if (key.startsWith('team_role:')) { role = key.split(':')[1]; candidate = data.defaults[role]; return show('edit_reserve'); }
    if (key.startsWith('team_assign:')) { note.textContent = 'In Discord, confirmation changes only the selected slot. Promoting a reserve keeps the three-member roster by swapping it with the lead.'; return; }
    if (key.startsWith('team_clear:')) { note.textContent = 'In Discord, this removes the companion from this reserve slot. It stays in your collection.'; return; }
    if (key === 'refresh') return show(current);
    note.textContent = 'This control uses your saved trainer in Discord.';
  }
  function selection(key, value) {
    if (key === 'log_entry') return show('log_detail');
    if (key === 'team_candidate') { const parts = value.split(':'); role = parts[0]; candidate = parts[1]; return show('edit_reserve'); }
    if (key === 'path' && value.endsWith(':challenge')) return show('during_battle');
    note.textContent = 'This route choice is stored with your expedition in Discord.';
  }
  function component(item, parent) {
    if (item.type === 17) { for (const child of item.components) component(child, parent); return; }
    if (item.type === 12) { const gallery = document.createElement('div'); gallery.className = 'cj-gallery'; for (const media of item.items) { const img = document.createElement('img'); img.src = art[media.media.url.replace('attachment://', '')] || ''; img.alt = media.description || 'Field Journal'; gallery.append(img); } parent.append(gallery); return; }
    if (item.type === 10) { const text = document.createElement('div'); text.className = 'cj-text'; text.innerHTML = markdown(item.content); parent.append(text); return; }
    if (item.type === 1) { const row = document.createElement('div'); row.className = 'cj-row'; for (const child of item.components) component(child, row); parent.append(row); return; }
    if (item.type === 2) { const button = document.createElement('button'); button.type = 'button'; button.textContent = item.label; button.disabled = !!item.disabled; button.dataset.style = item.style; button.onclick = () => buttonAction(action(item.custom_id)); parent.append(button); return; }
    if (item.type === 3) { const select = document.createElement('select'); select.setAttribute('aria-label', item.placeholder || 'Choose an option'); select.disabled = !!item.disabled; const placeholder = document.createElement('option'); placeholder.value = ''; placeholder.textContent = item.placeholder || 'Choose an option'; placeholder.disabled = true; select.append(placeholder); for (const entry of item.options) { const option = document.createElement('option'); option.value = entry.value; option.textContent = entry.label; option.selected = !!entry.default; select.append(option); } if (!item.options.some(option => option.default)) select.value = ''; select.onchange = () => selection(action(item.custom_id), select.value); parent.append(select); return; }
    if (item.type === 9) { const section = document.createElement('div'); section.className = 'cj-section'; const text = document.createElement('div'); for (const child of item.components) component(child, text); section.append(text); const img = document.createElement('img'); img.src = art[item.accessory.media.url.replace('attachment://', '')] || ''; img.alt = item.accessory.description || 'Companion'; section.append(img); parent.append(section); }
  }
  function journal(payload) { const card = document.createElement('div'); card.className = 'cj-card'; card.dataset.message = 'journal'; for (const item of payload) component(item, card); stage.append(card); }
  function label(text) { const item = document.createElement('p'); item.className = 'flow-label'; item.textContent = text; stage.append(item); }
  function render() {
    stage.replaceChildren(); root.dataset.flow = current;
    if (current === 'during_battle') {
      label('The saved journal waits while the encounter plays below it.'); journal(data.pages.during_battle);
      const battle = document.createElement('div'); battle.className = 'cj-card flow-battle'; battle.dataset.message = 'battle'; const img = document.createElement('img'); img.src = 'battle.png'; img.alt = 'Packaged battle renderer: Twine, Gloop and Barkley meet a wild Wisp'; battle.append(img); const button = document.createElement('button'); button.textContent = 'Finish sample encounter'; button.dataset.style = '3'; button.onclick = () => show('continued'); battle.append(button); stage.append(battle);
    } else if (current === 'continued') {
      label('Older journal removed · encounter result retained · one fresh journal at the bottom');
      const result = document.createElement('div'); result.className = 'cj-card flow-result'; result.dataset.message = 'result'; result.innerHTML = '<strong>Encounter complete</strong><p>' + html(data.result_note) + '</p>'; stage.append(result);
      journal(data.pages.continued);
    } else journal(current === 'edit_reserve' ? data.editors[role][candidate] : data.pages[current]);
    picker.value = current;
  }
  picker.onchange = () => show(picker.value);
  root.journalFlowPreview = {show, getState: () => ({page: current, role, candidate})};
  show('selection'); root.dataset.ready = 'true';
})();
"""


async def export(output):
    output.mkdir(parents=True, exist_ok=True)
    portraits = output / 'portraits'
    portraits.mkdir(exist_ok=True)
    art, checks = {}, []
    rng = random.Random(17)
    with tempfile.TemporaryDirectory(prefix='chibibeasts-journal-flow-') as directory, patch.object(storage, 'DB_PATH', str(Path(directory) / 'preview.db')):
        await storage.init_db()
        await storage.get_or_create_player(42, 'Journal preview')
        await storage.update_player(42, level=8, gold=3250)
        ids = []
        for species in ('twine', 'gloop', 'barkley', 'fairy'):
            ids.append(await storage.add_beast_to_player(42, dict(storage.get_beast_data(species), level=8), starter=species == 'twine'))
        await adventure_party.set_slot(42, ids[1], 1)
        await adventure_party.set_slot(42, ids[2], 2)
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.executemany('INSERT INTO player_equipment(user_id,equipment_id,beast_row_id) VALUES(42,?,?)',
                                 [('gnome_iron_plating', ids[0]), ('kelpie_scale_mail', ids[1]), ('sprite_silk_attire', ids[2])])
            await db.commit()
        view = JournalView(42, await journal_state.snapshot(42), page='team')

        async def save(name):
            view.team_art = await render_team([row for _, row in journal_team.roster(view)], view.art_realm) if view.page == 'team' else None
            view.render()
            assert view.content_length() <= 4000 and view.total_children_count <= 40
            if view.team_art is not None and view.team_art.name not in art:
                (portraits / view.team_art.name).write_bytes(view.team_art.data)
                art[view.team_art.name] = 'portraits/' + view.team_art.name
            for filename, appearance in view.portraits.items():
                if filename not in art:
                    rendered = await gear_art.render_portrait(*appearance)
                    if rendered is None:
                        raise RuntimeError(f'Missing portrait: {appearance}')
                    (portraits / filename).write_bytes(rendered.data)
                    art[filename] = 'portraits/' + filename
            if view.header.name not in art:
                (portraits / view.header.name).write_bytes(view.header.read_bytes())
                art[view.header.name] = 'portraits/' + view.header.name
            checks.append(dict(page=name, characters=view.content_length(), components=view.total_children_count))
            return view.to_components()

        try:
            pages = dict(team=await save('team'))
            view.page = 'adventure'
            pages['selection'] = await save('selection')
            view.page = 'team'
            editors, defaults = {}, {}
            for role in journal_team.ROLES:
                journal_team.select_role(view, role)
                defaults[role] = str(view.team_candidate_id)
                editors[role] = {}
                for row in view.state.workshop.companions:
                    journal_team.select_candidate(view, f"{role}:{row['id']}")
                    editors[role][str(row['id'])] = await save(f"editor:{role}:{row['id']}")
            pages['edit_reserve'] = editors['2'][str(ids[3])]
            trip = await expeditions.begin(42, 'woods', rng=rng)
            view.state, view.trip = await journal_state.snapshot(42), trip
            pages['frozen'] = await save('frozen')
            with patch('utils.expeditions.pick_encounter', return_value=storage.get_beast_data('wisp')):
                trip = await expeditions.choose(42, trip['id'], trip['version'], 'tracks', rng=rng)
            view.state, view.trip, view.page = await journal_state.snapshot(42), trip, 'adventure'
            pages['encounter'] = await save('encounter')
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'challenge', rng=rng)
            view.state, view.trip = await journal_state.snapshot(42), trip
            pages['during_battle'] = await save('during_battle')
            await expeditions.battle_result(42, trip['id'], trip['payload']['battle_token'], True)
            trip = await expeditions.read(42, trip['id'])
            view.state, view.trip = await journal_state.snapshot(42), trip
            pages['continued'] = await save('continued')
            result_note = trip['payload']['notes'][-1]
            view.page = 'battle_log'
            view.log_selected_id = trip['id']
            await view.load_page()
            pages['pending_log'] = await save('pending_log')
            trip = await expeditions.choose(42, trip['id'], trip['version'], 'return_early', rng=rng)
            view.state, view.trip, view.page = await journal_state.snapshot(42), trip, 'adventure'
            pages['returned'] = await save('returned')
            view.page, view.log_selected_id = 'battle_log', None
            await view.load_page()
            pages['battle_log'] = await save('battle_log')
            view.log_selected_id = trip['id']
            await view.load_page()
            pages['log_detail'] = await save('log_detail')
        finally:
            view.stop()
    party = [build_pve_beast_state(storage.get_beast_data(species), 8) for species in ('twine', 'gloop', 'barkley')]
    for index, (member, armor) in enumerate(zip(party, ('gnome_iron_plating', 'kelpie_scale_mail', 'sprite_silk_attire'))):
        member.update(id=101 + index, armor_id=armor)
    enemy = build_pve_beast_state(storage.get_beast_data('wisp'), 4)
    snapshot = battle_art.snapshot([dict(party[0], _party_states=party), enemy])
    battle_art.frame(snapshot, snapshot, 'woods').save(output / 'battle.png')
    data = dict(pages=pages, editors=editors, defaults=defaults, example_candidate=str(ids[3]), result_note=result_note)
    (output / 'components.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    template = (ROOT / 'scripts' / 'journal-preview-template.html').read_text(encoding='utf-8').split('<script>', 1)[0]
    fragment = template.replace('cb-field-journal', 'cb-journal-flow').replace('ChibiBeasts Field Journal preview', 'ChibiBeasts team and journal flow preview').replace('<div class="cj-card" aria-live="polite"></div>', '').replace('color-scheme: light dark;', 'color-scheme: dark;')
    script = RENDERER.replace('__FLOW_DATA__', json.dumps(data, ensure_ascii=False).replace('</', '<\\/')).replace('__ART_DATA__', json.dumps(art))
    style = '<style>html,#cb-journal-flow{color-scheme:dark}body{margin:0;padding:28px 12px;background:#202126;color:#dbdee1;font:14px/1.5 Arial,sans-serif}main{max-width:920px;margin:auto}header{max-width:640px;margin:0 auto 20px}h1{font-size:26px;line-height:1.25;margin:0 0 8px}header p{color:#b5bac1;margin:0}.flow-label{max-width:640px;margin:0 auto 12px;padding:0 2px;color:#d3c49c}.cj-stage{display:grid;gap:16px}.flow-battle img{width:100%;display:block;image-rendering:pixelated;margin-bottom:12px}.flow-result{border-left-color:#5aab7a!important}.flow-result p{margin:8px 0 0}@media(max-width:420px){body{padding:16px 8px}h1{font-size:22px}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}</style>'
    document = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ChibiBeasts · Adventure and battle log</title>' + style + '</head><body><main><header><h1>Adventure, without the clutter.</h1><p>Your next choice on Adventure. Past encounters and rewards in Battle log.</p></header>' + fragment + '<script>' + script + '</script></main></body></html>'
    (output / 'journal-flow.html').write_text(document, encoding='utf-8')
    report = dict(status='passed', scope='Actual JournalView.to_components() exports and packaged portraits from a disposable example database. Encounter outcome and chat placement are illustrative; automated regressions verify live message ordering and retirement.',
                  pages=list(pages), total_payloads=len(checks), largest_text_count=max(row['characters'] for row in checks), largest_component_count=max(row['components'] for row in checks), checks=checks)
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key != 'checks'}))
    return output / 'journal-flow.html'


async def capture(path, output):
    from playwright.async_api import async_playwright
    errors, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport=dict(width=1100, height=1000), device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(path.resolve().as_uri())
        await page.wait_for_selector('#cb-journal-flow[data-ready="true"]')
        for width, label in ((1100, 'desktop'), (390, 'mobile'), (320, 'narrow')):
            await page.set_viewport_size(dict(width=width, height=1000))
            for screen in ('selection', 'returned', 'battle_log', 'log_detail', 'pending_log', 'team', 'edit_reserve', 'frozen', 'during_battle', 'continued'):
                await page.locator('#cj-screen').select_option(screen)
                await page.wait_for_function('Array.from(document.images).every(img => img.complete && img.naturalWidth > 0)')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                if screen == 'continued':
                    assert await page.locator('[data-message="journal"]').count() == 1
                    assert await page.locator('[data-message="result"]').count() == 1
                    assert await page.locator('.cj-stage > [data-message]').evaluate_all('(nodes) => nodes.map(n => n.dataset.message).join(",")') == 'result,journal'
                await page.screenshot(path=str(output / f'{label}-{screen}.png'), full_page=True)
                checks.append(dict(width=width, page=screen, no_overflow=True, all_art_loaded=True))
        await page.locator('#cj-screen').select_option('returned')
        assert 'Rewards received' not in await page.locator('.cj-stage').inner_text()
        await page.get_by_role('button', name='Battle log', exact=True).click()
        picker = page.get_by_role('combobox', name='Open an expedition recap')
        await picker.select_option(index=1)
        assert 'Rewards received' in await page.locator('.cj-stage').inner_text()
        await page.get_by_role('button', name='Back to Adventure', exact=True).click()
        assert 'Back from' in await page.locator('.cj-stage').inner_text()
        await page.locator('#cj-screen').select_option('team')
        await page.get_by_role('button', name='Edit reserve 2', exact=True).click()
        picker = page.get_by_role('combobox', name='Choose your reserve 2')
        options = await picker.locator('option[value]').evaluate_all('(nodes) => nodes.filter(n => n.value).map(n => ({value:n.value,label:n.textContent}))')
        for option in options:
            await page.get_by_role('combobox', name='Choose your reserve 2').select_option(option['value'])
            assert option['label'].split(' · ')[1] in await page.locator('.cj-stage').inner_text()
        await page.locator('#cj-screen').select_option('during_battle')
        await page.get_by_role('button', name='Finish sample encounter').click()
        assert await page.locator('[data-message="journal"]').count() == 1
        assert await page.locator('[data-message="battle"]').count() == 0
        await browser.close()
    assert not errors, errors
    report = dict(status='passed', javascript_errors=errors, checks=checks, role_editor_navigation=True, candidate_selection=True, battle_return_order=['result', 'journal'], returned_live_journals=1)
    (output / 'browser-validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} viewport/page combinations, no overflow or missing artwork.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'journal-flow-preview')
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
