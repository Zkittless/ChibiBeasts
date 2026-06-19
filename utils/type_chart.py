# ── ChibiBeasts Type Chart ───────────────────────────────────────────────────
# Standard 2x / 0.5x / 1x multiplier system, lore-grounded.
# "Cosmic" is intentionally neutral to everything — Divine beasts exist outside
# the elemental hierarchy the Architects built, so they neither benefit from
# it nor suffer from it. This keeps Divine encounters feeling special.
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
        "shadow": 2.0,   # Light pushes out darkness (earth = grounded, stable)
        "water":  0.5,   # Washed away by water
        "nature": 0.5,   # Plants break through earth
    },
    "wind": {
        "nature": 2.0,   # Wind scatters seeds and spores harmlessly; rips at plants
        "fire":   2.0,   # Wind fans flames → fire-boost framing; wind fuels fire
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
        "light":  0.5,   # Light deflects light
    },
    "shadow": {
        "arcane": 2.0,   # Shadows swallow spelllight
        "light":  2.0,   # Shadow overwhelms brightness
        "earth":  0.5,   # Earth is indifferent to darkness
        "nature": 0.5,   # Nature is too alive for shadow to fully grip
    },
    "light": {
        "shadow": 2.0,   # Light banishes shadow
        "ice":    2.0,   # Light melts ice
        "arcane": 0.5,   # Arcane absorbs and redirects light
        "fire":   0.5,   # Fire is already bright; light adds little
    },
    # cosmic: neutral to everything (intentional — see file header)
    "cosmic": {},
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
    "nature": "Every tree is a sentence the Loom wrote and left standing. Nature remembers everything.",
    "earth":  "Pillar's favorite material. Earth is the Loom saying: *this part stays.*",
    "wind":   "The Loom's breath between stitches. Wind carries things — ideas, seeds, ash.",
    "ice":    "Stillness made sharp. The Loom uses ice to press pause on things it wants to keep.",
    "arcane": "Prism's native language. Pure structured intention, given edges and a direction.",
    "shadow": "Not evil — just everything the Loom wove without quite meaning to. Still counts.",
    "light":  "The Loom's own glow, caught and given a heartbeat.",
    "cosmic": "Beyond the chart. Cosmic beings exist outside the elemental hierarchy entirely — they predate it.",
}
