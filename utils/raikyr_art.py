"""Raikyr's approved Heavenfall choreography and persistent storm aura.

Presentation only: all HP, mana, charges and damage come from resolved combat.
"""
from dataclasses import replace
from functools import lru_cache
import math
from PIL import Image, ImageDraw, ImageFilter
from utils import move_animations as fx

SEQUENCE = 'raikyr_heavenfall'
TAU = math.tau
LOOP = 2.0

def unit(t, start, end):
    return fx.clamp((t-start)/(end-start))


def ramp(t, start, end):
    return fx.smooth(unit(t, start, end))


class Ink:
    """Thin hot cores and broad translucent blue glows, with an open center."""
    def __init__(self, size, scale=1):
        self.scale = scale
        self.glow = Image.new('RGBA', size)
        self.core = Image.new('RGBA', size)
        self.g = ImageDraw.Draw(self.glow)
        self.d = ImageDraw.Draw(self.core)

    def line(self, points, width=2, alpha=1, *, hot=True):
        if alpha <= 0 or len(points) < 2:
            return
        points = [(round(x*self.scale), round(y*self.scale)) for x, y in points]
        width = max(1, round(width*self.scale))
        self.g.line(points, fill=(19, 73, 255, round(105*fx.clamp(alpha))), width=max(3, width*5), joint='curve')
        self.d.line(points, fill=(38, 117, 255, round(200*fx.clamp(alpha))), width=width+2, joint='curve')
        self.d.line(points, fill=(103, 207, 255, round(255*fx.clamp(alpha))), width=width, joint='curve')
        if hot:
            self.d.line(points, fill=(226, 251, 255, round(245*fx.clamp(alpha))), width=max(1, width//2), joint='curve')

    @staticmethod
    def overlay(image, layer, blur=0):
        """Blur only visible ink, with transparent padding for the full kernel."""
        box = layer.getbbox()
        if box is None:
            return
        pad = math.ceil(blur*4)
        box = (max(0,box[0]-pad),max(0,box[1]-pad),
               min(layer.width,box[2]+pad),min(layer.height,box[3]+pad))
        cutout = layer.crop(box)
        if blur:
            cutout = cutout.filter(ImageFilter.GaussianBlur(blur))
        image.alpha_composite(cutout,box[:2])

    def composite(self, image):
        self.overlay(image,self.glow,2.2*self.scale)
        self.overlay(image,self.core)


def bolt(start, end, phase, seed=0, bend=12, segments=13):
    ax, ay = start
    bx, by = end
    dx, dy = bx-ax, by-ay
    length = max(1, math.hypot(dx, dy))
    nx, ny = -dy/length, dx/length
    result = []
    for n in range(segments+1):
        p = n/segments
        jitter = (math.sin(n*2.67+seed*1.83+math.sin(phase)*.7)
                  + .5*math.sin(n*5.17+seed+math.cos(phase*2)*.4))
        shift = jitter*bend*math.sin(math.pi*p)**.55
        result.append((fx.mix(ax,bx,p)+nx*shift, fx.mix(ay,by,p)+ny*shift))
    return result


def arc(x, y, rx, ry, a, length, count=28, wave=0, phase=0):
    points = []
    for i in range(count):
        p = i/(count-1)
        angle = a+p*length
        radius = 1+wave*math.sin(p*math.pi*5+phase)
        points.append((x+math.cos(angle)*rx*radius, y+math.sin(angle)*ry*radius))
    return points


def _draw_aura(image, pose, seconds, strength=1, *, front=False):
    if strength <= 0 or pose.alpha <= 0:
        return
    phase = seconds/LOOP*TAU
    scale = pose.size/180
    x, feet = pose.x, pose.feet
    ink = Ink(image.size)
    amount = min(1, strength)*pose.alpha
    # Helical storm currents cross behind the wings and in front of the feet.
    for band in range(3):
        cy = feet-(18+band*62)*scale
        rx, ry = (91-band*6)*scale, (16+band*5)*scale
        start = phase*(1 if band != 1 else -1)+band*2.1
        points = arc(x, cy, rx, ry, start, math.pi*1.18, wave=.05, phase=phase*2)
        for a, b in zip(points, points[1:]):
            nearer = (a[1]+b[1])/2 >= cy
            if nearer == front:
                # Keep the face and chest clear: front currents stay low/outer.
                opacity = .66 if band == 0 else .26 if front else .55
                ink.line([a,b], 1 if front else 2, opacity*amount, hot=band == 0)
    # Twin rising thunder channels frame the silhouette rather than filling it.
    if not front:
        # Transparent, tapered wind ribbons give the storm volume behind the body.
        for side in (-1, 1):
            for band in range(2):
                edge, inner = [], []
                for j in range(24):
                    z = j/23
                    center = x+side*(66+12*math.sin(z*6-phase+band))*scale
                    y = feet-(4+z*190)*scale
                    width = (7+band*3)*math.sin(z*math.pi)**.7*scale
                    edge.append((center+side*width,y))
                    inner.append((center-side*width*.4,y+4*scale))
                ink.g.polygon(edge+inner[::-1],fill=(25,92,235,round(52*amount)))
                ink.line(edge,1,.44*amount,hot=False)
        for side in (-1, 1):
            for strand in range(2):
                points = []
                for j in range(26):
                    z = j/25
                    px = x+side*(73+11*math.sin(z*5-phase+strand))*scale
                    px += math.sin(z*17+phase*2+strand*2)*8*scale
                    py = feet-(10+z*183)*scale
                    points.append((px,py))
                pulse = .48+.22*math.sin(phase*2+side+strand)**2
                ink.line(points, 2 if strand == 0 else 1, pulse*amount)
                tip = points[17]
                ink.line(bolt(tip,(tip[0]+side*24*scale,tip[1]-24*scale),phase,side+strand,6*scale,6),1,.65*amount)
        # A loose storm crown rides above the horns, open on both sides.
        ink.line(arc(x,feet-179*scale,74*scale,18*scale,-phase,.95*math.pi,wave=.1,phase=phase),2,.6*amount)
    # Tiny charged fragments orbit at different depths.
    for i in range(17):
        a = phase+(i*2.39996)
        near = math.sin(a)>0
        if near != front:
            continue
        px = x+math.cos(a)*(77+(i%3)*7)*scale
        py = feet-(13+(i%7)*25+math.sin(a*2)*6)*scale
        alpha = (.35+.5*(.5+.5*math.cos(a+i)))*amount
        ink.line([(px-3*scale,py+3*scale),(px,py),(px+3*scale,py)],1,alpha)
    ink.composite(image)


@lru_cache(maxsize=120)
def _aura_frame(index, front):
    """Two bounded 60-frame layers shared by outfits, battles and raid members."""
    image = Image.new('RGBA',(224,224))
    _draw_aura(image,fx.Pose(112,210),index/30,front=front)
    box = image.getbbox()
    return image.crop(box),box


def paint_aura(image, pose, seconds, *, front=False):
    if pose.alpha <= 0:
        return
    index = round((seconds % LOOP)*30) % 60
    source,box = _aura_frame(index,front)
    scale = pose.size/180
    if scale != 1:
        source = source.resize((max(1,round(source.width*scale)),max(1,round(source.height*scale))),Image.Resampling.NEAREST)
    if pose.alpha < 1:
        source = source.copy()
        source.putalpha(source.getchannel('A').point(lambda value: round(value*fx.clamp(pose.alpha))))
    image.alpha_composite(source,(round(pose.x+(box[0]-112)*scale),round(pose.feet+(box[1]-210)*scale)))


def paint(image, fighter, pose, *, seconds=0, aura=True):
    from utils.battle_art import fighter_sprite
    active = aura and fighter.hp > 0
    if active:
        paint_aura(image,pose,seconds)
    fx.paint_sprite(image,fighter_sprite(fighter),pose)
    if active:
        paint_aura(image,pose,seconds,front=True)


def motion(home, target, t):
    scale = home.size/180
    direction = 1 if target.x >= home.x else -1
    if t < .20:
        return replace(home,feet=home.feet+4*scale*math.sin(unit(t,0,.20)*math.pi),angle=-2*direction*math.sin(t/.20*math.pi))
    if t < .34:
        p = ramp(t,.20,.34)
        return replace(home,x=home.x+direction*18*scale*p,feet=home.feet-180*scale*p,angle=-15*direction*p,alpha=1-ramp(t,.25,.33))
    if t < .72:
        return replace(home,alpha=0)
    if t < .88:
        p = ramp(t,.72,.88)
        return replace(home,feet=home.feet-85*scale*(1-p),alpha=ramp(t,.72,.80),angle=-4*direction*(1-p))
    p = unit(t,.88,1)
    return replace(home,feet=home.feet+4*scale*math.sin(p*math.pi)*(1-p))


def reaction(home, actor, t):
    if t < .48 or t > .86:
        return home
    p = unit(t,.48,.86)
    kick = math.sin(p*math.pi)*(1-p)
    scale = home.size/180
    direction = 1 if home.x >= actor.x else -1
    return replace(home,x=home.x+direction*scale*(24*kick+math.sin(p*45)*3*(1-p)),
                   feet=home.feet-12*scale*math.sin(p*math.pi),angle=direction*8*kick)


def effect(layer, actor, target, t, *, back=False, scale=1, home=None):
    home = home or actor
    # Normalize geometry once; ink maps directly back to the actual field.
    # This also supports the smaller actor sizes and tall fields used by raids.
    scale = home.size/180
    home = replace(home,x=home.x/scale,feet=home.feet/scale)
    target = replace(target,x=target.x/scale,feet=target.feet/scale)
    hx, tx, feet = home.x, target.x, target.feet
    ink = Ink(layer.image.size,scale)
    phase = t*TAU*3
    if back:
        dark = ramp(t,.04,.30)*(1-ramp(t,.67,.96))
        shade = Image.new('RGBA',layer.image.size,(2,5,23,round(dark*190)))
        layer.image.alpha_composite(shade)
        # Inward-moving current gathers at the caster before launch.
        if t < .26:
            p = unit(t,0,.26)
            for i in range(14):
                angle = i*2.39996+p*.7
                r = 30+100*(1-p)
                x,y = hx+math.cos(angle)*r,home.feet-85+math.sin(angle)*r*.65
                ink.line([(x,y),(fx.mix(x,hx,.14),fx.mix(y,home.feet-85,.14))],2,p*.8)
        # A rotating storm eye opens above the enemy.
        if .30 < t < .77:
            p = ramp(t,.30,.44)*(1-ramp(t,.60,.77))
            # Cloud bands twist into the open eye; the battle HUD remains untouched.
            cloud = Image.new('RGBA',layer.image.size)
            cd = ImageDraw.Draw(cloud)
            for i in range(9):
                cx = tx-115+i*29+7*math.sin(phase+i)
                cy = 16+7*math.cos(phase*.6+i*1.3)
                cd.ellipse(tuple(value*scale for value in (cx-29,cy-13,cx+35,cy+12)),fill=(14,27,65,round(155*p)))
            Ink.overlay(layer.image,cloud,3*scale)
            for i in range(4):
                rx,ry = 35+i*17,5+i*3
                ink.line(arc(tx,29,rx,ry,phase*(1 if i%2 else -1)+i,.95*math.pi,wave=.08,phase=phase),2,p*(.85-i*.12))
            for side in (-1,1):
                ink.line(bolt((tx+side*88,16),(tx+side*15,28),phase,side,7,9),2,p*.7)
        if .29 < t < .46:
            p = unit(t,.29,.46)
            for i in range(8):
                y = 12+i*11
                x = fx.mix(hx-30,tx-40,fx.ease(p))
                ink.line([(x-44,y+8),(x,y),(x+18,y-2)],2,math.sin(p*math.pi)*.5)
        # A large broken ground ring establishes the strike's footprint.
        if .40 < t < .73:
            p = ramp(t,.40,.47)*(1-ramp(t,.58,.73))
            for i in range(3):
                ink.line(arc(tx,feet+3,62,15,phase+i*TAU/3,1.45),2,p*.7)
        ink.composite(layer.image)
        return
    # Raikyr turns into an upward lightning spear.
    if .20 < t < .36:
        p = unit(t,.20,.36)
        end = (hx+18*p,home.feet-35-235*fx.ease(p))
        ink.line(bolt((hx,home.feet-12),end,phase,2,12),3,math.sin(p*math.pi))
        ink.line(bolt((hx-45,home.feet-20),(end[0]-8,end[1]+20),phase,5,10),1,math.sin(p*math.pi)*.65)
    # Single, huge descending thunder stroke with branching leaders and a core.
    if .445 < t < .66:
        p = unit(t,.445,.66)
        travel = ramp(t,.445,.48)
        y = fx.mix(30,feet-10,travel)
        alpha = (1-ramp(t,.54,.66))*(.85+.15*math.cos(phase*3)**2)
        main = bolt((tx,21),(tx,y),phase,9,14,18)
        ink.line(main,16 if t >= .48 else 5,alpha)
        for i in (4,7,10,13):
            origin = main[i]
            sign = -1 if i%2 else 1
            dest = (tx+sign*(42+(i%3)*13),min(feet,origin[1]+54))
            ink.line(bolt(origin,dest,phase,i,12,7),3,alpha*.9)
        if t >= .48:
            for side in (-1,1):
                ink.line(bolt((tx+side*68,18),(tx+side*28,feet-9),phase,side+7,21,15),4,alpha*.85)
    # A quick impact star and expanding rings replace a full-screen flash.
    if .48 < t < .83:
        p = unit(t,.48,.83)
        a = (1-p)**1.4
        radius = 10+145*fx.ease(p)
        for band in range(2):
            ink.line(arc(tx,feet-8,radius*(1-band*.19),radius*.24,phase*.1+band,TAU,count=65,wave=.04,phase=phase),3-band,a)
        for i in range(7):
            angle = i*TAU/7+.25
            end = (tx+math.cos(angle)*(45+95*fx.ease(p)),feet-5+math.sin(angle)*(10+22*fx.ease(p)))
            ink.line(bolt((tx,feet-5),end,phase,i,6,7),2,a*.9)
        for i in range(18):
            angle = i*2.39996
            r = 10+(58+(i%4)*18)*fx.ease(p)
            cx,cy = tx+math.cos(angle)*r,feet-65+math.sin(angle)*r*.75+p*p*55
            end = (cx+math.cos(angle)*(6+13*(1-p)),cy+math.sin(angle)*(6+13*(1-p)))
            ink.line([(cx,cy),end],2,a*.85)
        if p < .20:
            a2 = 1-p/.2
            for i in range(8):
                angle = i*math.pi/4
                ink.line([(tx,feet-75),(tx+math.cos(angle)*(26+15*p),feet-75+math.sin(angle)*(26+15*p))],3,a2)
    # Returning electricity resolves back into the same character and aura.
    if .71 < t < .91:
        p = unit(t,.71,.91)
        ink.line(bolt((hx+8,9),(hx,home.feet-8),phase,4,12,17),3,math.sin(p*math.pi)*.8)
        ink.line(arc(hx,home.feet+2,18+60*fx.ease(p),4+15*fx.ease(p),phase,TAU),2,(1-p)*.7)
    ink.composite(layer.image)


def echoes(image, source, home, target, t):
    if .23 < t < .34:
        for offset,alpha in ((.035,.14),(.017,.24)):
            pose = motion(home,target,t-offset)
            fx.paint_sprite(image,source,replace(pose,alpha=pose.alpha*alpha))
