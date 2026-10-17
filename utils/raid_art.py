"""Shared raid arena: immutable combat snapshots, short concurrent action clips.

Presentation never computes damage, advances combat, or waits in a player's
action callback. All raids and duels share battle_art's two bounded workers.
"""
from dataclasses import dataclass
import asyncio
from functools import lru_cache
import io
import logging
import math
from uuid import uuid4

import discord
from PIL import Image, ImageDraw, ImageOps

from utils import battle_art as art
from utils.artwork import card_artwork_path

WIDTH, HEIGHT, HUD = 640, 464, 132
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


@dataclass(frozen=True)
class Event:
    sequence: int
    uid: int | None
    kind: str
    damage: int = 0
    target: int | None = None


def capture(session, warning, outcome=''):
    """Caller holds the session/state locks; there is no I/O here."""
    members = tuple(Member(uid, session.slots[uid], art.snapshot([session.state(uid)])[0])
                    for uid in session.users)
    return Scene(art.snapshot([session.enemy])[0], members,
                 REALMS.get(session.boss['id'], 'loom'),
                 max(0, min(600, math.ceil(600 - (session.clock() - session.started)))),
                 session.phase, bool(session.pending), warning,
                 session.weaves, session.weave_required(), outcome)


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
        group = result.setdefault(event.uid, {'kinds': set(), 'damage': 0, 'targets': set()})
        group['kinds'].add(event.kind)
        group['damage'] += max(0, event.damage)
        if event.target is not None:
            group['targets'].add(event.target)
    return result


def frame(before, after, events=(), t=1):
    image = Image.new('RGBA', (WIDTH, HUD + HEIGHT), '#171e32')
    image.alpha_composite(stage(after.realm), (0, HUD))
    draw = ImageDraw.Draw(image)
    animated = bool(events)
    impact = art.transition(t) if animated else 1
    hp = round(before.boss.hp + (after.boss.hp - before.boss.hp) * impact)
    draw.rounded_rectangle((8, 5, 632, HUD - 4), radius=8, fill='#222c44')
    art.label(draw, (17, 11), after.boss.name, 18, width=490)
    art.label(draw, (568, 12), f'{after.remaining // 60}:{after.remaining % 60:02}', 16)
    art.label(draw, (17, 36), f'Lv.{after.boss.level} / {art.REALMS[after.realm]} / {len(after.members)} trainers', 12, '#c2cce0', 604)
    art.label(draw, (17, 57), f'{hp:,} / {after.boss.max_hp:,} HP', 14)
    draw.rectangle((17, 79, 622, 86), fill='#101729')
    if hp < before.boss.hp and animated and t < .9:
        draw.rectangle((17, 79, 17 + round(605 * before.boss.hp / after.boss.max_hp), 86), fill='#d6ad78')
    if hp:
        draw.rectangle((17, 79, 17 + round(605 * min(1, hp / after.boss.max_hp)), 86), fill='#8ee0ac')
    for fraction in (.75, .45, .2):
        x = 17 + round(605 * fraction)
        draw.line((x, 79, x, 86), fill='#222c44', width=2)
    warning = (after.warning + f' / Weave {after.weaves}/{after.required}') if after.pending else 'Victory / the weave settles' if after.outcome == 'victory' else 'The party steps back' if after.outcome else 'The weave is steady'
    art.label(draw, (17, 102), warning, 14, '#f2d39a' if after.pending else '#d4dced', 605)
    groups = events_by_member(events)
    old = {member.uid: member for member in before.members}
    boss_hit = any(event.kind == 'boss' for event in events)
    boss_scale = 1 + (.08 * math.sin(t * math.pi) if boss_hit and animated else 0)
    knocked_out = after.boss.hp <= 0 and (not animated or t >= .6 or before.boss.hp <= 0)
    if knocked_out:
        boss_scale = .35 if not animated or before.boss.hp <= 0 else 1 - .65 * art.ease((t - .6) / .4)
    portrait = boss_portrait(after.boss.species)
    if portrait:
        if boss_scale != 1:
            portrait = portrait.resize((max(1, round(portrait.width * boss_scale)), max(1, round(portrait.height * boss_scale))), Image.Resampling.NEAREST)
        if knocked_out:
            portrait = portrait.copy()
            portrait.putalpha(portrait.getchannel('A').point(lambda a: round(a * .4)))
        draw.ellipse((250, HUD + 252, 390, HUD + 267), fill='#101729')
        image.alpha_composite(portrait, (320 - portrait.width // 2, HUD + 259 - portrait.height))
    else:
        draw.ellipse((230, HUD + 90, 410, HUD + 259), fill='#82679e')
    if after.pending:
        draw = ImageDraw.Draw(image)
        draw.ellipse((235, HUD + 242, 405, HUD + 276), outline='#e4c68f', width=3)

    places = placements(len(after.members))
    floating = []
    for index, (member, place) in enumerate(zip(after.members, places)):
        prior = old.get(member.uid, member)
        fighter = member.fighter
        kinds = groups.get(member.uid, {}).get('kinds', set())
        x, feet, scale_x, scale_y, alpha = place.x, place.feet, 1, 1, 1
        if animated and kinds.intersection({'attack', 'ultimate', 'skill'}):
            travel = art.ease((t - .15) / .33) if t < .48 else 1 - art.ease((t - .48) / .3)
            if 'skill' in kinds and not kinds.intersection({'attack', 'ultimate'}):
                travel *= .12
            x += (320 - x) * .48 * travel
            feet -= 92 * travel + math.sin(math.pi * max(0, travel)) * 16
            if t < .22:
                scale_x, scale_y = 1.08, .92
        if 'guard' in kinds and animated and t < .6:
            scale_x, scale_y = 1.07, .91
        changed_slot = prior.slot != member.slot or prior.fighter.species != fighter.species
        species = fighter.species
        if animated and changed_slot:
            if t < .48:
                species = prior.fighter.species
                alpha = 1 - art.ease(t / .48)
                x -= 32 * art.ease(t / .48)
            else:
                alpha = art.ease((t - .48) / .4)
                x += 32 * (1 - art.ease((t - .48) / .4))
        active = fighter if not animated or t >= .48 else prior.fighter
        if active.hp <= 0:
            scale_y *= .35 + .65 * (1 - art.ease((t - .48) / .35)) if animated and prior.fighter.hp > 0 else .35
            alpha *= .45
        elif animated and prior.fighter.hp <= 0 and not changed_slot:
            scale_y *= .35 + .65 * art.ease((t - .48) / .35)
        portrait = fitted(species, place.size)
        if x > 320:
            portrait = ImageOps.mirror(portrait)
        if scale_x != 1 or scale_y != 1:
            portrait = portrait.resize((max(1, round(portrait.width * scale_x)), max(1, round(portrait.height * scale_y))), Image.Resampling.NEAREST)
        if alpha != 1:
            portrait = portrait.copy()
            portrait.putalpha(portrait.getchannel('A').point(lambda a: round(a * alpha)))
        draw = ImageDraw.Draw(image)
        draw.ellipse((place.x - place.size * .31, HUD + place.feet - 3,
                      place.x + place.size * .31, HUD + place.feet + 9), fill='#172132')
        image.alpha_composite(portrait, (round(x - portrait.width / 2), round(HUD + feet - portrait.height)))
        draw = ImageDraw.Draw(image)
        if active.hp > 0 and (active.guard or active.shield):
            draw.ellipse((x - place.size * .49, HUD + feet - place.size * 1.04,
                          x + place.size * .49, HUD + feet + place.size * .1), outline='#d6baff' if active.guard else '#a5d7ef', width=3)
        if not changed_slot:
            delta = fighter.hp - prior.fighter.hp
            if animated and t >= .48 and delta:
                floating.append((place.x, HUD + place.feet - place.size - 18 - round((t - .48) * 30),
                                 f'{delta:+,}', '#b8fac5' if delta > 0 else '#ffe0bd'))
                if delta > 0:
                    for n in range(5):
                        px, py = place.x - 32 + n * 16, HUD + place.feet - 50 - round((t * 60 + n * 13) % 65)
                        draw.line((px - 3, py, px + 3, py), fill='#b8fac5', width=2)
                        draw.line((px, py - 3, px, py + 3), fill='#b8fac5', width=2)
        if animated:
            color = art.TYPE_COLORS.get(fighter.element, '#e4c5ff')
            if kinds.intersection({'attack', 'ultimate', 'skill'}) and .13 < t < .52:
                progress = art.ease((t - .13) / .39)
                px = round(place.x + (320 - place.x) * progress)
                py = HUD + round(place.feet - 70 + (157 - (place.feet - 70)) * progress - math.sin(progress * math.pi) * 24)
                draw.line((px - round((320 - place.x) * .15), py + 14, px, py), fill=color, width=7 if 'ultimate' in kinds else 4)
                draw.regular_polygon((px, py, 9 if 'ultimate' in kinds else 5), 4, fill='#fff3d5')
            if 'ultimate' in kinds and t < .4:
                radius = 26 + t * 65
                draw.ellipse((place.x - radius, HUD + place.feet - 66 - radius,
                              place.x + radius, HUD + place.feet - 66 + radius), outline=color, width=3)
            if 'weave' in kinds and t < .9:
                draw.line((place.x, HUD + place.feet - 50, 320, HUD + 310, 320, HUD + 250), fill='#e8d393', width=3)
            if .48 <= t <= .86 and groups.get(member.uid, {}).get('damage'):
                dx = (index % 3 - 1) * 55
                radius = 18 + (t - .48) * 80
                for n in range(6):
                    angle = n * math.pi / 3
                    draw.line((320 + dx + math.cos(angle) * 8, HUD + 160 + math.sin(angle) * 8,
                               320 + dx + math.cos(angle) * radius, HUD + 160 + math.sin(angle) * radius), fill=color, width=3)

    draw = ImageDraw.Draw(image)
    total_damage = sum(group['damage'] for group in groups.values())
    if animated and .48 <= t <= .86 and total_damage:
        # One combined number stays legible when all ten trainers strike.
        floating.append((320, HUD + 97 - round((t - .48) * 60), f'-{total_damage:,}', '#fff0d0'))
    if animated and boss_hit and .18 < t < 1:
        radius = 30 + art.ease(t) * 280
        center = HUD + 263 + art.ease(t) * 74
        draw.ellipse((320 - radius, center - radius * .4, 320 + radius, center + radius * .4), outline='#efb19a', width=4)
    # Markers are the final arena layer; front companions cannot hide the rear
    # trainers' numbers. Their positions are stable throughout each action.
    for index, box in enumerate(marker_boxes(after)):
        left, top, right, bottom = box
        draw.rectangle((left, HUD + top, right, HUD + bottom), fill='#1a243a')
        text = str(index + 1)
        width = draw.textlength(text, font=art.font(28))
        art.label(draw, ((left + right - width) / 2, HUD + top + 1), text, 28)
    for x, y, text, color in floating:
        width = draw.textlength(text, font=art.font(20))
        art.label(draw, (x - width / 2, y - 10), text, 20, color)
    return image.convert('RGB')


def render(before, after, events=()):
    before = before or after
    stream = io.BytesIO()
    if not events:
        frame(after, after).save(stream, 'PNG', optimize=True)
        return art.Artwork(stream.getvalue(), 'png')
    frames = [frame(before, after, events, i / 17) for i in range(18)]
    sample = Image.new('RGB', (WIDTH, (HUD + HEIGHT) * 3))
    for i, source in enumerate((frames[0], frames[9], frames[-1])):
        sample.paste(source, (0, i * (HUD + HEIGHT)))
    palette = sample.quantize(colors=256)
    frames = [source.quantize(palette=palette, dither=Image.Dither.NONE) for source in frames]
    durations = [60] * 18
    durations[-1] += 1200 - sum(durations)
    frames[0].save(stream, 'GIF', save_all=True, append_images=frames[1:],
                   duration=durations, disposal=1, optimize=False)
    data = stream.getvalue()
    if len(data) > art.MAX_BYTES:
        raise ValueError('Raid clip exceeded its 2 MB attachment budget')
    return art.Artwork(data, 'gif', CLIP_SECONDS)


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

            async def deliver(with_file, retain=False):
                if message:
                    kwargs = {} if retain else {'attachments': [file] if with_file else []}
                    await message.edit(embed=embed, view=view, **kwargs)
                    return message
                return await channel.send(embed=embed, view=view, **({'file': file} if with_file else {}))

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
