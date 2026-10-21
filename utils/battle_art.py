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
import threading
from copy import deepcopy
from uuid import uuid4

import discord
from PIL import Image, ImageFont

from utils.artwork import PROJECT_ROOT, card_artwork_path
from utils import field_journal as ui
from utils.habitats import BIOMES, DESTINATION_ALIASES

WIDTH, HUD, HEIGHT, JOURNAL = 640, 208, 248, 72
TOTAL_HEIGHT = HUD + HEIGHT + JOURNAL
def _plain_name(value):
    """Native Discord names may start with an emoji unsupported by bitmap fonts."""
    name = str(value).strip()
    return name[next((i for i, char in enumerate(name) if char.isalnum()), len(name)):]


REALMS = {key: _plain_name(region['name']) for key, region in BIOMES.items()}
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
        normalized = ' '.join(_plain_name(value).casefold().split())
        aliases = {_plain_name(alias): key for alias, key in DESTINATION_ALIASES.items()}
        for key, name in REALMS.items():
            label = name.casefold()
            for alias in (key, label, label.removeprefix('the ')):
                aliases[alias] = key
        # Saved IDs and old labels still identify the same destination after a
        # rename. Resolve every exact alias before considering a family name
        # contained inside a legacy "Whispering Woods · Depths" label.
        aliases.update({alias.replace(' · ', ' '): key for alias, key in list(aliases.items())
                        if alias.endswith(' · depths')})
        if normalized in aliases:
            return aliases[normalized]
        for alias in sorted(aliases, key=len, reverse=True):
            if alias not in REALMS and alias in normalized:
                return aliases[alias]
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
    armor_id: str = ''
    member_id: int | str = ''
    party: tuple['Fighter', ...] = ()
    assist_meter: int = 0


def snapshot(states):
    def member(s, party=()):
        return Fighter(
            s.get('beast_id', ''), str(s['name']), s.get('level', 1),
            max(0, int(s['hp'])), max(1, int(s['max_hp'])), max(0, int(s['mana'])),
            max(1, int(s['max_mana'])), s.get('ultimate_charges', 0),
            s.get('status') or '', bool(s.get('guard')), s.get('shield', 0),
            s.get('beast_type', ''), bool(s.get('revive_used')), s.get('armor_id') or '',
            s.get('id', ''), party, max(0, min(100, int(s.get('_assist_meter', 0)))),
        )

    # Party metadata is read once, without following references back to live
    # states. Frozen snapshots can outlive swaps and never mutate combat.
    return tuple(member(s, tuple(member(p) for p in s.get('_party_states', ())[:3])) for s in states)


@dataclass(frozen=True)
class Action:
    actor: int
    name: str
    kind: str = 'attack'
    damage: int = 0
    ultimate: bool = False
    journal: tuple[str, ...] = ()
    incoming_id: int | str = ''
    outgoing_id: int | str = ''
    support_id: int | str = ''
    support_name: str = ''
    support_species: str = ''
    support_armor_id: str = ''
    support_effect: str = ''

    @property
    def milliseconds(self):
        if self.ultimate:
            return 2650
        return {'status': 1100, 'guard': 2100, 'heal': 2100, 'skill': 1950,
                'swap': 1900, 'assist': 2400, 'enter': 1200}.get(self.kind, 1650)


@dataclass(frozen=True)
class Artwork:
    data: bytes
    extension: str
    seconds: float = 0


@lru_cache(maxsize=len(REALMS))
def background(realm):
    with Image.open(ASSETS / f'{realm}.png') as source:
        return source.convert('RGBA').resize((WIDTH, 360), Image.Resampling.NEAREST)


@lru_cache(maxsize=len(REALMS) * 2)
def field_background(realm, height):
    return ui.backdrop(background(realm), (WIDTH, height))


@lru_cache(maxsize=256)
def sprite(species, armor_id=''):
    # Canonical IDs from the packaged manifest; never load a user-supplied path.
    if armor_id:
        from utils import armor_art
        try:
            if armor_id in armor_art.manifest()['sets']:
                image = armor_art.portrait(species, armor_id)
                if image is not None:
                    image.thumbnail((180, 176), Image.Resampling.NEAREST)
                return image
        except (OSError, KeyError, ValueError):
            logger.warning('Unable to load armor catalog', exc_info=True)
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


def label(draw, xy, text, size=14, fill='#edf0fc', width=None, *, outline=False):
    text = ''.join(c for c in str(text) if c.isprintable())
    face = font(size)
    if width:
        while text and draw.textlength(text, font=face) > width:
            text = text[:-2] + '~' if len(text) > 1 else ''
    draw.text(xy, text, fill=fill, font=face,
              stroke_width=1 if outline else 0, stroke_fill='#071c24')


def ease(value):
    return 1 - (1 - max(0, min(1, value))) ** 3


def transition(t):
    return ease((t - .48) / .22)


def interpolate(before, after, t):
    return round(before + (after - before) * transition(t))


def frame(before, after, realm, action=None, t=1, journal=()):
    from utils.battle_scene import draw_frame
    return draw_frame(before, after, realm, action, t, journal)


def frame_durations(milliseconds, count):
    """Evenly distribute GIF's 10 ms ticks; no large final-frame remainder."""
    return [round((i+1)*milliseconds/(count*10))*10-round(i*milliseconds/(count*10))*10 for i in range(count)]


def render(before, after, realm, action=None, journal=()):
    output = io.BytesIO()
    if action is None:
        frame(after, after, realm, journal=journal).save(output, 'PNG', optimize=True)
        return Artwork(output.getvalue(), 'png')
    from utils.battle_scene import duration
    milliseconds = duration(before, after, action)
    count = (30 if action.ultimate else 24) + (14 if milliseconds > action.milliseconds else 0)
    frames = [frame(before, after, realm, action, i / (count - 1), journal) for i in range(count)]
    # One shared palette prevents background shimmer between GIF frames.
    sample = Image.new('RGB', (WIDTH, TOTAL_HEIGHT * 3))
    for i, source in enumerate((frames[0], frames[count // 2], frames[-1])):
        sample.paste(source, (0, i * TOTAL_HEIGHT))
    palette = sample.quantize(colors=256)
    frames = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    durations = frame_durations(milliseconds, count)
    # No loop extension: play once and hold the real final state. Disposal=1
    # permits delta compression; all frames were composed on an opaque scene.
    frames[0].save(output, 'GIF', save_all=True, append_images=frames[1:],
                   duration=durations, disposal=1, optimize=False)
    data = output.getvalue()
    if len(data) > MAX_BYTES:
        raise ValueError('Battle animation exceeded its attachment budget')
    return Artwork(data, 'gif', milliseconds / 1000)


async def render_async(before, after, realm, action=None, *, journal=()):
    return await render_job(render, before, after, realm, action, journal)


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
        self.journal = ()

    async def art(self, before, after, action=None):
        if not self.enabled:
            return None
        if action and action.journal:
            self.journal = action.journal[-2:]
        result = await render_async(before, after, self.realm, action, journal=self.journal)
        if result is None:
            # Do not retry a busy/failed renderer on every move of this match.
            self.enabled = False
        return result

    async def publish(self, interaction, message, embed, view, before, after, action=None):
        artwork = await self.art(before, after, action)
        footer = 'Playing action' if action else 'Battle complete' if view and view._closed else 'Choose a move / 45 seconds'
        embed.set_footer(text=footer)
        file = None
        buffer = None
        try:
            if artwork:
                filename = f'battle-{uuid4().hex}.{artwork.extension}'
                embed.set_image(url=f'attachment://{filename}')
                buffer = io.BytesIO(artwork.data)
                file = discord.File(buffer, filename=filename,
                                    description=f'{REALMS[self.realm]}: {before[0].name} versus {before[1].name}'[:1024])
            visual_embed = deepcopy(embed)
            if artwork:
                for index in reversed(range(len(visual_embed.fields))):
                    if visual_embed.fields[index].name == 'Last actions':
                        visual_embed.remove_field(index)
                if action or view and view._closed:
                    visual_embed.description = None
            async def deliver(with_art):
                shown = visual_embed if with_art else embed
                async with asyncio.timeout(IO_TIMEOUT):
                    if message:
                        await message.edit(embed=shown, view=view, attachments=[file] if with_art else [])
                        return message
                    return await interaction.channel.send(embed=shown, view=view, **({'file': file} if with_art else {}))

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
