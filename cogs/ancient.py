"""
Ancient Raid System — ChibiBeasts

Ancient bosses are world-level threats. Unlike corrupted guild raids, anyone
can trigger or join an ancient encounter. No guild membership required.

Flow:
  1. /ancient         — any Lv10+ player spends 25 Celestial Shards to open a lobby
  2. Lobby embed posts with a Join button — anyone in the server can click it
  3. After 60 seconds (or when full at 10 players) the raid begins automatically
  4. /ancient_attack  — party members deal damage, tracked separately from guild raids
  5. On defeat — top 3 get loot + catch chance for the boss's Ancient form
                  (10% rank 1, 6% rank 2, 3% rank 3)
"""

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
import asyncio
from utils.db import get_or_create_player, get_player, update_player, get_beast_data, get_active_beast
from utils.theme import COLORS, RARITY_EMOJI, hp_bar
from utils.progress import track_quest_event, unlock_simple_achievement, notify_unlocks, check_achievements
from cogs.questline import advance_quest_step

DB_PATH = "db/chibibeast.db"

ANCIENT_BOSSES = [
    {"id": "ancient_chronos", "name": "Ancient Chronos", "type": "ancient",
     "max_hp": 150000, "attack": 2000, "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/ancient_chronos.png",
     "description": "The primordial form of Chronos, existing before time itself had a name. It doesn't govern time. It is time.",
     "loot_table": ["tear_of_leviathan", "genesis_fruit", "ambrosia_tart", "sunforge_core"]},
    {"id": "ancient_genesis", "name": "Ancient Genesis", "type": "ancient",
     "max_hp": 160000, "attack": 2200, "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/ancient_genesis.png",
     "description": "The original Origin Phoenix, carrying the flame of the universe's first moment. Everything alive exists because this beast once burned.",
     "loot_table": ["genesis_fruit", "singularity_soda", "tear_of_leviathan"]},
    {"id": "ancient_abyss", "name": "Ancient Abyss", "type": "ancient",
     "max_hp": 140000, "attack": 2100, "image_url": "https://res.cloudinary.com/dpy3fwmkh/image/upload/ancient_abyss.png",
     "description": "The Dark Matter Panther in its oldest form — the void that existed before the universe had anything to put in it.",
     "loot_table": ["tear_of_leviathan", "ambrosia_tart", "genesis_fruit"]},
]

# Active ancient raids: {raid_id: {...}}
active_ancient_raids: dict[int, dict] = {}
# Active lobbies waiting to fill: {message_id: {...}}
active_lobbies: dict[int, dict] = {}
# Per-raid asyncio locks (same pattern as guild raids)
_ancient_locks: dict[int, asyncio.Lock] = {}

LOBBY_WAIT    = 60    # seconds to wait for players before auto-starting
MAX_PARTY     = 10    # max players
MIN_LEVEL     = 10    # minimum trainer level


SUMMON_ITEMS = {
    "epoch_shard":     "ancient_chronos",
    "firstborn_ember": "ancient_genesis",
    "void_prism":      "ancient_abyss",
}

# Per-boss summoning flavor — shown as a dramatic animation before the lobby opens
SUMMON_ANIMATIONS = {
    "ancient_chronos": [
        "*The Epoch Shard splinters. Time around the altar stutters — a moment repeats once, then again, then stops.*",
        "*Somewhere behind you, a sound occurs that hasn't happened yet. You turn. Nothing is there. It hasn't happened yet.*",
        "*Ancient Chronos was already here. It has always been here. It is choosing, now, to be seen.*",
    ],
    "ancient_genesis": [
        "*The Firstborn Ember touches the altar and ignites it with a light that predates color.*",
        "*The flame does not burn the stone. It reminds it of when it was something else.*",
        "*The light does not fade when it arrives. Ancient Genesis does not appear — it simply becomes true.*",
    ],
    "ancient_abyss": [
        "*The Void Prism is placed on the altar. The light in the room does not go out — it simply stops arriving.*",
        "*The silence changes. It becomes the kind of silence that existed before sound was invented.*",
        "*Something vast and patient has noticed. The darkness does not approach — it simply becomes the room.*",
    ],
}


class Ancient(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def summon_item_autocomplete(self, interaction: discord.Interaction, current: str):
        """Show only summon items the player actually owns."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT item_id, quantity FROM player_inventory WHERE user_id = ? AND item_id IN ('epoch_shard','firstborn_ember','void_prism')",
                (interaction.user.id,)
            ) as c:
                rows = {r["item_id"]: r["quantity"] for r in await c.fetchall()}
        names = {"epoch_shard": "⏳ Epoch Shard", "firstborn_ember": "🔥 Firstborn Ember", "void_prism": "🌑 Void Prism"}
        choices = []
        for iid, display in names.items():
            if iid in rows and current.lower() in display.lower():
                choices.append(app_commands.Choice(name=f"{display} (x{rows[iid]})", value=iid))
        return choices

    # ── /ancient ─────────────────────────────────────────────────────────
    @app_commands.command(name="ancient", description="Use a summon item to call an Ancient boss 🏛️")
    @app_commands.describe(summon_item="The item to use — Epoch Shard, Firstborn Ember, or Void Prism")
    @app_commands.autocomplete(summon_item=summon_item_autocomplete)
    async def ancient(self, interaction: discord.Interaction, summon_item: str):
        await interaction.response.defer()

        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        # Level check
        if player["level"] < MIN_LEVEL:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You need to be at least **Level {MIN_LEVEL}** to summon an Ancient.",
                color=COLORS["error"]
            ))

        # Validate item
        if summon_item not in SUMMON_ITEMS:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ That item can't summon an Ancient. You need an **Epoch Shard**, **Firstborn Ember**, or **Void Prism**.",
                color=COLORS["error"]
            ))

        # Check inventory
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                (interaction.user.id, summon_item)
            ) as c:
                inv_row = await c.fetchone()
        if not inv_row or inv_row["quantity"] < 1:
            item_names = {"epoch_shard": "Epoch Shard", "firstborn_ember": "Firstborn Ember", "void_prism": "Void Prism"}
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You don't have a **{item_names[summon_item]}**. Defeat Corrupted raid bosses to obtain one.",
                color=COLORS["error"]
            ))

        # Check no ancient already active in this channel
        for lobby in active_lobbies.values():
            if lobby["channel_id"] == interaction.channel_id:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ There's already an Ancient lobby open in this channel!",
                    color=COLORS["error"]
                ))
        for raid in active_ancient_raids.values():
            if raid["channel_id"] == interaction.channel_id:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ An Ancient raid is already active here!",
                    color=COLORS["error"]
                ))

        # Consume item
        async with aiosqlite.connect(DB_PATH) as db:
            if inv_row["quantity"] == 1:
                await db.execute("DELETE FROM player_inventory WHERE id = ?", (inv_row["id"],))
            else:
                await db.execute("UPDATE player_inventory SET quantity = quantity - 1 WHERE id = ?", (inv_row["id"],))
            await db.commit()

        # Find the specific boss this item summons
        boss_id = SUMMON_ITEMS[summon_item]
        boss = next(b for b in ANCIENT_BOSSES if b["id"] == boss_id)

        # Dramatic summoning animation
        animation_lines = SUMMON_ANIMATIONS[boss_id]
        summon_embed = discord.Embed(
            title=f"🏛️ The Altar Stirs...",
            description=animation_lines[0],
            color=COLORS.get("ancient", COLORS["legendary"])
        )
        summon_msg = await interaction.followup.send(embed=summon_embed)
        await asyncio.sleep(2.0)
        await summon_msg.edit(embed=discord.Embed(
            title=f"🏛️ The Altar Stirs...",
            description="\n\n".join(animation_lines[:2]),
            color=COLORS.get("ancient", COLORS["legendary"])
        ))
        await asyncio.sleep(2.0)
        await summon_msg.edit(embed=discord.Embed(
            title=f"🏛️ {boss['name']} Answers.",
            description="\n\n".join(animation_lines),
            color=COLORS.get("ancient", COLORS["legendary"])
        ))
        await asyncio.sleep(1.5)

        # Build lobby embed + Join button
        class LobbyView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=LOBBY_WAIT)
                self.party: dict[int, str] = {interaction.user.id: str(interaction.user)}

            @discord.ui.button(label="Join Party", style=discord.ButtonStyle.success, emoji="⚔️")
            async def join(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                uid = btn_interaction.user.id
                if uid in self.party:
                    return await btn_interaction.response.send_message("✦ You're already in the party!", ephemeral=True)
                if len(self.party) >= MAX_PARTY:
                    return await btn_interaction.response.send_message("✦ The party is full!", ephemeral=True)

                # Level check for joiners too
                joiner = await get_or_create_player(uid, str(btn_interaction.user))
                if joiner["level"] < MIN_LEVEL:
                    return await btn_interaction.response.send_message(
                        f"✦ You need Level {MIN_LEVEL} to join an Ancient raid.", ephemeral=True
                    )

                self.party[uid] = str(btn_interaction.user)
                await btn_interaction.response.send_message(
                    f"✅ Joined the party! **{len(self.party)}/{MAX_PARTY}** players ready.",
                    ephemeral=True
                )
                # Update the lobby embed to show new count
                try:
                    await btn_interaction.message.edit(embed=build_lobby_embed(self.party))
                except Exception:
                    pass

                # Auto-start if full
                if len(self.party) >= MAX_PARTY:
                    for item in view.children:
                        item.disabled = True
                    try:
                        await btn_interaction.message.edit(view=view)
                    except Exception:
                        pass
                    self.stop()

            @discord.ui.button(label="Solo Run", style=discord.ButtonStyle.danger, emoji="💀")
            async def solo_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message(
                        "✦ Only the summoner can start a solo run.", ephemeral=True
                    )
                await btn_interaction.response.send_message(
                    "💀 **Solo run initiated.**\n"
                    "*Ancient raids are tuned for full 3-beast parties. Solo, your survival window is roughly "
                    "one third of the time needed to kill the boss — the math does not work in your favour. "
                    "The boss will hit your party for ~7% of each beast's HP every 10 seconds, and signature "
                    "moves at 70%, 40%, and 15% HP hit all three simultaneously for up to 17% HP each. "
                    "You were warned. It is not impossible — just very close.*",
                    ephemeral=True
                )
                for item in view.children:
                    item.disabled = True
                try:
                    await btn_interaction.message.edit(view=view)
                except Exception:
                    pass
                self.stop()

            async def on_timeout(self):
                for item in self.children:
                    item.disabled = True
                try:
                    await msg.edit(view=self)
                except Exception:
                    pass

        def build_lobby_embed(party: dict) -> discord.Embed:
            names = "\n".join(f"• {name}" for name in list(party.values())[:10])
            embed = discord.Embed(
                title=f"🏛️ Ancient Lobby — {boss['name']}",
                description=(
                    f"*{boss['description']}*\n\n"
                    f"**Anyone can join — no guild required.**\n"
                    f"Click **Join Party** to enter. Raid starts in **{LOBBY_WAIT}s** or when full.\n\n"
                    f"**Party ({len(party)}/{MAX_PARTY}):**\n{names}"
                ),
                color=COLORS.get("ancient", COLORS["legendary"])
            )
            if boss.get("image_url"):
                embed.set_thumbnail(url=boss["image_url"])
            embed.set_footer(text=f"Summoned by {interaction.user.display_name} · item consumed")
            return embed

        view = LobbyView()
        msg = await interaction.followup.send(embed=build_lobby_embed(view.party), view=view)

        # Store lobby reference
        active_lobbies[msg.id] = {
            "channel_id": interaction.channel_id,
            "boss": boss,
            "party": view.party,
            "message_id": msg.id,
        }

        # Wait for lobby to fill or timeout
        await view.wait()

        # Remove from lobbies and start the raid
        active_lobbies.pop(msg.id, None)

        if len(view.party) < 1:
            return await msg.edit(embed=discord.Embed(
                description="✦ Not enough players — the Ancient fades back into the void.",
                color=COLORS["info"]
            ), view=None)

        # Disable join button
        for item in view.children:
            item.disabled = True
        await msg.edit(view=view)

        # Create raid in DB
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO raids (boss_id, boss_name, boss_type, max_hp, current_hp, guild_id, channel_id)
                VALUES (?, ?, 'ancient', ?, ?, NULL, ?)
            """, (boss["id"], boss["name"], boss["max_hp"], boss["max_hp"], interaction.channel_id))
            await db.commit()
            async with db.execute("SELECT last_insert_rowid()") as c:
                raid_id = (await c.fetchone())[0]

        # Dynamic scaling — fetch actual party beast stats at raid start
        # Formula derived from balance math:
        #   boss_hp  = party_dps_mid * 35  (~4.7 min fight at full DPS)
        #   boss_atk = avg_party_hp * 0.20  (hits for ~10% avg HP after defense)
        #   boss_def = avg_party_def * 0.80  (slightly easier to hit than players)
        party_size = max(1, len(view.party))
        party_beast_stats = []
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            for uid in view.party:
                # Use raid party beasts for scaling — fallback to active beast
                async with db.execute(
                    "SELECT hp, max_hp, attack, defense FROM player_beasts WHERE user_id = ? AND raid_slot IN (1,2,3) ORDER BY raid_slot",
                    (uid,)
                ) as c:
                    raid_beasts = [dict(r) for r in await c.fetchall()]
                if raid_beasts:
                    party_beast_stats.extend(raid_beasts)
                else:
                    async with db.execute(
                        "SELECT hp, max_hp, attack, defense FROM player_beasts WHERE user_id = ? AND is_active = 1",
                        (uid,)
                    ) as c:
                        row = await c.fetchone()
                    if row:
                        party_beast_stats.append(dict(row))

        if not party_beast_stats:
            # Fallback: use boss base stats if no beast data found
            party_beast_stats = [{"hp": 255, "max_hp": 255, "attack": 145, "defense": 125}]

        avg_party_hp  = sum(b["max_hp"]  for b in party_beast_stats) / len(party_beast_stats)
        avg_party_def = sum(b["defense"] for b in party_beast_stats) / len(party_beast_stats)
        total_party_atk = sum(b["attack"] for b in party_beast_stats)

        scaled_boss_def = min(200, int(avg_party_def * 0.60))  # lower DEF so mid-tier beasts deal meaningful damage
        # Estimate party DPS at mid-fight boss defense
        def _est_dps(atk, bdef):
            df = bdef / (bdef + 100)
            return max(1, int(atk * (1 - df)))
        # DPS per-beast, grouped by player
        # Scale boss HP on avg player DPS so weak players don't inflate beyond what strong ones can clear
        party_dps_mid = sum(_est_dps(b["attack"], scaled_boss_def) * 10 for b in party_beast_stats)
        _n_players = max(1, len(view.party))
        _avg_player_dps = party_dps_mid / _n_players

        # Scale by n_players^0.75 so larger parties face a proportionally bigger boss
        # but it never becomes punishing — solo=1x, duo=1.68x, 3p=2.28x, 5p=3.34x
        _party_scale = _n_players ** 0.75
        # cycles=15 calibrated to realistic ~1.5s/attack cadence (not theoretical 0.5s max)
        scaled_hp  = int(_avg_player_dps * 15 * _party_scale)
        scaled_atk = int(avg_party_hp * 0.07)     # ancient hits ~7% avg HP — longer fight, kinder hits
        # Minimum floor from boss base stats so it never feels trivial
        scaled_hp  = max(scaled_hp,  boss["max_hp"] // 3)
        scaled_atk = max(scaled_atk, boss["attack"] // 20)

        active_ancient_raids[raid_id] = {
            "boss": boss,
            "current_hp": scaled_hp,
            "max_hp": scaled_hp,
            "participants": {},
            "party": set(view.party.keys()),
            "channel_id": interaction.channel_id,
            "channel": interaction.channel,
            "raid_message": None,
            "attack_counts": {},
            "embed_lock": None,
            "last_attack": {},
            "player_hp": {},
            "player_max_hp": {},
            "player_mana": {},
            "player_defense": {},
            "player_atk": {},
            "phase_fired": set(),
            "phase_log": [],       # accumulated phase events shown on embed
            "boss_attack": scaled_atk,
            "last_event": "",
            "player_party": {},
            "player_active_slot": {},
            "player_party_hp": {},    # (uid, slot) -> current HP per party beast
        }
        _ancient_locks[raid_id] = asyncio.Lock()
        active_ancient_raids[raid_id]["embed_lock"] = asyncio.Lock()

        party_preview = ", ".join(list(view.party.values())[:5]) + ("..." if len(view.party) > 5 else "")
        ATTACK_COOLDOWN = 0.8
        BOSS_ATK_INTERVAL = 10

        async def _update_embed(view_ref=None, ended=False):
            if raid_id not in active_ancient_raids:
                return
            r = active_ancient_raids[raid_id]
            lock = r.get("embed_lock")
            if lock is None or lock.locked():
                return
            async with lock:
                if raid_id not in active_ancient_raids:
                    return
                r = active_ancient_raids[raid_id]
                msg = r.get("raid_message")
                if not msg:
                    return
                try:
                    v = None if ended else (view_ref or anc_view_ref[0])
                    if v:
                        v.update_ult_style("_multi", r)
                    await msg.edit(embed=build_ancient_embed(r), view=v)
                except discord.HTTPException:
                    pass

        anc_view_ref = [None]

        def boss_effective_defense(raid: dict) -> int:
            pct = raid["current_hp"] / max(raid["max_hp"], 1)
            base_def = scaled_boss_def
            if pct < 0.15:   return int(base_def * 0.40)
            elif pct < 0.40: return int(base_def * 0.60)
            elif pct < 0.70: return int(base_def * 0.80)
            return base_def

        def calc_player_damage(atk: int, defense: int, is_ultimate: bool, is_crit: bool, mana: int = 50) -> int:
            defense_factor = defense / (defense + 100)
            dmg = atk * (1 - defense_factor)
            if is_ultimate:
                ult_mult = 1.8 + max(0, mana - 50) / 50 * 0.9
                dmg *= ult_mult
            if is_crit:     dmg *= 1.5
            return max(1, int(dmg * random.uniform(0.85, 1.15)))

        def calc_boss_damage(boss_atk: int, player_def: int, player_max_hp: int = 0) -> int:
            if player_max_hp > 0:
                pct = random.uniform(0.05, 0.09)  # ancient: 7% avg HP per hit — longer fight, kinder hits
                return max(1, int(player_max_hp * pct))
            defense_factor = min(player_def, 300) / (min(player_def, 300) + 100)
            return max(1, int(boss_atk * (1 - defense_factor) * random.uniform(0.80, 1.20)))

        def build_ancient_embed(raid: dict) -> discord.Embed:
            current_hp = raid["current_hp"]
            max_hp = raid["max_hp"]
            pct = current_hp / max(max_hp, 1)
            status = "\U0001f534 CRITICAL" if pct < 0.15 else "\U0001f7e0 Weakened" if pct < 0.40 else "\U0001f7e1 Damaged" if pct < 0.70 else "\U0001f7e2 Active"
            base_def = scaled_boss_def
            eff_def = boss_effective_defense(raid)
            def_pct = int((1 - eff_def / max(base_def, 1)) * 100)
            def_note = f" *(\u2212{def_pct}% DEF)*" if def_pct > 0 else ""
            embed = discord.Embed(
                title=f"\U0001f3db\ufe0f ANCIENT RAID \u2014 {boss['name']}! ({party_size} players)",
                description=(
                    f"*The primordial beast manifests.*\n\n"
                    f"*{boss['description']}*\n\n"
                    f"\U0001f480 **HP:** {hp_bar(current_hp, max_hp)} {status}{def_note}\n"
                    f"`{current_hp:,} / {max_hp:,}`\n\n"
                    f"\U0001f3c6 Top 3 damage dealers can catch the boss!"
                ),
                color=COLORS.get("ancient", COLORS["legendary"])
            )
            if raid["participants"]:
                top = sorted(raid["participants"].items(), key=lambda x: x[1], reverse=True)[:5]
                medals = ["\U0001f947", "\U0001f948", "\U0001f949", "4\ufe0f\u20e3", "5\ufe0f\u20e3"]
                lines = []
                for i, (uid, dmg) in enumerate(top):
                    p_hp  = raid["player_hp"].get(uid, 0)
                    p_max = raid["player_max_hp"].get(uid, 1)
                    mana  = raid["player_mana"].get(uid, 0)
                    alive = "\U0001f480" if p_hp <= 0 else "\u26a1" if mana >= 50 else "\u2764\ufe0f"
                    lines.append(f"{medals[i]} <@{uid}> \u2014 `{dmg:,}` dmg {alive} `{p_hp}/{p_max}HP`")
                embed.add_field(name="⚔️ Party", value="\n".join(lines), inline=False)
            if raid.get("phase_log"):
                embed.add_field(name="📋 Phase Log", value="\n".join(raid["phase_log"]), inline=False)
            if boss.get("image_url"):
                embed.set_image(url=boss["image_url"])
            if raid.get("last_event"):
                embed.add_field(name="📣 Last Event", value=raid["last_event"], inline=False)
            embed.set_footer(text=f"Raid ID: #{raid_id} | Party: {party_preview} | Boss attacks every {BOSS_ATK_INTERVAL}s")
            return embed

        async def boss_attack_loop():
            while raid_id in active_ancient_raids:
                await asyncio.sleep(BOSS_ATK_INTERVAL)
                if raid_id not in active_ancient_raids:
                    break
                cur_raid = active_ancient_raids[raid_id]
                if not cur_raid["participants"]:
                    continue
                alive = [uid for uid, hp in cur_raid["player_hp"].items() if hp > 0]
                if not alive:
                    continue
                target_uid = random.choice(alive)
                p_def = cur_raid["player_defense"].get(target_uid, 50)
                dmg = calc_boss_damage(cur_raid["boss_attack"], p_def, cur_raid["player_max_hp"].get(target_uid, 0))
                async with _ancient_locks[raid_id]:
                    if raid_id not in active_ancient_raids:
                        break
                    cur_raid["player_hp"][target_uid] = max(0, cur_raid["player_hp"].get(target_uid, 0) - dmg)
                    p_hp  = cur_raid["player_hp"][target_uid]
                    p_max = cur_raid["player_max_hp"].get(target_uid, 1)
                    _aslot = cur_raid.get("player_active_slot", {}).get(target_uid, 0)
                    cur_raid["player_party_hp"][(target_uid, _aslot)] = p_hp
                died = p_hp <= 0
                # Store boss attack in last_event — no new messages
                if raid_id in active_ancient_raids:
                    active_ancient_raids[raid_id]["last_event"] = (
                        f"\U0001f4a5 **{boss['name']}** strikes <@{target_uid}>! `{dmg:,}` dmg"
                        + (" \u2014 **knocked out!** \U0001f480" if died else f" | `{p_hp}/{p_max}HP`")
                    )

                # Check full party wipe
                if cur_raid["participants"] and raid_id in active_ancient_raids:
                    all_down = all(
                        cur_raid["player_hp"].get(uid, 1) <= 0
                        for uid in cur_raid["participants"]
                    )
                    any_bench = any(
                        len(cur_raid.get("player_party", {}).get(uid, [])) > 1 and
                        cur_raid["player_active_slot"].get(uid, 0) < len(cur_raid["player_party"][uid]) - 1
                        for uid in cur_raid["participants"]
                    )
                    if all_down and not any_bench:
                        await cog.end_ancient_raid(raid_id, interaction.channel)
                        break
                asyncio.create_task(_update_embed())

        _PHASE_DEF_REDUCTION = {0.70: 20, 0.40: 40, 0.15: 60}

        async def check_phase_transitions(cur_raid: dict, channel):
            pct = cur_raid["current_hp"] / max(cur_raid["max_hp"], 1)
            from cogs.guilds import BOSS_SIGNATURES
            signatures = BOSS_SIGNATURES.get(boss["id"], [])
            for sig in signatures:
                if pct <= sig["threshold"] and sig["threshold"] not in cur_raid["phase_fired"]:
                    cur_raid["phase_fired"].add(sig["threshold"])
                    alive = [uid for uid, hp in cur_raid["player_hp"].items() if hp > 0]
                    for uid in alive:
                        dmg = int(calc_boss_damage(cur_raid["boss_attack"], cur_raid["player_defense"].get(uid, 50), cur_raid["player_max_hp"].get(uid, 0)) * sig["mult"])
                        cur_raid["player_hp"][uid] = max(0, cur_raid["player_hp"].get(uid, 0) - dmg)
                    _pslot = cur_raid.get("player_active_slot", {}).get(uid, 0)
                    cur_raid["player_party_hp"][(uid, _pslot)] = cur_raid["player_hp"][uid]
                    phase_status = (
                        "🔴 CRITICAL — Boss DEF −60%" if sig["threshold"] == 0.15 else
                        "🟠 Weakened — Boss DEF −40%"  if sig["threshold"] == 0.40 else
                        "🟡 Damaged — Boss DEF −20%"
                    )
                    cur_raid["last_event"] = f"⚡ **{sig['name']}** — {phase_status}"
                    cur_raid["phase_log"].append(f"{phase_status} · *{sig['name']}*")

        cog = self

        class AncientRaidView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=1800)

            def update_ult_style(self, uid: str, raid: dict):
                mana = raid.get("player_mana", {}).get(uid, 0) if uid != "_multi" else max(
                    (v for v in raid.get("player_mana", {}).values()), default=0
                )
                for item in self.children:
                    if isinstance(item, discord.ui.Button) and "Ultimate" in item.label:
                        item.style = discord.ButtonStyle.primary if mana >= 50 else discord.ButtonStyle.secondary

            @discord.ui.button(label="⚔️ Attack!", style=discord.ButtonStyle.danger, emoji="💥")
            async def attack_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                import time
                await btn_interaction.response.defer(ephemeral=True, thinking=False)
                if raid_id not in active_ancient_raids:
                    return await btn_interaction.followup.send("\u2746 The raid has ended!", ephemeral=True)
                cur_raid = active_ancient_raids[raid_id]
                uid = btn_interaction.user.id
                if uid not in cur_raid["party"]:
                    return await btn_interaction.followup.send("✦ You're not in this party!", ephemeral=True)

                # Knocked out — show swap UI if bench slots remain
                if uid in cur_raid["player_hp"] and cur_raid["player_hp"][uid] <= 0:
                    party = cur_raid.get("player_party", {}).get(uid, [])
                    current_slot = cur_raid.get("player_active_slot", {}).get(uid, 0)
                    bench = [(i, b) for i, b in enumerate(party) if i != current_slot and cur_raid.get("player_party_hp", {}).get((uid, i), 1) > 0]
                    if not bench:
                        return await btn_interaction.followup.send("✦ All your beasts are down. You can still watch.", ephemeral=True)

                    async def do_swap(slot_idx: int, swap_interaction: discord.Interaction):
                        if raid_id not in active_ancient_raids:
                            return await swap_interaction.response.send_message("✦ Raid ended!", ephemeral=True)
                        r = active_ancient_raids[raid_id]
                        new_beast = r["player_party"][uid][slot_idx]
                        r["player_active_slot"][uid]  = slot_idx
                        tracked_hp = r["player_party_hp"].get((uid, slot_idx), new_beast["hp"])
                        r["player_hp"][uid]           = tracked_hp
                        r["player_max_hp"][uid]       = new_beast["max_hp"]
                        r["player_defense"][uid]      = new_beast["defense"]
                        r["player_atk"][uid]          = new_beast["attack"]
                        r["player_mana"][uid]         = 0
                        bd = get_beast_data(new_beast["beast_id"]) or {}
                        name = new_beast.get("nickname") or bd.get("name", "Beast")
                        hp_str = f"`{tracked_hp}/{new_beast['max_hp']}HP`"
                        await swap_interaction.response.send_message(
                            f"✅ **{name}** enters the fight! {hp_str}",
                            ephemeral=True
                        )

                    class SwapView(discord.ui.View):
                        def __init__(self):
                            super().__init__(timeout=30)
                            for slot_idx, beast_row in bench:
                                bd = get_beast_data(beast_row["beast_id"]) or {}
                                label = beast_row.get("nickname") or bd.get("name", f"Beast {slot_idx+1}")
                                _tracked = cur_raid.get("player_party_hp", {}).get((uid, slot_idx), beast_row["hp"])
                                btn = discord.ui.Button(
                                    label=f"{label} ({_tracked}HP)",
                                    style=discord.ButtonStyle.primary,
                                    emoji="🔄"
                                )
                                async def _cb(inter, si=slot_idx):
                                    self.stop()
                                    for item in self.children: item.disabled = True
                                    await do_swap(si, inter)
                                btn.callback = _cb
                                self.add_item(btn)

                    return await btn_interaction.followup.send(
                        "💀 **Your beast is down!** Send in your next one:",
                        view=SwapView(),
                        ephemeral=True
                    )

                now = time.monotonic()
                if now - cur_raid["last_attack"].get(uid, 0) < ATTACK_COOLDOWN:
                    return
                cur_raid["last_attack"][uid] = now

                # Load raid party on first attack — must have 3 slots assigned via /raidparty
                if uid not in cur_raid["player_party"]:
                    async with aiosqlite.connect(DB_PATH) as _pdb:
                        _pdb.row_factory = aiosqlite.Row
                        async with _pdb.execute(
                            "SELECT * FROM player_beasts WHERE user_id = ? AND raid_slot IN (1,2,3) ORDER BY raid_slot",
                            (uid,)
                        ) as _c:
                            party_rows = [dict(r) for r in await _c.fetchall()]
                    if len(party_rows) < 3:
                        return await btn_interaction.followup.send(
                            f"✦ You need a full 3-beast raid party! You have {len(party_rows)}/3 slots filled.\n"
                            f"Use `/raidparty` to set up your team before attacking.",
                            ephemeral=True
                        )
                    cur_raid["player_party"][uid]       = party_rows
                    cur_raid["player_active_slot"][uid] = 0
                    for _si, _br in enumerate(party_rows):
                        cur_raid["player_party_hp"][( uid, _si)] = _br["hp"]
                    slot = party_rows[0]
                    cur_raid["player_hp"][uid]      = slot["hp"]
                    cur_raid["player_max_hp"][uid]  = slot["max_hp"]
                    cur_raid["player_defense"][uid] = slot["defense"]
                    cur_raid["player_atk"][uid]     = slot["attack"]
                    cur_raid["player_mana"][uid]    = 0

                # Use party slot stats — not live DB
                _slot = cur_raid["player_active_slot"].get(uid, 0)
                _party = cur_raid["player_party"].get(uid, [])
                _beast_row = _party[_slot] if _slot < len(_party) else _party[0]
                _player_atk = cur_raid["player_atk"].get(uid, _beast_row["attack"])
                _player_spd = _beast_row.get("speed", 50)

                is_crit = random.random() < 0.15
                defense = boss_effective_defense(cur_raid)
                damage  = calc_player_damage(_player_atk, defense, False, is_crit)
                raid_lock = _ancient_locks.get(raid_id)
                if not raid_lock:
                    return await btn_interaction.followup.send("\u2746 The raid just ended!", ephemeral=True)
                async with raid_lock:
                    if raid_id not in active_ancient_raids:
                        return await btn_interaction.followup.send("\u2746 The raid just ended!", ephemeral=True)
                    cur_raid = active_ancient_raids[raid_id]
                    cur_raid["current_hp"] = max(0, cur_raid["current_hp"] - damage)
                    cur_raid["participants"][uid] = cur_raid["participants"].get(uid, 0) + damage
                    cur_raid["attack_counts"][uid] = cur_raid["attack_counts"].get(uid, 0) + 1
                    _mana_gain = min(15, 8 + _player_spd // 40)
                    cur_raid["player_mana"][uid] = min(100, cur_raid["player_mana"].get(uid, 0) + _mana_gain)
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE raids SET current_hp = ? WHERE id = ?", (cur_raid["current_hp"], raid_id))
                        async with db.execute("SELECT damage_dealt FROM raid_participants WHERE raid_id = ? AND user_id = ?", (raid_id, uid)) as c:
                            existing = await c.fetchone()
                        if existing:
                            await db.execute("UPDATE raid_participants SET damage_dealt = damage_dealt + ? WHERE raid_id = ? AND user_id = ?", (damage, raid_id, uid))
                        else:
                            await db.execute("INSERT INTO raid_participants (raid_id, user_id, damage_dealt) VALUES (?, ?, ?)", (raid_id, uid, damage))
                        await db.commit()
                    raid_ended = cur_raid["current_hp"] <= 0
                    new_mana   = cur_raid["player_mana"].get(uid, 0)
                await check_phase_transitions(active_ancient_raids.get(raid_id, cur_raid), btn_interaction.channel)
                # Store last player action as last_event — no ephemeral
                if raid_id in active_ancient_raids:
                    crit_tag = "\u2b50 CRIT! " if is_crit else ""
                    active_ancient_raids[raid_id]["last_event"] = f"{crit_tag}<@{uid}> hit for `{damage:,}` dmg | Mana `{new_mana}/100`" + (" \u26a1" if new_mana >= 50 else "")
                asyncio.create_task(_update_embed(self, raid_ended))
                await track_quest_event(uid, "raid_damage", amount=damage)
                await advance_quest_step(uid, "raid_participate")
                if raid_ended:
                    await cog.end_ancient_raid(raid_id, btn_interaction.channel)

            @discord.ui.button(label="\u26a1 Ultimate", style=discord.ButtonStyle.secondary, emoji="\U0001f4ab")
            async def ultimate_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                import time
                await btn_interaction.response.defer(ephemeral=True, thinking=False)
                if raid_id not in active_ancient_raids:
                    return await btn_interaction.followup.send("\u2746 The raid has ended!", ephemeral=True)
                cur_raid = active_ancient_raids[raid_id]
                uid = btn_interaction.user.id
                if uid not in cur_raid["party"]:
                    return await btn_interaction.followup.send("✦ You're not in this party!", ephemeral=True)
                if uid in cur_raid["player_hp"] and cur_raid["player_hp"][uid] <= 0:
                    return await btn_interaction.followup.send("✦ Your beast is knocked out!", ephemeral=True)
                if cur_raid["player_mana"].get(uid, 0) < 50:
                    return await btn_interaction.followup.send(f"✦ Not enough mana! `{cur_raid['player_mana'].get(uid,0)}/50` needed.", ephemeral=True)
                self._set_attack_buttons(True)
                asyncio.create_task(self._re_enable_after(ATTACK_COOLDOWN))
                cur_raid["last_attack"][uid] = time.monotonic()
                # Use party slot stats
                _ult_slot = cur_raid.get("player_active_slot", {}).get(uid, 0)
                _ult_party = cur_raid.get("player_party", {}).get(uid, [])
                if not _ult_party:
                    return await btn_interaction.followup.send("✦ No party loaded! Attack first.", ephemeral=True)
                _ult_beast = _ult_party[_ult_slot] if _ult_slot < len(_ult_party) else _ult_party[0]
                _ult_bd = get_beast_data(_ult_beast["beast_id"]) or {}
                ult_name = _ult_bd.get("ultimate", "Ultimate")
                _ult_atk = cur_raid["player_atk"].get(uid, _ult_beast["attack"])
                is_crit = random.random() < 0.20
                defense = boss_effective_defense(cur_raid)
                _ult_mana = cur_raid["player_mana"].get(uid, 50)
                damage  = calc_player_damage(_ult_atk, defense, True, is_crit, _ult_mana)
                raid_lock = _ancient_locks.get(raid_id)
                if not raid_lock:
                    return await btn_interaction.followup.send("\u2746 The raid just ended!", ephemeral=True)
                async with raid_lock:
                    if raid_id not in active_ancient_raids:
                        return await btn_interaction.followup.send("\u2746 The raid just ended!", ephemeral=True)
                    cur_raid = active_ancient_raids[raid_id]
                    cur_raid["current_hp"] = max(0, cur_raid["current_hp"] - damage)
                    cur_raid["participants"][uid] = cur_raid["participants"].get(uid, 0) + damage
                    cur_raid["attack_counts"][uid] = cur_raid["attack_counts"].get(uid, 0) + 1
                    cur_raid["player_mana"][uid] = 0
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE raids SET current_hp = ? WHERE id = ?", (cur_raid["current_hp"], raid_id))
                        async with db.execute("SELECT damage_dealt FROM raid_participants WHERE raid_id = ? AND user_id = ?", (raid_id, uid)) as c:
                            existing = await c.fetchone()
                        if existing:
                            await db.execute("UPDATE raid_participants SET damage_dealt = damage_dealt + ? WHERE raid_id = ? AND user_id = ?", (damage, raid_id, uid))
                        else:
                            await db.execute("INSERT INTO raid_participants (raid_id, user_id, damage_dealt) VALUES (?, ?, ?)", (raid_id, uid, damage))
                        await db.commit()
                    raid_ended = cur_raid["current_hp"] <= 0
                await check_phase_transitions(active_ancient_raids.get(raid_id, cur_raid), btn_interaction.channel)
                # Ultimate stored as last_event — no new messages
                if raid_id in active_ancient_raids:
                    crit_tag = "\u2b50 CRIT! " if is_crit else ""
                    active_ancient_raids[raid_id]["last_event"] = f"\u26a1 <@{uid}> unleashes **{ult_name}**! {crit_tag}`{damage:,}` dmg \u2014 Mana reset."
                asyncio.create_task(_update_embed(self, raid_ended))
                await track_quest_event(uid, "raid_damage", amount=damage)
                await advance_quest_step(uid, "raid_participate")
                if raid_ended:
                    await cog.end_ancient_raid(raid_id, btn_interaction.channel)

            async def on_timeout(self):
                if raid_id in active_ancient_raids:
                    await cog.end_ancient_raid(raid_id, interaction.channel, timed_out=True)

        raid_view = AncientRaidView()
        anc_view_ref[0] = raid_view
        raid_msg = await interaction.channel.send(embed=build_ancient_embed(active_ancient_raids[raid_id]), view=raid_view)
        active_ancient_raids[raid_id]["raid_message"] = raid_msg

        if party_size > 1:
            await interaction.channel.send(
                f"⚖️ *{boss['name']} calibrates to the party.* "
                f"HP: `{scaled_hp:,}` · Boss ATK: `{scaled_atk}` · Boss DEF: `{scaled_boss_def}`",
                silent=True
            )

        asyncio.create_task(boss_attack_loop())

        await asyncio.sleep(1800)
        if raid_id in active_ancient_raids:
            await cog.end_ancient_raid(raid_id, interaction.channel, timed_out=True)

    # ── /ancient_attack (kept as slash fallback) ──────────────────────────
    @app_commands.command(name="ancient_attack", description="Attack an active Ancient boss! 🏛️ (use the button instead)")
    async def ancient_attack(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "✦ Click the **⚔️ Attack!** button on the raid announcement to attack!", ephemeral=True
        )

    # ── end_ancient_raid ──────────────────────────────────────────────────
    async def end_ancient_raid(self, raid_id: int, channel, timed_out: bool = False):
        if raid_id not in active_ancient_raids:
            return
        raid = active_ancient_raids.pop(raid_id)
        _ancient_locks.pop(raid_id, None)
        boss = raid["boss"]
        defeated = raid["current_hp"] <= 0

        sorted_participants = sorted(raid["participants"].items(), key=lambda x: x[1], reverse=True)

        # ── Per-boss kill scenes ─────────────────────────────────────────
        BOSS_KILL_SCENES = {
            "ancient_chronos": {
                "title": "⏳ Time Catches Its Breath",
                "lines": [
                    "Ancient Chronos does not fall. It simply stops.",
                    "One moment it is there — vast, ageless, older than the word 'old' — and then the moment passes.",
                    "Time resumes normally. You hadn't noticed it had been moving strangely until it didn't.",
                    "*Chronos does not die. It steps back. It will be here before everything else again, when everything else ends.*",
                    "*It is choosing, now, to let you have this.*",
                ],
                "color": "ancient",
            },
            "ancient_genesis": {
                "title": "🔥 The First Flame Dims",
                "lines": [
                    "The light that predates color fades to something the eye can actually hold.",
                    "Ancient Genesis folds inward — not extinguished, but contained. The flame that started everything becomes small enough to cup in two hands.",
                    "Everything alive in the vicinity flickers, briefly, as if reminded of something it was before it knew what it was.",
                    "*You did not kill the First Flame. That is not possible. You simply proved you were worth sharing it with.*",
                ],
                "color": "ancient",
            },
            "ancient_abyss": {
                "title": "🌑 The Darkness Recedes",
                "lines": [
                    "The light comes back. Not all at once — in edges, then corners, then the middle of things.",
                    "Ancient Abyss does not retreat. It simply becomes less present, pulling back into whatever it was before it chose to fill the room.",
                    "The silence changes again. It becomes ordinary silence — the kind that just means no one is talking.",
                    "*The void before stars is still out there. It will be out there after the stars are gone. It simply has no reason to be here anymore.*",
                    "*You gave it a reason to leave. That is not nothing.*",
                ],
                "color": "ancient",
            },
        }

        if defeated:
            scene = BOSS_KILL_SCENES.get(boss["id"])
            if scene:
                kill_embed = discord.Embed(
                    title=scene["title"],
                    description="\n\n".join(scene["lines"]),
                    color=COLORS.get(scene["color"], COLORS["legendary"])
                )
                if boss.get("image_url"):
                    kill_embed.set_image(url=boss["image_url"])
                await channel.send(embed=kill_embed)

            embed = discord.Embed(
                title=f"🏛️ {boss['name']} Defeated!",
                description=(
                    f"*The primordial force is subdued — for now.*\n\n"
                    f"⚠️ **Top 3 damage dealers have a chance to catch {boss['name']}.**\n"
                    f"Rank 1: **10%** · Rank 2: **6%** · Rank 3: **3%**"
                ),
                color=COLORS.get("ancient", COLORS["legendary"])
            )

        else:
            from cogs.guilds import BOSS_DEFEAT_SCENES
            scene = BOSS_DEFEAT_SCENES.get(boss["id"])
            if scene:
                defeat_embed = discord.Embed(
                    title=scene["title"],
                    description="\n\n".join(scene["lines"]),
                    color=COLORS.get(scene["color"], COLORS.get("ancient", COLORS["legendary"]))
                )
                if boss.get("image_url"):
                    defeat_embed.set_image(url=boss["image_url"])
                await channel.send(embed=defeat_embed)

            embed = discord.Embed(
                title=f"💀 {boss['name']} — The Party Falls",
                description=(
                    f"*The Ancient was not defeated.*\n\n"
                    f"*Regroup. Grow stronger. Return.*"
                ),
                color=COLORS["error"]
            )

        reward_lines = []
        CATCH_CHANCES = {1: 0.10, 2: 0.06, 3: 0.03}

        if defeated:
            for i, (user_id, damage) in enumerate(sorted_participants[:10]):
                rank = i + 1
                medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "🏅"
                gold = max(500, int(damage * 0.05))
                shards = max(5, 20 - (rank * 2))

                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE players SET gold = gold + ?, celestial_shards = celestial_shards + ? WHERE user_id = ?",
                        (gold, shards, user_id)
                    )
                    await db.commit()

                # Loot drop
                loot_line = ""
                if random.random() < (0.9 - (i * 0.08)):
                    loot = random.choice(boss["loot_table"])
                    from utils.db import add_item
                    await add_item(user_id, loot)
                    loot_line = f" | 🎁 {loot.replace('_',' ').title()}"

                reward_lines.append(
                    f"{medal} <@{user_id}> — `{damage:,}` dmg | +{gold:,}💰 | +{shards}🔮{loot_line}"
                )

                # Catch chance for top 3
                catch_chance = CATCH_CHANCES.get(rank, 0)
                if catch_chance and random.random() < catch_chance:
                    boss_beast_data = get_beast_data(boss["id"])
                    if boss_beast_data:
                        from utils.db import add_beast_to_player
                        await add_beast_to_player(user_id, {**boss_beast_data, "caught_from": "ancient_raid"})

                        member = channel.guild.get_member(user_id)
                        name = member.display_name if member else f"<@{user_id}>"
                        await channel.send(embed=discord.Embed(
                            title="🏛️ ✨ AN ANCIENT HAS BEEN CAUGHT! ✨ 🏛️",
                            description=(
                                f"*In the moment of defeat, the primordial force is stilled.*\n\n"
                                f"🌟 **{name}** has caught **{boss_beast_data['name']}** — *{boss_beast_data['title']}*!\n\n"
                                f"*{boss_beast_data['description']}*\n\n"
                                f"**Ancient** form — obtainable only by defeating Ancient bosses."
                            ),
                            color=COLORS.get("ancient", COLORS["legendary"])
                        ))

                        unlocked = await check_achievements(user_id)
                        if unlocked and member:
                            await notify_unlocks(channel, member, unlocked)

        else:
            # Timed out — consolation for top participants
            for i, (user_id, damage) in enumerate(sorted_participants[:5]):
                gold = max(100, int(damage * 0.02))
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "UPDATE players SET gold = gold + ? WHERE user_id = ?",
                        (gold, user_id)
                    )
                    await db.commit()
                reward_lines.append(f"🏅 <@{user_id}> — `{damage:,}` dmg | +{gold:,}💰 consolation")

        if reward_lines:
            embed.add_field(
                name="🏆 Party Results",
                value="\n".join(reward_lines),
                inline=False
            )

        # Update DB status
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE raids SET status = ?, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("completed" if defeated else "failed", raid_id)
            )
            await db.commit()

        await channel.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Ancient(bot))
