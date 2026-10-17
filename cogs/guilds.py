from utils import db as storage
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import random
import asyncio
from utils.artwork import beast_image_url
from utils.db import get_or_create_player, get_player, update_player, get_beast_data, get_active_beast, knockout_beast, revive_beast, is_knocked_out, ko_time_remaining
from utils.theme import COLORS, RARITY_EMOJI, hp_bar, SPARKLE
from utils.progress import (
    track_quest_event, check_achievements, unlock_simple_achievement, notify_unlocks, notify_quest_completions
)
from cogs.questline import advance_quest_step

GUILD_LEVEL_PERKS = {
    1:  {"max_members": 10,  "unlocks": "Guild created!"},
    5:  {"max_members": 20,  "unlocks": "Corrupted Raids"},
    10: {"max_members": 30,  "unlocks": "Loom Fragments in the Guild Shop"},
    15: {"max_members": 40,  "unlocks": "Sanctuary Blueprints in the Guild Shop"},
    20: {"max_members": 50,  "unlocks": "Room for 50 guild companions"},
    25: {"max_members": 50,  "unlocks": "A home established in the Loom"},
}

RAID_BOSSES = {
    "corrupted": [
        {"id": "corrupted_leviathan", "name": "Corrupted Leviathan", "type": "corrupted",
         "max_hp": 50000, "attack": 800, "defense": 120, "image_url": beast_image_url("corrupted_leviathan"),
         "description": "A once-great sea sovereign, now twisted by dark energy into something monstrous.",
         "loot_table": ["phoenix_elixir", "luna_nectar", "star_candy_shards", "void_prism", "abyssal_scale"],
         "min_guild_level": 5},
        {"id": "corrupted_fenrir", "name": "Corrupted Fenrir", "type": "corrupted",
         "max_hp": 45000, "attack": 900, "defense": 130, "image_url": beast_image_url("corrupted_fenrir"),
         "description": "The World Eater consumed by void energy, its howl now tears rifts in reality.",
         "loot_table": ["phoenix_elixir", "star_candy_shards", "void_shard", "epoch_shard"],
         "min_guild_level": 5},
        {"id": "corrupted_dragon", "name": "Corrupted Dragon", "type": "corrupted",
         "max_hp": 55000, "attack": 850, "defense": 140, "image_url": beast_image_url("corrupted_dragon"),
         "description": "The apex predator twisted into a creature of pure destructive energy.",
         "loot_table": ["ambrosia_tart", "phoenix_elixir", "star_candy_shards", "firstborn_ember"],
         "min_guild_level": 5},
    ],
    "ancient": [
        {"id": "ancient_chronos", "name": "Ancient Chronos", "type": "ancient",
         "max_hp": 150000, "attack": 2000, "defense": 300, "image_url": beast_image_url("ancient_chronos"),
         "description": "The primordial form of the Epoch Hare, existing before time itself had a name.",
         "loot_table": ["genesis_fruit", "ambrosia_tart", "spirit_crystal", "star_candy_shards"],
         "altered_divine": "void_chronos", "min_guild_level": 10},
        {"id": "ancient_genesis", "name": "Ancient Genesis", "type": "ancient",
         "max_hp": 160000, "attack": 2200, "defense": 280, "image_url": beast_image_url("ancient_genesis"),
         "description": "The original Origin Phoenix, carrying the flame of the universe's first moment.",
         "loot_table": ["genesis_fruit", "ambrosia_tart", "sunforge_residue", "phoenix_ash"],
         "altered_divine": "fractured_genesis", "min_guild_level": 10},
        {"id": "ancient_abyss", "name": "Ancient Abyss", "type": "ancient",
         "max_hp": 140000, "attack": 2100, "defense": 260, "image_url": beast_image_url("ancient_abyss"),
         "description": "The Dark Matter Panther in its oldest form, a void that predates existence.",
         "loot_table": ["genesis_fruit", "ambrosia_tart", "void_essence", "void_shard"],
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
BOSS_KILL_SCENES = {'corrupted_leviathan': {'title': '🌊 A Clear Current',
                         'lines': ['The tainted water lifts from the Leviathan in long dark '
                                   'ribbons.',
                                   'It breathes once, slowly. Light reaches the ocean floor again.',
                                   'The creature circles the party, then swims into the clear '
                                   'current. Something small and shining remains in its wake.',
                                   '*It was still the Leviathan underneath the pressure. Now it '
                                   'has room to be itself.*'],
                         'color': 'corrupted'},
 'corrupted_fenrir': {'title': '🐺 A Path Between the Rifts',
                      'lines': ['The last rift folds shut. Fenrir plants all four paws on ground '
                                'that stays beneath them.',
                                'The void loosens from its fur, thread by thread. It stops looking '
                                'over its shoulder.',
                                'It rests beside the party long enough to catch its breath, then '
                                'chooses a path through the trees.',
                                '*No chain replaces the old one. There is a path, and it can '
                                'choose where that path goes.*'],
                      'color': 'corrupted'},
 'corrupted_dragon': {'title': '🔥 Warmth After the Null-Flame',
                      'lines': ['The null-flame gutters into an ordinary, warm light.',
                                'The Dragon lowers itself beside the forge stones. Its breathing '
                                'settles. Its wings are whole.',
                                'It nudges a few loose scales and pieces of heat-glass toward the '
                                'companions, then lifts its head to the clear sky.',
                                '*The creature is still here. The pressure is not. There is room '
                                'to become something tomorrow.*'],
                      'color': 'corrupted'},
 'ancient_chronos': {'title': '⏳ Time Catches Its Breath',
                     'lines': ['Ancient Chronos does not fall. It simply stops.',
                               "One moment it is there — vast, ageless, older than the word 'old' "
                               '— and then the moment passes.',
                               "Time resumes normally. You hadn't noticed it had been moving "
                               "strangely until it didn't.",
                               '*Chronos does not die. It steps back. It will be here before '
                               'everything else again, when everything else ends.*',
                               '*It is choosing, now, to let you have this.*'],
                     'color': 'ancient'},
 'ancient_genesis': {'title': '🔥 The First Flame Dims',
                     'lines': ['The light that predates color fades to something the eye can '
                               'actually hold.',
                               'Ancient Genesis folds inward — not extinguished, but contained. '
                               'The flame that started everything becomes small enough to cup in '
                               'two hands.',
                               'Everything alive in the vicinity flickers, briefly, as if reminded '
                               'of something it was before it knew what it was.',
                               '*You did not kill the First Flame. That is not possible. You '
                               'simply proved you were worth sharing it with.*'],
                     'color': 'ancient'},
 'ancient_abyss': {'title': '🌑 The Darkness Recedes',
                   'lines': ['The light comes back. Not all at once — in edges, then corners, then '
                             'the middle of things.',
                             'Ancient Abyss does not retreat. It simply becomes less present, '
                             'pulling back into whatever it was before it chose to fill the room.',
                             'The silence changes again. It becomes ordinary silence — the kind '
                             'that just means no one is talking.',
                             '*The void before stars is still out there. It will be out there '
                             'after the stars are gone. It simply has no reason to be here '
                             'anymore.*',
                             '*You gave it a reason to leave. That is not nothing.*'],
                   'color': 'ancient'}}

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
        await get_or_create_player(interaction.user.id,str(interaction.user))
        cost=1000
        name=name.strip()
        if not 2<=len(name)<=40:return await interaction.followup.send("Choose a guild name of 2–40 characters.")
        try:
            async with storage.connect() as db:
                await db.execute("BEGIN IMMEDIATE")
                async with db.execute("SELECT 1 FROM guild_members WHERE user_id=? UNION SELECT 1 FROM guilds WHERE leader_id=?",(interaction.user.id,interaction.user.id)) as c:member=await c.fetchone()
                if member:raise ValueError("Leave your current guild before founding another.")
                paid=await db.execute("UPDATE players SET gold=gold-? WHERE user_id=? AND gold>=?",(cost,interaction.user.id,cost))
                if not paid.rowcount:raise ValueError("Creating a guild needs 1,000 gold.")
                inserted=await db.execute("INSERT INTO guilds(name,description,leader_id) VALUES(?,?,?)",(name,description[:500],interaction.user.id))
                guild_id=inserted.lastrowid
                await db.execute("INSERT INTO guild_members(guild_id,user_id,rank) VALUES(?,?,'leader')",(guild_id,interaction.user.id))
                await db.execute("UPDATE players SET guild_id=? WHERE user_id=?",(guild_id,interaction.user.id))
                await db.commit()
        except aiosqlite.IntegrityError:return await interaction.followup.send("That guild name is already taken.")
        except ValueError as error:return await interaction.followup.send(str(error))
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
        async with storage.connect() as db:
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
        async with storage.connect() as db:
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
            value=f"Level {next_level}: {next_level_info['unlocks']}" if next_level_info else f"Level {next_level}: Keep growing — more content coming!",
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
        async with storage.connect() as db:
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

        async with storage.connect() as db:
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
                async with storage.connect() as db:
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

    # ── /guild_promote ────────────────────────────────────────────────────
    @app_commands.command(name="guild_promote", description="Promote or demote a guild member 👑")
    @app_commands.describe(member="Member to promote/demote", rank="New rank to assign")
    @app_commands.choices(rank=[
        app_commands.Choice(name="⚔️ Officer", value="officer"),
        app_commands.Choice(name="🐾 Member",  value="member"),
    ])
    async def guild_promote(self, interaction: discord.Interaction, member: discord.Member, rank: str):
        await interaction.response.defer(ephemeral=True)
        uid = interaction.user.id

        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            # Verify caller is leader
            async with db.execute(
                "SELECT guild_id, rank FROM guild_members WHERE user_id = ?", (uid,)
            ) as c:
                caller = await c.fetchone()
            if not caller or caller["rank"] != "leader":
                return await interaction.followup.send(
                    "✦ Only the guild leader can promote or demote members.", ephemeral=True
                )
            guild_id = caller["guild_id"]

            # Verify target is in the same guild
            async with db.execute(
                "SELECT rank FROM guild_members WHERE guild_id = ? AND user_id = ?",
                (guild_id, member.id)
            ) as c:
                target = await c.fetchone()
            if not target:
                return await interaction.followup.send(
                    f"✦ **{member.display_name}** is not in your guild.", ephemeral=True
                )
            if target["rank"] == "leader":
                return await interaction.followup.send(
                    "✦ You cannot change the rank of the guild leader.", ephemeral=True
                )
            if target["rank"] == rank:
                return await interaction.followup.send(
                    f"✦ **{member.display_name}** is already {rank}.", ephemeral=True
                )

            await db.execute(
                "UPDATE guild_members SET rank = ? WHERE guild_id = ? AND user_id = ?",
                (rank, guild_id, member.id)
            )
            await db.commit()

        rank_label = "⚔️ Officer" if rank == "officer" else "🐾 Member"
        action = "promoted to" if rank == "officer" else "demoted to"
        await interaction.followup.send(embed=discord.Embed(
            description=f"✅ **{member.display_name}** has been {action} **{rank_label}**.",
            color=COLORS["success"]
        ), ephemeral=True)

    # ── /guild_leave ──────────────────────────────────────────────────────
    @app_commands.command(name="guild_leave", description="Leave your current guild 🚪")
    async def guild_leave(self, interaction: discord.Interaction):
        await interaction.response.defer()

        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT gm.rank, gm.guild_id, g.name, g.leader_id, g.member_count FROM guild_members gm JOIN guilds g ON gm.guild_id = g.id WHERE gm.user_id = ?",
                (interaction.user.id,)
            ) as c:
                member_row = await c.fetchone()

        if not member_row:
            # Check if players.guild_id is orphaned (membership row was deleted but player row wasn't cleaned)
            async with storage.connect() as db:
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
                        async with storage.connect() as db:
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
                async with storage.connect() as db:
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
                        async with storage.connect() as db:
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
                async with storage.connect() as db:
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

    # ── /contribute ──────────────────────────────────────────────────────
    @app_commands.command(name="contribute", description="Contribute to your guild and earn tickets 🎫")
    async def contribute(self, interaction: discord.Interaction):
        await interaction.response.defer()
        import time as _t, random as _r
        uid = interaction.user.id
        async with storage.connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT guild_id FROM guild_members WHERE user_id = ?", (uid,)) as c:
                member_row = await c.fetchone()
            if not member_row:
                return await interaction.followup.send(embed=discord.Embed(
                    description="❖ You are not in a guild. Use /guild_create or ask to be invited.",
                    color=COLORS["info"]
                ))
            guild_id = member_row["guild_id"]
            async with db.execute("SELECT contribute_last_at FROM players WHERE user_id = ?", (uid,)) as c:
                _pr = await c.fetchone()
            _last = float(_pr["contribute_last_at"]) if _pr and _pr["contribute_last_at"] else 0
            _remaining = 86400 - (_t.time() - _last)
            if _remaining > 0:
                _h, _rem = divmod(int(_remaining), 3600)
                _m = _rem // 60
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"⧗ Already contributed today. Next in **{_h}h {_m}m**.",
                    color=COLORS["info"]
                ))
            guild_exp_gain = _r.randint(80, 150)
            guild_token_gain = _r.randint(8, 15)
            player_tickets = _r.randint(5, 10)
            async with db.execute("SELECT * FROM guilds WHERE id = ?", (guild_id,)) as c:
                guild = dict(await c.fetchone())
            new_guild_exp = guild["exp"] + guild_exp_gain
            new_guild_level = guild["level"]
            EXP_PER_LEVEL = 500
            leveled_up = False
            while new_guild_exp >= new_guild_level * EXP_PER_LEVEL:
                new_guild_exp -= new_guild_level * EXP_PER_LEVEL
                new_guild_level += 1
                leveled_up = True
            capacity=max((p["max_members"] for level,p in GUILD_LEVEL_PERKS.items() if level<=new_guild_level),default=10)
            await db.execute("UPDATE guilds SET max_members=? WHERE id=?",(capacity,guild_id))
            await db.execute(
                "UPDATE guilds SET exp = ?, level = ?, guild_tokens = guild_tokens + ?, total_contributions = COALESCE(total_contributions, 0) + 1 WHERE id = ?",
                (new_guild_exp, new_guild_level, guild_token_gain, guild_id)
            )
            await db.execute(
                "UPDATE players SET guild_tickets = COALESCE(guild_tickets, 0) + ?, contribute_last_at = ? WHERE user_id = ?",
                (player_tickets, _t.time(), uid)
            )
            await db.execute(
                "UPDATE guild_members SET tickets_contributed = COALESCE(tickets_contributed, 0) + ? WHERE guild_id = ? AND user_id = ?",
                (player_tickets, guild_id, uid)
            )
            await db.commit()
            async with db.execute("SELECT guild_tickets FROM players WHERE user_id = ?", (uid,)) as c:
                _tp = await c.fetchone()
            total_tickets = _tp["guild_tickets"] if _tp else player_tickets
        LINES = [
            "*The guild acknowledges the effort. The Sanctuary feels slightly more solid.*",
            "*Something in the Sanctuary hums approvingly.*",
            "*Your contribution has been logged. The guild grows stronger.*",
            "*The Loom records the gesture. It records everything.*",
        ]
        desc = (
            f"*{_r.choice(LINES)}*\n\n"
            f"**Guild received:** +`{guild_exp_gain}` EXP · +`{guild_token_gain}` tokens\n"
            f"**You earned:** +`{player_tickets}` 🎫 Guild Tickets\n"
            f"**Your total:** `{total_tickets}` 🎫\n\n"
            f"*Guild EXP: `{new_guild_exp}/{new_guild_level * EXP_PER_LEVEL}`*"
        )
        if leveled_up:
            perk = GUILD_LEVEL_PERKS.get(new_guild_level, {})
            desc += f"\n\n🎉 **Guild leveled up to Lv.{new_guild_level}!**"
            if perk.get("unlocks"):
                desc += f"\n✨ *Unlocked: {perk['unlocks']}*"
        embed = discord.Embed(title="🏰 Guild Contribution", description=desc, color=COLORS["success"])
        embed.set_footer(text="ChibiBeasts 🐾  •  Contribute again in 24 hours · Spend tickets at /guild_shop")
        from utils.progress import track_quest_event
        await track_quest_event(uid,"guild_action")
        await interaction.followup.send(embed=embed)

    # ── /guild_shop ───────────────────────────────────────────────────────
    @app_commands.command(name="guild_shop", description="Spend guild tickets on exclusive items 🎫")
    async def guild_shop(self,interaction:discord.Interaction):
        await interaction.response.defer()
        from utils.shops import GUILD_ITEMS,purchase_guild
        uid=interaction.user.id
        embed=discord.Embed(title="🏰 Guild Shop",description="Earn tickets with /contribute. Purchases go to /inventory; activate with /use. Blueprints are spent by /build.",color=COLORS["legendary"])
        view=discord.ui.View(timeout=180)
        from utils.db import load_items
        items=load_items()
        for index,(id,(cost,level,description)) in enumerate(GUILD_ITEMS.items()):
            embed.add_field(name=f"{items[id]['name']} · {cost} tickets · Guild Lv{level}",value=description,inline=False)
            button=discord.ui.Button(label=items[id]["name"]+f" · {cost} tickets",style=discord.ButtonStyle.primary,row=index//3)
            async def buy(bi,key=id):
                if bi.user.id!=uid:return await bi.response.send_message("Not your shop.",ephemeral=True)
                await bi.response.defer(ephemeral=True)
                try:result=await purchase_guild(uid,key)
                except ValueError as error:result=str(error)
                await bi.followup.send(result,ephemeral=True)
            button.callback=buy;view.add_item(button)
        await interaction.followup.send(embed=embed,view=view)

    @app_commands.command(name="raid",description="Gather your guild to help a Corrupted beast ⚔️")
    async def raid(self,interaction:discord.Interaction):
        await interaction.response.defer()
        async with storage.connect() as db:
            db.row_factory=aiosqlite.Row
            async with db.execute("SELECT m.rank,g.* FROM guild_members m JOIN guilds g ON g.id=m.guild_id WHERE m.user_id=?",(interaction.user.id,)) as c:guild=await c.fetchone()
        if not guild or guild["rank"] not in {"leader","officer"}:return await interaction.followup.send("A guild leader or officer opens the raid.")
        if guild["level"]<5 or guild["guild_tokens"]<50:return await interaction.followup.send("Your guild needs level 5 and 50 guild tokens.")
        from utils.raids import open_raid
        try:await open_raid(interaction,random.choice(RAID_BOSSES["corrupted"]),guild_id=guild["id"])
        except ValueError as error:await interaction.followup.send(str(error))

async def setup(bot):await bot.add_cog(Guilds(bot))
