"""Validate an item effect before consuming it, using fresh inventory and live state."""
import copy
import random
import aiosqlite
from utils import db as storage, combat
from utils.gameplay import shorten_incubation
ELEMENTS = ('fire', 'water', 'ice', 'earth', 'wind', 'nature', 'arcane', 'shadow', 'cosmic', 'light')

async def use_item(user_id, item_id, *, choice=None, beast_number=None):
    item = storage.load_items().get(item_id)
    if not item:
        raise ValueError('That item has no usable definition.')
    effect = item['effect']
    if item['type'] in {'evolution', 'unlock', 'summon', 'blueprint'} or effect.get('evolution_item'):
        command = '/ancient' if item['type'] == 'summon' else '/build' if item['type'] == 'blueprint' else '/evolve'
        raise ValueError(f"Use {command} to spend {item['name']} on an eligible target. It has not been consumed.")
    async with combat.lock_states([user_id]):
        live = combat.active_states.get(user_id)
        if live and live.get('closed'):
            raise ValueError('Your battle is settling. Try again in a moment.')
        from utils.duels import active_fighters
        if user_id in active_fighters and live is None:
            raise ValueError('Your battle or transfer is preparing. Try again in a moment.')
        party = combat.active_parties.get(user_id, [live] if live else [])
        updated_party = [copy.deepcopy(state) for state in party]
        updated = next((state for state in updated_party if live and state['id'] == live['id']), None)
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            await db.execute('BEGIN IMMEDIATE')
            async with db.execute('SELECT * FROM player_inventory WHERE user_id=? AND item_id=? AND quantity>0', (user_id, item_id)) as c:
                inv = await c.fetchone()
            if not inv:
                raise ValueError(f"You do not have {item['name']}.")
            if beast_number is not None:
                async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND player_number=?', (user_id, beast_number)) as c:
                    row = await c.fetchone()
            elif live:
                async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND id=?', (user_id, live['id'])) as c:
                    row = await c.fetchone()
            else:
                async with db.execute('SELECT * FROM player_beasts WHERE user_id=? AND is_active=1', (user_id,)) as c:
                    row = await c.fetchone()
            beast = dict(row) if row else None
            async with db.execute('SELECT * FROM players WHERE user_id=?', (user_id,)) as c:
                player = dict(await c.fetchone())
            if beast:
                updated = next((state for state in updated_party if state['id'] == beast['id']), None)
            target = updated or beast
            no_beast = {'choose_element', 'clear_title', 'reduce_incubation_percent', 'reduce_incubation_hours', 'reset_explore', 'reset_challenge', 'encounter_boost', 'raid_damage_boost', 'catch_boost_charges', 'guaranteed_rare_charges', 'guild_title', 'revive_party'}
            if not beast and (not no_beast.intersection(effect)):
                raise ValueError('Open /journal → Companions and make a companion active first.')
            messages = []
            if effect.get('choose_element'):
                if choice not in ELEMENTS:
                    raise ValueError('Choose an element first.')
                if player.get('astral_type'):
                    raise ValueError('Your previous attunement is still waiting for an instant hatch.')
                await db.execute('UPDATE players SET astral_type=? WHERE user_id=?', (choice, user_id))
                messages.append(f'Your next eligible instant hatch is attuned to {choice}. Check its egg pool before hatching.')
            if effect.get('rename'):
                name = (choice or '').strip()
                if not 1 <= len(name) <= 40 or '\n' in name:
                    raise ValueError('Choose a name of 1–40 characters on one line.')
                name = __import__('discord').utils.escape_mentions(name)
                await db.execute('UPDATE player_beasts SET nickname=? WHERE id=?', (name, beast['id']))
                messages.append(f'Your companion is now called {name}.')
                if updated:
                    updated['name'] = name
            if effect.get('clear_title'):
                if not player.get('title'):
                    raise ValueError('Your title is already clear. The token is yours to keep.')
                await db.execute('UPDATE players SET title=NULL WHERE user_id=?', (user_id,))
                messages.append('Your title is clear. Earned titles remain available through /title.')
            if effect.get('reduce_incubation_percent') or effect.get('reduce_incubation_hours'):
                messages.append(await shorten_incubation(db, user_id, hours=effect.get('reduce_incubation_hours', 0), percent=effect.get('reduce_incubation_percent', 0)))
            if effect.get('revive_party'):
                await db.execute('UPDATE player_beasts SET hp=max_hp,knocked_out_until=NULL WHERE user_id=? AND (raid_slot IS NOT NULL OR is_active=1)', (user_id,))
                messages.append('Your active companion and raid party are fully recovered.')
                for member in updated_party:
                    member.update(hp=member['max_hp'], status=None, status_turns=0, ko_recorded=False)
            elif effect.get('revive'):
                from utils.db import is_knocked_out
                if not beast or not (is_knocked_out(beast) or beast['hp'] <= 0):
                    raise ValueError('Choose a knocked-out beast with the optional beast number.')
                hp = max(1, int(beast['max_hp'] * effect.get('heal_percent', 50) / 100))
                await db.execute('UPDATE player_beasts SET hp=?,knocked_out_until=NULL WHERE id=?', (hp, beast['id']))
                messages.append(f'Revived with {hp} HP.')
                if updated:
                    updated.update(hp=hp, status=None, status_turns=0, ko_recorded=False)
            elif effect.get('heal_percent'):
                if storage.is_knocked_out(beast) or target['hp'] <= 0:
                    raise ValueError('A knocked-out companion needs a revive before ordinary healing.')
                missing = target['max_hp'] - target['hp']
                if missing <= 0 or target.get('status') == 'blight':
                    raise ValueError('This beast cannot benefit from healing right now. The item is yours to keep.')
                hp = min(target['max_hp'], target['hp'] + int(target['max_hp'] * effect['heal_percent'] / 100))
                restored = hp - target['hp']
                if updated:
                    updated['hp'] = hp
                else:
                    await db.execute('UPDATE player_beasts SET hp=? WHERE id=?', (hp, beast['id']))
                messages.append(f'Restored {restored} HP.')
            if 'happiness' in effect:
                if beast['happiness'] >= 100 and len(effect) == 1:
                    raise ValueError('Your companion is already completely happy.')
                happiness = min(100, beast['happiness'] + effect['happiness'])
                await db.execute('UPDATE player_beasts SET happiness=? WHERE id=?', (happiness, beast['id']))
                messages.append(f'Happiness: {happiness}/100.')
            if effect.get('damage_multiplier_permanent'):
                old = beast.get('damage_bonus', 0)
                if old >= 25:
                    raise ValueError('This beast already has the maximum 25% Ambrosia bonus.')
                bonus = min(25, old + effect['damage_multiplier_permanent'])
                await db.execute('UPDATE player_beasts SET damage_bonus=? WHERE id=?', (bonus, beast['id']))
                if updated:
                    updated['damage_multiplier'] *= (1 + bonus / 100) / (1 + old / 100)
                messages.append(f'Permanent damage bonus: +{bonus}% (maximum 25%).')
            if effect.get('exp') or effect.get('instant_levels'):
                if live:
                    raise ValueError('Finish your battle before giving growth items.')
                from utils.rewards import activity_exp
                from utils.progression import advance_beast_xp, MAX_LEVEL
                if beast['level'] >= MAX_LEVEL:
                    raise ValueError('This companion is already at level 50. The item is yours to keep.')
                requested = effect.get('exp', 0) or 60
                gained = await activity_exp(db, user_id, beast, 'feed', requested=requested)
                if not gained:
                    raise ValueError('This companion has used its 60 growth-treat EXP for today. Try again after midnight UTC; the item is yours to keep.')
                level, exp = advance_beast_xp(beast['level'], beast['exp'], gained)
                await storage.apply_beast_levelup(db, beast, level, exp)
                messages.append(f'Beast level {level}; EXP {exp}. Gained {gained} EXP (60 daily growth-treat EXP limit).')
            if effect.get('stat_boost'):
                from utils.economy_balance import STAT_TREAT_LIMIT
                if live:
                    raise ValueError('Finish your battle before giving permanent stat treats.')
                if beast['stat_treats_used'] >= STAT_TREAT_LIMIT:
                    raise ValueError(f'This companion has reached its {STAT_TREAT_LIMIT}-treat limit.')
                stat = random.choice(['attack', 'defense', 'speed'])
                amount = effect['stat_boost']
                await db.execute(f'UPDATE player_beasts SET {stat}={stat}+?,stat_treats_used=stat_treats_used+1 WHERE id=?', (amount, beast['id']))
                if updated:
                    updated[stat] += amount
                messages.append(f'Permanent {stat}: +{amount}. Treats: {beast["stat_treats_used"]+1}/{STAT_TREAT_LIMIT}.')
            if effect.get('status_ward'):
                if updated:
                    updated['status'] = None
                    updated['status_ward'] = True
                else:
                    await db.execute('UPDATE players SET status_ward_charges=status_ward_charges+1 WHERE user_id=?', (user_id,))
                messages.append('Status cleared; an Aether ward blocks the next condition.')
            if effect.get('grant_ultimate_charge'):
                current = updated.get('ultimate_charges', 0) if updated else player['ultimate_charges']
                if current >= 3:
                    raise ValueError('Your ultimate is already fully charged.')
                charges = min(3, current + effect['grant_ultimate_charge'])
                await db.execute('UPDATE players SET ultimate_charges=? WHERE user_id=?', (charges, user_id))
                if updated:
                    updated['ultimate_charges'] = charges
                messages.append(f'Ultimate charges: {charges}/3.')
            for key, stat, column in [('defense_boost_percent', 'defense', 'next_defense_boost'), ('speed_boost_percent', 'speed', 'next_speed_boost')]:
                if effect.get(key):
                    if updated:
                        if updated.get('item_'+key,0):
                            raise ValueError(f'A {stat} item is already active for this battle.')
                        updated[stat] += int(updated.get('base_'+stat,updated[stat]) * effect[key] / 100)
                        updated['item_'+key] = effect[key]
                    else:
                        if player[column]:
                            raise ValueError(f'A {stat} item is already queued for your next battle.')
                        await db.execute(f'UPDATE players SET {column}=MAX({column},?) WHERE user_id=?', (effect[key], user_id))
                    messages.append(f"+{effect[key]}% {stat} in {('this' if updated else 'your next')} battle.")
            if effect.get('reset_explore') or effect.get('reset_challenge'):
                await db.execute('UPDATE players SET explore_last_at=0,challenge_last_at=0 WHERE user_id=?', (user_id,))
                messages.append('Exploration and challenge cooldowns reset.')
            flags = [('encounter_boost', 'incense_charges', effect.get('charges', 3)), ('raid_damage_boost', 'raid_totem_charges', 1), ('catch_boost_charges', 'catch_boost_charges', effect.get('catch_boost_charges', 0)), ('guaranteed_rare_charges', 'ancient_compass_charges', effect.get('guaranteed_rare_charges', 0))]
            for key, column, amount in flags:
                if effect.get(key):
                    await db.execute(f'UPDATE players SET {column}={column}+? WHERE user_id=?', (amount, user_id))
                    messages.append(f"{item['name']} is ready for {amount} encounter(s).")
            if effect.get('guild_title'):
                await db.execute("INSERT OR IGNORE INTO achievements(user_id,achievement_id) VALUES(?,'guild_heart')", (user_id,))
                await db.execute("UPDATE players SET title='Guild Heart' WHERE user_id=?", (user_id,))
                messages.append('Guild Heart title unlocked and equipped.')
            if not messages:
                raise ValueError('Use this item through its matching game command. It has not been consumed.')
            await db.execute('UPDATE player_inventory SET quantity=quantity-1 WHERE id=? AND quantity>0', (inv['id'],))
            await db.execute('DELETE FROM player_inventory WHERE id=? AND quantity<=0', (inv['id'],))
            await db.commit()
        for original, changed in zip(party, updated_party):
            original.update(changed)
    from utils.progress import track_quest_event
    await track_quest_event(user_id, 'use_item')
    return '\n'.join(messages)
