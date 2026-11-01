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
from copy import deepcopy
from uuid import uuid4

import discord
from PIL import Image

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
REALMS['hollowlight'] = 'The Hollowlight Carnival'
TYPE_REALMS = {'fire': 'ember', 'ice': 'glacial', 'wind': 'glacial',
               'water': 'trenches', 'shadow': 'trenches', 'cosmic': 'loom',
               'arcane': 'loom', 'light': 'loom'}
TYPE_COLORS = {'fire': '#ffb16b', 'ice': '#a0e6ff', 'water': '#73d9f3',
               'nature': '#a4ed9d', 'earth': '#e5bd87', 'shadow': '#c5a0ed',
               'cosmic': '#e8c2ff', 'arcane': '#b9a2ff', 'light': '#ffeba8',
               'wind': '#b7f0df'}
ASSETS = PROJECT_ROOT / 'data' / 'battle_realms'
ANIMATION_FPS = 30
AMBIENT_MS = 2000
# Keep mobile uploads bounded while allowing dense 30 FPS ultimates/raids.
MAX_BYTES = 4_000_000
RENDER_TIMEOUT = 6
IO_TIMEOUT = 15
_workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix='battle-art')
_slots = threading.BoundedSemaphore(2)
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
    form: str = ''


def snapshot(states):
    def member(s, party=()):
        return Fighter(
            s.get('beast_id', ''), str(s['name']), s.get('level', 1),
            max(0, int(s['hp'])), max(1, int(s['max_hp'])), max(0, int(s['mana'])),
            max(1, int(s['max_mana'])), s.get('ultimate_charges', 0),
            s.get('status') or '', bool(s.get('guard')), s.get('shield', 0),
            s.get('beast_type', ''), bool(s.get('revive_used')), s.get('armor_id') or '',
            s.get('id', ''), party, max(0, min(100, int(s.get('_assist_meter', 0)))),
            'hexfire' if s.get('beast_id') == 'hexpurr' and s.get('hexfire_turns', 0) > 0 else '',
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
    palette: bytes | None = None
    ambient_end: float = 0


@lru_cache(maxsize=len(REALMS))
def background(realm):
    with Image.open(ASSETS / f'{realm}.png') as source:
        return source.convert('RGBA').resize((WIDTH, 360), Image.Resampling.NEAREST)


@lru_cache(maxsize=len(REALMS) * 2)
def field_background(realm, height):
    return ui.backdrop(background(realm), (WIDTH, height))


@lru_cache(maxsize=256)
def sprite(species, armor_id='', form=''):
    # Canonical IDs from the packaged manifest; never load a user-supplied path.
    if species == 'hexpurr':
        from utils import hexpurr_art
        return hexpurr_art.sprite(form, armor_id)
    if armor_id:
        from utils import armor_art
        from utils import cosmetics
        try:
            if armor_id in armor_art.manifest()['sets'] or cosmetics.cosmetic_id(armor_id):
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


def fighter_sprite(fighter):
    return sprite(fighter.species, fighter.armor_id, fighter.form)


def paint_fighter(image, fighter, pose, *, before=None, transforming=False, t=1, seconds=0, aura=True):
    from utils import move_animations as fx
    if fighter.species == 'hexpurr':
        from utils import hexpurr_art
        return hexpurr_art.paint(image, before or fighter, fighter, pose, t=t,
                                transforming=transforming, seconds=seconds, aura=aura)
    source = fighter_sprite(fighter)
    if source is not None:
        fx.paint_sprite(image, source, pose)


def font(size):
    return ui.font(size, display=True)


def label(draw, xy, text, size=14, fill='#edf0fc', width=None, *, outline=False):
    text = ui.clean(text)
    chosen = size
    if width:
        while chosen > 8 and draw.textlength(text, font=font(chosen)) > width:
            chosen -= 1
    ui.paint_text(draw, xy, text, font(chosen), ui.INK)


def ease(value):
    return 1 - (1 - max(0, min(1, value))) ** 3


def transition(t):
    return ease((t - .48) / .22)


def interpolate(before, after, t):
    return round(before + (after - before) * transition(t))


def frame(before, after, realm, action=None, t=1, journal=(), ambient_seconds=0):
    from utils.battle_scene import draw_frame
    return draw_frame(before, after, realm, action, t, journal, ambient_seconds)


def frame_durations(milliseconds, count):
    """Evenly distribute GIF's 10 ms ticks; no large final-frame remainder."""
    return [round((i+1)*milliseconds/(count*10))*10-round(i*milliseconds/(count*10))*10 for i in range(count)]


def frame_count(milliseconds):
    return max(2, math.ceil(milliseconds * ANIMATION_FPS / 1000))


def render_animation(frame_at, milliseconds, *, loop=False, palette=None, ambient_start=0):
    """One shared GIF encoder for moves and ambient loops, at a fixed cadence.

    Quantize as frames are produced: Pillow retains palette frames for delta
    encoding, so there is no second full clip of RGB/RGBA frames in memory.
    Only ambient scenes loop; moves hold their actual resolved final frame.
    """
    count = frame_count(milliseconds)

    def source(index):
        # A looping clip excludes its duplicate endpoint; a move includes its
        # final HP/MP/retreat state, even if its duration is not divisible by FPS.
        return frame_at(index / (count if loop else count - 1))

    if palette:
        colors = Image.new('P', (1, 1))
        colors.putpalette(palette)
    else:
        first = source(0)
        width, height = first.size
        sample = Image.new('RGB', (width, height * 3))
        sample.paste(first, (0, 0))
        sample.paste(source(count // 2), (0, height))
        sample.paste(source(count - 1), (0, height * 2))
        colors = sample.quantize(colors=256)
        del first
        sample.close()
    # All frames use one palette, including the next ambient scene. GIF delta
    # optimization changes storage, not colors, geometry or playback timing.
    frames = (source(i).quantize(palette=colors, dither=Image.Dither.NONE) for i in range(count))
    first = next(frames)
    output = io.BytesIO()
    first.save(output, 'GIF', save_all=True, append_images=frames,
               duration=frame_durations(milliseconds, count), disposal=1,
               optimize=True, **({'loop': 0} if loop else {}))
    data = output.getvalue()
    if len(data) > MAX_BYTES:
        raise ValueError('Battle animation exceeded its attachment budget')
    return Artwork(data, 'gif', 0 if loop else milliseconds / 1000,
                   bytes(colors.getpalette()), (ambient_start + milliseconds / 1000) % 2)


def render(before, after, realm, action=None, journal=(), ambient_start=0, palette=None):
    output = io.BytesIO()
    if action is None:
        if all(f.hp > 0 for f in after) and any(f.form == 'hexfire' for f in after):
            return render_animation(
                lambda t: frame(after, after, realm, journal=journal,
                                ambient_seconds=ambient_start+t*AMBIENT_MS/1000),
                AMBIENT_MS, loop=True, palette=palette, ambient_start=ambient_start)
        frame(after, after, realm, journal=journal).save(output, 'PNG', optimize=True)
        return Artwork(output.getvalue(), 'png')
    from utils.battle_scene import duration
    milliseconds = duration(before, after, action)
    return render_animation(
        lambda t: frame(before, after, realm, action, t, journal,
                        ambient_start+t*milliseconds/1000),
        milliseconds, ambient_start=ambient_start)


async def render_async(before, after, realm, action=None, *, journal=(), ambient_start=0, palette=None):
    return await render_job(render, before, after, realm, action, journal, ambient_start, palette)


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
        self.ambient_time = 0
        self.palette = None

    async def art(self, before, after, action=None):
        if not self.enabled:
            return None
        if action and action.journal:
            self.journal = action.journal[-2:]
        result = await render_async(before, after, self.realm, action, journal=self.journal,
                                    ambient_start=self.ambient_time, palette=self.palette)
        if result:
            self.palette = result.palette
            if action:
                self.ambient_time = result.ambient_end
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
