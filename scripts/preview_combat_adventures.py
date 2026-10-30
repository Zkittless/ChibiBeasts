"""Record combat-first adventures through production Discord journal callbacks."""
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

import discord
from PIL import Image, ImageDraw, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cogs.hatch import Hatch
from scripts.preview_living_adventures import SCRIPT
from tests.test_gameplay import interaction
from tests.test_interactions import component
from utils import adventure_content, adventure_party, battle_art, db as storage
from utils import expeditions, habitats, journal_adventure, journeys, trial_combat


CSS = '''
*{box-sizing:border-box}body{margin:0;background:#202126;color:#dbdee1;font:14px/1.5 Arial,sans-serif}
main{max-width:900px;margin:auto;padding:28px 12px}header,#toolbar,#note,footer{max-width:640px;margin:0 auto 18px}
h1{font-size:26px;font-weight:500;line-height:1.2;margin:0 0 12px}header p,#note,footer{color:#b5bac1}
#toolbar{display:flex;gap:10px;align-items:center}.message{max-width:640px;margin:auto;background:#2b2d31;border:1px solid #4e5058;border-left:4px solid #b7ad87;border-radius:8px;padding:12px}
.message img{width:100%;display:block;image-rendering:pixelated;border-radius:4px}.text{margin:12px 0;overflow-wrap:anywhere}
.text h2{font-size:20px;font-weight:500;margin:0 0 8px}.text h3{font-size:18px;font-weight:500;margin:0 0 8px}
.row{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}.select-wrap{width:100%;min-width:0}
.option-detail{color:#b5bac1;margin:4px 0;font-size:12px;overflow-wrap:anywhere}
button,select{font:inherit;border:1px solid #50535c;border-radius:4px;color:#dbdee1;background:#41434a;padding:8px 11px;max-width:100%;min-width:0}
select{width:100%;background:#1e1f22}button[data-style="1"]{background:#5865f2;color:white}button[data-style="3"]{background:#278747;color:white}
button:disabled{opacity:.5}button{cursor:pointer}a{color:#d8cba5}footer,#note{font-size:12px}
button:focus-visible,select:focus-visible{outline:2px solid #e8cf9f;outline-offset:3px}.section{display:flex;gap:12px;align-items:center}.section>.text{flex:1;min-width:0}.section>img{width:80px;flex:none}.section>button{max-width:55%;flex-shrink:0;white-space:normal}
@media(max-width:420px){main{padding:20px 8px}.message{padding:9px}.row{gap:5px}button{padding:8px}}
'''


def example_seed():
    """Show a hostile opener followed by the one designated recruitable beat."""
    for seed in range(10000):
        payload = {'seed': str(seed)}
        journeys.configure_combat_route(payload, 'woods')
        if payload['recruitable_beat'] == 1:
            return seed
    raise AssertionError('No deterministic example seed found.')


def text_of(view):
    return '\n'.join(item.content for item in view.walk_children()
                     if isinstance(item, discord.ui.TextDisplay))


async def export(output):
    source_paths = sorted(adventure_content.CONTENT_ROOT.glob('*.json'))
    source_paths += [ROOT / 'utils' / (name + '.py') for name in
                     ('adventure_content', 'journeys', 'journey_service', 'journey_combat',
                      'expeditions', 'habitats', 'journal', 'journal_adventure', 'journal_team_art',
                      'trial_combat', 'team_combat', 'battle_art')]
    source_paths += [ROOT / 'cogs' / 'hatch.py', ROOT / 'scripts' / 'preview_living_adventures.py', Path(__file__).resolve()]
    source_hashes = {str(path.relative_to(ROOT)).replace('\\', '/'):
                     hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths}
    media = output / 'media'
    media.mkdir(parents=True, exist_ok=True)
    screens, art, uploads, events, models = {}, {}, [], [], {}
    pending, label, playback_index = 'selector', 'Fresh /explore destination selection', 0
    message = SimpleNamespace(id=8181, attachments=[], delete=AsyncMock())
    opened = []

    def capture(kwargs):
        nonlocal playback_index
        view = kwargs['view']
        for file in kwargs.get('files', kwargs.get('attachments', [])):
            if not hasattr(file, 'fp'):
                continue
            file.fp.seek(0)
            (media / file.filename).write_bytes(file.fp.read())
            file.fp.seek(0)
            art[file.filename] = 'media/' + file.filename
        trip = getattr(view, 'trip', None)
        key, title = pending, label
        if view.journey_playing:
            source = trip['payload']['combat']['events']
            event = source[min(playback_index, len(source) - 1)]
            playback_index += 1
            kind = event['action']['kind']
            if kind in ('swap', 'assist'):
                key, title = kind, {'swap': 'An equipped reserve swaps into battle',
                                    'assist': 'A reserve assists the active companion'}[kind]
            elif kind == 'enter' and trip['payload']['combat']['wave_index'] > 0:
                key, title = 'pack_entry', 'Hostile pack member enters; cannot be recruited'
            elif event['before'][1]['hp'] > 0 and event['after'][1]['hp'] <= 0:
                key, title = 'knockout', 'The defeated opponent withdraws'
            else:
                # Keep every generated clip, but the gallery selects only
                # meaningful committed states instead of every animation frame.
                uploads.append(dict(stage='playback', revision=trip['version'], message_id=8181))
                return message
        elif key == 'battle_action':
            # Ordinary turns are already represented by the selected battles.
            uploads.append(dict(stage=key, revision=trip['version'], message_id=8181))
            return message
        state = trip['payload'].get('combat') if trip else None
        screens[key] = dict(label=title, components=json.loads(json.dumps(view.to_components())),
                            version=trip['version'] if trip else 0, message_id=8181,
                            playing=view.journey_playing, characters=view.content_length(),
                            component_count=view.total_children_count,
                            recruitable=bool(trip and trip['payload'].get('capture_fight')
                                             and state and state.get('wave_index', 0) == 0),
                            text=text_of(view))
        uploads.append(dict(stage=key, revision=trip['version'] if trip else 0, message_id=8181,
                            media=[file.filename for file in kwargs.get('files', kwargs.get('attachments', []))
                                   if hasattr(file, 'filename')]))
        return message

    message.edit = AsyncMock(side_effect=lambda **kwargs: capture(kwargs))
    command = interaction()
    command.channel.id = 56667
    command.followup.send.side_effect = lambda **kwargs: capture(kwargs)
    command.channel.send.return_value = message
    command.edit_original_response.side_effect = lambda **kwargs: capture(kwargs)
    original_begin = expeditions.begin
    selected_seed = example_seed()
    rng = random.Random(73)
    source = SimpleNamespace(randint=rng.randint, choice=rng.choice, getrandbits=lambda _: selected_seed)

    async def seeded(*args, **kwargs):
        kwargs['rng'] = source
        return await original_begin(*args, **kwargs)

    def presentation_setup(presentation, realm, **kwargs):
        presentation.realm, presentation.enabled, presentation.journal = realm, True, ()
        presentation.sleep = AsyncMock()

    with tempfile.TemporaryDirectory(prefix='combat-adventure-preview-') as directory, \
            patch.object(storage, 'DB_PATH', str(Path(directory) / 'preview.db')), \
            patch.object(expeditions, 'begin', side_effect=seeded), \
            patch.object(habitats, 'random', random.Random(73)), \
            patch.object(battle_art.BattlePresentation, '__init__', autospec=True, side_effect=presentation_setup):
        await storage.init_db()
        await storage.get_or_create_player(42, 'Combat preview')
        await storage.update_player(42, level=5, gold=5000)
        ids = [await storage.add_beast_to_player(42, dict(storage.get_beast_data(species), level=5), starter=index == 0)
               for index, species in enumerate(('twine', 'gloop', 'barkley'))]
        for index, row_id in enumerate(ids[1:], 1):
            await adventure_party.set_slot(42, row_id, index)
        async with storage.connect() as db:
            # Prepared QA account: durable companions make swaps and assists
            # reproducible. This recording is not a live balance simulation.
            await db.execute('UPDATE player_beasts SET attack=18,defense=1500,speed=800,hp=1000,max_hp=1000,mana=100,max_mana=100 WHERE user_id=42')
            await db.executemany('INSERT INTO player_equipment(user_id,equipment_id,beast_row_id) VALUES(42,?,?)',
                                 [('gnome_iron_plating', ids[0]), ('kelpie_scale_mail', ids[1]), ('sprite_silk_attire', ids[2])])
            await db.commit()
        # No destination, expedition compatibility argument, or pacing override:
        # this is the same fresh default /explore callback that players use.
        view = await Hatch.explore.callback(Hatch(None), command)
        opened.append(view)
        assert view.trip is None and view.realm == 'woods'
        assert (await expeditions.energy(42)).charges == 1

        async def do(control, key, title, value=None):
            nonlocal pending, label, playback_index
            pending, label, playback_index = key, title, 0
            click = component()
            click.message, click.channel = message, command.channel
            click.edit_original_response.side_effect = lambda **kwargs: capture(kwargs)
            if value is not None:
                click.data['values'] = [value]
            await control.callback(click)
            assert not view.notice, view.notice
            assert view.message.id == 8181
            assert click.followup.send.await_count == 0
            if view.trip:
                models[key] = deepcopy(view.trip)
                events.extend(deepcopy(view.trip['payload'].get('combat', {}).get('events', [])))

        def button(prefix):
            return next(item for item in view.walk_children() if isinstance(item, discord.ui.Button)
                        and item.label.startswith(prefix))

        async def path(value, key, title):
            index = next(index for index, choice in enumerate(expeditions.presentation(view.trip)['choices'])
                         if choice['value'] == value)
            route = f":path:{view.trip['id']}:{view.trip['version']}:{index}:"
            control = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button)
                           and route in item.custom_id)
            await do(control, key, title)

        async def act(index, key='battle_action', title='Committed battle action and next decision'):
            route = f":journey_act:{view.trip['id']}:{view.trip['version']}:{index}:"
            control = next(item for item in view.walk_children() if isinstance(item, discord.ui.Button)
                           and route in item.custom_id)
            await do(control, key, title)

        async def win(*, demonstrate=False):
            assisted, swaps = False, 0
            for _ in range(55):
                if view.trip['status'] != 'battle':
                    return assisted
                state = view.trip['payload']['combat']
                if state['status'] == 'between':
                    await do(button('Next opponent'), 'pack_hostile', 'Remaining pack beast cannot be recruited')
                    assert not ('Can join your team' in text_of(view))
                    continue
                actions = trial_combat.legal_actions(state)
                assist = next((i for i, action in enumerate(actions) if action['kind'] == 'assist' and action['enabled']), None)
                swap = next((i for i, action in enumerate(actions) if action['kind'] == 'swap' and action['enabled']), None)
                if demonstrate and not assisted and assist is not None:
                    index, assisted = assist, True
                elif demonstrate and not assisted and swap is not None and swaps < 1:
                    index, swaps = swap, swaps + 1
                else:
                    index = next(i for i, action in enumerate(actions) if action['kind'] == 'move' and action['enabled'])
                await act(index)
            raise AssertionError('Prepared production combat did not resolve.')

        try:
            await do(button('Start adventure'), 'battle', 'Start adventure opens directly in combat')
            assert view.trip['status'] == 'battle'
            assert view.trip['payload']['pacing_version'] == 1
            assert view.trip['payload']['battle_target'] == 2
            assert view.trip['payload']['recruitable_beat'] == 1
            assert not view.trip['payload']['capture_fight']
            assert 'Hostile encounter' in text_of(view) and 'Cannot be recruited' in text_of(view)
            assert (await expeditions.energy(42)).charges == 0
            assert await win(demonstrate=True), 'The recorded route must show a real reserve assist.'
            assert view.trip['payload']['battles_completed'] == 1
            assert view.trip['status'] == 'active'
            pending, label = 'interlude', 'One short authored decision between battles'
            capture(dict(view=view))
            models['interlude'] = deepcopy(view.trip)
            lead = journal_adventure.scene_lead(expeditions.presentation(view.trip)['body'])
            assert len(lead) <= journal_adventure.LEAD_LIMIT
            assert not any(item.label in ('Read more', 'Hide details') for item in view.walk_children()
                           if isinstance(item, discord.ui.Button))
            assert not any(item is view.path_select for item in view.walk_children())
            choices = view.trip['payload']['page']['choices']
            safe = next(choice['value'] for choice in choices if not choice.get('effects', {}).get('battle')
                        and not choice.get('effects', {}).get('hp_cost'))
            await path(safe, 'pack_ready', 'The next required encounter is ready')
            # Some discoveries choose the run's one material before the next
            # battle. It is still a real persisted production picker.
            if view.trip['payload']['journey_status'] == 'material':
                await path(expeditions.options(view.trip)[0], 'pack_ready', 'One material choice; no additional reward bundle')
            if view.trip['status'] == 'active':
                await path(expeditions.options(view.trip)[0], 'pack_recruitable', 'Designated wild pack leader: one recruitment chance')
            assert view.trip['status'] == 'battle'
            assert view.trip['payload']['capture_fight']
            assert 'Can join your team after victory' in text_of(view)
            assert view.trip['payload']['combat']['plan']['total'] == 2
            assert await storage.get_player_beasts(42) and len(await storage.get_player_beasts(42)) == 3
            await win()
            assert view.trip['payload']['journey_status'] == 'homeward'
            assert view.trip['payload']['battles_completed'] == 2
            assert view.trip['payload']['completed'] == view.trip['payload']['total']
            pending, label = 'homeward', 'Two battles cleared; one earned bundle to bank'
            capture(dict(view=view))
            models['homeward'] = deepcopy(view.trip)
            await path('return', 'returned', 'Return and bank one adventure reward bundle')
            receipt = deepcopy(view.trip['payload'])
            assert view.trip['status'] == 'complete'
            assert receipt['material_granted'] and not receipt.get('egg_granted')
            assert len(await storage.get_player_beasts(42)) <= 4
            assert command.followup.send.await_count == 1
            command.channel.send.assert_not_awaited()
            message.delete.assert_not_awaited()
        finally:
            for view in opened:
                view.stop()

    # Keep the media used by the selected production stages. The real callback
    # recording also uploaded intermediate actions, which need not bloat the
    # review artifact after their saved events have been retained as JSON.
    serialized_screens = json.dumps(screens)
    art = {name: path for name, path in art.items() if 'attachment://' + name in serialized_screens}
    # Only prune this recorder's generated media if it is no longer referenced.
    for path in media.iterdir():
        if path.is_file() and path.name not in art and path.name.startswith(('adventure-', 'journal-', 'portrait-')):
            path.unlink()
    clips = []
    for path in media.glob('*.gif'):
        if path.name not in art:
            continue
        with Image.open(path) as image:
            durations = [frame.info['duration'] for frame in ImageSequence.Iterator(image)]
            distinct = len({hashlib.sha256(frame.convert('RGBA').tobytes()).digest()
                            for frame in ImageSequence.Iterator(image)})
            clip = dict(file='media/' + path.name, bytes=path.stat().st_size, frames=image.n_frames,
                        milliseconds=sum(durations), durations=durations, one_shot='loop' not in image.info,
                        dimensions=list(image.size), distinct_frames=distinct)
            assert clip['one_shot'] and clip['bytes'] < battle_art.MAX_BYTES and distinct > 1
            assert all(duration > 0 for duration in durations)
            clips.append(clip)
    selected_events = [next(event for event in events if event['action']['kind'] == kind)
                       for kind in ('enter', 'swap', 'assist')]
    contact = Image.new('RGB', (960, 792), '#202126')
    draw = ImageDraw.Draw(contact)
    for row, event in enumerate(selected_events):
        before, action, after = trial_combat.decode_event(event)
        for column, t in enumerate((0, .5, 1)):
            frame = battle_art.frame(before, after, 'woods', action, t).resize((320, 240), Image.Resampling.NEAREST)
            contact.paste(frame, (column * 320, row * 264 + 24))
            draw.text((column * 320 + 6, row * 264 + 6),
                      f'{action.kind.upper()} {("START", "MIDPOINT", "END")[column]}',
                      fill='#dbdee1', font=battle_art.font(12))
    contact.save(output / 'animation-contact.png')
    for key, screen in screens.items():
        assert screen['characters'] <= 4000 and screen['component_count'] <= 40, (key, screen)
        for component_group in screen['components']:
            # The serialized payload is inspected recursively because select
            # menus are nested under V2 containers and action rows.
            pending_components = [component_group]
            while pending_components:
                item = pending_components.pop()
                pending_components.extend(item.get('components', ()))
                if item.get('accessory'):
                    pending_components.append(item['accessory'])
                assert all(len(option['value']) <= 100 for option in item.get('options', ())), key
                assert len(item.get('custom_id', '')) <= 100, key
    assert all(hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
               for path, digest in source_hashes.items()), 'Source changed while recording; regenerate after edits finish.'
    report = dict(status='passed', initial_messages=1, additional_messages=0, deletions=0,
                  scope='Actual default Hatch.explore, rendered component callbacks and saved v3 service state in a disposable prepared QA account; real equipped animation renderer. Playback sleep skipped. Native Discord delivery and live balance were not tested.',
                  example_seed=selected_seed, habitat_seed=73, source_sha256=source_hashes,
                  stages=list(screens), uploads=uploads,
                  clips=clips, short_interlude_characters=len(lead), direct_action_buttons=True,
                  inline_choice_effects=True, disclosure_buttons=False,
                  max_characters=max(screen['characters'] for screen in screens.values()),
                  max_components=max(screen['component_count'] for screen in screens.values()),
                  first_battle_recruitable=False, pack_leader_recruitable=True, pack_filler_recruitable=False,
                  settled=dict(gold=receipt['gold'], trainer_exp=receipt['exp_granted'],
                               material=receipt['material_granted'], caught=receipt['caught'],
                               battles=receipt['battles_completed'], target=receipt['battle_target'],
                               completed=receipt['completed'], total=receipt['total']))
    for name, data in [('validation', report), ('components', screens), ('saved-events', events), ('saved-states', models)]:
        (output / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    final = dict(status='passed', source_hashes_current=True, source_sha256=source_hashes,
                 contact_sheet_sha256=hashlib.sha256((output / 'animation-contact.png').read_bytes()).hexdigest(),
                 clips=clips)
    (output / 'final-validation.json').write_text(json.dumps(final, indent=2) + '\n', encoding='utf-8')
    script = SCRIPT.replace('__DATA__', json.dumps(screens, ensure_ascii=False).replace('</', '<\\/')).replace('__ART__', json.dumps(art))
    script = script.replace('parent.append(section)}else if(item.type===1)',
                            'if(item.accessory&&item.accessory.type===2)draw(item.accessory,section);parent.append(section)}else if(item.type===1)')
    # These preview controls switch between real captured production states;
    # they do not send commands or mutate a live player's account.
    script += '''document.addEventListener('click',e=>{if(e.target.tagName!=='BUTTON')return;const target={
      'Start adventure · 1 charge':'battle',
      'Next opponent':'pack_hostile'}[e.target.textContent];if(target)document.getElementById('adventure-preview').preview.show(target)});'''
    html = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ChibiBeasts · Adventure Buttons</title><style>' + CSS + '</style></head><body><main id="adventure-preview"><header><h1>Direct buttons. Prompts in the adventure.</h1><p>Moves, swaps and assists are back on buttons. Short prompts and choice effects appear together; the latest choice result leads into the next encounter.</p></header><div id="toolbar"><label for="screen">Stage</label><select id="screen" aria-label="Preview stage"></select></div><div id="stage"></div><div id="note" role="status"></div><footer>Offline recording of production controls with prepared QA companions. <a href="validation.json">Validation</a> · <a href="animation-contact.png">Animation frames</a></footer><script>' + script + '</script></main></body></html>'
    path = output / 'index.html'
    path.write_text(html, encoding='utf-8')
    print(f'Exported {len(screens)} stages and {len(clips)} real clips: {path}')
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
                text = await page.locator('.message').inner_text()
                if screen in ('battle', 'pack_hostile'):
                    assert 'Cannot be recruited' in text
                if screen == 'pack_recruitable':
                    assert 'Can join your team after victory' in text
                assert not await page.get_by_role('button', name='Read more', exact=True).count()
                assert not await page.get_by_role('button', name='Hide details', exact=True).count()
                assert not await page.locator('select[aria-label="Choose your next step"],select[aria-label="Choose a move, swap or assist"]').count()
                if screen in ('battle', 'interlude', 'pack_ready', 'pack_recruitable', 'pack_hostile', 'homeward'):
                    await page.screenshot(path=str(output / f'{width}-{screen}.png'), full_page=True)
                checks.append(dict(width=width, stage=screen, one_message=True, no_overflow=True, media_loaded=True))
        await page.locator('#screen').select_option('interlude')
        assert await page.locator('.section>button').count() >= 2
        assert await page.locator('.section>.text').count() >= 2
        await browser.close()
    assert not errors and not failed, dict(errors=errors, failed=failed)
    (output / 'browser-validation.json').write_text(json.dumps(dict(status='passed', javascript_errors=errors,
        missing_assets=failed, checks=checks, disclosure_buttons=False,
        direct_action_buttons=True, inline_choice_effects=True), indent=2) + '\n', encoding='utf-8')
    final = json.loads((output / 'final-validation.json').read_text(encoding='utf-8'))
    final['browser_checks'] = len(checks)
    (output / 'final-validation.json').write_text(json.dumps(final, indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} viewport/stage combinations.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'combat-adventure-preview')
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
