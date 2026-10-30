"""Build an offline, full-roster costume and seasonal wardrobe review gallery."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hollowlight Carnival · Artwork gallery</title><style>
:root{color-scheme:dark;font:16px/1.5 system-ui,sans-serif;color:#f4ecdc;background:#171522}
*{box-sizing:border-box}body{margin:0}main{max-width:1040px;margin:auto;padding:24px}
h1{font-family:Georgia,serif;font-size:clamp(28px,6vw,48px);line-height:1.1;margin:0 0 16px}h2{font-size:23px}
p{color:#c9c0d0}a{color:#ffe0a2}button,select{font:inherit;color:inherit;background:#302b3d;border:1px solid #75617d;border-radius:7px;padding:9px;min-width:0}
button{cursor:pointer}button:hover{background:#51405b}label{display:grid;gap:6px}nav,.controls{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}
.controls label{flex:1 1 220px}.controls .step{flex:0 0 auto;align-self:end}.hero{border:1px solid #806b51;border-radius:12px;overflow:hidden}
.hero img{display:block;width:100%;image-rendering:pixelated}.hero div{padding:18px;background:#292336}.hero p{margin:6px 0}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}.card,.portrait{border:1px solid #66566c;border-radius:10px;background:#282333;text-align:center;overflow:hidden}
.card img{display:block;width:100%;height:190px;object-fit:contain;image-rendering:pixelated;padding:14px}.card strong{display:block;padding:0 8px 12px}
.pair{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.stage{height:330px;display:flex;align-items:center;justify-content:center;background:radial-gradient(ellipse at bottom,#4e3b55,#282333 75%)}
.stage img{max-width:92%;max-height:92%;object-fit:contain;image-rendering:pixelated}.light .stage{background:#f6efdf}.portrait h3{font-size:17px;margin:0;padding:12px;background:#30293f}
.meta{display:block;color:#dcc4a0;margin-top:8px}.note{padding:14px 18px;background:#30263c;border-left:3px solid #e4b16f;border-radius:5px}
.gallery-note{font-size:14px}footer{margin:32px 0;color:#b3a7bd}#description{min-height:3em}#clip,.team-stage{display:block;width:100%;max-width:760px;border-radius:8px;border:1px solid #66566c}
@media(max-width:600px){main{padding:16px}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.card img{height:160px}.pair{gap:8px}.stage{height:220px}.portrait h3{font-size:14px;padding:9px}.controls{gap:8px}}
</style></head><body><main>
<h1>The Hollowlight Carnival</h1><p>A lantern-lit detour, three new companions, and a little ghost costume tailored to every beast.</p>
<nav><a href="journal/hollowlight.html">Journal preview</a><a href="moves/index.html">New move animations</a><a href="../HOLLOWLIGHT-CARNIVAL.md">Event rules</a></nav>
<section class="hero"><img src="../../data/battle_realms/hollowlight.png" alt="Lantern-lit Carnival clearing beneath a moonlit forest"><div><strong>Follow the lanterns. Find your way home.</strong><p>Randomized adventures, occasional packs, and the Hollow King waiting at the final gate.</p></div></section>
<h2>Meet the Carnival</h2><div class="cards" id="cast"></div>
<h2>Your Carnival companions</h2><p>Patchkin leads the way, with Wickwing and Veilmoth behind. Each wears its own fitted ghost costume.</p><img class="team-stage" src="ghost-team-carnival.png" alt="Three ghost-costumed Carnival companions with Patchkin standing in front">
<h2>Every beast gets a fitted costume</h2><p class="note">Unlock the 2026 ghost costume once, then choose which companions wear it in Gear. Their equipped gear keeps all of its stats.</p>
<div class="controls"><label>Companion<select id="beast"></select></label><button class="step" id="prev" aria-label="Previous companion">←</button><button class="step" id="next" aria-label="Next companion">→</button><label>Equipped armor to compare<select id="armor"></select></label></div>
<strong id="name"></strong><span class="meta" id="meta"></span><p id="description"></p>
<div class="pair" id="pair"><section class="portrait"><h3 id="gearLabel">Equipped gear</h3><div class="stage"><img id="gear" alt=""></div></section><section class="portrait"><h3>Little ghost costume · 2026</h3><div class="stage"><img id="ghost" alt=""></div></section></div>
<div class="controls"><button id="background">Use light review background</button></div>
<p class="gallery-note" id="count"></p>
<h2>In battle</h2><p>Twine, Gloop and Barkley keep their costumes through attacks, tag swaps and assists. The actual battle renderer is shown against the Hollow King.</p>
<img id="clip" src="ghost-swap-final.png" alt="A ghost-costumed team facing the Hollow King in the Carnival">
<div class="controls"><button data-clip="ghost-strike">Replay attack</button><button data-clip="ghost-swap">Replay tag swap</button><button data-clip="ghost-assist">Replay assist</button></div>
<footer>Offline artwork review · no live game actions or purchases. Built-in image_gen artwork; original sprites, prompt provenance and content hashes are included in this repository. <a href="../art-prompts.json">Character prompts</a> · <a href="../hollowlight-armor-prompts.json">Armor prompts</a> · <a href="../../data/cosmetics/provenance.json">Costume prompts and review</a> · <a href="../../data/battle-artwork.json">Biome provenance</a></footer>
</main><script>
const data=__DATA__, beast=document.getElementById('beast'), armor=document.getElementById('armor');
function option(value,text){const el=document.createElement('option');el.value=value;el.textContent=text;return el}
for(const row of data.beasts)beast.append(option(row.id,row.name+' · '+row.rarity));
armor.append(option('','Show canonical appearance'));for(const row of data.armors)armor.append(option(row.id,row.name));
for(const id of ['patchkin','wickwing','veilmoth','hollow_king']){const b=data.beasts.find(x=>x.id===id),card=document.createElement('article'),img=document.createElement('img'),name=document.createElement('strong');card.className='card';img.src='../../data/sprites/'+id+'.png';img.alt=b.name;name.textContent=b.name;card.append(img,name);document.getElementById('cast').append(card)}
function render(){const b=data.beasts.find(x=>x.id===beast.value);document.getElementById('name').textContent=b.name;document.getElementById('meta').textContent=b.rarity+' · '+b.type;document.getElementById('description').textContent=b.description;const g=document.getElementById('gear'),c=document.getElementById('ghost');g.src='../../data/'+(armor.value?'armor/variants/'+armor.value+'/'+b.id+'.png':'sprites/'+b.id+'.png');g.alt=b.name+' wearing '+(armor.selectedOptions[0].textContent);c.src='../../data/cosmetics/ghost_costume_2026/'+b.id+'.png';c.alt=b.name+' in its fitted ghost costume';document.getElementById('gearLabel').textContent=armor.value?armor.selectedOptions[0].textContent:'Canonical appearance';}
beast.value='patchkin';beast.onchange=render;armor.onchange=render;
function step(delta){beast.selectedIndex=(beast.selectedIndex+delta+beast.options.length)%beast.options.length;render()}
document.getElementById('prev').onclick=()=>step(-1);document.getElementById('next').onclick=()=>step(1);
document.getElementById('background').onclick=()=>{const light=document.getElementById('pair').classList.toggle('light');document.getElementById('background').textContent=light?'Use dark review background':'Use light review background'};
for(const button of document.querySelectorAll('[data-clip]'))button.onclick=()=>{document.getElementById('clip').src=button.dataset.clip+'.gif?v='+Date.now()};
document.getElementById('count').textContent=data.beasts.length+' reviewed ghost fits · '+data.pairs+' dedicated armor variants · 4 original Carnival characters · 1 original Carnival backdrop.';
render();document.body.dataset.ready='true';
</script></body></html>'''


def build():
    read = lambda path: json.loads((ROOT / path).read_text(encoding='utf-8'))
    beasts = read('data/beasts.json')['beasts']
    armors = read('data/equipment.json')['equipment']
    manifest = read('data/cosmetics/manifest.json')
    variants = manifest['sets']['ghost_costume_2026']['variants']
    if set(variants) != set(beasts):
        raise ValueError('The gallery requires every reviewed ghost fit, including seasonal beasts.')
    for relative in variants.values():
        path = ROOT / 'data/cosmetics' / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest['sha256'][relative]:
            raise ValueError(f'Costume hash does not match: {relative}')
    armor_manifest = read('data/armor/manifest.json')
    pairs = sum(len(row['variants']) for row in armor_manifest['sets'].values())
    if pairs != len(beasts) * len(armors):
        raise ValueError('Every canonical species requires all 16 fitted regular armor variants.')
    payload = {'beasts': [dict(id=key, **{field: row[field] for field in
                           ('name', 'rarity', 'type', 'description')}) for key, row in beasts.items()],
               'armors': [dict(id=key, name=row['name']) for key, row in armors.items()], 'pairs': pairs}
    output = ROOT / 'docs/hollowlight-preview'
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'index.html'
    path.write_text(TEMPLATE.replace('__DATA__', json.dumps(payload, ensure_ascii=False).replace('</', '<\\/')), encoding='utf-8')
    return path


async def capture(path):
    from playwright.async_api import async_playwright
    errors, checks = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel='chrome', headless=True)
        page = await browser.new_page(viewport={'width': 1100, 'height': 1000}, device_scale_factor=1)
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.goto(path.as_uri())
        await page.wait_for_selector('body[data-ready="true"]')
        for width in (1100, 390, 320):
            await page.set_viewport_size({'width': width, 'height': 1000})
            for species in ('patchkin', 'wickwing', 'veilmoth', 'hollow_king', 'twine', 'hydra', 'leviathan'):
                await page.locator('#beast').select_option(species)
                for armor in ('', 'gnome_iron_plating', 'unicorn_healer_vestments', 'chronos_paradox_plate'):
                    await page.locator('#armor').select_option(armor)
                    await page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth>0)')
                    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (width, species)
                    checks.append({'width': width, 'species': species, 'armor': armor, 'all_art_loaded': True, 'no_overflow': True})
            await page.locator('#beast').select_option('veilmoth')
            await page.locator('#armor').select_option('thunderbird_storm_mail')
            await page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth>0)')
            await page.screenshot(path=str(path.parent / f'gallery-{width}.png'), full_page=True)
        # Validate every selectable sprite pair, not just the screenshot examples.
        for species in await page.locator('#beast option').evaluate_all('(rows)=>rows.map(r=>r.value)'):
            await page.locator('#beast').select_option(species)
            await page.wait_for_function('Array.from(document.images).every(i=>i.complete&&i.naturalWidth>0)')
        await browser.close()
    assert not errors, errors
    (path.parent / 'browser-validation.json').write_text(json.dumps({'status': 'passed', 'checks': checks,
        'all_90_costumes_loaded': True, 'javascript_errors': errors}, indent=2) + '\n', encoding='utf-8')
    print(f'Gallery passed {len(checks)} viewport/species/armor combinations and every costume.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--tool-path', type=Path)
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    target = build()
    print(target)
    if args.capture:
        asyncio.run(capture(target))
