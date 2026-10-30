"""Deterministic pixel choreography for the ordinary move repertoire.

Every sequence has a readable preparation, one contact at .48 and a recovery.
The caller owns the canvas and sprite placement. These painters only decorate
resolved actions and deliberately know nothing about combat state.
"""
import math


SEQUENCES = frozenset({
    'jaw_snap', 'fang_rend', 'armor_break', 'claw_cross', 'crescent_cut',
    'comet_dash', 'spear_thrust', 'seismic_fault', 'loom_snap', 'thorn_lash',
    'cinder_breath', 'fire_wheel', 'meteor_drop', 'tidal_crest', 'geyser',
    'water_spear', 'crystal_prison', 'hail_lance', 'fork_lightning',
    'storm_barrage', 'razor_gale', 'cyclone', 'sonic_cry', 'venom_fang',
    'toxic_cauldron', 'thorn_eruption', 'seed_bloom', 'petal_mend',
    'crystal_aegis', 'shell_bastion', 'mirror_decoy', 'feather_ward',
    'shadow_drain', 'spirit_bolt', 'arrow_volley', 'bat_swarm',
})
SUPPORT = frozenset({'seed_bloom', 'petal_mend', 'crystal_aegis',
                     'shell_bastion', 'mirror_decoy', 'feather_ward'})
TAU = math.tau
INK = '#091922'


class _Ink:
    """Keep bright pixel effects legible over snow and painted scenery.

    The narrow keyline follows each effect, never a full screen dimmer. It
    scales down for concurrent raid casts and leaves the approved HUD alone.
    """
    def __init__(self, layer, scale):
        self.layer = layer
        self.edge = 2 if scale >= .8 else 1

    def line(self, points, color, width=2, alpha=1):
        self.layer.line(points, INK, width+self.edge*2, alpha*.85)
        self.layer.line(points, color, width, alpha)

    def polygon(self, points, color, alpha=1):
        self.layer.line(points+[points[0]], INK, self.edge*2+1, alpha*.9)
        self.layer.polygon(points, color, alpha)

    def rect(self, x, y, width, height, color, alpha=1):
        edge=self.edge
        self.layer.rect(x-edge,y-edge,width+edge*2,height+edge*2,INK,alpha*.85)
        self.layer.rect(x,y,width,height,color,alpha)

    def ring(self, x, y, rx, ry, color, alpha=1, turn=0):
        self.line([(x+math.cos(turn+i*math.pi/8)*rx,
                    y+math.sin(turn+i*math.pi/8)*ry) for i in range(17)],
                  color,2,alpha)


def clamp(v):
    return max(0., min(1., v))


def smooth(v):
    v = clamp(v)
    return v * v * (3 - 2 * v)


def mix(a, b, p):
    return a + (b - a) * p


def _arc(e, x, y, rx, ry, start, end, color, width=3, alpha=1):
    e.line([(x + math.cos(a) * rx, y + math.sin(a) * ry)
            for a in (mix(start, end, i / 16) for i in range(17))],
           color, max(1, round(width)), alpha)


def _ribbon(e, points, width, dark, light, alpha=1):
    """A two tone trail with a sharp pixel core, rather than a soft glow."""
    e.line(points, dark, max(2, round(width)), alpha * .75)
    e.line(points, light, max(2, round(width * .32)), alpha)


def _leaf(e, x, y, length, angle, color, alpha=1):
    u, v = math.cos(angle), math.sin(angle)
    e.polygon([(x-u*length, y-v*length),
               (x-v*length*.38, y+u*length*.38),
               (x+u*length, y+v*length),
               (x+v*length*.38, y-u*length*.38)], color, alpha)


def _crystal(e, x, y, height, width, dark, light, alpha=1):
    e.polygon([(x-width, y), (x-width*.7, y-height*.66),
               (x, y-height), (x+width, y-height*.63),
               (x+width*.75, y)], dark, alpha * .95)
    e.polygon([(x, y-height), (x+width, y-height*.63),
               (x+width*.75, y), (x, y-height*.15)], light, alpha * .9)
    e.line([(x, y-height+4), (x, y-5)], light, 2, alpha)


def _cut(e, x, y, length, angle, dark, light, alpha=1):
    u, v = math.cos(angle), math.sin(angle)
    e.polygon([(x-u*length, y-v*length), (x-v*9, y+u*9),
               (x+u*length, y+v*length), (x+v*4, y-u*4)], dark, alpha)
    e.line([(x-u*length, y-v*length), (x+u*length, y+v*length)], light, 2, alpha)


def _chips(e, x, y, p, colors, size, count=9, angle=0):
    if not 0 <= p < 1:
        return
    for i in range(count):
        a = angle + i * 2.39996
        distance = size * (.25 + p * (1.1 + i % 3 * .2))
        px, py = x+math.cos(a)*distance, y+math.sin(a)*distance*.65+p*p*24
        length = (7 + i % 3 * 2) * (1-p*.65)
        _leaf(e, px, py, length, a+p, colors[i % 3], (1-p)*.9)


def paint(e, animation, actor, target, t, *, colors, back=False, scale=1):
    """Paint one registered sequence; return False only for an unknown ID."""
    seq = animation.sequence
    if seq not in SEQUENCES:
        return False
    if not .025 < t < .97:
        return True
    dark, mid, light = colors
    accent = animation.accent
    # Raid scale limits both silhouettes and particle travel. A duel stays
    # substantial even for a small beast, while ten trainers remain readable.
    s = max(.35, min(1.35, scale))
    e = _Ink(e, s)
    ax, ay = actor.x, actor.feet-(actor.height or actor.size)*.52
    tx, ty = target.x, target.feet-(target.height or target.size)*.52
    sign = 1 if tx >= ax else -1
    start = ax+sign*actor.size*.26
    end = tx-sign*target.size*.1
    charge = smooth((t-.06)/.34)
    approach = smooth((t-.22)/.26)
    release = clamp((t-.48)/.4)
    fade = 1-smooth((t-.67)/.28)
    pulse = math.sin(clamp((t-.05)/.43)*math.pi)
    phase = (accent % 17)/17*TAU

    if seq in {'jaw_snap', 'fang_rend', 'venom_fang'}:
        if back:
            if t < .48:
                for i in range(3):
                    _arc(e, ax, ay, (20+i*9)*s, (12+i*5)*s,
                         -.9+phase*.04, .9, mid, 2, pulse*.45)
            elif seq == 'venom_fang':
                e.ring(tx, target.feet-3, 42*s, 9*s, mid, fade*.65)
            return True
        if .28 < t < .7:
            p = smooth((t-.28)/.2) if t < .48 else 1-smooth((t-.48)/.22)
            gap = (1-p)*35*s
            for i in range(4 if seq == 'fang_rend' else 3):
                x = tx + (i-1.5)*18*s
                h = (24 if i % 2 else 34)*s
                for direction in (-1, 1):
                    y = ty+direction*(gap+3*s)
                    e.polygon([(x-6*s,y+direction*h), (x+6*s,y+direction*h),
                               (x+sign*3*s,y)], dark, fade*.9)
                    e.line([(x+3*s,y+direction*(h-3)), (x+sign*3*s,y)], light, 3, fade)
            if seq == 'venom_fang' and t >= .48:
                for i in range(6):
                    x=tx+(i-2.5)*10*s; y=ty+10+release*56+i%2*8
                    e.polygon([(x,y-7),(x-4,y),(x,y+5),(x+4,y)], mid, fade)
        if t >= .48:
            if seq == 'fang_rend':
                for i in range(3):
                    _cut(e, tx+(i-1)*16*s, ty+release*22, 38*s*(1-release*.4),
                         sign*1.05, dark, light, fade)
            elif seq == 'jaw_snap':
                _arc(e, tx,ty, (18+release*46)*s,(9+release*23)*s,0,TAU,mid,3,fade)
        return True

    if seq in {'armor_break', 'claw_cross', 'crescent_cut', 'comet_dash', 'spear_thrust'}:
        if back:
            if t < .48:
                lanes=5 if seq == 'comet_dash' else 3
                for i in range(lanes):
                    y=ay+(i-(lanes-1)/2)*12*s
                    e.line([(ax-sign*(25+charge*44)*s,y),
                            (ax-sign*10*s,y)],mid,2,pulse*.7)
            return True
        if seq == 'armor_break':
            if t >= .48:
                for i in range(6):
                    a=i*TAU/6+phase*.08
                    points=[(tx,ty)]
                    for j in range(1,4):
                        r=j*(10+release*19)*s
                        points.append((tx+math.cos(a)*r+(-1)**j*5*s,
                                       ty+math.sin(a)*r))
                    _ribbon(e,points,5,dark,light,fade)
                _chips(e,tx,ty,release,colors,39*s,8,phase)
        elif seq == 'claw_cross' and .36 < t < .82:
            for side in (-1,1):
                p=smooth((t-(.36 if side<0 else .40))/(.12 if side<0 else .08))
                for i in range(3):
                    x=tx+(i-1)*15*s
                    _cut(e,x,ty,45*s*p,side*.92,dark,light,fade)
            if t >= .48:
                _chips(e,tx,ty,release,colors,34*s,6)
        elif seq == 'crescent_cut' and .26 < t < .84:
            p=smooth((t-.26)/.22)
            center=mix(start,end,p)
            rotation=-1.25+phase*.02
            _arc(e,center,ty,43*s,66*s,rotation,rotation+math.pi*1.15, dark,11*s,fade)
            _arc(e,center+sign*4,ty,43*s,66*s,rotation,rotation+math.pi*1.15, light,3*s,fade)
            if t >= .48:
                _cut(e,tx,ty,60*s,sign*-.6,dark,light,fade*(1-release*.7))
        elif seq == 'comet_dash' and .18 < t < .78:
            p=smooth((t-.18)/.3)
            x=mix(start,end,p)
            for i in range(5):
                y=mix(ay,ty,p)+(i-2)*9*s
                length=(65-abs(i-2)*15)*s
                e.polygon([(x,y),(x-sign*length,y-3*s),(x-sign*length*.8,y+3*s)],colors[i%3],fade*.75)
            if t >= .48:
                _arc(e,tx,ty,12*s+release*52*s,25*s+release*55*s,-math.pi/2,math.pi/2,light,3,fade)
        elif seq == 'spear_thrust' and .2 < t < .8:
            p=smooth((t-.2)/.28);x=mix(start,end,p);y=mix(ay,ty,p)
            e.polygon([(x+sign*24*s,y),(x-sign*38*s,y-11*s),
                       (x-sign*26*s,y),(x-sign*38*s,y+11*s)],mid,fade)
            _ribbon(e,[(x-sign*76*s,y),(x+sign*20*s,y)],5*s,dark,light,fade)
            if t >= .48:
                _cut(e,tx,ty,62*s,0,dark,light,fade)
                for j in (-1,1):
                    e.line([(tx,ty),(tx+sign*35*s,ty+j*(10+release*27)*s)],mid,2,fade)
        return True

    if seq in {'seismic_fault', 'thorn_eruption', 'thorn_lash', 'loom_snap'}:
        if seq == 'loom_snap':
            if back:
                if .07 < t < .48:
                    for i in range(5):
                        x=ax+(i-2)*10*s
                        e.line([(x,actor.feet-12),(x+sign*22*s,ay-35*charge*s)],mid,2,pulse*.7)
                return True
            if .18 < t < .48:
                for i in range(4):
                    points=[]
                    for j in range(19):
                        u=j/18
                        points.append((mix(start,mix(start,end,approach),u),
                                       mix(ay,ty,u)+(i-1.5)*9*s+math.sin(u*math.pi)*25*(1-charge)))
                    _ribbon(e,points,3,dark,light,.85)
            elif .48 <= t < .9:
                for i in range(4):
                    y=ty+(i-1.5)*11*s
                    e.line([(tx-sign*release*35*s,y-25*release*s),
                            (tx-sign*(30+release*85)*s,y+release*36*s)],mid,2,fade)
                    e.line([(tx+sign*release*25*s,y+25*release*s),
                            (tx+sign*(30+release*38)*s,y-release*36*s)],light,2,fade)
                _cut(e,tx,ty,56*s,math.pi/2,dark,light,fade)
            return True
        if seq == 'thorn_lash':
            if back:
                return True
            if .12 < t < .75:
                points=[]
                for j in range(25):
                    u=j/24
                    x=mix(start,mix(start,end,smooth((t-.16)/.32)),u)
                    y=mix(ay,ty,u)-math.sin(u*math.pi)*55*s*(1-charge*.65)
                    points.append((x,y))
                    if j%4==0:
                        e.polygon([(x,y),(x-sign*10*s,y-15*s),(x-sign*5*s,y+4*s)],light,fade)
                _ribbon(e,points,8*s,dark,mid,fade)
            if t >= .48:
                _arc(e,tx,ty,(20+release*30)*s,(30+release*36)*s,0,TAU*.9,mid,3,fade)
            return True
        if back:
            if .12<t<.9:
                p=smooth((t-.12)/.36)
                points=[]
                for i in range(17):
                    u=i/16
                    points.append((mix(ax,tx,p*u),mix(actor.feet,target.feet,p*u)+(i%2)*7*s))
                _ribbon(e,points,7*s,dark,mid,fade)
            return True
        if t >= .48:
            growth=smooth((t-.48)/.12)
            if seq == 'seismic_fault':
                for i in range(6):
                    x=tx+(i-2.5)*22*s;h=(36+(i*19)%55)*s*growth
                    e.polygon([(x-12*s,target.feet),(x-16*s,target.feet-h*.5),
                               (x+6*s,target.feet-h),(x+17*s,target.feet-h*.4),
                               (x+11*s,target.feet)],dark,fade)
                    e.line([(x+6*s,target.feet-h),(x+17*s,target.feet-h*.4)],light,3,fade)
                _chips(e,tx,target.feet-14,release,colors,62*s,12)
            else:
                for i in range(5):
                    x=tx+(i-2)*24*s; h=(60+(i%3)*25)*s*growth
                    e.polygon([(x-8*s,target.feet),(x-6*s,target.feet-h*.6),
                               (x+sign*12*s,target.feet-h),(x+9*s,target.feet-h*.5),
                               (x+8*s,target.feet)],mid,fade)
                    for j in (1,2):
                        _leaf(e,x+sign*9*s,target.feet-h*j/3,10*s,(-1)**j*.65,light,fade)
        return True

    if seq in {'cinder_breath', 'fire_wheel', 'meteor_drop'}:
        if back:
            if t < .48:
                for i in range(6):
                    a=i*TAU/6+phase; r=(45-25*charge)*s
                    _leaf(e,ax+math.cos(a)*r,ay+math.sin(a)*r,5*s,a,mid,pulse*.8)
            return True
        if seq == 'cinder_breath' and .18 < t < .86:
            reach=smooth((t-.18)/.3); tip=mix(start,end,reach)
            for layer in range(3):
                upper=[];lower=[]
                for i in range(22):
                    u=i/21;x=mix(start,tip,u)
                    wave=math.sin(i*2.4-t*38+phase)*7*s*u
                    width=(43-layer*13)*s*u
                    y=mix(ay,ty,u*reach)+wave
                    upper.append((x,y-width));lower.append((x,y+width))
                e.polygon(upper+lower[::-1],colors[layer],fade*(.72+layer*.11))
            if t >= .48:
                for i in range(7):
                    a=i*TAU/7
                    _leaf(e,tx+math.cos(a)*(15+release*50)*s,
                          ty+math.sin(a)*(15+release*38)*s,12*s*(1-release*.6),a,light,fade)
        elif seq == 'fire_wheel' and .16<t<.9:
            p=smooth((t-.16)/.32);x=mix(start,end,p);y=mix(ay,ty,p)
            radius=(22+12*charge+release*26)*s
            for i in range(7):
                a=i*TAU/7+t*TAU*1.8
                _arc(e,x,y,radius,radius,a,a+.57,colors[i%3],8*s,fade)
                _leaf(e,x+math.cos(a+.57)*radius,y+math.sin(a+.57)*radius,10*s,a+.9,light,fade)
        elif seq == 'meteor_drop':
            if .18<t<.48:
                p=smooth((t-.18)/.3);x=tx-sign*(1-p)*120*s;y=mix(-65,ty,p)
                for layer in range(3):
                    width=(25-layer*8)*s
                    e.polygon([(x-width,y),(x-sign*55*s,y-95*s),
                               (x+width,y-10*s)],colors[layer],.9)
                e.polygon([(x-20*s,y-10*s),(x-9*s,y-28*s),(x+15*s,y-22*s),
                           (x+23*s,y+2*s),(x,y+15*s)],dark)
                e.line([(x-9*s,y-24*s),(x+13*s,y-18*s)],light,3)
            if t >= .48:
                _arc(e,tx,target.feet-5,(18+release*95)*s,(6+release*22)*s,0,TAU,mid,5*s,fade)
                for i in range(7):
                    a=math.pi*(1.1+i*.13)
                    r=(20+release*90)*s
                    e.polygon([(tx+math.cos(a)*r,ty+math.sin(a)*r),
                               (tx+math.cos(a+.12)*r,ty+math.sin(a+.12)*r),
                               (tx+math.cos(a)*r*.6,ty+math.sin(a)*r*.6)],colors[i%3],fade)
        return True

    if seq in {'tidal_crest', 'geyser', 'water_spear', 'crystal_prison', 'hail_lance'}:
        if back:
            if seq in {'geyser','crystal_prison'} and .1<t<.92:
                e.ring(tx,target.feet-3,(12+charge*43)*s,9*s,mid,fade*.8)
                if seq == 'crystal_prison':
                    for i in range(6):
                        a=i*TAU/6+phase
                        e.line([(tx+math.cos(a)*30*s,target.feet+math.sin(a)*8*s),
                                (tx+math.cos(a)*49*s,target.feet+math.sin(a)*13*s)],light,2,pulse)
            elif t < .48:
                for i in range(4):
                    _arc(e,ax,ay,(20+i*7)*s,(12+i*4)*s,-1.1,1.1,mid,2,pulse*.6)
            return True
        if seq == 'tidal_crest' and .16<t<.92:
            p=smooth((t-.16)/.32);x=mix(start,end,p)
            h=(65+25*charge)*s
            floor=mix(actor.feet,target.feet,p)
            e.polygon([(x-sign*84*s,floor),(x-sign*55*s,floor-h*.45),
                       (x-sign*20*s,floor-h),(x+sign*17*s,floor-h*1.07),
                       (x+sign*40*s,floor-h*.78),(x+sign*24*s,floor-h*.56),
                       (x+sign*7*s,floor-h*.72),(x-sign*13*s,floor-h*.55),
                       (x+sign*32*s,floor)],mid,fade*.85)
            _arc(e,x+sign*8*s,floor-h*.72,29*s,26*s,-math.pi*.9,math.pi*.45,light,5*s,fade)
            for i in range(4):
                e.line([(x-sign*(35+i*9)*s,floor-i*9*s),
                        (x-sign*(65+i*6)*s,floor-i*9*s)],light,2,fade*.7)
        elif seq == 'geyser' and t >= .48:
            p=smooth((t-.48)/.11)
            for i in range(5):
                x=tx+(i-2)*13*s;h=(112-abs(i-2)*19)*s*p
                points=[(x-7*s,target.feet),(x-6*s,target.feet-h*.7),
                        (x+(-1)**i*15*s,target.feet-h),(x+8*s,target.feet-h*.74),(x+7*s,target.feet)]
                e.polygon(points,colors[i%3],fade*.8)
            for i in range(8):
                a=math.pi*(1.05+i*.13);r=(15+release*70)*s
                _leaf(e,tx+math.cos(a)*r,target.feet-70*s+math.sin(a)*r+release*65,
                      6*s,a,light,fade)
        elif seq == 'water_spear' and .17<t<.85:
            p=smooth((t-.17)/.31);x=mix(start,end,p);y=mix(ay,ty,p)
            e.polygon([(x+sign*37*s,y),(x-sign*34*s,y-13*s),
                       (x-sign*16*s,y),(x-sign*34*s,y+13*s)],mid,fade)
            e.line([(x-sign*60*s,y),(x+sign*32*s,y)],light,3,fade)
            for i in (-1,1):
                _arc(e,x-sign*25*s,y,23*s,18*s,i*.3,i*2.4,light,2,fade*.8)
            if t>=.48:
                for i in range(5):
                    e.line([(tx,ty),(tx+sign*(25+release*42)*s,
                                             ty+(i-2)*(8+release*14)*s)],mid,3,fade)
        elif seq == 'crystal_prison' and t>=.48:
            grow=smooth((t-.48)/.1)
            for i in range(7):
                x=tx+(i-3)*17*s;h=(128-abs(i-3)*14)*s*grow
                _crystal(e,x,target.feet,h,13*s,mid,light,fade*.95)
            if t < .68:
                for i in range(3):
                    _cut(e,tx,ty,30*s*(1-release*.4),i*math.pi/3,
                         mid,light,(1-smooth((t-.56)/.12))*.8)
            if t>.62:
                for i in range(3):
                    e.line([(tx-39*s,ty+(i-1)*20*s),(tx-9*s,ty+(i-1)*20*s-9),
                            (tx+5*s,ty+(i-1)*20*s+6),(tx+37*s,ty+(i-1)*20*s-4)],dark,2,fade)
        elif seq == 'hail_lance':
            if .15<t<.48:
                p=smooth((t-.15)/.33)
                for i in range(5):
                    lane=(i-2)*17*s; x=mix(tx-sign*80*s,tx+lane,p)
                    y=mix(-35-i*12,ty+lane*.2,p)
                    _crystal(e,x,y,38*s,7*s,mid,light)
                    e.line([(x-sign*7*s,y-35*s),(x-sign*20*s,y-65*s)],light,2,.6)
            if t>=.48:
                _chips(e,tx,ty,release,colors,65*s,15,phase)
        return True

    if seq in {'fork_lightning','storm_barrage','razor_gale','cyclone','sonic_cry'}:
        if back:
            if t<.48 and seq in {'fork_lightning','storm_barrage'}:
                for i in range(3):
                    y=ay-25*s+i*13*s
                    e.line([(ax-23*s,y),(ax-8*s,y-5*s),(ax+9*s,y+5*s),(ax+24*s,y)],mid,2,pulse*.9)
            elif seq == 'cyclone' and .08<t<.9:
                e.ring(tx,target.feet-2,53*s,11*s,mid,fade*.65,t*5)
            return True
        if seq == 'fork_lightning' and .25<t<.82:
            p=smooth((t-.25)/.23)
            for branch in range(3):
                points=[]
                for j in range(10):
                    u=j/9
                    x=mix(start,mix(start,end,p),u)
                    y=mix(ay,ty,u)+(0 if j in (0,9) else (-1)**j*(7+branch*7)*s)
                    points.append((x,y))
                _ribbon(e,points,(7-branch*2)*s,dark,colors[2-branch%2],fade)
            if t>=.48:
                for i in range(5):
                    a=phase+i*TAU/5
                    e.line([(tx,ty),(tx+math.cos(a)*24*s,ty+math.sin(a)*24*s),
                            (tx+math.cos(a+.3)*(36+release*22)*s,
                             ty+math.sin(a+.3)*(36+release*22)*s)],light,2,fade)
        elif seq == 'storm_barrage':
            if .17<t<.48:
                for i in range(3):
                    x=tx+(i-1)*34*s;y=ty-100*s
                    _arc(e,x,y,24*s,10*s,math.pi,TAU,mid,6,pulse*.8)
            if .48<=t<.89:
                for i in range(3):
                    x=tx+(i-1)*28*s
                    points=[(x-14*s,ty-115*s),(x+6*s,ty-74*s),
                            (x-11*s,ty-69*s),(x+8*s,ty-22*s),(tx,ty+11*s)]
                    _ribbon(e,points,8*s,dark,light,fade)
                e.line([(tx-47*s,target.feet),(tx-14*s,target.feet-8*s),
                        (tx+10*s,target.feet+3*s),(tx+47*s,target.feet-3*s)],mid,4,fade)
        elif seq == 'razor_gale' and .16<t<.87:
            p=smooth((t-.16)/.32)
            for i in range(3):
                x=mix(start,end,p)+(i-1)*17*s;y=mix(ay,ty,p)+(i-1)*26*s
                _arc(e,x,y,43*s,23*s,-1.1,1.1,mid,7*s,fade)
                _arc(e,x+sign*5*s,y,43*s,23*s,-1.1,1.1,light,2,fade)
            if t>=.48:
                _cut(e,tx,ty,62*s,sign*-.85,dark,light,fade)
        elif seq == 'cyclone' and .18<t<.92:
            grow=smooth((t-.18)/.3)
            for i in range(6):
                y=target.feet-i*17*s*grow
                radius=(20+i*7)*s*grow
                a=t*TAU*2+i*.8
                _arc(e,tx,y,radius,10*s,a,a+math.pi*1.35,colors[i%3],3*s,fade)
            if t>=.48:
                for i in range(7):
                    a=i*2.4+t*5;r=(26+release*45)*s
                    _leaf(e,tx+math.cos(a)*r,ty+math.sin(a)*r*.6-release*35,7*s,a,mid,fade)
        elif seq == 'sonic_cry' and .13<t<.89:
            for i in range(4):
                p=clamp((t-.13-i*.045)/(.35-i*.045))
                x=mix(start,end,p);r=(12+34*p)*s
                alpha=fade*min(1,p*5)
                _arc(e,x,mix(ay,ty,p),r*.5,r,-math.pi/2,math.pi/2,colors[i%3],4*s,alpha)
            if t>=.48:
                for i in (-1,1):
                    points=[(tx+i*20*s,ty-48*s),(tx+i*37*s,ty-16*s),
                            (tx+i*26*s,ty+9*s),(tx+i*45*s,ty+39*s)]
                    e.line(points,light,4,fade)
                _arc(e,tx,ty,(24+release*54)*s,(44+release*32)*s,
                     -1.3 if sign>0 else math.pi-1.3,
                     1.3 if sign>0 else math.pi+1.3,mid,3,fade*.9)
        return True

    if seq in {'toxic_cauldron','shadow_drain','spirit_bolt','arrow_volley','bat_swarm'}:
        if back:
            if seq=='toxic_cauldron' and .12<t<.92:
                e.ring(tx,target.feet,54*s*charge,13*s,mid,fade*.7)
                e.ring(tx,target.feet,39*s*charge,9*s,dark,fade*.9)
            elif seq=='shadow_drain' and .12<t<.9:
                _arc(e,tx,ty,40*s,54*s,-1.3,1.3,mid,4,fade*.65)
            return True
        if seq=='toxic_cauldron' and t>=.48:
            for i in range(9):
                p=clamp(release*1.4-i*.025)
                x=tx+math.sin(i*2.4+p*3)*43*s
                y=target.feet-12-p*(70+i%3*21)*s
                radius=(4+i%3*3)*s*(1-p*.4)
                e.ring(x,y,radius,radius,mid,fade*.9)
                e.rect(x-radius*.5,y-radius*.5,3,3,light,fade)
            for i in range(3):
                x=tx+(i-1)*25*s
                e.polygon([(x-13*s,ty+7*s),(x-7*s,ty-13*s),(x+7*s,ty-13*s),
                           (x+13*s,ty+7*s),(x,ty+16*s)],dark,fade*.5)
        elif seq=='shadow_drain' and .15<t<.9:
            if t<.48:
                for i in range(3):
                    a=t*4+i*TAU/3
                    _arc(e,tx,ty,(25+charge*13)*s,(37+charge*20)*s,a,a+1.3,mid,4,pulse*.7)
            else:
                for lane in range(3):
                    points=[]
                    for i in range(21):
                        u=i/20
                        points.append((mix(tx,ax,u),mix(ty,ay,u)+math.sin(u*TAU-release*9+lane)*14*s))
                    e.line(points,colors[lane],3,fade*.8)
                    p=clamp(release*1.6-lane*.14)
                    _leaf(e,mix(tx,ax,p),mix(ty,ay,p),8*s,-release*3,light,fade)
        elif seq=='spirit_bolt' and .15<t<.88:
            p=smooth((t-.15)/.33);x=mix(start,end,p);y=mix(ay,ty,p)-math.sin(p*math.pi)*31*s
            for i in range(3):
                tail=(i+1)*20*s
                _ribbon(e,[(x-sign*tail,y+math.sin(i+t*8)*12*s),(x,y)],7-i*2,dark,mid,fade*.65)
            e.polygon([(x-sign*20*s,y),(x,y-15*s),(x+sign*21*s,y),
                       (x,y+15*s)],mid,fade)
            _cut(e,x,y,12*s,0,mid,light,fade)
            if t>=.48:
                _arc(e,tx,ty,(12+release*47)*s,(12+release*47)*s,phase,phase+TAU*.8,light,3,fade)
        elif seq=='arrow_volley' and .13<t<.82:
            p=smooth((t-.13)/.35)
            for i in range(7):
                lane=(i-3)*12*s
                x=mix(start-sign*(i%2)*25*s,tx+lane*.3,p)
                y=mix(ay+lane,ty+lane*.5,p)-math.sin(p*math.pi)*(35+i%3*11)*s
                motif=getattr(animation,'motif','arrow')
                if motif=='feather':
                    _leaf(e,x,y,20*s,0,mid,fade)
                    e.line([(x-sign*24*s,y),(x+sign*17*s,y)],light,2,fade)
                    for j in range(4):
                        fx=x-sign*(j-1)*7*s
                        e.line([(fx,y),(fx-sign*8*s,y-7*s)],light,2,fade*.8)
                        e.line([(fx,y),(fx-sign*8*s,y+7*s)],light,2,fade*.8)
                elif motif=='spark':
                    points=[(x-sign*j*8*s,y+math.sin(j*.55+t*12+i)*j*1.3*s)
                            for j in range(8)]
                    _ribbon(e,points,9*s,dark,mid,fade*.85)
                    e.polygon([(x+sign*12*s,y),(x-sign*5*s,y-8*s),
                               (x-sign*15*s,y),(x-sign*5*s,y+8*s)],light,fade)
                else:
                    e.line([(x-sign*31*s,y),(x,y)],mid,3,fade)
                    e.polygon([(x+sign*9*s,y),(x-sign*5*s,y-5*s),
                               (x-sign*5*s,y+5*s)],light,fade)
                    for j in (-1,1):
                        e.line([(x-sign*26*s,y),(x-sign*34*s,y+j*5*s)],mid,2,fade)
            if t>=.48:
                for i in range(4):
                    e.line([(tx+(i-1.5)*14*s,ty),(tx+(i-1.5)*22*s,ty-15*release*s)],light,2,fade)
        elif seq=='bat_swarm' and .13<t<.9:
            p=smooth((t-.13)/.35)
            for i in range(9):
                a=i*2.399+phase
                x=mix(start,tx,p)+math.cos(a+t*7)*(10+20*p)*s
                y=mix(ay,ty,p)+math.sin(a+t*7)*37*s
                flap=math.sin(t*28+i)*8*s
                e.polygon([(x,y-4*s),(x-17*s,y-10*s-flap),(x-10*s,y+6*s),
                           (x-4*s,y+2*s),(x,y+8*s),(x+4*s,y+2*s),
                           (x+10*s,y+6*s),(x+17*s,y-10*s-flap)],mid,fade*.85)
                e.rect(x-2*s,y-2*s,4*s,4*s,light,fade*.8)
        return True

    # Support remains actor-centered: no damaging strike on a non-damaging move.
    if seq in SUPPORT:
        floor=actor.feet
        if seq=='seed_bloom':
            if back and .08<t<.92:
                e.ring(ax,floor-2,(16+charge*37)*s,10*s,mid,fade*.7)
                for i in range(5):
                    a=i*TAU/5+phase
                    _leaf(e,ax+math.cos(a)*39*s,floor+math.sin(a)*9*s,
                          10*s*charge,a,mid,fade*.8)
            elif not back and .15<t<.94:
                for i in range(5):
                    x=ax+(i-2)*19*s;h=(38+i%3*18)*s*charge
                    e.line([(x,floor),(x+math.sin(i)*9*s,floor-h)],mid,3,fade)
                    if t>=.48:
                        for j in range(5):
                            a=j*TAU/5+phase
                            _leaf(e,x+math.cos(a)*9*s,floor-h+math.sin(a)*9*s,
                                  8*s*smooth((t-.48)/.12),a,light,fade)
                if t>=.48:
                    for i in range(5):
                        x=ax+(i-2)*20*s;y=ay-15-release*55*s+i%2*10*s
                        e.line([(x-4*s,y),(x+4*s,y)],light,2,fade)
                        e.line([(x,y-4*s),(x,y+4*s)],light,2,fade)
        elif seq=='petal_mend':
            if back:
                if .1<t<.9:
                    _arc(e,ax,ay,52*s*charge,61*s*charge,0,TAU,mid,2,fade*.45)
            else:
                for i in range(12):
                    a=i*2.399+t*3
                    r=(55-32*charge+release*24)*s
                    y=ay+math.sin(a)*r*.7-release*32*s
                    _leaf(e,ax+math.cos(a)*r,y,8*s,a+math.pi/2,colors[i%3],fade*.9)
                if t>=.48:
                    for i in range(4):
                        x=ax+(i-1.5)*22*s;y=ay-15-release*47*s+i%2*14*s
                        e.line([(x-5*s,y),(x+5*s,y)],light,3,fade)
                        e.line([(x,y-5*s),(x,y+5*s)],light,3,fade)
        elif seq=='crystal_aegis':
            if back:
                if .1<t<.9:
                    e.ring(ax,floor-2,48*s*charge,12*s,mid,fade*.7)
            else:
                grow=smooth((t-.12)/.36)
                points=[(ax,ay-65*s*grow),(ax+49*s*grow,ay-25*s),
                        (ax+38*s*grow,ay+35*s),(ax,ay+65*s*grow),
                        (ax-38*s*grow,ay+35*s),(ax-49*s*grow,ay-25*s)]
                e.polygon(points,dark,fade*.2)
                e.line(points+[points[0]],light,3,fade*.9)
                if t>=.48:
                    for x,y in points:
                        e.line([(ax,ay),(x,y)],mid,2,fade*.6)
                    e.polygon([(ax,ay-20*s),(ax+12*s,ay),(ax,ay+21*s),(ax-12*s,ay)],light,fade*.7)
        elif seq=='shell_bastion':
            if back:
                return True
            grow=smooth((t-.1)/.38)
            _arc(e,ax,floor,64*s*grow,130*s*grow,math.pi,TAU,mid,6*s,fade*.85)
            for i in range(7):
                a=math.pi+i*math.pi/6
                x=ax+math.cos(a)*64*s*grow;y=floor+math.sin(a)*130*s*grow
                e.line([(ax,floor-23*s),(x,y)],light,2,fade*.65)
                if i<6:
                    b=a+math.pi/6
                    e.polygon([(ax,floor-23*s),(x,y),
                               (ax+math.cos(b)*64*s*grow,floor+math.sin(b)*130*s*grow)],colors[i%2],fade*.14)
            if t>=.48:
                e.line([(ax-52*s,floor-46*s),(ax+52*s,floor-46*s)],light,3,fade)
        elif seq=='mirror_decoy':
            for i in (-1,1):
                x=ax+i*(20+charge*47)*s
                y=ay+math.sin(t*6+i)*8*s
                points=[(x,y-47*s),(x+21*s,y-28*s),(x+21*s,y+28*s),
                        (x,y+47*s),(x-21*s,y+28*s),(x-21*s,y-28*s)]
                if back:
                    e.polygon(points,dark,fade*.3)
                else:
                    e.line(points+[points[0]],mid,3,fade*.85)
                    e.line([(x-12*s,y+20*s),(x+12*s,y-20*s)],light,3,fade*.8)
                    if t>=.48:
                        e.line([(x-15*s,y-19*s),(x+15*s,y+19*s)],light,2,fade*.55)
        elif seq=='feather_ward':
            if back:
                return True
            for side in (-1,1):
                for i in range(6):
                    a=side*(.35+i*.14)
                    x=ax+side*(18+i*6)*s*charge
                    y=ay+13*s-i*13*s*charge
                    length=(18+i*2)*s
                    _leaf(e,x,y,length,side*(-.65+i*.07),colors[i%3],fade*.8)
                    e.line([(x-math.cos(a)*length*.55,y-math.sin(a)*length*.55),
                            (x+math.cos(a)*length*.55,y+math.sin(a)*length*.55)],light,2,fade*.8)
            if t>=.48:
                _arc(e,ax,ay,53*s,67*s,.4,math.pi-.4,light,3,fade)
        return True
    return True
