"""Export native authored-journal components and real equipped combat offline."""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_gameplay import interaction
from tests.test_interactions import component
from utils import adventure_content, adventure_party, battle_art, db as storage, expedition_events
from utils import expeditions, journal, journal_state, journeys, trial_combat
from utils.habitats import BIOMES


SCRIPT = r'''
(()=>{const root=document.getElementById('adventure-preview'),records=__DATA__,art=__ART__,picker=document.getElementById('screen'),stage=document.getElementById('stage'),note=document.getElementById('note');
const escape=value=>{const n=document.createElement('span');n.textContent=String(value);return n.innerHTML};
const md=value=>escape(value).replace(/\\([_~*`])/g,'$1').split('\n').map(line=>line.startsWith('### ')?'<h3>'+line.slice(4)+'</h3>':line.startsWith('## ')?'<h2>'+line.slice(3)+'</h2>':line.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')+'<br>').join('');
function draw(item,parent){if(item.type===17){for(const child of item.components)draw(child,parent)}else if(item.type===10){const text=document.createElement('div');text.className='text';text.innerHTML=md(item.content);parent.append(text)}else if(item.type===12){for(const entry of item.items){const img=document.createElement('img');img.src=art[entry.media.url.replace('attachment://','')];img.alt=entry.description||'Adventure artwork';parent.append(img)}}else if(item.type===9){const section=document.createElement('div');section.className='section';for(const child of item.components)draw(child,section);if(item.accessory&&item.accessory.media){const img=document.createElement('img');img.src=art[item.accessory.media.url.replace('attachment://','')];img.alt=item.accessory.description||'Companion';section.append(img)}parent.append(section)}else if(item.type===1){const row=document.createElement('div');row.className='row';for(const child of item.components)draw(child,row);parent.append(row)}else if(item.type===2){const b=document.createElement('button');b.textContent=item.label;b.disabled=!!item.disabled;b.dataset.style=item.style;b.onclick=()=>{note.textContent='Recorded production control. Select a saved stage above to inspect another committed state.'};parent.append(b)}else if(item.type===3){const wrap=document.createElement('div');wrap.className='select-wrap';const select=document.createElement('select');select.setAttribute('aria-label',item.placeholder);select.disabled=!!item.disabled;const placeholder=document.createElement('option');placeholder.textContent=item.placeholder;placeholder.disabled=true;placeholder.selected=true;select.append(placeholder);for(const choice of item.options){const option=document.createElement('option');option.value=choice.value;option.textContent=choice.label;option.selected=!!choice.default;option.dataset.description=choice.description||'';select.append(option)}const detail=document.createElement('div');detail.className='option-detail';detail.setAttribute('role','status');select.onchange=()=>detail.textContent=select.selectedOptions[0].dataset.description||'';wrap.append(select,detail);parent.append(wrap)}}
function show(key){if(!records[key])return;picker.value=key;root.dataset.screen=key;stage.replaceChildren();const card=document.createElement('article');card.className='message';card.dataset.message='8181';for(const item of records[key].components)draw(item,card);stage.append(card);note.textContent=records[key].label+' · saved revision '+records[key].version+' · one journal message'}
for(const [key,record] of Object.entries(records)){const option=document.createElement('option');option.value=key;option.textContent=record.label;picker.append(option)}picker.onchange=()=>show(picker.value);root.preview={show};show(Object.keys(records)[0]);root.dataset.ready='true';})();
'''


def seed_for(biome, first, second=None, *, egg=False):
    """Find a published deterministic example; production still draws its seed."""
    roots = [root['id'] for root in adventure_content.load(biome)['encounters']
             if not root['repeatable'] and not root.get('requires_flags')]
    for seed in range(200000):
        order = sorted(roots, key=lambda rid: hashlib.sha256(f'{seed}:authored-roots:{rid}'.encode()).digest())
        if order[0] != first or second is not None and order[1] != second:
            continue
        payload = dict(seed=str(seed), level=50, base_gold=100)
        expedition_events.prepare_nest(payload, biome)
        if not egg or payload['egg_offer']:
            return seed
    raise AssertionError('No deterministic example seed found.')


async def export(output):
    media = output / 'media'
    media.mkdir(parents=True, exist_ok=True)
    screens, art, uploads, events, models = {}, {}, [], [], {}
    pending, label, playback_index = 'selector', 'Choose an adventure', 0
    message = SimpleNamespace(id=8181, attachments=[], delete=AsyncMock())
    views = []

    def capture(kwargs):
        nonlocal playback_index
        panel = kwargs['view']
        for file in kwargs.get('files', kwargs.get('attachments', [])):
            if not hasattr(file, 'fp'):
                continue
            file.fp.seek(0)
            (media / file.filename).write_bytes(file.fp.read())
            file.fp.seek(0)
            art[file.filename] = 'media/' + file.filename
        trip = getattr(panel, 'trip', None)
        key, title = pending, label
        if panel.journey_playing:
            source = trip['payload']['combat']['events']
            event = source[min(playback_index, len(source) - 1)]
            playback_index += 1
            kind = event['action']['kind']
            if kind in ('enter', 'swap', 'assist'):
                key, title = kind, {'enter': 'Pack opponent enters', 'swap': 'Swap companions', 'assist': 'Reserve assist'}[kind]
            elif event['before'][1]['hp'] > 0 and event['after'][1]['hp'] <= 0:
                key, title = 'knockout', 'Opponent withdraws'
            else:
                key, title = 'action', 'Equipped combat action'
        screens[key] = dict(label=title, components=json.loads(json.dumps(panel.to_components())),
                            version=trip['version'] if trip else 0, message_id=8181,
                            playing=panel.journey_playing, characters=panel.content_length(),
                            component_count=panel.total_children_count)
        uploads.append(dict(stage=key, revision=trip['version'] if trip else 0, message_id=8181,
                            media=[f.filename for f in kwargs.get('files', kwargs.get('attachments', [])) if hasattr(f, 'filename')]))
        return message

    message.edit = AsyncMock(side_effect=lambda **kwargs: capture(kwargs))
    command = interaction()
    command.channel.id = 56666
    command.followup.send.side_effect = lambda **kwargs: capture(kwargs)
    command.channel.send.return_value = message
    command.edit_original_response.side_effect = lambda **kwargs: capture(kwargs)
    original_begin = expeditions.begin
    selected_seed = seed_for('loom', 'loom.unfinished_bridge', 'loom.starsteps', egg=True)
    rng = random.Random(73)
    source = SimpleNamespace(randint=rng.randint, choice=rng.choice, getrandbits=lambda _: selected_seed)

    async def seeded(*args, **kwargs):
        kwargs['rng'] = source
        return await original_begin(*args, **kwargs)

    def presentation_setup(presentation, realm, **kwargs):
        presentation.realm, presentation.enabled, presentation.journal = realm, True, ()
        presentation.sleep = AsyncMock()

    with tempfile.TemporaryDirectory(prefix='adventure-preview-') as directory, \
            patch.object(storage, 'DB_PATH', str(Path(directory) / 'preview.db')), \
            patch.object(expeditions, 'begin', side_effect=seeded), \
            patch.object(battle_art.BattlePresentation, '__init__', autospec=True, side_effect=presentation_setup):
        await storage.init_db()
        await storage.get_or_create_player(42, 'Adventure preview')
        await storage.update_player(42, level=50, gold=5000)
        ids = [await storage.add_beast_to_player(42, dict(storage.get_beast_data(species), level=46), starter=index == 0)
               for index, species in enumerate(('twine', 'gloop', 'barkley'))]
        for index, row_id in enumerate(ids[1:], 1):
            await adventure_party.set_slot(42, row_id, index)
        async with storage.connect() as db:
            # Deliberately prepared QA fixtures reach swaps/assists reliably;
            # these are not an unmodified balance simulation or live account.
            await db.execute('UPDATE player_beasts SET attack=320,defense=1500,speed=800,hp=1000,max_hp=1000,mana=100,max_mana=100 WHERE user_id=42')
            await db.executemany('INSERT INTO player_equipment(user_id,equipment_id,beast_row_id) VALUES(42,?,?)',
                                 [('gnome_iron_plating', ids[0]), ('kelpie_scale_mail', ids[1]), ('sprite_silk_attire', ids[2])])
            await db.commit()
        view = await journal.open_journal(command, page='adventure', realm='loom')
        views.append(view)

        async def do(action, key, title, value=None):
            nonlocal pending, label, playback_index
            pending, label, playback_index = key, title, 0
            click = component()
            click.message, click.channel = message, command.channel
            click.edit_original_response.side_effect = lambda **kwargs: capture(kwargs)
            if value is not None:
                click.data['values'] = [value]
            await view.handle(click, action)
            assert not view.notice, view.notice
            assert view.message.id == 8181
            assert click.followup.send.await_count == 0
            if view.trip:
                models[key] = deepcopy(view.trip)
                events.extend(deepcopy(view.trip['payload'].get('combat', {}).get('events', [])))

        async def pick(index, key, title):
            option = expeditions.options(view.trip)[index]
            await do('path', key, title, f"{view.trip['id']}:{view.trip['version']}:{option}")

        async def act(index, key, title):
            await do('journey_action', key, title, f"{view.trip['id']}:{view.trip['version']}:{index}")

        try:
            await do('begin', 'bridge', 'A connected authored location')
            assert view.trip['payload']['current_root'] == 'loom.unfinished_bridge'
            await pick(0, 'boon', 'A celestial defense boon')
            assert view.trip['payload']['boons']['defense'] == 5
            await pick(1, 'landing', 'The connected landing')
            await pick(0, 'pack_offer', 'Three practice challengers')
            assert view.trip['payload']['current_root'] == 'loom.starsteps'
            await pick(1, 'battle', 'Choose a move, swap or assist')
            state = view.trip['payload']['combat']
            swap = next(i for i, a in enumerate(trial_combat.legal_actions(state)) if a['kind'] == 'swap' and a['enabled'])
            await act(swap, 'battle_swapped', 'The reserve takes the lead')
            assisted = False
            for _ in range(50):
                if view.trip['status'] != 'battle':
                    break
                state = view.trip['payload']['combat']
                if state['status'] == 'between':
                    await do(f"journey_next:{view.trip['id']}:{view.trip['version']}", 'pack_between', 'The next pack opponent')
                    continue
                actions = trial_combat.legal_actions(state)
                assist = next((i for i, a in enumerate(actions) if a['kind'] == 'assist' and a['enabled']), None)
                index = assist if assist is not None and not assisted else next(i for i, a in enumerate(actions) if a['kind'] == 'move' and a['enabled'])
                await act(index, 'battle_after_action', 'Committed action and next decision')
                assisted = assisted or index == assist
            assert view.trip['status'] == 'active' and assisted
            assert view.trip['payload']['fights_won'] == 1
            pending, label = 'pack_victory', 'Pack complete; one reward bundle still pending'
            capture(dict(view=view))
            for _ in range(10):
                if view.trip['payload']['journey_status'] == 'homeward':
                    break
                choices = view.trip['payload']['page']['choices']
                index = next(i for i, c in enumerate(choices) if not c['effects'].get('battle') and not c['effects'].get('hp_cost'))
                await pick(index, 'later_discovery', 'Another authored discovery')
            assert view.trip['payload']['journey_status'] == 'homeward'
            assert view.trip['payload']['egg_offer']
            pending, label = 'homeward', 'A saved egg replaces the material bundle'
            capture(dict(view=view))
            await do('path', 'returned', 'Return to destination selection', f"{view.trip['id']}:{view.trip['version']}:return_egg")
            assert view.trip['payload']['egg_granted'] and not view.trip['payload']['material_granted']
            receipt = deepcopy(view.trip['payload'])
            await do('page:battle_log', 'log', 'Expedition notes stay in the Battle log')
            selected = view.log_entries[0]['id']
            await do('log_entry', 'recap', 'One settled journey receipt', selected)
            assert command.followup.send.await_count == 1
            command.channel.send.assert_not_awaited()
            message.delete.assert_not_awaited()

            # Every authored node uses the actual production planner/render
            # path, including maximum-size trip/revision control prefixes.
            page_checks = []
            base_state = await journal_state.snapshot(42)
            for biome in BIOMES:
                for location in adventure_content.load(biome)['encounters']:
                    for node_id in location['nodes']:
                        saved = dict(progress={location['id']: dict(node_id=node_id, completed=False, snapshot=location)},
                                     flags=[], nodes={}, recent=[])
                        payload = dict(prepared_party=deepcopy(models['bridge']['payload']['prepared_party']))
                        journeys.initialize(payload, biome, 73, payload['prepared_party'], saved)
                        trip = dict(id='f' * 32, user_id=42, status='active', version=1000000, realm=biome,
                                    companion_id=ids[0], payload=payload)
                        test_view = journal.JournalView(42, base_state, page='adventure', realm=biome)
                        test_view.trip = trip
                        test_view.render()
                        lengths = [len(option.value) for item in test_view.walk_children() if hasattr(item, 'options') for option in item.options]
                        check = dict(biome=biome, node=node_id, characters=test_view.content_length(),
                                     components=test_view.total_children_count, max_option_value=max(lengths, default=0))
                        assert check['characters'] <= 4000 and check['components'] <= 40 and check['max_option_value'] <= 100, check
                        page_checks.append(check)
                        test_view.stop()
        finally:
            for opened in views:
                opened.stop()

    # This output directory belongs to this recorder; remove only old generated
    # media that the new production recording no longer references.
    for path in media.iterdir():
        if path.is_file() and path.name not in art and path.name.startswith(('adventure-', 'journal-', 'portrait-')):
            path.unlink()
    clips = []
    for path in media.glob('*.gif'):
        if path.name not in art:
            continue
        with Image.open(path) as image:
            durations = [frame.info['duration'] for frame in ImageSequence.Iterator(image)]
            first = image.convert('RGBA')
            record = dict(file='media/' + path.name, bytes=path.stat().st_size, frames=image.n_frames,
                          milliseconds=sum(durations), durations=durations, one_shot='loop' not in image.info,
                          dimensions=list(image.size), alpha_extrema=list(first.getchannel('A').getextrema()))
            assert record['one_shot'] and record['bytes'] < battle_art.MAX_BYTES
            clips.append(record)
    selected_events = [next(event for event in events if event['action']['kind'] == kind) for kind in ('enter', 'swap', 'assist')]
    contact = Image.new('RGB', (960, 792), '#202126')
    draw = ImageDraw.Draw(contact)
    for row, event in enumerate(selected_events):
        before, action, after = trial_combat.decode_event(event)
        for column, t in enumerate((0, .5, 1)):
            frame = battle_art.frame(before, after, 'loom', action, t).resize((320, 240), Image.Resampling.NEAREST)
            contact.paste(frame, (column * 320, row * 264 + 24))
            draw.text((column * 320 + 6, row * 264 + 6), f'{action.kind.upper()} {("START", "MIDPOINT", "END")[column]}', fill='#dbdee1', font=battle_art.font(12))
    contact.save(output / 'animation-contact.png')
    report = dict(status='passed', initial_messages=1, additional_messages=0, deletions=0,
                  scope='Production JournalView callbacks and saved v3 service state in a disposable prepared QA account; real equipped battle renderer. Playback sleep skipped offline. Native Discord delivery and live balance were not tested.',
                  example_seed=selected_seed, stages=list(screens), uploads=uploads, clips=clips,
                  authored_pages=page_checks, biome_count=len({p['biome'] for p in page_checks}),
                  max_characters=max(p['characters'] for p in page_checks),
                  max_components=max(p['components'] for p in page_checks),
                  max_option_value=max(p['max_option_value'] for p in page_checks),
                  settled=dict(gold=receipt['gold'], trainer_exp=receipt['exp_granted'], material=receipt['material_granted'],
                               egg=receipt['egg_reward'], completed=receipt['completed'], total=receipt['total']))
    for name, data in [('validation', report), ('components', screens), ('saved-events', events), ('saved-states', models)]:
        (output / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    script = SCRIPT.replace('__DATA__', json.dumps(screens, ensure_ascii=False).replace('</', '<\\/')).replace('__ART__', json.dumps(art))
    html = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ChibiBeasts · Living Adventures</title><style>
*{box-sizing:border-box}body{margin:0;background:#202126;color:#dbdee1;font:14px/1.5 Arial,sans-serif}main{max-width:900px;margin:auto;padding:28px 12px}header,#toolbar,#note,footer{max-width:640px;margin:0 auto 18px}h1{font-size:26px;font-weight:500;line-height:1.2;margin:0 0 12px}header p,#note,footer{color:#b5bac1}#toolbar{display:flex;gap:10px;align-items:center}.message{max-width:640px;margin:auto;background:#2b2d31;border:1px solid #4e5058;border-left:4px solid #b7ad87;border-radius:8px;padding:12px}.message img{width:100%;display:block;image-rendering:pixelated;border-radius:4px}.text{margin:12px 0;overflow-wrap:anywhere}.text h2{font-size:20px;font-weight:500;margin:0 0 8px}.text h3{font-size:18px;font-weight:500;margin:0 0 8px}.row{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.select-wrap{width:100%;min-width:0}.option-detail{color:#b5bac1;margin:4px 0;font-size:12px;overflow-wrap:anywhere}button,select{font:inherit;border:1px solid #50535c;border-radius:4px;color:#dbdee1;background:#41434a;padding:8px 11px;max-width:100%;min-width:0}select{width:100%;background:#1e1f22}button[data-style="1"]{background:#5865f2;color:white}button[data-style="3"]{background:#278747;color:white}button:disabled{opacity:.5}button{cursor:pointer}a{color:#d8cba5}footer,#note{font-size:12px}button:focus-visible,select:focus-visible{outline:2px solid #e8cf9f;outline-offset:3px}.section{display:flex;gap:12px;align-items:start}.section>.text{flex:1}.section>img{width:80px;flex:none}@media(max-width:420px){main{padding:20px 8px}.message{padding:9px}.row{gap:5px}button{padding:8px}}
</style></head><body><main id="adventure-preview"><header><h1>Explore places that remember you.</h1><p>Authored locations, a temporary celestial boon, and one three-member team meeting a pack. Every stage below comes from the native journal's production components.</p></header><div id="toolbar"><label for="screen">Stage</label><select id="screen" aria-label="Preview stage"></select></div><div id="stage"></div><div id="note" role="status"></div><footer>Offline production recording with prepared QA stats; not a live adventure or balance simulation. <a href="validation.json">Validation</a> · <a href="animation-contact.png">Animation frames</a></footer><script>'''+script+'</script></main></body></html>'
    path = output / 'index.html'
    path.write_text(html, encoding='utf-8')
    print(f'Exported {len(screens)} stages and {len(clips)} clips; validated {len(page_checks)} authored nodes: {path}')
    return path, screens


async def capture_browser(path, output, screens):
    from playwright.async_api import async_playwright
    errors, failed, checks = [], [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport=dict(width=1100, height=1000))
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('requestfailed', lambda request: failed.append(request.url))
        await page.goto(path.resolve().as_uri())
        await page.wait_for_selector('#adventure-preview[data-ready="true"]')
        for width in (1100, 390, 320):
            await page.set_viewport_size(dict(width=width, height=1000))
            for screen in screens:
                await page.locator('#screen').select_option(screen)
                await page.wait_for_function('Array.from(document.images).every(image=>image.complete&&image.naturalWidth>0)')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert await page.locator('[data-message="8181"]').count() == 1
                if screen == 'homeward':
                    assert 'Shelter the' in await page.locator('.message').inner_text()
                if screen in ('bridge', 'boon', 'battle', 'homeward', 'recap'):
                    await page.screenshot(path=str(output / f'{width}-{screen}.png'), full_page=True)
                checks.append(dict(width=width, stage=screen, one_message=True, no_overflow=True, media_loaded=True))
        # Exercise a real serialized choice's description as well as stage switching.
        await page.locator('#screen').select_option('bridge')
        choice = page.locator('select[aria-label="Choose your next step"]')
        await choice.select_option(index=1)
        assert 'defense' in await page.locator('.option-detail').last.inner_text()
        await browser.close()
    assert not errors and not failed, dict(errors=errors, failed=failed)
    (output / 'browser-validation.json').write_text(json.dumps(dict(status='passed', javascript_errors=errors,
        missing_assets=failed, checks=checks, choice_description_interaction=True), indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} viewport/stage combinations.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'adventure-preview')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--tool-path', type=Path)
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    path, screens = await export(args.output)
    if args.capture:
        await capture_browser(path, args.output, screens)


if __name__ == '__main__':
    asyncio.run(main())
