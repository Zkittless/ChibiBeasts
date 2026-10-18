from utils import db as storage
from utils.db import credit_gold
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
    apply_beast_levelup, calc_exp_for_level, get_beast_exp_for_level, load_beasts
)
from utils.theme import COLORS, RARITY_EMOJI, TYPE_EMOJI, hp_bar, SPARKLE
from utils.progress import (
    track_quest_event, check_achievements, notify_unlocks, notify_quest_completions
)
from utils.type_chart import get_type_multiplier, type_effectiveness_label

pending_battles: dict[int, dict] = {}

# ── PvE AI Engine ─────────────────────────────────────────────────────────────


def build_pve_beast_state(beast_data,level):
    from utils.combat import build_state
    from utils.db import calc_stat_growth
    growth=calc_stat_growth({"rarity":beast_data["rarity"]},max(0,level-1))
    stats={k:v+growth[k] for k,v in beast_data["base_stats"].items()}
    row={**stats,"max_hp":stats["hp"],"max_mana":stats["mana"],"level":level,"happiness":70}
    return build_state(beast_data,row,buffs={"ultimate_charges":0})


# ── Level milestone definitions ──────────────────────────────────────────
LEVEL_MILESTONES = {
    3: {
        "title": "The Loom Notices You", "emoji": "🌱",
        "body": (
            "*You have proven you can survive out here. That is not nothing.*\n\n"
            "*Somewhere in the Whispering Woods, an old woman named **Maren** has heard about you. "
            "She keeps the First Bestiary — a record of every beast ever caught, "
            "going back before anyone can remember. She wants to meet you.*\n\n"
            "**Open `/journal` → Story to begin your story.**\n"
            "*The Loom has been waiting for you to be ready.*"
        ),
        "reward_gold": 500, "reward_shards": 5, "color": "legendary",
    },
    5: {
        "title": "Lv.5 — Getting Dangerous", "emoji": "⚔️",
        "body": (
            "*Your beasts are stronger. You are learning how to read a fight.*\n\n"
            "*The Glacial Hollows are now accessible — ice-type territory, tougher encounters, "
            "better rewards. Sable the Forger works there. "
            "She does not take meetings, but she takes notice of trainers who can handle the heat.*"
        ),
        "reward_gold": 800, "reward_shards": 8, "color": "epic",
    },
    10: {
        "title": "Lv.10 — A Real Trainer", "emoji": "🔥",
        "body": (
            "*Ten levels. Most people do not make it this far.*\n\n"
            "*Guild Raids are now available — join a guild and take on Corrupted bosses "
            "with other trainers. The rewards are unlike anything you find in the wild.\n\n"
            "The Celestial Loom has opened. Cosmic and arcane companions live here. "
            "Whatever lives there has been waiting a long time for someone worth fighting.*"
        ),
        "reward_gold": 2000, "reward_shards": 15, "color": "rare",
    },
    15: {
        "title": "Lv.15 — The Depths Call", "emoji": "🌊",
        "body": (
            "*Your starter can awaken at beast level 15 with 30 bond and 70 happiness. "
            "The beasts down there are old and strange and difficult.*\n\n"
            "*Cael the Loom-Watcher has been watching your progress. "
            "He has questions only you can answer now.*"
        ),
        "reward_gold": 3500, "reward_shards": 20, "color": "epic",
    },
    25: {
        "title": "Lv.25 — A Spark to Carry", "emoji": "🌌",
        "body": (
            "*Genesis Spark is now yours to equip. Start battles with more mana and empower your ultimate.*\n\n"
            "*Legendary and Divine beasts inhabit this place. The Archivist — the keeper of "
            "everything that has ever happened — has requested your presence. "
            "There are still companions to meet and builds to explore.*\n\n"
            "*Whatever you find there will change what you understand about the Loom.*"
        ),
        "reward_gold": 10000, "reward_shards": 50, "color": "divine",
    },
    50: {
        "title": "Lv.50 — The Weave Complete", "emoji": "✨",
        "body": (
            "*You have reached the end of what the Loom has measured so far.*\n\n"
            "*This is not the end. The Loom is still weaving. More will come.*\n\n"
            "*For now — you have caught beasts, fought battles, built bonds, and followed "
            "a thread that started with a single choice in the Whispering Woods.*\n\n"
            "*Maren would say something wise here. She probably already did.*"
        ),
        "reward_gold": 50000, "reward_shards": 100, "color": "divine",
    },
}


async def notify_level_up(
    interaction: discord.Interaction,
    old_level: int,
    new_level: int,
    user_id: int,
) -> None:
    """Send a level-up announcement embed. Shows milestones with rewards."""
    from utils.db import get_player, update_player
    from utils.theme import COLORS

    channel = interaction.channel
    if not channel:
        return

    for lvl in range(old_level + 1, new_level + 1):
        milestone = LEVEL_MILESTONES.get(lvl)

        if milestone:
            embed = discord.Embed(
                title=f"{milestone['emoji']} {milestone['title']}",
                description=(
                    f"**Trainer Level {lvl}!**\n\n"
                    + milestone["body"]
                    + f"\n\n🎁 **Milestone Rewards:** `+{milestone['reward_gold']:,}g` · `+{milestone['reward_shards']} 🔮`"
                ),
                color=COLORS.get(milestone["color"], COLORS["legendary"])
            )
            embed.set_footer(text="ChibiBeasts 🐾  •  Use /profile to see your progress")
            await channel.send(f"<@{user_id}>", embed=embed)
        else:
            # Regular level up — clean simple embed
            embed = discord.Embed(
                title=f"⬆️ Trainer Level {lvl}!",
                description=(
                    "*The Loom acknowledges your growth.*\n\n"
                    "Your beasts grow stronger alongside you.\n"
                    "Check `/profile` for your updated stats."
                ),
                color=COLORS["success"]
            )
            await channel.send(f"<@{user_id}>", embed=embed)


async def award_player_exp(user_id: int, exp_gain: int, interaction=None):
    from utils.rewards import player_exp
    async with storage.connect() as db:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute("SELECT level FROM players WHERE user_id=?",(user_id,)) as c:old=await c.fetchone()
        async with db.execute("SELECT 1 FROM guild_members m JOIN guild_sanctuary s ON s.guild_id=m.guild_id WHERE m.user_id=? AND s.arcane_library=1",(user_id,)) as c:
            if await c.fetchone():exp_gain=round(exp_gain*1.15)
        result=await player_exp(db,user_id,exp_gain)
        await db.commit()
    if result[2]:
        from utils.story import advance_quest_step
        await advance_quest_step(user_id,"level_up",level=result[0])
        if interaction:await notify_level_up(interaction,old[0],result[0],user_id)
    return result


async def run_pve_battle(interaction,player_beast_row,player_beast_data,player_perks,enemy_state,enemy_personality,battle_title,on_win,on_loss,*,reserved=False,realm=None):
    from utils.duels import reserve_fighters
    from contextlib import nullcontext
    with nullcontext() if reserved else reserve_fighters([interaction.user.id]):
        return await _run_pve_battle(interaction,player_beast_row,player_beast_data,player_perks,enemy_state,enemy_personality,battle_title,on_win,on_loss,realm=realm)

async def _run_pve_battle(interaction,player_beast_row,player_beast_data,player_perks,enemy_state,enemy_personality,battle_title,on_win,on_loss,*,realm=None):
    from utils.duels import prepare_state,duel,beast_exp,active_fighters
    from utils.db import is_knocked_out
    if is_knocked_out(player_beast_row):raise ValueError("Your companion is recovering. Use /use Phoenix Elixir or choose another beast.")
    state=await prepare_state(interaction.user.id,player_beast_row,player_beast_data,player_perks)
    winner,states,log,timed_out=await duel(interaction,[state,enemy_state],[interaction.user.id,None],battle_title,enemy_personality,MoveView,reserved=True,realm=realm)
    # The closing battlefield already contains final HP and its journal. This
    # follow-up carries the outcome, rewards and story instead of repeating it.
    embed=discord.Embed(title=f"⚔️ {battle_title} — {'Victory' if winner==0 else 'Draw' if winner is None else 'Lesson learned'}",color=COLORS["success"] if winner==0 else COLORS["info"])
    if timed_out:embed.add_field(name="Time limit",value="The match ended on time. Reduced rewards; wild creatures are not caught on a time-limit win.",inline=False)
    if winner==0:await on_win(embed,*states,timed_out)
    else:await on_loss(embed,*states)
    kind="sparr" if battle_title.startswith("Spar") else "pve"
    async with storage.connect() as db:
        cursor=await db.execute("INSERT INTO battles(challenger_id,winner_id,challenger_beast,status,battle_type) VALUES(?,?,?,'completed',?)",(interaction.user.id,interaction.user.id if winner==0 else None,player_beast_row["id"],kind))
        battle_id=cursor.lastrowid
        if winner!=0:await db.execute("UPDATE players SET losses=losses+1 WHERE user_id=?",(interaction.user.id,))
        await db.commit()
    if enemy_personality!="wild":await beast_exp(interaction.user.id,player_beast_row["id"],max(15,enemy_state.get("level",1)*8)*(2 if winner==0 else 1))
    if winner==0:
        completed=await track_quest_event(interaction.user.id,"spar_win" if kind=="sparr" else "battle_win",event_key=f"duel:{battle_id}",beast_row_id=player_beast_row["id"])
        await notify_quest_completions(interaction.channel,completed)
    await interaction.channel.send(embed=embed)


# ── Divine Passive Processor ──────────────────────────────────────────────────


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
        self.handled = False

    def _disable_all(self):
        for item in self.children:
            item.disabled = True

    async def on_timeout(self):
        # Without this, an expired challenge silently leaves clickable-looking
        # buttons that do nothing. Now it visibly closes the challenge.
        self.handled = True
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

        if self.handled:
            return await interaction.response.send_message('This challenge is already closed.', ephemeral=True)
        self.handled = True
        await interaction.response.defer()
        self.stop()
        self._disable_all()
        try:
            async with asyncio.timeout(DUEL_IO_TIMEOUT):
                await interaction.edit_original_response(view=self)
            if self.battle_id not in pending_battles:
                return await interaction.followup.send('This challenge is no longer active. Use /battle to start a new one.', ephemeral=True)
            # start_battle reserves both trainers before reading fresh companions.
            # Avoid duplicate lookups here and report expected busy/recovery errors.
            await start_battle(interaction, self.battle_id)
        except ValueError as error:
            await interaction.followup.send(str(error), ephemeral=True)
        finally:
            pending_battles.pop(self.battle_id, None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent_id:
            return await interaction.response.send_message("This challenge isn't for you!", ephemeral=True)
        if self.handled:
            return await interaction.response.send_message('This challenge is already closed.', ephemeral=True)
        self.handled = True
        self.stop()
        pending_battles.pop(self.battle_id, None)
        self._disable_all()
        await interaction.response.edit_message(
            embed=discord.Embed(description="❌ Battle challenge declined.", color=COLORS["error"]),
            view=self
        )

from utils.duels import MoveView, DUEL_IO_TIMEOUT


async def start_battle(interaction,battle_id):
    pending=pending_battles.pop(battle_id,None)
    if not pending:return None
    from utils.duels import reserve_fighters
    ids=[pending["challenger_id"],pending["opponent_id"]]
    with reserve_fighters(ids):
        return await _start_battle(interaction,battle_id,pending,ids)

async def _start_battle(interaction,battle_id,pending,ids):
    from utils.duels import prepare_state,duel,beast_exp
    from utils.db import credit_gold
    from utils.db import is_knocked_out
    states=[];rows=[]
    candidates=[await get_active_beast(uid) for uid in ids]
    if any(not row or is_knocked_out(row) for row in candidates):
        raise ValueError("Both trainers need a healthy active companion.")
    for uid,row in zip(ids,candidates):
        data=get_beast_data(row["beast_id"]);rows.append(row)
        async with storage.connect() as db:
            db.row_factory=aiosqlite.Row
            async with db.execute("SELECT * FROM player_perks WHERE user_id=? AND equipped=1",(uid,)) as c:perks=[dict(r) for r in await c.fetchall()]
        states.append(await prepare_state(uid,row,data,perks))
    winner,states,log,timed_out=await duel(interaction,states,ids,"Ranked" if pending.get("ranked") else "Trainer battle",view_class=MoveView,reserved=True)
    winner_id=ids[winner] if winner is not None else None
    async with storage.connect() as db:
        await db.execute("INSERT INTO battles(challenger_id,opponent_id,winner_id,challenger_beast,opponent_beast,status,battle_type) VALUES(?,?,?,?,?,'completed',?)",(*ids,winner_id,rows[0]["id"],rows[1]["id"],"ranked" if pending.get("ranked") else "pvp"))
        for uid in ids:await db.execute("UPDATE players SET ultimate_charges=MIN(3,ultimate_charges+1) WHERE user_id=?",(uid,))
        if winner_id:await db.execute("UPDATE players SET losses=losses+1 WHERE user_id=?",(ids[1-winner],))
        await db.commit()
    for i,uid in enumerate(ids):
        await credit_gold(uid,100 if uid==winner_id else 25)
        await award_player_exp(uid,80 if uid==winner_id else 25)
        await beast_exp(uid,rows[i]["id"],60 if uid==winner_id else 20)
    if winner_id:
        completed=await track_quest_event(winner_id,"pvp_win",event_key=f"pvp:{battle_id}",beast_row_id=rows[winner]["id"])
        await notify_quest_completions(interaction.channel,completed)
    embed=discord.Embed(title="⚔️ Battle complete",description=(f"<@{winner_id}> and **{states[winner]['name']}** win." if winner_id else "Both companions finish together. A draw."),color=COLORS["epic"])
    await interaction.channel.send(embed=embed)
    return winner_id

class Battle(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /challenge ────────────────────────────────────────────────────────
    @app_commands.command(name="challenge", description="Battle a wild beast in a biome! 🌿")
    @app_commands.describe(biome="Which biome to challenge a wild beast from")
    @app_commands.choices(biome=[
        app_commands.Choice(name="🌲 Whispering Woods (Lv1+)",          value="woods"),
        app_commands.Choice(name="🔥 Ember Wastes (Lv3+)",             value="ember"),
        app_commands.Choice(name="❄️ Glacial Hollows (Lv5+)",          value="glacial"),
        app_commands.Choice(name="🌊 Sunken Abyssal Trenches (Lv8+)",  value="trenches"),
        app_commands.Choice(name="🌌 Celestial Loom (Lv10+)",           value="loom"),
    ])
    async def challenge(self, interaction: discord.Interaction, biome: str = "woods"):
        await interaction.response.defer()
        from utils.duels import reserve_fighters
        with reserve_fighters([interaction.user.id]):
            return await self._challenge(interaction, biome)

    async def _challenge(self, interaction, biome):
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        active_row = await get_active_beast(interaction.user.id)
        if not active_row:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need an active beast to battle! Open `/journal` → Companions → Make active.",
                color=COLORS["error"]
            ))

        BIOME_GATES = {"woods": 1, "ember": 3, "glacial": 5, "trenches": 8, "loom": 10}
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

        # 20 minute cooldown
        import time as _t
        _now = _t.time()
        _cd_last = player.get("challenge_last_at", 0) or 0
        async with storage.connect() as db:
            from utils.rewards import cooldown
            challenge_cooldown=await cooldown(db,interaction.user.id,1200)
        _remaining = challenge_cooldown - (_now - _cd_last)
        if _remaining > 0:
            _m, _s = divmod(int(_remaining), 60)
            return await interaction.followup.send(embed=discord.Embed(
                description=f"⏳ The wild needs time to settle. Ready in **{_m}m {_s}s**.",
                color=COLORS["info"]
            ))

        from utils.habitats import BIOMES
        if player["level"]<BIOMES[biome]["min_level"]:
            return await interaction.followup.send(f"Reach trainer level {BIOMES[biome]['min_level']} first.")
        from utils.db import is_knocked_out
        if is_knocked_out(active_row):return await interaction.followup.send("Your companion is recovering. Select another or use a Phoenix Elixir.")
        async with storage.connect() as db:
            cur=await db.execute("UPDATE players SET challenge_last_at=? WHERE user_id=? AND challenge_last_at<=?",(_now,interaction.user.id,_now-challenge_cooldown))
            if not cur.rowcount:return await interaction.followup.send("A challenge is already underway. Try again after the cooldown.")
            await db.commit()

        pool_weights = dict(BIOMES[biome]["weights"])
        from utils.habitats import challenge_weights
        _biased_weights=challenge_weights(pool_weights,active_row.get('rarity','common'))

        from utils.habitats import pick_encounter
        wild_beast_data=pick_encounter(_biased_weights,biome)
        if not wild_beast_data:return await interaction.followup.send("No creature is available in that habitat yet.")
        rarity=wild_beast_data["rarity"]

        # Scale wild beast to player's active beast level ±2
        player_beast_level = active_row["level"]
        wild_level = max(1, min(50, player_beast_level + random.randint(-2, 2)))
        enemy_state = build_pve_beast_state(wild_beast_data, wild_level)

        active_beast_data = get_beast_data(active_row["beast_id"]) or {}
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1",
                (interaction.user.id,)
            ) as c:
                player_perks = [dict(r) for r in await c.fetchall()]

        rarity_emoji = RARITY_EMOJI.get(rarity, "⚪")
        _challenge_intro = discord.Embed(
            title=f"⚔️ Wild Battle! {BIOME_NAMES[biome]}",
            description=(
                f"A wild **{rarity_emoji} {wild_beast_data['name']}** (Lv.{wild_level}) "
                f"appeared and wants to fight!\n\n"
                f"*{wild_beast_data['description']}*"
            ),
            color=COLORS.get(rarity, COLORS["info"])
        )
        if wild_beast_data.get("image_url"):
            _challenge_intro.set_image(url=wild_beast_data["image_url"])
        from utils import gear_art
        armor = await gear_art.equipped_armor_ids(interaction.user.id)
        await gear_art.send_owned_portrait(interaction, _challenge_intro, active_row['beast_id'], armor.get(active_row['id'], ''), thumbnail=True)

        # Reward callbacks
        async def on_win(embed, p_state, e_state, timed_out=False):
            if timed_out:
                # Timeout win: reduced rewards, no catch chance.
                # The enemy beast wasn't defeated — just outlasted on HP.
                # Full rewards here would make stall tactics profitable.
                from utils.economy_balance import gold_reward_level
                gold_level=gold_reward_level(active_row["level"])
                consolation_gold = random.randint(gold_level * 2, gold_level * 4)
                await credit_gold(interaction.user.id, consolation_gold)
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

            from utils.economy_balance import gold_reward_level
            gold_level=gold_reward_level(active_row["level"])
            gold_gain = random.randint(gold_level * 8, gold_level * 15)
            # Beast EXP scales with enemy level and rarity — harder fights reward more
            _RARITY_EXP_MULT = {"common":1,"uncommon":1.5,"rare":2.5,"epic":4,"legendary":6,"divine":10}
            _exp_mult = _RARITY_EXP_MULT.get(rarity, 1)
            beast_exp  = int(random.randint(20, 40) * _exp_mult + wild_level * 1.5)

            from utils.duels import beast_exp as grant_beast_exp
            new_level=await grant_beast_exp(interaction.user.id,active_row["id"],beast_exp)

            await credit_gold(interaction.user.id, gold_gain)

            # Player EXP — scales with biome difficulty
            player_exp_gain = random.randint(active_row["level"] * 15, active_row["level"] * 25)
            _p_lvl, _, _p_leveled = await award_player_exp(interaction.user.id, player_exp_gain)

            if player.get("catch_boost_charges",0)>0:
                async with storage.connect() as db:
                    await db.execute("UPDATE players SET catch_boost_charges=MAX(0,catch_boost_charges-1) WHERE user_id=?",(interaction.user.id,));await db.commit()
            # Catch chance
            CATCH_RATES = {"common": 0.50, "uncommon": 0.35, "rare": 0.20,
                          "epic": 0.10, "legendary": 0.05, "divine": 0.02}
            pixie_bonus=.05 if any(p['perk_id']=='pixie_pocket' and p['equipped'] for p in player_perks) else 0
            caught = random.random() < min(.95,CATCH_RATES.get(rarity,.10)+pixie_bonus+(.15 if player.get("catch_boost_charges",0)>0 else 0))

            reward_text = (
                f"+**{gold_gain:,} gold** 💰 | +**{beast_exp} beast EXP** | +**{player_exp_gain} EXP** ✨"
                + (f"\n⬆️ **{active_beast_data.get('name','')} leveled up to Lv.{new_level}!**"
                   if new_level > active_row["level"] else "")
                + (f"\n⬆️ **Trainer leveled up to {_p_lvl}!**" if _p_leveled else "")
            )

            if caught:
                from utils.db import add_beast_to_player
                from utils.progress import record_bestiary_sighting
                await add_beast_to_player(interaction.user.id, {
                    **wild_beast_data, "caught_from": "wild_battle", "level": wild_level
                })
                if interaction.guild:
                    await record_bestiary_sighting(
                        interaction.guild.id, wild_beast_data["id"], interaction.user.id
                    )
                reward_text += f"\n🎉 **{wild_beast_data['name']} was caught!** Check `/collection`."

            embed.add_field(name="🎁 Rewards", value=reward_text, inline=False)
            # Duel completion records the canonical win event.
            async with storage.connect() as _udb:
                await _udb.execute("UPDATE players SET ultimate_charges = MIN(3, ultimate_charges + 1) WHERE user_id = ?", (interaction.user.id,))
                await _udb.commit()
            unlocked = await check_achievements(interaction.user.id)
            if unlocked:
                await notify_unlocks(interaction.channel, interaction.user, unlocked)

        async def on_loss(embed, p_state, e_state):
            async with storage.connect() as _udb:
                await _udb.execute("UPDATE players SET ultimate_charges = MIN(3, ultimate_charges + 1) WHERE user_id = ?", (interaction.user.id,))
                await _udb.commit()
            consolation_gold = random.randint(10, 25)
            consolation_exp  = random.randint(active_row["level"] * 5, active_row["level"] * 10)
            await credit_gold(interaction.user.id, consolation_gold)
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
            realm=biome,
            on_win=on_win,
            on_loss=on_loss,
            reserved=True,
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
        from utils.duels import reserve_fighters
        with reserve_fighters([interaction.user.id]):
            return await self._sparr(interaction, npc_name)

    async def _sparr(self, interaction, npc_name):
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        active_row = await get_active_beast(interaction.user.id)
        if not active_row:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need an active beast! Open `/journal` → Companions → Make active.",
                color=COLORS["error"]
            ))

        from utils.catalog import load_catalog
        npcs = load_catalog("npcs.json")["npcs"]
        npc  = npcs.get(npc_name)
        if not npc:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ NPC not found.", color=COLORS["error"]
            ))

        # Archivist locked until Chapter 5 complete
        if npc_name == "the_archivist":
            from utils.story import get_quest_state
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

        async with storage.connect() as db:
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

        all_beasts = load_beasts()
        companion_id = npc["beast_companion"]
        companion_data = all_beasts.get(companion_id, {})
        if not companion_data:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Companion data missing.", color=COLORS["error"]
            ))

        enemy_state = build_pve_beast_state(companion_data, npc_level)
        active_beast_data = get_beast_data(active_row["beast_id"]) or {}

        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM player_perks WHERE user_id = ? AND equipped = 1",
                (interaction.user.id,)
            ) as c:
                player_perks = [dict(r) for r in await c.fetchall()]

        npc_emoji = npc.get("emoji", "🐾")
        _sparr_intro = discord.Embed(
            title=f"{npc_emoji} Sparring with {npc['name']}",
            description=(
                f"*{npc.get('first_meeting', '').split('*')[1] if '*' in npc.get('first_meeting','') else ''}*\n\n"
                f"**{npc['name']}** sends out **{companion_data['name']}** (Lv.{npc_level})!\n"
                f"*{companion_data.get('description','')}*"
            ),
            color=COLORS["info"]
        )
        if companion_data.get("image_url"):
            _sparr_intro.set_image(url=companion_data["image_url"])
        from utils import gear_art
        armor = await gear_art.equipped_armor_ids(interaction.user.id)
        await gear_art.send_owned_portrait(interaction, _sparr_intro, active_row['beast_id'], armor.get(active_row['id'], ''), thumbnail=True)

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

        async def settle_spar_gold(amount):
            # Mark the daily attempt as paid in the SAME transaction. If a
            # later notification fails, retrying must not award the gold again.
            async with storage.connect() as db:
                await db.execute('BEGIN IMMEDIATE')
                await db.execute('UPDATE players SET gold=gold+?,total_gold_earned=total_gold_earned+? WHERE user_id=?', (amount, amount, interaction.user.id))
                await db.execute('UPDATE daily_quests SET progress=2 WHERE user_id=? AND quest_id=? AND date=?', (interaction.user.id, spar_key, today))
                await db.commit()

        async def on_win(embed, p_state, e_state, timed_out=False):
            if timed_out:
                # Timeout on a spar: small gold reward, no shard, no relationship upgrade.
                # The NPC wasn't actually bested — just outlasted.
                partial_gold = random.randint(30, 80)
                await settle_spar_gold(partial_gold)
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
            from utils.story import RELATIONSHIP_LEVELS, relationship_order
            async with storage.connect() as db:
                await db.execute("BEGIN IMMEDIATE")
                # Read and advance inside the reward transaction. Preserve
                # other NPCs and any story progress made during the spar.
                async with db.execute("SELECT npc_relationships FROM player_questline WHERE user_id=?", (interaction.user.id,)) as c:
                    latest = await c.fetchone()
                rels = json.loads(latest[0] or "{}") if latest else {}
                current_rel = rels.get(npc_name, "stranger")
                levels = list(RELATIONSHIP_LEVELS)
                next_rel = levels[min(relationship_order(current_rel) + 1, len(levels) - 1)]
                rel_advanced = next_rel != current_rel
                rels[npc_name] = next_rel
                from utils.rewards import shards
                await shards(db,interaction.user.id,1)
                # Gold and charge share the reward transaction.
                await db.execute(
                    "UPDATE players SET gold = gold + ?, ultimate_charges = MIN(3, ultimate_charges + 1) WHERE user_id = ?",
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
                        (interaction.user.id, json.dumps(rels))
                    )
                await db.execute('UPDATE daily_quests SET progress=2 WHERE user_id=? AND quest_id=? AND date=?', (interaction.user.id, spar_key, today))
                await db.commit()

            # Player EXP (outside the DB block — uses update_player which opens its own conn)
            spar_exp = random.randint(active_row["level"] * 12, active_row["level"] * 20)
            _p_lvl, _, _p_leveled = await award_player_exp(interaction.user.id, spar_exp, interaction)

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
            await settle_spar_gold(50)
            await award_player_exp(interaction.user.id, loss_exp)
            async with storage.connect() as _udb:
                await _udb.execute("UPDATE players SET ultimate_charges = MIN(3, ultimate_charges + 1) WHERE user_id = ?", (interaction.user.id,))
                await _udb.commit()
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
        async with storage.connect() as db:
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
                realm=npc['location'],
                on_win=on_win,
                on_loss=on_loss,
                reserved=True,
            )
        except BaseException:
            # Release an unpaid slot on errors AND cancellation. Paid attempts
            # remain claimed even when a later message or quest callback fails.
            async with storage.connect() as db:
                await db.execute(
                    "DELETE FROM daily_quests WHERE user_id = ? AND quest_id = ? AND date = ? AND progress=1",
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
