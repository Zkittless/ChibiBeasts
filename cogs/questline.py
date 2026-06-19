import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
import json
import random
from utils.db import get_or_create_player, get_player, update_player, add_beast_to_player
from utils.theme import COLORS, RARITY_EMOJI, TYPE_EMOJI, SPARKLE
from utils.progress import unlock_simple_achievement, notify_unlocks

DB_PATH = "db/chibibeast.db"

# ── Display constants ──────────────────────────────────────────────────────────
# Named rather than hardcoded inline so they're easy to adjust and extend
# without hunting through the interaction loop.
NPC_PREVIEW_LENGTH = 80      # chars shown in /meet NPC preview line
DIALOGUE_MAX_LENGTH = 4000   # Discord embed description hard limit

# ── Relationship level registry ────────────────────────────────────────────────
# Module-level so faction modifiers, server milestones, or story chapter
# unlocks can add new tiers by appending here rather than touching each callsite.
# Each entry: key → (display label, emoji indicator)
RELATIONSHIP_LEVELS: dict[str, tuple[str, str]] = {
    "stranger":  ("Stranger",  "⚪"),
    "known":     ("Known",     "🟢"),
    "trusted":   ("Trusted",   "🔵"),
    "companion": ("Companion", "🌸"),
}

def relationship_display(level: str) -> str:
    """Return the formatted display string for a relationship level."""
    label, icon = RELATIONSHIP_LEVELS.get(level, ("Stranger", "⚪"))
    return f"{icon} {label}"

def relationship_order(level: str) -> int:
    """Return the numeric rank of a relationship level (for comparisons)."""
    return list(RELATIONSHIP_LEVELS.keys()).index(level) if level in RELATIONSHIP_LEVELS else 0

# ── Data loaders ──────────────────────────────────────────────────────────────
def load_questline():
    with open("data/questline.json") as f:
        return json.load(f)["questline"]

def load_npcs():
    with open("data/npcs.json") as f:
        return json.load(f)["npcs"]


# ── Questline state helpers ───────────────────────────────────────────────────
async def get_quest_state(user_id: int) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM player_questline WHERE user_id = ?", (user_id,)
        ) as c:
            row = await c.fetchone()
    if not row:
        return {
            "user_id": user_id,
            "current_chapter": None,
            "completed_chapters": [],
            "step_progress": {},
            "collected_relics": [],
            "npc_relationships": {},
        }
    return {
        "user_id": user_id,
        "current_chapter": row["current_chapter"],
        "completed_chapters": json.loads(row["completed_chapters"] or "[]"),
        "step_progress": json.loads(row["step_progress"] or "{}"),
        "collected_relics": json.loads(row["collected_relics"] or "[]"),
        "npc_relationships": json.loads(row["npc_relationships"] or "{}"),
    }


async def save_quest_state(state: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO player_questline
                (user_id, current_chapter, completed_chapters, step_progress, collected_relics, npc_relationships, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(user_id) DO UPDATE SET
                current_chapter    = excluded.current_chapter,
                completed_chapters = excluded.completed_chapters,
                step_progress      = excluded.step_progress,
                collected_relics   = excluded.collected_relics,
                npc_relationships  = excluded.npc_relationships,
                last_updated       = excluded.last_updated
        """, (
            state["user_id"],
            state["current_chapter"],
            json.dumps(state["completed_chapters"]),
            json.dumps(state["step_progress"]),
            json.dumps(state["collected_relics"]),
            json.dumps(state["npc_relationships"]),
        ))
        await db.commit()


def get_relationship_level(state: dict, npc_id: str) -> str:
    return state["npc_relationships"].get(npc_id, "stranger")


def check_chapter_unlock(chapter: dict, state: dict, player: dict) -> tuple[bool, str]:
    """Returns (can_start, reason_if_not)."""
    cond = chapter.get("unlock_condition", "")
    if cond == "player_level_3":
        if player.get("level", 1) < 3:
            return False, "You need to reach **Level 3** first. Keep exploring and battling!"
    elif cond.endswith("_complete"):
        req = cond.replace("_complete", "")
        if req not in state["completed_chapters"]:
            return False, f"Complete **{req.replace('_',' ').title()}** first."
    return True, ""


def build_progress_bar(current: int, target: int, length: int = 10) -> str:
    filled = min(int((current / max(target, 1)) * length), length)
    return "🟦" * filled + "⬛" * (length - filled) + f" `{current}/{target}`"


def build_chapter_embed(chapter: dict, state: dict, player: dict) -> discord.Embed:
    """Build a clean embed for the current chapter's quest status."""
    npc_id = chapter["npc"]
    npcs = load_npcs()
    npc = npcs.get(npc_id, {})

    embed = discord.Embed(
        title=f"📜 Chapter: {chapter['name']}",
        description=f"*Speak with {npc.get('emoji','')} **{npc.get('name', npc_id)}** in the {chapter['location']}.*",
        color=COLORS["legendary"]
    )

    progress = state["step_progress"].get(chapter["id"], {})
    for step in chapter["steps"]:
        done = progress.get(step["id"], 0)
        target = step.get("target", 1)
        is_complete = done >= target
        status = "✅" if is_complete else build_progress_bar(done, target)
        embed.add_field(
            name=f"{'✅' if is_complete else '⏳'} {step['description']}",
            value=status,
            inline=False
        )
    return embed


# ── Questline event tracker ───────────────────────────────────────────────────
# Called from other cogs (hatch.py, guilds.py, progression.py) to advance steps.

async def advance_quest_step(user_id: int, event_type: str, **kwargs):
    """
    Call this after relevant gameplay events. Checks if the current chapter has
    a step matching `event_type` and advances it. Returns the chapter ID if a
    chapter just completed, None otherwise.
    """
    state = await get_quest_state(user_id)
    if not state["current_chapter"]:
        return None

    questline = load_questline()
    chapter = questline["chapters"].get(state["current_chapter"])
    if not chapter:
        return None

    ch_id = chapter["id"]
    progress = state["step_progress"].setdefault(ch_id, {})
    changed = False

    for step in chapter["steps"]:
        step_id = step["id"]
        step_type = step["type"]
        current = progress.get(step_id, 0)
        target = step.get("target", 1)
        if current >= target:
            continue

        if step_type == "explore_count" and event_type == "explore":
            # If tracking unique biomes, use biome name from kwargs
            if step.get("track_unique_biomes"):
                biomes_visited = set(json.loads(progress.get(step_id + "_biomes", "[]")))
                biome = kwargs.get("biome")
                if biome and biome not in biomes_visited:
                    biomes_visited.add(biome)
                    progress[step_id + "_biomes"] = json.dumps(list(biomes_visited))
                    progress[step_id] = len(biomes_visited)
                    changed = True
            else:
                progress[step_id] = current + 1
                changed = True

        elif step_type == "catch_count" and event_type == "catch":
            progress[step_id] = current + 1
            changed = True

        elif step_type == "catch_specific_beast" and event_type == "catch":
            beast_id = kwargs.get("beast_id", "")
            if beast_id in step.get("target_beasts", []):
                progress[step_id] = 1
                changed = True

        elif step_type == "material_collect" and event_type == "material_gained":
            mat_id = kwargs.get("material_id")
            if mat_id == step.get("material_id"):
                progress[step_id] = min(target, current + kwargs.get("amount", 1))
                changed = True

        elif step_type == "raid_participate" and event_type == "raid_participate":
            progress[step_id] = 1
            changed = True

        elif step_type == "player_level" and event_type == "level_up":
            new_level = kwargs.get("level", 0)
            if new_level >= step.get("target", 25):
                progress[step_id] = new_level
                changed = True

        elif step_type == "server_bestiary_count" and event_type == "bestiary_update":
            count = kwargs.get("count", 0)
            if count >= target:
                progress[step_id] = count
                changed = True

        elif step_type == "relic_collect" and event_type == "relic_found":
            relic_id = kwargs.get("relic_id")
            if relic_id == step.get("relic_id"):
                progress[step_id] = 1
                if relic_id not in state["collected_relics"]:
                    state["collected_relics"].append(relic_id)
                changed = True

        elif step_type == "use_command" and event_type == "questline_command":
            # This step completes when the player uses /questline
            progress[step_id] = 1
            changed = True

    if changed:
        state["step_progress"][ch_id] = progress
        await save_quest_state(state)

    # Check if all steps complete
    all_done = all(
        state["step_progress"].get(ch_id, {}).get(s["id"], 0) >= s.get("target", 1)
        for s in chapter["steps"]
    )
    if all_done and ch_id not in state["completed_chapters"]:
        return ch_id
    return None


class Questline(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /questline ────────────────────────────────────────────────────────
    @app_commands.command(name="questline", description="Check your story questline progress 📜")
    async def questline_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        player = await get_or_create_player(interaction.user.id, str(interaction.user))
        state = await get_quest_state(interaction.user.id)
        questline = load_questline()
        npcs = load_npcs()

        # Trigger "questline_command" event (completes the use_command steps)
        just_completed = await advance_quest_step(interaction.user.id, "questline_command")

        # Determine which chapter to show or unlock next
        chapters = questline["chapters"]
        chapter_order = list(chapters.keys())

        # Find the next chapter to start
        if not state["current_chapter"]:
            # Check if chapter_1 is unlockable
            ch1 = chapters["chapter_1"]
            can_start, reason = check_chapter_unlock(ch1, state, player)
            if can_start:
                state["current_chapter"] = "chapter_1"
                await save_quest_state(state)
            else:
                embed = discord.Embed(
                    title="📜 The Sundering of the Loom",
                    description=(
                        f"*{questline['description']}*\n\n"
                        f"✦ {reason}"
                    ),
                    color=COLORS["info"]
                )
                embed.set_footer(text="ChibiBeasts 🐾  •  Keep playing — your story is just beginning")
                return await interaction.followup.send(embed=embed)

        # Show chapter completion if just finished
        if just_completed:
            chapter = chapters.get(just_completed, {})
            npc_id = chapter.get("npc", "")
            npc = npcs.get(npc_id, {})
            reward = chapter.get("reward", {})

            # Apply rewards
            async with aiosqlite.connect(DB_PATH) as db:
                if reward.get("gold"):
                    await db.execute("UPDATE players SET gold = gold + ? WHERE user_id = ?",
                                     (reward["gold"], interaction.user.id))
                if reward.get("exp"):
                    await db.execute("UPDATE players SET exp = exp + ? WHERE user_id = ?",
                                     (reward["exp"], interaction.user.id))
                if reward.get("celestial_shards"):
                    await db.execute("UPDATE players SET celestial_shards = celestial_shards + ? WHERE user_id = ?",
                                     (reward["celestial_shards"], interaction.user.id))
                await db.commit()

            # Add relic to state
            if reward.get("relic"):
                if reward["relic"] not in state["collected_relics"]:
                    state["collected_relics"].append(reward["relic"])

            # Add items
            if reward.get("items"):
                from utils.db import add_item
                for item_id, qty in reward["items"].items():
                    for _ in range(qty):
                        await add_item(interaction.user.id, item_id)

            # Update NPC relationships
            if reward.get("relationship_unlock"):
                for npc_id_r, level in reward["relationship_unlock"].items():
                    state["npc_relationships"][npc_id_r] = level

            # Mark chapter complete, unlock next
            if just_completed not in state["completed_chapters"]:
                state["completed_chapters"].append(just_completed)

            if reward.get("unlock_next"):
                state["current_chapter"] = reward["unlock_next"]
            else:
                state["current_chapter"] = None  # Questline complete

            # Final achievement
            if reward.get("achievement"):
                newly_unlocked = await unlock_simple_achievement(interaction.user.id, reward["achievement"])
                if newly_unlocked:
                    await notify_unlocks(interaction.channel, interaction.user, [reward["achievement"]])

            await save_quest_state(state)

            # Show completion dialogue
            dialogue = chapter.get("completion_dialogue", ["*Chapter complete.*"])
            dialogue_text = "\n".join(dialogue)

            embed = discord.Embed(
                title=f"✅ Chapter Complete: {chapter['name']}",
                description=dialogue_text[:DIALOGUE_MAX_LENGTH],
                color=COLORS["success"]
            )
            reward_parts = []
            if reward.get("gold"):      reward_parts.append(f"+{reward['gold']:,} 💰 gold")
            if reward.get("exp"):       reward_parts.append(f"+{reward['exp']} EXP")
            if reward.get("celestial_shards"): reward_parts.append(f"+{reward['celestial_shards']} 🔮 shards")
            if reward.get("relic"):     reward_parts.append(f"🪨 {reward['relic'].replace('_',' ').title()}")
            if reward.get("items"):
                for iid in reward["items"]:
                    reward_parts.append(f"📦 {iid.replace('_',' ').title()}")
            if reward_parts:
                embed.add_field(name="🎁 Rewards", value=" | ".join(reward_parts), inline=False)

            npc_disp = npcs.get(chapter.get("npc",""), {})
            embed.set_footer(text=f"ChibiBeasts 🐾  •  {npc_disp.get('emoji','')} {npc_disp.get('name','')} — {npc_disp.get('location','')}")
            return await interaction.followup.send(embed=embed)

        # Show current chapter status
        current_ch_id = state["current_chapter"]
        if not current_ch_id:
            # All done
            embed = discord.Embed(
                title="🌟 The Sundering of the Loom — Complete",
                description=(
                    "*You've witnessed the Loom, met the people who hold it together, "
                    "and received the World-Tree Seed.*\n\n"
                    "*Whatever comes next — the hollow spot southwest of the Ember Wastes, "
                    "the accelerating Sundering, the question Cael is still trying to answer — "
                    "that's a story that hasn't happened yet.*\n\n"
                    "*You're ready for it.*"
                ),
                color=COLORS["divine"]
            )
            completed = len(state["completed_chapters"])
            embed.add_field(
                name="📖 Chapters Completed",
                value=" → ".join(f"*{chapters[c]['name']}*" for c in state["completed_chapters"] if c in chapters),
                inline=False
            )
            embed.set_footer(text="ChibiBeasts 🐾  •  Use /npc <name> to revisit any character")
            return await interaction.followup.send(embed=embed)

        chapter = chapters.get(current_ch_id)
        if not chapter:
            return await interaction.followup.send(embed=discord.Embed(
                description="✦ Something went wrong with your questline state. Please report this.",
                color=COLORS["error"]
            ))

        # Check if this chapter needs to be introduced first
        ch_progress = state["step_progress"].get(current_ch_id, {})
        has_started = bool(ch_progress)

        npc_id = chapter["npc"]
        npc = npcs.get(npc_id, {})

        if not has_started:
            # Show intro dialogue
            intro = chapter.get("intro", ["*...*"])
            embed = discord.Embed(
                title=f"📜 {chapter['name']}",
                description="\n".join(intro)[:DIALOGUE_MAX_LENGTH],
                color=COLORS["legendary"]
            )
            embed.add_field(
                name=f"{npc.get('emoji','')} {npc.get('name', npc_id)} — {npc.get('location','')}",
                value=f"*{npc.get('appearance', '')}*",
                inline=False
            )
            embed.add_field(
                name="📋 Your Objectives",
                value="\n".join(f"• {s['description']}" for s in chapter["steps"]),
                inline=False
            )
            embed.set_footer(text="ChibiBeasts 🐾  •  Complete these objectives, then use /questline again")
            # Mark as started with zero progress
            for step in chapter["steps"]:
                ch_progress.setdefault(step["id"], 0)
            state["step_progress"][current_ch_id] = ch_progress
            await save_quest_state(state)
        else:
            # Show progress status
            embed = build_chapter_embed(chapter, state, player)
            embed.add_field(
                name=f"{npc.get('emoji','')} Meet",
                value=f"*Use `/npc {npc.get('name','')}` to talk to {npc.get('name','')}.*",
                inline=False
            )

        await interaction.followup.send(embed=embed)

    # ── /npc ─────────────────────────────────────────────────────────────
    @app_commands.command(name="npc", description="Talk to an NPC 💬")
    @app_commands.describe(name="The NPC to speak with")
    @app_commands.choices(name=[
        app_commands.Choice(name="📖 Maren (Whispering Woods)", value="maren"),
        app_commands.Choice(name="⏳ Cael (Celestial Loom)",    value="cael"),
        app_commands.Choice(name="⚒️ Sable (Ember Wastes)",    value="sable"),
        app_commands.Choice(name="🌿 Orren (Whispering Woods)", value="orren"),
        app_commands.Choice(name="📚 The Archivist (Celestial Loom)", value="the_archivist"),
    ])
    async def npc(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        npcs = load_npcs()
        npc = npcs.get(name)
        if not npc:
            return await interaction.followup.send(embed=discord.Embed(
                description=f"✦ `{name}` not found.", color=COLORS["error"]
            ))

        state = await get_quest_state(interaction.user.id)
        rel_level = get_relationship_level(state, name)
        rel_lines = npc.get("relationship_levels", {})
        line = rel_lines.get(rel_level, rel_lines.get("stranger", "*...*"))

        # Pick a contextual quote too
        context_keys = [k for k in npc if k.startswith("on_")]
        context_key = random.choice(context_keys) if context_keys else None
        context_quote = npc.get(context_key, "") if context_key else ""
        context_label = context_key.replace("on_", "").replace("_", " ").title() if context_key else ""

        embed = discord.Embed(
            title=f"{npc.get('emoji','')} {npc['name']}",
            description=(
                f"*{npc['title']}*\n"
                f"📍 {npc['location']}\n\n"
                f"{npc.get('appearance','')}"
            ),
            color=COLORS.get(rel_level, COLORS["info"])
        )
        embed.add_field(
            name=f"💬 {npc['name']} says:",
            value=line,
            inline=False
        )
        if context_quote:
            embed.add_field(
                name=f"📖 On {context_label}:",
                value=context_quote,
                inline=False
            )

        # Show companion beast if available
        companion_id = npc.get("beast_companion")
        if companion_id:
            from utils.db import get_beast_data
            companion = get_beast_data(companion_id)
            if companion:
                embed.add_field(
                    name=f"🐾 Companion",
                    value=f"{TYPE_EMOJI.get(companion.get('type',''),'❓')} **{companion['name']}** — *{companion['title']}*",
                    inline=False
                )

        # Show relationship level
        embed.set_footer(
            text=f"Relationship: {relationship_display(rel_level)}  •  "
                 f"ChibiBeasts 🐾  •  /questline to track your progress"
        )
        await interaction.followup.send(embed=embed)

    # ── /meet ─────────────────────────────────────────────────────────────
    @app_commands.command(name="meet", description="View all NPCs and where to find them 🗺️")
    async def meet(self, interaction: discord.Interaction):
        await interaction.response.defer()
        npcs = load_npcs()
        state = await get_quest_state(interaction.user.id)

        embed = discord.Embed(
            title="🗺️ People of the Loom",
            description=(
                "*These are the people you'll meet on your journey. "
                "Each one knows something the others don't.*"
            ),
            color=COLORS["info"]
        )
        for npc_id, npc in npcs.items():
            rel = get_relationship_level(state, npc_id)
            rel_icon = RELATIONSHIP_LEVELS.get(rel, ("Stranger", "⚪"))[1]
            embed.add_field(
                name=f"{npc.get('emoji','')} {npc['name']} {rel_icon}",
                value=(
                    f"*{npc['title']}*\n"
                    f"📍 {npc['location']}\n"
                    f"_{npc.get('first_meeting','')[:NPC_PREVIEW_LENGTH]}..._"
                ),
                inline=False
            )
        embed.set_footer(
            text="ChibiBeasts 🐾  •  Use /npc <name> to speak with anyone • /questline to track your story"
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Questline(bot))
