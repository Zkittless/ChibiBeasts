"""Build a browsable art catalog without changing or re-encoding any PNG."""
import argparse
import base64
from html import escape
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

STYLE = """
:root{--paper:#f4f0e7;--ink:#23283b;--line:#d9d2c4;--art-size:190px}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,sans-serif}
main{max-width:1320px;margin:auto;padding:42px 24px}h1{font-size:clamp(30px,5vw,52px);letter-spacing:-.04em;margin:10px 0}
.eyebrow{font:700 12px ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}
.intro{max-width:780px;color:#636477}.tools{position:sticky;top:0;z-index:1;background:var(--paper);padding:16px 0;display:flex;gap:10px;flex-wrap:wrap;align-items:center;border-bottom:1px solid var(--line)}
input,select,button{font:inherit;color:inherit;border:1px solid #bab3a6;background:#fffcf6;padding:9px 12px}
input[type=search]{width:min(340px,100%)}button{cursor:pointer}label{font-size:13px;display:flex;gap:8px;align-items:center}
#count{color:#696879;font-size:13px;margin-left:auto}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:18px;margin-top:22px}
article{background:#fffdf7;border:1px solid var(--line);min-width:0}article[hidden]{display:none}
.stage{height:240px;display:flex;align-items:center;justify-content:center;overflow:hidden;background-color:#eee9de;background-image:linear-gradient(45deg,#e4dfd4 25%,transparent 25%),linear-gradient(-45deg,#e4dfd4 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#e4dfd4 75%),linear-gradient(-45deg,transparent 75%,#e4dfd4 75%);background-size:24px 24px;background-position:0 0,0 12px,12px -12px,-12px 0}
body.dark .stage{background-color:#282c3c;background-image:linear-gradient(45deg,#33384c 25%,transparent 25%),linear-gradient(-45deg,#33384c 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#33384c 75%),linear-gradient(-45deg,transparent 75%,#33384c 75%)}
.stage img{width:var(--art-size);height:var(--art-size);object-fit:contain;image-rendering:pixelated}
.info{padding:16px 18px}h2{font-size:19px;margin:0 0 4px}.meta{font:600 11px ui-monospace,monospace;color:#756b83;text-transform:uppercase}
.title{font-size:13px;color:#696879;margin:7px 0}.description{font-size:13px;color:#636477;margin:10px 0 0}
footer{border-top:1px solid var(--line);margin-top:30px;padding-top:20px;color:#696879;font-size:13px}
@media(max-width:550px){main{padding:24px 14px}.grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.stage{height:180px}.stage img{max-width:100%}.info{padding:12px}.description{display:none}.tools{gap:7px}}
"""

SCRIPT = """
const cards=[...document.querySelectorAll('article')],query=document.querySelector('#query'),rarity=document.querySelector('#rarity');
function filter(){const term=query.value.toLowerCase().trim(),tier=rarity.value;let count=0;cards.forEach(card=>{const visible=(!term||card.dataset.search.includes(term))&&(!tier||card.dataset.rarity===tier);card.hidden=!visible;if(visible)count++});document.querySelector('#count').textContent=count+' / '+cards.length+' beasts'}
query.addEventListener('input',filter);rarity.addEventListener('change',filter);
document.querySelector('#size').addEventListener('input',event=>document.documentElement.style.setProperty('--art-size',event.target.value+'px'));
document.querySelector('#background').addEventListener('click',()=>document.body.classList.toggle('dark'));filter();
"""

def build(output: Path, standalone: bool):
    roster = json.loads((ROOT / "data/beasts.json").read_text(encoding="utf-8"))["beasts"]
    cards = []
    for beast_id, beast in roster.items():
        path = ROOT / "data/sprites" / f"{beast_id}.png"
        if standalone:
            source = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
        else:
            source = os.path.relpath(path, output.parent).replace(os.sep, "/")
        search = " ".join(str(beast.get(key, "")) for key in ("name", "title", "rarity", "type", "description")).lower()
        cards.append(
            f'<article data-rarity="{escape(beast["rarity"], quote=True)}" data-search="{escape(search, quote=True)}">'
            f'<div class="stage"><img loading="lazy" src="{source}" alt="{escape(beast["name"], quote=True)}"></div>'
            f'<div class="info"><h2>{escape(beast["name"])}</h2>'
            f'<span class="meta">{escape(beast["rarity"].replace("_"," "))} / {escape(beast["type"])}</span>'
            f'<p class="title">{escape(beast["title"])}</p>'
            f'<p class="description">{escape(beast["description"])}</p></div></article>'
        )
    tiers = list(dict.fromkeys(beast["rarity"] for beast in roster.values()))
    options = "".join(f'<option value="{escape(tier)}">{escape(tier.replace("_"," ").title())}</option>' for tier in tiers)
    document = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>ChibiBeasts — complete pixel art roster</title><style>{STYLE}</style></head><body><main>'
        '<div class="eyebrow">ChibiBeasts / complete art refresh</div><h1>Meet the whole collection.</h1>'
        f'<p class="intro">{len(roster)} creatures, one shared pixel art direction. Search by name, type, rarity or description. '
        'The starter designs you approved set the style for the rest of the collection.</p>'
        '<div class="tools"><input id="query" type="search" placeholder="Find a beast…" aria-label="Search beasts">'
        f'<select id="rarity" aria-label="Filter by rarity"><option value="">All rarities</option>{options}</select>'
        '<label>Size <input id="size" type="range" min="48" max="210" value="190" aria-label="Image display size"></label>'
        '<button id="background" type="button">Light / dark backdrop</button><span id="count"></span></div>'
        f'<div class="grid">{"".join(cards)}</div>'
        '<footer>Generated with built-in ImageGen from the project descriptions. Transparent pixel-style renders; '
        'display scaling does not create native 32 × 32 sprites. PNG pixels are preserved. '
        'Source artwork: data/sprites · Manifest: data/artwork.json · Prompts: docs/art-prompts.json.</footer>'
        f'</main><script>{SCRIPT}</script></body></html>'
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    print(f"Saved {len(cards)}-beast gallery to {output}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "art-exploration/gallery.html")
    parser.add_argument("--standalone", action="store_true", help="Embed image bytes so the HTML opens on its own")
    args = parser.parse_args()
    build(args.output.resolve(), args.standalone)
