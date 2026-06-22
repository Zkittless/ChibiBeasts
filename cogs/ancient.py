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
SHARD_COST    = 25    # Celestial Shards to trigger
MIN_LEVEL     = 10    # minimum trainer level


class Ancient(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /ancient ─────────────────────────────────────────────────────────
    @app_commands.command(name="ancient", description="Summon an Ancient boss — open to all players! 🏛️")
    async def ancient(self, interaction: discord.Interaction):
        await interaction.response.defer()

        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        # Level check
        if player["level"] < MIN_LEVEL:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You need to be at least **Level {MIN_LEVEL}** to summon an Ancient.",
                color=COLORS["error"]
            ))

        # Shard cost
        if player["celestial_shards"] < SHARD_COST:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Summoning an Ancient costs **{SHARD_COST} Celestial Shards**. You have `{player['celestial_shards']}`.",
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
                    description="✦ An Ancient raid is already active here! Use `/ancient_attack`.",
                    color=COLORS["error"]
                ))

        # Deduct shards
        await update_player(interaction.user.id, celestial_shards=player["celestial_shards"] - SHARD_COST)

        boss = random.choice(ANCIENT_BOSSES)

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
                    self.stop()

            async def on_timeout(self):
                pass  # handled below after send

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
            embed.set_footer(text=f"Summoned by {interaction.user.display_name} · {SHARD_COST} 🔮 shards spent")
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

        active_ancient_raids[raid_id] = {
            "boss": boss,
            "current_hp": boss["max_hp"],
            "max_hp": boss["max_hp"],
            "participants": {},
            "party": set(view.party.keys()),
            "channel_id": interaction.channel_id,
            "channel": interaction.channel,
            "raid_message": None,
            "attack_counts": {},
            "embed_updating": False,
            "last_attack": {},
        }
        _ancient_locks[raid_id] = asyncio.Lock()

        party_preview = ", ".join(list(view.party.values())[:5]) + ("..." if len(view.party) > 5 else "")
        ATTACK_COOLDOWN = 0.5

        def build_ancient_embed(current_hp: int, participants: dict = None) -> discord.Embed:
            pct = current_hp / boss["max_hp"]
            status = "🔴 CRITICAL" if pct < 0.15 else "🟠 Weakened" if pct < 0.40 else "🟡 Damaged" if pct < 0.70 else "🟢 Active"
            embed = discord.Embed(
                title=f"🏛️ ANCIENT RAID — {boss['name']}!",
                description=(
                    f"*The primordial beast manifests. All {len(view.party)} summoners, engage.*\n\n"
                    f"*{boss['description']}*\n\n"
                    f"💀 **HP:** {hp_bar(current_hp, boss['max_hp'])} {status}\n"
                    f"`{current_hp:,} / {boss['max_hp']:,}`\n\n"
                    f"🏆 Top 3 damage dealers can catch the boss itself!"
                ),
                color=COLORS.get("ancient", COLORS["legendary"])
            )
            if participants:
                top = sorted(participants.items(), key=lambda x: x[1], reverse=True)[:5]
                medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                lines = [f"{medals[i]} <@{uid}> — `{dmg:,}` dmg" for i, (uid, dmg) in enumerate(top)]
                embed.add_field(name="⚔️ Damage Dealt", value="\n".join(lines), inline=False)
            if boss.get("image_url"):
                embed.set_image(url=boss["image_url"])
            embed.set_footer(text=f"Raid ID: #{raid_id} | Party: {party_preview}")
            return embed

        class AncientRaidView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=1800)

            @discord.ui.button(label="⚔️ Attack!", style=discord.ButtonStyle.danger, emoji="💥")
            async def attack_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                import time
                await btn_interaction.response.defer(ephemeral=True, thinking=False)

                if raid_id not in active_ancient_raids:
                    return await btn_interaction.followup.send("✦ The raid has ended!", ephemeral=True)

                cur_raid = active_ancient_raids[raid_id]
                uid = btn_interaction.user.id
                if uid not in cur_raid["party"]:
                    return await btn_interaction.followup.send(
                        "✦ You're not in this party! Join the next lobby with `/ancient`.", ephemeral=True
                    )

                # Per-player cooldown
                now = time.monotonic()
                last = cur_raid["last_attack"].get(uid, 0)
                if now - last < ATTACK_COOLDOWN:
                    remaining = ATTACK_COOLDOWN - (now - last)
                    return await btn_interaction.followup.send(f"⏱️ Wait `{remaining:.1f}s`.", ephemeral=True)
                cur_raid["last_attack"][uid] = now

                active = await get_active_beast(uid)
                if not active:
                    return await btn_interaction.followup.send("✦ You need an active beast! Use `/setactive`.", ephemeral=True)

                beast_data_btn = get_beast_data(active["beast_id"])
                damage = random.randint(int(active["attack"] * 0.8), int(active["attack"] * 1.5))
                is_crit = random.random() < 0.15
                if is_crit:
                    damage = int(damage * 1.5)

                raid_lock = _ancient_locks.get(raid_id)
                if not raid_lock:
                    return await btn_interaction.followup.send("✦ The raid just ended!", ephemeral=True)

                async with raid_lock:
                    if raid_id not in active_ancient_raids:
                        return await btn_interaction.followup.send("✦ The raid just ended!", ephemeral=True)
                    cur_raid = active_ancient_raids[raid_id]
                    cur_raid["current_hp"] = max(0, cur_raid["current_hp"] - damage)
                    cur_raid["participants"][uid] = cur_raid["participants"].get(uid, 0) + damage
                    cur_raid["attack_counts"][uid] = cur_raid["attack_counts"].get(uid, 0) + 1
                    async with aiosqlite.connect(DB_PATH) as db:
                        await db.execute("UPDATE raids SET current_hp = ? WHERE id = ?", (cur_raid["current_hp"], raid_id))
                        async with db.execute(
                            "SELECT damage_dealt FROM raid_participants WHERE raid_id = ? AND user_id = ?",
                            (raid_id, uid)
                        ) as c:
                            existing = await c.fetchone()
                        if existing:
                            await db.execute(
                                "UPDATE raid_participants SET damage_dealt = damage_dealt + ? WHERE raid_id = ? AND user_id = ?",
                                (damage, raid_id, uid)
                            )
                        else:
                            await db.execute(
                                "INSERT INTO raid_participants (raid_id, user_id, damage_dealt) VALUES (?, ?, ?)",
                                (raid_id, uid, damage)
                            )
                        await db.commit()
                    raid_ended = cur_raid["current_hp"] <= 0
                    current_hp_snap = cur_raid["current_hp"]
                    participants_snap = dict(cur_raid["participants"])

                # Deferred ephemeral with no followup simply never shows — no cleanup needed

                # Throttled embed update with live leaderboard
                if not cur_raid.get("embed_updating", False):
                    cur_raid["embed_updating"] = True
                    raid_msg = cur_raid.get("raid_message")
                    if raid_msg:
                        try:
                            await raid_msg.edit(embed=build_ancient_embed(current_hp_snap, participants_snap), view=self if not raid_ended else None)
                        except discord.HTTPException:
                            pass
                        finally:
                            if raid_id in active_ancient_raids:
                                active_ancient_raids[raid_id]["embed_updating"] = False

                await track_quest_event(uid, "raid_damage", amount=damage)
                await advance_quest_step(uid, "raid_participate")

                if raid_ended:
                    await self.end_ancient_raid(raid_id, btn_interaction.channel)

            async def on_timeout(self):
                if raid_id in active_ancient_raids:
                    await self.end_ancient_raid(raid_id, interaction.channel, timed_out=True)

        raid_view = AncientRaidView()
        raid_msg = await interaction.channel.send(embed=build_ancient_embed(boss["max_hp"], {}), view=raid_view)
        active_ancient_raids[raid_id]["raid_message"] = raid_msg

        # Auto-end after 30 minutes
        await asyncio.sleep(1800)
        if raid_id in active_ancient_raids:
            await self.end_ancient_raid(raid_id, interaction.channel, timed_out=True)

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
        defeated = not timed_out and raid["current_hp"] <= 0

        sorted_participants = sorted(raid["participants"].items(), key=lambda x: x[1], reverse=True)

        embed = discord.Embed(
            title=f"🏛️ {'Ancient Defeated!' if defeated else 'Ancient Escaped...'}",
            description=(
                f"**{boss['name']}** has been {'defeated' if defeated else 'driven off'}.\n\n"
                f"*{'The primordial force is subdued — for now.' if defeated else 'The Ancient retreats into the void. Regroup and try again.'}*"
            ),
            color=COLORS["success"] if defeated else COLORS["error"]
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
