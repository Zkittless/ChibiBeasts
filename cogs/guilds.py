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

        active_raids[raid_id] = {
            "boss": boss, "current_hp": boss["max_hp"],
            "max_hp": boss["max_hp"], "participants": {},
            "guild_id": guild_data["id"], "channel": interaction.channel,
            "raid_message": None,
            "attack_counts": {},
            "embed_updating": False,
            "guild_members": set(),
            "last_attack": {},   # user_id -> monotonic timestamp, for cooldown
        }
        _raid_locks[raid_id] = asyncio.Lock()

        # Pre-cache guild members so button clicks don't need a DB round-trip
        async with aiosqlite.connect("db/chibibeast.db") as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT user_id FROM guild_members WHERE guild_id = ?", (guild_data["id"],)
            ) as c:
                rows = await c.fetchall()
        active_raids[raid_id]["guild_members"] = {r["user_id"] for r in rows}

        ATTACK_COOLDOWN = 1.5  # seconds between attacks per player

        def build_raid_embed(current_hp: int, participants: dict = None) -> discord.Embed:
            pct = current_hp / boss["max_hp"]
            status = "🔴 CRITICAL" if pct < 0.15 else "🟠 Weakened" if pct < 0.40 else "🟡 Damaged" if pct < 0.70 else "🟢 Active"
            embed = discord.Embed(
                title=f"⚔️ CORRUPTED RAID — {boss['name']}!",
                description=(
                    f"*{sundering_line}*\n\n"
                    f"**{interaction.guild.name}**, a **Corrupted** beast has emerged: **{boss['name']}**.\n"
                    f"*{boss['description']}*\n\n"
                    f"💀 **HP:** {hp_bar(current_hp, boss['max_hp'])} {status}\n"
                    f"`{current_hp:,} / {boss['max_hp']:,}`\n\n"
                    f"🏆 Top 3 damage dealers can catch the boss itself!"
                ),
                color=COLORS["epic"]
            )
            if participants:
                top = sorted(participants.items(), key=lambda x: x[1], reverse=True)[:5]
                medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
                lines = [f"{medals[i]} <@{uid}> — `{dmg:,}` dmg" for i, (uid, dmg) in enumerate(top)]
                embed.add_field(name="⚔️ Damage Dealt", value="\n".join(lines), inline=False)
            if boss.get("image_url"):
                embed.set_image(url=boss["image_url"])
            embed.set_footer(text=f"Raid ID: #{raid_id} | Triggered by {interaction.user.display_name} | 30 min timer")
            return embed

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

                # Per-player cooldown
                now = time.monotonic()
                last = raid["last_attack"].get(uid, 0)
                if now - last < ATTACK_COOLDOWN:
                    remaining = ATTACK_COOLDOWN - (now - last)
                    return await btn_interaction.followup.send(f"⏱️ Wait `{remaining:.1f}s`.", ephemeral=True)
                raid["last_attack"][uid] = now

                active = await get_active_beast(uid)
                if not active:
                    return await btn_interaction.followup.send("✦ You need an active beast! Use `/setactive`.", ephemeral=True)

                beast_data_btn = get_beast_data(active["beast_id"])
                damage = random.randint(int(active["attack"] * 0.8), int(active["attack"] * 1.5))
                is_crit = random.random() < 0.15
                if is_crit:
                    damage = int(damage * 1.5)

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
                    async with aiosqlite.connect("db/chibibeast.db") as db:
                        await db.execute("UPDATE raids SET current_hp = ? WHERE id = ?", (raid["current_hp"], raid_id))
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
                    raid_ended = raid["current_hp"] <= 0
                    current_hp_snap = raid["current_hp"]
                    participants_snap = dict(raid["participants"])

                # Deferred ephemeral with no followup simply never shows — no cleanup needed

                # Throttled public embed update with live leaderboard
                if not raid.get("embed_updating", False):
                    raid["embed_updating"] = True
                    raid_msg = raid.get("raid_message")
                    if raid_msg:
                        try:
                            await raid_msg.edit(
                                embed=build_raid_embed(current_hp_snap, participants_snap),
                                view=self if not raid_ended else None
                            )
                        except discord.HTTPException:
                            pass
                        finally:
                            if raid_id in active_raids:
                                active_raids[raid_id]["embed_updating"] = False

                await track_quest_event(uid, "raid_damage", amount=damage)
                await advance_quest_step(uid, "raid_participate")

                if raid_ended:
                    await self.end_raid(raid_id, btn_interaction.channel)

            async def on_timeout(self):
                if raid_id in active_raids:
                    await self.end_raid(raid_id, interaction.channel, timed_out=True)

        view = RaidView()
        raid_msg = await interaction.followup.send(embed=build_raid_embed(boss["max_hp"], {}), view=view)
        active_raids[raid_id]["raid_message"] = raid_msg

        # Auto-end raid after 30 minutes
        await asyncio.sleep(1800)
        if raid_id in active_raids:
            await self.end_raid(raid_id, interaction.channel, timed_out=True)

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
            if not member:
                continue
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
                    await notify_unlocks(channel, member, all_raid_unlocks)

                # Loot drop
                if random.random() < (0.8 - (i * 0.1)):
                    loot = random.choice(boss["loot_table"])
                    from utils.db import add_item
                    await add_item(user_id, loot)
                    reward_lines.append(f"{'🥇' if rank==1 else '🥈' if rank==2 else '🥉' if rank==3 else '🏅'} **{member.display_name}** — `{damage:,}` dmg | +{gold}💰 | +{player_tokens}🎟️ | 🎁 {loot.replace('_',' ').title()}")
                else:
                    reward_lines.append(f"{'🥇' if rank==1 else '🥈' if rank==2 else '🥉' if rank==3 else '🏅'} **{member.display_name}** — `{damage:,}` dmg | +{gold}💰 | +{player_tokens}🎟️")

                # ── Raid boss catch chance for top 3 ─────────────────────────
                # Corrupted raids: rank 1=5%, rank 2=3%, rank 3=2%
                # Corrupted raid catch chance for top 3: rank 1=5%, rank 2=3%, rank 3=2%
                # Ancient raids are handled separately in cogs/ancient.py
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
                                    f"🌟 **{member.display_name}** has caught **{boss_beast_data['name']}** — *{boss_beast_data['title']}*!\n\n"
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
