"""Shared raid arena: immutable combat snapshots, short concurrent action clips.

Presentation never computes damage, advances combat, or waits in a player's
action callback. All raids and duels share battle_art's two bounded workers.
"""
from dataclasses import dataclass
from copy import deepcopy
import asyncio
from functools import lru_cache
import io
import logging
import math
import re
from uuid import uuid4

import discord
from PIL import Image, ImageDraw

from utils import battle_art as art
from utils import field_journal as ui
from utils.artwork import card_artwork_path

WIDTH, HEIGHT, HUD, JOURNAL = 640, 464, 156, 72
TOTAL_HEIGHT = HUD + HEIGHT + JOURNAL
BOSS_HEALTH = (88, 102, 620, 126)
WARNING = (88, 132, 620, 148)
CLIP_SECONDS = 1.2
MAX_EVENTS = 32
REALMS = {'corrupted_leviathan': 'trenches', 'corrupted_fenrir': 'glacial',
          'corrupted_dragon': 'ember', 'ancient_chronos': 'loom',
          'ancient_genesis': 'loom', 'ancient_abyss': 'loom'}
FLOORS = {'ember': ('#392e32', '#604c4b', '#94715e'),
          'glacial': ('#34576a', '#648999', '#aed6d8'),
          'trenches': ('#244454', '#456b73', '#76adb3'),
          'loom': ('#2d2d4b', '#535374', '#9690b9')}
logger = logging.getLogger('chibibeasts.raid.art')


@dataclass(frozen=True)
class Member:
    uid: int
    slot: int
    fighter: art.Fighter


@dataclass(frozen=True)
class Scene:
    boss: art.Fighter
    members: tuple[Member, ...]
    realm: str
    remaining: int
    phase: int
    pending: bool
    warning: str
    weaves: int
    required: int
    outcome: str = ''
    last_event: str = ''


@dataclass(frozen=True)
class Event:
    sequence: int
    uid: int | None
    kind: str
    damage: int = 0
    target: int | None = None
    move: str = ''
    species: str = ''
    slot: int | None = None


def capture(session, warning, outcome=''):
    """Caller holds the session/state locks; there is no I/O here."""
    members = tuple(Member(uid, session.slots[uid], art.snapshot([session.state(uid)])[0])
                    for uid in session.users)
    positions = {str(uid): str(index + 1) for index, uid in enumerate(session.users)}
    event = re.sub(r'<@!?(\d+)>', lambda match: 'Trainer ' + positions.get(match[1], '?'), session.last_event)
    return Scene(art.snapshot([session.enemy])[0], members,
                 REALMS.get(session.boss['id'], 'loom'),
                 max(0, min(600, math.ceil(600 - (session.clock() - session.started)))),
                 session.phase, bool(session.pending), warning,
                 session.weaves, session.weave_required(), outcome, event)


@dataclass(frozen=True)
class Place:
    x: int
    feet: int
    size: int
    rear: bool = False


def placements(count):
    if not 1 <= count <= 10:
        raise ValueError('A raid arena supports one to ten trainers')
    if count == 1:
        return (Place(320, 420, 122),)
    if count == 2:
        return (Place(210, 415, 122), Place(430, 415, 122))
    if count == 3:
        return (Place(125, 394, 110), Place(320, 430, 122), Place(515, 394, 110))
    if count == 4:
        return (Place(96, 363, 110), Place(240, 420, 122),
                Place(408, 420, 122), Place(548, 363, 110))
    if count <= 6:
        return tuple(Place(round(64 + i * 518 / (count - 1)),
                           round(430 - abs(i - (count - 1) / 2) * 10), 100)
                     for i in range(count))
    rear = {1: (Place(52, 297, 85, True),),
            2: (Place(52, 297, 85, True), Place(590, 297, 85, True)),
            3: (Place(52, 297, 85, True), Place(150, 330, 93, True), Place(590, 297, 85, True)),
            4: (Place(52, 297, 85, True), Place(150, 330, 93, True),
                Place(493, 330, 93, True), Place(590, 297, 85, True))}[count - 6]
    front = tuple(Place(x, y, size) for x, y, size in
                  ((64, 405, 100), (168, 420, 108), (272, 430, 108),
                   (376, 430, 108), (480, 420, 108), (582, 405, 100)))
    return rear + front


@lru_cache(maxsize=4)
def stage(realm):
    source = art.background(realm)
    width = round(source.width * HEIGHT / source.height)
    image = source.resize((width, HEIGHT), Image.Resampling.NEAREST)
    image = image.crop(((width - WIDTH) // 2, 0, (width + WIDTH) // 2, HEIGHT))
    draw = ImageDraw.Draw(image)
    colors = FLOORS[realm]

    def points(cx, cy, rx, ry):
        return [(round((cx + math.cos(i * math.pi / 10) * rx) / 4) * 4,
                 round((cy + math.sin(i * math.pi / 10) * ry) / 4) * 4) for i in range(20)]

    draw.polygon(points(320, 352, 312, 117), fill=colors[0])
    draw.polygon(points(320, 339, 312, 117), fill=colors[1])
    draw.line(points(320, 339, 279, 99) + [points(320, 339, 279, 99)[0]], fill=colors[2], width=1)
    for i in range(8):
        angle = math.pi * i / 4
        draw.line((320 + math.cos(angle) * 57, 339 + math.sin(angle) * 20,
                   320 + math.cos(angle) * 294, 339 + math.sin(angle) * 105), fill=colors[2], width=1)
    return image


@lru_cache(maxsize=6)
def boss_portrait(species):
    try:
        with Image.open(card_artwork_path(species)) as source:
            image = source.convert('RGBA')
            box = image.getchannel('A').point(lambda a: 255 if a > 20 else 0).getbbox()
            if box is None:
                return None
            image = image.crop(box)
            image.thumbnail((282, 221), Image.Resampling.NEAREST)
            return image
    except (KeyError, OSError):
        logger.warning('Missing raid boss portrait: %s', species, exc_info=True)
        return None


def fitted(species, size):
    portrait = art.sprite(species)
    if portrait is None:
        image = Image.new('RGBA', (size, size))
        draw = ImageDraw.Draw(image)
        draw.ellipse((size // 6, 2, size * 5 // 6, size - 2), fill='#b7a8dc')
        art.label(draw, (size // 2 - 5, size // 2 - 8), '?', 18, '#242139')
        return image
    ratio = min(size / portrait.width, size / portrait.height)
    return portrait.resize((max(1, round(portrait.width * ratio)), max(1, round(portrait.height * ratio))), Image.Resampling.NEAREST)


def marker_boxes(scene):
    """Stable marker positions, also exposed for phone-size geometry checks."""
    result = []
    for member, place in zip(scene.members, placements(len(scene.members))):
        height = fitted(member.fighter.species, place.size).height
        center = place.feet - height - 16 if place.rear else place.feet + 16
        result.append((place.x - 19, center - 15, place.x + 19, center + 15))
    return tuple(result)


def events_by_member(events):
    # Bounded aggregate: concurrent player hits share one clip, never a FIFO of
    # individual 1.4-second animations that could lag behind live combat.
    result = {}
    for event in events[-MAX_EVENTS:]:
        if event.uid is None:
            continue
        group = result.setdefault(event.uid, {'kinds': set(), 'damage': 0, 'targets': set(), 'event': event})
        group['kinds'].add(event.kind)
        group['damage'] += max(0, event.damage)
        group['event'] = event
        if event.target is not None:
            group['targets'].add(event.target)
    return result


def frame(before, after, events=(), t=1):
    from utils.raid_scene import draw_frame
    return draw_frame(before, after, events, t)


def render(before, after, events=()):
    before = before or after
    from utils.raid_scene import departure_events, duration
    events = departure_events(before, after, events)
    stream = io.BytesIO()
    if not events:
        frame(after, after).save(stream, 'PNG', optimize=True)
        return art.Artwork(stream.getvalue(), 'png')
    milliseconds = duration(before, after, events)
    count = 36 if milliseconds > CLIP_SECONDS * 1000 else 18
    frames = [frame(before, after, events, i / (count-1)) for i in range(count)]
    sample = Image.new('RGB', (WIDTH, TOTAL_HEIGHT * 3))
    for i, source in enumerate((frames[0], frames[count//2], frames[-1])):
        sample.paste(source, (0, i * TOTAL_HEIGHT))
    palette = sample.quantize(colors=256)
    frames = [source.quantize(palette=palette, dither=Image.Dither.NONE) for source in frames]
    durations = art.frame_durations(milliseconds, count)
    frames[0].save(stream, 'GIF', save_all=True, append_images=frames[1:],
                   duration=durations, disposal=1, optimize=False)
    data = stream.getvalue()
    if len(data) > art.MAX_BYTES:
        raise ValueError('Raid clip exceeded its 2 MB attachment budget')
    return art.Artwork(data, 'gif', milliseconds/1000)


class RaidPresentation:
    def __init__(self, *, sleep=asyncio.sleep):
        self.sleep = sleep
        self.previous = None
        self.sequence = 0
        self.filename = None
        self.failures = 0
        self.enabled = True

    async def prepare(self, scene, events=()):
        if not self.enabled:
            return None
        artwork = await art.render_job(render, self.previous, scene, events)
        self.failures = 0 if artwork else self.failures + 1
        if self.failures >= 3:
            self.enabled = False
        return artwork

    async def publish(self, channel, message, embed, view, *, artwork=None, keep=False):
        """The caller owns HTTP deadlines and serializes edits, never combat."""
        file = buffer = None
        filename = None
        try:
            if artwork:
                filename = f'raid-{uuid4().hex}.{artwork.extension}'
                buffer = io.BytesIO(artwork.data)
                file = discord.File(buffer, filename=filename, description='Shared raid arena: boss, active companions, and numbered trainer positions.')
                embed.set_image(url=f'attachment://{filename}')
            elif keep and self.filename:
                embed.set_image(url=f'attachment://{self.filename}')
            else:
                embed.set_image(url=None)

            visual_embed = deepcopy(embed)
            if artwork:
                # The scene owns HP/time and the journal. Keep native pattern
                # warnings and party resources; they remain useful live controls.
                description = embed.description or ''
                marker = description.find('⚠️')
                visual_embed.description = description[marker:] if marker >= 0 and view is not None else None
                if view is None:
                    visual_embed.clear_fields()
                    visual_embed.set_footer(text=None)

            async def deliver(with_file, retain=False):
                shown = visual_embed if with_file else embed
                if message:
                    kwargs = {} if retain else {'attachments': [file] if with_file else []}
                    await message.edit(embed=shown, view=view, **kwargs)
                    return message
                return await channel.send(embed=shown, view=view, **({'file': file} if with_file else {}))

            try:
                result = await deliver(bool(file), keep and not file)
            except discord.HTTPException as error:
                if not file or isinstance(error, discord.NotFound):
                    raise
                logger.warning('Raid attachment rejected (%s); retrying text panel', error.status)
                self.enabled = False
                filename = None
                embed.set_image(url=None)
                result = await deliver(False)
            if not keep or file:
                self.filename = filename
            return result
        finally:
            if file:
                file.close()
            if buffer:
                buffer.close()

    def accepted(self, scene, sequence):
        self.previous = scene
        self.sequence = sequence
