"""Package the four starter awakenings into a local before-and-after preview."""
import argparse
import base64
from html import escape
import json
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
PAIRS = {'prismite': 'prismara', 'twine': 'timestitch', 'gloop': 'nebuloom', 'barkley': 'rootwarden'}

def build(output, standalone=False):
    roster = json.loads((ROOT / 'data/beasts.json').read_text(encoding='utf-8'))['beasts']
    cards = []
    for before, after in PAIRS.items():
        images = []
        for key in (before, after):
            path = ROOT / 'data/sprites' / f'{key}.png'
            source = 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii') if standalone else os.path.relpath(path, output.parent).replace(os.sep, '/')
            images.append(f'''<figure><div class="stage"><img src="{source}" alt="{escape(roster[key]['name'])}"></div><figcaption>{escape(roster[key]['name'])}<span>{('STARTER' if key == before else 'BOND AWAKENING')}</span></figcaption></figure>''')
        data = roster[after]
        passive = data['divine_passive']
        cards.append(f"""<article><div class="pair">{''.join(images)}</div><div class="info"><p class="type">{escape(data['type'])} · Divine</p><h2>{escape(roster[before]['name'])} <span>→</span> {escape(data['name'])}</h2><p>{escape(data['description'])}</p><p class="ability"><strong>{escape(passive['passive_name'])}</strong> {escape(passive['passive_desc'])}</p></div></article>""")
    style = '*{box-sizing:border-box}body{margin:0;background:#f1eadb;color:#25283a;font:16px/1.6 system-ui,sans-serif}main{max-width:1240px;margin:auto;padding:48px 24px 32px}.eyebrow{font:700 12px ui-monospace,monospace;letter-spacing:.16em;text-transform:uppercase;color:#74634b}h1{font-size:clamp(35px,5vw,60px);line-height:1.1;letter-spacing:-.05em;margin:14px 0 20px}header>p{max-width:750px;color:#656170}header button{font:inherit;background:#2b3044;color:#fff9ea;border:0;padding:10px 16px;cursor:pointer}.requirements{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0 34px}.requirements span{padding:8px 14px;border:1px solid #cec1a9;background:#fffaf0;font:600 13px ui-monospace,monospace}.grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}article{background:#fffaf1;border:1px solid #d3c7b3;overflow:hidden}.pair{display:grid;grid-template-columns:1fr 1fr;background:#f6f2e9}figure{margin:0;min-width:0}.stage{height:260px;display:flex;align-items:center;justify-content:center;background-color:#e9e3d8;background-image:linear-gradient(45deg,#ded7ca 25%,transparent 25%),linear-gradient(-45deg,#ded7ca 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#ded7ca 75%),linear-gradient(-45deg,transparent 75%,#ded7ca 75%);background-size:24px 24px;background-position:0 0,0 12px,12px -12px,-12px 0}.stage img{height:94%;width:94%;object-fit:contain;image-rendering:pixelated}figcaption{padding:10px 16px;font-weight:750;border-bottom:1px solid #d8cebc}figcaption span{display:block;font:10px ui-monospace,monospace;color:#786d5c;letter-spacing:.06em}.info{padding:20px 24px 24px}.type{text-transform:uppercase;letter-spacing:.12em;font:700 11px ui-monospace,monospace;color:#897044;margin:0}h2{font-size:23px;letter-spacing:-.04em;margin:10px 0}h2 span{color:#af8750}p{margin:12px 0}.info p{font-size:14px;color:#605e69}.ability{border-top:1px solid #e2d8c8;padding-top:14px}.ability strong{display:block;color:#393f57}.changes{margin-top:38px;border-top:2px solid #b39d79;padding-top:20px}.changes h2{font-size:28px}.changes p{max-width:850px;color:#625e69}footer{font-size:12px;color:#726958;margin-top:32px}body.dark .stage{background-color:#222b42;background-image:linear-gradient(45deg,#2d3852 25%,transparent 25%),linear-gradient(-45deg,#2d3852 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#2d3852 75%),linear-gradient(-45deg,transparent 75%,#2d3852 75%)}@media(max-width:800px){.grid{grid-template-columns:1fr}.stage{height:220px}main{padding:28px 14px}.info{padding:18px}.requirements{gap:6px}.requirements span{padding:6px 8px;font-size:11px}}'
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ChibiBeasts · Starter awakenings</title><style>{style}</style></head><body><main><header><div class="eyebrow">ChibiBeasts · Gameplay revival</div><h1>An old friend.<br>A new shape.</h1><p>Four starter awakenings built around the companion you have raised. New anatomy, new silhouettes and a new passive for each—while your shared history stays with them.</p><button id="backdrop">Switch art backdrop</button></header><div class="requirements"><span>BEAST LEVEL 15</span><span>BOND 30 / 100</span><span>HAPPINESS 70+</span><span>/journal → Companions</span></div><section class="grid">{''.join(cards)}</section><section class="changes"><div class="eyebrow">Beyond the art</div><h2>A campaign with an ending.</h2><p>Ten connected chapters now resolve the clasp, the reunion, the Fraying and the World Tree Seed. Choose care, curiosity or courage at the hollow, with a lasting scene and title.</p><h2>More decisions in every fight.</h2><p>Reliable attacks restore mana, typed skills change the battle, and guarding prepares your next strike. Gear, bonds and passives share the same rules in wild battles, spars, PvP and ranked. Raids add ally tending, swapping and six boss-specific Weave responses; support earns rewards too.</p><h2>Rewards that reach the player.</h2><p>Working shard and guild purchases, usable consumables, crafting for runes, real forging-material routes, reachable perks and reliable story claims. Evolutions preserve nickname, training, earned stat boosts, gear and party assignments.</p></section><footer>Original PNG pixels preserved. Generated with built-in ImageGen; transparent pixel-style artwork, not native 32 × 32 sprites. Saved locally for review. Live Discord testing remains a deployment step.</footer></main><script>document.getElementById('backdrop').onclick=()=>document.body.classList.toggle('dark')</script></body></html>"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding='utf-8')
    print(f'Saved four awakening pairs to {output}')
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'chibibeasts-evolutions.html')
    parser.add_argument('--standalone', action='store_true')
    args = parser.parse_args()
    build(args.output.resolve(), args.standalone)
