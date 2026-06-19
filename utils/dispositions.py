# ── ChibiBeasts Beast Dispositions ──────────────────────────────────────────
# When a beast is caught or hatched it rolls a Disposition: a permanent +10%
# boost to one stat and a -10% penalty to another. This is purely additive on
# top of base stats, calculated at time of catch and stored directly in the
# player_beasts row — so a beast's displayed stats are always the real values
# the battle engine reads (no hidden multipliers at runtime).
#
# Starters never roll a disposition (they're already who they are).
# Altered Divines don't roll one either — their stats are set by the raid.

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
        "affinity":   "The Loom",        # time/curiosity → quick, impulsive
    },
    "stardust_kissed": {
        "name":       "Stardust-Kissed",
        "emoji":      "✨",
        "boost":      "mana",
        "penalty":    "attack",
        "flavor":     "A faint cosmic shimmer clings to its fur. It prefers magic to muscle.",
        "affinity":   "Cosmic Creators", # arcane/magical inclination
    },
    "feral": {
        "name":       "Feral",
        "emoji":      "🔥",
        "boost":      "attack",
        "penalty":    "speed",
        "flavor":     "Hits first. Thinks never. Somehow this works out fine.",
        "affinity":   "Primordial Aspects",  # raw force
    },
    "ancient": {
        "name":       "Ancient",
        "emoji":      "🏛️",
        "boost":      "hp",
        "penalty":    "speed",
        "flavor":     "Moves like it's been here longer than the word 'hurry'.",
        "affinity":   "Mythological Pillars",  # foundation/endurance
    },
    "blighted": {
        "name":       "Blighted",
        "emoji":      "🌑",
        "boost":      "attack",
        "penalty":    "hp",
        "flavor":     "Something about it feels slightly unfinished, like a thread that didn't quite close. It hits harder for it.",
        "affinity":   "Altered",         # echo of the Sundering
    },
    "crystalline": {
        "name":       "Crystalline",
        "emoji":      "🔷",
        "boost":      "defense",
        "penalty":    "mana",
        "flavor":     "Precise. Measured. Shrugs off hits with an almost architectural calm.",
        "affinity":   "The Architect",   # order/form
    },
    "radiant": {
        "name":       "Radiant",
        "emoji":      "☀️",
        "boost":      "mana",
        "penalty":    "defense",
        "flavor":     "Practically glows. Uses mana like most beasts use air.",
        "affinity":   "Celestial Loom",  # light/fate
    },
    "rooted": {
        "name":       "Rooted",
        "emoji":      "🌿",
        "boost":      "defense",
        "penalty":    "speed",
        "flavor":     "Immovable. Patient. Probably still thinking about something that happened three days ago.",
        "affinity":   "The Pillar",      # steadiness/nature
    },
}

DISPOSITION_LIST = list(DISPOSITIONS.keys())

# Starters and altered divines explicitly skip disposition rolls
DISPOSITION_EXEMPT = {"prismite", "twine", "gloop", "barkley"}


def roll_disposition(beast_id: str) -> str | None:
    """Roll a random disposition for a beast. Returns None for exempt beasts."""
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
