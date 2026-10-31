"""Fresh journal snapshots and durable goals; UI pages never own inventory."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache

import aiosqlite

from utils import crafting, db as storage, expeditions, hollowlight, story
from utils.catalog import load_catalog, catalog_revision
from utils.economy_balance import material_sources, recipe_level
from utils.habitats import BIOMES, REGIONAL_MATERIALS, biome_key, eligible
from utils.sanctuary import apply_craft_discount


def habitats(species):
    return list(_habitats(species, catalog_revision()))


@lru_cache(maxsize=128)
def _habitats(species, revision):
    beast = storage.get_beast_data(species)
    if not beast or not eligible(beast):
        return ()
    return tuple(key for key, realm in BIOMES.items() if realm['weights'].get(beast['rarity'], 0) > 0
                 and beast['type'] in realm['types'])


def hunt_targets():
    return sorted((b for b in storage.load_beasts().values() if eligible(b) and habitats(b['id'])),
                  key=lambda b: (min(BIOMES[k]['min_level'] for k in habitats(b['id'])), b['name']))


async def pin_goal(user_id, kind, target):
    target = str(target)
    expected = ''
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        await db.execute('BEGIN IMMEDIATE')
        if kind == 'recipe':
            if target not in crafting.recipes():
                raise ValueError('Choose a recipe from the workshop.')
        elif kind == 'creature':
            if not habitats(target):
                raise ValueError('Choose a creature that can be found while exploring.')
        elif kind == 'awakening':
            async with db.execute('SELECT beast_id FROM player_beasts WHERE id=? AND user_id=?', (target, user_id)) as c:
                row = await c.fetchone()
            evo = (storage.get_beast_data(row['beast_id']).get('evolution') or {}) if row else {}
            if evo.get('form') != 'bond':
                raise ValueError('Choose a companion with a bond awakening still ahead.')
            expected = evo['evolves_to']
        else:
            raise ValueError('Choose a recipe, awakening or creature goal.')
        await db.execute('INSERT INTO journal_goals VALUES(?,?,?,?) ON CONFLICT(user_id) '
                         'DO UPDATE SET kind=excluded.kind,target=excluded.target,expected_species=excluded.expected_species',
                         (user_id, kind, target, expected))
        await db.commit()


async def clear_goal(user_id):
    async with storage.connect() as db:
        await db.execute('DELETE FROM journal_goals WHERE user_id=?', (user_id,))
        await db.commit()


async def save_explore_default(user_id, realm):
    """Save a destination preference without starting a trip or spending."""
    if realm not in BIOMES:
        raise ValueError('Choose a destination from your journal.')
    async with storage.connect() as db:
        await db.execute('BEGIN IMMEDIATE')
        async with db.execute('SELECT level FROM players WHERE user_id=?', (user_id,)) as cursor:
            player = await cursor.fetchone()
        if not player:
            raise ValueError('Open your Field Journal before saving a destination.')
        if player[0] < BIOMES[realm]['min_level']:
            raise ValueError(f"Reach trainer level {BIOMES[realm]['min_level']} to save this destination.")
        await db.execute('INSERT INTO exploration_preferences(user_id,realm) VALUES(?,?) '
                         'ON CONFLICT(user_id) DO UPDATE SET realm=excluded.realm', (user_id, realm))
        await db.commit()


async def clear_explore_default(user_id):
    async with storage.connect() as db:
        await db.execute('DELETE FROM exploration_preferences WHERE user_id=?', (user_id,))
        await db.commit()


def explore_destination(state, requested=None):
    """Resolve an opening selection; explicit choices never update the default."""
    if requested is not None:
        return (requested, '') if requested in BIOMES else ('woods', 'Choose an available destination from your journal.')
    saved = state.explore_default
    if saved is None:
        return 'woods', ''
    if saved in BIOMES and state.player['level'] >= BIOMES[saved]['min_level']:
        return saved, ''
    return 'woods', 'Your saved destination is unavailable at your current trainer level. Choose a destination or clear the default.'


def goal_status(goal, state):
    """Compute progress from current ownership, recipe costs and level gates."""
    if not goal:
        return None
    kind, target = goal['kind'], goal['target']
    result = dict(kind=kind, target=target, done=False, action='', realm=None)
    if kind == 'recipe':
        item = crafting.recipes().get(target)
        if not item:
            return {**result, 'name': 'Recipe unavailable', 'text': 'Choose another goal.'}
        costs = apply_craft_discount(item['recipe'], state.sanctuary)
        mats = load_catalog('materials.json')['materials']
        owned = state.owned.get(target, 0) or any(target in (r['armor_id'], r['rune_id']) for r in state.companions)
        missing = [mid for mid, n in costs.items() if state.materials.get(mid, 0) < n]
        result.update(name=item['name'], done=bool(owned), action='workshop')
        progress = ' · '.join(f"{mats[mid]['name']} {min(state.materials.get(mid, 0), n)}/{n}" for mid, n in costs.items())
        if owned:
            result['text'] = 'Crafted! Open the workshop to equip or use it.'
        elif state.level < recipe_level(item):
            result['text'] = f"Trainer Lv.{state.level}/{recipe_level(item)} to craft.\n{progress}"
        elif not missing:
            result['text'] = 'All materials ready. Craft it in the workshop.'
        else:
            first = missing[0]
            regions = [key for key in BIOMES if first in REGIONAL_MATERIALS[biome_key(key)]]
            unlocked = [key for key in dict.fromkeys(regions) if state.level >= BIOMES[key]['min_level']]
            result['realm'] = min(unlocked, key=lambda k: BIOMES[k]['min_level']) if unlocked else None
            hint = f"Next: gather in {BIOMES[result['realm']]['name']}." if unlocked else material_sources(first)
            result['text'] = progress + '\n' + hint
    elif kind == 'awakening':
        row = next((r for r in state.companions if str(r['id']) == target), None)
        result['name'] = 'Bond awakening'
        if not row:
            result['text'] = 'This companion has left your collection. Choose another goal.'
            return result
        beast = storage.get_beast_data(row['beast_id'])
        expected = goal['expected_species']
        descendants = set()
        while expected and expected not in descendants:
            descendants.add(expected)
            expected = (storage.get_beast_data(expected).get('evolution') or {}).get('evolves_to')
        result.update(name=f"{row['nickname'] or beast['name']} · Awakening", companion=row)
        if row['beast_id'] in descendants:
            result.update(done=True, text='Awakening complete. Your shared progress carried into the new form.')
        elif (beast.get('evolution') or {}).get('form') != 'bond':
            result['text'] = 'This companion has changed. Choose another goal.'
        else:
            evo = beast['evolution']
            bond_required = evo.get('bond_required', 40)
            happiness_required = evo.get('happiness_required', 70)
            trainer_required = evo.get('trainer_level_required', 8)
            ready = (row['level'] >= evo['level_required'] and row['bond'] >= bond_required
                     and row['happiness'] >= happiness_required and state.level >= trainer_required)
            guidance = 'Ready to awaken together.' if ready else 'Play, train and battle together to grow your bond.'
            if ready and (row['listed'] or row['hp'] <= 0 or storage.is_knocked_out(row)):
                ready = False
                guidance = 'Delist this companion before awakening.' if row['listed'] else 'Let this companion recover before awakening.'
            result.update(action='awaken' if ready else 'companions',
                          text=f"Companion Lv.{min(row['level'], evo['level_required'])}/{evo['level_required']} · Trainer Lv.{min(state.level, trainer_required)}/{trainer_required} · Bond {min(row['bond'], bond_required)}/{bond_required} · Happiness {min(row['happiness'], happiness_required)}/{happiness_required}\n" +
                          guidance)
    else:
        beast = storage.get_beast_data(target)
        if not beast:
            return {**result, 'name': 'Creature unavailable', 'text': 'Choose another goal.'}
        done = any(row['beast_id'] == target for row in state.companions)
        regions = habitats(target)
        if not regions:
            return {**result, 'name': beast['name'], 'done': done,
                    'text': 'Found! This creature is in your collection.' if done else
                    'This creature has no current wild habitat. Unpin this goal and choose another creature.'}
        unlocked = [key for key in regions if state.level >= BIOMES[key]['min_level']]
        result.update(name=beast['name'], done=done, action='adventure',
                      realm=unlocked[0] if unlocked else None,
                      text='Found! This creature is in your collection.' if done else
                      ('Search ' + ' or '.join(BIOMES[k]['name'] for k in unlocked) + '. Encounters remain random.' if unlocked else
                       'Reach trainer Lv.' + str(min(BIOMES[k]['min_level'] for k in regions)) + ' to reach its habitat.'))
    return result


@dataclass
class JournalState:
    player: dict
    workshop: crafting.WorkshopState
    energy: expeditions.Energy
    trip: dict | None
    goal: dict | None
    story: dict
    team: list = field(default_factory=list)
    team_slots: dict = field(default_factory=dict)
    explore_default: str | None = None
    carnival: dict = field(default_factory=dict)
    trial: dict | None = None


async def snapshot(user_id):
    from utils import trials
    # Expiry can bank rewards and restore stored HP/MP. Read the journal's
    # player/team snapshot after that settlement, never from the old state.
    trial = await trials.current(user_id)
    await storage.get_active_beast(user_id)  # Apply expired recovery timers first.
    workshop = await crafting.workshop_state(user_id)
    energy = await expeditions.energy(user_id)
    trip = await expeditions.current(user_id)
    async with storage.connect() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as c:
            player = dict(await c.fetchone())
        async with db.execute("SELECT 1 FROM daily_quests WHERE user_id=? AND quest_id='daily_claim' AND date=?", (user_id, datetime.now(timezone.utc).strftime('%Y-%m-%d'))) as c:
            player['_daily_claimed'] = bool(await c.fetchone())
        async with db.execute('SELECT * FROM journal_goals WHERE user_id=?', (user_id,)) as c:
            row = await c.fetchone()
            goal = dict(row) if row else None
        async with db.execute('SELECT realm FROM exploration_preferences WHERE user_id=?', (user_id,)) as c:
            preference = await c.fetchone()
        state = await story.read_state(db, user_id)
    from utils import adventure_party
    team = await adventure_party.rows(user_id)
    slots = await adventure_party.assignments(user_id)
    return JournalState(player, workshop, energy, trip, goal_status(goal, workshop), state, team, slots,
                        preference['realm'] if preference else None, await hollowlight.status(user_id), trial)
