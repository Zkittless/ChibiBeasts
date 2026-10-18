"""Shared, pure progression rules for every reward and evolution path.

The existing XP curves stay unchanged. Rewards buy a small fraction of a
companion level; a normal, equally matched win is one action of progress.
"""
MAX_LEVEL = 50
AWAKENING_LEVEL = 15
AWAKENING_TRAINER_LEVEL = 8
AWAKENING_BOND = 40
AWAKENING_HAPPINESS = 70


def awakening_requirements(evolution=None):
    evolution = evolution or {}
    return {
        'level': evolution.get('level_required', AWAKENING_LEVEL),
        'trainer_level': evolution.get('trainer_level_required', AWAKENING_TRAINER_LEVEL),
        'bond': evolution.get('bond_required', AWAKENING_BOND),
        'happiness': evolution.get('happiness_required', AWAKENING_HAPPINESS),
    }


def evolution_blockers(row, trainer_level, evolution, *, inventory=None, knocked_out=False):
    """Shared requirements for the service and its player-facing preview.

    Inventory is optional so a transaction can consume its own catalyst after
    checking the other requirements. A preview supplies summed item counts.
    """
    blockers = []
    if knocked_out or row.get('hp', 1) <= 0:
        blockers.append('Let your companion recover before evolving.')
    level_required = evolution.get('level_required', 1)
    if row['level'] < level_required:
        blockers.append(f'Reach beast level {level_required} first.')
    if evolution.get('form') == 'bond':
        required = awakening_requirements(evolution)
        if trainer_level < required['trainer_level']:
            blockers.append(f"Reach trainer level {required['trainer_level']} first.")
        if row.get('bond', 0) < required['bond']:
            blockers.append(f"Reach {required['bond']} bond first ({row.get('bond', 0)}/{required['bond']}). Play, battle and train together.")
        if row['happiness'] < required['happiness']:
            blockers.append(f"Your companion needs at least {required['happiness']} happiness to awaken.")
    elif inventory is not None:
        method = evolution['method']
        owns_catalyst = inventory.get(method, 0) > 0
        if evolution.get('form') == 'radiant':
            owns_catalyst = owns_catalyst or inventory.get('sunforge_core', 0) > 0
        if not owns_catalyst:
            blockers.append(f"You need {method.replace('_', ' ').title()}. Check /craft or /ancient.")
    return blockers

RARITY_GROWTH = {
    'common': {'hp': 4, 'attack': 1, 'defense': 1, 'speed': 1, 'mana': 2},
    'uncommon': {'hp': 5, 'attack': 2, 'defense': 1, 'speed': 1, 'mana': 2},
    'rare': {'hp': 6, 'attack': 2, 'defense': 2, 'speed': 2, 'mana': 3},
    'epic': {'hp': 8, 'attack': 3, 'defense': 2, 'speed': 2, 'mana': 4},
    'legendary': {'hp': 10, 'attack': 4, 'defense': 3, 'speed': 3, 'mana': 5},
    'divine': {'hp': 12, 'attack': 5, 'defense': 4, 'speed': 4, 'mana': 6},
    'altered_divine': {'hp': 15, 'attack': 6, 'defense': 5, 'speed': 5, 'mana': 7},
    'corrupted': {'hp': 18, 'attack': 7, 'defense': 6, 'speed': 6, 'mana': 8},
    'ancient': {'hp': 20, 'attack': 8, 'defense': 7, 'speed': 7, 'mana': 9},
    'dev': {'hp': 50, 'attack': 20, 'defense': 20, 'speed': 20, 'mana': 20},
}


def beast_exp_for_level(level):
    return int(15 * max(1, int(level)) ** 1.1)


def trainer_exp_for_level(level):
    return int(60 * max(1, int(level)) ** 1.45)


def _advance(level, exp, gained, curve):
    level = min(MAX_LEVEL, max(1, int(level)))
    exp = max(0, int(exp)) + max(0, int(gained))
    while level < MAX_LEVEL and exp >= curve(level):
        exp -= curve(level)
        level += 1
    return level, 0 if level == MAX_LEVEL else exp


def advance_beast_xp(level, exp, gained):
    return _advance(level, exp, gained, beast_exp_for_level)


def advance_trainer_xp(level, exp, gained):
    return _advance(level, exp, gained, trainer_exp_for_level)


def _encounter_reward(base, actor_level, enemy_level, difficulty, effort):
    tiers = {'trivial': .35, 'easy': .75, 'normal': 1., 'hard': 1.3, 'boss': 1.6}
    if difficulty not in tiers:
        raise ValueError('Unknown encounter difficulty.')
    effort = min(1.5, max(0., float(effort)))
    if effort == 0:
        return 0
    # Difficulty already compares actual combat stats. A lower-level rare can
    # still challenge a common starter: do not penalize that fair fight twice.
    return max(1, round(base * tiers[difficulty] * effort))


def encounter_beast_xp(beast_level, enemy_level=None, *, difficulty='normal', effort=1.0):
    level = min(MAX_LEVEL, max(1, int(beast_level)))
    return _encounter_reward(22 + 4 * level, level, enemy_level, difficulty, effort)


def encounter_trainer_xp(trainer_level, enemy_level=None, *, difficulty='normal', effort=1.0):
    level = min(MAX_LEVEL, max(1, int(trainer_level)))
    return _encounter_reward(52 + 16 * level, level, enemy_level, difficulty, effort)


ACTIVITY_XP = {'training': (10, 40), 'feed': (20, 60)}


def activity_beast_xp(kind, level, daily_earned=0):
    """Bound the optional gold-to-XP route; caller persists today's earned XP."""
    reward, daily_cap = ACTIVITY_XP[kind]
    if int(level) >= MAX_LEVEL:
        return 0
    return min(reward, max(0, daily_cap - max(0, int(daily_earned))))


def rarity_growth(rarity, levels):
    return {stat: amount * max(0, int(levels))
            for stat, amount in RARITY_GROWTH.get(rarity, RARITY_GROWTH['common']).items()}


def companion_growth(beast_id, level, catalog):
    """Canonical evolution growth with gradual catch-up to the new tier.

    At its unlock, a form inherits its ancestor's earned growth. Afterwards
    its new rarity applies, and the ancestral rarity deficit is distributed
    across the levels remaining to 50. Every form reaches its full tier's
    growth at the cap. Level-50 ascensions award that earned tier immediately.
    A direct grant and a normally evolved companion therefore agree.
    """
    level = min(MAX_LEVEL, max(1, int(level)))
    beast = catalog[beast_id]
    parent = next((entry for entry in catalog.values()
                   if (entry.get('evolution') or {}).get('evolves_to') == beast_id), None)
    if parent is None:
        return rarity_growth(beast['rarity'], level - 1)
    milestone = parent['evolution']['level_required']
    if milestone >= MAX_LEVEL and level == MAX_LEVEL:
        return rarity_growth(beast['rarity'], level - 1)
    inherited = companion_growth(parent['id'], min(level, milestone), catalog)
    future = rarity_growth(beast['rarity'], max(0, level - milestone))
    native_at_unlock = rarity_growth(beast['rarity'], milestone - 1)
    progress = max(0, level - milestone)
    remaining = max(1, MAX_LEVEL - milestone)
    return {stat: inherited[stat] + future[stat]
            + (native_at_unlock[stat] - inherited[stat]) * progress // remaining
            for stat in inherited}
