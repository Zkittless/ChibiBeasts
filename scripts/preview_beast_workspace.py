"""Record real beast-workspace callbacks and export an offline navigation preview."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_gameplay import interaction
from tests.test_interactions import component
from utils import adventure_party, db as storage
from utils.roster_view import open_roster


RENDERER = r"""
(() => {
  const root = document.getElementById('cb-beast-workspace'), data = __DATA__, art = __ART__;
  const stage = root.querySelector('.cj-stage'), picker = root.querySelector('#cj-screen'), note = root.querySelector('[role="status"]');
  let current = 'beast';
  const routes = {beast:'beast',care:'care',gear:'gear',train:'train',evolve:'evolve',team:'team'};
  const names = {beast:'Beast card',care:'Care',care_item:'Care item preview',gear:'Gear',gear_edit:'Armor editor',train:'Training',evolve:'Evolution',team:'Exploration team',raid:'Raid team',roster:'Collection'};
  const links = {'care:Choose an owned care item':'care_item','gear:Change armor':'gear_edit','gear_edit:Done':'gear','team:Raids':'raid','raid:Exploration':'team','beast:Back to roster':'roster'};
  function html(text) { const span=document.createElement('span'); span.textContent=String(text); return span.innerHTML; }
  function md(text) { return html(text).replace(/\\([_~*`])/g,'$1').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/\n/g,'<br>'); }
  function show(name) { if (!data[name]) return; current=name; render(); }
  function action(label,value) {
    if (label==='Back to beast') return show('beast');
    if (['Companion actions','Beast workspace'].includes(label) && routes[value]) return show(routes[value]);
    if (label.startsWith('Open a companion') || label.startsWith('Choose a companion')) return show('beast');
    if (links[current+':'+label]) return show(links[current+':'+label]);
    if (label==='Refresh') return show(current);
    note.textContent='Navigation preview only. Item use, training, evolution and assignments are covered by transaction regression tests.';
  }
  function control(item,parent) {
    if (item.type===1) { const row=document.createElement('div'); row.className='cj-row'; for(const child of item.components) control(child,row); parent.append(row); }
    if (item.type===2) { const button=document.createElement('button'); button.textContent=item.label; button.disabled=!!item.disabled; button.dataset.style=item.style; button.onclick=()=>action(item.label); parent.append(button); }
    if (item.type===3) { const select=document.createElement('select'); select.setAttribute('aria-label',item.placeholder); select.disabled=!!item.disabled; const hint=document.createElement('option'); hint.textContent=item.placeholder; hint.value=''; hint.disabled=true; select.append(hint); for(const option of item.options) { const element=document.createElement('option'); element.value=option.value; element.textContent=option.label; element.selected=!!option.default; select.append(element); } if (!item.options.some(option=>option.default)) select.value=''; select.onchange=()=>action(item.placeholder,select.value); parent.append(select); }
  }
  function render() {
    stage.replaceChildren(); root.dataset.screen=current; picker.value=current;
    const panel=data[current], embed=panel.embed, card=document.createElement('article'); card.className='cj-card'; card.dataset.message='8181';
    const text=document.createElement('div'); text.className='cj-text'; text.innerHTML='<h3>'+html(embed.title||'')+'</h3>'+md(embed.description||'');
    for (const field of embed.fields||[]) text.innerHTML+='<h4>'+html(field.name)+'</h4>'+md(field.value);
    card.append(text);
    if (embed.image?.url) { const image=document.createElement('img'); image.className=panel.wide_art?'beast-art team-art':'beast-art'; image.src=art[embed.image.url.replace('attachment://','')]; image.alt=embed.title; card.append(image); }
    if (embed.footer) { const footer=document.createElement('p'); footer.className='cj-note'; footer.textContent=embed.footer.text; card.append(footer); }
    for (const item of panel.components) control(item,card); stage.append(card);
    note.textContent='One message · same selected companion · saved roster filters and position';
  }
  for (const [value,label] of Object.entries(names)) { const option=document.createElement('option'); option.value=value; option.textContent=label; picker.append(option); }
  picker.onchange=()=>show(picker.value); root.workspacePreview={show}; show('beast'); root.dataset.ready='true';
})();
"""


async def export(output):
    output.mkdir(parents=True, exist_ok=True)
    (output / 'portraits').mkdir(exist_ok=True)
    data, art, transitions = {}, {}, []
    message = SimpleNamespace(id=8181, attachments=[], edit=AsyncMock(), delete=AsyncMock())
    views = []
    with tempfile.TemporaryDirectory(prefix='beast-workspace-') as directory, patch.object(storage, 'DB_PATH', str(Path(directory) / 'preview.db')):
        await storage.init_db()
        await storage.get_or_create_player(42, 'Example trainer')
        await storage.update_player(42, level=8, gold=3250)
        ids = [await storage.add_beast_to_player(42, dict(storage.get_beast_data(species), level=8), starter=index == 0)
               for index, species in enumerate(('twine', 'gloop', 'barkley'))]
        await adventure_party.set_slot(42, ids[1], 1)
        await adventure_party.set_slot(42, ids[2], 2)
        await storage.add_item(42, 'pixie_dust_potion', 3)
        async with storage.connect() as db:
            await db.executemany('INSERT INTO player_equipment(user_id,equipment_id,beast_row_id) VALUES(42,?,?)',
                                 [('gnome_iron_plating', ids[0]), ('kelpie_scale_mail', ids[1]), ('sprite_silk_attire', ids[2]), ('kelpie_scale_mail', None)])
            await db.execute('UPDATE player_beasts SET hp=35 WHERE id=?', (ids[0],))
            await db.commit()
        command = interaction()
        command.followup.send.return_value = message
        root = await open_roster(command, details=True)
        nav = root.navigator

        def save(name, payload):
            view = payload['view']
            views.append(view)
            assert view.message.id == 8181
            assert len(view.to_components()) <= 5
            data[name] = dict(embed=payload['embed'].to_dict(), components=view.to_components(), wide_art=view.workspace_route == 'team')
            attachments = payload.get('files', payload.get('attachments', []))
            for file in attachments:
                assert file.filename in art

        def capture(kwargs):
            for file in kwargs.get('files', kwargs.get('attachments', [])):
                file.fp.seek(0)
                (output / 'portraits' / file.filename).write_bytes(file.fp.read())
                file.fp.seek(0)
                art[file.filename] = 'portraits/' + file.filename
            return message

        # Re-publish the first card with the same production edit path to collect its art.
        async def step(name, callback, value=None):
            click = component(section=value, edit=lambda **kwargs: capture(kwargs))
            click.message = message
            source = nav.active.workspace_route
            await callback(click)
            click.followup.send.assert_not_awaited()
            click.edit_original_response.assert_awaited_once()
            save(name, click.edit_original_response.call_args.kwargs)
            transitions.append(dict(source=source, destination=name, message_id=message.id, followups=0))

        async def press(label, name):
            button = next(item for item in nav.active.children if getattr(item, 'label', '') == label)
            await step(name, button.callback)

        async def route(key):
            picker = next(item for item in nav.active.children if getattr(item, 'placeholder', '') in {'Companion actions', 'Beast workspace'})
            await step(key, picker.callback, key)

        try:
            await step('beast', root.refresh_button.callback)
            await route('care')
            await step('care_item', nav.active.item_select.callback, 'pixie_dust_potion')
            await press('Back to beast', 'beast')
            await route('gear')
            await press('Change armor', 'gear_edit')
            await press('Back to beast', 'beast')
            await route('train')
            await press('Back to beast', 'beast')
            await route('evolve')
            await press('Back to beast', 'beast')
            await route('team')
            await press('Raids', 'raid')
            await press('Back to beast', 'beast')
            await press('Back to roster', 'roster')
            message.delete.assert_not_awaited()
        finally:
            nav.active.stop()
            for view in views:
                view.stop()
    (output / 'components.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    template = (ROOT / 'scripts/journal-preview-template.html').read_text(encoding='utf-8').split('<script>', 1)[0]
    template = template.replace('cb-field-journal', 'cb-beast-workspace').replace('ChibiBeasts Field Journal preview', 'ChibiBeasts beast workspace preview').replace('<div class="cj-card" aria-live="polite"></div>', '').replace('color-scheme: light dark;', 'color-scheme: dark;')
    script = RENDERER.replace('__DATA__', json.dumps(data, ensure_ascii=False).replace('</', '<\\/')).replace('__ART__', json.dumps(art))
    style = '<style>body{margin:0;padding:24px 10px;background:#202126;color:#dbdee1;font:14px/1.5 Arial,sans-serif}main{max-width:760px;margin:auto}header{max-width:640px;margin:0 auto 20px}header p{color:#b5bac1}.beast-art{display:block;max-width:100%;width:240px;max-height:220px;object-fit:contain;image-rendering:pixelated;margin:16px auto}.team-art{width:100%;max-height:none}h1{font-size:24px;font-weight:500}h4{margin-bottom:6px}</style>'
    document = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Beast workspace</title>' + style + '</head><body><main><header><h1>One companion. One message.</h1><p>Open an action, then use Back to beast. These screens come from real callback round trips in a disposable example game.</p></header>' + template + '<script>' + script + '</script></main></body></html>'
    path = output / 'beast-workspace.html'
    path.write_text(document, encoding='utf-8')
    report = dict(status='passed', message_id=8181, followups=0, deletions=0, actual_callback_transitions=transitions,
                  scope='Real Discord view/edit payloads and packaged art. Offline navigation only; no live account or mutations.')
    (output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Exported {len(data)} screens and {len(transitions)} actual callback transitions into one message.')
    return path


async def capture(path, output):
    from playwright.async_api import async_playwright
    errors, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport=dict(width=1100, height=1000))
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(path.resolve().as_uri())
        await page.wait_for_selector('#cb-beast-workspace[data-ready="true"]')
        screens = ['beast', 'care', 'care_item', 'gear', 'gear_edit', 'train', 'evolve', 'team', 'raid', 'roster']
        for width in (1100, 390, 320):
            await page.set_viewport_size(dict(width=width, height=1000))
            for screen in screens:
                await page.locator('#cj-screen').select_option(screen)
                await page.wait_for_function('Array.from(document.images).every(img=>img.complete && img.naturalWidth>0)')
                assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert await page.locator('[data-message="8181"]').count() == 1
                if screen not in {'beast', 'roster'}:
                    await page.get_by_role('button', name='Back to beast', exact=True).click()
                    assert await page.locator('#cb-beast-workspace').get_attribute('data-screen') == 'beast'
                    await page.locator('#cj-screen').select_option(screen)
                await page.screenshot(path=str(output / f'{width}-{screen}.png'), full_page=True)
                checks.append(dict(width=width, screen=screen, one_message=True, no_overflow=True, art_loaded=True))
        for route in ('care', 'gear', 'train', 'evolve', 'team'):
            await page.locator('#cj-screen').select_option('beast')
            await page.get_by_role('combobox', name='Companion actions', exact=True).select_option(route)
            assert await page.get_by_role('button', name='Back to beast', exact=True).count() == 1
            await page.get_by_role('button', name='Back to beast', exact=True).click()
            assert await page.locator('#cb-beast-workspace').get_attribute('data-screen') == 'beast'
        await browser.close()
    assert not errors, errors
    (output / 'browser-validation.json').write_text(json.dumps(dict(status='passed', javascript_errors=errors, checks=checks), indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} screen/viewport combinations and all five action/Back round trips.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/beast-workspace-preview')
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
