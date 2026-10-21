"""Export production biome artwork, battle clips and saved crossing pages offline.

python scripts/preview_biomes.py --capture --tool-path ../.tools/artwork-preview-packages
No Discord connection, game database or image-generation API is used.
"""
import argparse
import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch

from PIL import Image, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cogs.battle import build_pve_beast_state
from utils import battle_art as art, battle_scene, combat, db as storage, expedition_events, journal_team_art
from utils.habitats import BIOMES, REGIONAL_MATERIALS, biome_key

SPECIES = ('twine', 'gloop', 'barkley')
ARMOR = ('gnome_iron_plating', 'kelpie_scale_mail', 'sprite_silk_attire')


def battle_sample(level):
    party = [build_pve_beast_state(storage.get_beast_data(species), level) for species in SPECIES]
    for index, (member, armor) in enumerate(zip(party, ARMOR)):
        member.update(id=101 + index, armor_id=armor, _assist_meter=0)
    enemy = build_pve_beast_state(storage.get_beast_data('slime'), level)
    enemy['hp'] = 1  # The last exchange of a prepared battle, not a new encounter rule.
    capture = lambda: art.snapshot([dict(party[0], _party_states=party), enemy])
    before = capture()
    log = []
    move = party[0]['moves'][0]
    with patch('utils.combat.random.random', return_value=.99):
        damage = combat.resolve_move(party[0], enemy, move, False, log)
    assert enemy['hp'] == 0
    action = art.Action(0, move, damage=damage, journal=tuple(log[-2:]))
    rows = [dict(beast_id=member['beast_id'], armor_id=member['armor_id'], hp=member['hp']) for member in party]
    return before, capture(), action, rows, log


def crossing_sample(key, level):
    material = REGIONAL_MATERIALS[biome_key(key)][0]
    payload = dict(gold=100, exp=100, base_exp=100, level=level, offered=[material],
                   material=material, bond=0, happiness=0, notes=[])
    party = [dict(id=101 + index, beast_id=species, name=storage.get_beast_data(species)['name'],
                  type=storage.get_beast_data(species)['type']) for index, species in enumerate(SPECIES)]
    expedition_events.initialize(payload, key, 73, party)
    expedition_events.resolve(payload, key, 'shrine')
    expedition_events.resolve(payload, key, 'repair')
    assert payload['event'] == 'obstacle'
    saved = json.loads(json.dumps(payload))
    trip = dict(payload=saved, status='active')
    page = expedition_events.presentation(trip)
    assert page == expedition_events.presentation(json.loads(json.dumps(trip)))
    outcomes = {}
    for choice in page['choices']:
        if choice['value'] == 'return_early':
            continue
        after = copy.deepcopy(saved)
        effects = expedition_events.resolve(after, key, choice['value'])
        outcomes[choice['value']] = dict(effects=effects, notes=after['notes'], bonus=after['bonus'],
                                          page=expedition_events.presentation(dict(payload=after, status='active')))
    return dict(page=page, saved_payload=saved, outcomes=outcomes,
                transitions=['initialize', 'shrine', 'repair'], seed=73)


DOCUMENT = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChibiBeasts · Biome review</title><style>
*{box-sizing:border-box}body{margin:0;background:#091c20;color:#eee6d1;font:15px/1.55 system-ui,sans-serif}main{max-width:1100px;margin:auto;padding:42px 28px 30px}header{max-width:800px;margin-bottom:26px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.15em;color:#9dc6b2;font-weight:700}h1{font:400 clamp(31px,4.4vw,48px)/1.15 Georgia,serif;margin:10px 0 15px}p{margin:0 0 16px;color:#b8c9bf}.picker{display:grid;grid-template-columns:42px minmax(0,1fr) 42px;gap:8px;margin:24px 0 13px;max-width:740px}select,button{font:600 14px/1.4 system-ui,sans-serif;background:#163b3b;color:#f2e8cd;border:1px solid #587269;border-radius:6px;padding:10px;min-width:0;max-width:100%}select{width:100%;font-weight:500}button{cursor:pointer}button:hover{background:#255049}button:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid #e4c897;outline-offset:3px}.identity{margin-bottom:20px}.identity h2{font:400 clamp(27px,3.6vw,37px)/1.2 Georgia,serif;margin:0 0 8px}.meta{display:flex;flex-wrap:wrap;gap:6px 16px;color:#c9bc98;font-size:13px}.new{font-size:11px;background:#254a40;border:1px solid #577d65;border-radius:4px;padding:2px 7px;color:#c8dfb7}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.card{min-width:0;border:1px solid #3e594f;border-radius:8px;overflow:hidden;background:#102b2c}.wide{grid-column:1/-1}.card h3{font:400 22px/1.3 Georgia,serif;margin:0}.card .body{padding:18px}.card .body p{font-size:13px;margin:8px 0 0}.card img{display:block;width:100%;height:auto;image-rendering:pixelated;background:#0a2125}.banner img{max-width:640px;margin:auto}.banner{background:#0b2227}.controls{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}.controls button{font-size:12px;padding:8px 11px}a{color:#d5c59d}.caption{font-size:12px;color:#95b4a3;margin-top:9px!important}.team-labels{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;color:#c4d0b8;font-size:12px}.team-labels span{border:1px solid #3e5b4c;border-radius:4px;padding:3px 7px}.crossing h3{margin-bottom:12px}.crossing .clue{color:#e2ddca;font-size:15px;line-height:1.65;margin:0 0 16px}.choices{display:grid;gap:10px}.choice{border:1px solid #3d5b50;border-radius:5px;padding:11px 13px;background:#143330}.choice strong{display:block;font-size:14px;color:#e9e0c5}.choice small{display:block;color:#b4c7b9;font-size:12px;line-height:1.5;margin-top:4px}.empty{padding:22px;color:#a8bcaf;font-size:14px}.footnote{margin-top:26px;font-size:12px;color:#94afa2;max-width:900px}.footnote a{margin-right:14px}img[hidden],section[hidden],span[hidden],button[hidden]{display:none}.status{font-size:12px;color:#9eb8a6;margin-top:10px}.section-label{margin-bottom:6px;font-size:11px;color:#9ec4ae;text-transform:uppercase;letter-spacing:.1em}@media(max-width:720px){main{padding:28px 12px 22px}.grid{grid-template-columns:1fr;gap:14px}.wide{grid-column:auto}.card .body{padding:15px}.picker{grid-template-columns:38px minmax(0,1fr) 38px;gap:6px}.picker button{padding:8px}.meta{font-size:12px;gap:5px 12px}h1{margin-top:10px}header p{font-size:14px}}
</style></head><body><main id="biome-gallery">
<header><div class="eyebrow">ChibiBeasts / Production artwork review</div><h1>A different place<br>for every level band.</h1><p>Ten destinations, seen through the actual Field Journal, equipped team and battle renderer. Explore the five new places and their saved crossing pages.</p>
<nav class="picker" aria-label="Browse destinations"><button id="previous" aria-label="Previous destination">←</button><select id="destination" aria-label="Destination"></select><button id="next" aria-label="Next destination">→</button></nav></header>
<section class="identity"><h2 id="name"></h2><div class="meta"><span id="band"></span><span id="gate"></span><span id="new" class="new">New biome</span></div></section>
<div class="grid">
<section class="card wide banner"><img id="header-art" alt=""><div class="body"><h3>Field Journal</h3><p>Packaged destination banner shown above the journal controls.</p></div></section>
<section class="card"><img id="team-art" alt=""><div class="body"><h3>Exploration team</h3><p>Three equipped companions, with the lead standing in front.</p><div class="team-labels"><span>Twine · Gnome Iron Plating</span><span>Gloop · Kelpie Scale Mail</span><span>Barkley · Sprite Silk Attire</span></div></div></section>
<section class="card"><img id="battle-art" alt=""><div class="body"><h3>Battle complete</h3><p>The final still keeps the same destination as the action.</p><div class="controls"><button id="replay">Replay final exchange</button><button id="still">Show final still</button></div><p id="clip-note" class="caption"></p></div></section>
<section id="crossing" class="card wide crossing" hidden><div class="body"><div class="section-label">Saved expedition page</div><h3 id="crossing-title"></h3><p id="crossing-clue" class="clue"></p><div id="choices" class="choices"></div><p id="crossing-footer" class="caption"></p><div class="status">Example route: visit the landmark → repair it → read the crossing.</div></div></section>
</div><footer class="footnote"><p>Offline review of packaged artwork and production event output. Battle snapshots show a prepared final exchange using the normal move resolver; they do not change encounters or rewards. Crossing text and offered choices are saved output from the expedition system. Clips play once; use Replay to inspect them.</p><a href="validation.json">Render evidence</a><a href="crossing-pages.json">Saved crossing examples</a><a href="browser-validation.json">Browser checks</a></footer>
</main><script>
(()=>{const records=__RECORDS__,keys=Object.keys(records),root=document.querySelector('#biome-gallery'),picker=document.querySelector('#destination');let current=keys[1];
const byId=id=>document.getElementById(id);for(const key of keys){const r=records[key],option=document.createElement('option');option.value=key;option.textContent=r.name+' · Lv.'+r.level_range[0]+'–'+r.level_range[1];picker.append(option)}
function show(key){const r=records[key];current=key;picker.value=key;root.dataset.destination=key;byId('name').textContent=r.name;byId('band').textContent='Companion Lv.'+r.level_range[0]+'–'+r.level_range[1];byId('gate').textContent='Trainer Lv.'+r.min_level+'+';byId('new').hidden=!r.new;for(const [id,src,label]of[['header-art',r.header,'Field Journal banner'],['team-art',r.team,'Equipped exploration team'],['battle-art',r.still,'Battle final still']]){byId(id).src=src;byId(id).alt=r.name+' — '+label}byId('replay').hidden=!r.clip;byId('still').hidden=!r.clip;byId('clip-note').textContent=r.clip?(r.clip_info.milliseconds/1000)+' second one-shot animation · '+Math.round(r.clip_info.bytes/1000)+' KB':'Final still rendered with the current destination background.';byId('crossing').hidden=!r.crossing;byId('choices').replaceChildren();if(r.crossing){const p=r.crossing.page;byId('crossing-title').textContent=p.title;byId('crossing-clue').textContent=p.body;byId('crossing-footer').textContent=p.footer;for(const choice of p.choices){const node=document.createElement('div');node.className='choice';const title=document.createElement('strong'),detail=document.createElement('small');title.textContent=choice.label;detail.textContent=choice.description;node.append(title,detail);byId('choices').append(node)}}}
picker.onchange=()=>show(picker.value);byId('previous').onclick=()=>show(keys[(keys.indexOf(current)+keys.length-1)%keys.length]);byId('next').onclick=()=>show(keys[(keys.indexOf(current)+1)%keys.length]);byId('replay').onclick=()=>{const r=records[current];if(r.clip)byId('battle-art').src=r.clip+'?replay='+Date.now()};byId('still').onclick=()=>byId('battle-art').src=records[current].still;root.gallery={show,records};show(current);root.dataset.ready='true';})();
</script></body></html>'''


async def export(output):
    output.mkdir(parents=True, exist_ok=True)
    media = output / 'media'
    media.mkdir(exist_ok=True)
    records, crossing_pages, evidence = {}, {}, {}
    for key, region in BIOMES.items():
        level = sum(region['level_range']) // 2
        before, after, action, rows, log = battle_sample(level)
        header = ROOT / 'data' / 'journal' / f'journal-{key}.png'
        shutil.copyfile(header, media / header.name)
        team = await journal_team_art.render_team(rows, key)
        if team is None:
            raise RuntimeError(f'No Team artwork returned for {key}')
        (media / f'{key}-team.png').write_bytes(team.data)
        still = art.render(after, after, key, journal=action.journal)
        (media / f'{key}-final.png').write_bytes(still.data)
        record = dict(name=art.REALMS[key], level_range=region['level_range'], min_level=region['min_level'],
                      new=key != region['base'], header='media/' + header.name,
                      team=f'media/{key}-team.png', still=f'media/{key}-final.png')
        if record['new']:
            start = time.perf_counter()
            clip = art.render(before, after, key, action)
            seconds = time.perf_counter() - start
            (media / f'{key}.gif').write_bytes(clip.data)
            with Image.open(io.BytesIO(clip.data)) as image:
                info = dict(bytes=len(clip.data), frames=image.n_frames, size=list(image.size),
                            milliseconds=sum(frame.info['duration'] for frame in ImageSequence.Iterator(image)),
                            one_shot='loop' not in image.info, render_seconds=round(seconds, 3))
            assert info['one_shot'] and info['bytes'] <= art.MAX_BYTES
            assert info['milliseconds'] == battle_scene.duration(before, after, action)
            record.update(clip=f'media/{key}.gif', clip_info=info)
            crossing = crossing_sample(key, level)
            record['crossing'] = dict(page=crossing['page'])
            crossing_pages[key] = crossing
        files = [record[field] for field in ('header', 'team', 'still')]
        if record.get('clip'):
            files.append(record['clip'])
        evidence[key] = dict(files={path: dict(bytes=(output / path).stat().st_size,
                            sha256=hashlib.sha256((output / path).read_bytes()).hexdigest()) for path in files},
                             level_range=region['level_range'], trainer_gate=region['min_level'],
                             clip=record.get('clip_info'), combat_log=log)
        records[key] = record
    (output / 'biomes.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (output / 'crossing-pages.json').write_text(json.dumps(crossing_pages, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    document = DOCUMENT.replace('__RECORDS__', json.dumps(records, ensure_ascii=False).replace('</', '<\\/'))
    path = output / 'index.html'
    path.write_text(document, encoding='utf-8')
    report = dict(status='passed', destinations=len(records), new_destinations=len(crossing_pages),
                  scope='Packaged journal headers, actual equipped three-member team renderer, normal move resolver and battle renderer, production seeded saved expedition pages; no live game state.',
                  records=evidence)
    (output / 'validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Exported {len(records)} destinations, {len(crossing_pages)} clips and saved crossing pages: {path}')
    return path, records


async def capture(path, output, records):
    from playwright.async_api import async_playwright
    errors, failed_requests, checks = [], [], []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport=dict(width=1100, height=1000), reduced_motion='reduce')
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('requestfailed', lambda request: failed_requests.append(dict(url=request.url, failure=request.failure)))
        await page.goto(path.resolve().as_uri())
        await page.wait_for_selector('#biome-gallery[data-ready="true"]')
        for width in (1100, 390, 320):
            await page.set_viewport_size(dict(width=width, height=1000))
            for key, record in records.items():
                await page.locator('#destination').select_option(key)
                await page.wait_for_function('Array.from(document.images).every(image=>image.complete&&image.naturalWidth>0)')
                assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (width, key)
                assert await page.locator('#biome-gallery').get_attribute('data-destination') == key
                if record['new']:
                    assert await page.locator('#crossing').is_visible()
                    assert await page.locator('.choice').count() == len(record['crossing']['page']['choices'])
                    await page.get_by_role('button', name='Replay final exchange', exact=True).click()
                    await page.wait_for_function('document.querySelector("#battle-art").complete&&document.querySelector("#battle-art").naturalWidth>0')
                    assert '.gif?' in await page.locator('#battle-art').get_attribute('src')
                    await page.get_by_role('button', name='Show final still', exact=True).click()
                    await page.wait_for_function('document.querySelector("#battle-art").complete&&document.querySelector("#battle-art").naturalWidth>0')
                if key in ('woods_deep', 'loom_deep'):
                    await page.screenshot(path=str(output / f'{width}-{key}.png'), full_page=True)
                checks.append(dict(width=width, destination=key, no_overflow=True, all_images_loaded=True,
                                   crossing_visible=record['new'], replay_checked=record['new']))
            await page.locator('#destination').select_option('woods')
            await page.get_by_role('button', name='Next destination', exact=True).click()
            assert await page.locator('#destination').input_value() == 'woods_deep'
            await page.get_by_role('button', name='Previous destination', exact=True).click()
            assert await page.locator('#destination').input_value() == 'woods'
        await browser.close()
    assert not errors, errors
    assert not failed_requests, failed_requests
    report = dict(status='passed', javascript_errors=errors, missing_requests=failed_requests,
                  checks=checks, selector_and_previous_next=True, screenshots=6)
    (output / 'browser-validation.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'Browser QA passed: {len(checks)} destination/viewport combinations; five clips checked at every width.')


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'biome-preview')
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--tool-path', type=Path)
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    path, records = await export(args.output)
    if args.capture:
        await capture(path, args.output, records)


if __name__ == '__main__':
    asyncio.run(main())
