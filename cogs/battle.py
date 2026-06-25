import discord
from discord import app_commands
from discord.ext import commands
import random
import asyncio
import aiosqlite
import json
from utils.db import (
    get_or_create_player, get_player, update_player,
    get_active_beast, get_beast_data, calc_player_exp_for_level,
    apply_beast_levelup, calc_exp_for_level, get_beast_exp_for_level
)
from utils.theme import COLORS, RARITY_EMOJI, TYPE_EMOJI, hp_bar, SPARKLE
from utils.progress import (
    track_quest_event, check_achievements, notify_unlocks, notify_quest_completions
)
from utils.type_chart import get_type_multiplier, type_effectiveness_label

# ── Module-level equipment cache ─────────────────────────────────────────────
# Loaded once at import time rather than on every battle start.
# equipment.json is read-only at runtime — no need to re-read per request.
# bot.py sets the working directory to its own folder at startup, so this
# relative path resolves correctly on Railway and locally.
with open("data/equipment.json") as _eq_f:
    _EQ_DATA = json.load(_eq_f)
_ALL_GEAR: dict = {**_EQ_DATA["equipment"], **_EQ_DATA["runes"]}

STATUS_EFFECTS = {
    "poison":  {"damage_per_turn": 0.05, "emoji": "☠️"},
    "burn":    {"damage_per_turn": 0.08, "emoji": "🔥"},
    "freeze":  {"skip_turns": 1,          "emoji": "❄️"},
    "sleep":   {"skip_turns": 2,          "emoji": "💤"},
    "paralyze":{"speed_reduce": 0.5,      "emoji": "⚡"},
    "blind":   {"miss_chance": 0.30,      "emoji": "👁️"},
    "blight":  {"damage_per_turn": 0.06, "no_heal": True, "emoji": "💜"},
}

pending_battles: dict[int, dict] = {}

# ── PvE AI Engine ─────────────────────────────────────────────────────────────

def ai_pick_move(ai_state: dict, player_state: dict, personality: str) -> tuple[str, bool]:
    """
    Select an AI move based on the NPC/wild personality.

    Returns (move_name, is_ultimate).

    Type advantage is checked at the beast level (ai_type vs opp_type) because
    moves in ChibiBeasts inherit the beast's element — there is no per-move type
    field in the data model. All moves benefit equally from a type matchup.

    Personalities:
      defensive  — Maren/Barkley: consistent, cautious, holds ultimate for emergencies
      chaotic    — Cael/Twine: purely random, ignores all heuristics
      aggressive — Sable/Hellhound: maximum pressure, uses ultimate whenever available
      patient    — Orren/Dryad: type-advantage first, conservative ultimate use
      optimal    — The Archivist/Paradox: mathematically best option each turn
      wild       — random wild beast: weighted toward type-advantage, occasional ultimate
    """
    moves    = ai_state["moves"]
    ultimate = ai_state["ultimate"]
    can_ult  = ai_state["mana"] >= 50
    hp_ratio = ai_state["hp"] / max(ai_state["max_hp"], 1)
    ai_type  = ai_state.get("beast_type", "")
    opp_type = player_state.get("beast_type", "")

    # All moves carry the beast's type — type advantage is a beast-level property.
    # True when this beast's element deals 2× damage to the opponent's element.
    has_type_adv = get_type_multiplier(ai_type, opp_type) >= 2.0

    # When type advantage exists, prefer the first move in the list (index 0).
    # When it doesn't, pick from the middle of the moveset to avoid a predictable
    # "always spams move 1" pattern that experienced players exploit.
    preferred_move = moves[0] if has_type_adv else moves[len(moves) // 2]

    if personality == "chaotic":
        # Truly random — Twine doesn't plan
        if can_ult and random.random() < 0.25:
            return ultimate, True
        return random.choice(moves), False

    elif personality == "defensive":
        # Maren: holds ultimate for genuine emergencies, consistent and predictable
        if can_ult and hp_ratio < 0.30:
            return ultimate, True
        return preferred_move, False

    elif personality == "aggressive":
        # Sable: always ultimate when available, highest pressure
        if can_ult:
            return ultimate, True
        return preferred_move, False

    elif personality == "patient":
        # Orren: ultimate only when it's likely decisive (type-advantage + medium HP)
        if can_ult and hp_ratio < 0.60 and has_type_adv:
            return ultimate, True
        return preferred_move, False

    elif personality == "optimal":
        # The Archivist: ultimate whenever HP ratio suggests a KO is possible,
        # or when mana would overflow next turn (efficient use of resources)
        if can_ult and (hp_ratio < 0.80 or ai_state["mana"] >= 90):
            return ultimate, True
        return preferred_move, False

    else:  # wild
        # 60% type-advantaged if available, 30% random, 10% ultimate
        if can_ult and random.random() < 0.10:
            return ultimate, True
        if has_type_adv and random.random() < 0.60:
            return preferred_move, False
        return random.choice(moves), False


def build_pve_beast_state(beast_data: dict, level: int) -> dict:
    """
    Build a battle state dict for a PvE beast (wild or NPC companion) at
    a given level, applying stat growth the same way apply_beast_levelup does.
    No DB row exists for these beasts — they live only in memory for the fight.
    """
    from utils.db import calc_stat_growth

    base  = beast_data["base_stats"]
    # Treat as a wild-caught beast for growth purposes (caught_from='wild')
    dummy_row = {"rarity": beast_data["rarity"], "caught_from": "wild"}
    growth = calc_stat_growth(dummy_row, max(0, level - 1))

    hp     = base["hp"]     + growth["hp"]
    atk    = base["attack"] + growth["attack"]
    defe   = base["defense"]+ growth["defense"]
    spd    = base["speed"]  + growth["speed"]
    mana   = base["mana"]   + growth["mana"]

    return {
        "id":          None,              # no DB row
        "name":        beast_data["name"],
        "hp":          hp,
        "max_hp":      hp,
        "attack":      atk,
        "defense":     defe,
        "speed":       spd,
        "mana":        mana,
        "max_mana":    mana,
        "status":      None,
        "status_turns":0,
        "phoenix_used":False,
        "moves":       beast_data["moves"],
        "ultimate":    beast_data["ultimate"],
        "beast_type":  beast_data.get("type", ""),
        # Divine passive (if applicable)
        **({} if not beast_data.get("divine_passive") else
           {"divine_passive": beast_data["divine_passive"],
            "divine_passive_id": beast_data["divine_passive"].get("passive_id"),
            "dp_shield": int(hp * beast_data["divine_passive"].get("passive_effect",{}).get("shield_percent",0)/100),
            "dp_used": False, "dp_crit_charges": 0, "dp_stacks": 0, "dp_bifrost_triggered": False}),
    }


async def award_player_exp(user_id: int, exp_gain: int) -> tuple[int, int, bool]:
    """
    Add `exp_gain` to the player's EXP, handle level-ups, and persist.
    Returns (new_level, new_exp, leveled_up).
    Used by /challenge, /sparr, and /explore so the logic lives in one place.
    """
    from utils.db import get_player, update_player, calc_player_exp_for_level
    player = await get_player(user_id)
    if not player:
        return 1, 0, False
    new_exp   = player["exp"] + exp_gain
    new_level = player["level"]
    while new_exp >= calc_player_exp_for_level(new_level):
        new_exp -= calc_player_exp_for_level(new_level)
        new_level += 1
    leveled_up = new_level > player["level"]
    await update_player(user_id, exp=new_exp, level=new_level)
    # Fire level_up quest event so chapter steps that check player level
    # (e.g. chapter 2's "reach level 25") advance automatically on level-up.
    if leveled_up:
        try:
            from cogs.questline import advance_quest_step
            await advance_quest_step(user_id, "level_up", level=new_level)
        except Exception:
            pass  # questline advance is best-effort — never block a reward
    return new_level, new_exp, leveled_up


async def run_pve_battle(
    interaction: discord.Interaction,
    player_beast_row: dict,
    player_beast_data: dict,
    player_perks: list,
    enemy_state: dict,
    enemy_personality: str,
    battle_title: str,
    on_win,   # async callback(player_state, enemy_state, timed_out) → embed additions
    on_loss,  # async callback(player_state, enemy_state) → embed additions
) -> None:
    """
    Shared PvE battle engine used by both /challenge (wild) and /sparr (NPC).
    Runs the full turn loop with the player choosing via MoveView and the
    enemy AI selecting via ai_pick_move(). Calls on_win or on_loss at the
    end with the final states so the caller can apply rewards/dialogue.
    """
    player_state = {
        "id":          player_beast_row["id"],
        "name":        player_beast_row.get("nickname") or player_beast_data["name"],
        "hp":          player_beast_row["hp"],
        "max_hp":      player_beast_row["max_hp"],
        "attack":      player_beast_row["attack"],
        "defense":     player_beast_row["defense"],
        "speed":       player_beast_row["speed"],
        "mana":        player_beast_row["mana"],
        "max_mana":    player_beast_row["max_mana"],
        "status":      None,
        "status_turns":0,
        "phoenix_used":False,
        "moves":       player_beast_data["moves"],
        "ultimate":    player_beast_data["ultimate"],
        "beast_type":  player_beast_data.get("type", ""),
        **({} if not player_beast_data.get("divine_passive") else
           {"divine_passive": player_beast_data["divine_passive"],
            "divine_passive_id": player_beast_data["divine_passive"].get("passive_id"),
            "dp_shield": int(player_beast_row["max_hp"] * player_beast_data["divine_passive"].get("passive_effect",{}).get("shield_percent",0)/100),
            "dp_used": False, "dp_crit_charges": 0, "dp_stacks": 0, "dp_bifrost_triggered": False}),
    }

    # ── Happiness modifier ────────────────────────────────────────────────
    # Ranges from -10% (≤30 happiness) to +10% (100 happiness), linear
    # between 30 and 100.  Makes the Fairy Garden sanctuary feel meaningful
    # and gives players a reason to care about the number beyond cosmetics.
    _hap = player_beast_row.get("happiness", 100)
    if _hap >= 100:
        _hap_mult = 1.10
    elif _hap <= 30:
        _hap_mult = 0.90
    else:
        _hap_mult = 0.90 + (_hap - 30) / 70 * 0.20
    player_state["attack"]  = max(1, int(player_state["attack"]  * _hap_mult))
    player_state["defense"] = max(1, int(player_state["defense"] * _hap_mult))
    player_state["speed"]   = max(1, int(player_state["speed"]   * _hap_mult))

    # Apply equipment bonuses to player (reuse existing helper via inline call)
    async def _apply_equip(state):
        # Use module-level _ALL_GEAR cache — no per-battle disk I/O
        async with aiosqlite.connect("db/chibibeast.db") as _db:
            _db.row_factory = aiosqlite.Row
            async with _db.execute(
                "SELECT equipment_id FROM player_equipment WHERE user_id = ? AND beast_row_id = ?",
                (interaction.user.id, player_beast_row["id"])
            ) as _c:
                _armors = [dict(r) for r in await _c.fetchall()]
            async with _db.execute(
                "SELECT rune_id FROM player_beasts WHERE id = ?", (player_beast_row["id"],)
            ) as _c:
                _b = await _c.fetchone()
            _rune_id = _b["rune_id"] if _b else None
        for _ar in _armors:
            _g = _ALL_GEAR.get(_ar["equipment_id"], {})
            _eff = _g.get("effect", {})
            _gname = _g.get("name", _ar["equipment_id"])
            if "defense_percent" in _eff:
                state["defense"] += int(state["defense"] * _eff["defense_percent"] / 100)
            if "hp_percent" in _eff:
                state["max_hp"] += int(state["max_hp"] * _eff["hp_percent"] / 100)
                state["hp"]     += int(state["hp"]     * _eff["hp_percent"] / 100)
            if "damage_reduction_flat_percent" in _eff:
                state["_armor_reduction"] = _eff["damage_reduction_flat_percent"]
                state["_armor_name"]      = _gname
            if "hp_regen_percent_per_round" in _eff:
                state["_hp_regen_percent"] = _eff["hp_regen_percent_per_round"]
                state["_hp_regen_name"]    = _gname
            if "crit_immunity" in _eff:
                state["_crit_immune"] = True
            if "evasion_percent" in _eff:
                state["_evasion"]      = _eff["evasion_percent"]
                state["_evasion_name"] = _gname
            if "burn_immunity" in _eff:
                state["_burn_immune"] = True
            if "water_resist_percent" in _eff:
                state["_water_resist"] = _eff["water_resist_percent"]
                state["_water_resist_name"] = _gname
            if "attack_percent" in _eff:
                state["attack"] += int(state["attack"] * _eff["attack_percent"] / 100)
            if "water_attack_percent" in _eff and state.get("beast_type") == "water":
                state["attack"] += int(state["attack"] * _eff["water_attack_percent"] / 100)
            if "reflect_damage_percent" in _eff:
                state["_reflect_pct"] = _eff["reflect_damage_percent"]
                state["_reflect_name"] = _gname
            if "thorns_poison_on_hit_chance" in _eff:
                state["_thorns_poison_chance"] = _eff["thorns_poison_on_hit_chance"]
                state["_thorns_name"] = _gname
            if "burn_on_hit_chance" in _eff:
                state["_armor_burn_chance"] = _eff["burn_on_hit_chance"]
                state["_armor_burn_name"]   = _gname
            if "stun_on_hit_chance" in _eff:
                state["_armor_stun_chance"] = _eff["stun_on_hit_chance"]
                state["_armor_stun_name"]   = _gname
            if "blight_on_hit_chance" in _eff:
                state["_blight_chance"] = _eff["blight_on_hit_chance"]
                state["_blight_name"]   = _gname
            if "time_rewind_on_fatal_once" in _eff:
                state["_time_rewind"] = True
                state["_time_rewind_name"] = _gname
            if "speed_percent" in _eff:
                if _eff["speed_percent"] > 0:
                    state["speed"] += int(state["speed"] * _eff["speed_percent"] / 100)
                else:
                    state["speed"] = max(1, state["speed"] - int(state["speed"] * abs(_eff["speed_percent"]) / 100))
        if _rune_id:
            _r   = _ALL_GEAR.get(_rune_id, {})
            _eff = _r.get("effect", {})
            _rname = _r.get("name", _rune_id)
            if "speed" in _eff:     state["speed"]   += _eff["speed"]
            if "attack" in _eff:    state["attack"]  += _eff["attack"]
            if "hp_flat" in _eff:
                state["hp"]      += _eff["hp_flat"]
                state["max_hp"]  += _eff["hp_flat"]
            if "defense" in _eff:  state["defense"] += _eff["defense"]
            if "lifesteal_percent" in _eff:
                state["_rune_lifesteal"] = _eff["lifesteal_percent"]
                state["_rune_lifesteal_name"] = _rname
            if "death_explosion_fire" in _eff:
                state["_death_explosion"] = True
                state["_death_explosion_name"] = _rname
            if "mana_regen_on_hit" in _eff:
                state["_mana_regen_on_hit"] = _eff["mana_regen_on_hit"]
                state["_mana_regen_name"] = _rname
            if "burn_on_hit_chance" in _eff:
                state["_rune_burn_chance"] = _eff["burn_on_hit_chance"]
                state["_rune_burn_name"]   = _rname
            if "attack_percent" in _eff:
                state["attack"] += int(state["attack"] * _eff["attack_percent"] / 100)
            if "speed_percent" in _eff:
                state["speed"]  += int(state["speed"]  * _eff["speed_percent"]  / 100)
            if "defense_percent" in _eff:
                state["defense"]+= int(state["defense"]* _eff["defense_percent"] / 100)
            if "hp_percent" in _eff:
                bonus = int(state["max_hp"] * _eff["hp_percent"] / 100)
                state["max_hp"] += bonus
                state["hp"]     += bonus
            if "crit_chance" in _eff:
                state["_rune_crit_bonus"] = _eff["crit_chance"]

    await _apply_equip(player_state)

    # Apply battle-start passives
    battle_log = []
    apply_battle_start_passives(player_state, enemy_state, battle_log)
    apply_battle_start_passives(enemy_state, player_state, battle_log)

    # Genesis Spark: bonus mana at battle start
    if any(p.get("perk_id") == "genesis_spark" and p.get("equipped") for p in player_perks):
        player_state["mana"] = min(player_state.get("max_mana", 100), player_state.get("mana", 0) + 20)
        battle_log.append("✨ **Genesis Spark** — started with 20 bonus mana!")

    player_goes_first = player_state["speed"] >= enemy_state["speed"]
    turn = 1

    while player_state["hp"] > 0 and enemy_state["hp"] > 0 and turn <= 20:
        is_player_turn = (turn % 2 == 1) == player_goes_first
        attacker_state = player_state if is_player_turn else enemy_state
        defender_state = enemy_state  if is_player_turn else player_state

        # Status skip (freeze/sleep)
        if attacker_state["status"] in ["freeze", "sleep"]:
            emoji = STATUS_EFFECTS[attacker_state["status"]]["emoji"]
            battle_log.append(
                f"{emoji} **{attacker_state['name']}** is "
                f"{'frozen' if attacker_state['status'] == 'freeze' else 'asleep'} and can't move!"
            )
            attacker_state["status_turns"] -= 1
            if attacker_state["status_turns"] <= 0:
                attacker_state["status"] = None
                attacker_state["status_turns"] = 0
            turn += 1
            continue

        # Turn-start passives
        double_turn = apply_turn_start_passives(attacker_state, battle_log, defender_state)

        # HP regen — equipment rune regen
        if attacker_state.get("_hp_regen_percent") and attacker_state["hp"] > 0:
            regen = max(1, int(attacker_state["max_hp"] * attacker_state["_hp_regen_percent"] / 100))
            attacker_state["hp"] = min(attacker_state["max_hp"], attacker_state["hp"] + regen)
            battle_log.append(f"🩹 **{attacker_state['name']}** regenerated {regen} HP!")

        # Endless Regen (Radiant Hydra) — divine passive regen each turn
        _dp = attacker_state.get("divine_passive", {})
        if _dp.get("passive_id") == "endless_regen" and attacker_state["hp"] > 0:
            _regen_pct = _dp.get("passive_effect", {}).get("regen_percent", 6)
            _regen = max(1, int(attacker_state["max_hp"] * _regen_pct / 100))
            attacker_state["hp"] = min(attacker_state["max_hp"], attacker_state["hp"] + _regen)
            battle_log.append(f"🐍 **{attacker_state['name']}'s Endless Regen** — +{_regen} HP!")

        # DoT
        for status, key, emoji in [("poison","max_hp","☠️"),("burn","max_hp","🔥"),("blight","max_hp","💜")]:
            if attacker_state["status"] == status:
                factor = {"poison":0.05,"burn":0.08,"blight":0.06}[status]
                dot = max(1, int(attacker_state[key] * factor))
                attacker_state["hp"] = max(0, attacker_state["hp"] - dot)
                battle_log.append(f"{emoji} **{attacker_state['name']}** took `{dot}` {status} damage!")

        if attacker_state["hp"] <= 0:
            break

        # Move selection
        if is_player_turn:
            # Show the battle state and present move buttons to the player
            state_embed = discord.Embed(
                title=f"⚔️ {battle_title} — Turn {turn}",
                description=(
                    f"**Your {player_state['name']}**\n"
                    f"{hp_bar(player_state['hp'], player_state['max_hp'])}\n\n"
                    f"**{enemy_state['name']}**\n"
                    f"{hp_bar(enemy_state['hp'], enemy_state['max_hp'])}"
                ),
                color=COLORS["info"]
            )
            if battle_log:
                state_embed.add_field(name="📜 Last Turn", value="\n".join(battle_log[-3:]), inline=False)
            state_embed.set_footer(text="Your turn — Choose a move!")

            move_view = MoveView(
                player_state["moves"], player_state["ultimate"],
                interaction.user.id, player_state
            )
            await interaction.channel.send(embed=state_embed, view=move_view)
            await move_view.wait()

            if move_view.chosen_move is None:
                move_view.chosen_move = (random.choice(player_state["moves"]), False)

            move_name, is_ultimate = move_view.chosen_move
            attacker_perks = player_perks
            defender_perks = []
        else:
            # AI picks a move — no interaction needed
            move_name, is_ultimate = ai_pick_move(enemy_state, player_state, enemy_personality)
            attacker_perks = []
            defender_perks = player_perks

        # Damage resolution (reuse existing engine)
        damage, is_crit, type_mult, crit_charge_delta = calc_damage(
            attacker_state, defender_state, move_name, is_ultimate, attacker_perks
        )

        # ── Mana update happens before blind-miss short-circuit ─────────────
        # The attempt was made regardless of whether the attack connected.
        # A blind ultimate that misses still drains mana (the Loom doesn't
        # refund effort). A blind basic attack still earns its passive regen.
        if is_ultimate:
            attacker_state["mana"] = max(0, attacker_state["mana"] - 50)
        else:
            # Mana gain scales with speed — faster beasts charge ultimates quicker
            mana_gain = min(15, 8 + attacker_state.get("speed", 50) // 40)
            attacker_state["mana"] = min(attacker_state["max_mana"], attacker_state["mana"] + mana_gain)

        if damage == 0 and attacker_state.get("status") == "blind":
            battle_log.append(f"👁️ **{attacker_state['name']}** missed! (Blinded)")
            turn += 1
            continue

        damage = apply_on_hit_passives(attacker_state, defender_state, damage, is_ultimate, battle_log)

        if crit_charge_delta != 0:
            if crit_charge_delta > 0:
                attacker_state["dp_crit_charges"] = attacker_state.get("dp_crit_charges", 0) + crit_charge_delta
            else:
                attacker_state["dp_crit_charges"] = 0

        # Lifesteal now handled in on-hit block above

        damage = apply_on_hit_taken_passives(attacker_state, defender_state, damage, battle_log)

        # Armor reduction and evasion now handled above in the on-hit block
        if attacker_state.get("_phoenix_shroud") and not attacker_state.get("_phoenix_shroud_triggered"):
            if attacker_state["hp"] <= attacker_state["max_hp"] * 0.25:
                attacker_state["attack"] = int(attacker_state["attack"] * 2)
                attacker_state["_phoenix_shroud_triggered"] = True
                battle_log.append(f"🔥 **Phoenix-Born Shroud** — {attacker_state['name']}'s ATK doubled!")

        # KO handling
        if defender_state["hp"] - damage <= 0:
            if apply_ko_passives(defender_state, attacker_state, battle_log):
                damage = 0
            elif defender_state.get("_time_rewind") and not defender_state.get("_time_rewind_used"):
                # Chronos Paradox Plate — rewind to 30% HP once
                revive_hp = int(defender_state["max_hp"] * 0.30)
                defender_state["hp"]             = revive_hp
                defender_state["_time_rewind_used"] = True
                damage = 0
                battle_log.append(f"⏳ **{defender_state['name']}'s {defender_state.get('_time_rewind_name','Chronos Plate')}** rewound time — revived at `{revive_hp}HP`!")
            else:
                if defender_state.get("_death_explosion"):
                    explosion = int(defender_state["max_hp"] * 0.30)
                    attacker_state["hp"] = max(0, attacker_state["hp"] - explosion)
                    battle_log.append(f"💥 **{defender_state.get('_death_explosion_name','Core of the Phoenix')}** explodes for `{explosion}` damage!")
                defender_state["hp"] = 0
        else:
            defender_state["hp"] = max(0, defender_state["hp"] - damage)

        # ── On-hit attacker gear effects ──────────────────────────────────────
        skip_attack = False  # True when attack is fully negated (e.g. stun, miss)
        if damage > 0 and not skip_attack:
            # Lifesteal rune — log it
            if attacker_state.get("_rune_lifesteal") and damage > 0:
                heal = int(damage * attacker_state["_rune_lifesteal"] / 100)
                attacker_state["hp"] = min(attacker_state["max_hp"], attacker_state["hp"] + heal)
                if heal > 0:
                    battle_log.append(f"🩸 **{attacker_state['name']}'s {attacker_state.get('_rune_lifesteal_name','Lifesteal Rune')}** — healed `{heal}HP`!")

            # Rune burn on hit
            if attacker_state.get("_rune_burn_chance") and not defender_state.get("status"):
                if random.random() < attacker_state["_rune_burn_chance"] / 100:
                    defender_state["status"] = "burn"
                    defender_state["status_turns"] = 3
                    battle_log.append(f"🔥 **{attacker_state['name']}'s {attacker_state.get('_rune_burn_name','Ember Spark')}** — {defender_state['name']} is Burned!")

            # Armor burn on hit (defender's thorns)
            if attacker_state.get("_armor_burn_chance") and not defender_state.get("status"):
                if random.random() < attacker_state["_armor_burn_chance"] / 100:
                    defender_state["status"] = "burn"
                    defender_state["status_turns"] = 3
                    battle_log.append(f"🔥 **{attacker_state['name']}'s {attacker_state.get('_armor_burn_name','armor')}** — {defender_state['name']} is Burned!")

            # Armor stun on hit
            if attacker_state.get("_armor_stun_chance") and not defender_state.get("status"):
                if random.random() < attacker_state["_armor_stun_chance"] / 100:
                    defender_state["status"] = "freeze"
                    defender_state["status_turns"] = 1
                    battle_log.append(f"⚡ **{attacker_state['name']}'s {attacker_state.get('_armor_stun_name','Storm-Mail')}** — {defender_state['name']} is Stunned!")

            # Blight on hit
            if attacker_state.get("_blight_chance") and not defender_state.get("status"):
                if random.random() < attacker_state["_blight_chance"] / 100:
                    defender_state["status"] = "blight"
                    defender_state["status_turns"] = 4
                    battle_log.append(f"💜 **{attacker_state['name']}'s {attacker_state.get('_blight_name','Regalia')}** — {defender_state['name']} is Blighted!")

            # Mana regen on hit (Thunderbird Feather rune)
            if attacker_state.get("_mana_regen_on_hit"):
                gain = attacker_state["_mana_regen_on_hit"]
                attacker_state["mana"] = min(attacker_state.get("max_mana", 100), attacker_state.get("mana", 0) + gain)

        # ── On-hit defender gear effects (thorns / reflect) ───────────────
        if damage > 0:
            # Armor reduction — log it
            if defender_state.get("_armor_reduction") and damage > 0:
                raw = damage
                damage = max(1, damage - int(damage * defender_state["_armor_reduction"] / 100))
                saved  = raw - damage
                if saved > 0:
                    battle_log.append(f"🛡️ **{defender_state.get('_armor_name','Aegis')}** absorbed `{saved}` damage!")

            # Reflect damage
            if defender_state.get("_reflect_pct") and damage > 0:
                reflect = max(1, int(damage * defender_state["_reflect_pct"] / 100))
                attacker_state["hp"] = max(0, attacker_state["hp"] - reflect)
                battle_log.append(f"🔄 **{defender_state['name']}'s {defender_state.get('_reflect_name','Emberstone Vest')}** reflected `{reflect}` damage!")

            # Thorns — poison chance on being hit
            if defender_state.get("_thorns_poison_chance") and not attacker_state.get("status"):
                if random.random() < defender_state["_thorns_poison_chance"] / 100:
                    attacker_state["status"] = "poison"
                    attacker_state["status_turns"] = 3
                    battle_log.append(f"🌿 **{defender_state['name']}'s {defender_state.get('_thorns_name','Thornweave Cloak')}** — {attacker_state['name']} is Poisoned by the thorns!")

            # Water resist
            if defender_state.get("_water_resist") and attacker_state.get("beast_type") == "water":
                raw   = damage
                damage = max(1, damage - int(damage * defender_state["_water_resist"] / 100))
                battle_log.append(f"🌊 **{defender_state.get('_water_resist_name','armor')}** resisted `{raw-damage}` water damage!")

        # ── Evasion — log it (already handles damage=0) ───────────────────
        if defender_state.get("_evasion") and damage > 0:
            if random.random() < defender_state["_evasion"] / 100:
                battle_log.append(f"💨 **{defender_state['name']}** ({defender_state.get('_evasion_name','Abyssal Shroud')}) evaded!")
                damage = 0

        # ── Burn immunity ─────────────────────────────────────────────────
        # Applied at status-grant time — see status block

        # Log
        effectiveness = type_effectiveness_label(type_mult)
        crit_tag = "⭐ CRIT! " if is_crit else ""
        type_tag = f" {effectiveness}" if effectiveness else ""
        battle_log.append(f"{crit_tag}**{attacker_state['name']}** used **{move_name}** → `{damage}` dmg!{type_tag}")

        # Status application — faster attacker lands status more reliably
        _atk_spd = attacker_state.get("speed", 50)
        _def_spd = defender_state.get("speed", 50)
        _status_chance = (_atk_spd / max(_atk_spd + _def_spd, 1)) * 0.25
        if random.random() < _status_chance and not defender_state["status"]:
            if not defender_state.get("divine_passive", {}).get("passive_effect", {}).get("status_immune"):
                status_pool = ["poison","burn","freeze","sleep","paralyze"]
                if defender_state.get("_burn_immune"):
                    status_pool = [s for s in status_pool if s != "burn"]
                new_status = random.choice(status_pool) if status_pool else None
                if defender_state.get("divine_passive", {}).get("passive_id") == "karmic_echo" and random.random() < 0.40:
                    attacker_state["status"] = new_status
                    attacker_state["status_turns"] = STATUS_EFFECTS[new_status].get("skip_turns", 0)
                    battle_log.append(f"🔄 **Karmic Echo** reflected {new_status}!")
                else:
                    defender_state["status"] = new_status
                    defender_state["status_turns"] = STATUS_EFFECTS[new_status].get("skip_turns", 0)
                    battle_log.append(f"{STATUS_EFFECTS[new_status]['emoji']} **{defender_state['name']}** is {new_status}!")

        # End-of-turn status cleanup
        for state in [player_state, enemy_state]:
            if state["status"] in ["blind", "paralyze"] and state.get("status_turns", 0) > 0:
                state["status_turns"] -= 1
                if state["status_turns"] <= 0:
                    prev = state["status"]
                    state["status"] = None
                    state["status_turns"] = 0
                    battle_log.append(f"✨ **{state['name']}** recovered from {prev}!")

        # Chronos double turn
        if double_turn and attacker_state["hp"] > 0 and defender_state["hp"] > 0:
            bonus_move, _ = ai_pick_move(attacker_state, defender_state, "wild") if not is_player_turn else (random.choice(attacker_state["moves"]), False)
            bonus_dmg, _, _, _ = calc_damage(attacker_state, defender_state, bonus_move, False, attacker_perks)
            bonus_dmg = apply_on_hit_taken_passives(attacker_state, defender_state, bonus_dmg, battle_log)
            defender_state["hp"] = max(0, defender_state["hp"] - bonus_dmg)
            battle_log.append(f"⏳ **{attacker_state['name']}** acts again → `{bonus_dmg}` dmg!")

        turn += 1

    # Determine result — distinguish timeout from genuine KO
    timed_out   = turn > 20 and player_state["hp"] > 0 and enemy_state["hp"] > 0
    player_won  = not timed_out and player_state["hp"] > 0 and enemy_state["hp"] <= 0

    if timed_out:
        # Both beasts survived 20 turns — judge by remaining HP percentage
        player_hp_pct = player_state["hp"] / max(player_state["max_hp"], 1)
        enemy_hp_pct  = enemy_state["hp"]  / max(enemy_state["max_hp"],  1)
        player_won    = player_hp_pct > enemy_hp_pct
        title_str     = f"⏱️ {battle_title} — Time Limit! ({'Your beast wins on HP!' if player_won else 'Opponent wins on HP!'})"
        timeout_note  = (
            f"\n\n*The battle reached its 20-turn limit. "
            f"Winner decided by remaining HP: "
            f"you at `{player_hp_pct*100:.0f}%` vs opponent at `{enemy_hp_pct*100:.0f}%`.*"
        )
    else:
        title_str    = f"⚔️ {battle_title} — {'Victory!' if player_won else 'Defeated!'}"
        timeout_note = ""

    # Build result embed
    result_embed = discord.Embed(
        title=title_str,
        description=(
            f"**Your {player_state['name']}** — {hp_bar(max(0,player_state['hp']), player_state['max_hp'])}\n"
            f"**{enemy_state['name']}** — {hp_bar(max(0,enemy_state['hp']), enemy_state['max_hp'])}"
            + timeout_note
        ),
        color=COLORS["success"] if player_won else (COLORS["info"] if timed_out else COLORS["error"])
    )
    result_embed.add_field(
        name="📜 Battle Log",
        value="\n".join(battle_log[-6:]) or "No moves logged.",
        inline=False
    )

    if player_won:
        await on_win(result_embed, player_state, enemy_state, timed_out)
    else:
        await on_loss(result_embed, player_state, enemy_state)

    await interaction.channel.send(embed=result_embed)

    # ── Log battle for /stats tracking ────────────────────────────────────
    async with aiosqlite.connect("db/chibibeast.db") as _blog:
        await _blog.execute(
            """INSERT INTO battles
               (challenger_id, winner_id, challenger_beast, status, battle_type)
               VALUES (?, ?, ?, 'completed', ?)""",
            (interaction.user.id,
             interaction.user.id if player_won else None,
             player_beast_row["id"],
             "sparr" if battle_title.startswith("Spar") else "pve")
        )
        await _blog.commit()


# ── Divine Passive Processor ──────────────────────────────────────────────────
def init_divine_state(beast_data: dict, beast_row: dict) -> dict:
    """Return divine-passive-specific state fields to merge into battle state."""
    passive = beast_data.get("divine_passive", {})
    if not passive:
        return {}
    effect = passive.get("passive_effect", {})
    extra = {
        "divine_passive": passive,
        "divine_passive_id": passive.get("passive_id"),
        # Trackers
        "dp_shield": int(beast_row["max_hp"] * effect.get("shield_percent", 0) / 100),
        "dp_used": False,           # once_per_battle flag
        "dp_crit_charges": 0,       # Supernova's Critical Mass
        "dp_stacks": 0,             # Zodiac's Constellation Charge
        "dp_bifrost_triggered": False,
    }
    return extra


def apply_battle_start_passives(attacker_state: dict, defender_state: dict, battle_log: list):
    """Apply passives that trigger at battle start (gravity_well, stellar_nursery)."""
    for state in [attacker_state, defender_state]:
        passive = state.get("divine_passive", {})
        if not passive:
            continue
        trigger = passive.get("passive_trigger")
        effect  = passive.get("passive_effect", {})
        pid     = passive.get("passive_id")

        if trigger == "battle_start":
            if pid == "gravity_well":
                # Reduce opponent speed by 15%
                opp = defender_state if state is attacker_state else attacker_state
                reduction = int(opp["speed"] * 0.15)
                opp["speed"] = max(1, opp["speed"] - reduction)
                battle_log.append(f"🌀 **{state['name']}'s Gravity Well** drags — {opp['name']} loses {reduction} Speed!")
            elif pid == "stellar_nursery" and state.get("dp_shield", 0) > 0:
                battle_log.append(f"🌌 **{state['name']}'s Stellar Nursery** — shield absorbs {state['dp_shield']} damage!")


def apply_turn_start_passives(active_state: dict, battle_log: list, opponent_state: dict = None) -> bool:
    """Apply turn-start passives. Returns True if the beast should act twice (Chronos)."""
    passive = active_state.get("divine_passive", {})
    if not passive:
        return False
    trigger = passive.get("passive_trigger")
    effect  = passive.get("passive_effect", {})
    pid     = passive.get("passive_id")
    double_turn = False

    if trigger == "turn_start":
        if pid == "star_weave":
            regen = int(active_state["max_mana"] * effect.get("self_mana_restore_percent", 8) / 100)
            active_state["mana"] = min(active_state["max_mana"], active_state["mana"] + regen)
            battle_log.append(f"⭐ **{active_state['name']}'s Star Weave** — +{regen} mana!")

        elif pid == "borrowed_time":
            if random.random() < effect.get("double_turn_chance", 0.20):
                battle_log.append(f"⏳ **{active_state['name']}'s Borrowed Time** — acts twice this turn!")
                double_turn = True

        elif pid == "constellation_charge":
            active_state["dp_stacks"] = active_state.get("dp_stacks", 0) + 1
            stacks = active_state["dp_stacks"]
            atk_gain = int(active_state["attack"] * effect.get("attack_stack_percent", 5) / 100)
            spd_gain = int(active_state["speed"] * effect.get("speed_stack_percent", 5) / 100)
            active_state["attack"] += atk_gain
            active_state["speed"]  += spd_gain
            if stacks <= 4:  # Only log first few to avoid spam
                battle_log.append(f"✨ **{active_state['name']}'s Constellation Charge** (×{stacks}) — ATK+{atk_gain} SPD+{spd_gain}!")

    # Boundary Break (Ascended Pegasus) — speed stacks each turn
    if pid == "boundary_break":
        spd_gain = int(active_state["speed"] * effect.get("speed_stack_percent", 8) / 100)
        active_state["speed"] += spd_gain
        active_state.setdefault("dp_stacks", 0)
        active_state["dp_stacks"] += 1
        if active_state["dp_stacks"] <= 4:
            battle_log.append(f"🌪️ **{active_state['name']}'s Boundary Break** — SPD+{spd_gain}! (×{active_state['dp_stacks']})")

    # Fox Sovereign (Radiant Kitsune) — every 3rd turn all nine tails strike
    if pid == "fox_sovereign" and opponent_state is not None:
        active_state.setdefault("dp_stacks", 0)
        active_state["dp_stacks"] += 1
        interval = effect.get("nine_tail_strike_interval", 3)
        if active_state["dp_stacks"] % interval == 0:
            mult = effect.get("nine_tail_multiplier", 9)
            base_tail  = max(1, int(active_state["attack"] / 9))
            def_factor = opponent_state.get("defense", 0) / (opponent_state.get("defense", 0) + 100)
            tail_dmg   = max(1, int(base_tail * (1 - def_factor) * mult))
            opponent_state["hp"] = max(0, opponent_state["hp"] - tail_dmg)
            battle_log.append(
                f"🦊 **{active_state['name']}'s Fox Sovereign** — all nine tails strike! `{tail_dmg}` bonus damage!"
            )

    return double_turn


def apply_on_hit_passives(attacker_state: dict, defender_state: dict, damage: int, is_ultimate: bool, battle_log: list) -> int:
    """Apply on-hit attacker passives. Returns modified damage."""
    passive = attacker_state.get("divine_passive", {})
    if not passive:
        return damage
    trigger = passive.get("passive_trigger")
    effect  = passive.get("passive_effect", {})
    pid     = passive.get("passive_id")

    if trigger == "on_hit":
        if pid == "void_hunger":
            heal = int(damage * effect.get("lifesteal_percent", 12) / 100)
            attacker_state["hp"] = min(attacker_state["max_hp"], attacker_state["hp"] + heal)
            battle_log.append(f"🌑 **{attacker_state['name']}'s Void Hunger** — healed {heal} HP!")

    if trigger == "on_attack":
        if pid == "dark_matter" and random.random() < effect.get("blind_chance", 0.25):
            defender_state["status"] = "blind"
            defender_state["status_turns"] = effect.get("blind_turns", 2)
            battle_log.append(f"👁️ **{attacker_state['name']}'s Dark Matter** — {defender_state['name']} is Blinded!")

        elif pid == "sacred_mending":
            attacker_state.setdefault("dp_stacks", 0)
            attacker_state["dp_stacks"] += 1
            if attacker_state["dp_stacks"] % 3 == 0:
                # Every 3rd attack: heal self instead of damage
                self_heal = int(attacker_state["max_hp"] * effect.get("third_hit_heal_percent", 30) / 100)
                attacker_state["hp"] = min(attacker_state["max_hp"], attacker_state["hp"] + self_heal)
                battle_log.append(f"✨ **{attacker_state['name']}'s Sacred Mending** — restored {self_heal} HP on 3rd strike!")
            else:
                heal = int(damage * effect.get("lifesteal_percent", 10) / 100)
                attacker_state["hp"] = min(attacker_state["max_hp"], attacker_state["hp"] + heal)
                if heal > 0:
                    battle_log.append(f"✨ **{attacker_state['name']}'s Sacred Mending** — healed {heal} HP!")

        elif pid == "boundary_break":
            pass  # handled in turn_start via apply_turn_start_passives

        elif pid == "critical_mass":
            # Tracked in calc_damage via dp_crit_charges
            pass

    if trigger == "on_ultimate" and is_ultimate and pid == "final_word":
        # Terminus ultimate always applies a random status
        from random import choice
        new_status = choice(["poison", "burn", "paralyze"])
        defender_state["status"] = new_status
        defender_state["status_turns"] = STATUS_EFFECTS[new_status].get("skip_turns", 0)
        battle_log.append(f"☠️ **{attacker_state['name']}'s Final Word** — {defender_state['name']} is {new_status}!")

    return damage


def apply_on_hit_taken_passives(attacker_state: dict, defender_state: dict, damage: int, battle_log: list) -> int:
    """Apply passives on the defender when hit. Returns modified damage (after shield etc)."""
    passive = defender_state.get("divine_passive", {})
    if not passive:
        return damage
    trigger = passive.get("passive_trigger")
    effect  = passive.get("passive_effect", {})
    pid     = passive.get("passive_id")

    # Shield (Nebula - Stellar Nursery)
    if defender_state.get("dp_shield", 0) > 0:
        absorbed = min(damage, defender_state["dp_shield"])
        defender_state["dp_shield"] -= absorbed
        damage -= absorbed
        if absorbed > 0:
            battle_log.append(f"🛡️ **{defender_state['name']}'s Stellar Nursery** absorbed {absorbed} damage! (Shield: {defender_state['dp_shield']} remaining)")

    # Flat damage reduction (Atlas - Weight of Worlds)
    if pid == "weight_of_worlds" and trigger == "passive":
        reduction = effect.get("damage_reduction_flat", 25)
        reduced = max(0, int(damage * reduction / 100))
        damage = max(1, damage - reduced)
        if reduced > 0:
            battle_log.append(f"🌍 **{defender_state['name']}'s Weight of Worlds** reduced damage by {reduced}!")

    # Mirror Reality (Paradox) — reflect
    if trigger == "on_hit_taken" and pid == "mirror_reality":
        if random.random() < effect.get("reflect_chance", 0.15):
            reflect_dmg = int(damage * effect.get("reflect_percent", 50) / 100)
            attacker_state["hp"] = max(0, attacker_state["hp"] - reflect_dmg)
            battle_log.append(f"🪞 **{defender_state['name']}'s Mirror Reality** — reflected {reflect_dmg} damage!")

    # Bifrost Surge (Asgard) — threshold trigger
    if trigger == "on_damage_taken" and pid == "bifrost_surge" and not defender_state.get("dp_bifrost_triggered"):
        hp_after = defender_state["hp"] - damage
        threshold = defender_state["max_hp"] * effect.get("threshold_percent", 50) / 100
        if hp_after <= threshold:
            defender_state["dp_bifrost_triggered"] = True
            atk_boost = int(defender_state["attack"] * effect.get("attack_bonus", 30) / 100)
            spd_boost = int(defender_state["speed"]  * effect.get("speed_bonus",  20) / 100)
            defender_state["attack"] += atk_boost
            defender_state["speed"]  += spd_boost
            battle_log.append(f"⚡ **{defender_state['name']}'s Bifrost Surge** — ATK+{atk_boost} SPD+{spd_boost}!")

    # Karmic Echo — status reflect
    if trigger == "on_status_received":
        pass  # handled at status application time

    # Forge Fury (Radiant Goblin) — each hit taken stacks attack
    if trigger == "on_hit_taken" and pid == "forge_fury" and damage > 0:
        stack = int(defender_state["attack"] * effect.get("attack_stack_percent", 4) / 100)
        defender_state["attack"] += stack
        defender_state.setdefault("dp_stacks", 0)
        defender_state["dp_stacks"] += 1
        if defender_state["dp_stacks"] <= 5:
            battle_log.append(f"🔥 **{defender_state['name']}'s Forge Fury** — ATK+{stack}! (×{defender_state['dp_stacks']})")

    # Void Absorption (Ascended Slime) — converts damage to attack stacks
    if trigger == "on_hit_taken" and pid == "void_absorption" and damage > 0:
        absorbed_atk = int(damage * effect.get("absorb_to_attack_percent", 20) / 100)
        defender_state["attack"] = defender_state.get("attack", 0) + absorbed_atk
        defender_state.setdefault("dp_stacks", 0)
        defender_state["dp_stacks"] += absorbed_atk
        if absorbed_atk > 0:
            battle_log.append(f"🌊 **{defender_state['name']}'s Void Absorption** — ATK+{absorbed_atk} absorbed!")

    return damage


def apply_ko_passives(ko_state: dict, opponent_state: dict, battle_log: list) -> bool:
    """Apply on-KO passives. Returns True if the KO was prevented (Genesis - First Flame)."""
    passive = ko_state.get("divine_passive", {})
    if not passive:
        return False
    effect = passive.get("passive_effect", {})
    pid    = passive.get("passive_id")

    if pid == "first_flame" and not ko_state.get("dp_used"):
        heal = int(ko_state["max_hp"] * effect.get("revive_heal_percent", 30) / 100)
        ko_state["hp"] = heal
        ko_state["status"] = None
        ko_state["dp_used"] = True
        battle_log.append(f"🔥 **{ko_state['name']}'s First Flame** — rose from the ashes with {heal} HP!")
        return True

    if pid == "deathless_flame" and not ko_state.get("dp_used"):
        heal_pct = effect.get("revive_hp_percent", 40)
        heal = int(ko_state["max_hp"] * heal_pct / 100)
        atk_boost = int(ko_state["attack"] * effect.get("revive_attack_boost", 50) / 100)
        ko_state["hp"] = heal
        ko_state["mana"] = ko_state.get("max_mana", 100)
        ko_state["attack"] += atk_boost
        ko_state["status"] = None
        ko_state["dp_used"] = True
        battle_log.append(
            f"🔥 **{ko_state['name']}'s Deathless Flame** — *it refused.* "
            f"Revived at {heal} HP, full mana, ATK+{atk_boost}!"
        )
        return True

    return False


def calc_damage(attacker: dict, defender: dict, move: str, is_ultimate: bool = False, perks: list = None) -> tuple:
    """
    Pure damage calculation — reads state, never writes it.
    Returns (damage, is_crit, type_mult, crit_charge_delta) where
    crit_charge_delta is +1 if Supernova missed a crit (caller applies to state),
    -charges_needed if the charged crit fired (caller resets to 0).
    """
    base = attacker["attack"]
    defense_factor = defender["defense"] / (defender["defense"] + 100)
    damage = base * (1 - defense_factor)

    # Type advantage — Horizon's Rift Step passive: ignore type disadvantage
    passive_id = attacker.get("divine_passive", {}).get("passive_id", "")
    raw_mult = get_type_multiplier(attacker.get("beast_type", ""), defender.get("beast_type", ""))
    # Boundary Break (Ascended Pegasus): above 200% base speed, ignore type resistance
    if passive_id == "boundary_break":
        base_speed = attacker.get("base_speed", attacker.get("speed", 1))
        if attacker.get("speed", 0) >= base_speed * 2:
            raw_mult = max(1.0, raw_mult)  # no resistance, floor at neutral
    type_mult = max(1.0, raw_mult) if passive_id == "rift_step" else raw_mult
    damage *= type_mult

    # Ultimate multiplier — Terminus overrides to 1.4×
    if is_ultimate:
        ult_mult = attacker.get("divine_passive", {}).get(
            "passive_effect", {}
        ).get("ultimate_damage_mod", 1.8)
        damage *= ult_mult
        if perks:
            for perk in perks:
                if perk["perk_id"] == "genesis_spark" and perk["equipped"]:
                    damage *= 1.10  # 10% ultimate boost

    # Crit chance
    crit_chance = 0.10
    if perks:
        for perk in perks:
            if perk["perk_id"] == "spellbound_focus" and perk["equipped"]:
                crit_chance += 0.10
    # Rune crit bonus (applied by caller via _rune_crit_bonus key)
    crit_chance += attacker.get("_rune_crit_bonus", 0) / 100

    # Blind miss check
    if attacker.get("status") == "blind":
        miss_chance = STATUS_EFFECTS["blind"].get("miss_chance", 0.30)
        if random.random() < miss_chance:
            return 0, False, type_mult, 0

    is_crit = random.random() < crit_chance

    # Crit immunity (Abyssal Shroud armor)
    if defender.get("_crit_immune") and is_crit:
        is_crit = False

    # Supernova Critical Mass — pure read only, return charge delta to caller
    crit_charge_delta = 0
    if passive_id == "critical_mass":
        effect         = attacker.get("divine_passive", {}).get("passive_effect", {})
        charges_needed = effect.get("charges_needed", 3)
        current_charges = attacker.get("dp_crit_charges", 0)
        if not is_crit:
            crit_charge_delta = +1          # tell caller to add 1 charge
        if current_charges + (1 if not is_crit else 0) >= charges_needed:
            is_crit           = True
            damage           *= effect.get("charged_crit_multiplier", 3.0)
            crit_charge_delta = -charges_needed  # tell caller to reset
    elif is_crit:
        damage *= 1.5

    # Variance roll
    damage = int(damage * random.uniform(0.85, 1.15))
    return max(1, damage), is_crit, type_mult, crit_charge_delta

class BattleView(discord.ui.View):
    # 90s gives the pinged opponent realistic time to notice the mention,
    # switch to the channel, and respond. The old 30s window expired before
    # most players ever clicked, leaving dead buttons with no explanation.
    def __init__(self, battle_id: int, challenger_id: int, opponent_id: int):
        super().__init__(timeout=90)
        self.battle_id = battle_id
        self.challenger_id = challenger_id
        self.opponent_id = opponent_id
        self.message: discord.Message | None = None  # set by the /battle command

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    async def on_timeout(self):
        # Without this, an expired challenge silently leaves clickable-looking
        # buttons that do nothing. Now it visibly closes the challenge.
        pending_battles.pop(self.battle_id, None)
        self._disable_all()
        if self.message:
            try:
                await self.message.edit(
                    embed=discord.Embed(
                        description="⌛ This battle challenge expired. Use `/battle` to try again.",
                        color=COLORS["info"],
                    ),
                    view=self,
                )
            except discord.HTTPException:
                pass  # message deleted or otherwise gone — nothing to clean up

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        # View callbacks don't hit bot.py's app-command error handler, so an
        # exception here would otherwise vanish with no feedback to the players.
        import logging
        logging.getLogger("chibibeasts.battle").exception("BattleView error", exc_info=error)
        msg = "✦ Something went wrong starting that battle — try `/battle` again."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Accept Battle", style=discord.ButtonStyle.success, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            return await interaction.response.send_message("This challenge isn't for you!", ephemeral=True)

        # The challenge may have expired or been cleared (e.g. bot restart wiped
        # the in-memory record). Fail loudly instead of doing nothing.
        if self.battle_id not in pending_battles:
            self.stop()
            self._disable_all()
            return await interaction.response.edit_message(
                embed=discord.Embed(
                    description="⌛ This challenge is no longer active. Use `/battle` to start a new one.",
                    color=COLORS["info"],
                ),
                view=self,
            )

        # Re-validate both trainers still have an active beast — either side
        # could have released or swapped theirs during the wait.
        challenger_beast = await get_active_beast(self.challenger_id)
        opponent_beast = await get_active_beast(self.opponent_id)
        if not challenger_beast or not opponent_beast:
            self.stop()
            pending_battles.pop(self.battle_id, None)
            self._disable_all()
            return await interaction.response.edit_message(
                embed=discord.Embed(
                    description="✦ One of the trainers no longer has an active beast. Challenge cancelled.",
                    color=COLORS["error"],
                ),
                view=self,
            )

        self.stop()
        self._disable_all()
        await interaction.response.edit_message(view=self)
        await start_battle(interaction, self.battle_id)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            return await interaction.response.send_message("This challenge isn't for you!", ephemeral=True)
        self.stop()
        pending_battles.pop(self.battle_id, None)
        self._disable_all()
        await interaction.response.edit_message(
            embed=discord.Embed(description="❌ Battle challenge declined.", color=COLORS["error"]),
            view=self
        )

class MoveView(discord.ui.View):
    def __init__(self, moves: list, ultimate: str, current_player_id: int, battle_state: dict):
        super().__init__(timeout=30)
        self.chosen_move = None
        self.current_player_id = current_player_id
        self.battle_state = battle_state

        for i, move in enumerate(moves[:3]):
            btn = discord.ui.Button(label=move, style=discord.ButtonStyle.primary, row=0)
            btn.callback = self.make_callback(move)
            self.add_item(btn)

        ult_btn = discord.ui.Button(
            label=f"⚡ {ultimate}",
            style=discord.ButtonStyle.danger,
            row=1,
            disabled=battle_state.get("mana", 0) < 50
        )
        ult_btn.callback = self.make_callback(ultimate, is_ultimate=True)
        self.add_item(ult_btn)

    def make_callback(self, move: str, is_ultimate: bool = False):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.current_player_id:
                return await interaction.response.send_message("It's not your turn!", ephemeral=True)
            self.chosen_move = (move, is_ultimate)
            self.stop()
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
        return callback

def apply_status(beast_state: dict, status: str, perks: list = None) -> bool:
    if perks:
        for perk in perks:
            if perk["perk_id"] == "tether_of_fate" and perk["equipped"]:
                if random.random() < 0.25:
                    return False
    beast_state["status"] = status
    return True

async def start_battle(interaction: discord.Interaction, battle_id: int):
    battle = pending_battles.get(battle_id)
    if not battle:
        return

    async with aiosqlite.connect("db/chibibeast.db") as db:
        db.row_factory = aiosqlite.Row

        async with db.execute(
            "SELECT * FROM player_beasts WHERE user_id = ? AND is_active = 1", (battle["challenger_id"],)
        ) as c:
            c_beast_row = await c.fetchone()

        async with db.execute(
            "SELECT * FROM player_beasts WHERE user_id = ? AND is_active = 1", (battle["opponent_id"],)
        ) as c:
            o_beast_row = await c.fetchone()

        async with db.execute(
            "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1", (battle["challenger_id"],)
        ) as c:
            c_perks = [dict(r) for r in await c.fetchall()]

        async with db.execute(
            "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1", (battle["opponent_id"],)
        ) as c:
            o_perks = [dict(r) for r in await c.fetchall()]

    if not c_beast_row or not o_beast_row:
        return await interaction.followup.send(embed=discord.Embed(
            description="✦ One of the trainers doesn't have an active beast!",
            color=COLORS["error"]
        ))

    c_beast_data = get_beast_data(c_beast_row["beast_id"])
    o_beast_data = get_beast_data(o_beast_row["beast_id"])

    c_state = {
        "id": c_beast_row["id"], "name": c_beast_row["nickname"] or c_beast_data["name"],
        "hp": c_beast_row["hp"], "max_hp": c_beast_row["max_hp"],
        "attack": c_beast_row["attack"], "defense": c_beast_row["defense"],
        "speed": c_beast_row["speed"], "mana": c_beast_row["mana"],
        "max_mana": c_beast_row["max_mana"], "status": None, "status_turns": 0,
        "phoenix_used": False, "moves": c_beast_data["moves"], "ultimate": c_beast_data["ultimate"],
        "beast_type": c_beast_data.get("type", ""),
        **init_divine_state(c_beast_data, dict(c_beast_row))
    }
    o_state = {
        "id": o_beast_row["id"], "name": o_beast_row["nickname"] or o_beast_data["name"],
        "hp": o_beast_row["hp"], "max_hp": o_beast_row["max_hp"],
        "attack": o_beast_row["attack"], "defense": o_beast_row["defense"],
        "speed": o_beast_row["speed"], "mana": o_beast_row["mana"],
        "max_mana": o_beast_row["max_mana"], "status": None, "status_turns": 0,
        "phoenix_used": False, "moves": o_beast_data["moves"], "ultimate": o_beast_data["ultimate"],
        "beast_type": o_beast_data.get("type", ""),
        **init_divine_state(o_beast_data, dict(o_beast_row))
    }

    # ── Happiness modifier (same formula as PvE, applied to both fighters) ──
    def _apply_happiness(state: dict, beast_row) -> None:
        hap = beast_row["happiness"] if beast_row["happiness"] is not None else 100
        if hap >= 100:
            mult = 1.10
        elif hap <= 30:
            mult = 0.90
        else:
            mult = 0.90 + (hap - 30) / 70 * 0.20
        state["attack"]  = max(1, int(state["attack"]  * mult))
        state["defense"] = max(1, int(state["defense"] * mult))
        state["speed"]   = max(1, int(state["speed"]   * mult))

    _apply_happiness(c_state, c_beast_row)
    _apply_happiness(o_state, o_beast_row)

    # ── Apply equipment bonuses ─────────────────────────────────────────────
    def apply_equipment_bonuses(beast_state: dict, beast_row_id: int, user_id: int, equipment: dict, runes: dict, battle_log: list):
        """Apply equipped armor and rune stat bonuses to beast state."""
        import json as _json

        # Load equipped armor
        equipped_gear = []
        # (armor is read from player_equipment, rune from player_beasts.rune_id)

        # Apply rune bonuses (already on beast_row via rune_id)
        rune_id = None
        # We need to look this up from the already-fetched beast_row
        return beast_state  # stat bonuses applied below via separate query

    # Apply equipment stat bonuses via DB query
    async def _apply_equipment(state: dict, beast_row_id: int, user_id: int):
        # Use module-level _ALL_GEAR cache — no per-battle disk I/O
        all_gear = _ALL_GEAR

        async with aiosqlite.connect("db/chibibeast.db") as _db:
            _db.row_factory = aiosqlite.Row
            # Equipped armor
            async with _db.execute(
                "SELECT equipment_id FROM player_equipment WHERE user_id = ? AND beast_row_id = ?",
                (user_id, beast_row_id)
            ) as _c:
                _armor_rows = [dict(r) for r in await _c.fetchall()]
            # Equipped rune (stored on beast_row)
            async with _db.execute(
                "SELECT rune_id FROM player_beasts WHERE id = ?", (beast_row_id,)
            ) as _c:
                _b = await _c.fetchone()
            _rune_id = _b["rune_id"] if _b else None
            # Brew/krakenshale flag
            async with _db.execute(
                "SELECT brew_active, damage_multiplier FROM players WHERE user_id = ?", (user_id,)
            ) as _c:
                _pr_row = await _c.fetchone()
                _pr = dict(_pr_row) if _pr_row else None

        gear_bonus_log = []
        for _ar in _armor_rows:
            _g = all_gear.get(_ar["equipment_id"], {})
            _eff = _g.get("effect", {})
            if "defense_percent" in _eff:
                bonus = int(state["defense"] * _eff["defense_percent"] / 100)
                state["defense"] += bonus
                gear_bonus_log.append(f"+{bonus} DEF ({_g.get('name','gear')})")
            if "speed_percent" in _eff:
                bonus = int(state["speed"] * abs(_eff["speed_percent"]) / 100)
                if _eff["speed_percent"] > 0:
                    state["speed"] += bonus
                else:
                    state["speed"] = max(1, state["speed"] - bonus)
            if "damage_reduction_flat_percent" in _eff:
                state["_armor_reduction"] = _eff["damage_reduction_flat_percent"]
            if "hp_regen_percent_per_round" in _eff:
                state["_hp_regen_percent"] = _eff["hp_regen_percent_per_round"]
            if "crit_immunity" in _eff:
                state["_crit_immune"] = True
            if "attack_double_below_25hp" in _eff:
                state["_phoenix_shroud"] = True
                state["_phoenix_shroud_triggered"] = False
            if "evasion_percent" in _eff:
                state["_evasion"] = _eff["evasion_percent"]

        if _rune_id:
            _r = all_gear.get(_rune_id, {})
            _eff = _r.get("effect", {})
            if "speed" in _eff:
                state["speed"] += _eff["speed"]
            if "defense" in _eff:
                state["defense"] += _eff["defense"]
            if "crit_chance" in _eff:
                state["_rune_crit_bonus"] = _eff["crit_chance"]
            if "lifesteal_percent" in _eff:
                state["_rune_lifesteal"] = _eff["lifesteal_percent"]
            if "death_explosion_fire" in _eff:
                state["_death_explosion"] = True

        # Krakenshale Brew: double defense flag
        if _pr and _pr.get("brew_active") and _pr["brew_active"] > 0:
            state["defense"] = int(state["defense"] * 2.0)
            gear_bonus_log.append("×2 DEF (Krakenshale Brew)")
            async with aiosqlite.connect("db/chibibeast.db") as _db2:
                await _db2.execute(
                    "UPDATE players SET brew_active = CASE WHEN brew_active > 0 THEN brew_active - 1 ELSE 0 END WHERE user_id = ?",
                    (user_id,)
                )
                await _db2.commit()

        # Ambrosia Tart permanent damage multiplier
        if _pr and _pr.get("damage_multiplier") and _pr["damage_multiplier"] > 1.0:
            state["_damage_multiplier"] = _pr["damage_multiplier"]

        return gear_bonus_log

    c_gear_log = await _apply_equipment(c_state, c_beast_row["id"], battle["challenger_id"])
    o_gear_log = await _apply_equipment(o_state, o_beast_row["id"], battle["opponent_id"])

    # Fable Feather check
    async with aiosqlite.connect("db/chibibeast.db") as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM player_inventory WHERE user_id = ? AND item_id = 'fable_feather'",
            (battle["challenger_id"],)
        ) as c:
            c_feather = await c.fetchone()
        async with db.execute(
            "SELECT * FROM player_inventory WHERE user_id = ? AND item_id = 'fable_feather'",
            (battle["opponent_id"],)
        ) as c:
            o_feather = await c.fetchone()

    # Turn order by speed
    c_goes_first = c_state["speed"] >= o_state["speed"]
    if c_feather and not o_feather:
        c_goes_first = True
    elif o_feather and not c_feather:
        c_goes_first = False

    challenger = interaction.guild.get_member(battle["challenger_id"])
    opponent = interaction.guild.get_member(battle["opponent_id"])

    turn = 1
    battle_log = []
    winner_id = None

    # Apply battle-start passives (Gravity Well, Stellar Nursery)
    apply_battle_start_passives(c_state, o_state, battle_log)
    if c_goes_first:
        apply_battle_start_passives(o_state, c_state, battle_log)  # opponent's passives on challenger
    else:
        apply_battle_start_passives(o_state, c_state, battle_log)

    while c_state["hp"] > 0 and o_state["hp"] > 0 and turn <= 20:
        attacker_id = battle["challenger_id"] if (turn % 2 == 1) == c_goes_first else battle["opponent_id"]
        attacker_state = c_state if attacker_id == battle["challenger_id"] else o_state
        defender_state = o_state if attacker_id == battle["challenger_id"] else c_state
        attacker_perks = c_perks if attacker_id == battle["challenger_id"] else o_perks
        defender_perks = o_perks if attacker_id == battle["challenger_id"] else c_perks
        attacker_member = challenger if attacker_id == battle["challenger_id"] else opponent

        # ── Status effect resolution ─────────────────────────────────────────
        # Freeze/sleep: log the skip first, THEN decrement so turn 1 of a
        # 2-turn freeze is correctly shown as "frozen" (not silently cleared).
        if attacker_state["status"] in ["freeze", "sleep"]:
            emoji = STATUS_EFFECTS[attacker_state["status"]]["emoji"]
            battle_log.append(
                f"{emoji} **{attacker_state['name']}** is "
                f"{'frozen' if attacker_state['status'] == 'freeze' else 'asleep'} and can't move!"
            )
            # Decrement after logging — clear only when turns fully expire
            attacker_state["status_turns"] -= 1
            if attacker_state["status_turns"] <= 0:
                attacker_state["status"] = None
                attacker_state["status_turns"] = 0
            turn += 1
            continue

        # Turn-start divine passives (Star Weave, Borrowed Time, Constellation Charge)
        double_turn = apply_turn_start_passives(attacker_state, battle_log, defender_state)

        # Armor HP regen per round (Unicorn Vestments)
        if attacker_state.get("_hp_regen_percent") and attacker_state["hp"] > 0:
            regen = max(1, int(attacker_state["max_hp"] * attacker_state["_hp_regen_percent"] / 100))
            attacker_state["hp"] = min(attacker_state["max_hp"], attacker_state["hp"] + regen)
            battle_log.append(f"🩹 **{attacker_state['name']}** regenerated {regen} HP!")

        if attacker_state["status"] == "poison":
            poison_dmg = max(1, int(attacker_state["max_hp"] * 0.05))
            attacker_state["hp"] = max(0, attacker_state["hp"] - poison_dmg)
            battle_log.append(f"☠️ **{attacker_state['name']}** took `{poison_dmg}` poison damage!")

        if attacker_state["status"] == "burn":
            burn_dmg = max(1, int(attacker_state["max_hp"] * 0.08))
            attacker_state["hp"] = max(0, attacker_state["hp"] - burn_dmg)
            battle_log.append(f"🔥 **{attacker_state['name']}** took `{burn_dmg}` burn damage!")

        if attacker_state["status"] == "blight":
            blight_dmg = max(1, int(attacker_state["max_hp"] * 0.06))
            attacker_state["hp"] = max(0, attacker_state["hp"] - blight_dmg)
            battle_log.append(f"💜 **{attacker_state['name']}** took `{blight_dmg}` blight damage!")

        if attacker_state["hp"] <= 0:
            break

        # Show move selection for current attacker
        embed = discord.Embed(
            title=f"⚔️ Battle — Turn {turn}",
            description=(
                f"**{challenger.display_name}'s** {c_state['name']}\n"
                f"{hp_bar(c_state['hp'], c_state['max_hp'])}\n"
                f"{'☠️ Poisoned' if c_state['status'] == 'poison' else ''}"
                f"\n\n"
                f"**{opponent.display_name}'s** {o_state['name']}\n"
                f"{hp_bar(o_state['hp'], o_state['max_hp'])}\n"
                f"{'☠️ Poisoned' if o_state['status'] == 'poison' else ''}"
            ),
            color=COLORS["info"]
        )
        if battle_log:
            embed.add_field(name="📜 Last Turn", value="\n".join(battle_log[-3:]), inline=False)
        embed.set_footer(text=f"⚔️ {attacker_member.display_name}'s turn — Choose your move!")

        move_view = MoveView(
            attacker_state["moves"], attacker_state["ultimate"],
            attacker_id, attacker_state
        )
        await interaction.channel.send(embed=embed, view=move_view)
        await move_view.wait()

        if move_view.chosen_move is None:
            # Auto pick random move on timeout
            move_view.chosen_move = (random.choice(attacker_state["moves"]), False)

        damage, is_crit, type_mult, crit_charge_delta = calc_damage(
            attacker_state, defender_state, move_name, is_ultimate, attacker_perks
        )

        # ── Mana update before blind-miss short-circuit ─────────────────────
        # The attempt was made. A blind ultimate that misses still costs mana.
        # A blind basic attack still earns its passive mana regen for the turn.
        #
        # Mana is always clamped: max(0,...) on deductions, min(max_mana,...) on
        # additions. This means mana ∈ [0, max_mana] after every operation.
        # Frozen/sleep turn-skips fire before this block (continue before reaching
        # it), so a beast waking up from sleep at 0 mana will not regen on the
        # turn it skips — correct behaviour, not a bug.
        if is_ultimate:
            attacker_state["mana"] = max(0, attacker_state["mana"] - 50)
        else:
            # Mana gain scales with speed — faster beasts charge ultimates quicker
            mana_gain = min(15, 8 + attacker_state.get("speed", 50) // 40)
            attacker_state["mana"] = min(attacker_state["max_mana"], attacker_state["mana"] + mana_gain)

        # Handle missed attack (Blind)
        if damage == 0 and attacker_state.get("status") == "blind":
            battle_log.append(f"👁️ **{attacker_state['name']}** attacked but missed! (Blinded)")
            turn += 1
            continue

        # Apply on-hit attacker passives (Void Hunger, Dark Matter, Terminus, Critical Mass)
        damage = apply_on_hit_passives(attacker_state, defender_state, damage, is_ultimate, battle_log)

        # Apply Supernova charge delta post-resolution (pure calc_damage returned it)
        if crit_charge_delta != 0:
            if crit_charge_delta > 0:
                attacker_state["dp_crit_charges"] = attacker_state.get("dp_crit_charges", 0) + crit_charge_delta
                if attacker_state["dp_crit_charges"] >= 3:
                    battle_log.append(f"🌟 **{attacker_state['name']}'s Critical Mass** is charged — next hit is GUARANTEED CRIT!")
            else:
                attacker_state["dp_crit_charges"] = 0

        # Rune lifesteal
        # Lifesteal now handled in on-hit block above
            battle_log.append(f"💍 **Ouroboros Ring** — {attacker_state['name']} healed {heal} HP!")

        # On-hit-taken defender passives (Shield, Weight of Worlds, Mirror Reality, Bifrost Surge)
        damage = apply_on_hit_taken_passives(attacker_state, defender_state, damage, battle_log)

        # Armor flat damage reduction
        if defender_state.get("_armor_reduction") and damage > 0:
            reduced = max(0, int(damage * defender_state["_armor_reduction"] / 100))
            damage = max(1, damage - reduced)

        # Evasion check
        if defender_state.get("_evasion") and random.random() < defender_state["_evasion"] / 100:
            battle_log.append(f"💨 **{defender_state['name']}** evaded the attack!")
            damage = 0

        # Phoenix Shroud — double attack below 25% HP (once)
        if attacker_state.get("_phoenix_shroud") and not attacker_state.get("_phoenix_shroud_triggered"):
            if attacker_state["hp"] <= attacker_state["max_hp"] * 0.25:
                attacker_state["attack"] = int(attacker_state["attack"] * 2)
                attacker_state["_phoenix_shroud_triggered"] = True
                battle_log.append(f"🔥 **Phoenix-Born Shroud** ignites — {attacker_state['name']}'s ATK doubled!")

        # Phoenix perk check
        if defender_state["hp"] - damage <= 0:
            # Genesis First Flame divine passive
            if apply_ko_passives(defender_state, attacker_state, battle_log):
                damage = 0  # KO prevented
            else:
                # Core of the Phoenix rune — death explosion
                if defender_state.get("_death_explosion"):
                    explosion = int(defender_state["max_hp"] * 0.30)
                    attacker_state["hp"] = max(0, attacker_state["hp"] - explosion)
                    battle_log.append(f"💥 **Core of the Phoenix** — {defender_state['name']} explodes for {explosion} fire damage!")
                phoenix_active = False
                for perk in defender_perks:
                    if perk["perk_id"] == "aura_of_phoenix" and perk["equipped"]:
                        if not defender_state["phoenix_used"]:
                            phoenix_active = True
                            defender_state["phoenix_used"] = True
                            break
                if phoenix_active:
                    defender_state["hp"] = 1
                    damage = 0
                    battle_log.append(f"🔥 **Aura of the Phoenix** activated! {defender_state['name']} survived with 1 HP!")
                else:
                    defender_state["hp"] = max(0, defender_state["hp"] - damage)
        else:
            defender_state["hp"] = max(0, defender_state["hp"] - damage)

        effectiveness = type_effectiveness_label(type_mult)
        crit_tag = "⭐ CRIT! " if is_crit else ""
        type_tag = f" {effectiveness}" if effectiveness else ""
        # Show divine passive name if beast is divine
        divine_tag = ""
        if attacker_state.get("divine_passive"):
            pid = attacker_state["divine_passive"].get("passive_id", "")
            if pid in ["void_hunger", "dark_matter", "final_word"]:
                divine_tag = f" ✨"
        log_entry = f"{crit_tag}**{attacker_state['name']}** used **{move_name}** → `{damage}` dmg!{type_tag}{divine_tag}"
        battle_log.append(log_entry)

        # Random status chance — Nirvana is immune to all status
        if random.random() < 0.15 and not defender_state["status"]:
            # Nirvana: Lotus Veil — status immune
            if defender_state.get("divine_passive", {}).get("passive_effect", {}).get("status_immune"):
                pass  # immune, nothing happens
            else:
                possible_statuses = ["poison", "burn", "freeze", "sleep", "paralyze"]
                new_status = random.choice(possible_statuses)

                # Karma: Karmic Echo — 40% chance to reflect status
                if defender_state.get("divine_passive", {}).get("passive_id") == "karmic_echo":
                    if random.random() < 0.40:
                        attacker_state["status"] = new_status
                        attacker_state["status_turns"] = STATUS_EFFECTS[new_status].get("skip_turns", 0)
                        battle_log.append(f"🔄 **{defender_state['name']}'s Karmic Echo** reflected {new_status} back!")
                        new_status = None

                if new_status:
                    applied = apply_status(defender_state, new_status, defender_perks)
                    if applied:
                        defender_state["status_turns"] = STATUS_EFFECTS[new_status].get("skip_turns", 0)
                        battle_log.append(f"{STATUS_EFFECTS[new_status]['emoji']} **{defender_state['name']}** is now **{new_status}**!")

        turn += 1

        # ── End-of-turn status cleanup ───────────────────────────────────────
        # All status turn decrements happen here, after the full turn resolves,
        # so the log never shows a cleared status mid-action.
        for state in [attacker_state, defender_state]:
            if state["status"] in ["blind", "paralyze"] and state.get("status_turns", 0) > 0:
                state["status_turns"] -= 1
                if state["status_turns"] <= 0:
                    prev = state["status"]
                    state["status"] = None
                    state["status_turns"] = 0
                    battle_log.append(f"✨ **{state['name']}** recovered from {prev}!")
        if double_turn and attacker_state["hp"] > 0 and defender_state["hp"] > 0:
            second_damage, second_crit, second_mult, _ = calc_damage(
                attacker_state, defender_state,
                random.choice(attacker_state["moves"]), False, attacker_perks
            )
            second_damage = apply_on_hit_taken_passives(attacker_state, defender_state, second_damage, battle_log)
            defender_state["hp"] = max(0, defender_state["hp"] - second_damage)
            battle_log.append(f"⏳ **{attacker_state['name']}** acts again (Borrowed Time) → `{second_damage}` dmg!")

    # Determine winner — distinguish timeout from genuine KO
    timed_out_pvp = turn > 20 and c_state["hp"] > 0 and o_state["hp"] > 0

    if timed_out_pvp:
        # Both survived — judge by remaining HP percentage
        c_pct = c_state["hp"] / max(c_state["max_hp"], 1)
        o_pct = o_state["hp"] / max(o_state["max_hp"], 1)
        if c_pct > o_pct:
            winner_id = battle["challenger_id"]
        elif o_pct > c_pct:
            winner_id = battle["opponent_id"]
        else:
            winner_id = None  # exact tie on HP percentage
    elif c_state["hp"] <= 0 and o_state["hp"] <= 0:
        winner_id = None
    elif c_state["hp"] <= 0:
        winner_id = battle["opponent_id"]
    else:
        winner_id = battle["challenger_id"]

    # Rewards
    exp_gain = random.randint(40, 80)
    gold_gain = random.randint(50, 150)

    if winner_id:
        winner = challenger if winner_id == battle["challenger_id"] else opponent
        loser_id = battle["opponent_id"] if winner_id == battle["challenger_id"] else battle["challenger_id"]

        winner_player = await get_player(winner_id)
        loser_player = await get_player(loser_id)

        # Fable Scholar perk
        for perk in (c_perks if winner_id == battle["challenger_id"] else o_perks):
            if perk["perk_id"] == "fable_scholar":
                exp_gain = int(exp_gain * 1.10)

        new_wins = winner_player["wins"] + 1
        new_gold = winner_player["gold"] + gold_gain
        new_exp = winner_player["exp"] + exp_gain
        new_level = winner_player["level"]

        while new_exp >= calc_player_exp_for_level(new_level):
            new_exp -= calc_player_exp_for_level(new_level)
            new_level += 1

        await update_player(winner_id, wins=new_wins, gold=new_gold, exp=new_exp, level=new_level)
        if new_level > winner_player["level"]:
            try:
                from cogs.questline import advance_quest_step as _aq
                await _aq(winner_id, "level_up", level=new_level)
            except Exception:
                pass

        # ── Loser consolation rewards ──────────────────────────────────────
        # ~25% of win rewards so losing always yields something.
        # Low enough that intentional losing is never better than trying to win.
        # High enough that a close loss against a tough opponent still feels
        # like time well spent — prevents tactical paralysis.
        loser_consolation_gold = random.randint(15, 40)
        loser_consolation_exp  = random.randint(10, 20)
        loser_beast_exp        = random.randint(5, 10)

        loser_new_exp   = loser_player["exp"] + loser_consolation_exp
        loser_new_level = loser_player["level"]
        while loser_new_exp >= calc_player_exp_for_level(loser_new_level):
            loser_new_exp -= calc_player_exp_for_level(loser_new_level)
            loser_new_level += 1

        await update_player(
            loser_id,
            losses=(loser_player["losses"] + 1),
            gold=loser_player["gold"] + loser_consolation_gold,
            exp=loser_new_exp,
            level=loser_new_level
        )
        if loser_new_level > loser_player["level"]:
            try:
                from cogs.questline import advance_quest_step as _aq
                await _aq(loser_id, "level_up", level=loser_new_level)
            except Exception:
                pass

        # Loser beast also gains a small participation EXP
        loser_beast_row  = c_beast_row if loser_id == battle["challenger_id"] else o_beast_row
        loser_beast_dict = dict(loser_beast_row)
        loser_beast_new_exp   = loser_beast_dict["exp"] + loser_beast_exp
        loser_beast_new_level = loser_beast_dict["level"]
        while loser_beast_new_exp >= get_beast_exp_for_level(loser_beast_dict, loser_beast_new_level):
            loser_beast_new_exp -= get_beast_exp_for_level(loser_beast_dict, loser_beast_new_level)
            loser_beast_new_level += 1
        async with aiosqlite.connect("db/chibibeast.db") as _ldb:
            await apply_beast_levelup(_ldb, loser_beast_dict, loser_beast_new_level, loser_beast_new_exp)
            await _ldb.commit()

        # Beast EXP + stat growth for the winner's active beast
        beast_exp_gain = random.randint(20, 45)
        winner_beast_row = c_beast_row if winner_id == battle["challenger_id"] else o_beast_row
        winner_beast_dict = dict(winner_beast_row)
        beast_new_exp = winner_beast_dict["exp"] + beast_exp_gain
        beast_new_level = winner_beast_dict["level"]
        # Use the correct EXP curve for this beast's origin (starter vs wild)
        while beast_new_exp >= get_beast_exp_for_level(winner_beast_dict, beast_new_level):
            beast_new_exp -= get_beast_exp_for_level(winner_beast_dict, beast_new_level)
            beast_new_level += 1
        beast_leveled = beast_new_level > winner_beast_dict["level"]
        async with aiosqlite.connect("db/chibibeast.db") as db:
            await apply_beast_levelup(db, winner_beast_dict, beast_new_level, beast_new_exp)
            # Restore both active beasts to full HP after battle
            await db.execute(
                "UPDATE player_beasts SET hp = max_hp WHERE id IN (?, ?)",
                (winner_beast_dict["id"], loser_beast_dict["id"])
            )
            await db.commit()

        # ── Progress tracking: quests + achievements for the winner ────
        completed_quests = await track_quest_event(winner_id, "battle_win")
        if beast_leveled:
            await track_quest_event(winner_id, "beast_level_up")
        unlocked = await check_achievements(winner_id)

        loser = opponent if winner_id == battle["challenger_id"] else challenger
        timeout_note = (
            f"\n\n*⏱️ Battle reached 20-turn limit — winner decided by remaining HP.*"
            if timed_out_pvp else ""
        )
        result_embed = discord.Embed(
            title=f"⚔️ Battle Over!{' (Time Limit)' if timed_out_pvp else ''}",
            description=(
                f"✨ **{winner.display_name}** wins!\n\n"
                f"**{c_state['name']}** — {hp_bar(max(0, c_state['hp']), c_state['max_hp'])}\n"
                f"**{o_state['name']}** — {hp_bar(max(0, o_state['hp']), o_state['max_hp'])}"
                + timeout_note
                + f"\n\n**{winner.display_name} (Victory):** +{exp_gain} EXP | +{gold_gain} 💰"
                + (f"\n⬆️ **Trainer Level Up!** Now level {new_level}!" if new_level > winner_player["level"] else "")
                + f"\n**Beast:** +{beast_exp_gain} EXP"
                + (f" | ⬆️ **{winner_beast_dict.get('nickname') or get_beast_data(winner_beast_dict['beast_id'])['name']} leveled up to Lv.{beast_new_level}!**" if beast_leveled else "")
                + f"\n\n**{loser.display_name} (Lesson):** +{loser_consolation_exp} EXP | +{loser_consolation_gold} 💰 | Beast +{loser_beast_exp} EXP"
                + f"\n*Every lesson teaches something.*"
            ),
            color=COLORS["legendary"]
        )
    else:
        if timed_out_pvp:
            result_embed = discord.Embed(
                title="⚔️ Battle Over — Time Limit Draw!",
                description=(
                    "*Both beasts survived 20 turns and finished at equal HP.*\n\n"
                    "*No rewards given — fight again to break the tie.*"
                ),
                color=COLORS["info"]
            )
        else:
            result_embed = discord.Embed(
                title="⚔️ Battle Over — It's a Draw!",
                description="Both beasts fainted at the same time!\n\nNo rewards given.",
                color=COLORS["info"]
            )

    result_embed.add_field(name="📜 Battle Log", value="\n".join(battle_log[-5:]) or "No moves logged.", inline=False)
    result_embed.set_footer(text="ChibiBeasts 🐾  •  /battle to fight again!")
    await interaction.channel.send(embed=result_embed)

    # ── Log battle for /stats tracking ────────────────────────────────────
    async with aiosqlite.connect("db/chibibeast.db") as _blog:
        await _blog.execute(
            """INSERT INTO battles
               (challenger_id, opponent_id, winner_id, challenger_beast, opponent_beast, status, battle_type)
               VALUES (?, ?, ?, ?, ?, 'completed', 'pvp')""",
            (battle["challenger_id"], battle["opponent_id"],
             winner_id,
             c_beast_row["id"], o_beast_row["id"])
        )
        await _blog.commit()

    if winner_id:
        await notify_quest_completions(interaction.channel, completed_quests)
        await notify_unlocks(interaction.channel, winner, unlocked)

    pending_battles.pop(battle_id, None)

class Battle(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /challenge ────────────────────────────────────────────────────────
    @app_commands.command(name="challenge", description="Battle a wild beast in a biome! 🌿")
    @app_commands.describe(biome="Which biome to challenge a wild beast from")
    @app_commands.choices(biome=[
        app_commands.Choice(name="🌲 Whispering Woods (Lv1+)",          value="woods"),
        app_commands.Choice(name="🔥 Ember Wastes (Lv10+)",             value="ember"),
        app_commands.Choice(name="❄️ Glacial Hollows (Lv12+)",          value="glacial"),
        app_commands.Choice(name="🌊 Sunken Abyssal Trenches (Lv15+)",  value="trenches"),
        app_commands.Choice(name="🌌 Celestial Loom (Lv25+)",           value="loom"),
    ])
    async def challenge(self, interaction: discord.Interaction, biome: str = "woods"):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        active_row = await get_active_beast(interaction.user.id)
        if not active_row:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need an active beast to battle! Use `/setactive`.",
                color=COLORS["error"]
            ))

        BIOME_GATES = {"woods": 1, "ember": 10, "glacial": 12, "trenches": 15, "loom": 25}
        BIOME_NAMES = {
            "woods":    "🌲 Whispering Woods",
            "ember":    "🔥 Ember Wastes",
            "glacial":  "❄️ Glacial Hollows",
            "trenches": "🌊 Sunken Abyssal Trenches",
            "loom":     "🌌 Celestial Loom",
        }
        BIOME_POOLS = {
            "woods":    {"common": 0.55, "uncommon": 0.30, "rare": 0.10, "epic": 0.05},
            "ember":    {"uncommon": 0.35, "rare": 0.35, "epic": 0.20, "legendary": 0.10},
            "glacial":  {"uncommon": 0.30, "rare": 0.35, "epic": 0.25, "legendary": 0.10},
            "trenches": {"rare": 0.30, "epic": 0.35, "legendary": 0.25, "divine": 0.10},
            "loom":     {"epic": 0.30, "legendary": 0.40, "divine": 0.30},
        }

        min_level = BIOME_GATES[biome]
        if player["level"] < min_level:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You need to be **Level {min_level}** to challenge beasts in {BIOME_NAMES[biome]}.",
                color=COLORS["error"]
            ))

        all_beasts = json.load(open("data/beasts.json"))["beasts"]
        pool_weights = dict(BIOME_POOLS[biome])
        # Bias rarity pool toward player's active beast rarity — prevents trivial dead zones
        _RARITY_ORDER = ["common","uncommon","rare","epic","legendary","divine"]
        _player_rarity = active_row.get("rarity", "common")
        _player_tier = _RARITY_ORDER.index(_player_rarity) if _player_rarity in _RARITY_ORDER else 0
        _biased_weights = {}
        for _r, _w in pool_weights.items():
            _r_tier = _RARITY_ORDER.index(_r) if _r in _RARITY_ORDER else 0
            _tier_diff = abs(_player_tier - _r_tier)
            # Closer to player rarity = 3x weight, 1 tier away = 1.5x, 2+ tiers = unchanged
            _bias = 3.0 if _tier_diff == 0 else (1.5 if _tier_diff == 1 else 1.0)
            _biased_weights[_r] = _w * _bias
        _total = sum(_biased_weights.values())
        _biased_weights = {k: v / _total for k, v in _biased_weights.items()}
        rarity = random.choices(list(_biased_weights), weights=list(_biased_weights.values()))[0]

        # Pick a wild beast of that rarity from the biome
        candidates = [
            b for b in all_beasts.values()
            if b["rarity"] == rarity and not b.get("starter")
        ]
        if not candidates:
            candidates = [b for b in all_beasts.values() if b["rarity"] == "common"]
        wild_beast_data = random.choice(candidates)

        # Scale wild beast to player's active beast level ±2
        player_beast_level = active_row["level"]
        wild_level = max(1, min(50, player_beast_level + random.randint(-2, 2)))
        enemy_state = build_pve_beast_state(wild_beast_data, wild_level)

        active_beast_data = get_beast_data(active_row["beast_id"]) or {}
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1",
                (interaction.user.id,)
            ) as c:
                player_perks = [dict(r) for r in await c.fetchall()]

        rarity_emoji = RARITY_EMOJI.get(rarity, "⚪")
        await interaction.followup.send(embed=discord.Embed(
            title=f"⚔️ Wild Battle! {BIOME_NAMES[biome]}",
            description=(
                f"A wild **{rarity_emoji} {wild_beast_data['name']}** (Lv.{wild_level}) "
                f"appeared and wants to fight!\n\n"
                f"*{wild_beast_data['description']}*"
            ),
            color=COLORS.get(rarity, COLORS["info"])
        ))

        # Reward callbacks
        async def on_win(embed, p_state, e_state, timed_out=False):
            if timed_out:
                # Timeout win: reduced rewards, no catch chance.
                # The enemy beast wasn't defeated — just outlasted on HP.
                # Full rewards here would make stall tactics profitable.
                consolation_gold = random.randint(active_row["level"] * 2, active_row["level"] * 4)
                await update_player(interaction.user.id, gold=player["gold"] + consolation_gold)
                _lvl, _, _leveled = await award_player_exp(interaction.user.id, random.randint(active_row["level"] * 8, active_row["level"] * 12))
                embed.add_field(
                    name="⏱️ Time Limit Rewards",
                    value=(
                        f"+**{consolation_gold:,} gold** 💰 | +**10–20 EXP** ✨"
                        + (f"\n⬆️ **Trainer leveled up to {_lvl}!**" if _leveled else "")
                        + f"\n*No catch — the {wild_beast_data['name']} wasn't actually defeated.*\n"
                        f"*A win on HP, not a win by defeat. Try for a clean KO next time.*"
                    ),
                    inline=False
                )
                return

            gold_gain = random.randint(active_row["level"] * 8, active_row["level"] * 15)
            # Beast EXP scales with enemy level and rarity — harder fights reward more
            _RARITY_EXP_MULT = {"common":1,"uncommon":1.5,"rare":2.5,"epic":4,"legendary":6,"divine":10}
            _exp_mult = _RARITY_EXP_MULT.get(rarity, 1)
            beast_exp  = int(random.randint(20, 40) * _exp_mult + wild_level * 1.5)

            # Beast EXP
            new_exp   = active_row["exp"] + beast_exp
            new_level = active_row["level"]
            while new_exp >= get_beast_exp_for_level(dict(active_row), new_level):
                new_exp -= get_beast_exp_for_level(dict(active_row), new_level)
                new_level += 1
            async with aiosqlite.connect("db/chibibeast.db") as db:
                await apply_beast_levelup(db, dict(active_row), new_level, new_exp)
                await db.commit()

            await update_player(interaction.user.id, gold=player["gold"] + gold_gain)

            # Player EXP — scales with biome difficulty
            player_exp_gain = random.randint(active_row["level"] * 15, active_row["level"] * 25)
            _p_lvl, _, _p_leveled = await award_player_exp(interaction.user.id, player_exp_gain)

            # Catch chance
            CATCH_RATES = {"common": 0.50, "uncommon": 0.35, "rare": 0.20,
                          "epic": 0.10, "legendary": 0.05, "divine": 0.02}
            caught = random.random() < CATCH_RATES.get(rarity, 0.10)

            reward_text = (
                f"+**{gold_gain:,} gold** 💰 | +**{beast_exp} beast EXP** | +**{player_exp_gain} EXP** ✨"
                + (f"\n⬆️ **{active_beast_data.get('name','')} leveled up to Lv.{new_level}!**"
                   if new_level > active_row["level"] else "")
                + (f"\n⬆️ **Trainer leveled up to {_p_lvl}!**" if _p_leveled else "")
            )

            if caught:
                from utils.db import add_beast_to_player
                from utils.dispositions import roll_disposition
                from utils.progress import record_bestiary_sighting
                disposition = roll_disposition(wild_beast_data.get("rarity", "common"))
                await add_beast_to_player(interaction.user.id, {
                    **wild_beast_data, "caught_from": "wild_battle"
                })
                if interaction.guild:
                    await record_bestiary_sighting(
                        interaction.guild.id, wild_beast_data["id"], interaction.user.id
                    )
                reward_text += f"\n🎉 **{wild_beast_data['name']} was caught!** Check `/collection`."

            embed.add_field(name="🎁 Rewards", value=reward_text, inline=False)
            await track_quest_event(interaction.user.id, "battle_win")
            unlocked = await check_achievements(interaction.user.id)
            if unlocked:
                await notify_unlocks(interaction.channel, interaction.user, unlocked)

        async def on_loss(embed, p_state, e_state):
            consolation_gold = random.randint(10, 25)
            consolation_exp  = random.randint(active_row["level"] * 5, active_row["level"] * 10)
            await update_player(interaction.user.id, gold=player["gold"] + consolation_gold)
            await award_player_exp(interaction.user.id, consolation_exp)
            embed.add_field(
                name="💤 Lesson",
                value=(
                    f"+**{consolation_gold} gold** 💰 | +**{consolation_exp} EXP** ✨\n"
                    f"*{wild_beast_data['name']} returns to the {BIOME_NAMES[biome].split()[-1]}.*\n"
                    f"*Every lesson teaches something.*"
                ),
                inline=False
            )

        await run_pve_battle(
            interaction,
            dict(active_row),
            active_beast_data,
            player_perks,
            enemy_state,
            enemy_personality="wild",
            battle_title=f"Wild {wild_beast_data['name']}",
            on_win=on_win,
            on_loss=on_loss,
        )

    # ── /sparr ────────────────────────────────────────────────────────────
    @app_commands.command(name="sparr", description="Spar with an NPC to deepen your bond 🤝")
    @app_commands.describe(npc_name="Which NPC to challenge")
    @app_commands.choices(npc_name=[
        app_commands.Choice(name="📖 Maren (easy — Barkley)",              value="maren"),
        app_commands.Choice(name="⏳ Cael (medium — Twine, unpredictable)", value="cael"),
        app_commands.Choice(name="⚒️ Sable (medium-hard — Hellhound)",     value="sable"),
        app_commands.Choice(name="🌿 Orren (hard — Dryad, patient)",        value="orren"),
        app_commands.Choice(name="📚 The Archivist (endgame — Paradox)",    value="the_archivist"),
    ])
    async def sparr(self, interaction: discord.Interaction, npc_name: str):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        active_row = await get_active_beast(interaction.user.id)
        if not active_row:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need an active beast! Use `/setactive`.",
                color=COLORS["error"]
            ))

        npcs = json.load(open("data/npcs.json"))["npcs"]
        npc  = npcs.get(npc_name)
        if not npc:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ NPC not found.", color=COLORS["error"]
            ))

        # Archivist locked until Chapter 5 complete
        if npc_name == "the_archivist":
            from cogs.questline import get_quest_state
            qs = await get_quest_state(interaction.user.id)
            if "chapter_5" not in qs.get("completed_chapters", []):
                return await interaction.followup.send(embed=discord.Embed(
                    description=(
                        f"*The Archivist looks at you for a long moment.*\n\n"
                        f"*'You're not ready yet. Finish the questline first.'*\n\n"
                        f"✦ Complete The Sundering of the Loom questline to unlock this battle."
                    ),
                    color=COLORS["info"]
                ))

        # Daily spar limit — one per NPC per day
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        spar_key = f"sparr_{npc_name}"

        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT 1 FROM daily_quests WHERE user_id = ? AND quest_id = ? AND date = ?",
                (interaction.user.id, spar_key, today)
            ) as c:
                already_sparred = await c.fetchone()

        if already_sparred:
            return await interaction.followup.send(embed=discord.Embed(
                description=(
                    f"✦ You've already sparred with **{npc['name']}** today.\n"
                    f"*Come back tomorrow — they'll be here.*"
                ),
                color=COLORS["info"]
            ))

        # Build NPC beast state scaled to player level
        NPC_SCALING = {
            "maren":         (0.80, "defensive"),
            "cael":          (1.00, "chaotic"),
            "sable":         (1.10, "aggressive"),
            "orren":         (1.20, "patient"),
            "the_archivist": (1.50, "optimal"),
        }
        scale, personality = NPC_SCALING[npc_name]
        npc_level = max(1, min(50, int(active_row["level"] * scale)))

        all_beasts = json.load(open("data/beasts.json"))["beasts"]
        companion_id = npc["beast_companion"]
        companion_data = all_beasts.get(companion_id, {})
        if not companion_data:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Companion data missing.", color=COLORS["error"]
            ))

        enemy_state = build_pve_beast_state(companion_data, npc_level)
        active_beast_data = get_beast_data(active_row["beast_id"]) or {}

        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1",
                (interaction.user.id,)
            ) as c:
                player_perks = [dict(r) for r in await c.fetchall()]

        npc_emoji = npc.get("emoji", "🐾")
        await interaction.followup.send(embed=discord.Embed(
            title=f"{npc_emoji} Sparring with {npc['name']}",
            description=(
                f"*{npc.get('first_meeting', '').split('*')[1] if '*' in npc.get('first_meeting','') else ''}*\n\n"
                f"**{npc['name']}** sends out **{companion_data['name']}** (Lv.{npc_level})!\n"
                f"*{companion_data.get('description','')}*"
            ),
            color=COLORS["info"]
        ))

        # Win/loss dialogue per NPC — pulled from their established voice
        NPC_WIN_LINES = {
            "maren":        "Maren nods slowly. *'Good. Barkley needed the exercise. More importantly — you needed to know you could.'*",
            "cael":         "Cael blinks. Twine blinks. *'I didn't see that coming. I see everything coming. That's — that's interesting.'*",
            "sable":        "*'Hm.'* Sable picks up her tools again. *'You earned that.'*",
            "orren":        "Orren is quiet for a moment. *'The woods noticed. They notice everything eventually.'*",
            "the_archivist":"*'As expected. I've had this conversation forty-eight times. This is the first time the outcome was uncertain.'*",
        }
        NPC_LOSS_LINES = {
            "maren":        "*'That's alright,'* says Maren. *'Barkley's been here since before most things were named. Try again tomorrow.'*",
            "cael":         "*'That was — actually I saw that coming. Sorry. Twine told me not to say anything.'*",
            "sable":        "*'Try harder.'* She's already back at the forge.",
            "orren":        "*'The forest doesn't judge losing. It just keeps growing. Come back when you're ready.'*",
            "the_archivist":"*'The outcome is noted. Several of the other versions of this conversation went the same way. You'll figure it out.'*",
        }

        async def on_win(embed, p_state, e_state, timed_out=False):
            if timed_out:
                # Timeout on a spar: small gold reward, no shard, no relationship upgrade.
                # The NPC wasn't actually bested — just outlasted.
                partial_gold = random.randint(30, 80)
                await update_player(interaction.user.id, gold=player["gold"] + partial_gold)
                _lvl, _, _leveled = await award_player_exp(interaction.user.id, random.randint(active_row["level"] * 8, active_row["level"] * 12))
                NPC_TIMEOUT_LINES = {
                    "maren":         "*'A draw,'* says Maren. *'Barkley doesn't mind. Come back when you can end it properly.'*",
                    "cael":          "*'Twenty turns and neither of us won. Twine predicted this. I thought it was being dramatic.'*",
                    "sable":         "*'Neither of us finished it.'* She goes back to her forge. *'Try again tomorrow.'*",
                    "orren":         "*'The woods have seen longer standoffs,'* Orren says. *'Neither of us blinked. That\\'s something.'*",
                    "the_archivist": "*'A stalemate. I\\'ve had forty-eight of these conversations. Six ended this way. The outcome is still open.'*",
                }
                embed.add_field(
                    name="⏱️ Time Limit",
                    value=(
                        f"+**{partial_gold} gold** 💰 | +**15–30 EXP** ✨"
                        + (f"\n⬆️ **Trainer leveled up to {_lvl}!**" if _leveled else "")
                        + f"\n{NPC_TIMEOUT_LINES.get(npc_name, '*The spar ended on time.*')}"
                    ),
                    inline=False
                )
                return

            gold_gain = random.randint(100, 300)
            # All writes for a spar win in one connection — the old code opened
            # a connection here then called save_quest_state() inside it, which
            # opens another connection, causing "database is locked" errors.
            from cogs.questline import get_quest_state, RELATIONSHIP_LEVELS
            import json as _json
            qs = await get_quest_state(interaction.user.id)
            current_rel = qs["npc_relationships"].get(npc_name, "stranger")
            levels = list(RELATIONSHIP_LEVELS.keys())
            rel_advanced = False
            next_rel = current_rel
            if levels.index(current_rel) < len(levels) - 1:
                next_rel = levels[levels.index(current_rel) + 1]
                qs["npc_relationships"][npc_name] = next_rel
                rel_advanced = True

            async with aiosqlite.connect("db/chibibeast.db") as db:
                # Gold + shard in one statement
                await db.execute(
                    "UPDATE players SET gold = gold + ?, celestial_shards = celestial_shards + 1 WHERE user_id = ?",
                    (gold_gain, interaction.user.id)
                )
                # Relationship advancement written directly — same connection
                if rel_advanced:
                    await db.execute(
                        """INSERT INTO player_questline
                               (user_id, npc_relationships, last_updated)
                           VALUES (?, ?, datetime('now'))
                           ON CONFLICT(user_id) DO UPDATE SET
                               npc_relationships = excluded.npc_relationships,
                               last_updated      = excluded.last_updated""",
                        (interaction.user.id, _json.dumps(qs["npc_relationships"]))
                    )
                await db.commit()

            # Player EXP (outside the DB block — uses update_player which opens its own conn)
            spar_exp = random.randint(active_row["level"] * 12, active_row["level"] * 20)
            _p_lvl, _, _p_leveled = await award_player_exp(interaction.user.id, spar_exp)

            if rel_advanced:
                embed.add_field(
                    name="💬 Relationship improved!",
                    value=f"Your bond with **{npc['name']}** is now *{next_rel.capitalize()}*.",
                    inline=False
                )
            embed.add_field(
                name="🎁 Rewards",
                value=(
                    f"+**{gold_gain:,} gold** 💰 | +**1 🔮 Celestial Shard** | +**{spar_exp} EXP** ✨"
                    + (f"\n⬆️ **Trainer leveled up to {_p_lvl}!**" if _p_leveled else "")
                    + f"\n\n{NPC_WIN_LINES[npc_name]}"
                ),
                inline=False
            )
            unlocked = await check_achievements(interaction.user.id)
            if unlocked:
                await notify_unlocks(interaction.channel, interaction.user, unlocked)

        async def on_loss(embed, p_state, e_state):
            loss_exp = random.randint(active_row["level"] * 5, active_row["level"] * 8)
            await update_player(interaction.user.id, gold=player["gold"] + 50)
            await award_player_exp(interaction.user.id, loss_exp)
            embed.add_field(
                name="💤 Lesson",
                value=f"+**50 gold** 💰 | +**{loss_exp} EXP** ✨\n\n{NPC_LOSS_LINES[npc_name]}",
                inline=False
            )

        # Atomically claim today's spar slot. INSERT OR IGNORE reports
        # rowcount 1 only when the row is newly written; 0 means a spar with
        # this NPC was already recorded today — which closes the race where
        # two near-simultaneous /sparr calls both passed the read check above
        # before either had finished.
        async with aiosqlite.connect("db/chibibeast.db") as db:
            cur = await db.execute(
                "INSERT OR IGNORE INTO daily_quests (user_id, quest_id, progress, completed, date) VALUES (?,?,1,1,?)",
                (interaction.user.id, spar_key, today)
            )
            await db.commit()
            claimed = cur.rowcount == 1

        if not claimed:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You've already sparred with **{npc['name']}** today.",
                color=COLORS["info"]
            ))

        try:
            await run_pve_battle(
                interaction,
                dict(active_row),
                active_beast_data,
                player_perks,
                enemy_state,
                enemy_personality=personality,
                battle_title=f"Spar with {npc['name']}",
                on_win=on_win,
                on_loss=on_loss,
            )
        except Exception:
            # Release the slot so a failed spar never burns the player's daily
            # attempt. The global handler in bot.py still reports the error.
            async with aiosqlite.connect("db/chibibeast.db") as db:
                await db.execute(
                    "DELETE FROM daily_quests WHERE user_id = ? AND quest_id = ? AND date = ?",
                    (interaction.user.id, spar_key, today)
                )
                await db.commit()
            raise

    @app_commands.command(name="battle", description="Challenge another trainer to a beast battle! ⚔️")
    @app_commands.describe(opponent="The trainer you want to challenge")
    async def battle(self, interaction: discord.Interaction, opponent: discord.Member):
        await interaction.response.defer()

        if opponent.id == interaction.user.id:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You can't battle yourself!", color=COLORS["error"]
            ))
        if opponent.bot:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You can't battle a bot!", color=COLORS["error"]
            ))

        challenger_beast = await get_active_beast(interaction.user.id)
        opponent_beast = await get_active_beast(opponent.id)

        if not challenger_beast:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You don't have an active beast! Use `/hatch` or `/explore` first.",
                color=COLORS["error"]
            ))
        if not opponent_beast:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ **{opponent.display_name}** doesn't have an active beast!",
                color=COLORS["error"]
            ))

        challenger_data = get_beast_data(challenger_beast["beast_id"])
        opponent_data = get_beast_data(opponent_beast["beast_id"])

        battle_id = interaction.id
        pending_battles[battle_id] = {
            "challenger_id": interaction.user.id,
            "opponent_id": opponent.id
        }

        embed = discord.Embed(
            title="⚔️ Battle Challenge!",
            description=(
                f"**{interaction.user.display_name}** challenges **{opponent.display_name}** to a battle!\n\n"
                f"🐾 **{interaction.user.display_name}'s** {challenger_data['name']} (Lv.{challenger_beast['level']})\n"
                f"vs\n"
                f"🐾 **{opponent.display_name}'s** {opponent_data['name']} (Lv.{opponent_beast['level']})\n\n"
                f"*{opponent.mention}, do you accept?*"
            ),
            color=COLORS["epic"]
        )
        embed.set_footer(text="Challenge expires in 90 seconds")
        view = BattleView(battle_id, interaction.user.id, opponent.id)
        view.message = await interaction.followup.send(embed=embed, view=view)

async def setup(bot: commands.Bot):
    await bot.add_cog(Battle(bot))
