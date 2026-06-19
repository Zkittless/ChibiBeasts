import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
import asyncio
from utils.db import get_or_create_player, get_player, update_player, get_beast_data, get_active_beast
from utils.theme import COLORS, RARITY_EMOJI, hp_bar, SPARKLE
from utils.progress import (
    track_quest_event, check_achievements, unlock_simple_achievement, notify_unlocks, notify_quest_completions
)
from cogs.questline import advance_quest_step

GUILD_LEVEL_PERKS = {
    1:  {"max_members": 10,  "unlocks": "Guild created!"},
    5:  {"max_members": 20,  "unlocks": "Corrupted Raids"},
    10: {"max_members": 30,  "unlocks": "Ancient Raids + Guild Shop"},
    15: {"max_members": 40,  "unlocks": "Guild Leaderboard Bonuses"},
    20: {"max_members": 50,  "unlocks": "Exclusive Titles"},
    25: {"max_members": 50,  "unlocks": "Guild Ancient Divine Raids"},
}

RAID_BOSSES = {
    "corrupted": [
        {"id": "corrupted_leviathan", "name": "Corrupted Leviathan", "type": "corrupted",
         "max_hp": 50000, "attack": 800, "image_url": "",
         "description": "A once-great sea sovereign, now twisted by dark energy into something monstrous.",
         "loot_table": ["phoenix_elixir", "luna_nectar", "star_candy_shards"],
         "min_guild_level": 5},
        {"id": "corrupted_fenrir", "name": "Corrupted Fenrir", "type": "corrupted",
         "max_hp": 45000, "attack": 900, "image_url": "",
         "description": "The World Eater consumed by void energy, its howl now tears rifts in reality.",
         "loot_table": ["phoenix_elixir", "nebula_macaron", "krakenshale_brew"],
         "min_guild_level": 5},
        {"id": "corrupted_dragon", "name": "Corrupted Dragon", "type": "corrupted",
         "max_hp": 55000, "attack": 850, "image_url": "",
         "description": "The apex predator twisted into a creature of pure destructive energy.",
         "loot_table": ["ambrosia_tart", "phoenix_elixir", "star_candy_shards"],
         "min_guild_level": 5},
    ],
    "ancient": [
        {"id": "ancient_chronos", "name": "Ancient Chronos", "type": "ancient",
         "max_hp": 150000, "attack": 2000, "image_url": "",
         "description": "The primordial form of the Epoch Hare, existing before time itself had a name.",
         "loot_table": ["tear_of_leviathan", "genesis_fruit", "ambrosia_tart", "sunforge_core"],
         "altered_divine": "void_chronos",
         "min_guild_level": 10},
        {"id": "ancient_genesis", "name": "Ancient Genesis", "type": "ancient",
         "max_hp": 160000, "attack": 2200, "image_url": "",
         "description": "The original Origin Phoenix, carrying the flame of the universe's first moment.",
         "loot_table": ["genesis_fruit", "cosmic_singularity_soda", "tear_of_leviathan"],
         "altered_divine": "fractured_genesis",
         "min_guild_level": 10},
        {"id": "ancient_abyss", "name": "Ancient Abyss", "type": "ancient",
         "max_hp": 140000, "attack": 2100, "image_url": "",
         "description": "The Dark Matter Panther in its oldest form, a void that predates existence.",
         "loot_table": ["tear_of_leviathan", "ambrosia_tart", "genesis_fruit"],
         "altered_divine": "abyssal_nebula",
         "min_guild_level": 10},
    ]
}

ALTERED_DIVINES = {
    "void_chronos": {
        "name": "Void Chronos", "base_beast": "chronos",
        "description": "A shattered version of Chronos existing outside of time, its clock ticking backwards.",
        "unique_moves": ["Time Fracture", "Void Epoch"],
        "unique_ultimate": "Temporal Collapse",
        "stat_modifier": 1.25
    },
    "fractured_genesis": {
        "name": "Fractured Genesis", "base_beast": "genesis",
        "description": "The Origin Phoenix broken across infinite realities, burning with impossible colors.",
        "unique_moves": ["Null Creation", "Reality Shard"],
        "unique_ultimate": "Genesis Implosion",
        "stat_modifier": 1.25
    },
    "abyssal_nebula": {
        "name": "Abyssal Nebula", "base_beast": "abyss",
        "description": "The Dark Matter Panther merged with the void between stars, consuming light itself.",
        "unique_moves": ["Event Horizon", "Star Death"],
        "unique_ultimate": "Cosmic Annihilation",
        "stat_modifier": 1.25
    },
}

active_raids: dict[int, dict] = {}

# Per-raid asyncio locks prevent concurrent /raid_attack coroutines from
# reading a stale HP value and both concluding they delivered the killing blow.
# A lock is created when a raid starts and removed when end_raid fires.
# asyncio.Lock() is not reentrant — end_raid must not be called while holding it.
_raid_locks: dict[int, asyncio.Lock] = {}

class Guilds(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="guild_create", description="Create a new guild 🏰")
    @app_commands.describe(name="Guild name", description="Guild description")
    async def guild_create(self, interaction: discord.Interaction, name: str, description: str = ""):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))

        async with aiosqlite.connect("db/chibibeast.db") as db:
            async with db.execute(
                "SELECT id FROM guilds WHERE leader_id = ?", (interaction.user.id,)
            ) as c:
                existing_leader = await c.fetchone()
            async with db.execute(
                "SELECT guild_id FROM guild_members WHERE user_id = ?", (interaction.user.id,)
            ) as c:
                existing_member = await c.fetchone()

        if existing_leader or existing_member:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You're already in a guild! Leave it first with `/guild_leave`.",
                color=COLORS["error"]
            ))

        cost = 1000
        if player["gold"] < cost:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Creating a guild costs `{cost:,} gold`. You have `{player['gold']:,}`.",
                color=COLORS["error"]
            ))

        async with aiosqlite.connect("db/chibibeast.db") as db:
            try:
                await db.execute(
                    "INSERT INTO guilds (name, description, leader_id) VALUES (?, ?, ?)",
                    (name, description, interaction.user.id)
                )
                await db.commit()
                async with db.execute("SELECT last_insert_rowid()") as c:
                    guild_id = (await c.fetchone())[0]
                await db.execute(
                    "INSERT INTO guild_members (guild_id, user_id, rank) VALUES (?, ?, 'leader')",
                    (guild_id, interaction.user.id)
                )
                await db.execute("UPDATE players SET guild_id = ? WHERE user_id = ?", (guild_id, interaction.user.id))
                await db.commit()
            except Exception as e:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Guild name already taken! Try a different name.",
                    color=COLORS["error"]
                ))

        await update_player(interaction.user.id, gold=player["gold"] - cost)
        await interaction.followup.send(embed=discord.Embed(
            title=f"🏰 Guild **{name}** Created!",
            description=(
                f"You've founded **{name}**!\n\n"
                f"💰 Spent `{cost:,} gold`\n"
                f"👑 You are the Guild Leader\n"
                f"🐾 Invite members with `/guild_invite`\n"
                f"⚔️ Trigger raids with `/raid` once you reach Guild Level 5!"
            ),
            color=COLORS["legendary"]
        ))
        guild_unlocked = await unlock_simple_achievement(interaction.user.id, "first_guild")
        if guild_unlocked:
            await notify_unlocks(interaction.channel, interaction.user, ["first_guild"])

    @app_commands.command(name="guild", description="View your guild info 🏰")
    async def guild(self, interaction: discord.Interaction):
        await interaction.response.defer()
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT guild_id FROM guild_members WHERE user_id = ?", (interaction.user.id,)
            ) as c:
                member_row = await c.fetchone()

        if not member_row:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You're not in a guild! Create one with `/guild_create` or ask to be invited.",
                color=COLORS["info"]
            ))

        guild_id = member_row["guild_id"]
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM guilds WHERE id = ?", (guild_id,)) as c:
                guild = dict(await c.fetchone())
            async with db.execute(
                "SELECT gm.*, p.username FROM guild_members gm JOIN players p ON gm.user_id = p.user_id WHERE gm.guild_id = ?",
                (guild_id,)
            ) as c:
                members = [dict(r) for r in await c.fetchall()]

        next_level = guild["level"] + 1
        next_level_info = GUILD_LEVEL_PERKS.get(next_level, {})
        exp_needed = guild["level"] * 500

        embed = discord.Embed(
            title=f"🏰 {guild['name']}",
            description=guild["description"] or "*No description set.*",
            color=COLORS["legendary"]
        )
        embed.add_field(
            name="📊 Guild Stats",
            value=(
                f"⭐ **Level:** {guild['level']}\n"
                f"✨ **EXP:** `{guild['exp']}/{exp_needed}`\n"
                f"🎟️ **Guild Tokens:** `{guild['guild_tokens']}`\n"
                f"👥 **Members:** {guild['member_count']}/{guild['max_members']}"
            ),
            inline=True
        )
        embed.add_field(
            name="🔓 Next Unlock",
            value=f"Level {next_level}: {next_level_info.get('unlocks', 'Max level reached!')}" if next_level_info else "Max level reached! 👑",
            inline=True
        )

        member_list = "\n".join(
            f"{'👑' if m['rank'] == 'leader' else '⚔️' if m['rank'] == 'officer' else '🐾'} {m['username']}"
            for m in members[:10]
        )
        embed.add_field(name="👥 Members", value=member_list or "No members", inline=False)
        embed.set_footer(text="ChibiBeasts 🐾  •  /raid to trigger a raid boss!")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="guild_invite", description="Invite a player to your guild 📨")
    @app_commands.describe(member="Player to invite")
    async def guild_invite(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT guild_id, rank FROM guild_members WHERE user_id = ?", (interaction.user.id,)
            ) as c:
                inviter = await c.fetchone()

        if not inviter or inviter["rank"] not in ["leader", "officer"]:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need to be a Guild Leader or Officer to invite members!",
                color=COLORS["error"]
            ))

        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM guilds WHERE id = ?", (inviter["guild_id"],)) as c:
                guild = dict(await c.fetchone())
            async with db.execute(
                "SELECT guild_id FROM guild_members WHERE user_id = ?", (member.id,)
            ) as c:
                already_in = await c.fetchone()

        if already_in:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ **{member.display_name}** is already in a guild!",
                color=COLORS["error"]
            ))

        if guild["member_count"] >= guild["max_members"]:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Your guild is full!", color=COLORS["error"]
            ))

        embed = discord.Embed(
            title="🏰 Guild Invitation!",
            description=f"**{interaction.user.display_name}** has invited you to join **{guild['name']}**!\n\nDo you accept?",
            color=COLORS["legendary"]
        )

        class InviteView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)
                self.accepted = False

            @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="✅")
            async def accept(self, inv_interaction: discord.Interaction, button: discord.ui.Button):
                if inv_interaction.user.id != member.id:
                    return await inv_interaction.response.send_message("This isn't for you!", ephemeral=True)
                self.accepted = True
                self.stop()
                async with aiosqlite.connect("db/chibibeast.db") as db:
                    await db.execute(
                        "INSERT INTO guild_members (guild_id, user_id) VALUES (?, ?)",
                        (inviter["guild_id"], member.id)
                    )
                    await db.execute(
                        "UPDATE guilds SET member_count = member_count + 1 WHERE id = ?",
                        (inviter["guild_id"],)
                    )
                    await db.execute(
                        "UPDATE players SET guild_id = ? WHERE user_id = ?",
                        (inviter["guild_id"], member.id)
                    )
                    await db.commit()
                for item in self.children:
                    item.disabled = True
                await inv_interaction.response.edit_message(view=self)
                await inv_interaction.followup.send(embed=discord.Embed(
                    description=f"✦ **{member.display_name}** joined **{guild['name']}**! 🏰",
                    color=COLORS["success"]
                ))
                join_unlocked = await unlock_simple_achievement(member.id, "first_guild")
                if join_unlocked:
                    await notify_unlocks(inv_interaction.channel, member, ["first_guild"])

            @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger, emoji="❌")
            async def decline(self, inv_interaction: discord.Interaction, button: discord.ui.Button):
                if inv_interaction.user.id != member.id:
                    return await inv_interaction.response.send_message("This isn't for you!", ephemeral=True)
                self.stop()
                for item in self.children:
                    item.disabled = True
                await inv_interaction.response.edit_message(view=self)

        view = InviteView()
        await interaction.followup.send(content=member.mention, embed=embed, view=view)

    @app_commands.command(name="raid", description="Trigger a raid boss battle! ⚔️")
    @app_commands.choices(raid_type=[
        app_commands.Choice(name="⚔️ Corrupted Raid", value="corrupted"),
        app_commands.Choice(name="👑 Ancient Raid", value="ancient"),
    ])
    async def raid(self, interaction: discord.Interaction, raid_type: str = "corrupted"):
        await interaction.response.defer()
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT gm.rank, g.* FROM guild_members gm JOIN guilds g ON gm.guild_id = g.id WHERE gm.user_id = ?",
                (interaction.user.id,)
            ) as c:
                guild_data = await c.fetchone()

        if not guild_data:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need to be in a guild to trigger raids!", color=COLORS["error"]
            ))

        guild_data = dict(guild_data)

        if guild_data["rank"] not in ["leader", "officer"]:
            if raid_type == "ancient" and guild_data["rank"] != "leader":
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ Only Guild Leaders can trigger Ancient Raids!", color=COLORS["error"]
                ))

        min_level = 5 if raid_type == "corrupted" else 10
        if guild_data["level"] < min_level:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Your guild needs to be Level {min_level} to trigger {raid_type.capitalize()} Raids!",
                color=COLORS["error"]
            ))

        token_cost = 50 if raid_type == "corrupted" else 150
        if guild_data["guild_tokens"] < token_cost:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Triggering this raid costs `{token_cost}` Guild Tokens. You have `{guild_data['guild_tokens']}`.",
                color=COLORS["error"]
            ))

        # Pick random boss
        boss = random.choice(RAID_BOSSES[raid_type])

        # Lore-flavored intro lines — the Sundering framing from the bible
        SUNDERING_LINES = [
            "Something in the Loom snapped. The thread didn't finish weaving — and now it's here.",
            "The Loom tried to make something too large, too quickly. The result is in front of you now.",
            "An Altered Divine has torn through. It isn't evil — it's unfinished, and it's in pain. "
            "Fight it down long enough for the Loom to recapture the thread.",
            "The weave split. What came loose is trying to finish becoming itself the wrong way. "
            "Your guild needs to hold it steady.",
        ]
        sundering_line = random.choice(SUNDERING_LINES)

        async with aiosqlite.connect("db/chibibeast.db") as db:
            await db.execute(
                "UPDATE guilds SET guild_tokens = guild_tokens - ? WHERE id = ?",
                (token_cost, guild_data["id"])
            )
            await db.execute("""
                INSERT INTO raids (boss_id, boss_name, boss_type, max_hp, current_hp, guild_id, channel_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (boss["id"], boss["name"], raid_type, boss["max_hp"], boss["max_hp"], guild_data["id"], interaction.channel_id))
            await db.commit()
            async with db.execute("SELECT last_insert_rowid()") as c:
                raid_id = (await c.fetchone())[0]

        active_raids[raid_id] = {
            "boss": boss, "current_hp": boss["max_hp"],
            "max_hp": boss["max_hp"], "participants": {},
            "guild_id": guild_data["id"], "channel": interaction.channel
        }
        _raid_locks[raid_id] = asyncio.Lock()  # one lock per raid, cleared in end_raid

        embed = discord.Embed(
            title=f"⚠️ RAID ALERT — {boss['name']}!",
            description=(
                f"*{sundering_line}*\n\n"
                f"**{interaction.guild.name}**, an **Altered Divine** has emerged: **{boss['name']}**.\n"
                f"*{boss['description']}*\n\n"
                f"💀 **HP:** {hp_bar(boss['max_hp'], boss['max_hp'])}\n\n"
                f"Use `/raid_attack` to deal damage! The raid lasts 30 minutes.\n"
                f"🏆 Top damage dealers get bonus loot — and a chance to catch what it was always meant to be."
            ),
            color=COLORS["altered_divine"] if raid_type == "ancient" else COLORS["epic"]
        )
        embed.set_footer(text=f"Raid ID: #{raid_id} | Triggered by {interaction.user.display_name}")
        await interaction.followup.send(embed=embed)

        # Auto-end raid after 30 minutes
        await asyncio.sleep(1800)
        if raid_id in active_raids:
            await self.end_raid(raid_id, interaction.channel, timed_out=True)

    @app_commands.command(name="raid_attack", description="Attack the active raid boss! ⚔️")
    async def raid_attack(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Find active raid in this guild
        player_guild = None
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT guild_id FROM guild_members WHERE user_id = ?", (interaction.user.id,)
            ) as c:
                member = await c.fetchone()

        if not member:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need to be in a guild to participate in raids!", color=COLORS["error"]
            ))

        raid_id = None
        for rid, raid in active_raids.items():
            if raid["guild_id"] == member["guild_id"]:
                raid_id = rid
                break

        if not raid_id:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ No active raid for your guild! Ask your Guild Leader to trigger one.",
                color=COLORS["error"]
            ))

        raid = active_raids[raid_id]
        active = await get_active_beast(interaction.user.id)
        if not active:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need an active beast to attack!", color=COLORS["error"]
            ))

        beast_data = get_beast_data(active["beast_id"])
        damage = random.randint(
            int(active["attack"] * 0.8),
            int(active["attack"] * 1.5)
        )
        is_crit = random.random() < 0.15
        if is_crit:
            damage = int(damage * 1.5)

        # ── Atomic HP mutation under per-raid lock ───────────────────────────
        # Multiple concurrent /raid_attack coroutines can interleave between
        # the HP read and write. The lock serialises all damage applications so
        # only one coroutine mutates raid state at a time. We release the lock
        # before sending Discord responses to avoid holding it during I/O.
        raid_ended = False
        raid_lock  = _raid_locks.get(raid_id)
        if raid_lock is None:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ The raid just ended!", color=COLORS["info"]
            ))

        async with raid_lock:
            # Re-check inside the lock — another coroutine may have ended it
            if raid_id not in active_raids:
                return await interaction.followup.send(embed=discord.Embed(
                    description="✦ The raid just ended!", color=COLORS["info"]
                ))

            raid["current_hp"] = max(0, raid["current_hp"] - damage)
            raid["participants"][interaction.user.id] = (
                raid["participants"].get(interaction.user.id, 0) + damage
            )

            async with aiosqlite.connect("db/chibibeast.db") as db:
                await db.execute(
                    "UPDATE raids SET current_hp = ? WHERE id = ?",
                    (raid["current_hp"], raid_id)
                )
                async with db.execute(
                    "SELECT damage_dealt FROM raid_participants WHERE raid_id = ? AND user_id = ?",
                    (raid_id, interaction.user.id)
                ) as c:
                    existing = await c.fetchone()
                if existing:
                    await db.execute(
                        "UPDATE raid_participants SET damage_dealt = damage_dealt + ? WHERE raid_id = ? AND user_id = ?",
                        (damage, raid_id, interaction.user.id)
                    )
                else:
                    await db.execute(
                        "INSERT INTO raid_participants (raid_id, user_id, damage_dealt) VALUES (?, ?, ?)",
                        (raid_id, interaction.user.id, damage)
                    )
                await db.commit()

            raid_ended              = raid["current_hp"] <= 0
            current_hp_snapshot     = raid["current_hp"]
            max_hp_snapshot         = raid["max_hp"]
            boss_name_snapshot      = raid["boss"]["name"]
            beast_name_snapshot     = beast_data["name"] if beast_data else "Beast"

        # Lock released — safe to do Discord I/O now
        embed = discord.Embed(
            title=f"⚔️ {beast_name_snapshot} attacks {boss_name_snapshot}!",
            description=(
                f"{'⭐ CRITICAL HIT! ' if is_crit else ''}Dealt **`{damage:,}`** damage!\n\n"
                f"💀 **{boss_name_snapshot} HP:**\n"
                f"{hp_bar(current_hp_snapshot, max_hp_snapshot)}"
            ),
            color=COLORS["epic"]
        )
        await interaction.followup.send(embed=embed)

        # ── Quest tracking: raid damage counts toward the boss-buster quest ──
        raid_quests_completed = await track_quest_event(interaction.user.id, "raid_damage", amount=damage)
        await notify_quest_completions(interaction.channel, raid_quests_completed)
        # Questline: count as raid participation
        await advance_quest_step(interaction.user.id, "raid_participate")

        if raid_ended:
            await self.end_raid(raid_id, interaction.channel)

    async def end_raid(self, raid_id: int, channel, timed_out: bool = False):
        if raid_id not in active_raids:
            return
        raid = active_raids.pop(raid_id)
        _raid_locks.pop(raid_id, None)  # clean up lock — raid is over
        boss = raid["boss"]
        defeated = not timed_out and raid["current_hp"] <= 0

        sorted_participants = sorted(raid["participants"].items(), key=lambda x: x[1], reverse=True)

        if defeated:
            embed = discord.Embed(
                title=f"🏆 **{boss['name']}** Defeated!",
                description=f"*The raid boss has been slain! Distributing rewards...*",
                color=COLORS["legendary"]
            )
        else:
            embed = discord.Embed(
                title=f"⏰ Raid Expired — {boss['name']} Escaped!",
                description="*The raid boss escaped before being defeated.*",
                color=COLORS["error"]
            )

        # Rewards
        reward_lines = []
        for i, (user_id, damage) in enumerate(sorted_participants[:10]):
            member = channel.guild.get_member(user_id)
            if not member:
                continue
            rank = i + 1
            gold = 500 if rank == 1 else 300 if rank == 2 else 200 if rank == 3 else 100
            exp = 200 if rank == 1 else 150 if rank <= 3 else 80

            if defeated:
                await update_player(user_id, gold=(await get_player(user_id))["gold"] + gold)

                # ── Achievement tracking: raid victory + any stat thresholds crossed ──
                raid_win_unlocked = await unlock_simple_achievement(user_id, "first_raid_win")
                stat_unlocked = await check_achievements(user_id)
                all_raid_unlocks = (["first_raid_win"] if raid_win_unlocked else []) + stat_unlocked
                if all_raid_unlocks:
                    await notify_unlocks(channel, member, all_raid_unlocks)

                # Loot drop
                if random.random() < (0.8 - (i * 0.1)):
                    loot = random.choice(boss["loot_table"])
                    from utils.db import add_item
                    await add_item(user_id, loot)
                    reward_lines.append(f"{'🥇' if rank==1 else '🥈' if rank==2 else '🥉' if rank==3 else '🏅'} **{member.display_name}** — `{damage:,}` dmg | +{gold}💰 | 🎁 {loot.replace('_',' ').title()}")
                else:
                    reward_lines.append(f"{'🥇' if rank==1 else '🥈' if rank==2 else '🥉' if rank==3 else '🏅'} **{member.display_name}** — `{damage:,}` dmg | +{gold}💰")

                # Altered Divine catch chance for top 3
                altered_id = boss.get("altered_divine")
                if altered_id and rank <= 3:
                    catch_chance = 0.001 * (4 - rank)
                    if random.random() < catch_chance:
                        altered = ALTERED_DIVINES.get(altered_id)
                        if altered:
                            # Per LORE.md §III: a successful raid doesn't kill the Altered Divine —
                            # it finally finishes being born correctly. The trainer catches the
                            # *purified* divine entity (base_beast), not the unstable phenomenon.
                            # beast_id → the completed divine's canonical ID
                            # rarity  → "divine" (it finished; is_altered_divine=1 flags the origin)
                            purified_id = altered["base_beast"]
                            purified_data = get_beast_data(purified_id)
                            if not purified_data:
                                continue

                            async with aiosqlite.connect("db/chibibeast.db") as db:
                                await db.execute("""
                                    INSERT INTO player_beasts
                                    (user_id, beast_id, hp, max_hp, attack, defense, speed, mana, max_mana,
                                     rarity, is_altered_divine, altered_name, caught_from)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'divine', 1, ?, 'raid')
                                """, (
                                    user_id,
                                    purified_id,
                                    int(purified_data["base_stats"]["hp"]     * altered["stat_modifier"]),
                                    int(purified_data["base_stats"]["hp"]     * altered["stat_modifier"]),
                                    int(purified_data["base_stats"]["attack"] * altered["stat_modifier"]),
                                    int(purified_data["base_stats"]["defense"]* altered["stat_modifier"]),
                                    int(purified_data["base_stats"]["speed"]  * altered["stat_modifier"]),
                                    int(purified_data["base_stats"]["mana"]   * altered["stat_modifier"]),
                                    int(purified_data["base_stats"]["mana"]   * altered["stat_modifier"]),
                                    altered["name"]  # altered_name preserves the raid encounter name for lore
                                ))
                                # Historical record: log the unstable form for lore continuity
                                await db.execute(
                                    "INSERT INTO altered_divines (beast_id, altered_name, caught_by, server_id, raid_id) VALUES (?, ?, ?, ?, ?)",
                                    (purified_id, altered["name"], user_id, channel.guild.id, raid_id)
                                )
                                await db.commit()

                            # Record in server bestiary as the purified divine
                            from utils.progress import record_bestiary_sighting
                            await record_bestiary_sighting(channel.guild.id, purified_id, user_id)

                            await channel.send(embed=discord.Embed(
                                title="🌸 ✨ THE LOOM FINISHED THE WEAVE ✨ 🌸",
                                description=(
                                    f"*The loose thread is recaptured. The Altered Divine finishes being born — correctly, this time.*\n\n"
                                    f"🌌 **{member.display_name}** has caught **{purified_data['name']}** — *{purified_data['title']}*!\n\n"
                                    f"*{purified_data['description']}*\n\n"
                                    f"Previously: **{altered['name']}** — now resolved into its true form.\n"
                                    f"Unique raid moves preserved: **{' | '.join(altered['unique_moves'])}**"
                                ),
                                color=COLORS["divine"]
                            ))
                            altered_unlocked = await unlock_simple_achievement(user_id, "first_altered_divine")
                            if altered_unlocked:
                                await notify_unlocks(channel, member, ["first_altered_divine"])

        if reward_lines:
            embed.add_field(name="🏆 Top Damage Dealers", value="\n".join(reward_lines), inline=False)

        embed.set_footer(text="ChibiBeasts 🐾  •  /raid to trigger another raid!")
        await channel.send(embed=embed)

        # Update raid status
        async with aiosqlite.connect("db/chibibeast.db") as db:
            await db.execute(
                "UPDATE raids SET status = ?, ended_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("completed" if defeated else "expired", raid_id)
            )
            await db.commit()

async def setup(bot: commands.Bot):
    await bot.add_cog(Guilds(bot))
