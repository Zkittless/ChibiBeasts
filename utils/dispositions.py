# ── ChibiBeasts Beast Dispositions ──────────────────────────────────────────
# When a beast is caught or hatched it rolls a Disposition: a permanent +10%
# boost to one stat and a -10% penalty to another. This is purely additive on
# top of base stats, calculated at time of catch and stored directly in the
# player_beasts row — so a beast's displayed stats are always the real values
# the battle engine reads (no hidden multipliers at runtime).
#
# Starters never roll a disposition (they're already who they are).
# Boss beasts (corrupted, ancient, altered divine) have LOCKED dispositions
# unique to each one — they are specific entities, not random wild catches.

import random

# ── Disposition definitions ───────────────────────────────────────────────
# Each entry: display name → {emoji, description, boost_stat, penalty_stat,
# lore_flavor, architect_affinity}
# architect_affinity is narrative-only (used in flavor text, not mechanics).

DISPOSITIONS = {
    "whimsical": {
        "name":       "Whimsical",
        "emoji":      "🎀",
        "boost":      "speed",
        "penalty":    "defense",
        "flavor":     "This one moves like it's always chasing something it just thought of.",
        "affinity":   "The Loom",
    },
    "stardust_kissed": {
        "name":       "Stardust-Kissed",
        "emoji":      "✨",
        "boost":      "mana",
        "penalty":    "attack",
        "flavor":     "A faint cosmic shimmer clings to its fur. It prefers magic to muscle.",
        "affinity":   "Cosmic Creators",
    },
    "feral": {
        "name":       "Feral",
        "emoji":      "🔥",
        "boost":      "attack",
        "penalty":    "speed",
        "flavor":     "Hits first. Thinks never. Somehow this works out fine.",
        "affinity":   "Primordial Aspects",
    },
    "ancient": {
        "name":       "Ancient",
        "emoji":      "🏛️",
        "boost":      "hp",
        "penalty":    "speed",
        "flavor":     "Moves like it's been here longer than the word 'hurry'.",
        "affinity":   "Mythological Pillars",
    },
    "blighted": {
        "name":       "Blighted",
        "emoji":      "🌑",
        "boost":      "attack",
        "penalty":    "hp",
        "flavor":     "Something about it feels slightly unfinished, like a thread that didn't quite close. It hits harder for it.",
        "affinity":   "Altered",
    },
    "crystalline": {
        "name":       "Crystalline",
        "emoji":      "🔷",
        "boost":      "defense",
        "penalty":    "mana",
        "flavor":     "Precise. Measured. Shrugs off hits with an almost architectural calm.",
        "affinity":   "The Architect",
    },
    "radiant": {
        "name":       "Radiant",
        "emoji":      "☀️",
        "boost":      "mana",
        "penalty":    "defense",
        "flavor":     "Practically glows. Uses mana like most beasts use air.",
        "affinity":   "Celestial Loom",
    },
    "rooted": {
        "name":       "Rooted",
        "emoji":      "🌿",
        "boost":      "defense",
        "penalty":    "speed",
        "flavor":     "Immovable. Patient. Probably still thinking about something that happened three days ago.",
        "affinity":   "The Pillar",
    },
    # ── Boss-exclusive locked dispositions ──────────────────────────────
    # These are never rolled randomly — only assigned to their specific beast.
    "tainted_deep": {
        "name":       "Tainted Deep",
        "emoji":      "🌊",
        "boost":      "hp",
        "penalty":    "speed",
        "flavor":     "The corruption made it vast. It doesn't move fast — it doesn't need to.",
        "affinity":   "Corrupted",
    },
    "void_hunger": {
        "name":       "Void Hunger",
        "emoji":      "🖤",
        "boost":      "attack",
        "penalty":    "defense",
        "flavor":     "The rift energy didn't just corrupt it — it made it want. It always wants.",
        "affinity":   "Corrupted",
    },
    "null_flame": {
        "name":       "Null Flame",
        "emoji":      "🔥",
        "boost":      "attack",
        "penalty":    "mana",
        "flavor":     "Its fire leaves nothing. Not ash. Not warmth. Nothing. More attack, less thought.",
        "affinity":   "Corrupted",
    },
    "primordial_epoch": {
        "name":       "Primordial Epoch",
        "emoji":      "⏳",
        "boost":      "speed",
        "penalty":    "hp",
        "flavor":     "It existed before time had rules. Speed is the only stat that still applies.",
        "affinity":   "Ancient",
    },
    "first_fire": {
        "name":       "First Fire",
        "emoji":      "🌟",
        "boost":      "mana",
        "penalty":    "defense",
        "flavor":     "Burns with the flame that started everything. Doesn't need to defend — it was here before defense was invented.",
        "affinity":   "Ancient",
    },
    "pre_existence": {
        "name":       "Pre-Existence",
        "emoji":      "🌌",
        "boost":      "defense",
        "penalty":    "attack",
        "flavor":     "The void before everything. Nothing touches it. It doesn't need to touch back.",
        "affinity":   "Ancient",
    },
    "shattered_epoch": {
        "name":       "Shattered Epoch",
        "emoji":      "🕰️",
        "boost":      "speed",
        "penalty":    "defense",
        "flavor":     "Time runs wrong around it. Faster in the wrong direction. It acts before the moment exists.",
        "affinity":   "Altered",
    },
    "null_origin": {
        "name":       "Null Origin",
        "emoji":      "🔥",
        "boost":      "attack",
        "penalty":    "hp",
        "flavor":     "Burns across realities simultaneously. More firepower than any one timeline can contain.",
        "affinity":   "Altered",
    },
    "consuming_dark": {
        "name":       "Consuming Dark",
        "emoji":      "🌑",
        "boost":      "mana",
        "penalty":    "speed",
        "flavor":     "It doesn't move through space. Space moves around it. Slow, but the void has infinite patience.",
        "affinity":   "Altered",
    },
}

# ── Boss beasts get locked dispositions — no random roll ─────────────────
BOSS_DISPOSITIONS = {
    # Corrupted
    "corrupted_leviathan": "tainted_deep",
    "corrupted_fenrir":    "void_hunger",
    "corrupted_dragon":    "null_flame",
    # Ancient
    "ancient_chronos":     "primordial_epoch",
    "ancient_genesis":     "first_fire",
    "ancient_abyss":       "pre_existence",
    # Altered Divine
    "void_chronos":        "shattered_epoch",
    "fractured_genesis":   "null_origin",
    "abyssal_nebula":      "consuming_dark",
}

DISPOSITION_LIST = [k for k in DISPOSITIONS if k not in BOSS_DISPOSITIONS.values()]

# Starters and boss beasts skip random disposition rolls
DISPOSITION_EXEMPT = {"prismite", "twine", "gloop", "barkley"} | set(BOSS_DISPOSITIONS.keys())


def roll_disposition(beast_id: str) -> str | None:
    """Roll a disposition for a beast.
    Boss beasts get their locked disposition. Exempt beasts get None. Others roll randomly."""
    if beast_id in BOSS_DISPOSITIONS:
        return BOSS_DISPOSITIONS[beast_id]
    if beast_id in DISPOSITION_EXEMPT:
        return None
    return random.choice(DISPOSITION_LIST)


def apply_disposition(base_stats: dict, disposition_id: str | None) -> dict:
    """
    Return a NEW stat dict with disposition applied (+10% boost, -10% penalty),
    with all values clamped to a minimum of 1. Does not mutate base_stats.
    """
    if not disposition_id or disposition_id not in DISPOSITIONS:
        return dict(base_stats)

    d = DISPOSITIONS[disposition_id]
    stats = dict(base_stats)

    boost_stat   = d["boost"]
    penalty_stat = d["penalty"]

    if boost_stat in stats:
        stats[boost_stat] = max(1, round(stats[boost_stat] * 1.10))
    if penalty_stat in stats:
        stats[penalty_stat] = max(1, round(stats[penalty_stat] * 0.90))

    return stats


def disposition_display(disposition_id: str | None) -> str:
    """Return a short formatted string for embedding in beast info."""
    if not disposition_id or disposition_id not in DISPOSITIONS:
        return "⚪ *None*"
    d = DISPOSITIONS[disposition_id]
    boost   = d["boost"].capitalize()
    penalty = d["penalty"].capitalize()
    return (
        f"{d['emoji']} **{d['name']}** — *{d['flavor']}*\n"
        f"▲ +10% {boost}  •  ▼ -10% {penalty}"
    )
