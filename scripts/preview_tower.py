"""Record real tower callbacks, equipped animations and Back navigation offline."""
import argparse
import asyncio
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.test_gameplay import interaction
from tests.test_interactions import component
from utils import adventure_party, battle_art, db as storage, trial_combat, trial_panel, trials


RENDERER = r'''
(()=>{const root=document.getElementById('tower-preview'),records=__DATA__,art=__ART__,keys=Object.keys(records),picker=document.querySelector('#screen'),stage=document.querySelector('#stage'),note=document.querySelector('#note');let current='selector';
const labels={selector:'Choose a tower',ready:'Team ready',entry:'Enemy enters',battle:'Choose an action',swap:'Swap companions',action:'Current action',knockout:'Opponent withdraws',between:'Wave cleared',paused:'Pause safely',resumed:'Resume saved climb',result:'Retreat & bank',returned:'Back to journal'};
const escape=value=>{const node=document.createElement('span');node.textContent=String(value);return node.innerHTML};
const md=value=>escape(value).replace(/\\([_~*`])/g,'$1').split('\n').map(line=>line.startsWith('## ')?'<h2>'+line.slice(3)+'</h2>':line.startsWith('### ')?'<h3>'+line.slice(4)+'</h3>':line.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>')+'<br>').join('');
function show(key){if(!records[key])return;current=key;picker.value=key;root.dataset.screen=key;stage.replaceChildren();const card=document.createElement('article');card.className='card';card.dataset.message='8181';for(const item of records[key].components)draw(item,card);stage.append(card);note.textContent='One message · saved revision '+records[key].version+' · '+labels[key]}
function action(label){const links={'Begin tower':'ready','Next wave':'entry','Pause':'paused','Resume climb':'resumed','Retreat & bank':'result','Back to trials':'selector','Back to journal':'returned'};if(links[label])show(links[label]);else if(label.startsWith('Swap:'))show('swap');else if(current==='battle'||current==='resumed')show('knockout');else note.textContent='Saved production controls, exported from the example climb. Choose a stage above to inspect it.'}
function draw(item,parent){if(item.type===17){for(const child of item.components)draw(child,parent)}else if(item.type===10){const text=document.createElement('div');text.className='text';text.innerHTML=md(item.content);parent.append(text)}else if(item.type===12){for(const entry of item.items){const image=document.createElement('img');image.src=art[entry.media.url.replace('attachment://','')];image.alt=entry.description||'Tower Trials';parent.append(image)}}else if(item.type===1){const row=document.createElement('div');row.className='row';for(const child of item.components)draw(child,row);parent.append(row)}else if(item.type===2){const button=document.createElement('button');button.textContent=item.label;button.disabled=!!item.disabled;button.dataset.style=item.style;button.onclick=()=>action(item.label);parent.append(button)}else if(item.type===3){const select=document.createElement('select');select.setAttribute('aria-label',item.placeholder);select.disabled=!!item.disabled;const placeholder=document.createElement('option');placeholder.textContent=item.placeholder;placeholder.disabled=true;placeholder.selected=true;select.append(placeholder);for(const choice of item.options){const option=document.createElement('option');option.value=choice.value;option.textContent=choice.label;option.selected=!!choice.default;select.append(option)}parent.append(select)}}
for(const key of keys){const option=document.createElement('option');option.value=key;option.textContent=labels[key];picker.append(option)}picker.onchange=()=>show(picker.value);root.preview={show};show(current);root.dataset.ready='true';})();
'''


async def export(output):
    media = output / 'media'
    media.mkdir(parents=True, exist_ok=True)
    screens, art, uploads, events = {}, {}, [], []
    pending, view, playback_index = 'selector', None, 0
    message = SimpleNamespace(id=8181, attachments=[], delete=AsyncMock())
    def capture(kwargs):
        nonlocal playback_index
        panel = kwargs['view']
        for file in kwargs.get('files', kwargs.get('attachments', [])):
            file.fp.seek(0)
            (media / file.filename).write_bytes(file.fp.read())
            file.fp.seek(0)
            art[file.filename] = 'media/' + file.filename
        components = json.loads(json.dumps(panel.to_components()))
        version = getattr(panel, 'version', None)
        label = pending
        if isinstance(panel, trial_panel.TrialPanel) and panel.playing:
            source = panel.payload.get('events', ())
            event = source[min(playback_index, len(source) - 1)] if source else None
            playback_index += 1
            kind = event['action']['kind'] if event else ''
            if kind == 'enter':
                label = 'entry'
            elif kind == 'swap':
                label = 'swap'
            elif event and event['before'][1]['hp'] > 0 and event['after'][1]['hp'] <= 0:
                label = 'knockout'
            else:
                label = 'action'
        elif isinstance(panel, trial_panel.TrialPanel) and pending in {'entry', 'swap', 'knockout'}:
            label = 'battle' if panel.run['status'] == 'fighting' else 'between'
        screens[label] = dict(components=components, version=version[1] if version else 0,
                              message_id=8181, playing=getattr(panel, 'playing', False))
        uploads.append(dict(stage=label, version=version, message_id=8181,
                            media=[file.filename for file in kwargs.get('files', kwargs.get('attachments', []))]))
        return message
    message.edit = AsyncMock(side_effect=lambda **kwargs: capture(kwargs))
    command = interaction()
    command.channel.id = 55555
    command.followup.send.side_effect = lambda **kwargs: capture(kwargs)
    command.channel.send.return_value = message
    original_begin = trials.begin
    async def seeded(*args, **kwargs):
        return await original_begin(*args, **kwargs, seed=73)
    with tempfile.TemporaryDirectory(prefix='tower-preview-') as directory, \
            patch.object(storage, 'DB_PATH', str(Path(directory) / 'preview.db')), \
            patch.object(trials, 'begin', side_effect=seeded), \
            patch.object(battle_art.BattlePresentation, '__init__', autospec=True) as setup:
        # Keep the real bounded art renderer while skipping client playback
        # waits in this offline recording. Discord uses the normal sleep.
        def initialize(presentation, realm, **kwargs):
            presentation.realm, presentation.enabled, presentation.journal = realm, True, ()
            presentation.sleep = AsyncMock()
        setup.side_effect = initialize
        await storage.init_db()
        await storage.get_or_create_player(42, 'Tower preview')
        await storage.update_player(42, level=12, gold=5000)
        async with storage.connect() as db:
            await db.execute("INSERT INTO player_perks(user_id,perk_id,equipped) VALUES(42,'chronos_hourglass',1)")
            await db.commit()
        ids = [await storage.add_beast_to_player(42, dict(storage.get_beast_data(species), level=18), starter=index == 0)
               for index, species in enumerate(('twine', 'gloop', 'barkley'))]
        await adventure_party.set_slot(42, ids[1], 1)
        await adventure_party.set_slot(42, ids[2], 2)
        async with storage.connect() as db:
            await db.executemany('INSERT INTO player_equipment(user_id,equipment_id,beast_row_id) VALUES(42,?,?)',
                                 [('gnome_iron_plating', ids[0]), ('kelpie_scale_mail', ids[1]), ('sprite_silk_attire', ids[2])])
            await db.commit()
        await storage.add_item(42, 'pixie_dust_potion', 3)
        view = await trial_panel.open_trials(command, destination='woods_deep')
        assert view is not None
        cooldown = next(row['effective_cooldown_seconds'] for row in view.board['destinations'] if row['key'] == 'woods_deep')
        assert cooldown == 24 * 60
        async def press(label, screen):
            nonlocal pending, playback_index
            pending, playback_index = screen, 0
            click = component()
            click.message, click.channel = message, command.channel
            click.edit_original_response.side_effect = lambda **kwargs: capture(kwargs)
            button = next(item for item in view.walk_children() if isinstance(item, __import__('discord').ui.Button) and item.label == label)
            await button.callback(click)
            click.followup.send.assert_not_awaited()
            assert view.message.id == 8181
            return click
        try:
            await press('Begin tower', 'ready')
            await press('Next wave', 'entry')
            first_events = json.loads(json.dumps(view.payload['events']))
            events.extend(first_events)
            screens['battle'] = dict(components=json.loads(json.dumps(view.to_components())), version=view.run['version'], message_id=8181, playing=False)
            await press('Swap: Gloop', 'swap')
            events.extend(json.loads(json.dumps(view.payload['events'])))
            for _ in range(20):
                if view.run['status'] != 'fighting':
                    break
                active = trial_combat.active(view.payload)
                await press(active['moves'][0], 'knockout')
                events.extend(json.loads(json.dumps(view.payload['events'])))
            assert view.run['status'] == 'between' and view.payload['cleared'] == 1
            screens['between'] = dict(components=json.loads(json.dumps(view.to_components())), version=view.run['version'], message_id=8181, playing=False)
            await press('Pause', 'paused')
            await press('Resume climb', 'resumed')
            bank_quote = json.loads(json.dumps(view.payload['bank_quote']))
            full_quote = json.loads(json.dumps(view.payload['reward_preview']))
            await press('Retreat & bank', 'result')
            settlement = view.payload['settlement']
            assert (bank_quote['gold'], bank_quote['trainer_xp']) == (settlement['gold'], settlement['trainer_xp'])
            await press('Back to journal', 'returned')
            command.channel.send.assert_not_awaited()
            message.delete.assert_not_awaited()
            assert command.followup.send.await_count == 1
            assert all(row['message_id'] == 8181 for row in uploads)
        finally:
            view.cancel_timer()
            view.stop()
            trial_panel._panels.pop(view.key, None)
    entry = next(event for event in events if event['action']['kind'] == 'enter')
    before, action, after = trial_combat.decode_event(entry)
    contact = Image.new('RGB', (960, 290), '#102b2c')
    draw = ImageDraw.Draw(contact)
    for index, t in enumerate((0, .5, 1)):
        image = battle_art.frame(before, after, 'woods_deep', action, t)
        contact.paste(image.resize((320, 264), Image.Resampling.NEAREST), (index * 320, 24))
        draw.text((index * 320 + 8, 6), ('ENTRY START', 'ENTRY MIDPOINT', 'ENTRY END')[index], fill='#ede4cd', font=battle_art.font(12))
    contact.save(output / 'entry-contact.png')
    clips = []
    # Remove only obsolete generated media from an earlier export of this same
    # gallery, keeping its evidence tied to the freshly recorded callbacks.
    for path in media.iterdir():
        if path.is_file() and path.name not in art and path.name.startswith(('trial-', 'exploration-team-', 'journal-')):
            path.unlink()
    for path in media.glob('*.gif'):
        with Image.open(path) as image:
            record = dict(file=str(path.relative_to(output)).replace('\\', '/'), bytes=path.stat().st_size,
                          frames=image.n_frames, one_shot='loop' not in image.info,
                          milliseconds=sum(frame.info['duration'] for frame in ImageSequence.Iterator(image)))
            assert record['one_shot'] and record['bytes'] < battle_art.MAX_BYTES
            clips.append(record)
    (output / 'components.json').write_text(json.dumps(screens, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'saved-events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report = dict(status='passed', message_id=8181, initial_messages=1, additional_messages=0, deletions=0,
                  stages=list(screens), uploads=uploads, clips=clips, settlement=settlement,
                  hourglass_equipped=True, effective_cooldown_seconds=cooldown,
                  bank_quote=bank_quote, full_reward_quote=full_quote, exact_bank_quote_matches_receipt=True,
                  scope='Actual TrialPanel callbacks, committed service state and equipped battle media from a disposable level18 example team in Moonveil Marsh. Playback sleep skipped offline; no live account or balance claims.')
    (output / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    script = RENDERER.replace('__DATA__', json.dumps(screens, ensure_ascii=False).replace('</', '<\\/')).replace('__ART__', json.dumps(art))
    html = '''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ChibiBeasts · Tower Trials</title><style>
*{box-sizing:border-box}body{margin:0;background:#202126;color:#dbdee1;font:14px/1.5 Arial,sans-serif}main{max-width:900px;margin:auto;padding:30px 12px}header,#toolbar,#note{max-width:640px;margin:0 auto 18px}h1{font-size:28px;font-weight:500;line-height:1.2;margin:0 0 12px}header p,#note{color:#b5bac1}#toolbar{display:flex;gap:10px;align-items:center}.card{max-width:640px;margin:auto;background:#2b2d31;border:1px solid #4e5058;border-left:4px solid #789f88;border-radius:8px;padding:12px}.card img{width:100%;display:block;image-rendering:pixelated;border-radius:4px}.text{margin:12px 0;overflow-wrap:anywhere}.text h2{font-size:20px;font-weight:500;margin:0 0 8px}.text h3{font-size:18px;font-weight:500;margin:0 0 8px}.text strong{font-weight:600}.row{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}button,select{font:inherit;border:1px solid #50535c;border-radius:4px;color:#dbdee1;background:#41434a;padding:8px 11px;max-width:100%;min-width:0}select{width:100%;background:#1e1f22}button[data-style="1"]{background:#5865f2;color:white}button[data-style="3"]{background:#278747;color:white}button:disabled{opacity:.5}button{cursor:pointer}a{color:#d8cba5}footer{max-width:640px;margin:22px auto;color:#b5bac1;font-size:12px}#note{padding-top:12px;font-size:12px}button:focus-visible,select:focus-visible{outline:2px solid #e8cf9f;outline-offset:3px}@media(max-width:420px){main{padding:22px 8px}h1{font-size:24px}.card{padding:9px}button{padding:8px}.row{gap:5px}}
</style></head><body><main id="tower-preview"><header><h1>One team. One climb.<br>One message.</h1><p>Inspect the actual tower panel and a committed wave: equipped entry, actions, retreat, and the same-message return to your journal.</p></header><div id="toolbar"><label for="screen">Stage</label><select id="screen" aria-label="Preview stage"></select></div><div id="stage"></div><div id="note" role="status"></div><footer>Production callback recording with a prepared example team. This is an offline review, not a live climb. <a href="validation.json">Validation</a> · <a href="entry-contact.png">Entrance frames</a></footer><script>'''+script+'</script></main></body></html>'
    path = output / 'index.html'
    path.write_text(html, encoding='utf-8')
    print(f'Exported {len(screens)} stages, {len(clips)} one-shot clips, one message: {path}')
    return path, screens


async def capture(path, output, screens):
    from playwright.async_api import async_playwright
    errors, failed, checks = [], [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport=dict(width=1100, height=1000))
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('requestfailed', lambda request: failed.append(request.url))
        await page.goto(path.resolve().as_uri())
        await page.wait_for_selector('#tower-preview[data-ready="true"]')
        for width in (1100, 390, 320):
            await page.set_viewport_size(dict(width=width, height=1000))
            for screen in screens:
                await page.locator('#screen').select_option(screen)
                await page.wait_for_function('Array.from(document.images).every(image=>image.complete&&image.naturalWidth>0)')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert await page.locator('[data-message="8181"]').count() == 1
                if screen == 'selector':
                    assert '24 minute cooldown' in await page.locator('.card').inner_text()
                if screen == 'between':
                    assert 'Bank now' in await page.locator('.card').inner_text()
                if screen in ('selector', 'battle', 'between', 'result'):
                    await page.screenshot(path=str(output / f'{width}-{screen}.png'), full_page=True)
                checks.append(dict(width=width, stage=screen, one_message=True, no_overflow=True, media_loaded=True))
        await browser.close()
    assert not errors, errors
    assert not failed, failed
    (output / 'browser-validation.json').write_text(json.dumps(dict(status='passed', javascript_errors=errors, missing_assets=failed, checks=checks), indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} stage/viewport combinations.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'tower-preview')
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
