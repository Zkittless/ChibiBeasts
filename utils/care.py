"""Read-only Care previews; spending stays in the existing item transaction."""
import copy
from dataclasses import dataclass, field

import aiosqlite

from utils import combat, db as storage, owned_roster
from utils.duels import active_fighters
from utils.economy_balance import STAT_TREAT_LIMIT
from utils.items import care_fingerprint, growth_treat_earned, party_recovery_targets
from utils.progression import advance_beast_xp, MAX_LEVEL

SUPPORTED = frozenset(('heal_percent', 'revive', 'revive_party', 'happiness',
    'damage_multiplier_permanent', 'exp', 'instant_levels', 'stat_boost',
    'status_ward', 'grant_ultimate_charge', 'defense_boost_percent',
    'speed_boost_percent', 'rename'))


@dataclass
class CareState:
    rows: list = field(default_factory=list)
    player: dict = field(default_factory=dict)
    inventory: dict = field(default_factory=dict)
    selected_id: int | None = None
    beast: dict | None = None
    target: dict | None = None
    party: list = field(default_factory=list)
    listed_ids: set = field(default_factory=set)
    preparing: bool = False
    in_battle: bool = False
    live_target: bool = False
    feed_earned: int = 0
    trial_locked: bool = False


@dataclass(frozen=True)
class CarePreview:
    item_id: str
    name: str
    quantity: int
    text: str
    usable: bool
    reason: str
    requires_confirmation: bool
    needs_choice: bool
    fingerprint: str


def recovery_rows(state):
    """Only patients, using live battle HP when it differs from collection HP."""
    live = {row['id']: row for row in state.party}
    if state.live_target and state.target:
        live[state.selected_id] = state.target
    patients = []
    for row in state.rows:
        health = live.get(row['id'], row)
        if (storage.is_knocked_out(row) or health['hp'] < health['max_hp']
                or health['hp'] <= 0 or health.get('status')):
            patients.append({**row, 'hp': health['hp'], 'max_hp': health['max_hp'], 'status': health.get('status')})
    return patients


async def snapshot(user_id, selected_id=None):
    # Apply the normal expired-KO recovery before collecting the locked view.
    await storage.get_player_beasts(user_id)
    async with combat.lock_states([user_id]):
        live = combat.active_states.get(user_id)
        party = copy.deepcopy(combat.active_parties.get(user_id, [live] if live else []))
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN')
            async with db.execute('SELECT * FROM player_beasts WHERE user_id=? ORDER BY COALESCE(player_number,id),id', (user_id,)) as cursor:
                rows = [dict(row) for row in await cursor.fetchall()]
            if selected_id is None:
                selected = next((row for row in rows if row['is_active']), rows[0] if rows else None)
                selected_id = selected['id'] if selected else None
            beast = next((row for row in rows if row['id'] == selected_id), None)
            async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as cursor:
                player_row = await cursor.fetchone()
                player = dict(player_row) if player_row else {}
            async with db.execute('SELECT item_id,SUM(quantity) AS quantity FROM player_inventory WHERE user_id=? AND quantity>0 GROUP BY item_id', (user_id,)) as cursor:
                inventory = {row['item_id']: row['quantity'] for row in await cursor.fetchall()}
            async with db.execute('SELECT beast_row_id FROM beast_market WHERE seller_id=?', (user_id,)) as cursor:
                listed = {row[0] for row in await cursor.fetchall()}
            recovery = await party_recovery_targets(db, user_id, party)
            earned = await growth_treat_earned(db, beast['id']) if beast else 0
            from utils.trial_locks import member_ids
            frozen = await member_ids(db, user_id)
        live_target = next((row for row in party if beast and row['id'] == beast['id']), None)
        return CareState(rows, player, inventory, selected_id, beast, live_target or beast,
                         recovery, listed,
                         bool(live and live.get('closed') or user_id in active_fighters and live is None),
                         bool(live), live_target is not None, earned, bool(beast and beast['id'] in frozen))


def supported_items(state):
    catalog = storage.load_items()
    ids = [key for key, quantity in state.inventory.items() if quantity > 0
           and key in catalog and SUPPORTED.intersection(catalog[key]['effect'])
           and not catalog[key]['effect'].get('evolution_item')]
    def order(key):
        effect = catalog[key]['effect']
        group = 0 if effect.get('heal_percent') or effect.get('revive') else 1
        if effect.get('revive_party') or effect.get('damage_multiplier_permanent') or effect.get('stat_boost'):
            group = 2
        return group, catalog[key]['name']
    return sorted(ids, key=order)


def preview(state, item_id, choice=None):
    item = storage.load_items().get(item_id, {})
    effect = item.get('effect', {})
    name = item.get('name', 'Choose a care item')
    quantity = state.inventory.get(item_id, 0)
    beast, target, player = state.beast, state.target, state.player
    members = state.party if effect.get('revive_party') else []
    token = care_fingerprint(beast, target, player, members, state.feed_earned)
    lines, reasons = [], []
    confirm = bool(effect.get('revive_party') or effect.get('damage_multiplier_permanent') or effect.get('stat_boost'))
    needs_choice = bool(effect.get('rename') and not choice)
    if not item or not SUPPORTED.intersection(effect):
        reasons.append('Choose a companion care item from your inventory.')
    if quantity <= 0:
        reasons.append('None left. Choose another owned item.')
    if not beast:
        reasons.append('This companion is no longer in your collection. Choose another companion.')
    if state.preparing:
        reasons.append('Your battle or transfer is preparing or settling. Refresh in a moment.')
    if state.trial_locked:
        reasons.append('This companion is on a tower trial. Use its between-wave care panel, or retreat and bank before other care.')
    affected_ids = {row['id'] for row in members} if effect.get('revive_party') else {state.selected_id}
    if affected_ids.intersection(state.listed_ids):
        reasons.append('Delist the affected companion before using items.')
    if beast and target:
        ko = storage.is_knocked_out(beast) or target['hp'] <= 0
        if effect.get('revive_party'):
            if not members:
                reasons.append('Assign a team before using party recovery.')
            elif not any(row['hp'] < row['max_hp'] or storage.is_knocked_out(row) or row.get('status') for row in members):
                reasons.append('Your assigned teams are already fully recovered.')
            lines.append('Fully recover the current battle team.' if state.in_battle else 'Fully recover your active companion and assigned adventure/raid teams.')
            lines.extend(f"#{owned_roster.number(row)} {owned_roster.name(row)}: {row['hp']}/{row['max_hp']} → {row['max_hp']}/{row['max_hp']} HP" for row in members)
        elif effect.get('revive'):
            if not ko:
                reasons.append('This companion is not knocked out. Choose an ordinary heal instead.')
            restored = max(1, int(target['max_hp'] * effect.get('heal_percent', 50) / 100))
            lines.append(f"Revive: {target['hp']}/{target['max_hp']} → {restored}/{target['max_hp']} HP; clear the recovery timer.")
        elif effect.get('heal_percent'):
            if ko:
                reasons.append('A knocked-out companion needs a Phoenix Elixir before ordinary healing.')
            elif target.get('status') == 'blight':
                reasons.append('Blight blocks healing. Clear the condition with an Aether Tonic first.')
            hp = min(target['max_hp'], target['hp'] + int(target['max_hp'] * effect['heal_percent'] / 100))
            if hp <= target['hp']:
                reasons.append('Already at full health; this item cannot restore HP.')
            lines.append(f"{target['hp']}/{target['max_hp']} → {hp}/{target['max_hp']} HP (+{max(0, hp-target['hp'])}).")
        if 'happiness' in effect:
            happy = min(100, beast['happiness'] + effect['happiness'])
            if beast['happiness'] >= 100 and len(effect) == 1:
                reasons.append('Already completely happy.')
            lines.append(f"Happiness: {beast['happiness']}/100 → {happy}/100.")
        if effect.get('damage_multiplier_permanent'):
            old = beast.get('damage_bonus', 0)
            if old >= 25:
                reasons.append('Already at the maximum 25% Ambrosia damage bonus.')
            lines.append(f"Permanent damage bonus: +{old}% → +{min(25, old+effect['damage_multiplier_permanent'])}% (25% maximum).")
        if effect.get('stat_boost'):
            used = beast.get('stat_treats_used', 0)
            if state.in_battle:
                reasons.append('Finish your battle before giving permanent stat treats.')
            if used >= STAT_TREAT_LIMIT:
                reasons.append(f'Already at the {STAT_TREAT_LIMIT}-treat limit.')
            lines.append(f"Permanently add {effect['stat_boost']} to one random Attack, Defense or Speed stat; the stat is rolled when used. Treats: {used}/{STAT_TREAT_LIMIT} → {min(STAT_TREAT_LIMIT,used+1)}/{STAT_TREAT_LIMIT}.")
        if effect.get('exp') or effect.get('instant_levels'):
            if state.in_battle:
                reasons.append('Finish your battle before giving growth treats.')
            if beast['level'] >= MAX_LEVEL:
                reasons.append('Already at companion level 50.')
            gained = min(effect.get('exp', 0) or 60, max(0, 60-state.feed_earned)) if beast['level'] < MAX_LEVEL else 0
            if not gained and beast['level'] < MAX_LEVEL:
                reasons.append('The 60 growth-treat EXP allowance is used for today. It resets at midnight UTC.')
            level, exp = advance_beast_xp(beast['level'], beast['exp'], gained)
            lines.append(f"+{gained} EXP: Lv.{beast['level']} ({beast['exp']} EXP) → Lv.{level} ({exp} EXP). Today's growth-treat EXP: {state.feed_earned}/60 → {state.feed_earned+gained}/60.")
        if effect.get('status_ward'):
            if state.live_target:
                if target.get('status_ward') and not target.get('status'):
                    reasons.append('This companion is already protected by an Aether ward.')
                lines.append('Clear the current condition and block the next status condition in this battle.')
            else:
                count = player.get('status_ward_charges', 0)
                lines.append(f"Trainer effect: queue an Aether ward for the next battle. Stored wards: {count} → {count+1}.")
        if effect.get('grant_ultimate_charge'):
            current = target.get('ultimate_charges', 0) if state.live_target else player.get('ultimate_charges', 0)
            if current >= 3:
                reasons.append('The ultimate meter is already fully charged.')
            scope = 'This companion' if state.live_target else 'Trainer ultimate meter'
            lines.append(f"{scope}: {current}/3 → {min(3,current+effect['grant_ultimate_charge'])}/3 charges.")
        for key, stat, column in (('defense_boost_percent', 'defense', 'next_defense_boost'), ('speed_boost_percent', 'speed', 'next_speed_boost')):
            if effect.get(key):
                if state.live_target:
                    if target.get('item_'+key, 0):
                        reasons.append(f'A {stat} item is already active for this battle.')
                    increase = int(target.get('base_'+stat, target[stat]) * effect[key] / 100)
                    lines.append(f"This battle: {stat.title()} {target[stat]} → {target[stat]+increase} (+{effect[key]}% of base).")
                else:
                    if player.get(column):
                        reasons.append(f'A {stat} item is already queued for the next battle.')
                    lines.append(f"Trainer effect: +{effect[key]}% {stat} queued for the next battle.")
        if effect.get('rename'):
            if not choice:
                lines.append('Choose the new name, then review it before using one token.')
            elif not 1 <= len(choice.strip()) <= 40 or '\n' in choice:
                reasons.append('Choose a name of 1–40 characters on one line.')
            else:
                lines.append(f"Name: {owned_roster.name(beast)} → {choice.strip()}.")
    reason = reasons[0] if reasons else 'Choose a name before using the token.' if needs_choice else ''
    return CarePreview(item_id, name, quantity, '\n'.join(lines), not reasons and not needs_choice,
                       reason, confirm, needs_choice and not reasons, token)
