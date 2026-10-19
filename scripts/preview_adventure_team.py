"""Export real armored team clips, final stills and a playback audit.

python scripts/preview_adventure_team.py --output ../output/adventure-team
No Discord login, database or generated artwork is required.
"""
import argparse
from html import escape
import io
import json
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw, ImageSequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cogs.battle import build_pve_beast_state
from utils import battle_art as art, battle_scene as scene, db as storage
from utils.team_combat import assist_effect


def make_team(active=0):
    party = [build_pve_beast_state(storage.get_beast_data(species), 8)
             for species in ('twine', 'gloop', 'barkley')]
    for i, (fighter, armor) in enumerate(zip(party, ('gnome_iron_plating', 'kelpie_scale_mail', 'sprite_silk_attire'))):
        fighter.update(id=101+i, armor_id=armor, _assist_meter=100)
    enemy = build_pve_beast_state(storage.get_beast_data('prismite'), 9)
    return party, enemy, active


def capture(party, enemy, active):
    lead = dict(party[active], _party_states=party)
    return art.snapshot([lead, enemy])


def cases():
    party, enemy, active = make_team()
    party[0]['hp'] = max(1, party[0]['max_hp']//3)
    party[1]['hp'] = max(1, party[1]['max_hp']*3//4)
    before = capture(party, enemy, active)
    yield 'tag-switch', before, capture(party, enemy, 1), art.Action(0, 'Twine tags in Gloop', 'swap', incoming_id=102, outgoing_id=101)

    for effect, supporter, active in (('heal', 1, 0), ('guard', 2, 0), ('attack', 0, 2)):
        party, enemy, _ = make_team(active)
        if effect == 'guard':
            # Barkley is a healer under the production support policy. Use an
            # eligible earth protector while retaining the saved slot and gear.
            party[2] = build_pve_beast_state(storage.get_beast_data('goblin'), 8)
            party[2].update(id=103, armor_id='sprite_silk_attire', _assist_meter=100)
        assert assist_effect(party[supporter]) == effect, (party[supporter]['beast_id'], effect)
        party[active]['hp'] = party[active]['max_hp']//2
        before = capture(party, enemy, active)
        if effect == 'heal':
            party[active]['hp'] += max(1, party[active]['max_hp']//10)
        elif effect == 'guard':
            party[active]['guard'] = 25
        else:
            enemy['hp'] -= 18
        party[active]['_assist_meter'] = 0
        party[supporter]['mana'] -= 12
        support = party[supporter]
        action = art.Action(0, f'{support["name"]} {effect} assist', 'assist', 18 if effect == 'attack' else 0,
                            support_id=support['id'], support_name=support['name'], support_species=support['beast_id'],
                            support_armor_id=support['armor_id'], support_effect=effect)
        yield f'{effect}-assist', before, capture(party, enemy, active), action

    party, enemy, active = make_team()
    party[0]['hp'] = 22
    before = capture(party, enemy, active)
    party[0]['hp'] = 0
    after = capture(party, enemy, active)
    yield 'companion-knockout', before, after, art.Action(1, enemy['moves'][0], damage=22)
    yield 'reserve-replacement', after, capture(party, enemy, 1), art.Action(0, 'Gloop takes over', 'swap', incoming_id=102, outgoing_id=101)

    party, enemy, active = make_team()
    enemy['hp'] = 16
    before = capture(party, enemy, active)
    enemy['hp'] = 0
    yield 'victory-retreat', before, capture(party, enemy, active), art.Action(0, 'Tick Bite', damage=16)


def write_gallery(output, records):
    descriptions = {
        'tag-switch': ('A deliberate handoff', 'Twine exits, the field holds clear, and Gloop joins. Each keeps its own armor and HP.'),
        'heal-assist': ('A helping hand', 'Gloop leaves its reserve slot, restores Twine, and returns. The assist meter is spent.'),
        'guard-assist': ('Cover for a teammate', 'Goblin joins briefly while a protective ring forms around the active companion.'),
        'attack-assist': ('Support with a punch', 'Twine sends an elemental strike at Prismite while Barkley stays active.'),
        'companion-knockout': ('A setback, not the end', 'Twine takes the hit and retreats. Two healthy companions remain available.'),
        'reserve-replacement': ('The team carries on', 'Gloop takes over after the knockout. Twine stays marked KO in reserve.'),
        'victory-retreat': ('A clean finish', 'Prismite leaves the battlefield after the final hit. The team remains visible.'),
    }
    cards = []
    for slug, record in records.items():
        title, description = descriptions[slug]
        cards.append(f'''<article class="card"><div class="art"><img src="{slug}-final.png" data-clip="{slug}.gif" data-still="{slug}-final.png" alt="{escape(description)}" width="640" height="528"></div>
<div class="body"><div class="meta">{record['milliseconds']/1000:g} seconds <span>·</span> {round(record['bytes']/1000)} KB</div><h2>{title}</h2><p>{description}</p>
<div class="controls"><button type="button" data-play>Replay clip</button><button type="button" data-stop>Final still</button><a href="{slug}.gif" download>GIF ↗</a></div></div></article>''')
    html = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ChibiBeasts · Adventure team animation review</title>
<style>*{box-sizing:border-box}body{margin:0;background:#091d20;color:#f2e8cd;font:16px/1.55 system-ui,sans-serif}main{max-width:1280px;margin:auto;padding:54px 28px 40px}.eyebrow{color:#aec9b3;font-size:12px;font-weight:700;letter-spacing:.18em;text-transform:uppercase}h1{font-family:Georgia,serif;font-weight:400;font-size:clamp(35px,5vw,62px);line-height:1.12;margin:14px 0 20px;max-width:750px}header p{color:#becdbf;max-width:750px;font-size:17px}header{margin-bottom:36px}.options{display:flex;gap:20px;align-items:center;flex-wrap:wrap;margin-top:26px}.options label{cursor:pointer;color:#e2d0a5;display:flex;gap:8px;align-items:center}a{color:#dccb9c;text-decoration:none}a:hover{text-decoration:underline}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px}.card{background:#102b2c;border:1px solid #435b4e;overflow:hidden;border-radius:10px}.art{background:#071a1d;border-bottom:1px solid #435b4e}.art img{width:100%;height:auto;display:block;image-rendering:pixelated}.body{padding:22px}.meta{font-size:12px;color:#9fc1ad;letter-spacing:.04em}.meta span{padding:0 5px}h2{font:400 26px/1.3 Georgia,serif;margin:8px 0 10px}.body p{color:#bdcbbd;margin:0 0 22px;min-height:50px}.controls{display:flex;align-items:center;gap:9px;flex-wrap:wrap}button{font:600 13px system-ui,sans-serif;border:1px solid #829078;color:#e7ddbd;background:#1b3b38;border-radius:5px;padding:9px 13px;cursor:pointer}button:hover{background:#2d5146}button:focus-visible,a:focus-visible{outline:2px solid #ffdf9e;outline-offset:4px}.controls a{font-size:12px;margin-left:auto}footer{margin-top:32px;color:#96b09f;font-size:13px;max-width:850px}input{accent-color:#8edbb7}@media(max-width:760px){main{padding:32px 16px}.grid{grid-template-columns:1fr}.body p{min-height:0}}</style>
<main><header><div class="eyebrow">ChibiBeasts / Field Journal</div><h1>Three companions.<br>One battlefield.</h1><p>Review the actual armored team animations: visible reserves, smooth tag switches, support visits, and a team that keeps going after a knockout.</p><div class="options"><label><input id="stills" type="checkbox"> Prefer still images</label><a href="animation-validation.json">Playback audit ↗</a><a href="team-overview.png">Contact sheet ↗</a></div></header><div class="grid">'''+''.join(cards)+'''</div><footer>These previews use the packaged sprites and production battle renderer with prepared combat snapshots. Every clip plays once and holds its final state. The gallery respects reduced motion; Replay clip is an explicit playback request.</footer></main>
<script>const stills=document.querySelector('#stills');stills.checked=matchMedia('(prefers-reduced-motion: reduce)').matches;function play(img){img.src=img.dataset.clip+'?replay='+Date.now()}function stop(img){img.src=img.dataset.still}document.querySelectorAll('.card').forEach(card=>{const img=card.querySelector('img');card.querySelector('[data-play]').addEventListener('click',()=>play(img));card.querySelector('[data-stop]').addEventListener('click',()=>stop(img));if(!stills.checked)play(img)});stills.addEventListener('change',()=>document.querySelectorAll('.card img').forEach(img=>stills.checked?stop(img):play(img)));</script></html>'''
    (output/'index.html').write_text(html, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = {}
    examples = list(cases())
    overview = Image.new('RGB', (960, len(examples)*284), '#102b2c')
    draw = ImageDraw.Draw(overview)
    for row, (slug, before, after, action) in enumerate(examples):
        started = time.perf_counter()
        clip = art.render(before, after, 'woods', action)
        elapsed = time.perf_counter()-started
        (args.output/f'{slug}.gif').write_bytes(clip.data)
        final = art.frame(after, after, 'woods')
        final.save(args.output/f'{slug}-final.png')
        contact = Image.new('RGB', (1280, 2*284), '#102b2c')
        ticks = (0, .16, .32, .45, .55, .7, .86, 1)
        contact_draw = ImageDraw.Draw(contact)
        for index, tick in enumerate(ticks):
            x, y = index%4*320, index//4*284
            still = art.frame(before, after, 'woods', action, tick)
            contact.paste(still.resize((320, 264), Image.Resampling.NEAREST), (x, y+20))
            contact_draw.text((x+7, y+4), f'{tick:.2f}', fill='#f2e8cd', font=art.font(12))
        contact.save(args.output/f'{slug}-contact.png')
        draw.text((7, row*284+4), slug.replace('-', ' ').upper(), fill='#f2e8cd', font=art.font(12))
        for column, tick in enumerate((0, .55, 1)):
            still = art.frame(before, after, 'woods', action, tick)
            overview.paste(still.resize((320,264), Image.Resampling.NEAREST), (column*320, row*284+20))
        with Image.open(io.BytesIO(clip.data)) as image:
            record = {'bytes': len(clip.data), 'frames': image.n_frames,
                      'milliseconds': sum(frame.info['duration'] for frame in ImageSequence.Iterator(image)),
                      'one_shot': 'loop' not in image.info, 'render_seconds': round(elapsed, 3),
                      'size': list(image.size)}
        assert record['bytes'] < art.MAX_BYTES and record['one_shot']
        assert record['milliseconds'] == scene.duration(before, after, action)
        assert record['render_seconds'] < art.RENDER_TIMEOUT
        records[slug] = record
    overview.save(args.output/'team-overview.png')
    # A separate exact 320px still supports reviewing Discord's compact view.
    _, _, replacement, _ = next(c for c in examples if c[0] == 'reserve-replacement')
    art.frame(replacement, replacement, 'woods').resize((320, 264), Image.Resampling.NEAREST).save(args.output/'compact-team-final.png')
    report = {'clips': records, 'layout': {'active_homes': scene.TEAM_HOMES, 'reserve_boxes': scene.RESERVE_BOXES,
                                         'hud_height': art.HUD, 'field_height': art.HEIGHT, 'journal_height': art.JOURNAL},
              'scope': 'Actual packaged armored sprites. Synthetic resolved states for presentation validation; no combat rewards modified.'}
    (args.output/'animation-validation.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    write_gallery(args.output, records)
    print(json.dumps(records, indent=2))


if __name__ == '__main__':
    main()
