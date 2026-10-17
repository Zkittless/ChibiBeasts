from utils import db as storage
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import asyncio
import json
import logging
from datetime import datetime, timezone
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
                f"💎 **Celestial Shards:** `{player['celestial_shards']}` — *spend at `/shop`*\n"
                f"🎫 **Guild Tickets:** `{player['guild_tickets']}`"
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
                happiness = active.get("happiness", 100)
                hap_filled = int(happiness / 10)
                hap_bar = "🟨" * hap_filled + "⬛" * (10 - hap_filled)
                ult_charges = player.get("ultimate_charges", 0) or 0
                ult_bar = "⚡" * ult_charges + "◇" * (3 - ult_charges)
                ult_label = "**READY**" if ult_charges >= 3 else f"{ult_charges}/3"
                embed.add_field(
                    name="⚔️ Active Beast",
                    value=(
                        f"{RARITY_EMOJI.get(active['rarity'], '⚪')} **{name}** — Lv.{active['level']}\n"
                        f"❤️ {hp_bar(active['hp'], active['max_hp'])}\n"
                        f"😊 {hap_bar} `{happiness}/100`\n"
                        f"⚡ Ultimate: {ult_bar} {ult_label}"
                    ),
                    inline=False
                )
        import time as _t
        from utils.rewards import cooldown
        async with storage.connect() as db:
            explore_cd = await cooldown(db, target.id, 1800)
            challenge_cd = await cooldown(db, target.id, 1200)
        explore_last = player.get("explore_last_at", 0) or 0
        explore_ready_in = max(0, explore_cd - (_t.time() - explore_last))
        if explore_ready_in > 0:
            em, es = divmod(int(explore_ready_in), 60)
            explore_status = f"⏳ Explore ready in `{em}m {es}s`"
        else:
            explore_status = "✅ Explore ready!"

        challenge_last = player.get("challenge_last_at", 0) or 0
        challenge_ready_in = max(0, challenge_cd - (_t.time() - challenge_last))
        if challenge_ready_in > 0:
            cm, cs = divmod(int(challenge_ready_in), 60)
            challenge_status = f"\n⚔️ Challenge ready in `{cm}m {cs}s`"
        else:
            challenge_status = "\n⚔️ Challenge ready!"

        # Raid party quick status
        raid_party = await get_raid_party(target.id)
        ko_in_party = [b for b in raid_party if b and is_knocked_out(b)]
        party_status = ""
        if ko_in_party:
            party_status = f"\n💀 `{len(ko_in_party)}` beast{'s' if len(ko_in_party)>1 else ''} recovering in raid party"

        embed.set_thumbnail(url=target.display_avatar.url)
        # Sanctuary active bonuses
        sanctuary_status = ""
        if interaction.guild:
            try:
                async with storage.connect() as _sdb:
                    _sdb.row_factory = aiosqlite.Row
                    async with _sdb.execute("SELECT * FROM guild_sanctuary WHERE guild_id = ?", (interaction.guild.id,)) as _sc:
                        _sanc = await _sc.fetchone()
                if _sanc:
                    _sanc = dict(_sanc)
                    _bonuses = []
                    if _sanc.get("training_grounds"): _bonuses.append("⚒️ -10% train cost")
                    if _sanc.get("arcane_library"):   _bonuses.append("📚 +15% EXP")
                    if _sanc.get("raid_altar"):        _bonuses.append("⚔️ +10% raid dmg")
                    if _sanc.get("celestial_observatory"): _bonuses.append("🔭 +2% rare encounters")
                    if _bonuses:
                        sanctuary_status = "\n🏛️ **Sanctuary:** " + " · ".join(_bonuses)
            except Exception:
                pass

        embed.add_field(
            name="🗺️ Activity",
            value=explore_status + challenge_status + party_status + sanctuary_status,
            inline=False
        )
        embed.set_footer(text="ChibiBeasts 🐾  •  /collection to see all beasts")

        # Questline nudge for own profile
        nudge_embed = None
        is_own = target.id == interaction.user.id
        if is_own:
            try:
                from cogs.questline import get_quest_state as _gqs, load_questline as _lql
                from utils.db import load_npcs as _ln
                _qstate = await _gqs(interaction.user.id)
                _curr_ch = _qstate.get("current_chapter", "")
                if _curr_ch:
                    _ql = _lql()
                    _ch = _ql["chapters"].get(_curr_ch, {})
                    if _ch:
                        _npc = _ln().get(_ch.get("npc",""), {})
                        nudge_embed = discord.Embed(
                            description=(
                                f"{_npc.get('emoji','📖')} **{_npc.get('name','your guide')}** is waiting — "
                                f"*{_ch['name']}* is in progress.\n"
                                f"Use `/questline` to check your objectives."
                            ),
                            color=COLORS["legendary"]
                        )
            except Exception:
                pass

        # Show new player guide for lv1 players with <=1 beast viewing own profile
        if is_own and player["level"] == 1 and len(beasts) <= 1:
            guide_lines = [
                "*The Loom has noted your arrival. Here is what to do first:*",
                "",
                "**`/dailies`** — 4 quests reset every day. Best source of early gold.",
                "**`/explore`** — Visit the Whispering Woods to catch wild beasts.",
                "**`/shop`** — Buy and hatch a Common Egg for a new companion.",
                "**`/sparr`** — Spar with an NPC to earn EXP and level up.",
                "**`/questline`** — Follow the main story for big rewards.",
                "**`/daily`** — Claim free gold and shards once per day.",
                "",
                "*Use `/guide` to browse all commands.*",
            ]
            guide = discord.Embed(
                title="Where to Start",
                description="\n".join(guide_lines),
                color=COLORS["success"]
            )
            guide.set_footer(text="This guide disappears once you level up")
            embeds = [embed, guide]
            if nudge_embed:
                embeds.append(nudge_embed)
            await interaction.followup.send(embeds=embeds[:10])
        else:
            if nudge_embed:
                await interaction.followup.send(embeds=[embed, nudge_embed])
            else:
                await interaction.followup.send(embed=embed)

    @app_commands.command(name="collection", description="View your ChibiBeast collection 🐾")
    async def collection(self, interaction: discord.Interaction):
        page, rarity = 1, "all"
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
            def __init__(self_v):
                super().__init__(timeout=180)
                self_v.tab  = current_tab
                self_v.page = current_page
                self_v._rebuild()

            def _rebuild(self_v):
                self_v.clear_items()
                visible = [t for t in TAB_RARITIES if has_tab(t)]
                # Row 0 — rarity select
                select = discord.ui.Select(
                    placeholder="🐾 Filter by rarity…",
                    options=[
                        discord.SelectOption(label=TAB_LABELS[t], value=t, default=t==self_v.tab)
                        for t in visible
                    ],
                    row=0
                )
                async def _on_select(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your collection!", ephemeral=True)
                    self_v.tab  = inter.data["values"][0]
                    self_v.page = 1
                    self_v._rebuild()
                    emb, _ = build_embed(self_v.tab, self_v.page)
                    await inter.response.edit_message(embed=emb, view=self_v)
                select.callback = _on_select
                self_v.add_item(select)
                # Row 1 — pagination
                _, total = build_embed(self_v.tab, self_v.page)
                prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=1, disabled=self_v.page<=1)
                async def _prev(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your collection!", ephemeral=True)
                    self_v.page -= 1; self_v._rebuild()
                    emb, _ = build_embed(self_v.tab, self_v.page)
                    await inter.response.edit_message(embed=emb, view=self_v)
                prev.callback = _prev
                self_v.add_item(prev)
                self_v.add_item(discord.ui.Button(label=f"{self_v.page}/{total}", style=discord.ButtonStyle.secondary, row=1, disabled=True))
                nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=1, disabled=self_v.page>=total)
                async def _nxt(inter):
                    if inter.user.id != uid:
                        return await inter.response.send_message("✦ This isn't your collection!", ephemeral=True)
                    self_v.page += 1; self_v._rebuild()
                    emb, _ = build_embed(self_v.tab, self_v.page)
                    await inter.response.edit_message(embed=emb, view=self_v)
                nxt.callback = _nxt
                self_v.add_item(nxt)

        emb, _ = build_embed(current_tab, current_page)
        view = CollectionView()
        await interaction.followup.send(embed=emb, view=view)

    @app_commands.command(name="beastinfo", description="View detailed info about one of your beasts 🔍")
    @app_commands.describe(beast_number="Your beast number from /collection (leave blank for active beast)")
    async def beastinfo(self, interaction: discord.Interaction, beast_number: int = None):
        await interaction.response.defer()

        player = await get_or_create_player(interaction.user.id, str(interaction.user))

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
            ult_charges = player.get("ultimate_charges", 0) or 0
            ult_line = ""
            if row.get("is_active"):
                ult_bar = "⚡" * ult_charges + "◇" * (3 - ult_charges)
                ult_label = "**READY**" if ult_charges >= 3 else f"{ult_charges}/3"
                ult_line = f"\n⚡ Ultimate: {ult_bar} {ult_label}"
            embed.add_field(
                name="📈 Progress",
                value=(
                    f"⭐ Level: `{row['level']}`\n"
                    f"✨ EXP: {exp_bar(row['exp'], exp_needed)}\n"
                    f"😊 Happiness: `{row['happiness']}/100`"
                    + ult_line
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
                if form == 'bond':
                    can_evolve = can_evolve and row.get('bond', 0) >= 30 and row['happiness'] >= 70
                    method_name = 'No item needed — a bond awakening'
                    recipe_str = f"\nBond: {row.get('bond', 0)}/30 · Happiness: {row['happiness']}/70 · `/bond` shows your progress."
                lvl_note = f"Lv.{lvl_req} required" if not can_evolve else f"✅ Lv.{lvl_req} — **ready to evolve!**"
                if form == 'bond' and row['level'] >= lvl_req and not can_evolve:
                    lvl_note = f"Lv.{lvl_req} reached; build bond and happiness"
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
            embed.set_footer(text=f"Beast #{num} · {len(all_beasts)} companions | Caught via: {row['caught_from']}")
            return embed

        # Navigation buttons
        current_idx = next((i for i, b in enumerate(all_beasts) if b["id"] == beast_row["id"]), 0)

        class GoToModal(discord.ui.Modal, title="Go to Beast #"):
            number = discord.ui.TextInput(
                label="Beast number shown in your collection",
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
                    idx = next((i for i, b in enumerate(all_beasts) if b.get("player_number") is None and b["id"] == target_num), None)
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
        try:
            await storage.set_active_companion(interaction.user.id, beast_number)
        except ValueError as error:
            return await interaction.followup.send(str(error), ephemeral=True)
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
        async with storage.connect() as db:
            async with db.execute(
                "SELECT id FROM player_beasts WHERE player_number = ? AND user_id = ?",
                (beast_number, interaction.user.id)
            ) as c:
                exists = await c.fetchone()
            if not exists:
                async with db.execute(
                    "SELECT id FROM player_beasts WHERE id = ? AND user_id = ? AND player_number IS NULL",
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
    async def use(self,interaction:discord.Interaction,item_name:str,beast_number:int=None):
        await interaction.response.defer(ephemeral=True)
        from utils.items import use_item,ELEMENTS
        items=load_items();key=item_name.lower().replace(" ","_").replace("-","_")
        if key not in items:key=next((k for k,v in items.items() if item_name.lower() in v["name"].lower()),None)
        if not key:return await interaction.followup.send("That item was not found. Check /inventory.",ephemeral=True)
        async def activate(bi,choice=None):
            try:result=await use_item(bi.user.id,key,choice=choice,beast_number=beast_number)
            except ValueError as error:result=str(error)
            await bi.followup.send(result,ephemeral=True)
        if items[key]["effect"].get("choose_element"):
            view=discord.ui.View(timeout=120);select=discord.ui.Select(placeholder="Choose your next hatch's element",options=[discord.SelectOption(label=t.title(),value=t) for t in ELEMENTS])
            async def selected(bi):
                if bi.user.id!=interaction.user.id:return await bi.response.send_message("This is not your item.",ephemeral=True)
                await bi.response.defer(ephemeral=True);await activate(bi,select.values[0]);view.stop()
            select.callback=selected;view.add_item(select)
            return await interaction.followup.send("The token is consumed only after a valid choice.",view=view,ephemeral=True)
        if items[key]["effect"].get("rename"):
            class Rename(discord.ui.Modal,title="Name your companion"):
                name=discord.ui.TextInput(label="Name",min_length=1,max_length=40)
                async def on_submit(self,bi):
                    await bi.response.defer(ephemeral=True);await activate(bi,str(self.name))
            view=discord.ui.View(timeout=120);button=discord.ui.Button(label="Choose name",style=discord.ButtonStyle.primary)
            async def open_modal(bi):
                if bi.user.id!=interaction.user.id:return await bi.response.send_message("This is not your item.",ephemeral=True)
                await bi.response.send_modal(Rename())
            button.callback=open_modal;view.add_item(button)
            return await interaction.followup.send("Rename your active beast, or supply its collection number with /use.",view=view,ephemeral=True)
        await activate(interaction)

class Shop(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot



    @app_commands.command(name="shop", description="Browse the ChibiBeasts shop 🏪")
    async def shop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player   = await get_or_create_player(interaction.user.id, str(interaction.user))
        uid      = interaction.user.id
        RARITY_ORDER = ["common","uncommon","rare","epic","legendary","divine","altered_divine"]
        async with storage.connect() as db:
            from utils.rewards import has_perk
            merchant=await has_perk(db,uid,"whimsy_merchant")

        # ── purchase helper ────────────────────────────────────────────────
        async def _buy(bi, item_id, _price, _name, next_step, qty=1):
            await bi.response.defer(ephemeral=True)
            from utils.shops import purchase_gold
            try:
                result = await purchase_gold(bi.user.id,item_id,qty)
            except ValueError as error:
                return await bi.followup.send(str(error),ephemeral=True)
            total = result['total']
            item = result['item']
            if item.get('type') in {'evolution','unlock'}:
                next_step = 'Use /evolve with an eligible companion.'
            qs = f"`{qty}x` " if qty > 1 else ""
            await bi.followup.send(f"Purchased {qs}**{item['name']}**!\n`{total:,}g` spent - Balance: `{result['remaining']:,}g`\n{next_step}",ephemeral=True)
            await view.refresh(bi, acknowledged=True)
            from utils.progress import track_quest_event, notify_quest_completions
            completed = await track_quest_event(bi.user.id, "spend_gold", amount=total)
            if completed and bi.channel:
                await notify_quest_completions(bi.channel, completed)

        # ── section data builders ──────────────────────────────────────────
        def get_instant_eggs():
            """Returns list of (id, name, price, pool_str)."""
            from cogs.hatch import HATCH_EGGS, _BASE_EGG_POOLS, get_egg_pool
            PRICES = {key:item["price"] for key,item in HATCH_EGGS.items()}
            NAMES  = {"common_egg":"🥚 Common Egg","rare_egg":"🥚✨ Rare Egg",
                      "celestial_egg":"🌌🥚 Celestial Egg","abyssal_egg":"🌊💎 Abyssal Egg"}
            out = []
            for eid in PRICES:
                pool = get_egg_pool(eid)
                alt = _BASE_EGG_POOLS.get(eid,{}).get("altered_chance",0)
                pool_str = "Base odds: " + " · ".join(f"{RARITY_EMOJI.get(r,'⚪')} {v*(1-alt)*100:.1f}%"
                    for r,v in sorted(pool.items(), key=lambda x: RARITY_ORDER.index(x[0]) if x[0] in RARITY_ORDER else 99))
                if alt: pool_str += f" · ⚠️ Altered {int(alt*100)}%"
                out.append((eid, NAMES[eid], PRICES[eid], pool_str))
            return out

        def get_incub_eggs():
            """Returns list of (id, egg_dict, price) sorted by rarity."""
            from cogs.world import EGGS, EGG_PRICES
            out = []
            for eid, egg in EGGS.items():
                if eid=="world_tree_seed":continue
                price = EGG_PRICES[egg["rarity"]]
                out.append((eid, egg, price))
            out.sort(key=lambda x: RARITY_ORDER.index(x[1].get("rarity","common")) if x[1].get("rarity","common") in RARITY_ORDER else 99)
            return out

        def get_items_by_type(types):
            from utils.db import load_items
            items = load_items()
            out = [{**i, "id": k} for k, i in items.items() if i.get("price",0) > 0 and i.get("type") in types]
            out.sort(key=lambda i: (RARITY_ORDER.index(i["rarity"]) if i["rarity"] in RARITY_ORDER else 99, i["price"]))
            return out

        def get_shard_items(week_data):
            from cogs.utilities import SHARD_SHOP
            SUMMON_IDS = {"epoch_shard","firstborn_ember","void_prism"}
            regular = [(s,i) for s,i in SHARD_SHOP.items() if s not in SUMMON_IDS]
            summons  = [(s,i) for s,i in SHARD_SHOP.items() if s in SUMMON_IDS]
            return regular, summons

        # ── embed builders ─────────────────────────────────────────────────
        def make_embed(title, description, color="legendary"):
            emb = discord.Embed(title=f"🏪 {title}", description=description, color=COLORS.get(color, COLORS["legendary"]))
            emb.set_footer(text="ChibiBeasts 🐾 · Closes after 3 minutes of inactivity"+(" · Whimsy Merchant: 5% off gold prices at checkout" if merchant else ""))
            return emb

        def build_instant_embed(page, items):
            from cogs.hatch import HATCH_EGGS
            gold = player["gold"]
            per  = 3
            total = max(1,(len(items)+per-1)//per)
            page  = max(1,min(page,total))
            emb = make_embed("⚡ Instant Eggs", f"💰 `{gold:,}g` · Page {page}/{total}\nHatch immediately with `/hatch` — no waiting.\n\u200b")
            for eid, name, price, pool_str in items[(page-1)*per:page*per]:
                emb.add_field(name=f"{name} — `{price:,}g`", value=pool_str+f"\nTrainer Lv.{HATCH_EGGS[eid].get('min_level',1)}", inline=False)
            return emb, page, total

        def build_incub_embed(page, items):
            from utils.economy_balance import shop_level
            gold = player["gold"]
            per  = 3
            total = max(1,(len(items)+per-1)//per)
            page  = max(1,min(page,total))
            emb = make_embed("⏱️ Incubation Eggs", f"💰 `{gold:,}g` · Page {page}/{total}\nTend with `/tend` after purchase.\n\u200b")
            for eid, egg, price in items[(page-1)*per:page*per]:
                r = RARITY_EMOJI.get(egg.get("rarity","common"),"⚪")
                emoji = egg.get("emoji","🥚")
                hours = egg.get("incubation_hours",1)
                price_str = f"`{price:,}g`" if price > 0 else "`Free`"
                emb.add_field(name=f"{emoji} {egg['name']} — {price_str}",
                    value=f"{r} {egg.get('rarity','?').title()} · ⏱️ {hours}h incubation - Trainer Lv.{shop_level(egg)}\n{egg.get('flavor','')[:80]}", inline=False)
            return emb, page, total

        def build_potions_embed(page, items, label, color):
            gold = player["gold"]
            per  = 3
            total = max(1,(len(items)+per-1)//per)
            page  = max(1,min(page,total))
            emb = make_embed(label, f"💰 `{gold:,}g` · Page {page}/{total}\nUse with `/use <item name>`.\n\u200b", color)
            for item in items[(page-1)*per:page*per]:
                r = RARITY_EMOJI.get(item["rarity"],"⚪")
                emb.add_field(name=f"{r} {item['name']} — `{item['price']:,}g`", value=item["description"]+f"\nTrainer Lv.{item.get('min_level',1)}", inline=False)
            return emb, page, total

        def build_shard_embed(page, regular, summons, week_data, shards):
            per  = 3
            reg_pages = max(1,(len(regular)+per-1)//per)
            total = reg_pages + (1 if summons else 0)
            page  = max(1,min(page,total))
            is_summon = page > reg_pages
            if is_summon:
                emb = make_embed("💎 Ancient Relics", f"🔮 `{shards}` shards\n*These items call something that has been waiting a very long time.*\n\u200b","ancient")
                page_items = summons
            else:
                emb = make_embed("💎 Shard Shop", f"🔮 `{shards}` shards · Page {page}/{total}\n*Exclusive items purchasable with Celestial Shards.*\n\u200b","divine")
                page_items = regular[(page-1)*per:page*per]
            for sid, item in page_items:
                lim = f" · {item['weekly_limit']}/week" if item["weekly_limit"] else ""
                done = week_data.get(sid,0) >= item["weekly_limit"] > 0
                status = " ✅" if done else ""
                emb.add_field(name=f"{item['name']} — `{item['cost']} 🔮`{lim}{status}", value=item["desc"]+f"\nTrainer Lv.{item.get('min_level',1)}", inline=False)
            return emb, page, total, reg_pages

        # ── main view ──────────────────────────────────────────────────────
        class ShopView(discord.ui.View):
            def __init__(self_v, section="instant"):
                super().__init__(timeout=180)
                self_v.section  = section
                self_v.page     = 1
                self_v.week_data = {}
                self_v.message = None
                self_v._render_lock = asyncio.Lock()
                self_v._section_select = None
                self_v._pagination = None
                self_v._read_week_data()
                self_v._load_section_data()
                self_v._rebuild()

            def _read_week_data(self_v):
                try:
                    limits = json.loads(player.get('shard_shop_week') or '{}')
                except (TypeError, json.JSONDecodeError):
                    limits = {}
                week = datetime.now(timezone.utc).strftime('%G-W%V')
                self_v.week_data = limits if isinstance(limits, dict) and limits.get('week') == week else {}

            async def refresh(self_v, bi, *, section=None, delta=0, acknowledged=False):
                if bi.user.id != uid:
                    return await bi.response.send_message("✦ This isn't your shop!", ephemeral=True)
                # Acknowledge before SQLite or another render can make us wait.
                if not acknowledged:
                    await bi.response.defer()
                # Serialise the entire edit, including HTTP. Otherwise Discord
                # can display one page while the view registers another page's
                # controls when two tab responses complete out of order.
                async with self_v._render_lock:
                    if self_v.is_finished():
                        return await bi.followup.send('This shop has closed. Use /shop to reopen it.', ephemeral=True)
                    fresh = await get_player(uid)
                    if fresh:
                        player.update(fresh)
                    self_v._read_week_data()
                    if section is not None:
                        self_v.section = section
                        self_v.page = 1
                    self_v.page += delta
                    self_v._rebuild()
                    async with asyncio.timeout(15):
                        await bi.edit_original_response(embed=self_v._current_embed(), view=self_v)

            async def on_error(self_v, bi, error, item):
                logging.getLogger('chibibeasts.shop').error(
                    'Shop callback failed: trainer=%s section=%s page=%s control=%s',
                    uid, self_v.section, self_v.page, getattr(item, 'custom_id', None), exc_info=error,
                )
                message = 'The shop request failed. Check /inventory for any completed purchase, then use /shop to reopen.'
                try:
                    if bi.response.is_done():
                        await bi.followup.send(message, ephemeral=True)
                    else:
                        await bi.response.send_message(message, ephemeral=True)
                except discord.HTTPException:
                    pass

            async def on_timeout(self_v):
                async with self_v._render_lock:
                    for child in self_v.children:
                        child.disabled = True
                    if self_v.message:
                        try:
                            await self_v.message.edit(content='Shop closed after 3 minutes of inactivity. Use /shop to reopen.', view=self_v)
                        except discord.HTTPException:
                            pass

            def _load_section_data(self_v):
                self_v._instant_items  = get_instant_eggs()
                self_v._incub_items    = get_incub_eggs()
                self_v._potion_items   = get_items_by_type({"heal","revive","cure","mana","ultimate_charge"})
                self_v._consumable_items = get_items_by_type({"happiness","happiness_boost","exp","stat_boost","cooldown","cooldown_reset","encounter","encounter_boost","incubation_boost"})
                self_v._other_items    = get_items_by_type({"defense_boost","speed_boost","unlock","permanent_boost"})

            def _rebuild(self_v):
                self_v.clear_items()
                # Row 0: section select
                select = self_v._section_select or discord.ui.Select(
                    placeholder="📂 Switch section…",
                    options=[
                        discord.SelectOption(label="⚡ Instant Eggs",     value="instant",    description="Hatch immediately — no waiting",      default=self_v.section=="instant"),
                        discord.SelectOption(label="⏱️ Incubation Eggs",  value="incubation", description="Timed eggs with unique beast pools",  default=self_v.section=="incubation"),
                        discord.SelectOption(label="🔮 Revive & Heal",    value="potions",    description="Heals, cures, Phoenix Elixirs",       default=self_v.section=="potions"),
                        discord.SelectOption(label="⚗️ Consumables",      value="consumables",description="EXP, happiness, stat boosts",         default=self_v.section=="consumables"),
                        discord.SelectOption(label="🎒 Special Items",    value="items",      description="Evolution items & permanent upgrades", default=self_v.section=="items"),
                        discord.SelectOption(label="💎 Shard Shop",       value="shards",     description="Spend Celestial Shards",               default=self_v.section=="shards"),
                    ],
                    row=0
                )
                self_v._section_select = select
                for option in select.options:
                    option.default = option.value == self_v.section
                async def _on_select(bi):
                    await self_v.refresh(bi, section=bi.data['values'][0])
                select.callback = _on_select
                self_v.add_item(select)
                # Rows 1-3: buy buttons; Row 4: pagination
                self_v._add_buy_buttons()

            def _current_embed(self_v):
                s = self_v.section
                p = self_v.page
                if s == "instant":
                    emb, _, _ = build_instant_embed(p, self_v._instant_items)
                elif s == "incubation":
                    emb, _, _ = build_incub_embed(p, self_v._incub_items)
                elif s == "potions":
                    emb, _, _ = build_potions_embed(p, self_v._potion_items, "🔮 Revive & Heal", "epic")
                elif s == "consumables":
                    emb, _, _ = build_potions_embed(p, self_v._consumable_items, "⚗️ Consumables", "rare")
                elif s == "items":
                    emb, _, _ = build_potions_embed(p, self_v._other_items, "🎒 Special Items", "legendary")
                else:
                    shards = player.get("celestial_shards",0)
                    reg, summons = get_shard_items(self_v.week_data)
                    emb, _, _, _ = build_shard_embed(p, reg, summons, self_v.week_data, shards)
                return emb

            def _add_buy_buttons(self_v):
                s = self_v.section
                p = self_v.page

                if s == "instant":
                    items = self_v._instant_items
                    per   = 3
                    total = max(1,(len(items)+per-1)//per)
                    p     = max(1,min(p,total))
                    self_v.page = p
                    for i,(eid,name,price,_) in enumerate(items[(p-1)*per:p*per]):
                        short = name.replace("🥚","").replace("✨","").replace("🌌","").replace("🌊💎","").strip()
                        b1 = discord.ui.Button(label=short, emoji="🥚", style=discord.ButtonStyle.success, row=i+1)
                        async def _b1(bi, _id=eid, _p=price, _n=name):
                            if bi.user.id!=uid: return await bi.response.send_message("✦ Not your shop!",ephemeral=True)
                            await _buy(bi,_id,_p,_n,"Use `/hatch` to open!")
                        b5 = discord.ui.Button(label="+5", style=discord.ButtonStyle.secondary, row=i+1)
                        async def _b5(bi, _id=eid, _p=price, _n=name):
                            if bi.user.id!=uid: return await bi.response.send_message("✦ Not your shop!",ephemeral=True)
                            await _buy(bi,_id,_p,_n,"Use `/hatch` to open!",5)
                        b1.callback=_b1; b5.callback=_b5
                        self_v.add_item(b1); self_v.add_item(b5)
                    self_v._add_pagination(total, s)

                elif s == "incubation":
                    items = self_v._incub_items
                    per   = 3
                    total = max(1,(len(items)+per-1)//per)
                    p     = max(1,min(p,total))
                    self_v.page = p
                    for i,(eid,egg,price) in enumerate(items[(p-1)*per:p*per]):
                        emoji = egg.get("emoji","🥚")
                        label = egg["name"][:20]
                        b1 = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.success, row=i+1)
                        async def _b1(bi, _id=eid, _p=price, _n=egg["name"]):
                            if bi.user.id!=uid: return await bi.response.send_message("✦ Not your shop!",ephemeral=True)
                            await _buy(bi,_id,_p,_n,"Use `/tend` to care for your egg!")
                        b1.callback=_b1
                        self_v.add_item(b1)
                    self_v._add_pagination(total, s)

                elif s in ("potions","consumables","items"):
                    item_map = {"potions":self_v._potion_items,"consumables":self_v._consumable_items,"items":self_v._other_items}
                    items = item_map[s]
                    per   = 3
                    total = max(1,(len(items)+per-1)//per)
                    p     = max(1,min(p,total))
                    self_v.page = p
                    for i,item in enumerate(items[(p-1)*per:p*per]):
                        r = RARITY_EMOJI.get(item["rarity"],"⚪")
                        b1 = discord.ui.Button(label=item["name"][:22], emoji=r, style=discord.ButtonStyle.success, row=i+1)
                        async def _b1(bi, _id=item["id"], _p=item["price"], _n=item["name"]):
                            if bi.user.id!=uid: return await bi.response.send_message("✦ Not your shop!",ephemeral=True)
                            await _buy(bi,_id,_p,_n,f"Use `/use {_n}` to apply!")
                        b5 = discord.ui.Button(label="+5", style=discord.ButtonStyle.secondary, row=i+1)
                        async def _b5(bi, _id=item["id"], _p=item["price"], _n=item["name"]):
                            if bi.user.id!=uid: return await bi.response.send_message("✦ Not your shop!",ephemeral=True)
                            await _buy(bi,_id,_p,_n,f"Use `/use {_n}` to apply!",5)
                        b1.callback=_b1; b5.callback=_b5
                        self_v.add_item(b1); self_v.add_item(b5)
                    self_v._add_pagination(total, s)

                else:  # shards
                    shards = player.get("celestial_shards",0)
                    reg, summons = get_shard_items(self_v.week_data)
                    per   = 3
                    reg_pages = max(1,(len(reg)+per-1)//per)
                    total = reg_pages + (1 if summons else 0)
                    p     = max(1,min(p,total))
                    self_v.page = p
                    is_summon = p > reg_pages
                    page_items = summons if is_summon else reg[(p-1)*per:p*per]
                    from cogs.utilities import SHARD_SHOP
                    for i,(sid,item) in enumerate(page_items):
                        done = self_v.week_data.get(sid,0) >= item["weekly_limit"] > 0
                        can  = shards >= item["cost"]
                        lbl  = f"{item['name'][:18]} ({item['cost']}🔮)"
                        btn  = discord.ui.Button(label=lbl,
                            style=discord.ButtonStyle.primary if (can and not done) else discord.ButtonStyle.secondary,
                            disabled=not can or done, row=i+1)
                        async def _cb(bi, _sid=sid):
                            if bi.user.id!=uid:return await bi.response.send_message("Not your shop.",ephemeral=True)
                            await bi.response.defer(ephemeral=True)
                            from utils.shops import purchase_shard
                            try:
                                result,remaining,limits=await purchase_shard(uid,_sid)
                                self_v.week_data=limits
                                await bi.followup.send(f"{result}\nRemaining: {remaining} Celestial Shards.",ephemeral=True)
                                await self_v.refresh(bi, acknowledged=True)
                            except ValueError as error:await bi.followup.send(str(error),ephemeral=True)
                        btn.callback=_cb
                        self_v.add_item(btn)
                    self_v._add_pagination(total, s)

            def _add_pagination(self_v, total, s):
                if self_v._pagination is None:
                    prev = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, row=4)
                    pg = discord.ui.Button(style=discord.ButtonStyle.secondary, disabled=True, row=4)
                    nxt = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, row=4)
                    async def _prev(bi):
                        await self_v.refresh(bi, delta=-1)
                    async def _nxt(bi):
                        await self_v.refresh(bi, delta=1)
                    prev.callback = _prev
                    nxt.callback = _nxt
                    self_v._pagination = (prev, pg, nxt)
                prev, pg, nxt = self_v._pagination
                prev.disabled = self_v.page <= 1
                pg.label = f'{self_v.page}/{total}'
                nxt.disabled = self_v.page >= total
                self_v.add_item(prev); self_v.add_item(pg); self_v.add_item(nxt)

        view = ShopView("instant")
        message = await interaction.followup.send(embed=view._current_embed(), view=view, wait=True)
        # Use the bot's channel message handle for expiry edits, even after the
        # original slash-command webhook token has expired during active browsing.
        view.message = interaction.channel.get_partial_message(message.id) if interaction.channel else message

async def setup(bot: commands.Bot):
    await bot.add_cog(Profile(bot))
    await bot.add_cog(Inventory(bot))
    await bot.add_cog(Shop(bot))
