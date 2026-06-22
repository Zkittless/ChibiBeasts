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
         "max_hp": 50000, "attack": 800, "defense": 120, "image_url": "",
         "description": "A once-great sea sovereign, now twisted by dark energy into something monstrous.",
         "loot_table": ["phoenix_elixir", "luna_nectar", "star_candy_shards", "void_prism"],
         "min_guild_level": 5},
        {"id": "corrupted_fenrir", "name": "Corrupted Fenrir", "type": "corrupted",
         "max_hp": 45000, "attack": 900, "defense": 130, "image_url": "",
         "description": "The World Eater consumed by void energy, its howl now tears rifts in reality.",
         "loot_table": ["phoenix_elixir", "nebula_macaron", "krakenshale_brew", "epoch_shard"],
         "min_guild_level": 5},
        {"id": "corrupted_dragon", "name": "Corrupted Dragon", "type": "corrupted",
         "max_hp": 55000, "attack": 850, "defense": 140, "image_url": "",
         "description": "The apex predator twisted into a creature of pure destructive energy.",
         "loot_table": ["ambrosia_tart", "phoenix_elixir", "star_candy_shards", "firstborn_ember"],
         "min_guild_level": 5},
    ],
    "ancient": [
        {"id": "ancient_chronos", "name": "Ancient Chronos", "type": "ancient",
         "max_hp": 150000, "attack": 2000, "defense": 300, "image_url": "",
         "description": "The primordial form of the Epoch Hare, existing before time itself had a name.",
         "loot_table": ["tear_of_leviathan", "genesis_fruit", "ambrosia_tart", "sunforge_core"],
         "altered_divine": "void_chronos", "min_guild_level": 10},
        {"id": "ancient_genesis", "name": "Ancient Genesis", "type": "ancient",
         "max_hp": 160000, "attack": 2200, "defense": 280, "image_url": "",
         "description": "The original Origin Phoenix, carrying the flame of the universe's first moment.",
         "loot_table": ["genesis_fruit", "cosmic_singularity_soda", "tear_of_leviathan"],
         "altered_divine": "fractured_genesis", "min_guild_level": 10},
        {"id": "ancient_abyss", "name": "Ancient Abyss", "type": "ancient",
         "max_hp": 140000, "attack": 2100, "defense": 260, "image_url": "",
         "description": "The Dark Matter Panther in its oldest form, a void that predates existence.",
         "loot_table": ["tear_of_leviathan", "ambrosia_tart", "genesis_fruit"],
         "altered_divine": "abyssal_nebula", "min_guild_level": 10},
    ]
}

# Per-boss signature moves — triggered at phase thresholds (70%, 40%, 15% HP)
BOSS_SIGNATURES = {
    "corrupted_leviathan": [
        {"threshold": 0.70, "name": "Tainted Surge",    "mult": 1.4, "flavor": "*The Leviathan surges forward, dark water flooding the arena!*"},
        {"threshold": 0.40, "name": "Abyssal Crush",    "mult": 1.8, "flavor": "*The depths rise. Everyone takes the weight of the ocean at once.*"},
        {"threshold": 0.15, "name": "Void Tide",        "mult": 2.5, "flavor": "*🌊 THE LEVIATHAN UNLEASHES EVERYTHING — it has nothing left to lose.*"},
    ],
    "corrupted_fenrir": [
        {"threshold": 0.70, "name": "Rift Howl",        "mult": 1.4, "flavor": "*Fenrir's howl tears a rift directly through the party!*"},
        {"threshold": 0.40, "name": "Void Fang",        "mult": 1.8, "flavor": "*It bites reality itself. Everyone standing here feels it.*"},
        {"threshold": 0.15, "name": "World Eater's Wrath","mult": 2.5, "flavor": "*🐺 THE VOID FENRIR REMEMBERS WHAT IT WAS MADE TO DO.*"},
    ],
    "corrupted_dragon": [
        {"threshold": 0.70, "name": "Null Flame",       "mult": 1.4, "flavor": "*The Dragon breathes null-fire. It leaves nothing — not ash, not memory.*"},
        {"threshold": 0.40, "name": "Apex Rupture",     "mult": 1.8, "flavor": "*The apex predator reminds everyone why it was called that.*"},
        {"threshold": 0.15, "name": "Oblivion Breath",  "mult": 2.5, "flavor": "*🐉 THE CORRUPTED DRAGON TEARS OPEN ITS OWN CHEST AND BREATHES FROM THE VOID INSIDE.*"},
    ],
    "ancient_chronos": [
        {"threshold": 0.70, "name": "Temporal Rupture", "mult": 1.6, "flavor": "*Time stutters. Everyone takes damage from a moment that hasn't happened yet.*"},
        {"threshold": 0.40, "name": "Epoch Collapse",   "mult": 2.2, "flavor": "*The Primordial Epoch compresses. Everything in range experiences its own end briefly.*"},
        {"threshold": 0.15, "name": "Before Time",      "mult": 3.0, "flavor": "*⏳ ANCIENT CHRONOS RETURNS TO WHAT IT WAS BEFORE THE UNIVERSE NEEDED IT TO HAVE A NAME.*"},
    ],
    "ancient_genesis": [
        {"threshold": 0.70, "name": "Firstborn's Wrath","mult": 1.6, "flavor": "*The First Flame remembers it created everything. It can unmake too.*"},
        {"threshold": 0.40, "name": "Origin Pulse",     "mult": 2.2, "flavor": "*A pulse of creation energy detonates outward. It hurts because it means something.*"},
        {"threshold": 0.15, "name": "The Beginning",    "mult": 3.0, "flavor": "*🔥 ANCIENT GENESIS IGNITES WITH THE LIGHT OF THE UNIVERSE'S FIRST MOMENT. EVERYTHING FLICKERS.*"},
    ],
    "ancient_abyss": [
        {"threshold": 0.70, "name": "Pre-Silence",      "mult": 1.6, "flavor": "*The void before sound reaches into every beast here.*"},
        {"threshold": 0.40, "name": "Void Collapse",    "mult": 2.2, "flavor": "*The absence of light becomes a weapon. Everyone standing here feels the weight of nothing.*"},
        {"threshold": 0.15, "name": "Unmaking",         "mult": 3.0, "flavor": "*🌑 ANCIENT ABYSS BECOMES THE ROOM. THE ROOM BECOMES THE VOID. EVERYTHING IN IT FOLLOWS.*"},
    ],
}

# ── Boss kill scenes — shown as a cinematic embed when a raid boss falls ──────
BOSS_KILL_SCENES = {
    "corrupted_leviathan": {
        "title": "🌊 The Tainted Deep Goes Still",
        "lines": [
            "The water darkens one final time.",
            "Then the darkness lifts. Not slowly — all at once, as if the sea finally remembers what it was before.",
            "The Corrupted Leviathan descends. The ocean it poisoned for miles in every direction clears within seconds.",
            "*It was still the Leviathan, somewhere underneath all that corruption. The sea knew it. The sea let it go.*",
        ],
        "color": "corrupted",
    },
    "corrupted_fenrir": {
        "title": "🐺 The Howling Void Falls Silent",
        "lines": [
            "The last howl tears one more rift — smaller than the others, already closing.",
            "Corrupted Fenrir collapses. The rifts it left in the air seal shut, one by one, like wounds healing backward.",
            "The void energy unravels from its body in threads, pulled apart by something that doesn't want it here.",
            "*The gods who chained it once are not watching. They don't need to be. This time, you did it.*",
        ],
        "color": "corrupted",
    },
    "corrupted_dragon": {
        "title": "🔥 The Broken Apex Burns Out",
        "lines": [
            "The null-flame gutters. For a moment, the Corrupted Dragon is just a dragon — the apex of everything, briefly visible through the ruin.",
            "Then it falls, and the fire that leaves nothing behind leaves nothing of itself either.",
            "The air tastes clean. You hadn't noticed how wrong it smelled until it stopped.",
            "*Whatever it was before the corruption, it was something worth becoming. You saw that at the end.*",
        ],
        "color": "corrupted",
    },
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

BOSS_DEFEAT_SCENES = {
    "corrupted_leviathan": {
        "title": "🌊 The Deep Does Not Yield",
        "lines": [
            "The water closes over everything.",
            "The Corrupted Leviathan does not pursue. It does not need to. It simply remains, enormous and patient, while the tide carries what's left of your effort back to shore.",
            "The ocean it poisoned does not clear. It was never going to clear for you.",
            "*It noticed you. That is not nothing. It noticed you, and it decided you were not enough. There is a difference between those two things, and today you felt it.*",
        ],
        "color": "corrupted",
    },
    "corrupted_fenrir": {
        "title": "🐺 The Void Howls On",
        "lines": [
            "The last rift tears open and does not close.",
            "Corrupted Fenrir does not celebrate. It simply turns away, which is worse. The rifts it left in the air stay open behind you — souvenirs you didn't ask for.",
            "The void energy hums. It has somewhere else to be. You were a delay, not a destination.",
            "*The gods who chained it once are not watching. They don't need to be. Neither did it.*",
        ],
        "color": "corrupted",
    },
    "corrupted_dragon": {
        "title": "🔥 The Apex Remains",
        "lines": [
            "The null-flame does not gutter. It was never close to guttering.",
            "The Corrupted Dragon looks at what remains of your effort the way an ocean looks at a stone someone threw into it. Then it looks away.",
            "The air still tastes wrong. It will for a while.",
            "*It was the apex before the corruption. The corruption only made it less interested in things that can be defeated. You confirmed you are one of those things.*",
        ],
        "color": "corrupted",
    },
    "ancient_chronos": {
        "title": "⏳ Time Did Not Wait",
        "lines": [
            "Ancient Chronos does not stop. It was never going to stop for you.",
            "Time continues moving in the directions it was always going to move. Some of those directions are not forward. You have already experienced several of them.",
            "The moment where you might have won has passed. It passed some time ago, technically speaking. Chronos knew which moment it was.",
            "*It has watched things try and fail since before trying and failing were concepts. It is patient in a way that has nothing to do with kindness. You were not enough — this time. Time will tell if there is a next time. Time always tells.*",
        ],
        "color": "ancient",
    },
    "ancient_genesis": {
        "title": "🔥 The First Flame Was Not Yours to Take",
        "lines": [
            "The light does not dim. It never considered dimming.",
            "Ancient Genesis does not move toward you or away from you. It simply continues being the origin of everything, which is a thing it has been doing since before there was a word for it.",
            "Everything alive in the vicinity flickers — but not from exhaustion. From recognition. Something very old is still here, and it is not you.",
            "*You were not worth sharing it with. Not yet. The flame does not say this to be cruel. It simply knows what has earned it and what hasn't. Come back when you know too.*",
        ],
        "color": "ancient",
    },
    "ancient_abyss": {
        "title": "🌑 The Darkness Stays",
        "lines": [
            "The light does not come back.",
            "Ancient Abyss does not press its advantage. It does not have advantages. It has presence, and it is still present, and that is enough.",
            "The silence does not change. It remains the kind of silence that existed before sound was invented. You are standing in it. You are a very small sound in a very large silence.",
            "*The void before stars was here before you arrived. It will be here after you leave. It noticed you were here. It is noticing you leave. That is all it will say about you.*",
        ],
        "color": "ancient",
    },
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
            # Self-heal: if players.guild_id is set but no actual membership row exists,
            # clear it — this happens after a /dev reset or other data inconsistency.
            if not existing_leader and not existing_member:
                await db.execute(
                    "UPDATE players SET guild_id = NULL WHERE user_id = ?",
                    (interaction.user.id,)
                )
                await db.commit()

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
                super().__init__(timeout=120)
                self.accepted = False
                self.message: discord.Message | None = None

            def _disable_all(self):
                for item in self.children:
                    item.disabled = True

            async def on_timeout(self):
                self._disable_all()
                if self.message:
                    try:
                        await self.message.edit(
                            embed=discord.Embed(
                                description="⌛ This guild invitation expired.",
                                color=COLORS["info"],
                            ),
                            view=self,
                        )
                    except discord.HTTPException:
                        pass

            async def on_error(self, interaction: discord.Interaction, error: Exception, item):
                import logging
                logging.getLogger("chibibeasts.guilds").exception("InviteView error", exc_info=error)
                msg = "✦ Something went wrong with the invite — please try again."
                try:
                    if interaction.response.is_done():
                        await interaction.followup.send(msg, ephemeral=True)
                    else:
                        await interaction.response.send_message(msg, ephemeral=True)
                except discord.HTTPException:
                    pass

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
        view.message = await interaction.followup.send(content=member.mention, embed=embed, view=view)

    @app_commands.command(name="guild_leave", description="Leave your current guild 🚪")
    async def guild_leave(self, interaction: discord.Interaction):
        await interaction.response.defer()

        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT gm.rank, gm.guild_id, g.name, g.leader_id, g.member_count FROM guild_members gm JOIN guilds g ON gm.guild_id = g.id WHERE gm.user_id = ?",
                (interaction.user.id,)
            ) as c:
                member_row = await c.fetchone()

        if not member_row:
            # Check if players.guild_id is orphaned (membership row was deleted but player row wasn't cleaned)
            async with aiosqlite.connect("db/chibibeast.db") as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT guild_id FROM players WHERE user_id = ? AND guild_id IS NOT NULL",
                    (interaction.user.id,)
                ) as c:
                    orphan = await c.fetchone()
                if orphan:
                    await db.execute(
                        "UPDATE players SET guild_id = NULL WHERE user_id = ?",
                        (interaction.user.id,)
                    )
                    await db.commit()
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You're not in a guild!",
                color=COLORS["info"]
            ))

        guild_id   = member_row["guild_id"]
        guild_name = member_row["name"]
        is_leader  = member_row["rank"] == "leader"
        member_count = member_row["member_count"]

        # Leaders must transfer or disband — can't just walk out
        if is_leader:
            if member_count <= 1:
                # Last member — disband entirely
                class DisbandView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)

                    @discord.ui.button(label="Disband Guild", style=discord.ButtonStyle.danger, emoji="💥")
                    async def confirm(self, inv: discord.Interaction, btn: discord.ui.Button):
                        if inv.user.id != interaction.user.id:
                            return await inv.response.send_message("This isn't for you!", ephemeral=True)
                        self.stop()
                        for item in self.children: item.disabled = True
                        await inv.response.edit_message(view=self)
                        async with aiosqlite.connect("db/chibibeast.db") as db:
                            await db.execute("DELETE FROM guild_members WHERE guild_id = ?", (guild_id,))
                            await db.execute("DELETE FROM guilds WHERE id = ?", (guild_id,))
                            await db.execute("UPDATE players SET guild_id = NULL, guild_tokens = 0 WHERE user_id = ?", (interaction.user.id,))
                            await db.commit()
                        await inv.followup.send(embed=discord.Embed(
                            description=f"💥 **{guild_name}** has been disbanded.",
                            color=COLORS["error"]
                        ))

                    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
                    async def cancel(self, inv: discord.Interaction, btn: discord.ui.Button):
                        if inv.user.id != interaction.user.id:
                            return await inv.response.send_message("This isn't for you!", ephemeral=True)
                        self.stop()
                        for item in self.children: item.disabled = True
                        await inv.response.edit_message(view=self)

                return await interaction.followup.send(embed=discord.Embed(
                    title="💥 Disband Guild?",
                    description=(
                        f"You're the only member of **{guild_name}**.\n\n"
                        f"Leaving will permanently disband the guild. This cannot be undone."
                    ),
                    color=COLORS["error"]
                ), view=DisbandView())
            else:
                # Other members exist — must promote an officer or member first
                async with aiosqlite.connect("db/chibibeast.db") as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT user_id, rank FROM guild_members WHERE guild_id = ? AND user_id != ? ORDER BY CASE rank WHEN 'officer' THEN 0 ELSE 1 END LIMIT 1",
                        (guild_id, interaction.user.id)
                    ) as c:
                        next_member = await c.fetchone()

                if not next_member:
                    return await interaction.followup.send(embed=discord.Embed(
                        description="✦ Something went wrong finding a member to promote.",
                        color=COLORS["error"]
                    ))

                new_leader_id = next_member["user_id"]
                new_leader = interaction.guild.get_member(new_leader_id) if interaction.guild else None
                new_leader_name = new_leader.display_name if new_leader else f"<@{new_leader_id}>"

                class TransferView(discord.ui.View):
                    def __init__(self):
                        super().__init__(timeout=60)

                    @discord.ui.button(label="Transfer & Leave", style=discord.ButtonStyle.danger, emoji="🚪")
                    async def confirm(self, inv: discord.Interaction, btn: discord.ui.Button):
                        if inv.user.id != interaction.user.id:
                            return await inv.response.send_message("This isn't for you!", ephemeral=True)
                        self.stop()
                        for item in self.children: item.disabled = True
                        await inv.response.edit_message(view=self)
                        async with aiosqlite.connect("db/chibibeast.db") as db:
                            await db.execute("UPDATE guilds SET leader_id = ? WHERE id = ?", (new_leader_id, guild_id))
                            await db.execute("UPDATE guild_members SET rank = 'leader' WHERE guild_id = ? AND user_id = ?", (guild_id, new_leader_id))
                            await db.execute("DELETE FROM guild_members WHERE guild_id = ? AND user_id = ?", (guild_id, interaction.user.id))
                            await db.execute("UPDATE guilds SET member_count = member_count - 1 WHERE id = ?", (guild_id,))
                            await db.execute("UPDATE players SET guild_id = NULL WHERE user_id = ?", (interaction.user.id,))
                            await db.commit()
                        await inv.followup.send(embed=discord.Embed(
                            description=(
                                f"✦ You've left **{guild_name}**.\n"
                                f"👑 **{new_leader_name}** is now Guild Leader."
                            ),
                            color=COLORS["info"]
                        ))

                    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
                    async def cancel(self, inv: discord.Interaction, btn: discord.ui.Button):
                        if inv.user.id != interaction.user.id:
                            return await inv.response.send_message("This isn't for you!", ephemeral=True)
                        self.stop()
                        for item in self.children: item.disabled = True
                        await inv.response.edit_message(view=self)

                return await interaction.followup.send(embed=discord.Embed(
                    title="🚪 Leave Guild?",
                    description=(
                        f"You're the leader of **{guild_name}**.\n\n"
                        f"Leadership will be transferred to **{new_leader_name}** (most senior member).\n\n"
                        f"*This cannot be undone.*"
                    ),
                    color=COLORS["info"]
                ), view=TransferView())

        # Regular member — straight leave with confirmation
        class LeaveView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)

            @discord.ui.button(label="Leave Guild", style=discord.ButtonStyle.danger, emoji="🚪")
            async def confirm(self, inv: discord.Interaction, btn: discord.ui.Button):
                if inv.user.id != interaction.user.id:
                    return await inv.response.send_message("This isn't for you!", ephemeral=True)
                self.stop()
                for item in self.children: item.disabled = True
                await inv.response.edit_message(view=self)
                async with aiosqlite.connect("db/chibibeast.db") as db:
                    await db.execute("DELETE FROM guild_members WHERE guild_id = ? AND user_id = ?", (guild_id, interaction.user.id))
                    await db.execute("UPDATE guilds SET member_count = member_count - 1 WHERE id = ?", (guild_id,))
                    await db.execute("UPDATE players SET guild_id = NULL WHERE user_id = ?", (interaction.user.id,))
                    await db.commit()
                await inv.followup.send(embed=discord.Embed(
                    description=f"✦ You've left **{guild_name}**.",
                    color=COLORS["info"]
                ))

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="↩️")
            async def cancel(self, inv: discord.Interaction, btn: discord.ui.Button):
                if inv.user.id != interaction.user.id:
                    return await inv.response.send_message("This isn't for you!", ephemeral=True)
                self.stop()
                for item in self.children: item.disabled = True
                await inv.response.edit_message(view=self)

        await interaction.followup.send(embed=discord.Embed(
            title="🚪 Leave Guild?",
            description=f"Are you sure you want to leave **{guild_name}**?",
            color=COLORS["info"]
        ), view=LeaveView())

    @app_commands.command(name="raid", description="Trigger a raid boss battle! ⚔️")
    @app_commands.choices(raid_type=[
        app_commands.Choice(name="⚔️ Corrupted Raid", value="corrupted"),
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
                description="✦ You need to be in a guild to trigger Corrupted Raids!", color=COLORS["error"]
            ))

        guild_data = dict(guild_data)

        if guild_data["rank"] not in ["leader", "officer"]:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Only Guild Leaders and Officers can trigger Corrupted Raids!", color=COLORS["error"]
            ))

        min_level = 5
        if guild_data["level"] < min_level:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Your guild needs to be Level {min_level} to trigger Corrupted Raids!",
                color=COLORS["error"]
            ))

        token_cost = 50
        if guild_data["guild_tokens"] < token_cost:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ Triggering a Corrupted Raid costs `{token_cost}` Guild Tokens. You have `{guild_data['guild_tokens']}`.",
                color=COLORS["error"]
            ))

        # Pick random corrupted boss
        boss = random.choice(RAID_BOSSES["corrupted"])

        # Lore-flavored intro lines
        SUNDERING_LINES = [
            "Something in the Loom snapped. The thread didn't finish weaving — and now it's here.",
            "The Loom tried to make something too large, too quickly. The result is in front of you now.",
            "A Corrupted beast has torn through. It isn't evil — it's unfinished, and it's in pain. Fight it down long enough for the Loom to recapture the thread.",
            "The weave split. What came loose is trying to finish becoming itself the wrong way. Your guild needs to hold it steady.",
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
            """, (boss["id"], boss["name"], "corrupted", boss["max_hp"], boss["max_hp"], guild_data["id"], interaction.channel_id))
            await db.commit()
            async with db.execute("SELECT last_insert_rowid()") as c:
                raid_id = (await c.fetchone())[0]

        # Pre-cache guild members and fetch their active beast stats for dynamic scaling
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT user_id FROM guild_members WHERE guild_id = ?", (guild_data["id"],)
            ) as c:
                member_rows = await c.fetchall()
            guild_member_ids = {r["user_id"] for r in member_rows}

            # Fetch raid party beasts for all guild members for dynamic scaling
            # Falls back to active beast if no raid party set
            raid_beast_stats = []
            for uid in guild_member_ids:
                async with db.execute(
                    "SELECT max_hp, attack, defense FROM player_beasts WHERE user_id = ? AND raid_slot IN (1,2,3) ORDER BY raid_slot",
                    (uid,)
                ) as c:
                    party_beasts = [dict(r) for r in await c.fetchall()]
                if party_beasts:
                    raid_beast_stats.extend(party_beasts)
                else:
                    # Fallback: active beast
                    async with db.execute(
                        "SELECT max_hp, attack, defense FROM player_beasts WHERE user_id = ? AND is_active = 1",
                        (uid,)
                    ) as c:
                        b = await c.fetchone()
                    if b:
                        raid_beast_stats.append(dict(b))

        if not raid_beast_stats:
            raid_beast_stats = [{"max_hp": 220, "attack": 120, "defense": 100}]

        avg_party_hp  = sum(b["max_hp"]  for b in raid_beast_stats) / len(raid_beast_stats)
        avg_party_def = sum(b["defense"] for b in raid_beast_stats) / len(raid_beast_stats)

        scaled_boss_def = min(300, int(avg_party_def * 0.80))  # cap prevents near-immunity at extreme defense

        def _est_dps(atk, bdef):
            df = bdef / (bdef + 100)
            return max(1, int(atk * (1 - df)))

        party_dps_mid = sum(_est_dps(b["attack"], scaled_boss_def) * 10 for b in raid_beast_stats)
        scaled_hp  = max(party_dps_mid * 35, boss["max_hp"] // 3)
        scaled_atk = max(int(avg_party_hp * 0.20), boss["attack"] // 20)

        active_raids[raid_id] = {
            "boss": boss, "current_hp": scaled_hp,
            "max_hp": scaled_hp, "participants": {},
            "guild_id": guild_data["id"], "channel": interaction.channel,
            "raid_message": None,
            "attack_counts": {},
            "embed_updating": False,
            "guild_members": guild_member_ids,
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
            "scaled_boss_def": scaled_boss_def,
            "player_party": {},       # uid -> [beast_row, beast_row, beast_row]
            "player_active_slot": {}, # uid -> int (0,1,2)
        }
        _raid_locks[raid_id] = asyncio.Lock()

        ATTACK_COOLDOWN = 0.8
        BOSS_ATK_INTERVAL = 8  # seconds between boss auto-attacks

        def boss_effective_defense(raid: dict) -> int:
            """Defense drops as boss HP falls — rewards sustained DPS."""
            pct = raid["current_hp"] / max(raid["max_hp"], 1)
            base_def = raid.get("scaled_boss_def", scaled_boss_def)
            if pct < 0.15:
                return int(base_def * 0.40)
            elif pct < 0.40:
                return int(base_def * 0.60)
            elif pct < 0.70:
                return int(base_def * 0.80)
            return base_def

        def calc_player_damage(atk: int, defense: int, is_ultimate: bool, is_crit: bool) -> int:
            defense_factor = defense / (defense + 100)
            dmg = atk * (1 - defense_factor)
            if is_ultimate:
                dmg *= 1.8
            if is_crit:
                dmg *= 1.5
            return max(1, int(dmg * random.uniform(0.85, 1.15)))

        def calc_boss_damage(boss_atk: int, player_def: int, player_max_hp: int = 0) -> int:
            # Flat % of player max HP — immune to defense outliers like Desync
            # boss_atk acts as a percentage multiplier: scaled_atk / avg_hp * 100 ≈ 20%
            if player_max_hp > 0:
                pct = random.uniform(0.12, 0.18)
                return max(1, int(player_max_hp * pct))
            # Fallback to formula if no HP provided
            defense_factor = min(player_def, 300) / (min(player_def, 300) + 100)
            return max(1, int(boss_atk * (1 - defense_factor) * random.uniform(0.80, 1.20)))

        def build_raid_embed(raid: dict) -> discord.Embed:
            current_hp = raid["current_hp"]
            max_hp = raid["max_hp"]
            pct = current_hp / max_hp
            status = "🔴 CRITICAL" if pct < 0.15 else "🟠 Weakened" if pct < 0.40 else "🟡 Damaged" if pct < 0.70 else "🟢 Active"
            def_pct = int((1 - boss_effective_defense(raid) / max(raid.get("scaled_boss_def", scaled_boss_def), 1)) * 100)
            def_note = f" *(−{def_pct}% DEF)*" if def_pct > 0 else ""

            embed = discord.Embed(
                title=f"⚔️ CORRUPTED RAID — {boss['name']}!",
                description=(
                    f"*{sundering_line}*\n\n"
                    f"*{boss['description']}*\n\n"
                    f"💀 **HP:** {hp_bar(current_hp, max_hp)} {status}{def_note}\n"
                    f"`{current_hp:,} / {max_hp:,}`\n\n"
                    f"🏆 Top 3 damage dealers can catch the boss itself!"
                ),
                color=COLORS["epic"]
            )
            if raid["participants"]:
                top = sorted(raid["participants"].items(), key=lambda x: x[1], reverse=True)[:5]
                medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                lines = []
                for i, (uid, dmg) in enumerate(top):
                    p_hp = raid["player_hp"].get(uid, 0)
                    p_max = raid["player_max_hp"].get(uid, 1)
                    p_mana = raid["player_mana"].get(uid, 0)
                    alive = "💀" if p_hp <= 0 else "⚡" if p_mana >= 50 else "❤️"
                    lines.append(f"{medals[i]} <@{uid}> — `{dmg:,}` dmg {alive} `{p_hp}/{p_max}HP`")
                embed.add_field(name="⚔️ Party", value="\n".join(lines), inline=False)
            if raid.get("phase_log"):
                embed.add_field(name="📋 Phase Log", value="\n".join(raid["phase_log"]), inline=False)
            if raid.get("last_event"):
                embed.add_field(name="📣 Last Event", value=raid["last_event"], inline=False)
            if boss.get("image_url"):
                embed.set_image(url=boss["image_url"])
            embed.set_footer(text=f"Raid ID: #{raid_id} | Triggered by {interaction.user.display_name} | Boss attacks every {BOSS_ATK_INTERVAL}s")
            return embed

        async def boss_attack_loop():
            """Boss auto-attacks a random active player every BOSS_ATK_INTERVAL seconds."""
            while raid_id in active_raids:
                await asyncio.sleep(BOSS_ATK_INTERVAL)
                if raid_id not in active_raids:
                    break
                raid = active_raids[raid_id]
                if not raid["participants"]:
                    continue

                # Pick a random alive participant
                alive = [uid for uid, hp in raid["player_hp"].items() if hp > 0]
                if not alive:
                    continue

                target_uid = random.choice(alive)
                p_def = raid["player_defense"].get(target_uid, 50)
                dmg = calc_boss_damage(raid["boss_attack"], p_def, raid["player_max_hp"].get(target_uid, 0))

                async with _raid_locks[raid_id]:
                    if raid_id not in active_raids:
                        break
                    raid["player_hp"][target_uid] = max(0, raid["player_hp"].get(target_uid, 0) - dmg)
                    p_hp = raid["player_hp"][target_uid]
                    p_max = raid["player_max_hp"].get(target_uid, 1)

                # Store boss attack as last_event — shown in embed, no new message
                died = p_hp <= 0
                raid["last_event"] = (
                    f"💥 **{boss['name']}** strikes <@{target_uid}>! `{dmg:,}` dmg"
                    + (f" — **knocked out!** 💀" if died else f" | `{p_hp}/{p_max}HP`")
                )

                # Check full party wipe — end raid if every participant is down with no bench left
                if raid["participants"] and raid_id in active_raids:
                    def _has_bench(uid):
                        party = raid.get("player_party", {}).get(uid, [])
                        slot  = raid.get("player_active_slot", {}).get(uid, 0)
                        return any(raid["player_hp"].get(uid, 1) > 0 or i != slot
                                   for i, _ in enumerate(party) if i != slot)
                    all_down = all(
                        raid["player_hp"].get(uid, 1) <= 0
                        for uid in raid["participants"]
                    )
                    any_bench = any(
                        len(raid.get("player_party", {}).get(uid, [])) > 1 and
                        raid["player_active_slot"].get(uid, 0) < len(raid["player_party"][uid]) - 1
                        for uid in raid["participants"]
                    )
                    if all_down and not any_bench:
                        await cog.end_raid(raid_id, channel)
                        break

                # Update embed
                if not raid.get("embed_updating") and raid.get("raid_message"):
                    raid["embed_updating"] = True
                    try:
                        await raid["raid_message"].edit(embed=build_raid_embed(raid))
                    except Exception:
                        pass
                    finally:
                        if raid_id in active_raids:
                            active_raids[raid_id]["embed_updating"] = False

        # Map threshold -> defense reduction % for phase display
        _PHASE_DEF_REDUCTION = {0.70: 20, 0.40: 40, 0.15: 60}

        async def check_phase_transitions(raid: dict, channel):
            """Fire boss signature moves at phase thresholds."""
            pct = raid["current_hp"] / raid["max_hp"]
            signatures = BOSS_SIGNATURES.get(boss["id"], [])
            for sig in signatures:
                if pct <= sig["threshold"] and sig["threshold"] not in raid["phase_fired"]:
                    raid["phase_fired"].add(sig["threshold"])
                    # Apply AoE damage to all alive players silently
                    alive = [uid for uid, hp in raid["player_hp"].items() if hp > 0]
                    for uid in alive:
                        raw = calc_boss_damage(raid["boss_attack"], raid["player_defense"].get(uid, 50), raid["player_max_hp"].get(uid, 0))
                        dmg = int(raw * sig["mult"])
                        raid["player_hp"][uid] = max(0, raid["player_hp"].get(uid, 0) - dmg)

                    # Store as last_event on the embed — no new channel message
                    phase_status = (
                        "🔴 CRITICAL — Boss DEF −60%" if sig["threshold"] == 0.15 else
                        "🟠 Weakened — Boss DEF −40%"  if sig["threshold"] == 0.40 else
                        "🟡 Damaged — Boss DEF −20%"
                    )
                    raid["last_event"] = f"⚡ **{sig['name']}** — {phase_status}"
                    raid["phase_log"].append(f"{phase_status} · *{sig['name']}*")

        cog = self

        class RaidView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=1800)

            @discord.ui.button(label="⚔️ Attack!", style=discord.ButtonStyle.danger, emoji="💥")
            async def attack_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                import time
                await btn_interaction.response.defer(ephemeral=True, thinking=False)

                if raid_id not in active_raids:
                    return await btn_interaction.followup.send("✦ The raid has ended!", ephemeral=True)
                raid = active_raids[raid_id]
                uid = btn_interaction.user.id

                if uid not in raid["guild_members"]:
                    return await btn_interaction.followup.send("✦ You need to be in this guild to attack!", ephemeral=True)

                # Knocked out — show swap UI if bench slots remain
                if uid in raid["player_hp"] and raid["player_hp"][uid] <= 0:
                    party = raid.get("player_party", {}).get(uid, [])
                    current_slot = raid.get("player_active_slot", {}).get(uid, 0)
                    bench = [(i, b) for i, b in enumerate(party) if i != current_slot]
                    if not bench:
                        return await btn_interaction.followup.send("✦ All your beasts are down. You can still watch.", ephemeral=True)

                    async def do_swap(slot_idx: int, swap_interaction: discord.Interaction):
                        if raid_id not in active_raids:
                            return await swap_interaction.response.send_message("✦ Raid ended!", ephemeral=True)
                        r = active_raids[raid_id]
                        new_beast = r["player_party"][uid][slot_idx]
                        r["player_active_slot"][uid]  = slot_idx
                        r["player_hp"][uid]           = new_beast["hp"]
                        r["player_max_hp"][uid]       = new_beast["max_hp"]
                        r["player_defense"][uid]      = new_beast["defense"]
                        r["player_atk"][uid]          = new_beast["attack"]
                        r["player_mana"][uid]         = 0
                        bd = get_beast_data(new_beast["beast_id"]) or {}
                        name = new_beast.get("nickname") or bd.get("name", "Beast")
                        await swap_interaction.response.send_message(
                            f"✅ **{name}** enters the fight! `{new_beast['hp']}/{new_beast['max_hp']}HP`",
                            ephemeral=True
                        )

                    class SwapView(discord.ui.View):
                        def __init__(self):
                            super().__init__(timeout=30)
                            for slot_idx, beast_row in bench:
                                bd = get_beast_data(beast_row["beast_id"]) or {}
                                label = beast_row.get("nickname") or bd.get("name", f"Beast {slot_idx+1}")
                                btn = discord.ui.Button(
                                    label=f"{label} ({beast_row['hp']}HP)",
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

                # Per-player cooldown
                now = time.monotonic()
                if now - raid["last_attack"].get(uid, 0) < ATTACK_COOLDOWN:
                    return await btn_interaction.followup.send(f"⏱️ Wait `{ATTACK_COOLDOWN - (now - raid['last_attack'].get(uid,0)):.1f}s`.", ephemeral=True)
                raid["last_attack"][uid] = now

                active = await get_active_beast(uid)
                if not active:
                    return await btn_interaction.followup.send("✦ You need an active beast! Use `/setactive`.", ephemeral=True)

                # Load raid party on first attack — must have 3 slots assigned via /raidparty
                if uid not in raid["player_party"]:
                    async with aiosqlite.connect("db/chibibeast.db") as _pdb:
                        _pdb.row_factory = aiosqlite.Row
                        async with _pdb.execute(
                            "SELECT * FROM player_beasts WHERE user_id = ? AND raid_slot IN (1,2,3) ORDER BY raid_slot",
                            (uid,)
                        ) as _c:
                            party_rows = [dict(r) for r in await _c.fetchall()]
                    if len(party_rows) < 3:
                        return await btn_interaction.followup.send(
                            f"✦ You need a full 3-beast raid party! You have {len(party_rows)}/3 slots filled.\n"
                            f"Use `/raidparty` to set up your team before joining a raid.",
                            ephemeral=True
                        )
                    raid["player_party"][uid]       = party_rows
                    raid["player_active_slot"][uid] = 0
                    slot = party_rows[0]
                    raid["player_hp"][uid]      = slot["hp"]
                    raid["player_max_hp"][uid]  = slot["max_hp"]
                    raid["player_defense"][uid] = slot["defense"]
                    raid["player_atk"][uid]     = slot["attack"]
                    raid["player_mana"][uid]    = 0

                # Use party slot stats — not live DB (prevents setactive exploit)
                active_slot = raid["player_active_slot"].get(uid, 0)
                party = raid["player_party"].get(uid, [])
                if not party:
                    return await btn_interaction.followup.send("✦ No party loaded!", ephemeral=True)
                active_beast_row = party[active_slot] if active_slot < len(party) else party[0]
                active_beast_data = get_beast_data(active_beast_row["beast_id"]) or {}

                is_crit = random.random() < 0.15
                defense = boss_effective_defense(raid)
                player_atk = raid["player_atk"].get(uid, active_beast_row["attack"])
                player_spd = active_beast_row.get("speed", 50)
                is_ultimate = False
                damage = calc_player_damage(player_atk, defense, is_ultimate, is_crit)

                raid_lock = _raid_locks.get(raid_id)
                if not raid_lock:
                    return await btn_interaction.followup.send("✦ The raid just ended!", ephemeral=True)

                async with raid_lock:
                    if raid_id not in active_raids:
                        return await btn_interaction.followup.send("✦ The raid just ended!", ephemeral=True)
                    raid = active_raids[raid_id]
                    raid["current_hp"] = max(0, raid["current_hp"] - damage)
                    raid["participants"][uid] = raid["participants"].get(uid, 0) + damage
                    raid["attack_counts"][uid] = raid["attack_counts"].get(uid, 0) + 1
                    # Mana gain scales with speed
                    mana_gain = min(15, 8 + player_spd // 40)
                    raid["player_mana"][uid] = min(100, raid["player_mana"].get(uid, 0) + mana_gain)

                    async with aiosqlite.connect("db/chibibeast.db") as db:
                        await db.execute("UPDATE raids SET current_hp = ? WHERE id = ?", (raid["current_hp"], raid_id))
                        async with db.execute("SELECT damage_dealt FROM raid_participants WHERE raid_id = ? AND user_id = ?", (raid_id, uid)) as c:
                            existing = await c.fetchone()
                        if existing:
                            await db.execute("UPDATE raid_participants SET damage_dealt = damage_dealt + ? WHERE raid_id = ? AND user_id = ?", (damage, raid_id, uid))
                        else:
                            await db.execute("INSERT INTO raid_participants (raid_id, user_id, damage_dealt) VALUES (?, ?, ?)", (raid_id, uid, damage))
                        await db.commit()

                    raid_ended = raid["current_hp"] <= 0
                    current_hp_snap = raid["current_hp"]
                    new_mana = raid["player_mana"].get(uid, 0)

                # Phase transition check
                await check_phase_transitions(active_raids.get(raid_id, raid), btn_interaction.channel)

                # Store last player action as last_event in embed — no ephemeral needed
                if raid_id in active_raids:
                    crit_tag = "⭐ CRIT! " if is_crit else ""
                    active_raids[raid_id]["last_event"] = f"{crit_tag}<@{uid}> hit for `{damage:,}` dmg | Mana `{new_mana}/100`" + (" ⚡" if new_mana >= 50 else "")

                if not raid.get("embed_updating") and raid.get("raid_message"):
                    raid["embed_updating"] = True
                    try:
                        await raid["raid_message"].edit(embed=build_raid_embed(active_raids.get(raid_id, raid)), view=self if not raid_ended else None)
                    except discord.HTTPException:
                        pass
                    finally:
                        if raid_id in active_raids:
                            active_raids[raid_id]["embed_updating"] = False

                await track_quest_event(uid, "raid_damage", amount=damage)
                await advance_quest_step(uid, "raid_participate")

                if raid_ended:
                    await cog.end_raid(raid_id, btn_interaction.channel)

            @discord.ui.button(label="⚡ Ultimate", style=discord.ButtonStyle.secondary, emoji="💫")
            async def ultimate_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                import time
                await btn_interaction.response.defer(ephemeral=True, thinking=False)

                if raid_id not in active_raids:
                    return await btn_interaction.followup.send("✦ The raid has ended!", ephemeral=True)
                raid = active_raids[raid_id]
                uid = btn_interaction.user.id

                if uid not in raid["guild_members"]:
                    return await btn_interaction.followup.send("✦ You need to be in this guild!", ephemeral=True)
                if uid in raid["player_hp"] and raid["player_hp"][uid] <= 0:
                    return await btn_interaction.followup.send("✦ Your beast is knocked out!", ephemeral=True)
                if raid["player_mana"].get(uid, 0) < 50:
                    return await btn_interaction.followup.send(f"✦ Not enough mana! `{raid['player_mana'].get(uid,0)}/50` needed.", ephemeral=True)

                now = time.monotonic()
                if now - raid["last_attack"].get(uid, 0) < ATTACK_COOLDOWN:
                    return await btn_interaction.followup.send(f"⏱️ Wait `{ATTACK_COOLDOWN - (now - raid['last_attack'].get(uid,0)):.1f}s`.", ephemeral=True)
                raid["last_attack"][uid] = now

                # Use party slot — not live DB
                ult_slot = raid.get("player_active_slot", {}).get(uid, 0)
                ult_party = raid.get("player_party", {}).get(uid, [])
                if not ult_party:
                    return await btn_interaction.followup.send("✦ No party loaded! Attack first.", ephemeral=True)
                ult_beast_row = ult_party[ult_slot] if ult_slot < len(ult_party) else ult_party[0]
                ult_beast_data = get_beast_data(ult_beast_row["beast_id"]) or {}
                ult_name = ult_beast_data.get("ultimate", "Ultimate")
                ult_atk  = raid["player_atk"].get(uid, ult_beast_row["attack"])

                is_crit = random.random() < 0.20
                defense = boss_effective_defense(raid)
                damage = calc_player_damage(ult_atk, defense, True, is_crit)

                raid_lock = _raid_locks.get(raid_id)
                if not raid_lock:
                    return await btn_interaction.followup.send("✦ The raid just ended!", ephemeral=True)

                async with raid_lock:
                    if raid_id not in active_raids:
                        return await btn_interaction.followup.send("✦ The raid just ended!", ephemeral=True)
                    raid = active_raids[raid_id]
                    raid["current_hp"] = max(0, raid["current_hp"] - damage)
                    raid["participants"][uid] = raid["participants"].get(uid, 0) + damage
                    raid["attack_counts"][uid] = raid["attack_counts"].get(uid, 0) + 1
                    raid["player_mana"][uid] = 0  # drain mana

                    async with aiosqlite.connect("db/chibibeast.db") as db:
                        await db.execute("UPDATE raids SET current_hp = ? WHERE id = ?", (raid["current_hp"], raid_id))
                        async with db.execute("SELECT damage_dealt FROM raid_participants WHERE raid_id = ? AND user_id = ?", (raid_id, uid)) as c:
                            existing = await c.fetchone()
                        if existing:
                            await db.execute("UPDATE raid_participants SET damage_dealt = damage_dealt + ? WHERE raid_id = ? AND user_id = ?", (damage, raid_id, uid))
                        else:
                            await db.execute("INSERT INTO raid_participants (raid_id, user_id, damage_dealt) VALUES (?, ?, ?)", (raid_id, uid, damage))
                        await db.commit()

                    raid_ended = raid["current_hp"] <= 0

                # Public ultimate announcement
                await check_phase_transitions(active_raids.get(raid_id, raid), btn_interaction.channel)

                # Ultimate stored as last_event — visible on embed, no new messages
                if raid_id in active_raids:
                    crit_tag = "⭐ CRIT! " if is_crit else ""
                    active_raids[raid_id]["last_event"] = f"⚡ <@{uid}> unleashes **{ult_name}**! {crit_tag}`{damage:,}` dmg — Mana reset."

                if not raid.get("embed_updating") and raid.get("raid_message"):
                    raid["embed_updating"] = True
                    try:
                        await raid["raid_message"].edit(embed=build_raid_embed(active_raids.get(raid_id, raid)), view=self if not raid_ended else None)
                    except discord.HTTPException:
                        pass
                    finally:
                        if raid_id in active_raids:
                            active_raids[raid_id]["embed_updating"] = False

                await track_quest_event(uid, "raid_damage", amount=damage)
                await advance_quest_step(uid, "raid_participate")

                if raid_ended:
                    await cog.end_raid(raid_id, btn_interaction.channel)

            async def on_timeout(self):
                if raid_id in active_raids:
                    await cog.end_raid(raid_id, interaction.channel, timed_out=True)

        view = RaidView()
        raid_msg = await interaction.followup.send(embed=build_raid_embed(active_raids[raid_id]), view=view)
        active_raids[raid_id]["raid_message"] = raid_msg

        # Start boss attack loop as background task
        asyncio.create_task(boss_attack_loop())

        # Auto-end after 30 minutes
        await asyncio.sleep(1800)
        if raid_id in active_raids:
            await cog.end_raid(raid_id, interaction.channel, timed_out=True)

    async def end_raid(self, raid_id: int, channel, timed_out: bool = False):
        if raid_id not in active_raids:
            return
        raid = active_raids.pop(raid_id)
        _raid_locks.pop(raid_id, None)  # clean up lock — raid is over
        boss = raid["boss"]
        defeated = raid["current_hp"] <= 0

        sorted_participants = sorted(raid["participants"].items(), key=lambda x: x[1], reverse=True)

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
                title=f"🏆 {boss['name']} Defeated!",
                description=(
                    f"*The guild has prevailed. Rewards are being distributed.*\n\n"
                    f"⚠️ **Top 3 damage dealers have a chance to catch {boss['name']}.**\n"
                    f"Rank 1: **5%** · Rank 2: **3%** · Rank 3: **2%**"
                ),
                color=COLORS["legendary"]
            )
        else:
            scene = BOSS_DEFEAT_SCENES.get(boss["id"])
            if scene:
                defeat_embed = discord.Embed(
                    title=scene["title"],
                    description="\n\n".join(scene["lines"]),
                    color=COLORS.get(scene["color"], COLORS["error"])
                )
                if boss.get("image_url"):
                    defeat_embed.set_image(url=boss["image_url"])
                await channel.send(embed=defeat_embed)

            embed = discord.Embed(
                title=f"💀 {boss['name']} — The Guild Falls",
                description="*The boss was not defeated. Regroup, grow stronger, and return.*",
                color=COLORS["error"]
            )

        # Rewards
        reward_lines = []
        guild_id_for_tokens = raid.get("guild_id")

        # ── Guild token award on victory ──────────────────────────────────
        # Every participant earns tokens scaled by rank so contributing to
        # a kill is always worth doing. The guild pot gets a flat bonus too
        # so the guild itself banks tokens toward future raids organically.
        #   Rank 1: 30 tokens | Rank 2-3: 20 | Rank 4-10: 10 | All others: 5
        # Ancient raids pay 1.5× (rounded) to reflect their higher cost and
        # difficulty — solo-qualifying an ancient should feel distinctly rewarding.
        RANK_TOKENS  = {1: 30, 2: 20, 3: 20}
        BASE_TOKENS  = 10
        CONSOLATION  = 5
        ancient_mult = 1.5 if boss.get("type") == "ancient" else 1.0
        GUILD_BONUS  = int(50 * ancient_mult)

        for i, (user_id, damage) in enumerate(sorted_participants[:10]):
            member = channel.guild.get_member(user_id)
            display_name = member.display_name if member else f"<@{user_id}>"
            rank = i + 1
            gold = 500 if rank == 1 else 300 if rank == 2 else 200 if rank == 3 else 100
            exp = 200 if rank == 1 else 150 if rank <= 3 else 80

            if defeated:
                await update_player(user_id, gold=(await get_player(user_id))["gold"] + gold)

                # ── Guild token earn: scaled by rank, 1.5× for ancient raids ──
                player_tokens = int(RANK_TOKENS.get(rank, BASE_TOKENS) * ancient_mult)
                async with aiosqlite.connect("db/chibibeast.db") as _tdb:
                    await _tdb.execute(
                        "UPDATE players SET guild_tokens = guild_tokens + ? WHERE user_id = ?",
                        (player_tokens, user_id)
                    )
                    await _tdb.commit()

                # ── Achievement tracking: raid victory + any stat thresholds crossed ──
                raid_win_unlocked = await unlock_simple_achievement(user_id, "first_raid_win")
                stat_unlocked = await check_achievements(user_id)
                all_raid_unlocks = (["first_raid_win"] if raid_win_unlocked else []) + stat_unlocked
                if all_raid_unlocks:
                    if member:
                        await notify_unlocks(channel, member, all_raid_unlocks)

                # Loot drop
                if random.random() < (0.8 - (i * 0.1)):
                    loot = random.choice(boss["loot_table"])
                    from utils.db import add_item
                    await add_item(user_id, loot)
                    reward_lines.append(f"{'🥇' if rank==1 else '🥈' if rank==2 else '🥉' if rank==3 else '🏅'} **{display_name}** — `{damage:,}` dmg | +{gold}💰 | +{player_tokens}🎟️ | 🎁 {loot.replace('_',' ').title()}")
                else:
                    reward_lines.append(f"{'🥇' if rank==1 else '🥈' if rank==2 else '🥉' if rank==3 else '🏅'} **{display_name}** — `{damage:,}` dmg | +{gold}💰 | +{player_tokens}🎟️")

                # ── Raid boss catch chance for top 3 ─────────────────────────
                if rank <= 3:
                    catch_chances = {1: 0.05, 2: 0.03, 3: 0.02}
                    catch_chance = catch_chances.get(rank, 0)
                    if random.random() < catch_chance:
                        boss_beast_data = get_beast_data(boss["id"])
                        if boss_beast_data:
                            from utils.db import add_beast_to_player
                            await add_beast_to_player(user_id, {**boss_beast_data, "caught_from": "raid"})
                            from utils.progress import record_bestiary_sighting
                            await record_bestiary_sighting(channel.guild.id, boss["id"], user_id)
                            await channel.send(embed=discord.Embed(
                                title="⚔️ THE RAID BOSS HAS BEEN CAUGHT!",
                                description=(
                                    f"*In the moment of defeat, something shifts.*\n\n"
                                    f"🌟 **{display_name}** has caught **{boss_beast_data['name']}** — *{boss_beast_data['title']}*!\n\n"
                                    f"*{boss_beast_data['description']}*\n\n"
                                    f"**Corrupted** form — obtainable only through guild raids."
                                ),
                                color=COLORS.get(boss_beast_data["rarity"], COLORS["legendary"])
                            ))

        if reward_lines:
            embed.add_field(name="🏆 Top Damage Dealers", value="\n".join(reward_lines), inline=False)

        # ── Guild treasury bonus on victory ───────────────────────────────
        if defeated and guild_id_for_tokens:
            async with aiosqlite.connect("db/chibibeast.db") as _gdb:
                await _gdb.execute(
                    "UPDATE guilds SET guild_tokens = guild_tokens + ? WHERE id = ?",
                    (GUILD_BONUS, guild_id_for_tokens)
                )
                await _gdb.commit()
            embed.add_field(
                name="🎟️ Guild Treasury",
                value=f"+**{GUILD_BONUS} Guild Tokens** added to the guild!",
                inline=False
            )

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
