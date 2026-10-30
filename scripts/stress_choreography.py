"""Offline stress check: ten matching simultaneous ultimates in a raid."""
import argparse
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cogs.battle import build_pve_beast_state
from cogs.guilds import RAID_BOSSES
from scripts.preview_raid_arena import make_raid, capture
from scripts.preview_move_animations import gif_record
from utils import db, raid_art

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, default=ROOT.parent / 'output/battle-choreography')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
records = []
for species in ('genesis', 'singularity', 'chronos', 'hollow_king', 'leviathan', 'zodiac'):
    random.seed(917)
    session = make_raid(RAID_BOSSES['ancient'][0], 10)
    # Synthetic high-health enemy keeps all ten simultaneous real moves alive
    # for this renderer stress case; no live state or gameplay rule is changed.
    session.enemy.update(hp=1_000_000, max_hp=1_000_000)
    for uid in session.users:
        actor = build_pve_beast_state(db.get_beast_data(species), 20)
        actor.update(ultimate_charges=3, mana=actor['max_mana'])
        session.party[uid][0] = actor
    before = capture(session)
    for uid in session.users:
        session.resolve_action(uid, 'ultimate')
    after = capture(session)
    start = time.perf_counter()
    try:
        result = raid_art.render(before, after, tuple(session._art_events))
        record = dict(species=species, trainers=10, **gif_record(result))
    except ValueError as exc:
        record = dict(species=species, trainers=10, error=str(exc))
    record['render_ms'] = round((time.perf_counter()-start)*1000, 2)
    records.append(record)
output = args.output / 'raid-stress.json'
output.write_text(json.dumps(dict(scope='Synthetic ten matching simultaneous ultimates against a high-health target; production combat and renderer.', records=records), indent=2)+'\n')
print(json.dumps(records))
sys.exit(any('error' in r for r in records))
