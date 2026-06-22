import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from utils.db import (
    get_or_create_player, get_player, update_player,
    get_player_beasts, get_active_beast, get_inventory,
    add_item, remove_item, load_beasts, load_items, load_perks,
    get_beast_data, calc_exp_for_level, calc_player_exp_for_level,
    get_perk_slots, apply_beast_levelup, get_beast_exp_for_level
)
from utils.theme import COLORS, RARITY_EMOJI, RARITY_LABEL, TYPE_EMOJI, hp_bar, exp_bar, fmt_stats, SPARKLE
from utils.progress import track_quest_event, notify_quest_completions
from utils.dispositions import disposition_display

class Profile(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="profile", description="View your trainer profile 📖")
    @app_commands.describe(member="View another trainer's profile")
    async def profile(self, interaction: discord.Interaction, member: discord.Member = None):
        target = member or interaction.user
        await interaction.response.defer()
        player = await get_or_create_player(target.id, str(target))
        beasts = await get_player_beasts(target.id)
        active = await get_active_beast(target.id)

        exp_needed = calc_player_exp_for_level(player["level"])
        beast_count = len(beasts)
        rarity_counts = {}
        for b in beasts:
            rarity_counts[b["rarity"]] = rarity_counts.get(b["rarity"], 0) + 1

        collection_str = " ".join(
            f"{RARITY_EMOJI.get(r, '⚪')}`{c}`"
            for r, c in sorted(rarity_counts.items(), key=lambda x: ["common","uncommon","rare","epic","legendary","divine","altered_divine"].index(x[0]) if x[0] in ["common","uncommon","rare","epic","legendary","divine","altered_divine"] else 99)
        ) or "No beasts yet!"

        title_line = f"🏷️ **Title:** *{player.get('title', 'None')}*\n" if player.get('title') else ""

        embed = discord.Embed(
            title=f"🐾 {target.display_name}'s Trainer Profile",
            description=title_line,
            color=COLORS["divine"]
        )
        embed.add_field(
            name="📊 Trainer Stats",
            value=(
                f"⭐ **Level:** {player['level']}\n"
                f"✨ **EXP:** {exp_bar(player['exp'], exp_needed)}\n"
                f"💰 **Gold:** `{player['gold']:,}`\n"
                f"💎 **Celestial Shards:** `{player['celestial_shards']}` — *spend at `/shard_shop`*\n"
                f"🎟️ **Guild Tokens:** `{player['guild_tokens']}`"
            ),
            inline=False
        )
        embed.add_field(
            name="⚔️ Battle Record",
            value=f"✨ Victories: `{player['wins']}` | 💤 Lessons: `{player['losses']}`",
            inline=True
        )
        embed.add_field(
            name="🐾 Collection",
            value=f"**{beast_count}** beasts total\n{collection_str}",
            inline=True
        )
        if active:
            beast_data = get_beast_data(active["beast_id"])
            if beast_data:
                name = active["nickname"] or beast_data["name"]
                embed.add_field(
                    name="⚔️ Active Beast",
                    value=(
                        f"{RARITY_EMOJI.get(active['rarity'], '⚪')} **{name}** — Lv.{active['level']}\n"
                        f"❤️ {hp_bar(active['hp'], active['max_hp'])}"
                    ),
                    inline=False
                )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text="ChibiBeasts 🐾  •  /collection to see all beasts")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="collection", description="View your ChibiBeast collection 🐾")
    @app_commands.describe(page="Page number", rarity="Filter by rarity")
    @app_commands.choices(rarity=[
        app_commands.Choice(name="All", value="all"),
        app_commands.Choice(name="⚪ Common", value="common"),
        app_commands.Choice(name="🟢 Uncommon", value="uncommon"),
        app_commands.Choice(name="🔵 Rare", value="rare"),
        app_commands.Choice(name="🟣 Epic", value="epic"),
        app_commands.Choice(name="🟡 Legendary", value="legendary"),
        app_commands.Choice(name="🌸 Divine", value="divine"),
        app_commands.Choice(name="⚠️ Altered Divine", value="altered_divine"),
    ])
    async def collection(self, interaction: discord.Interaction, page: int = 1, rarity: str = "all"):
        await interaction.response.defer()
        beasts = await get_player_beasts(interaction.user.id)
        if rarity != "all":
            beasts = [b for b in beasts if b["rarity"] == rarity]

        if not beasts:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ No beasts found! Use `/hatch` or `/explore` to find some.",
                color=COLORS["info"]
            ))

        per_page = 8
        total_pages = max(1, (len(beasts) + per_page - 1) // per_page)
        page = max(1, min(page, total_pages))

        def build_page(p: int) -> discord.Embed:
            page_beasts = beasts[(p - 1) * per_page : p * per_page]
            embed = discord.Embed(
                title=f"🐾 {interaction.user.display_name}'s Collection",
                description=f"**{len(beasts)}** beasts total | Page {p}/{total_pages}",
                color=COLORS["divine"]
            )
            for b in page_beasts:
                beast_data = get_beast_data(b["beast_id"])
                if not beast_data:
                    continue
                name = b["nickname"] or beast_data["name"]
                rarity_emoji = RARITY_EMOJI.get(b["rarity"], "⚪")
                type_emoji = TYPE_EMOJI.get(beast_data["type"], "❓")
                active_tag = " ⚔️ **ACTIVE**" if b["is_active"] else ""
                fav_tag = " ⭐" if b["is_favorite"] else ""
                embed.add_field(
                    name=f"{rarity_emoji} {name} — Lv.{b['level']}{active_tag}{fav_tag}",
                    value=f"{type_emoji} {beast_data['type'].capitalize()} | ❤️ {b['hp']}/{b['max_hp']} | #{b.get('player_number') or b['id']}",
                    inline=True
                )
            embed.set_footer(text=f"ChibiBeasts 🐾  •  /beastinfo <id> to view details  •  Page {p}/{total_pages}")
            return embed

        # No buttons needed for single-page collections
        if total_pages == 1:
            return await interaction.followup.send(embed=build_page(1))

        class CollectionView(discord.ui.View):
            def __init__(self, current: int):
                super().__init__(timeout=120)
                self.page = current
                self._update_buttons()

            def _update_buttons(self):
                self.prev_btn.disabled = self.page <= 1
                self.next_btn.disabled = self.page >= total_pages
                self.prev_btn.label = f"◀ Page {self.page - 1}" if self.page > 1 else "◀"
                self.next_btn.label = f"Page {self.page + 1} ▶" if self.page < total_pages else "▶"

            @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("This isn't your collection!", ephemeral=True)
                self.page -= 1
                self._update_buttons()
                await btn_interaction.response.edit_message(embed=build_page(self.page), view=self)

            @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("This isn't your collection!", ephemeral=True)
                self.page += 1
                self._update_buttons()
                await btn_interaction.response.edit_message(embed=build_page(self.page), view=self)

        view = CollectionView(page)
        await interaction.followup.send(embed=build_page(page), view=view)

    @app_commands.command(name="beastinfo", description="View detailed info about one of your beasts 🔍")
    @app_commands.describe(beast_number="Your beast number from /collection (leave blank for active beast)")
    async def beastinfo(self, interaction: discord.Interaction, beast_number: int = None):
        await interaction.response.defer()

        # Load all player beasts ordered by player_number
        all_beasts = await get_player_beasts(interaction.user.id)
        if not all_beasts:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You don't have any beasts yet!", color=COLORS["error"]
            ))

        # Resolve which beast to show
        if beast_number is None:
            # Default to active beast
            beast_row = next((b for b in all_beasts if b["is_active"]), all_beasts[0])
        else:
            beast_row = next(
                (b for b in all_beasts if b.get("player_number") == beast_number),
                None
            )
            # Fallback for old beasts without player_number — find by position
            if not beast_row:
                beast_row = next(
                    (b for b in all_beasts if b["id"] == beast_number),
                    None
                )
            if not beast_row:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Beast `#{beast_number}` not found in your collection!",
                    color=COLORS["error"]
                ))

        def build_embed(row: dict) -> discord.Embed:
            beast_data = get_beast_data(row["beast_id"])
            if not beast_data:
                return discord.Embed(description="✦ Beast data not found!", color=COLORS["error"])
            name = row["nickname"] or beast_data["name"]
            rarity = row["rarity"]
            exp_needed = get_beast_exp_for_level(dict(row), row["level"])
            num = row.get("player_number") or f"#{row['id']}"
            active_tag = " ⚔️" if row["is_active"] else ""
            embed = discord.Embed(
                title=f"{RARITY_EMOJI.get(rarity,'⚪')} {name}{active_tag}",
                description=f"*{beast_data['title']}*\n{beast_data['description']}",
                color=COLORS.get(rarity, COLORS["info"])
            )
            embed.add_field(name="📊 Stats", value=fmt_stats(row), inline=True)
            embed.add_field(
                name="📈 Progress",
                value=(
                    f"⭐ Level: `{row['level']}`\n"
                    f"✨ EXP: {exp_bar(row['exp'], exp_needed)}\n"
                    f"😊 Happiness: `{row['happiness']}/100`"
                ),
                inline=True
            )
            embed.add_field(
                name="⚡ Moves",
                value="\n".join(f"• {m}" for m in beast_data["moves"]) + f"\n🌟 **Ultimate:** {beast_data['ultimate']}",
                inline=False
            )
            embed.add_field(name="🎭 Disposition", value=disposition_display(row.get("disposition")), inline=False)
            if beast_data.get("divine_passive"):
                dp = beast_data["divine_passive"]
                passive_label = {
                    "divine": "✨ Divine Passive", "altered_divine": "⚠️ Altered Passive",
                    "corrupted": "🖤 Corrupted Passive", "ancient": "🏛️ Ancient Passive",
                }.get(rarity, "✨ Special Passive")
                embed.add_field(name=f"{passive_label}: **{dp['passive_name']}**", value=f"*{dp['passive_desc']}*", inline=False)
            if beast_data.get("starter"):
                embed.add_field(
                    name="🏛️ Origin",
                    value=f"*{beast_data.get('starter_house', 'Unknown House')} — {beast_data.get('starter_flavor', '')}*",
                    inline=False
                )
            if beast_data.get("image_url"):
                embed.set_image(url=beast_data["image_url"])
            embed.set_footer(text=f"Beast #{num} of {len(all_beasts)} | Caught via: {row['caught_from']}")
            return embed

        # Navigation buttons
        current_idx = next((i for i, b in enumerate(all_beasts) if b["id"] == beast_row["id"]), 0)

        class GoToModal(discord.ui.Modal, title="Go to Beast #"):
            number = discord.ui.TextInput(
                label=f"Beast number (1 – {len(all_beasts)})",
                placeholder=f"e.g. 5",
                min_length=1, max_length=5, required=True
            )
            def __init__(self, view):
                super().__init__()
                self._view = view

            async def on_submit(self, modal_interaction: discord.Interaction):
                raw = self.number.value.strip()
                try:
                    target_num = int(raw)
                except ValueError:
                    return await modal_interaction.response.send_message("✦ Enter a whole number.", ephemeral=True)
                # Find beast by player_number
                idx = next((i for i, b in enumerate(all_beasts) if b.get("player_number") == target_num), None)
                # Fallback: find by position
                if idx is None:
                    idx = next((i for i, b in enumerate(all_beasts) if b["id"] == target_num), None)
                if idx is None or idx < 0 or idx >= len(all_beasts):
                    return await modal_interaction.response.send_message(
                        f"✦ Beast `#{target_num}` not found in your collection.", ephemeral=True
                    )
                self._view.idx = idx
                self._view._update()
                await modal_interaction.response.edit_message(embed=build_embed(all_beasts[idx]), view=self._view)

        class BeastInfoView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.idx = current_idx
                self._update()

            def _update(self):
                self.prev_btn.disabled = self.idx <= 0
                self.next_btn.disabled = self.idx >= len(all_beasts) - 1
                self.prev_btn.label = f"◀ #{all_beasts[self.idx-1].get('player_number', self.idx)}" if self.idx > 0 else "◀"
                self.next_btn.label = f"#{all_beasts[self.idx+1].get('player_number', self.idx+2)} ▶" if self.idx < len(all_beasts)-1 else "▶"

            @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
            async def prev_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("This isn't your collection!", ephemeral=True)
                self.idx -= 1
                self._update()
                await btn_interaction.response.edit_message(embed=build_embed(all_beasts[self.idx]), view=self)

            @discord.ui.button(label="Go to #", style=discord.ButtonStyle.secondary, emoji="🔍")
            async def goto_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("This isn't your collection!", ephemeral=True)
                await btn_interaction.response.send_modal(GoToModal(self))

            @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
            async def next_btn(self, btn_interaction: discord.Interaction, button: discord.ui.Button):
                if btn_interaction.user.id != interaction.user.id:
                    return await btn_interaction.response.send_message("This isn't your collection!", ephemeral=True)
                self.idx += 1
                self._update()
                await btn_interaction.response.edit_message(embed=build_embed(all_beasts[self.idx]), view=self)

        view = BeastInfoView()
        await interaction.followup.send(embed=build_embed(beast_row), view=view)

    @app_commands.command(name="setactive", description="Set a beast as your active battle beast ⚔️")
    @app_commands.describe(beast_number="Your beast number from /collection")
    async def setactive(self, interaction: discord.Interaction, beast_number: int):
        await interaction.response.defer()
        async with aiosqlite.connect("db/chibibeast.db") as db:
            async with db.execute(
                "SELECT id FROM player_beasts WHERE player_number = ? AND user_id = ?",
                (beast_number, interaction.user.id)
            ) as cursor:
                exists = await cursor.fetchone()
            if not exists:
                # Fallback: try matching old global id
                async with db.execute(
                    "SELECT id FROM player_beasts WHERE id = ? AND user_id = ?",
                    (beast_number, interaction.user.id)
                ) as cursor:
                    exists = await cursor.fetchone()
            if not exists:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Beast `#{beast_number}` not found in your collection!", color=COLORS["error"]
                ))
            row_id = exists[0]
            await db.execute("UPDATE player_beasts SET is_active = 0 WHERE user_id = ?", (interaction.user.id,))
            await db.execute("UPDATE player_beasts SET is_active = 1 WHERE id = ?", (row_id,))
            await db.commit()
        await interaction.followup.send(embed=discord.Embed(
            description=f"✦ Beast `#{beast_number}` is now your active beast! ⚔️",
            color=COLORS["success"]
        ))

    @app_commands.command(name="nickname", description="Give your beast a nickname 💬")
    @app_commands.describe(beast_number="Your beast number from /collection", name="New nickname (max 20 chars)")
    async def nickname(self, interaction: discord.Interaction, beast_number: int, name: str):
        if len(name) > 20:
            return await interaction.response.send_message(embed=discord.Embed(
                description="✦ Nickname must be 20 characters or less!", color=COLORS["error"]
            ), ephemeral=True)
        async with aiosqlite.connect("db/chibibeast.db") as db:
            async with db.execute(
                "SELECT id FROM player_beasts WHERE player_number = ? AND user_id = ?",
                (beast_number, interaction.user.id)
            ) as c:
                exists = await c.fetchone()
            if not exists:
                async with db.execute(
                    "SELECT id FROM player_beasts WHERE id = ? AND user_id = ?",
                    (beast_number, interaction.user.id)
                ) as c:
                    exists = await c.fetchone()
            if not exists:
                return await interaction.response.send_message(embed=discord.Embed(
                    description=f"✦ Beast `#{beast_number}` not found!", color=COLORS["error"]
                ), ephemeral=True)
            row_id = exists[0]
            await db.execute("UPDATE player_beasts SET nickname = ? WHERE id = ?", (name, row_id))
            await db.commit()
        await interaction.response.send_message(embed=discord.Embed(
            description=f"✦ Beast `#{beast_number}` has been named **{name}**! 💬",
            color=COLORS["success"]
        ))

class Inventory(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="inventory", description="View your item inventory 🎒")
    async def inventory(self, interaction: discord.Interaction):
        await interaction.response.defer()
        inv = await get_inventory(interaction.user.id)
        items_data = load_items()

        # Build egg lookup from both instant and incubation egg definitions
        from cogs.hatch import HATCH_EGGS
        from cogs.world import EGGS as INCUB_EGGS
        egg_lookup = {}
        for eid, egg in HATCH_EGGS.items():
            egg_lookup[eid] = {"name": egg["name"], "description": egg["flavor"], "rarity": "rare", "emoji": "🥚"}
        for eid, egg in INCUB_EGGS.items():
            egg_lookup[eid] = {"name": egg["name"], "description": egg["flavor"], "rarity": egg["rarity"], "emoji": egg.get("emoji", "🥚")}

        if not inv:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Your inventory is empty! Visit the `/shop` to buy items.",
                color=COLORS["info"]
            ))

        embed = discord.Embed(title="🎒 Your Inventory", color=COLORS["info"])
        for entry in inv:
            item = items_data.get(entry["item_id"]) or egg_lookup.get(entry["item_id"])
            if not item:
                continue
            rarity_emoji = RARITY_EMOJI.get(item.get("rarity", "common"), "⚪")
            name_emoji = item.get("emoji", "")
            embed.add_field(
                name=f"{rarity_emoji} {name_emoji} {item['name']} x{entry['quantity']}",
                value=item["description"][:80] + ("..." if len(item["description"]) > 80 else ""),
                inline=False
            )
        embed.set_footer(text="ChibiBeasts 🐾  •  /use <item> to use · /hatch for instant eggs · /incubate for timed eggs")
        await interaction.followup.send(embed=embed)

    async def use_autocomplete(self, interaction: discord.Interaction, current: str):
        inv = await get_inventory(interaction.user.id)
        items_data = load_items()
        choices = []
        for row in inv:
            item = items_data.get(row["item_id"])
            if not item:
                continue
            if current.lower() in item["name"].lower():
                qty = f" (x{row['quantity']})" if row["quantity"] > 1 else ""
                choices.append(app_commands.Choice(name=f"{item['name']}{qty}", value=row["item_id"]))
        return choices[:25]

    @app_commands.command(name="use", description="Use an item from your inventory 💊")
    @app_commands.describe(item_name="Item to use")
    @app_commands.autocomplete(item_name=use_autocomplete)
    async def use(self, interaction: discord.Interaction, item_name: str):
        await interaction.response.defer()
        items_data = load_items()
        item_id = item_name.lower().replace(" ", "_").replace("-", "_")
        item = items_data.get(item_id)

        if not item:
            matches = [i for i in items_data.values() if item_name.lower() in i["name"].lower()]
            if matches:
                item = matches[0]
                item_id = item["id"]
            else:
                return await interaction.followup.send(embed=discord.Embed(
                    description=f"✦ Item `{item_name}` not found!", color=COLORS["error"]
                ))

        inv = await get_inventory(interaction.user.id)
        has_item = any(e["item_id"] == item_id and e["quantity"] > 0 for e in inv)
        if not has_item:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ You don't have **{item['name']}** in your inventory!",
                color=COLORS["error"]
            ))

        active = await get_active_beast(interaction.user.id)
        if not active and item["type"] not in ["cooldown", "unlock", "reset"]:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ You need an active beast to use this item!", color=COLORS["error"]
            ))

        effect = item["effect"]
        result_lines = []

        async with aiosqlite.connect("db/chibibeast.db") as db:
            if "heal_percent" in effect and active:
                heal = int(active["max_hp"] * (effect["heal_percent"] / 100))
                new_hp = min(active["max_hp"], active["hp"] + heal)
                await db.execute("UPDATE player_beasts SET hp = ? WHERE id = ?", (new_hp, active["id"]))
                result_lines.append(f"❤️ Healed **{heal} HP** ({active['hp']} → {new_hp})")

            if "revive" in effect and active and active["hp"] <= 0:
                heal = int(active["max_hp"] * (effect.get("heal_percent", 50) / 100))
                await db.execute("UPDATE player_beasts SET hp = ? WHERE id = ?", (heal, active["id"]))
                result_lines.append(f"🔥 Revived with **{heal} HP**!")

            if "restore_mana_percent" in effect and active:
                restore = int(active["max_mana"] * (effect["restore_mana_percent"] / 100))
                new_mana = min(active["max_mana"], active["mana"] + restore)
                await db.execute("UPDATE player_beasts SET mana = ? WHERE id = ?", (new_mana, active["id"]))
                result_lines.append(f"💠 Restored **{restore} Mana**!")

            if "happiness" in effect and active:
                new_hap = min(100, active["happiness"] + effect["happiness"])
                await db.execute("UPDATE player_beasts SET happiness = ? WHERE id = ?", (new_hap, active["id"]))
                result_lines.append(f"😊 Happiness increased to **{new_hap}/100**!")

            if "exp" in effect and active:
                new_exp = active["exp"] + effect["exp"]
                new_level = active["level"]
                while new_exp >= get_beast_exp_for_level(active, new_level):
                    new_exp -= get_beast_exp_for_level(active, new_level)
                    new_level += 1
                await apply_beast_levelup(db, active, new_level, new_exp)
                result_lines.append(f"✨ Gained **{effect['exp']} EXP**!")
                if new_level > active["level"]:
                    result_lines.append(f"⬆️ **LEVEL UP!** Now Lv.{new_level}! Stats increased!")

            if "instant_levels" in effect and active:
                target_level = active["level"] + effect["instant_levels"]
                await apply_beast_levelup(db, active, target_level, 0)
                result_lines.append(f"⬆️ Leveled up **{effect['instant_levels']} levels** → Lv.{target_level}! Stats increased!")

            # Chrono-Biscuit: instantly ready the oldest incubating egg
            if item_id == "chrono_biscuit":
                async with db.execute(
                    "SELECT id, egg_name FROM incubating_eggs WHERE user_id = ? AND hatched = 0 ORDER BY started_at ASC LIMIT 1",
                    (interaction.user.id,)
                ) as c:
                    egg_row = await c.fetchone()
                if egg_row:
                    await db.execute(
                        "UPDATE incubating_eggs SET ready_at = datetime('now', '-1 minute') WHERE id = ?",
                        (egg_row[0],)
                    )
                    result_lines.append(f"⏰ **{egg_row[1]}** is now ready to hatch! Use `/hatchegg`.")
                else:
                    result_lines.append("⏰ No eggs currently incubating.")

            # Star-Candy Shards: +3 to a random combat stat permanently
            if "stat_boost" in effect and active:
                import random as _rand
                stat_choices = ["attack", "defense", "speed"]
                boosted_stat = _rand.choice(stat_choices)
                boost_amt = effect.get("stat_boost", 3)
                await db.execute(
                    f"UPDATE player_beasts SET {boosted_stat} = {boosted_stat} + ? WHERE id = ?",
                    (boost_amt, active["id"])
                )
                result_lines.append(f"⭐ **{boosted_stat.capitalize()}** permanently increased by **+{boost_amt}**!")


            # Aether Tonic — cure_all
            if "cure_all" in effect:
                result_lines.append("✨ All status conditions cleared! Effect persists into next battle.")

            # Sugarsprout Cupcake — temp speed boost flag
            if "speed_boost_percent" in effect and active:
                boost = int(active["speed"] * (effect["speed_boost_percent"] / 100))
                result_lines.append(f"💨 Speed boosted by **+{boost}** for your next battle!")
                await db.execute("UPDATE players SET brew_active = brew_active + 1 WHERE user_id = ?", (interaction.user.id,))

            # Spellbound Incense — encounter boost for 30 mins
            if "encounter_boost" in effect:
                import time as _time
                duration_mins = effect.get("duration_minutes", 30)
                until = _time.time() + (duration_mins * 60)
                await db.execute("UPDATE players SET incense_active_until = ? WHERE user_id = ?", (until, interaction.user.id))
                result_lines.append(f"🌿 Encounter boost active for **{duration_mins} minutes**! Uncommon and Rare beasts more likely in `/explore`.")

            # Krakenshale Brew — double defense next battle
            if "defense_multiplier" in effect:
                await db.execute("UPDATE players SET brew_active = brew_active + 2 WHERE user_id = ?", (interaction.user.id,))
                result_lines.append("🛡️ **Defense doubled** for your next battle! (Krakenshale Brew)")

            # Tear of Leviathan — stat reset to base at current level
            if "reset_stats" in effect and active:
                from utils.db import load_beasts as _lb, calc_stat_growth as _csg
                all_b = _lb()
                bbase = all_b.get(active["beast_id"], {}).get("base_stats", {})
                if bbase:
                    growth = _csg(dict(active), active["level"] - 1)
                    await db.execute("""UPDATE player_beasts SET max_hp=?, hp=?, attack=?, defense=?, speed=?, mana=?, max_mana=? WHERE id=?""",
                        (bbase["hp"]+growth["hp"], bbase["hp"]+growth["hp"],
                         bbase["attack"]+growth["attack"], bbase["defense"]+growth["defense"],
                         bbase["speed"]+growth["speed"], bbase["mana"]+growth["mana"],
                         bbase["mana"]+growth["mana"], active["id"]))
                    result_lines.append("💎 **Stats fully reset** to optimal base values for current level!")

            # Genesis Fruit — unlock divine_trait_slot
            if "unlock_divine_trait" in effect and active:
                await db.execute("UPDATE player_beasts SET divine_trait = 'unlocked' WHERE id = ?", (active["id"],))
                result_lines.append("🌈 **Divine Trait Slot unlocked** on this beast!")
                result_lines.append("*Something stayed behind when the fruit disappeared.*")

            # Sunforge Core — evolve if level met
            if "evolution_trigger" in effect and active:
                from utils.db import load_beasts as _lb2, calc_stat_growth as _csg2
                all_b2 = _lb2()
                bdata = all_b2.get(active["beast_id"], {})
                evo = bdata.get("evolution")
                if evo and evo.get("method") == "sunforge_core":
                    lvl_req = evo.get("level_required", 1)
                    if active["level"] >= lvl_req:
                        tid = evo["evolves_to"]
                        tdata = all_b2.get(tid, {})
                        ns = tdata.get("base_stats", {})
                        g2 = _csg2({"rarity": tdata.get("rarity","common")}, active["level"]-1)
                        await db.execute("""UPDATE player_beasts SET beast_id=?,rarity=?,max_hp=?,hp=?,attack=?,defense=?,speed=?,mana=?,max_mana=? WHERE id=?""",
                            (tid, tdata.get("rarity","common"),
                             ns.get("hp",100)+g2["hp"], ns.get("hp",100)+g2["hp"],
                             ns.get("attack",50)+g2["attack"], ns.get("defense",50)+g2["defense"],
                             ns.get("speed",50)+g2["speed"], ns.get("mana",50)+g2["mana"],
                             ns.get("mana",50)+g2["mana"], active["id"]))
                        result_lines.append(f"🌟 **{bdata.get('name','Beast')}** evolved into **{tdata.get('name','?')}**!")
                        if tdata.get("divine_passive"):
                            result_lines.append(f"✨ New passive: **{tdata['divine_passive']['passive_name']}**!")
                    else:
                        result_lines.append(f"✦ Needs to be Level **{lvl_req}** to evolve. Currently Lv.{active['level']}.")
                elif evo:
                    result_lines.append(f"✦ This beast needs **{evo.get('method','?').replace('_',' ').title()}** to evolve, not a Sunforge Core.")
                else:
                    result_lines.append("✦ This beast has no evolution path.")

            await db.commit()

        await remove_item(interaction.user.id, item_id)
        result_text = "\n".join(result_lines) if result_lines else "Item used!"
        await interaction.followup.send(embed=discord.Embed(
            title=f"✦ Used {item['name']}",
            description=result_text,
            color=COLORS["success"]
        ))

class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="shop", description="Browse the ChibiBeasts shop 🏪")
    @app_commands.describe(category="What to browse")
    @app_commands.choices(category=[
        app_commands.Choice(name="⚡ Instant Eggs",     value="instant"),
        app_commands.Choice(name="⏱️ Incubation Eggs",  value="incubation"),
        app_commands.Choice(name="🎒 Items",             value="items"),
    ])
    async def shop(self, interaction: discord.Interaction, category: str = "instant"):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        items_data = load_items()
        RARITY_ORDER = ["common", "uncommon", "rare", "epic", "legendary", "divine", "altered_divine"]

        # ── Atomic purchase helper — quantity already resolved before calling ─
        async def _do_purchase(bi: discord.Interaction, item_id: str, price: int, display_name: str, next_step: str, quantity: int = 1):
            await bi.response.defer(ephemeral=True)
            total_price = price * quantity
            async with aiosqlite.connect("db/chibibeast.db") as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT gold FROM players WHERE user_id = ?", (bi.user.id,)) as c:
                    pr = await c.fetchone()
                if not pr or pr["gold"] < total_price:
                    return await bi.followup.send(
                        f"✦ You need `{total_price:,}g` but only have `{pr['gold'] if pr else 0:,}g`.",
                        ephemeral=True
                    )
                cur = await db.execute(
                    "UPDATE players SET gold = gold - ? WHERE user_id = ? AND gold >= ?",
                    (total_price, bi.user.id, total_price)
                )
                if cur.rowcount == 0:
                    await db.rollback()
                    return await bi.followup.send(
                        "✦ Purchase failed — gold changed between clicks. Try again.", ephemeral=True
                    )
                async with db.execute(
                    "SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?",
                    (bi.user.id, item_id)
                ) as c:
                    inv = await c.fetchone()
                if inv:
                    await db.execute("UPDATE player_inventory SET quantity = quantity + ? WHERE id = ?", (quantity, inv["id"]))
                else:
                    await db.execute(
                        "INSERT INTO player_inventory (user_id, item_id, quantity) VALUES (?,?,?)",
                        (bi.user.id, item_id, quantity)
                    )
                await db.commit()
                new_gold = pr["gold"] - total_price
            qty_str = f"`{quantity}x` " if quantity > 1 else ""
            await bi.followup.send(
                f"✅ Purchased {qty_str}**{display_name}**!\n`{total_price:,}g` spent · Balance: `{new_gold:,}g`\n{next_step}",
                ephemeral=True
            )
            from utils.progress import track_quest_event, notify_quest_completions
            completed = await track_quest_event(bi.user.id, "spend_gold", amount=total_price)
            if completed and bi.channel:
                await notify_quest_completions(bi.channel, completed)

        # ── Quantity modal helper — wraps _do_purchase with a qty prompt ───
        async def _buy_with_qty(bi: discord.Interaction, item_id: str, price: int, display_name: str, next_step: str, max_qty: int = 99):
            from utils.modals import QuantityModal
            async def on_submit(modal_bi: discord.Interaction, qty: int):
                await _do_purchase(modal_bi, item_id, price, display_name, next_step, qty)
            modal = QuantityModal(
                title=f"Buy {display_name}",
                item_name=display_name,
                max_quantity=max_qty,
                callback=on_submit
            )
            # Override label to show per-unit price
            modal.quantity_input.label = f"Quantity ({price:,}g each)"
            modal.quantity_input.placeholder = f"Enter amount (e.g. 5)"
            await bi.response.send_modal(modal)

        # ══════════════════════════════════════════════════════════════════
        # INSTANT EGGS TAB
        # ══════════════════════════════════════════════════════════════════
        if category == "instant":
            from cogs.hatch import HATCH_EGGS, _BASE_EGG_POOLS
            INSTANT_EGGS = [
                ("🥚 Common Egg",      200,   "common_egg"),
                ("🥚✨ Rare Egg",      1500,  "rare_egg"),
                ("🌌🥚 Celestial Egg", 8000,  "celestial_egg"),
                ("🌊💎 Abyssal Egg",   25000, "abyssal_egg"),
            ]
            embed = discord.Embed(
                title="🏪 Shop — ⚡ Instant Eggs",
                description=(
                    f"💰 Your gold: `{player['gold']:,}`\n\n"
                    f"Buy below — then use `/hatch` and select the egg to open it immediately.\n"
                    f"No waiting. No timers. Just click and hatch.\n\u200b"
                ),
                color=COLORS["legendary"]
            )
            for name, price, egg_id in INSTANT_EGGS:
                pool = {k: v for k, v in _BASE_EGG_POOLS.get(egg_id, {}).items() if k != "altered_chance"}
                pool_str = " · ".join(
                    f"{RARITY_EMOJI.get(r,'⚪')} {int(v*100)}%"
                    for r, v in sorted(pool.items(), key=lambda x: RARITY_ORDER.index(x[0]) if x[0] in RARITY_ORDER else 99)
                )
                altered = _BASE_EGG_POOLS.get(egg_id, {}).get("altered_chance", 0)
                if altered:
                    pool_str += f" · ⚠️ Altered {int(altered*100)}%"
                embed.add_field(
                    name=f"{name} — `{price:,}g`",
                    value=pool_str,
                    inline=False
                )
            embed.set_footer(text="ChibiBeasts 🐾  •  Click a button to buy instantly")

            class InstantEggView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=120)
                    for row_idx, (name, price, egg_id) in enumerate(INSTANT_EGGS):
                        short = name.replace("🥚","").replace("✨","").replace("🌌","").replace("🌊💎","").strip()
                        next_step = "Use `/hatch` and select this egg to open it!"
                        btn1 = discord.ui.Button(
                            label=short,
                            style=discord.ButtonStyle.primary,
                            emoji="🥚",
                            row=row_idx
                        )
                        async def cb1(bi: discord.Interaction, _id=egg_id, _p=price, _n=name, _ns=next_step):
                            if bi.user.id != interaction.user.id:
                                return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns, quantity=1)
                        btn1.callback = cb1
                        self.add_item(btn1)
                        btn5 = discord.ui.Button(
                            label="+5",
                            style=discord.ButtonStyle.secondary,
                            row=row_idx
                        )
                        async def cb5(bi: discord.Interaction, _id=egg_id, _p=price, _n=name, _ns=next_step):
                            if bi.user.id != interaction.user.id:
                                return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns, quantity=5)
                        btn5.callback = cb5
                        self.add_item(btn5)

            return await interaction.followup.send(embed=embed, view=InstantEggView())

        # ══════════════════════════════════════════════════════════════════
        # INCUBATION EGGS TAB
        # ══════════════════════════════════════════════════════════════════
        if category == "incubation":
            from cogs.world import EGGS as EGG_TYPES
            EGG_PRICES = {
                "sprout_pod": 300,        "pebble_shell": 300,       "soot_hatchling": 300,
                "dewdrop_bulb": 1200,     "gale_nest": 1200,         "cavern_core": 1200,
                "prism_sphere": 4000,     "glow_spore": 4000,        "eclipse_pebble": 4000,
                "volcanic_core": 12000,   "nimbus_cloud": 12000,     "monolith_relic": 12000,
                "abyssal_trench_orb": 50000, "dragon_hoard_scale": 50000, "glacial_monolith": 50000,
            }
            named_eggs = sorted(
                [(eid, EGG_TYPES[eid], EGG_PRICES[eid]) for eid in EGG_PRICES if eid in EGG_TYPES],
                key=lambda x: x[2]
            )
            per_page = 4  # 4 eggs × 2 buttons = rows 0-3, row 4 free for pagination
            total_pages = max(1, (len(named_eggs) + per_page - 1) // per_page)

            def build_incub_embed(page: int) -> discord.Embed:
                embed = discord.Embed(
                    title="🏪 Shop — ⏱️ Incubation Eggs",
                    description=(
                        f"💰 Your gold: `{player['gold']:,}`\n\n"
                        f"Buy below — then use `/incubate` to start the timer.\n"
                        f"Use `/hatchegg` once ready. Up to 3 incubating at once.\n\u200b"
                    ),
                    color=COLORS["legendary"]
                )
                for eid, egg, price in named_eggs[(page-1)*per_page : page*per_page]:
                    pool = egg.get("pool", {})
                    pool_str = " · ".join(
                        f"{RARITY_EMOJI.get(r,'⚪')} {int(v*100)}%"
                        for r, v in sorted(
                            {k: v for k, v in pool.items() if k != "altered_chance"}.items(),
                            key=lambda x: RARITY_ORDER.index(x[0]) if x[0] in RARITY_ORDER else 99
                        )
                    )
                    altered = pool.get("altered_chance", 0)
                    if altered:
                        altered_names = [b.replace("_"," ").title() for b in egg.get("altered_pool", [])]
                        pool_str += f" · ⚠️ Altered {altered*100:.1f}%"
                        if altered_names:
                            pool_str += f" ({', '.join(altered_names)})"
                    h = egg["incubation_hours"]
                    time_str = f"{h}h" if h < 24 else f"{h//24}d{' '+str(h%24)+'h' if h%24 else ''}"
                    exclusive = ""
                    if "legendary_pool" in egg:
                        beast_names = [b.replace("_"," ").title() for b in egg["legendary_pool"]]
                        exclusive = f"\n✨ *Exclusive:* {', '.join(beast_names[:3])}{'...' if len(beast_names) > 3 else ''}"
                    embed.add_field(
                        name=f"{egg['emoji']} {egg['name']} — `{price:,}g` · ⏱️ {time_str}",
                        value=f"{pool_str}{exclusive}\n*{egg['flavor']}*",
                        inline=False
                    )
                embed.set_footer(text=f"ChibiBeasts 🐾  •  Page {page}/{total_pages} · Click to buy")
                return embed

            class IncubationEggView(discord.ui.View):
                def __init__(self, page: int):
                    super().__init__(timeout=120)
                    self.page = page
                    self._build()

                def _build(self):
                    self.clear_items()
                    page_eggs = named_eggs[(self.page-1)*per_page : self.page*per_page]
                    for row_idx, (eid, egg, price) in enumerate(page_eggs):
                        next_step = "Use `/incubate` to start the timer!"
                        short_name = egg["name"][:20]
                        btn1 = discord.ui.Button(
                            label=short_name,
                            style=discord.ButtonStyle.success,
                            emoji=egg.get("emoji", "🥚"),
                            row=row_idx
                        )
                        async def cb1(bi: discord.Interaction, _id=eid, _p=price, _n=egg["name"], _ns=next_step):
                            if bi.user.id != interaction.user.id:
                                return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns, quantity=1)
                        btn1.callback = cb1
                        self.add_item(btn1)
                        btn5 = discord.ui.Button(
                            label="+5",
                            style=discord.ButtonStyle.secondary,
                            row=row_idx
                        )
                        async def cb5(bi: discord.Interaction, _id=eid, _p=price, _n=egg["name"], _ns=next_step):
                            if bi.user.id != interaction.user.id:
                                return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns, quantity=5)
                        btn5.callback = cb5
                        self.add_item(btn5)
                    if total_pages > 1:
                        prev_btn = discord.ui.Button(
                            label=f"◀ Page {self.page-1}" if self.page > 1 else "◀",
                            style=discord.ButtonStyle.secondary,
                            disabled=self.page <= 1, row=4
                        )
                        next_btn = discord.ui.Button(
                            label=f"Page {self.page+1} ▶" if self.page < total_pages else "▶",
                            style=discord.ButtonStyle.secondary,
                            disabled=self.page >= total_pages, row=4
                        )
                        async def prev_cb(bi: discord.Interaction, _s=self):
                            if bi.user.id != interaction.user.id:
                                return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                            _s.page -= 1; _s._build()
                            await bi.response.edit_message(embed=build_incub_embed(_s.page), view=_s)
                        async def next_cb(bi: discord.Interaction, _s=self):
                            if bi.user.id != interaction.user.id:
                                return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                            _s.page += 1; _s._build()
                            await bi.response.edit_message(embed=build_incub_embed(_s.page), view=_s)
                        prev_btn.callback = prev_cb
                        next_btn.callback = next_cb
                        self.add_item(prev_btn)
                        self.add_item(next_btn)

            return await interaction.followup.send(embed=build_incub_embed(1), view=IncubationEggView(1))

        # ══════════════════════════════════════════════════════════════════
        # ITEMS TAB
        # ══════════════════════════════════════════════════════════════════
        all_items = sorted(
            [i for i in items_data.values() if i.get("price", 0) > 0],
            key=lambda i: (
                RARITY_ORDER.index(i["rarity"]) if i["rarity"] in RARITY_ORDER else 99,
                i["price"]
            )
        )

        per_page = 4  # 4 items × 2 buttons each = rows 0-3, row 4 free for pagination
        total_pages = max(1, (len(all_items) + per_page - 1) // per_page)

        def build_item_embed(page: int) -> discord.Embed:
            embed = discord.Embed(
                title="🏪 Shop — Items",
                description=f"💰 Your gold: `{player['gold']:,}` · Page {page}/{total_pages}",
                color=COLORS["legendary"]
            )
            for item in all_items[(page-1)*per_page : page*per_page]:
                r = RARITY_EMOJI.get(item["rarity"], "⚪")
                embed.add_field(
                    name=f"{r} {item['name']} — `{item['price']:,}g`",
                    value=item["description"][:120],
                    inline=False
                )
            embed.set_footer(text="ChibiBeasts 🐾  •  Click a button to buy instantly")
            return embed

        class ItemShopView(discord.ui.View):
            def __init__(self, page: int):
                super().__init__(timeout=120)
                self.page = page
                self._build()

            def _build(self):
                self.clear_items()
                page_items = all_items[(self.page-1)*per_page : self.page*per_page]
                for row_idx, item in enumerate(page_items):
                    r = RARITY_EMOJI.get(item["rarity"], "⚪")
                    next_step = "Check `/inventory` to use it!"
                    # Truncate name to keep button width consistent
                    short_name = item["name"][:20]
                    btn1 = discord.ui.Button(
                        label=short_name,
                        style=discord.ButtonStyle.success,
                        emoji=r,
                        row=row_idx
                    )
                    async def cb1(bi: discord.Interaction, _id=item["id"], _p=item["price"], _d=item["name"], _ns=next_step):
                        if bi.user.id != interaction.user.id:
                            return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                        await _do_purchase(bi, _id, _p, _d, _ns, quantity=1)
                    btn1.callback = cb1
                    self.add_item(btn1)
                    btn5 = discord.ui.Button(
                        label="+5",
                        style=discord.ButtonStyle.secondary,
                        row=row_idx
                    )
                    async def cb5(bi: discord.Interaction, _id=item["id"], _p=item["price"], _d=item["name"], _ns=next_step):
                        if bi.user.id != interaction.user.id:
                            return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                        await _do_purchase(bi, _id, _p, _d, _ns, quantity=5)
                    btn5.callback = cb5
                    self.add_item(btn5)

                if total_pages > 1:
                    prev_btn = discord.ui.Button(
                        label=f"◀ Page {self.page-1}" if self.page > 1 else "◀",
                        style=discord.ButtonStyle.secondary,
                        disabled=self.page <= 1,
                        row=4
                    )
                    next_btn = discord.ui.Button(
                        label=f"Page {self.page+1} ▶" if self.page < total_pages else "▶",
                        style=discord.ButtonStyle.secondary,
                        disabled=self.page >= total_pages,
                        row=4
                    )
                    async def prev_cb(bi: discord.Interaction, _self=self):
                        if bi.user.id != interaction.user.id:
                            return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                        _self.page -= 1
                        _self._build()
                        await bi.response.edit_message(embed=build_item_embed(_self.page), view=_self)
                    async def next_cb(bi: discord.Interaction, _self=self):
                        if bi.user.id != interaction.user.id:
                            return await bi.response.send_message("This isn't your shop!", ephemeral=True)
                        _self.page += 1
                        _self._build()
                        await bi.response.edit_message(embed=build_item_embed(_self.page), view=_self)
                    prev_btn.callback = prev_cb
                    next_btn.callback = next_cb
                    self.add_item(prev_btn)
                    self.add_item(next_btn)

        await interaction.followup.send(embed=build_item_embed(1), view=ItemShopView(1))

async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
    await bot.add_cog(Inventory(bot))
    await bot.add_cog(Shop(bot))
