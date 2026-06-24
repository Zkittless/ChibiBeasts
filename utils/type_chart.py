# ── ChibiBeasts Type Chart ───────────────────────────────────────────────────
# Standard 2x / 0.5x / 1x multiplier system, lore-grounded.
# "Cosmic" is no longer fully neutral — shadow and light both interact with it.
# Shadow represents the void before stars existed; it cuts through cosmic presence.
# Light represents the brightness that cosmic beings dwarf and absorb — they
# are stronger than ordinary light, but shadow predates them both.
# Everything else still defaults to 1.0x against cosmic.
#
# Each entry: attacking_type → {defending_type: multiplier}
# Omitted pairs default to 1.0x.

TYPE_CHART: dict[str, dict[str, float]] = {
    "fire": {
        "nature": 2.0,   # Fire burns plants
        "ice":    2.0,   # Fire melts ice
        "earth":  0.5,   # Earth smothers flame
        "water":  0.5,   # Water douses fire
    },
    "water": {
        "fire":   2.0,   # Water extinguishes fire
        "earth":  2.0,   # Water erodes earth
        "nature": 0.5,   # Plants drink water
        "ice":    0.5,   # Cold resists cold
    },
    "nature": {
        "water":  2.0,   # Roots drink, grow through water
        "earth":  2.0,   # Nature breaks through stone
        "fire":   0.5,   # Burns easily
        "wind":   0.5,   # Wind scatters spores
    },
    "earth": {
        "fire":   2.0,   # Earth smothers fire
        "shadow": 2.0,   # Earth is grounded, stable — indifferent to darkness
        "water":  0.5,   # Washed away by water
        "nature": 0.5,   # Plants break through earth
    },
    "wind": {
        "nature": 2.0,   # Wind rips at plants, scatters growth
        "fire":   2.0,   # Wind fans flames — fuels fire
        "earth":  0.5,   # Earth anchors against wind
        "arcane": 0.5,   # Arcane magic is precise; wind is chaos
    },
    "ice": {
        "water":  2.0,   # Freezes water-type instincts
        "nature": 2.0,   # Cold kills plants
        "fire":   0.5,   # Melted by fire
        "earth":  0.5,   # Earth insulates against cold
    },
    "arcane": {
        "shadow": 2.0,   # Light of knowledge pierces shadow
        "wind":   2.0,   # Arcane precision tames chaos
        "earth":  0.5,   # Brute stone resists magic
        "light":  0.5,   # Light deflects arcane light
    },
    "shadow": {
        "arcane": 2.0,   # Shadows swallow spelllight
        "light":  2.0,   # Shadow overwhelms brightness
        "cosmic": 2.0,   # The void before stars predates cosmic presence — shadow cuts through it
        "earth":  0.5,   # Earth is indifferent to darkness
        "nature": 0.5,   # Nature is too alive for shadow to fully grip
    },
    "light": {
        "shadow": 2.0,   # Light banishes shadow
        "cosmic": 2.0,   # Cosmic beings exist beyond ordinary light but pure light still reaches them
        "ice":    2.0,   # Light melts ice
        "arcane": 0.5,   # Arcane absorbs and redirects light
        "fire":   0.5,   # Fire is already bright; light adds little
    },
    "cosmic": {
        "light":  2.0,   # Cosmic beings dwarf ordinary light — they absorb and overwhelm it
        "shadow": 0.5,   # The void predates even cosmic power — shadow resists cosmic force
    },
}


def get_type_multiplier(attacking_type: str, defending_type: str) -> float:
    """Return the damage multiplier for an attacking type vs a defending type."""
    if not attacking_type or not defending_type:
        return 1.0
    return TYPE_CHART.get(attacking_type, {}).get(defending_type, 1.0)


def type_effectiveness_label(multiplier: float) -> str:
    """Return a human-readable effectiveness label for a multiplier."""
    if multiplier >= 2.0:
        return "⚡ Super effective!"
    elif multiplier <= 0.5:
        return "🛡️ Not very effective..."
    return ""


TYPE_LORE = {
    "fire":   "The first warmth the Loom ever wove. Hungry, bright, and always reaching upward.",
    "water":  "Older than fire — the Loom wove water to carry change from one place to another.",
    "nature": "Every tree is a sentence the Loom wrote and left standing. Nature remembers everything. So do the things that grew before the trees.",
    "earth":  "Pillar's favorite material. Earth is the Loom saying: *this part stays.*",
    "wind":   "The Loom's breath between stitches. Wind carries things — ideas, seeds, ash, the last words of things that burned.",
    "ice":    "Stillness made sharp. The Loom uses ice to press pause on things it wants to keep exactly as they are.",
    "arcane": "Prism's native language. Pure structured intention, given edges and a direction. The most precise thing the Loom makes.",
    "shadow": "Not evil — just everything the Loom wove without quite meaning to. The void before the first thread. It was there first, and it remembers.",
    "light":  "The Loom's own glow, caught and given a heartbeat. Simple. Honest. Older than it looks.",
    "cosmic": "These beings predate the elemental hierarchy. Shadow remembers the time before them. Light cannot quite reach their edges. Everything else simply watches.",
}
