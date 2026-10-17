"""Local, bounded battle rendering. Combat results remain owned by combat.py.

The renderer receives immutable snapshots, never player objects or database
connections. A busy/failed renderer returns to the text battle without queuing
unlimited image jobs or delaying Discord's interaction acknowledgement.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import io
import logging
import math
import threading
from uuid import uuid4

import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps

from utils.artwork import PROJECT_ROOT, card_artwork_path

WIDTH, HUD, HEIGHT = 640, 88, 360
REALMS = {
    'woods': 'The Whispering Woods', 'ember': 'The Ember Wastes',
    'glacial': 'The Glacial Hollows', 'trenches': 'The Sunken Abyssal Trenches',
    'loom': 'The Celestial Loom',
}
TYPE_REALMS = {'fire': 'ember', 'ice': 'glacial', 'wind': 'glacial',
               'water': 'trenches', 'shadow': 'trenches', 'cosmic': 'loom',
               'arcane': 'loom', 'light': 'loom'}
TYPE_COLORS = {'fire': '#ffb16b', 'ice': '#a0e6ff', 'water': '#73d9f3',
               'nature': '#a4ed9d', 'earth': '#e5bd87', 'shadow': '#c5a0ed',
               'cosmic': '#e8c2ff', 'arcane': '#b9a2ff', 'light': '#ffeba8',
               'wind': '#b7f0df'}
ASSETS = PROJECT_ROOT / 'data' / 'battle_realms'
MAX_BYTES = 2_000_000
RENDER_TIMEOUT = 6
IO_TIMEOUT = 15
_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='battle-art')
_slots = threading.BoundedSemaphore(2)
_fonts = threading.local()
logger = logging.getLogger('chibibeasts.battle.art')


def realm_key(value=None, states=()):
    """Explicit destination/NPC home first; PvP uses the host beast's element."""
    if value:
        normalized = str(value).casefold()
        for key, name in REALMS.items():
            if normalized == key or name.removeprefix('The ').casefold() in normalized:
                return key
    return TYPE_REALMS.get(states[0].get('beast_type'), 'woods') if states else 'woods'


@dataclass(frozen=True)
class Fighter:
    species: str
    name: str
    level: int
    hp: int
    max_hp: int
    mana: int
    max_mana: int
    charges: int
    status: str
    guard: bool
    shield: int
    element: str
    revived: bool


def snapshot(states):
    return tuple(Fighter(
        s.get('beast_id', ''), str(s['name']), s.get('level', 1),
        max(0, int(s['hp'])), max(1, int(s['max_hp'])), max(0, int(s['mana'])),
        max(1, int(s['max_mana'])), s.get('ultimate_charges', 0),
        s.get('status') or '', bool(s.get('guard')), s.get('shield', 0),
        s.get('beast_type', ''), bool(s.get('revive_used')),
    ) for s in states)


@dataclass(frozen=True)
class Action:
    actor: int
    name: str
    kind: str = 'attack'
    damage: int = 0
    ultimate: bool = False

    @property
    def milliseconds(self):
        return 1900 if self.ultimate else 1200 if self.kind in {'guard', 'heal', 'status'} else 1400


@dataclass(frozen=True)
class Artwork:
    data: bytes
    extension: str
    seconds: float = 0


@lru_cache(maxsize=5)
def background(realm):
    with Image.open(ASSETS / f'{realm}.png') as source:
        return source.convert('RGBA').resize((WIDTH, HEIGHT), Image.Resampling.NEAREST)


@lru_cache(maxsize=96)
def sprite(species):
    # Canonical IDs from the packaged manifest; never load a user-supplied path.
    try:
        path = card_artwork_path(species)
    except KeyError:
        return None
    try:
        with Image.open(path) as source:
            image = source.convert('RGBA')
            box = image.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox()
            if not box:
                return None
            image = image.crop(box)
            image.thumbnail((180, 176), Image.Resampling.NEAREST)
            return image
    except OSError:
        logger.warning('Missing battle portrait: %s', species, exc_info=True)
        return None


def font(size):
    # Pillow's bundled font works on Railway/Linux without system fonts.
    # A FreeType face is confined to its rendering thread.
    if not hasattr(_fonts, 'sizes'):
        _fonts.sizes = {}
    if size not in _fonts.sizes:
        _fonts.sizes[size] = ImageFont.load_default(size=size)
    return _fonts.sizes[size]


def label(draw, xy, text, size=14, fill='#edf0fc', width=None):
    text = ''.join(c for c in str(text) if c.isprintable())
    face = font(size)
    if width:
        while text and draw.textlength(text, font=face) > width:
            text = text[:-2] + '~' if len(text) > 1 else ''
    draw.text(xy, text, fill=fill, font=face)


def ease(value):
    return 1 - (1 - max(0, min(1, value))) ** 3


def transition(t):
    return ease((t - .48) / .22)


def interpolate(before, after, t):
    return round(before + (after - before) * transition(t))


def frame(before, after, realm, action=None, t=1):
    """HUD above the scene keeps both silhouettes clear at phone widths."""
    image = Image.new('RGBA', (WIDTH, HEIGHT + HUD), '#171e32')
    image.alpha_composite(background(realm), (0, HUD))
    draw = ImageDraw.Draw(image)
    for i, (old, new) in enumerate(zip(before, after)):
        x = 12 + i * 320
        visible = new if t >= .48 else old
        hp = interpolate(old.hp, new.hp, t)
        mp = interpolate(old.mana, new.mana, t)
        draw.rounded_rectangle((x - 4, 5, x + 300, HUD - 7), radius=8, fill='#222c44')
        label(draw, (x + 4, 10), f'{new.name}  Lv.{new.level}', 16, width=285)
        label(draw, (x + 4, 31), f'{hp:,} / {new.max_hp:,} HP', 13, '#dfe6f6', 220)
        label(draw, (x + 230, 31), f'{visible.charges}/3 ULT', 12, '#d2bcff')
        draw.rectangle((x + 4, 49, x + 288, 56), fill='#101729')
        if old.hp > hp and t < .9:
            draw.rectangle((x + 4, 49, x + 4 + round(284 * old.hp / old.max_hp), 56), fill='#d6ad78')
        if hp:
            draw.rectangle((x + 4, 49, x + 4 + round(284 * min(1, hp / new.max_hp)), 56), fill='#8ee0ac' if hp / new.max_hp > .3 else '#f2ae91')
        draw.rectangle((x + 4, 60, x + 118, 63), fill='#101729')
        if mp:
            draw.rectangle((x + 4, 60, x + 4 + round(114 * min(1, mp / new.max_mana)), 63), fill='#8eb9ee')
        status = 'KO' if not visible.hp else visible.status or ('guarded' if visible.guard else 'shielded' if visible.shield else 'ready')
        label(draw, (x + 4, 67), f'{mp}/{new.max_mana} MP', 12, '#adc3e3')
        label(draw, (x + 140, 65), status.upper(), 12, '#d5c1f9', 148)

    actor = action.actor if action else 0
    hit = .48 <= t <= .78
    color = TYPE_COLORS.get(before[actor].element, '#dac5ff')
    positions = []
    for i, (old, new) in enumerate(zip(before, after)):
        x, feet = (162 if i == 0 else 478), HUD + 307
        direction = 1 if i == 0 else -1
        sx = sy = 1
        if action and i == actor:
            if action.kind in {'attack', 'skill'} or action.ultimate:
                travel = ease((t - .2) / .28) if t < .48 else 1 - ease((t - .48) / .3)
                if action.kind != 'skill' or action.ultimate:
                    x += round(direction * 150 * max(0, travel))
                    feet -= round(math.sin(math.pi * max(0, travel)) * 24)
                if .06 < t < .24:
                    sy, sx = .9, 1.06
            elif action.kind == 'guard' and .1 < t < .8:
                sy, sx = .9, 1.06
            elif action.kind == 'heal':
                feet -= round(max(0, math.sin(t * math.pi)) * 12)
        elif action and hit and (new.hp < old.hp or action.damage):
            x -= round(direction * math.sin((t - .48) / .3 * math.pi) * 16)
        was_knocked_out = old.hp <= 0
        knockout = new.hp <= 0 and (was_knocked_out or not action or t >= .6)
        if knockout:
            sy *= .32 + .68 * (1 - ease((t - .6) / .3)) if action and not was_knocked_out else .32
        art = sprite(new.species)
        if art is None:
            # A missing individual portrait must not break a live battle.
            draw.ellipse((x - 35, feet - 75, x + 35, feet), fill='#b7a8dc', outline='#ede6ff', width=3)
            label(draw, (x - 7, feet - 52), '?', 25, '#242139')
        else:
            art = ImageOps.mirror(art) if i else art
            if sx != 1 or sy != 1:
                art = art.resize((max(1, round(art.width * sx)), max(1, round(art.height * sy))), Image.Resampling.NEAREST)
            if knockout:
                art = art.copy()
                art.putalpha(art.getchannel('A').point(lambda a: round(a * .6)))
            shadow = Image.new('RGBA', image.size)
            ImageDraw.Draw(shadow).ellipse((x - 62, feet - 8, x + 62, feet + 10), fill=(10, 20, 28, 95))
            image.alpha_composite(shadow)
            image.alpha_composite(art, (x - art.width // 2, feet - art.height))
        positions.append((x, feet - 90))
        casting_guard = action and action.kind == 'guard' and i == actor and .15 <= t < .48
        if (new if t >= .48 else old).guard or casting_guard:
            overlay = Image.new('RGBA', image.size)
            shield_draw = ImageDraw.Draw(overlay)
            grow = .25 + .75 * ease((t - .15) / .33) if casting_guard else 1
            shield_draw.rounded_rectangle((x - round(91 * grow), feet - 91 - round(96 * grow),
                                           x + round(91 * grow), feet - 91 + round(96 * grow)),
                                          radius=round(52 * grow), fill=(165, 147, 247, 28),
                                          outline=(202, 185, 255, 185), width=3)
            image.alpha_composite(overlay)
    draw = ImageDraw.Draw(image)
    if action:
        ax, ay = positions[actor]
        dx, dy = positions[1 - actor]
        if action.ultimate and .05 < t < .48:
            radius = round(24 + 75 * t)
            draw.ellipse((ax - radius, ay - radius, ax + radius, ay + radius), outline=color, width=3)
        if (action.kind == 'skill' or action.ultimate) and .22 < t < .55:
            p = ease((t - .22) / .3)
            px, py = round(ax + (dx - ax) * p), round(ay - 35 * math.sin(math.pi * p))
            draw.line((px - (26 if actor == 0 else -26), py, px, py), fill=color, width=7 if action.ultimate else 4)
            draw.regular_polygon((px, py, 11 if action.ultimate else 7), 4, fill='#fff4d4')
        if hit and (action.damage or after[1 - actor].hp < before[1 - actor].hp):
            radius = round(18 + (t - .48) * 95)
            for ray in range(8):
                angle = ray * math.pi / 4
                draw.line((dx + math.cos(angle) * 9, dy + math.sin(angle) * 9,
                           dx + math.cos(angle) * radius, dy + math.sin(angle) * radius), fill=color, width=4)
            draw.rectangle((dx - 4, dy - 4, dx + 4, dy + 4), fill='#fff4d4')
        if t >= .48:
            for i, (old, new) in enumerate(zip(before, after)):
                x, y = positions[i]
                delta = new.hp - old.hp
                if delta:
                    label(draw, (x - 30, y - 75 - round((t - .48) * 50)), f'{delta:+,}', 23, '#b5fac5' if delta > 0 else '#fff0ce')
                if new.revived and not old.revived:
                    label(draw, (x - 40, y - 105), 'REVIVED', 14, '#ffdf9e')
                if delta > 0 or (action.kind == 'heal' and i == actor):
                    for n in range(7):
                        px = x - 58 + n * 18
                        py = y + 55 - round(((t * 90 + n * 17) % 90))
                        draw.line((px - 3, py, px + 3, py), fill='#b5fac5', width=2)
                        draw.line((px, py - 3, px, py + 3), fill='#b5fac5', width=2)
            if action.kind in {'attack', 'skill'} and not action.damage and after[1 - actor].hp == before[1 - actor].hp:
                blocked = before[1 - actor].shield > after[1 - actor].shield
                label(draw, (dx - 33, dy - 80), 'BLOCKED' if blocked else '0 DAMAGE', 16, '#e0d5f9')
        # The move name is a quiet caption in the empty upper battlefield.
        caption = action.name + (' / ULTIMATE' if action.ultimate else '')
        caption_width = min(570, round(draw.textlength(caption, font=font(15))))
        x = (WIDTH - caption_width) // 2
        draw.rounded_rectangle((x - 10, HUD + 12, x + caption_width + 10, HUD + 38), radius=7, fill='#192135')
        label(draw, (x, HUD + 16), caption, 15, '#e8ddfa', 570)
    return image.convert('RGB')


def render(before, after, realm, action=None):
    output = io.BytesIO()
    if action is None:
        frame(after, after, realm).save(output, 'PNG', optimize=True)
        return Artwork(output.getvalue(), 'png')
    count = 30 if action.ultimate else 22
    frames = [frame(before, after, realm, action, i / (count - 1)) for i in range(count)]
    # One shared palette prevents background shimmer between GIF frames.
    sample = Image.new('RGB', (WIDTH, (HEIGHT + HUD) * 3))
    for i, source in enumerate((frames[0], frames[count // 2], frames[-1])):
        sample.paste(source, (0, i * (HEIGHT + HUD)))
    palette = sample.quantize(colors=256)
    frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    durations = [action.milliseconds // (count * 10) * 10] * count
    durations[-1] += action.milliseconds - sum(durations)
    # No loop extension: play once and hold the real final state. Disposal=1
    # permits delta compression; all frames were composed on an opaque scene.
    frames[0].save(output, 'GIF', save_all=True, append_images=frames[1:],
                   duration=durations, disposal=1, optimize=False)
    data = output.getvalue()
    if len(data) > MAX_BYTES:
        raise ValueError('Battle animation exceeded its attachment budget')
    return Artwork(data, 'gif', action.milliseconds / 1000)


async def render_async(before, after, realm, action=None):
    return await render_job(render, before, after, realm, action)


async def render_job(renderer, *args):
    """Share one bounded worker budget across duel and cooperative raid art."""
    # Acquire BEFORE submit. Canceled/timed-out renders retain their worker slot
    # until their actual thread finishes, preventing an unbounded executor queue.
    if not _slots.acquire(blocking=False):
        return None

    def work():
        try:
            return renderer(*args)
        finally:
            _slots.release()

    try:
        future = _workers.submit(work)
    except BaseException:
        _slots.release()
        raise
    wrapped = asyncio.wrap_future(future)
    # Retrieve late exceptions even after the caller's timeout/cancellation.
    wrapped.add_done_callback(lambda f: None if f.cancelled() else f.exception())
    try:
        async with asyncio.timeout(RENDER_TIMEOUT):
            return await asyncio.shield(wrapped)
    except Exception:
        logger.warning('Battle artwork unavailable; using text controls', exc_info=True)
        return None


class BattlePresentation:
    """Attachment lifecycle and playback pacing for one shared duel panel."""
    def __init__(self, realm, *, sleep=asyncio.sleep):
        self.realm = realm
        self.sleep = sleep
        self.enabled = True

    async def art(self, before, after, action=None):
        if not self.enabled:
            return None
        result = await render_async(before, after, self.realm, action)
        if result is None:
            # Do not retry a busy/failed renderer on every move of this match.
            self.enabled = False
        return result

    async def publish(self, interaction, message, embed, view, before, after, action=None):
        artwork = await self.art(before, after, action)
        footer = 'Playing action' if action else 'Battle complete' if view and view._closed else 'Choose a move / 45 seconds'
        embed.set_footer(text=REALMS[self.realm] + ' / ' + footer)
        file = None
        buffer = None
        try:
            if artwork:
                filename = f'battle-{uuid4().hex}.{artwork.extension}'
                embed.set_image(url=f'attachment://{filename}')
                buffer = io.BytesIO(artwork.data)
                file = discord.File(buffer, filename=filename,
                                    description=f'{REALMS[self.realm]}: {before[0].name} versus {before[1].name}'[:1024])
            async def deliver(with_art):
                async with asyncio.timeout(IO_TIMEOUT):
                    if message:
                        await message.edit(embed=embed, view=view, attachments=[file] if with_art else [])
                        return message
                    return await interaction.channel.send(embed=embed, view=view, **({'file': file} if with_art else {}))

            try:
                message = await deliver(bool(file))
            except discord.HTTPException as error:
                if not file or isinstance(error, discord.NotFound):
                    raise
                # Missing Attach Files permission, upload rejection, or a CDN
                # failure should still leave a playable text prompt.
                self.enabled = False
                artwork = None
                embed.set_image(url=None)
                logger.warning('Battle attachment rejected (%s); retrying text panel', error.status)
                message = await deliver(False)
        finally:
            if file:
                file.close()
            if buffer:
                buffer.close()
        if view is not None:
            # The first actor may be AI. Expose the uploaded panel before
            # sleeping so cancellation can still remove its visible controls.
            view.message = message
        if artwork and action:
            # Start pacing AFTER Discord accepts the upload. The next move's
            # 45-second decision window is opened by the caller after playback.
            await self.sleep(artwork.seconds + .2)
        return message
