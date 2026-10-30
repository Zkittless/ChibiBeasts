"""Pixel effects for immutable snapshots. No combat, database or network calls.

The packaged manifest assigns every move a family, palette and motion. Both
duels and concurrent raid clips use these primitives; no keyword guessing is
performed while a player is waiting for a frame.
"""
from dataclasses import dataclass, replace
from functools import lru_cache
import json
import math

from PIL import Image, ImageDraw, ImageOps

from utils.artwork import PROJECT_ROOT
from utils import divine_effects, kinetic_effects

IMPACT = .48
RETREAT_MS = 1400
BOSS_RETREAT_MS = 1800
FAMILIES = frozenset({'bite', 'headbutt', 'slash', 'dash', 'stomp', 'whip', 'thread',
    'bolt', 'flame', 'splash', 'wave', 'ice', 'wind', 'lightning', 'gaze', 'venom',
    'roots', 'time', 'rift', 'orbit', 'swarm', 'guard', 'heal', 'rewind', 'shelter',
    'drain', 'illusion', 'wind_guard', 'status'})
MOTIFS = frozenset({'spark', 'arrow', 'feather', 'bat', 'fang', 'lance', 'flower', 'nova'})
PALETTES = {
    'fire': ('#c44c48', '#f49252', '#ffe29a'),
    'ice': ('#578cae', '#9bdaef', '#e1faf4'),
    'water': ('#467baa', '#79cce2', '#d2f4ed'),
    'nature': ('#647f55', '#a4d38a', '#def0b2'),
    'earth': ('#755745', '#bf9b65', '#f0d9a1'),
    'shadow': ('#77548e', '#b18dda', '#edd0f2'),
    'cosmic': ('#a777bf', '#dfadf1', '#fff0bd'),
    'arcane': ('#777bc2', '#b9b1f1', '#f2dafa'),
    'light': ('#bb9c68', '#edd795', '#fff7dc'),
    'wind': ('#659d9d', '#afe0ce', '#e1f3e5'),
    'thread': ('#a52c4c', '#ec657a', '#f1cd82'),
    'clock': ('#aa89ca', '#e8bc69', '#fff0c4'),
    'galaxy': ('#ad8bde', '#93d7f0', '#f6bbdf'),
    'wood': ('#755b42', '#b8955f', '#acd18b'),
    'venom': ('#735087', '#b8c56a', '#e7eeb1'),
}


def clamp(value, low=0, high=1):
    return max(low, min(high, value))


def ease(value):
    return 1 - (1 - clamp(value)) ** 3


def smooth(value):
    value = clamp(value)
    return value * value * (3 - 2 * value)


def mix(a, b, value):
    return a + (b - a) * value


def pixel(value):
    return round(value / 2) * 2


@dataclass(frozen=True)
class Animation:
    family: str
    palette: str
    motion: str = 'cast'
    variant: int = 0
    motif: str = 'spark'
    sequence: str = 'legacy'
    accent: int = 0
    ultimate: bool = False


@lru_cache(maxsize=1)
def manifest():
    return json.loads((PROJECT_ROOT / 'data' / 'move-animations.json').read_text(encoding='utf-8'))['beasts']


@lru_cache(maxsize=1024)
def profile(species, name, kind='attack', ultimate=False):
    if kind == 'status':
        return Animation('status', 'shadow')
    name = name.removesuffix(' (bonus)')
    record = manifest().get(species, {}).get(name)
    if record:
        return Animation(**record)
    # Unknown/temporary actions remain readable and playable. Canonical moves
    # never need this fallback; coverage is checked against the full catalog.
    return Animation({'heal': 'heal', 'guard': 'guard', 'tend': 'heal'}.get(kind, 'bolt' if kind == 'skill' or ultimate else 'headbutt'), 'arcane',
                     'bounce' if kind == 'heal' else 'brace' if kind == 'guard' else 'cast')


@dataclass(frozen=True)
class Pose:
    x: float
    feet: float
    size: float = 180
    flip: bool = False
    angle: float = 0
    alpha: float = 1
    height: float = 0


def motion(animation, home, target, t):
    """Full-body movement; aspect ratio stays fixed even through a knockout."""
    if animation.sequence != 'legacy':
        return choreographed_motion(animation, home, target, t)
    forward = smooth((t - .22) / .26) if t < .48 else 1 - smooth((t - .53) / .3)
    dx, dy = target.x - home.x, target.feet - home.feet
    sign = 1 if dx >= 0 else -1
    mode = animation.motion
    if mode in {'bite', 'dash', 'headbutt', 'slash'}:
        travel = (.57 if mode in {'bite', 'dash'} else .46) * forward
        hop = math.sin(clamp((t - .22) / .26) * math.pi) * 25 if t < .48 else 0
        return replace(home, x=home.x + dx * travel - sign * 7 * math.sin(t * math.pi) * (1-forward),
                       feet=home.feet + dy * travel - hop,
                       angle=sign * -6 * math.sin(t * math.pi * 2) * (1 if t < .48 else .3))
    if mode == 'time':
        travel = smooth((t - .32) / .16) if t < .48 else 1 - smooth((t - .66) / .2)
        return replace(home, x=home.x + dx * .56 * travel,
                       feet=home.feet + dy * .7 * travel - math.sin(travel * math.pi) * 14,
                       angle=-sign * 4 * math.sin(travel * math.pi))
    if mode == 'rewind':
        travel = smooth(t / .33) if t < .33 else 1 - smooth((t - .33) / .25)
        return replace(home, x=home.x-sign*45*travel, feet=home.feet-8*math.sin(travel*math.pi))
    if mode == 'bounce':
        return replace(home, feet=home.feet-54*math.sin(clamp((t-.08)/.68)*math.pi))
    if mode == 'stomp':
        return replace(home, feet=home.feet-22*math.sin(clamp((t-.12)/.36)*math.pi))
    if mode in {'hover', 'cast', 'brace'}:
        amount = 21 if mode == 'hover' else -4 if mode == 'brace' else 7
        return replace(home, feet=home.feet-amount*math.sin(clamp(t/.8)*math.pi),
                       angle=-sign*3*math.sin(clamp((t-.08)/.5)*math.pi))
    return home


# These paths deliberately use different windups, silhouettes and recoveries.
# They sample the already-resolved move clock: contact remains at IMPACT, and
# both endpoints return the exact home pose, including armor and aspect ratio.
RUSHES = frozenset({'jaw_snap', 'fang_rend', 'armor_break', 'claw_cross',
    'crescent_cut', 'comet_dash', 'spear_thrust', 'venom_fang', 'ragnarok_fangs'})
TRANSLATIONS = frozenset({'clock_fracture', 'paradox_loop', 'horizon_gate',
    'terminus_severance', 'ouroboros_reset'})
SLAMS = frozenset({'seismic_fault', 'titan_crush', 'meteor_drop'})
LEVITATIONS = frozenset({'event_horizon', 'genesis_bloom', 'genesis_implosion',
    'zodiac_alignment', 'astral_loom', 'judgment_lance', 'abyss_descent',
    'stellar_nursery', 'supernova_detonation', 'nirvana_lotus', 'world_tree',
    'phoenix_sunrise', 'dragon_apocalypse', 'hydra_constellation',
    'sovereign_veil', 'prism_cathedral', 'lunar_procession', 'phantom_court',
    'terminus_severance'})


def choreographed_motion(animation, home, target, t):
    if t <= 0 or t >= 1:
        return home
    seq = animation.sequence
    dx, dy = target.x-home.x, target.feet-home.feet
    sign = 1 if dx >= 0 else -1
    windup = math.sin(clamp(t/.3)*math.pi)
    # A brief settled contact provides hit weight without extending playback
    # or delaying the next action window.
    drive = smooth((t-.26)/.22) if t < IMPACT else 1-smooth((t-.57)/.3)
    envelope = math.sin(clamp(t/.93)*math.pi)
    if seq in RUSHES:
        thrust = seq in {'spear_thrust', 'comet_dash'}
        travel = (.68 if thrust else .59)*drive
        leap = math.sin(clamp((t-.26)/.22)*math.pi)*(15 if thrust else 29) if t < IMPACT else 0
        slash = seq in {'claw_cross', 'crescent_cut', 'fang_rend'}
        return replace(home, x=home.x+dx*travel-sign*15*windup,
                       feet=home.feet+dy*travel-leap-(8*drive if slash else 0),
                       angle=sign*(-10*windup+(9 if slash else 5)*drive))
    if seq in TRANSLATIONS and (seq != 'terminus_severance' or animation.family == 'time'):
        travel = smooth((t-.37)/.11) if t < IMPACT else 1-smooth((t-.68)/.16)
        # Horizon arrives high; time steps freeze near the contact position.
        lift = (27 if seq == 'horizon_gate' else 8)*math.sin(travel*math.pi)
        return replace(home, x=home.x+dx*.64*travel-sign*8*windup,
                       feet=home.feet+dy*.64*travel-lift,
                       angle=-sign*4*envelope)
    if seq in SLAMS:
        height = 56 if seq == 'titan_crush' else 38
        lift = smooth((t-.1)/.18) if t < .28 else 1-smooth((t-.33)/.15)
        recoil = math.sin(clamp((t-IMPACT)/.26)*math.pi)*7
        return replace(home, feet=home.feet-height*lift-recoil,
                       angle=-sign*7*windup+sign*4*recoil/7)
    if seq == 'clock_rewind' or animation.motion == 'rewind':
        loop = smooth((t-.06)/.25) if t < .31 else 1-smooth((t-.5)/.31)
        return replace(home, x=home.x-sign*37*loop,
                       feet=home.feet-12*math.sin(loop*math.pi), angle=sign*5*windup)
    if seq in {'petal_mend', 'seed_bloom', 'nirvana_lotus'} and animation.family in {'heal', 'guard', 'rewind'}:
        return replace(home, feet=home.feet-23*envelope,
                       angle=sign*4*math.sin(t*math.pi*2)*envelope)
    if seq in {'crystal_aegis', 'shell_bastion', 'mirror_decoy', 'feather_ward', 'karmic_mirror'}:
        return replace(home, x=home.x-sign*8*envelope, feet=home.feet-3*envelope,
                       angle=-sign*5*windup)
    if seq in LEVITATIONS:
        rise = smooth((t-.06)/.23)*(1-smooth((t-.72)/.22))
        height = 42 if seq == 'phoenix_sunrise' else 25 if seq in {'genesis_bloom', 'stellar_nursery'} else 17
        return replace(home, feet=home.feet-height*rise,
                       angle=-sign*3*windup+sign*2*math.sin(t*math.pi*2)*rise)
    if seq in {'cyclone', 'razor_gale', 'feather_ward', 'bat_swarm'}:
        return replace(home, feet=home.feet-25*envelope,
                       x=home.x+sign*8*math.sin(t*math.pi*2)*envelope,
                       angle=-sign*8*math.sin(t*math.pi*2)*envelope)
    if seq == 'leviathan_deluge' or seq in {'tidal_crest', 'geyser', 'water_spear'}:
        return replace(home, feet=home.feet-11*envelope,
                       angle=-sign*6*windup+sign*4*drive,
                       x=home.x-sign*9*windup-sign*4*drive)
    # Recoil is tied to the cast, rather than perpetual idle bobbing. Thread
    # pulls have a backward snap; breath casters brace before exhaling.
    pull = seq in {'loom_snap', 'thorn_lash', 'shadow_drain'}
    recoil = math.sin(clamp((t-.44)/.32)*math.pi)
    return replace(home, x=home.x-sign*(11 if pull else 6)*recoil-sign*5*windup,
                   feet=home.feet-5*envelope,
                   angle=-sign*(6 if seq == 'cinder_breath' else 4)*windup+sign*3*recoil)


def reaction(animation, home, attacker, t):
    """Resolved HP loss only; no recoil for guards, heals, swaps or misses."""
    if not IMPACT <= t < .85:
        return home
    p = (t-IMPACT)/.37
    sign = 1 if home.x >= attacker.x else -1
    weight = 1.45 if animation.ultimate else 1
    distance = 19 if animation.sequence in RUSHES | SLAMS else 11
    # A sharp displacement followed by damped recovery reads as an impact,
    # unlike the old symmetric idle sway.
    impulse = math.sin(clamp(p/.24)*math.pi/2)*(1-p)**2
    lift = 7 if animation.sequence in SLAMS else 3
    return replace(home, x=home.x+sign*distance*weight*impulse,
                   feet=home.feet-lift*weight*impulse,
                   angle=sign*8*weight*impulse)


def paint_echoes(image, source, animation, home, target, t):
    """Readable body trails on fast moves, sampled from their actual paths."""
    if source is None or not .28 < t < .77:
        return
    if animation.sequence not in RUSHES | TRANSLATIONS and animation.family not in {'time', 'rewind', 'illusion'}:
        return
    for delay, opacity in ((.11, .09), (.065, .15), (.028, .22)):
        pose = motion(animation, home, target, max(0, t-delay))
        current = motion(animation, home, target, t)
        if abs(pose.x-current.x)+abs(pose.feet-current.feet) > 4:
            paint_sprite(image, source, replace(pose, alpha=opacity))


FLOATING = frozenset({'wisp', 'will_o_wisp', 'fairy', 'pixie', 'singularity', 'astraea', 'chronos',
    'aetherius', 'genesis', 'terminus', 'paradox', 'horizon', 'karma', 'zodiac', 'abyss', 'nebula',
    'supernova', 'nirvana', 'void_chronos', 'fractured_genesis', 'abyssal_nebula', 'ancient_chronos',
    'ancient_genesis', 'ancient_abyss', 'loom_fragment', 'ouroboros'})
FLYING = frozenset({'dragon', 'wyvern', 'phoenix', 'ascended_phoenix', 'corrupted_dragon',
    'harpy', 'griffin', 'pegasus', 'ascended_pegasus', 'rukh', 'simurgh', 'thunderbird', 'strix'})


def retreat(home, progress, species, width=640, *, boss=False):
    progress = clamp(progress)
    travel = smooth((progress-.12)/.88)
    sign = -1 if home.x < width/2 else 1
    floating = species in FLOATING
    if boss and (species in FLYING or floating):
        return replace(home, x=home.x+205*travel, feet=home.feet-420*travel,
                       size=home.size*(1-.18*travel), angle=12*travel, flip=False,
                       alpha=1-smooth((progress-.74)/.26))
    distance = home.x+home.size if sign < 0 else width-home.x+home.size
    bob = math.sin(travel*math.pi*3)*9 if floating or species in FLYING else abs(math.sin(travel*math.pi*4))*12
    return replace(home, x=home.x+sign*distance*travel, feet=home.feet-bob-12*travel,
                   flip=sign < 0 if travel > .03 else home.flip,
                   angle=sign*4*math.sin(travel*math.pi*3))


def paint_sprite(image, source, pose):
    if source is None or pose.alpha <= 0:
        return
    scale = pose.size / max(source.size)
    width, height = max(1, round(source.width*scale)), max(1, round(source.height*scale))
    source = source.resize((width, height), Image.Resampling.NEAREST) if source.size != (width, height) else source
    if pose.flip:
        source = ImageOps.mirror(source)
    if pose.alpha < 1:
        source = source.copy()
        source.putalpha(source.getchannel('A').point(lambda a: round(a*clamp(pose.alpha))))
    if abs(pose.angle) >= 1:
        source = source.rotate(-pose.angle, Image.Resampling.NEAREST, expand=True)
    image.alpha_composite(source, (round(pose.x-source.width/2), round(pose.feet-height/2-source.height/2)))


class Effects:
    """One shared transparent field layer, clipped away from HUD and journal."""
    def __init__(self, size):
        self.image = Image.new('RGBA', size)
        self.draw = ImageDraw.Draw(self.image)

    @staticmethod
    def color(value, alpha):
        return (*tuple(int(value[i:i+2], 16) for i in (1,3,5)), round(clamp(alpha)*255))

    def rect(self, x, y, width, height, color, alpha=1):
        self.draw.rectangle((pixel(x), pixel(y), pixel(x)+max(2,pixel(width)), pixel(y)+max(2,pixel(height))), fill=self.color(color, alpha))

    def line(self, points, color, width=2, alpha=1):
        if len(points)>1:
            self.draw.line([(pixel(x),pixel(y)) for x,y in points], fill=self.color(color,alpha), width=width)

    def polygon(self, points, color, alpha=1):
        self.draw.polygon([(pixel(x),pixel(y)) for x,y in points], fill=self.color(color,alpha))

    def ring(self, x, y, rx, ry, color, alpha=1, turn=0):
        self.line([(x+math.cos(turn+i*math.pi/8)*rx,y+math.sin(turn+i*math.pi/8)*ry) for i in range(17)], color, 2, alpha)

    def star(self, x, y, size, color, alpha=1):
        self.rect(x-size,y-2,size*2+2,4,color,alpha)
        self.rect(x-2,y-size,4,size*2+2,color,alpha)
        self.rect(x-2,y-2,4,4,'#fff1c4',alpha)

    def clock(self, x, y, radius, turn, alpha=1):
        self.ring(x,y,radius,radius,'#e8bc69',alpha)
        self.ring(x,y,radius+5,radius+5,'#aa89ca',alpha*.65)
        for i in range(12):
            angle=i*math.pi/6
            self.line([(x+math.cos(angle)*(radius-6),y+math.sin(angle)*(radius-6)),
                       (x+math.cos(angle)*(radius-1),y+math.sin(angle)*(radius-1))], '#e8bc69',2,alpha)
        angle=turn*math.pi*2-math.pi/2
        self.line([(x+math.cos(angle)*radius*.72,y+math.sin(angle)*radius*.72),(x,y),
                   (x+math.cos(angle*.3)*radius*.44,y+math.sin(angle*.3)*radius*.44)],'#fff0c4',3,alpha)

    def burst(self, x, y, p, colors, count=16, radius=58):
        if not 0<=p<=1:
            return
        for i in range(count):
            angle=i*2.39996
            distance=(10+radius*ease(p))*(.5+(i%4)*.16)
            px,py=x+math.cos(angle)*distance,y+math.sin(angle)*distance+p*p*16
            if i%3==0:
                self.star(px,py,4,colors[i%len(colors)],(1-p)*.85)
            else:
                self.rect(px,py,4,4,colors[i%len(colors)],1-p)

    def departure(self, pose, p, species, *, boss=False):
        if not .12<p<.97:
            return
        for i in range(9 if not boss else 13):
            age=(p*2+i*.08)%1
            sign=-1 if pose.flip else 1
            x=pose.x-sign*(28+age*55)
            y=pose.feet+4-age*18
            if boss and species in FLYING|FLOATING:
                x=pose.x-75+(i*29)%150
                y=pose.feet+12+age*75
            if species in FLOATING:
                self.star(x,y-10,3,'#fff1c4',(1-age)*.55*(1-p))
            else:
                self.rect(x,y,8-age*4,4,'#b4b595',(1-age)*.5*(1-p))

    def effect(self, animation, actor, target, t, *, back=False, scale=1):
        """Each family has its own silhouette, travel path and impact sequence."""
        if animation.sequence != 'legacy':
            module = divine_effects if animation.sequence in divine_effects.SEQUENCES else kinetic_effects
            if module.paint(self, animation, actor, target, t,
                            colors=PALETTES[animation.palette], back=back, scale=scale):
                return
        family, colors = animation.family, PALETTES[animation.palette]
        dark, mid, light = colors
        ax,ay=actor.x,actor.feet-(actor.height or actor.size)*.52
        tx,ty=target.x,target.feet-(target.height or target.size)*.52
        sign=1 if tx>=ax else -1
        start=ax+sign*actor.size*.3
        end=tx-sign*target.size*.15
        fade=1-smooth((t-.82)/.18)
        variant=animation.variant
        motif=animation.motif
        scale *= 1+(variant-1.5)*.035
        if family=='status':
            if not back:
                self.burst(ax,ay,(t-.35)/.5,colors,9,35)
            return
        if family in {'rewind','time'}:
            if back and .04<t<.83:
                alpha=math.sin(clamp((t-.04)/.79)*math.pi)*.7
                self.clock(ax,ay,min(actor.size*.5,85),-t*1.4 if family=='rewind' else min(t,.3)*-.2,alpha)
                if family=='time' and t>.2:
                    self.clock(tx,ty,min(target.size*.48,100),.25,alpha*.55)
            if not back and family=='time':
                if .2<t<.47:
                    for i in range(6):
                        self.rect(mix(ax,tx,(i+1)/7),mix(ay,ty,(i+1)/7),4,8,mid,.65)
                self.burst(tx,ty,(t-.48)/.35,colors,22,68*scale)
                if .48<=t<.83:
                    p=(t-.48)/.35
                    for i in range(12):
                        a=i*math.pi/6-p*.5;r=28+60*ease(p)
                        x,y=tx+math.cos(a)*r,ty+math.sin(a)*r
                        self.line([(x-4*math.sin(a),y+4*math.cos(a)),(x+4*math.sin(a),y-4*math.cos(a))],mid,3,1-p)
            if not back and family=='rewind' and t>=.48:
                self.ring(ax,ay,actor.size*.46,28,mid,.5*fade)
                for i in range(4):
                    a=i*math.pi/2-t*math.pi*2
                    self.star(ax+math.cos(a)*actor.size*.44,ay+math.sin(a)*28,4,light,.8*fade)
            return
        if family=='heal':
            if .12<t<.97:
                p=clamp((t-.12)/.85)
                if back:
                    self.ring(ax,actor.feet-3,actor.size*.46,15,mid,math.sin(p*math.pi)*.8)
                    self.ring(ax,actor.feet-3,actor.size*.34,10,light,math.sin(p*math.pi)*.7)
                else:
                    for i in range(16):
                        a=i*2.39996+p*2.8;r=actor.size*.42*(1-p*.7)
                        x,y=ax+math.cos(a)*r,actor.feet-40+math.sin(a)*r*.3-100*p
                        if animation.palette=='wood':
                            self.polygon([(x,y),(x+7,y-5),(x+13,y),(x+4,y+5)],light,math.sin(p*math.pi))
                        else:
                            self.star(x,y,6 if i%4==0 else 3,colors[i%3],math.sin(p*math.pi))
                    if t>=.48:
                        p1=(t-.48)/.49
                        for i in range(5):
                            x,y=ax-42+i*21,actor.feet-90-50*p1+i%2*22
                            self.line([(x-4,y),(x+4,y)],'#b8e5a1',3,1-p1)
                            self.line([(x,y-4),(x,y+4)],'#b8e5a1',3,1-p1)
            return
        if family in {'guard','shelter','wind_guard','illusion'}:
            if back and family=='illusion' and .1<t<.82:
                self.ring(ax,ay,actor.size*.48,actor.size*.52,mid,.45*math.sin(t*math.pi))
            if back:
                return
            grow=smooth((t-.12)/.36)
            if t>.12:
                if family=='shelter':
                    for i in range(5):
                        a=math.pi*(.13+i*.185)
                        x,y=ax+math.cos(a)*actor.size*.48,actor.feet-math.sin(a)*actor.size*grow
                        self.polygon([(x-9,actor.feet),(x-12,y+10),(x,y),(x+12,y+10),(x+9,actor.feet)],mid,.22*fade)
                        self.line([(x-9,actor.feet),(x-12,y+10),(x,y),(x+12,y+10)],light,2,.7*fade)
                elif family=='wind_guard':
                    for i in range(3):
                        self.ring(ax,ay-30+i*29,actor.size*.46*grow,12,colors[i],fade*.65,t*2+i)
                else:
                    self.ring(ax,ay,actor.size*.48*grow,actor.size*.53*grow,mid,.65*fade,t*.2)
                    for i in range(4):
                        a=i*math.pi/2+t
                        self.star(ax+math.cos(a)*actor.size*.48*grow,ay+math.sin(a)*actor.size*.53*grow,4,light,fade)
            return
        if family=='roots':
            if .16<t<1:
                grow=smooth((t-.16)/.31)
                if back:
                    for lane in range(3):
                        points=[]
                        for i in range(9):
                            f=i/8*grow
                            points.append((mix(ax,end,f),mix(actor.feet,target.feet,f)+math.sin(i*2+lane)*7+lane*5))
                        self.line(points,dark,10,fade);self.line([(x,y-3) for x,y in points],mid,5,fade)
                elif t>.36:
                    p=smooth((t-.36)/.16)
                    for i in range(5):
                        x=tx-60*scale+i*30*scale;h=(87 if i%2 else 58)*p*scale;y=target.feet
                        self.polygon([(x-8,y),(x-8,y-h*.55),(x+8,y-h*.7),(x+2,y-h),(x+14,y-h+12),(x+20,y-h*.52),(x+7,y)],mid,fade)
                        self.line([(x+4,y-5),(x+8,y-h*.55),(x+6,y-h+8)],'#e0c78a',2,fade)
                        if p>.6:self.polygon([(x+8,y-h+12),(x+20,y-h+3),(x+29,y-h+10),(x+17,y-h+19)],light,fade)
                    self.burst(tx,ty,(t-.48)/.4,(mid,light),12,45*scale)
            return
        if family=='ice':
            if back:
                if .15<t<.75:
                    p=smooth((t-.15)/.33);alpha=1-smooth((t-.6)/.15)
                    self.line([(start,actor.feet),(mix(start,tx,p),mix(actor.feet,target.feet,p))],mid,5,alpha*.75)
                return
            if t>=.42:
                p=smooth((t-.42)/.13)
                for i in range(5):
                    x=tx-55*scale+i*27*scale;h=(100+i%2*40)*p*scale;y=target.feet
                    points=[(x-10,y),(x-15,y-h*.7),(x,y-h),(x+15,y-h*.77),(x+13,y)]
                    self.polygon(points,mid,.25*(.6+.4*fade));self.line(points,light,2,.7*(.6+.4*fade))
                self.burst(tx,ty,(t-.48)/.35,colors,13,56*scale)
            return
        if back:
            if family in {'orbit','rift'} and .08<t<.45:
                grow=smooth((t-.08)/.37)
                for i in range(3):self.ring(ax,ay,20+45*grow,8+14*grow,colors[i],.5*grow,t*2+i)
            return
        if family in {'thread','whip'}:
            if .1<t<.51:
                p=clamp((t-.12)/.3);tight=smooth((t-.31)/.17)
                points=[]
                for i in range(25):
                    f=i/24
                    points.append((mix(start,mix(start,end,p),f),mix(ay,ty,f)+math.sin(f*math.pi)*32*(1-tight)+math.sin(f*4*math.pi+t*12)*4*(1-tight)))
                self.line([(x,y+3) for x,y in points],dark,5,.8);self.line(points,mid,3)
                if t>.36:self.ring(end,ty,12+12*(1-tight),20,light,.8)
            if .48<=t<.82:
                p=(t-.48)/.34
                for i in range(7):
                    x=mix(start,end,i/6)+(i-3)*20*p;y=mix(ay,ty,i/6)+(1 if i%2 else -1)*38*p
                    self.line([(x-13,y),(x,y-7),(x+8,y+3)],mid if i%2 else light,2,1-p)
            self.burst(end,ty,(t-.48)/.3,colors,12,42*scale)
        elif family in {'bite','headbutt','slash','dash'}:
            if family=='bite' and .44<t<.66:
                p=(t-.44)/.22;gap=18*(1-math.sin(p*math.pi));x=end
                for i in range(3):
                    px=x-12+i*10
                    self.polygon([(px,ty-gap-13),(px+7,ty-gap-13),(px+4,ty-gap)],light,1-p*.8)
                    self.polygon([(px,ty+gap+13),(px+7,ty+gap+13),(px+4,ty+gap)],mid,1-p*.8)
            elif family=='slash' and .43<t<.7 and motif=='lance':
                p=(t-.43)/.27
                x=mix(ax,tx,smooth(p))
                self.polygon([(x-sign*38,ty-7),(x+sign*27,ty),(x-sign*38,ty+7),(x-sign*26,ty)],mid,1-p*.65)
                self.line([(x-sign*28,ty),(x+sign*22,ty)],light,2,1-p*.6)
            elif family=='slash' and .43<t<.7:
                p=(t-.43)/.27
                for i in range(3):
                    self.line([(tx-35+i*15,ty-45+70*p),(tx-20+i*15,ty-20+70*p),(tx+i*15,ty+10+70*p)],light,3,1-p)
            elif family=='dash' and .23<t<.52:
                for i in range(4):self.line([(ax-sign*(45+i*13),ay-24+i*16),(ax-sign*(15+i*6),ay-24+i*16)],mid,3,.6)
            if family=='bite' and animation.palette=='clock' and .06<t<.3:
                self.clock(ax-sign*30,ay-12,19,t*.8,math.sin((t-.06)/.24*math.pi)*.65)
            self.burst(end,ty,(t-.48)/.3,colors,13,43*scale)
        elif family=='flame':
            if .22<t<.72:
                p=smooth((t-.22)/.25);alpha=1-smooth((t-.56)/.16)
                stop=mix(start,end,p);y_end=mix(ay,ty,p)
                for layer in range(3):
                    spread=(36-layer*10)*p*scale;top=[];bottom=[]
                    for i in range(17):
                        f=i/16;x=mix(start,stop,f);wave=math.sin(i*2+t*42)*6*f
                        top.append((x,mix(ay,y_end,f)-spread*f+wave));bottom.append((x,mix(ay,y_end,f)+spread*f+wave+8))
                    self.polygon(top+list(reversed(bottom)),colors[layer],alpha*(.72 if layer==0 else .95))
                for i in range(16):
                    f=(i/16+t*1.6)%1
                    self.rect(mix(start,stop,f),mix(ay,y_end,f)+math.sin(i*2.4+t*10)*34*f,6+i%3*2,4,light,alpha*.9)
            self.burst(tx,ty,(t-.48)/.42,colors,12,40*scale)
        elif family in {'splash','bolt','venom','swarm','drain'}:
            backwards=family=='drain'
            if .2<t<.5:
                p=clamp((t-.2)/.28)
                amount=6 if family=='swarm' else 4
                for i in reversed(range(amount)):
                    f=clamp(p-i*.065)
                    if backwards:f=1-f
                    x=mix(start,end,f);y=mix(ay,ty,f)-math.sin(f*math.pi)*(70 if family=='splash' else 25)
                    if family=='swarm':y+=math.sin(i*3.4+t*19)*26;x+=math.cos(i)*14
                    if family=='swarm' and motif=='arrow':
                        x=tx-56+i*22+sign*f*15;y=ty-175+f*190
                        self.line([(x-sign*12,y-28),(x,y)],'#c2ac7d',3,.9)
                        self.polygon([(x-sign*6,y-2),(x+sign*5,y+10),(x+sign*6,y-7)],light,.95)
                        self.line([(x-sign*10,y-24),(x-sign*17,y-28)],mid,3,.85)
                        continue
                    if family=='swarm' and motif in {'feather','bat','fang'}:
                        if motif=='bat':
                            self.polygon([(x-17,y+3),(x-13,y-10),(x-5,y-3),(x,y-7),(x+5,y-3),(x+13,y-10),(x+17,y+3),(x+5,y+1),(x,y+8),(x-5,y+1)],mid,.85-i*.08)
                        elif motif=='fang':
                            self.polygon([(x-7,y-12),(x+7,y-12),(x+2,y+10)],light,.85-i*.08)
                        else:
                            self.polygon([(x-16,y-3),(x+7,y-8),(x+17,y),(x+5,y+7)],mid,.85-i*.08)
                            self.line([(x-11,y),(x+11,y)],light,2,.8)
                        continue
                    r=17 if i==0 else 7-i%4
                    self.polygon([(x-r,y-6),(x-r+6,y-r),(x+8,y-r),(x+r,y-7),(x+r,y+8),(x+7,y+r),(x-r+5,y+r)],colors[i%3],.9-i*.1)
                    if i==0:self.star(x+4,y,5,light)
            self.burst(ax if backwards else tx,ay if backwards else ty,(t-.48)/.42,colors,22 if family=='splash' else 14,70*scale if family=='splash' else 48*scale)
            if family=='splash' and .48<=t<.91:self.ring(tx,target.feet-10,22+65*ease((t-.48)/.43),8+9*t,mid,fade*.8)
        elif family in {'wind','wave'}:
            if .18<t<.76:
                for i in range(4):
                    p=clamp((t-.18-i*.06)/.3)
                    if p in (0,1):continue
                    x,y=mix(start,end,p),mix(ay,ty,p);r=(16+35*p)*scale
                    if family=='wind':
                        self.line([(x+sign*math.cos(-math.pi*.43+j*math.pi*.086)*r*.45,y+math.sin(-math.pi*.43+j*math.pi*.086)*r) for j in range(11)],colors[i%3],3,(1-p)*.8+.1)
                    else:
                        y=mix(actor.feet,target.feet,p)
                        self.polygon([(x-22,y),(x-22,y-35),(x-8,y-55),(x+8,y-55),(x+22,y-35),(x+28,y)],mid,(1-p)*.65+.2)
                        self.line([(x-19,y-36),(x-7,y-52),(x+8,y-52),(x+15,y-43)],light,3,.8)
            self.burst(tx,ty,(t-.48)/.42,colors,12,55*scale)
        elif family=='lightning':
            if .26<t<.68:
                alpha=math.sin(clamp((t-.26)/.42)*math.pi)
                path=[(mix(start,end,i/8)+math.sin(i*9+int(t*20))*10,mix(ay,ty,i/8)+(12 if i%2 else -12)) for i in range(9)]
                self.line(path,mid,7,alpha*.7);self.line(path,light,2,alpha)
            self.burst(tx,ty,(t-.48)/.3,colors,16,50*scale)
        elif family=='gaze':
            if .18<t<.57:
                p=smooth((t-.18)/.3)
                self.ring(start,ay,12+8*p,10,light,.8)
                self.line([(start,ay),(mix(start,end,p),mix(ay,ty,p))],mid,2,.65)
                self.ring(end,ty,12+26*p,12+26*p,mid,.6*p)
            self.burst(tx,ty,(t-.48)/.38,colors,12,50*scale)
        elif family=='stomp':
            if .32<t<.85:
                p=smooth((t-.32)/.25)
                self.ring(ax,actor.feet,24+170*p,6+18*p,mid,(1-clamp((t-.48)/.37))*.8)
                for i in range(7):
                    f=clamp((p-i*.07));x=mix(ax,end,f);y=mix(actor.feet,target.feet,f)
                    self.polygon([(x-10,y),(x-6,y-14),(x+5,y-20),(x+12,y)],colors[i%3],fade*.8)
            self.burst(tx,target.feet-18,(t-.48)/.35,colors,18,55*scale)
        elif family in {'orbit','rift'}:
            if .25<t<.78:
                p=smooth((t-.25)/.23);x,y=mix(ax,tx,p),mix(ay,ty,p)
                if family=='rift':
                    for i in range(3):
                        self.line([(x-18+i*12,y-50),(x+6+i*12,y-12),(x-6+i*12,y+12),(x+18+i*12,y+50)],colors[i],4,fade*.8)
                else:
                    for i in range(6):
                        a=i*math.pi/3+t*6;r=24+22*p
                        if motif=='flower':
                            px,py=x+math.cos(a)*r*.5,y+math.sin(a)*r*.35
                            self.polygon([(x,y),(px-6,py-7),(x+math.cos(a)*r, y+math.sin(a)*r*.7),(px+6,py+7)],colors[i%3],fade*.8)
                        else:
                            self.star(x+math.cos(a)*r,y+math.sin(a)*r*.7,5,colors[i%3],fade)
                    self.ring(x,y,16+12*p,10+8*p,light,fade)
                if motif=='nova' and .48<=t<.9:
                    p1=(t-.48)/.42
                    for i in range(3):
                        self.ring(tx,ty,20+(95+i*13)*ease(p1),12+(52+i*8)*ease(p1),colors[i],(1-p1)*.8)
            self.burst(tx,ty,(t-.48)/.4,colors,20,60*scale)
