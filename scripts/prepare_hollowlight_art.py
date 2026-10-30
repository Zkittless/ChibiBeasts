"""Install approved seasonal sprites and cards without rebuilding existing art.

Usage: python scripts/prepare_hollowlight_art.py selected-base-art.json prompts.json
Sources are built-in image_gen originals; this only packages and hashes them.
"""
import hashlib
import json
from pathlib import Path
import shutil
import sys

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_card_artwork import SIZE, save, sprite

ROOT = Path(__file__).resolve().parents[1]


def attack():
    return {'power': 1, 'mana_cost': 0, 'mana_restore': 10,
            'description': 'Reliable attack; restore 10 mana.'}


def condition(status):
    return {'power': .65, 'mana_cost': 15, 'status': status,
            'description': f'A controlled strike that applies {status}.'}


def guard():
    return {'power': 0, 'mana_cost': 0, 'guard_percent': 55,
            'focus_multiplier': 1.4, 'mana_restore': 20,
            'description': 'Reduce the next hit by 55%, restore 20 mana and empower your next attack by 40%.'}


CATALOG = {
    'patchkin': {
        'name': 'Patchkin', 'title': 'The Little Harvest Guard',
        'rarity': 'uncommon', 'type': 'earth',
        'stats': (130, 50, 70, 25, 65), 'catch_rate': .65,
        'moves': ['Gourd Bump', 'Straw Snare', 'Harvest Brace'],
        'effects': [attack(), condition('weaken'), guard()],
        'ultimate': 'Lantern Bulwark',
        'description': 'A pumpkin-headed scarecrow who patrols the Carnival in oversized burlap sleeves. It plants its little root boots firmly whenever someone smaller needs protecting.',
        'encounter_situations': [
            'A Patchkin is carefully straightening the lanterns along an empty stall. It turns one toward you, as if checking whether you belong here.',
            'You find a Patchkin standing guard over a dropped toffee apple. It has taken this responsibility very seriously.',
            'A small pair of root boots is visible beneath a curtain of prize ribbons. The Patchkin steps out and politely blocks the path.'
        ],
    },
    'wickwing': {
        'name': 'Wickwing', 'title': 'The Candle Acrobat',
        'rarity': 'rare', 'type': 'fire',
        'stats': (100, 80, 45, 95, 75), 'catch_rate': .45,
        'moves': ['Ember Flutter', 'Waxflare', 'Candle Shelter'],
        'effects': [attack(), {'power': 1.45, 'mana_cost': 30,
                             'description': 'A concentrated flare that deals 145% attack damage.'}, guard()],
        'ultimate': 'Candleburst',
        'description': 'A plum-purple bat whose tiny candle flames never scorch the canvas above it. Wickwings light the Carnival by performing elaborate, slightly boastful loops.',
        'encounter_situations': [
            'A Wickwing is flying figure eights through a string of unlit lanterns. Each one wakes with a warm glow as it passes.',
            'The stage curtains lift to reveal a single Wickwing bowing to an audience of empty chairs. It notices you and immediately starts another act.',
            'A bead of warm wax lands beside your boot. Above you, a Wickwing has mistaken your team for its next audience.'
        ],
    },
    'veilmoth': {
        'name': 'Veilmoth', 'title': 'The Midnight Lanternbearer',
        'rarity': 'legendary', 'type': 'shadow',
        'stats': (150, 95, 85, 105, 125), 'catch_rate': .08,
        'moves': ['Velvet Wing', 'Funeral Glimmer', 'Moonveil Mend'],
        'effects': [attack(), condition('blind'),
                    {'power': 0, 'mana_cost': 20, 'heal_percent': 20, 'cleanse': True,
                     'description': 'Restore 20% HP and clear status.'}],
        'ultimate': 'Midnight Procession',
        'description': 'A quiet moth with ivory veil-wings and a lantern no larger than a thimble. It follows the last procession, guiding lost Carnival visitors toward the gate.',
        'encounter_situations': [
            'The music goes quiet. A Veilmoth drifts between the lanterns, its pale wings folding moonlight into the empty space above the path.',
            'You follow a tiny gold light behind the shuttered theatre. A Veilmoth waits there, as though it has been expecting this exact moment.',
            'A procession of shadows parts around a single ivory-winged moth. Its little lantern turns toward your team.'
        ],
    },
    'hollow_king': {
        'name': 'The Hollow King', 'title': 'Keeper of the Last Procession',
        'rarity': 'epic', 'type': 'earth',
        'stats': (150, 75, 65, 50, 85), 'catch_rate': 0,
        'moves': ['Harvest Sweep', 'Lantern Decree', 'Burlap Bastion'],
        'effects': [attack(), condition('weaken'), guard()],
        'ultimate': 'Last Procession',
        'description': 'The Carnival guardian wears a crooked candle crown and carries the lights of visitors who never found the exit. He tests every departing team before opening the final gate.',
        'encounter_situations': [
            'The Hollow King steps down from the silent stage. Three little lights turn inside his lantern, and the final gate closes behind him.',
            'A crooked candle crown glows above the crowd. The Hollow King raises his lantern; the procession stops to watch your team.',
            'At the edge of the Carnival, the Hollow King waits beside an unlatched gate. He plants his root boots and gives you one solemn nod.'
        ],
    },
}


def prepare(sources_path, prompts_path):
    sources = json.loads(Path(sources_path).read_text(encoding='utf-8'))
    prompts = json.loads(Path(prompts_path).read_text(encoding='utf-8'))
    if set(sources) != set(CATALOG) or not set(CATALOG) <= set(prompts):
        raise ValueError('All four approved sources and prompts are required.')
    beasts_path = ROOT / 'data/beasts.json'
    art_path = ROOT / 'data/artwork.json'
    cards_path = ROOT / 'data/card-artwork.json'
    provenance_path = ROOT / 'docs/art-prompts.json'
    beasts = json.loads(beasts_path.read_text(encoding='utf-8'))
    art = json.loads(art_path.read_text(encoding='utf-8'))
    cards = json.loads(cards_path.read_text(encoding='utf-8'))
    provenance = json.loads(provenance_path.read_text(encoding='utf-8'))
    records = {record['id']: record for record in provenance['characters']}
    # Validate the entire selection before modifying the catalog.
    for key, source in sources.items():
        with Image.open(source) as image:
            if image.mode != 'RGBA' or image.getchannel('A').getextrema()[0] != 0:
                raise ValueError(f'{key} needs a true transparent RGBA source.')
    for key, details in CATALOG.items():
        target = ROOT / 'data/sprites' / f'{key}.png'
        shutil.copyfile(sources[key], target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        with Image.open(target) as image:
            width, height = image.size
        art['beasts'][key] = {'file': target.name, 'sha256': digest,
                              'width': width, 'height': height, 'source': 'image_gen'}
        portrait = Image.new('RGBA', SIZE)
        cutout = sprite(target, (SIZE[0] - 128, SIZE[1] - 48))
        portrait.alpha_composite(cutout, ((SIZE[0] - cutout.width) // 2,
                                         (SIZE[1] - cutout.height) // 2))
        entry = save(portrait, f'{key}.png')
        entry['source_sha256'] = digest
        cards['beasts'][key] = entry
        data = {k: v for k, v in details.items() if k not in ('stats', 'effects')}
        data.update(id=key, event_id='hollowlight_2026',
                    wild_encounter=key != 'hollow_king', evolution=None,
                    base_stats=dict(zip(('hp', 'attack', 'defense', 'speed', 'mana'), details['stats'])),
                    move_effects=dict(zip(details['moves'], details['effects'])))
        beasts['beasts'][key] = data
        records[key] = {'id': key, 'prompt': prompts[key], 'references': [],
                        'event_id': 'hollowlight_2026'}
    provenance['characters'] = list(records.values())
    for path, value in ((art_path, art), (cards_path, cards),
                        (provenance_path, provenance), (beasts_path, beasts)):
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'Installed {len(CATALOG)} seasonal sprites and cards; {len(beasts["beasts"])} total beasts.')


if __name__ == '__main__':
    prepare(*sys.argv[1:])
