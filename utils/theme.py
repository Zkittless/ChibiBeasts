# ── ChibiBeasts Theme ─────────────────────────────────────────────────────────

# Rarity Colors
COLORS = {
    "common":        0x95A5A6,
    "uncommon":      0x57F287,
    "rare":          0x4A90E2,
    "epic":          0x9B59B6,
    "legendary":     0xFFD700,
    "divine":        0xFF6EC7,
    "altered_divine":0x800080,
    "corrupted":     0x2C0A3F,   # Deep void purple — wrongness made visible
    "ancient":       0xF0C040,   # Aged gold — older than gold itself
    "dev":           0xFF0055,   # Hot red — wrong on purpose
    "error":         0xFF6B6B,
    "success":       0x57F287,
    "info":          0x4A90E2,
    "gold":          0xFFD700,
}

# Rarity Emojis
RARITY_EMOJI = {
    "common":        "⚪",
    "uncommon":      "🟢",
    "rare":          "🔵",
    "epic":          "🟣",
    "legendary":     "🟡",
    "divine":        "🌸",
    "altered_divine":"⚠️",
    "corrupted":     "🖤",
    "ancient":       "🏛️",
    "dev":           "👑",
}

# Rarity Labels
RARITY_LABEL = {
    "common":        "Common",
    "uncommon":      "Uncommon",
    "rare":          "Rare",
    "epic":          "Epic",
    "legendary":     "Legendary",
    "divine":        "Divine ✨",
    "altered_divine":"⚠️ Altered Divine",
    "corrupted":     "🖤 Corrupted",
    "ancient":       "🏛️ Ancient",
    "dev":           "👑 Developer Exclusive",
}

# Type Emojis
TYPE_EMOJI = {
    "fire":   "🔥",
    "water":  "💧",
    "wind":   "🌪️",
    "earth":  "🌍",
    "arcane": "✨",
    "shadow": "🌑",
    "light":  "☀️",
    "ice":    "❄️",
    "cosmic": "🌌",
    "nature": "🌿",
}

SPARKLE = "✦"
BEAST   = "🐾"
SWORD   = "⚔️"
SHIELD  = "🛡️"
STAR    = "⭐"
CROWN   = "👑"

def hp_bar(current: int, maximum: int, length: int = 16) -> str:
    if maximum == 0:
        filled = 0
    else:
        filled = int((current / maximum) * length)
    pct = current / maximum if maximum > 0 else 0
    if pct > 0.5:
        char = "🟩"
    elif pct > 0.25:
        char = "🟨"
    else:
        char = "🟥"
    empty = "⬛"
    return f"{char * filled}{empty * (length - filled)} `{current}/{maximum}`"

def exp_bar(current: int, required: int, length: int = 12) -> str:
    if required == 0:
        filled = length
    else:
        filled = min(int((current / required) * length), length)
    return f"{'🟦' * filled}{'⬛' * (length - filled)} `{current}/{required}`"

def fmt_stats(beast_row: dict) -> str:
    return (
        f"❤️ **HP:** {beast_row['hp']}/{beast_row['max_hp']}\n"
        f"⚔️ **ATK:** {beast_row['attack']}\n"
        f"🛡️ **DEF:** {beast_row['defense']}\n"
        f"💨 **SPD:** {beast_row['speed']}\n"
        f"💠 **MANA:** {beast_row['mana']}/{beast_row['max_mana']}"
    )
