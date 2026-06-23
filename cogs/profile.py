import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from utils.db import (
    get_or_create_player, get_player, update_player,
    get_player_beasts, get_active_beast, get_inventory,
    add_item, remove_item, load_beasts, load_items, load_perks,
    get_beast_data, calc_exp_for_level, calc_player_exp_for_level,
    get_perk_slots, apply_beast_levelup, get_beast_exp_for_level,
    is_knocked_out, ko_time_remaining, get_raid_party
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
        import time as _t
        explore_last = player.get("explore_last_at", 0) or 0
        explore_ready_in = max(0, 3600 - (_t.time() - explore_last))
        if explore_ready_in > 0:
            em, es = divmod(int(explore_ready_in), 60)
            explore_status = f"⏳ Ready in `{em}m {es}s`"
        else:
            explore_status = "✅ Ready!"

        # Raid party quick status
        raid_party = await get_raid_party(target.id)
        ko_in_party = [b for b in raid_party if b and is_knocked_out(b)]
        party_status = ""
        if ko_in_party:
            party_status = f"\n💀 `{len(ko_in_party)}` beast{'s' if len(ko_in_party)>1 else ''} recovering in raid party"

        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="🗺️ Explore",
            value=explore_status + party_status,
            inline=False
        )
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
        uid = interaction.user.id
        all_beasts = await get_player_beasts(uid)

        if not all_beasts:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ No beasts yet! Use `/hatch` or `/explore` to find some.",
                color=COLORS["info"]
            ))

        RARITY_ORDER = ["common","uncommon","rare","epic","legendary","divine","altered_divine","corrupted","ancient","dev"]
        SPECIAL = {"altered_divine","corrupted","ancient","dev"}
        TAB_RARITIES = ["common","uncommon","rare","epic","legendary","divine","special","all"]
        TAB_LABELS   = {
            "all":      "📋 All",
            "common":   "⚪ Common",
            "uncommon": "🟢 Uncommon",
            "rare":     "🔵 Rare",
            "epic":     "🟣 Epic",
            "legendary":"🟠 Legendary",
            "divine":   "✨ Divine",
            "special":  "✦ Special",
        }
        RARITY_COLORS = {
            "common":"common","uncommon":"uncommon","rare":"rare",
            "epic":"epic","legendary":"legendary","divine":"divine",
            "special":"legendary","all":"divine",
        }

        def filter_beasts(tab: str):
            if tab == "all":
                return sorted(all_beasts, key=lambda b: (RARITY_ORDER.index(b["rarity"]) if b["rarity"] in RARITY_ORDER else 99, b.get("player_number") or b["id"]))
            if tab == "special":
                bs = [b for b in all_beasts if b["rarity"] in SPECIAL]
            else:
                bs = [b for b in all_beasts if b["rarity"] == tab]
            return sorted(bs, key=lambda b: (b.get("player_number") or b["id"]))

        def has_tab(tab: str) -> bool:
            return len(filter_beasts(tab)) > 0

        per_page = 10

        def build_embed(tab: str, p: int) -> discord.Embed:
            beasts = filter_beasts(tab)
            total_pages = max(1, (len(beasts) + per_page - 1) // per_page)
            p = max(1, min(p, total_pages))
            page_beasts = beasts[(p-1)*per_page : p*per_page]

            tab_label = TAB_LABELS.get(tab, tab)
            color_key = RARITY_COLORS.get(tab, "divine")
            embed = discord.Embed(
                title=f"🐾 {interaction.user.display_name}'s Collection — {tab_label}",
                description=f"`{len(beasts)}` beast{'s' if len(beasts)!=1 else ''} · Page {p}/{total_pages}",
                color=COLORS.get(color_key, COLORS["divine"])
            )
            for b in page_beasts:
                bd = get_beast_data(b["beast_id"])
                if not bd:
                    continue
                name    = b.get("nickname") or bd["name"]
                r_emoji = RARITY_EMOJI.get(b["rarity"], "⚪")
                t_emoji = TYPE_EMOJI.get(bd["type"], "❓")
                num     = b.get("player_number") or b["id"]
                tags    = (" ⚔️" if b["is_active"] else "") + (" ⭐" if b.get("is_favorite") else "")
                val = f"{t_emoji} {bd['type'].capitalize()} · Lv.{b['level']} · `{b['hp']}/{b['max_hp']}HP`{tags}"
                embed.add_field(name=f"{r_emoji} #{num} {name}", value=val, inline=True)
            embed.set_footer(text="Use /beastinfo <#> for details · /setactive <#> to switch")
            return embed, max(1, (len(filter_beasts(tab)) + per_page - 1) // per_page)

        current_tab  = rarity if rarity in TAB_RARITIES else "all"
        current_page = page

        class CollectionView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=180)
                self.tab  = current_tab
                self.page = current_page
                self._rebuild()

            def _rebuild(self):
                self.clear_items()
                # Row 0 + 1 — rarity tabs split across two rows (max 5 per row)
                visible_tabs = [t for t in TAB_RARITIES if has_tab(t)]
                for idx_t, t in enumerate(visible_tabs):
                    btn = discord.ui.Button(
                        label=TAB_LABELS[t],
                        style=discord.ButtonStyle.primary if t == self.tab else discord.ButtonStyle.secondary,
                        row=0 if idx_t < 5 else 1,
                        disabled=(t == self.tab)
                    )
                    async def _tab_cb(inter, tab=t):
                        if inter.user.id != uid:
                            return await inter.response.send_message("✦ This isn't your collection!", ephemeral=True)
                        self.tab  = tab
                        self.page = 1
                        self._rebuild()
                        emb, _ = build_embed(self.tab, self.page)
                        await inter.response.edit_message(embed=emb, view=self)
                    btn.callback = _tab_cb
                    self.add_item(btn)

                # Row 2 — prev / page indicator / next
                _, total = build_embed(self.tab, self.page)
                prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=2, disabled=self.page<=1)
                async def _prev(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your collection!", ephemeral=True)
                    self.page -= 1
                    self._rebuild()
                    emb, _ = build_embed(self.tab, self.page)
                    await inter.response.edit_message(embed=emb, view=self)
                prev.callback = _prev
                self.add_item(prev)

                page_lbl = discord.ui.Button(label=f"{self.page}/{total}", style=discord.ButtonStyle.secondary, row=2, disabled=True)
                self.add_item(page_lbl)

                nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=2, disabled=self.page>=total)
                async def _next(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your collection!", ephemeral=True)
                    self.page += 1
                    self._rebuild()
                    emb, _ = build_embed(self.tab, self.page)
                    await inter.response.edit_message(embed=emb, view=self)
                nxt.callback = _next
                self.add_item(nxt)

        emb, _ = build_embed(current_tab, current_page)
        view = CollectionView()
        await interaction.followup.send(embed=emb, view=view)

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
            # KO recovery status
            if is_knocked_out(row):
                timer = ko_time_remaining(row)
                embed.add_field(
                    name="💀 Knocked Out",
                    value=f"Recovering — ready in `{timer}`\n*Use a **Phoenix Elixir** to revive instantly.*",
                    inline=False
                )
            # Evolution hint
            evo = beast_data.get("evolution")
            if evo and evo.get("evolves_to"):
                from utils.db import load_items as _li
                _items = _li()
                method_id = evo.get("method", "")
                method_item = _items.get(method_id, {})
                method_name = method_item.get("name", method_id.replace("_"," ").title())
                tgt_id = evo["evolves_to"]
                tgt_bd = get_beast_data(tgt_id) or {}
                tgt_name = tgt_bd.get("name", tgt_id)
                tgt_r = RARITY_EMOJI.get(tgt_bd.get("rarity",""), "⚪")
                form = evo.get("form","")
                form_label = "✨ Ascended" if form == "ascended" else "🌟 Radiant" if form == "radiant" else "🔀 Evolves"
                lvl_req = evo.get("level_required", 1)
                recipe = method_item.get("recipe")
                recipe_str = ""
                if recipe:
                    recipe_parts = ", ".join(f"{q}× {m.replace('_',' ').title()}" for m, q in recipe.items())
                    recipe_str = f"\n*Craft: {recipe_parts}*"
                elif method_id == "abyssal_scale":
                    recipe_str = "\n*Drop: Corrupted Leviathan raid*"
                can_evolve = row["level"] >= lvl_req
                lvl_note = f"Lv.{lvl_req} required" if not can_evolve else f"✅ Lv.{lvl_req} — **ready to evolve!**"
                embed.add_field(
                    name=f"{form_label} → {tgt_r} {tgt_name}",
                    value=f"**Item:** {method_name} · {lvl_note}{recipe_str}\n*Use `/evolve #{num}` when ready.*",
                    inline=False
                )
            if beast_data.get("divine_passive"):
                dp = beast_data["divine_passive"]
                passive_label = {
                    "divine": "✨ Divine Passive", "altered_divine": "⚠️ Altered Passive",
                    "corrupted": "🖤 Corrupted Passive", "ancient": "🏛️ Ancient Passive",
                    "dev": "👑 Developer Passive",
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

        from cogs.hatch import HATCH_EGGS
        from cogs.world import EGGS as INCUB_EGGS
        egg_lookup = {}
        for eid, egg in HATCH_EGGS.items():
            egg_lookup[eid] = {"name": egg["name"], "description": egg["flavor"], "rarity": "rare", "emoji": "🥚", "type": "egg"}
        for eid, egg in INCUB_EGGS.items():
            egg_lookup[eid] = {"name": egg["name"], "description": egg["flavor"], "rarity": egg["rarity"], "emoji": egg.get("emoji","🥚"), "type": "egg"}

        if not inv:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Your inventory is empty! Visit `/shop` to buy items.",
                color=COLORS["info"]
            ))

        # Separate into categories
        TYPE_GROUPS = {
            "🔮 Potions & Consumables": ["heal","revive","cure","mana","happiness","happiness_boost","exp","stat_boost","battle","encounter","cooldown"],
            "🥚 Eggs": ["egg"],
            "📦 Other": [],
        }

        def categorize(item_id: str, item: dict) -> str:
            t = item.get("type","")
            for group, types in TYPE_GROUPS.items():
                if t in types:
                    return group
            return "📦 Other"

        uid = interaction.user.id
        per_page = 10

        categorized = {}
        for entry in inv:
            item = items_data.get(entry["item_id"]) or egg_lookup.get(entry["item_id"])
            if not item:
                continue
            cat = categorize(entry["item_id"], item)
            categorized.setdefault(cat, []).append((entry, item))

        all_entries = []
        for cat in ["🔮 Potions & Consumables", "🥚 Eggs", "📦 Other"]:
            for entry, item in categorized.get(cat, []):
                all_entries.append((cat, entry, item))

        total_pages = max(1, (len(all_entries) + per_page - 1) // per_page)

        def build_inv_embed(page: int) -> discord.Embed:
            embed = discord.Embed(
                title="🎒 Inventory",
                description=f"`{len(all_entries)}` items · Page {page}/{total_pages}",
                color=COLORS["info"]
            )
            page_entries = all_entries[(page-1)*per_page : page*per_page]
            last_cat = None
            for cat, entry, item in page_entries:
                r_emoji = RARITY_EMOJI.get(item.get("rarity","common"), "⚪")
                name = item["name"]
                qty = entry["quantity"]
                desc = item["description"]
                if len(desc) > 60:
                    desc = desc[:57] + "..."
                if cat != last_cat:
                    embed.add_field(name=f"\u200b", value=f"**{cat}**", inline=False)
                    last_cat = cat
                embed.add_field(
                    name=f"{r_emoji} {name} ×{qty}",
                    value=desc,
                    inline=True
                )
            embed.set_footer(text="/use <item> to use · /shop to buy more")
            return embed

        if total_pages == 1:
            return await interaction.followup.send(embed=build_inv_embed(1))

        class InvView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.page = 1
                self._rebuild()

            def _rebuild(self):
                self.clear_items()
                prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page<=1, row=0)
                lbl  = discord.ui.Button(label=f"{self.page}/{total_pages}", style=discord.ButtonStyle.secondary, disabled=True, row=0)
                nxt  = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self.page>=total_pages, row=0)
                async def _prev(inter):
                    if inter.user.id != uid: return await inter.response.send_message("✦ Not your inventory!", ephemeral=True)
                    self.page -= 1; self._rebuild()
                    await inter.response.edit_message(embed=build_inv_embed(self.page), view=self)
                async def _next(inter):
                    if inter.user.id != uid: return await inter.response.send_message("✦ Not your inventory!", ephemeral=True)
                    self.page += 1; self._rebuild()
                    await inter.response.edit_message(embed=build_inv_embed(self.page), view=self)
                prev.callback = _prev; nxt.callback = _next
                self.add_item(prev); self.add_item(lbl); self.add_item(nxt)

        await interaction.followup.send(embed=build_inv_embed(1), view=InvView())

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
        if not active and item["type"] not in ["cooldown", "unlock", "reset", "revive"]:
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

            if "revive" in effect:
                raid_party = await get_raid_party(interaction.user.id)
                ko_beasts = [b for b in raid_party if b and is_knocked_out(b)]
                if not ko_beasts and active and active["hp"] <= 0:
                    ko_beasts = [active]
                if not ko_beasts:
                    result_lines.append("✦ No knocked-out beast to revive! Your party is already healthy.")
                elif len(ko_beasts) == 1:
                    ko_beast = ko_beasts[0]
                    heal = int(ko_beast["max_hp"] * (effect.get("heal_percent", 50) / 100))
                    await db.execute(
                        "UPDATE player_beasts SET knocked_out_until = NULL, hp = ? WHERE id = ?",
                        (heal, ko_beast["id"])
                    )
                    bd = get_beast_data(ko_beast["beast_id"]) or {}
                    bname = ko_beast.get("nickname") or bd.get("name", "Beast")
                    result_lines.append(f"🔥 **{bname}** revived with `{heal}/{ko_beast['max_hp']}HP`! Ready to fight.")
                else:
                    # Multiple KO'd — let player choose via buttons
                    await db.commit()  # commit current db state before sending view
                    heal_pct = effect.get("heal_percent", 50)
                    class ReviveView(discord.ui.View):
                        def __init__(self):
                            super().__init__(timeout=30)
                            for b in ko_beasts:
                                bdd = get_beast_data(b["beast_id"]) or {}
                                bname = b.get("nickname") or bdd.get("name","Beast")
                                timer = ko_time_remaining(b)
                                btn = discord.ui.Button(
                                    label=f"{bname} ({timer})",
                                    style=discord.ButtonStyle.danger,
                                    emoji="🔥"
                                )
                                async def _cb(inter, beast=b, name=bname):
                                    heal = int(beast["max_hp"] * (heal_pct / 100))
                                    async with aiosqlite.connect("db/chibibeast.db") as _db:
                                        await _db.execute(
                                            "UPDATE player_beasts SET knocked_out_until = NULL, hp = ? WHERE id = ?",
                                            (heal, beast["id"])
                                        )
                                        await _db.commit()
                                    await inter.response.edit_message(
                                        content=f"🔥 **{name}** revived with `{heal}/{beast['max_hp']}HP`! Ready to fight.",
                                        view=None
                                    )
                                btn.callback = _cb
                                self.add_item(btn)
                    await interaction.followup.send(
                        f"🔥 You have `{len(ko_beasts)}` knocked-out beasts. Which one to revive?",
                        view=ReviveView(), ephemeral=True
                    )
                    return

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

            # Chrono-Biscuit: instantly ready the oldest incubating egg for its next tend
            if item_id == "chrono_biscuit":
                async with db.execute(
                    "SELECT id, egg_name, tends_done, tends_required FROM incubating_eggs WHERE user_id = ? AND hatched = 0 ORDER BY started_at ASC LIMIT 1",
                    (interaction.user.id,)
                ) as c:
                    egg_row = await c.fetchone()
                if egg_row:
                    await db.execute(
                        "UPDATE incubating_eggs SET ready_at = datetime('now', '-1 minute'), next_tend_at = datetime('now', '-1 minute') WHERE id = ?",
                        (egg_row[0],)
                    )
                    tends_left = (egg_row[3] or 1) - (egg_row[2] or 0)
                    if tends_left <= 1:
                        result_lines.append(f"⏰ **{egg_row[1]}** is ready for its final tend! Use `/tend`.")
                    else:
                        result_lines.append(f"⏰ **{egg_row[1]}** is ready to tend now! Use `/tend` — {tends_left} tend{'s' if tends_left != 1 else ''} remaining.")
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
    async def shop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player  = await get_or_create_player(interaction.user.id, str(interaction.user))
        items_data  = load_items()
        RARITY_ORDER = ["common","uncommon","rare","epic","legendary","divine","altered_divine"]
        uid = interaction.user.id

        TABS = [
            ("instant",    "⚡", "Instant Eggs"),
            ("incubation", "⏱️", "Incubation"),
            ("potions",    "🔮", "Revive & Heal"),
            ("consumables","⚗️", "Consumables"),
            ("items",      "🎒", "Items"),
            ("shards",     "💎", "Shard Shop"),
        ]

        # ── purchase helper ────────────────────────────────────────────────
        async def _do_purchase(bi, item_id, price, display_name, next_step, quantity=1):
            await bi.response.defer(ephemeral=True)
            total_price = price * quantity
            async with aiosqlite.connect("db/chibibeast.db") as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT gold FROM players WHERE user_id = ?", (bi.user.id,)) as c:
                    pr = await c.fetchone()
                if not pr or pr["gold"] < total_price:
                    return await bi.followup.send(f"✦ Need `{total_price:,}g`, you have `{pr['gold'] if pr else 0:,}g`.", ephemeral=True)
                cur = await db.execute("UPDATE players SET gold = gold - ? WHERE user_id = ? AND gold >= ?", (total_price, bi.user.id, total_price))
                if cur.rowcount == 0:
                    return await bi.followup.send("✦ Purchase failed — try again.", ephemeral=True)
                async with db.execute("SELECT id, quantity FROM player_inventory WHERE user_id = ? AND item_id = ?", (bi.user.id, item_id)) as c:
                    inv = await c.fetchone()
                if inv:
                    await db.execute("UPDATE player_inventory SET quantity = quantity + ? WHERE id = ?", (quantity, inv["id"]))
                else:
                    await db.execute("INSERT INTO player_inventory (user_id, item_id, quantity) VALUES (?,?,?)", (bi.user.id, item_id, quantity))
                await db.commit()
                new_gold = pr["gold"] - total_price
            qty_str = f"`{quantity}x` " if quantity > 1 else ""
            await bi.followup.send(f"✅ Purchased {qty_str}**{display_name}**!\n`{total_price:,}g` spent · Balance: `{new_gold:,}g`\n{next_step}", ephemeral=True)
            from utils.progress import track_quest_event, notify_quest_completions
            completed = await track_quest_event(bi.user.id, "spend_gold", amount=total_price)
            if completed and bi.channel:
                await notify_quest_completions(bi.channel, completed)

        async def _buy_with_qty(bi, item_id, price, display_name, next_step, max_qty=99):
            from utils.modals import QuantityModal
            async def on_submit(modal_bi, qty):
                await _do_purchase(modal_bi, item_id, price, display_name, next_step, qty)
            modal = QuantityModal(title=f"Buy {display_name}", item_name=display_name, max_quantity=max_qty, callback=on_submit)
            modal.quantity_input.label = f"Quantity ({price:,}g each)"
            modal.quantity_input.placeholder = f"Enter amount (e.g. 5)"
            await bi.response.send_modal(modal)

        # ── section embed + item builders ─────────────────────────────────
        def build_embed_and_items(section: str):
            """Return (embed, [(item_id, item_data, price)] or special) for each section."""
            gold   = player["gold"]
            shards = player.get("celestial_shards", 0)

            if section == "instant":
                from cogs.hatch import HATCH_EGGS, _BASE_EGG_POOLS
                INSTANT_EGGS = [
                    ("🥚 Common Egg",      200,   "common_egg"),
                    ("🥚✨ Rare Egg",      1500,  "rare_egg"),
                    ("🌌🥚 Celestial Egg", 8000,  "celestial_egg"),
                    ("🌊💎 Abyssal Egg",   25000, "abyssal_egg"),
                ]
                embed = discord.Embed(title="🏪 ⚡ Instant Eggs", description=f"💰 `{gold:,}g`\nBuy and hatch with `/hatch`.\n\u200b", color=COLORS["legendary"])
                for name, price, egg_id in INSTANT_EGGS:
                    pool = {k: v for k, v in _BASE_EGG_POOLS.get(egg_id, {}).items() if k != "altered_chance"}
                    pool_str = " · ".join(f"{RARITY_EMOJI.get(r,'⚪')} {int(v*100)}%" for r, v in sorted(pool.items(), key=lambda x: RARITY_ORDER.index(x[0]) if x[0] in RARITY_ORDER else 99))
                    altered = _BASE_EGG_POOLS.get(egg_id, {}).get("altered_chance", 0)
                    if altered: pool_str += f" · ⚠️ Altered {int(altered*100)}%"
                    embed.add_field(name=f"{name} — `{price:,}g`", value=pool_str, inline=False)
                embed.set_footer(text="ChibiBeasts 🐾")
                return embed, INSTANT_EGGS

            elif section == "incubation":
                from cogs.world import EGGS as EGG_TYPES
                EGG_PRICES = {"1hr": 300, "3hr": 500, "6hr": 800, "12hr": 1200, "24hr": 2000, "48hr": 4000, "72hr": 6000, "96hr": 10000}
                by_time = {}
                for eid, egg in EGG_TYPES.items():
                    t = egg.get("incubation_time","")
                    by_time.setdefault(t, []).append((eid, egg))
                embed = discord.Embed(title="🏪 ⏱️ Incubation Eggs", description=f"💰 `{gold:,}g`\nTend with `/tend` after purchase.\n\u200b", color=COLORS["legendary"])
                items_out = []
                for t in sorted(EGG_PRICES):
                    for eid, egg in by_time.get(t, []):
                        price = EGG_PRICES.get(t, 500)
                        r = RARITY_EMOJI.get(egg.get("rarity","common"), "⚪")
                        embed.add_field(name=f"{r} {egg['name']} — `{price:,}g`", value=egg.get("flavor","")[:100], inline=False)
                        items_out.append((eid, egg, price))
                embed.set_footer(text="ChibiBeasts 🐾")
                return embed, items_out

            elif section in ("potions", "consumables"):
                REVIVE_HEAL = {"heal","revive","cure","mana"}
                CONSUMABLE  = {"happiness","happiness_boost","exp","stat_boost","cooldown","battle","encounter","permanent_boost"}
                types = REVIVE_HEAL if section == "potions" else CONSUMABLE
                label = "🔮 Revive & Heal" if section == "potions" else "⚗️ Consumables"
                color = COLORS["epic"] if section == "potions" else COLORS["rare"]
                filtered = sorted([i for i in items_data.values() if i.get("price",0) > 0 and i.get("type") in types],
                    key=lambda i: (RARITY_ORDER.index(i["rarity"]) if i["rarity"] in RARITY_ORDER else 99, i["price"]))
                embed = discord.Embed(title=f"🏪 {label}", description=f"💰 `{gold:,}g`\nUse with `/use <item name>`.\n\u200b", color=color)
                for item in filtered:
                    r = RARITY_EMOJI.get(item["rarity"],"⚪")
                    embed.add_field(name=f"{r} {item['name']} — `{item['price']:,}g`", value=item["description"][:100], inline=False)
                embed.set_footer(text="ChibiBeasts 🐾")
                return embed, filtered

            elif section == "items":
                filtered = sorted([i for i in items_data.values() if i.get("price",0) > 0 and i.get("type") not in
                    {"heal","revive","cure","mana","happiness","happiness_boost","exp","stat_boost","cooldown","battle","encounter","permanent_boost"}],
                    key=lambda i: (RARITY_ORDER.index(i["rarity"]) if i["rarity"] in RARITY_ORDER else 99, i["price"]))
                embed = discord.Embed(title="🏪 🎒 Special Items", description=f"💰 `{gold:,}g`\n\u200b", color=COLORS["legendary"])
                for item in filtered:
                    r = RARITY_EMOJI.get(item["rarity"],"⚪")
                    embed.add_field(name=f"{r} {item['name']} — `{item['price']:,}g`", value=item["description"][:100], inline=False)
                embed.set_footer(text="ChibiBeasts 🐾")
                return embed, filtered

            else:  # shards
                from cogs.utilities import SHARD_SHOP
                import json as _j
                from datetime import datetime as _dt, timezone as _tz
                week_str = _dt.now(_tz.utc).strftime("%Y-W%W")
                import asyncio as _asyncio
                week_data = {}
                embed = discord.Embed(title="🏪 💎 Shard Shop",
                    description=f"🔮 Shards: `{shards}`\n*Exclusive items purchasable with Celestial Shards.*\n\u200b",
                    color=COLORS["divine"])
                SUMMON_IDS = {"epoch_shard","firstborn_ember","void_prism"}
                for sid, item in SHARD_SHOP.items():
                    if sid in SUMMON_IDS: continue
                    lim = f" · {item['weekly_limit']}/week" if item["weekly_limit"] else ""
                    embed.add_field(name=f"{item['name']} — `{item['cost']} 🔮`{lim}", value=item["desc"], inline=False)
                embed.add_field(name="\u200b", value="**🏛️ Ancient Summon Relics**", inline=False)
                for sid in SUMMON_IDS:
                    if sid in SHARD_SHOP:
                        item = SHARD_SHOP[sid]
                        embed.add_field(name=f"{item['name']} — `{item['cost']} 🔮`", value=item["desc"], inline=False)
                embed.set_footer(text="ChibiBeasts 🐾")
                return embed, "shards"

        # ── main tabbed view ───────────────────────────────────────────────
        class ShopView(discord.ui.View):
            def __init__(self_v, section="instant"):
                super().__init__(timeout=180)
                self_v.section = section
                self_v._rebuild()

            def _rebuild(self_v):
                self_v.clear_items()
                # Row 0: tab buttons (3 per row across 2 rows)
                for idx, (key, emoji, name) in enumerate(TABS):
                    btn = discord.ui.Button(
                        label=name, emoji=emoji,
                        style=discord.ButtonStyle.primary if key == self_v.section else discord.ButtonStyle.secondary,
                        disabled=(key == self_v.section),
                        row=idx // 3
                    )
                    async def _tab(bi, k=key, _v=self_v):
                        if bi.user.id != uid:
                            return await bi.response.send_message("✦ This isn't your shop!", ephemeral=True)
                        _v.section = k
                        _v._rebuild()
                        emb, _ = build_embed_and_items(k)
                        await _v._render(bi, emb)
                    btn.callback = _tab
                    self_v.add_item(btn)
                # Row 2+: buy buttons for current section
                self_v._add_section_buttons()

            async def _render(self_v, bi, emb):
                if bi.response.is_done():
                    await bi.edit_original_response(embed=emb, view=self_v)
                else:
                    await bi.response.edit_message(embed=emb, view=self_v)

            def _add_section_buttons(self_v):
                section = self_v.section
                emb, items = build_embed_and_items(section)

                if section == "instant":
                    from cogs.hatch import HATCH_EGGS
                    for i, (name, price, egg_id) in enumerate(items):
                        short = name.replace("🥚","").replace("✨","").replace("🌌","").replace("🌊💎","").strip()
                        ns = "Use `/hatch` to open it!"
                        b1 = discord.ui.Button(label=short, style=discord.ButtonStyle.success, emoji="🥚", row=i+2)
                        async def _b1(bi, _id=egg_id, _p=price, _n=name, _ns=ns):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns)
                        b1.callback = _b1
                        b5 = discord.ui.Button(label="+5", style=discord.ButtonStyle.secondary, row=i+2)
                        async def _b5(bi, _id=egg_id, _p=price, _n=name, _ns=ns):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns, 5)
                        b5.callback = _b5
                        self_v.add_item(b1); self_v.add_item(b5)

                elif section == "incubation":
                    per_page = 2
                    page = getattr(self_v, "_incub_page", 1)
                    total = max(1, (len(items)+per_page-1)//per_page)
                    page_items = items[(page-1)*per_page:page*per_page]
                    for i, (eid, egg, price) in enumerate(page_items):
                        ns = "Use `/tend` to care for your egg!"
                        btn = discord.ui.Button(label=egg["name"][:22], style=discord.ButtonStyle.success, row=i+2)
                        async def _cb(bi, _id=eid, _p=price, _n=egg["name"], _ns=ns):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns)
                        btn.callback = _cb
                        self_v.add_item(btn)
                    if total > 1:
                        prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=page<=1, row=4)
                        nxt  = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=page>=total, row=4)
                        async def _prev(bi, _v=self_v):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            _v._incub_page = getattr(_v,"_incub_page",1) - 1
                            _v._rebuild(); emb2, _ = build_embed_and_items("incubation")
                            await _v._render(bi, emb2)
                        async def _nxt(bi, _v=self_v):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            _v._incub_page = getattr(_v,"_incub_page",1) + 1
                            _v._rebuild(); emb2, _ = build_embed_and_items("incubation")
                            await _v._render(bi, emb2)
                        prev.callback = _prev; nxt.callback = _nxt
                        self_v.add_item(prev); self_v.add_item(nxt)

                elif section in ("potions","consumables"):
                    per_page = 3
                    page = getattr(self_v, f"_{section}_page", 1)
                    total = max(1, (len(items)+per_page-1)//per_page)
                    page_items = items[(page-1)*per_page:page*per_page]
                    for i, item in enumerate(page_items):
                        r = RARITY_EMOJI.get(item["rarity"],"⚪")
                        ns = f"Use `/use {item['name']}` to apply!"
                        btn = discord.ui.Button(label=item["name"][:22], style=discord.ButtonStyle.success, emoji=r, row=i+2)
                        async def _cb(bi, _id=item["id"], _p=item["price"], _n=item["name"], _ns=ns):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns)
                        btn.callback = _cb
                        b5 = discord.ui.Button(label="+5", style=discord.ButtonStyle.secondary, row=i+2)
                        async def _cb5(bi, _id=item["id"], _p=item["price"], _n=item["name"], _ns=ns):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns, 5)
                        _cb5.__name__ = f"cb5_{i}"
                        b5.callback = _cb5
                        self_v.add_item(btn); self_v.add_item(b5)
                    if total > 1:
                        prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=page<=1, row=4)
                        nxt  = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=page>=total, row=4)
                        async def _prev(bi, _v=self_v, _sec=section):
                            _v.__dict__[f"_{_sec}_page"] = getattr(_v, f"_{_sec}_page", 1) - 1
                            _v._rebuild(); emb2, _ = build_embed_and_items(_sec)
                            await _v._render(bi, emb2)
                        async def _nxt(bi, _v=self_v, _sec=section):
                            _v.__dict__[f"_{_sec}_page"] = getattr(_v, f"_{_sec}_page", 1) + 1
                            _v._rebuild(); emb2, _ = build_embed_and_items(_sec)
                            await _v._render(bi, emb2)
                        prev.callback = _prev; nxt.callback = _nxt
                        self_v.add_item(prev); self_v.add_item(nxt)

                elif section == "items":
                    per_page = 3
                    page = getattr(self_v, "_items_page", 1)
                    total = max(1, (len(items)+per_page-1)//per_page)
                    page_items = items[(page-1)*per_page:page*per_page]
                    for i, item in enumerate(page_items):
                        r = RARITY_EMOJI.get(item["rarity"],"⚪")
                        ns = f"Check `/inventory` to use it!"
                        btn = discord.ui.Button(label=item["name"][:22], style=discord.ButtonStyle.success, emoji=r, row=i+2)
                        async def _cb(bi, _id=item["id"], _p=item["price"], _n=item["name"], _ns=ns):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            await _do_purchase(bi, _id, _p, _n, _ns)
                        btn.callback = _cb
                        self_v.add_item(btn)
                    if total > 1:
                        prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=page<=1, row=4)
                        nxt  = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=page>=total, row=4)
                        async def _prev(bi, _v=self_v):
                            _v._items_page = getattr(_v,"_items_page",1)-1; _v._rebuild()
                            emb2, _ = build_embed_and_items("items"); await _v._render(bi, emb2)
                        async def _nxt(bi, _v=self_v):
                            _v._items_page = getattr(_v,"_items_page",1)+1; _v._rebuild()
                            emb2, _ = build_embed_and_items("items"); await _v._render(bi, emb2)
                        prev.callback = _prev; nxt.callback = _nxt
                        self_v.add_item(prev); self_v.add_item(nxt)

                elif section == "shards":
                    from cogs.utilities import SHARD_SHOP
                    import json as _j
                    from datetime import datetime as _dt, timezone as _tz
                    week_str = _dt.now(_tz.utc).strftime("%Y-W%W")
                    shards = player.get("celestial_shards", 0)
                    SUMMON_IDS = {"epoch_shard","firstborn_ember","void_prism"}
                    page = getattr(self_v, "_shards_page", 1)
                    all_items = list(SHARD_SHOP.items())
                    regular = [(s,i) for s,i in all_items if s not in SUMMON_IDS]
                    summons = [(s,i) for s,i in all_items if s in SUMMON_IDS]
                    per_pg = 3
                    reg_pages = max(1,(len(regular)+per_pg-1)//per_pg)
                    total = reg_pages + 1
                    is_summon = page > reg_pages
                    page_items = summons if is_summon else regular[(page-1)*per_pg:page*per_pg]
                    for ri, (sid, item) in enumerate(page_items):
                        can_afford = shards >= item["cost"]
                        btn = discord.ui.Button(
                            label=f"{item['name'][:18]} ({item['cost']}🔮)",
                            style=discord.ButtonStyle.primary if can_afford else discord.ButtonStyle.secondary,
                            disabled=not can_afford, row=ri+2
                        )
                        async def _cb(bi, _sid=sid):
                            if bi.user.id != uid: return await bi.response.send_message("✦ Not your shop!", ephemeral=True)
                            from cogs.utilities import _handle_shard_item
                            await bi.response.defer(ephemeral=True)
                            shop_item = SHARD_SHOP[_sid]
                            fp = await get_player(bi.user.id)
                            fs = fp.get("celestial_shards",0) if fp else 0
                            if fs < shop_item["cost"]:
                                return await bi.followup.send(f"✦ Need `{shop_item['cost']} 🔮`.", ephemeral=True)
                            async with aiosqlite.connect("db/chibibeast.db") as _db:
                                await _db.execute("UPDATE players SET celestial_shards = celestial_shards - ? WHERE user_id = ?", (shop_item["cost"], bi.user.id))
                                res = await _handle_shard_item(_db, bi.user.id, _sid, shop_item)
                                await _db.commit()
                            await bi.followup.send(embed=discord.Embed(
                                title=f"🔮 {shop_item['name']}",
                                description=f"{res}\n\nRemaining: `{fs-shop_item['cost']} 🔮`",
                                color=COLORS["divine"]
                            ), ephemeral=True)
                        btn.callback = _cb
                        self_v.add_item(btn)
                    prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=page<=1, row=4)
                    nxt_lbl = "🏛️ Relics ▶" if page == reg_pages else "▶"
                    nxt_sty = discord.ButtonStyle.danger if page == reg_pages else discord.ButtonStyle.secondary
                    nxt  = discord.ui.Button(label=nxt_lbl, style=nxt_sty, disabled=page>=total, row=4)
                    async def _prev(bi, _v=self_v):
                        _v._shards_page = getattr(_v,"_shards_page",1)-1; _v._rebuild()
                        emb2, _ = build_embed_and_items("shards"); await _v._render(bi, emb2)
                    async def _nxt(bi, _v=self_v):
                        _v._shards_page = getattr(_v,"_shards_page",1)+1; _v._rebuild()
                        emb2, _ = build_embed_and_items("shards"); await _v._render(bi, emb2)
                    prev.callback = _prev; nxt.callback = _nxt
                    self_v.add_item(prev); self_v.add_item(nxt)

        emb, _ = build_embed_and_items("instant")
        await interaction.followup.send(embed=emb, view=ShopView("instant"))

async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
    await bot.add_cog(Inventory(bot))
    await bot.add_cog(Shop(bot))
