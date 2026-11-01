"""Hexpurr's approved choreography and open hexfire aura.

All timing, form and wardrobe inputs are explicit. Render workers never share
mutable animation state, and cached source pixels are never painted into.
"""
from dataclasses import replace
from functools import lru_cache
import json
import math
from PIL import Image
from utils.artwork import PROJECT_ROOT
from utils import move_animations as fx

ASSETS = PROJECT_ROOT / 'data/hexpurr'
SEQUENCE = 'hexpurr_lanterns_out'
LOOP_SECONDS = 2.0


@lru_cache(maxsize=1)
def manifest():
    return json.loads((ASSETS / 'manifest.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=40)
def asset(relative):
    path = (ASSETS / relative).resolve()
    if not path.is_relative_to(ASSETS.resolve()) or relative not in manifest()['sha256']:
        raise ValueError('Unregistered Hexpurr artwork')
    with Image.open(path) as source:
        image = source.convert('RGBA')
    image.thumbnail((180, 176), Image.Resampling.NEAREST)
    return image


def pose_asset(name):
    return asset(manifest()['poses'][name])


def sprite(form='', armor_id=''):
    form = 'hexfire' if form == 'hexfire' else 'lantern'
    if armor_id and form == 'hexfire':
        relative = manifest()['forms'][form]['armor'].get(armor_id)
        image = asset(relative).copy() if relative else pose_asset(form).copy()
    elif armor_id:
        from utils import armor_art
        image = armor_art.portrait('hexpurr', armor_id)
        image.thumbnail((180, 176), Image.Resampling.NEAREST)
    else:
        image = pose_asset(form).copy()
    image.info['hexpurr_armor'] = armor_id
    return image


def u(t, start, end):
    return fx.clamp((t-start)/(end-start))


def s(t, start, end):
    return fx.smooth(u(t, start, end))


def motion(home, target, t):
    direction = 1 if target.x >= home.x else -1
    reach = target.x - direction * 96 * home.size/164
    landing = target.feet
    if t < .395:
        return replace(home, feet=home.feet+2*math.sin(u(t, 0, .087)*math.pi) if t < .087 else home.feet)
    if t < .50:
        p = u(t, .395, .50)
        return replace(home, x=fx.mix(home.x, reach, fx.smooth(p)),
                       feet=fx.mix(home.feet, landing, fx.smooth(p))-math.sin(p*math.pi)*25,
                       angle=-direction*4*math.sin(p*math.pi))
    if t < .565:
        return replace(home, x=reach, feet=landing+3*math.sin(u(t, .50, .565)*math.pi))
    if t < .79:
        p = u(t, .565, .79)
        return replace(home, x=fx.mix(reach, home.x, fx.smooth(p)),
                       feet=fx.mix(landing, home.feet, fx.smooth(p))-math.sin(p*math.pi)*18,
                       angle=direction*3*math.sin(p*math.pi))
    return home


def pose_name(t):
    for limit, name in ((.087, 'lantern'), (.28, 'crouch'), (.35, 'silhouette'),
                        (.395, 'hexfire'), (.50, 'pounce'), (.81, 'landing')):
        if t < limit:
            return name
    return 'hexfire'


def visible(old, new, t, transforming=False):
    return new if t >= (.35 if transforming else fx.IMPACT) else old


def paint(image, old, new, pose, *, t=1, transforming=False, seconds=0, aura=True):
    shown = visible(old, new, t, transforming)
    source = sprite(shown.form, shown.armor_id)
    if transforming:
        name = pose_name(t)
        if not shown.armor_id:
            source = pose_asset('crouch' if name == 'silhouette' else name)
            if name in {'crouch', 'pounce', 'landing', 'silhouette'}:
                pose = replace(pose, size=pose.size*1.06)
        elif name in {'crouch', 'silhouette', 'landing'}:
            # The fitted outfit travels with the whole character. Squash the
            # dressed cutout instead of substituting an unarmored action pose.
            source = source.resize((source.width, round(source.height*.84)), Image.Resampling.NEAREST)
        if name == 'silhouette':
            silhouette = Image.new('RGBA', source.size, '#080611')
            silhouette.putalpha(source.getchannel('A'))
            source = silhouette
    strength = s(t, .35, .395) if transforming else 1
    if aura and shown.form == 'hexfire' and shown.hp > 0:
        layer = fx.Effects(image.size)
        paint_aura(layer, pose, seconds, strength)
        image.alpha_composite(layer.image)
    fx.paint_sprite(image, source, pose)
    if aura and shown.form == 'hexfire' and shown.hp > 0:
        layer = fx.Effects(image.size)
        paint_aura(layer, pose, seconds, strength, front=True)
        image.alpha_composite(layer.image)


def echoes(image, source, home, target, t):
    if not .415 < t < .535:
        return
    armor_id = source.info.get('hexpurr_armor', '') if source else ''
    source = sprite('hexfire', armor_id) if armor_id else pose_asset('pounce')
    for delay, alpha in ((.055, .12), (.027, .23)):
        pose = motion(home, target, max(.395, t-delay))
        fx.paint_sprite(image, source, replace(pose, size=pose.size*(1 if armor_id else 1.06), alpha=alpha))


def crescent(layer, x, y, radius, color, alpha=1, angle=0, thickness=.24):
    points = []
    for i in range(27):
        a = -1.45+i/26*2.9+angle
        points.append((x+math.cos(a)*radius, y+math.sin(a)*radius))
    for i in reversed(range(27)):
        a = -1.45+i/26*2.9+angle
        r = radius*(1-thickness*math.sin(math.pi*i/26))
        points.append((x+math.cos(a)*r-2*math.sin(math.pi*i/26), y+math.sin(a)*r))
    layer.polygon(points, color, alpha)


def spirit_point(p, home):
    a = 1-p
    direction = -1 if home.flip else 1
    scale = home.size/164
    return (home.x+direction*scale*(a*a*a*(-46)+3*a*a*p*(-64)+3*a*p*p*30+p*p*p*43),
            home.feet+scale*(a*a*a*(-84)+3*a*a*p*(-173)+3*a*p*p*(-149)+p*p*p*(-70)))


def effect(layer, actor, target, t, *, home=None, back=False):
    home = home or actor
    direction = -1 if home.flip else 1
    scale = home.size/164
    x, feet = home.x, home.feet
    if back:
        if .35 <= t < .46:
            p = u(t, .35, .46)
            layer.ring(x, feet, (30+fx.ease(p)*75)*scale, (9+fx.ease(p)*19)*scale, '#d62d4b', (1-p)*.8)
            crescent(layer, x-7*direction*scale, feet-77*scale, (35+fx.ease(p)*58)*scale, '#bc1637', (1-p)*.6, -.55, .15)
            layer.burst(x+18*direction*scale, feet-80*scale, p, ('#c51035', '#f54860', '#ffd0a0'), count=16, radius=62*scale)
        return
    dark = s(t, .245, .285)*(1-s(t, .35, .395))
    if dark:
        layer.rect(0, 0, *layer.image.size, '#050410', dark*.83)
    if .087 <= t < .28:
        p = u(t, .087, .275)
        sx, sy = spirit_point(fx.smooth(p), home)
        tail = [spirit_point(max(0, fx.smooth(p)-j*.025), home) for j in range(10)]
        layer.line(tail, '#ff9825', 3, .7*(1-p))
        size = max(3, 61*(1-p)**.55)*scale
        fx.paint_sprite(layer.image, pose_asset('spirit'), fx.Pose(sx, sy+size/2, size=size, flip=home.flip, alpha=1-s(t,.255,.28)))
        for i in range(9):
            px, py = spirit_point(fx.clamp(p-i*.025), home)
            layer.rect(px+math.sin(i*2.4)*7*(1-p), py+math.cos(i*2.4)*7*(1-p), 2, 2, '#ffc36a', .6*(1-p))
    if .28 <= t < .35:
        alpha = s(t, .28, .30)
        crescent(layer, x+39*direction*scale, feet-70*scale, 7*scale, '#ff2149', alpha, -.2, .52)
        if t >= .302:
            for ax, ay, bx, by in ((21,-49,29,-47),(45,-45,53,-47)):
                layer.line([(x+ax*direction*scale,feet+ay*scale),(x+bx*direction*scale,feet+by*scale)], '#ff3e5b', 2, alpha)
    if .465 <= t < .625:
        p = u(t, .465, .625)
        alpha = (1-p)**.7
        sx, sy = target.x-31*direction*scale, target.feet-88*scale
        crescent(layer, sx+(-13+p*11)*direction*scale, sy, (54+fx.ease(p)*20)*scale, '#df153d', alpha, -.28, .22)
        crescent(layer, sx+(-9+p*11)*direction*scale, sy, (50+fx.ease(p)*20)*scale, '#ffb39a', alpha*.9, -.28, .065)
        layer.burst(target.x-9*direction*scale, target.feet-79*scale, u(t, .48, .64), ('#ef254b', '#ff6777', '#ffc596'), count=17, radius=54*scale)
    if .50 <= t < .62:
        p = u(t, .50, .62)
        layer.ring(actor.x, actor.feet+1, (16+fx.ease(p)*49)*scale, (3+fx.ease(p)*9)*scale, '#f75b69', (1-p)*.6)
    if .80 < t < .98:
        p = u(t, .80, .98)
        for i in range(6):
            layer.rect(x+(-35+i*15+math.sin(i+p*3)*4)*scale, feet-(25+p*62+(i%3)*14)*scale, 2, 2, '#ff425e', (1-p)*.55)


def ribbon(layer, points, widths, color, alpha):
    if not points or alpha <= 0:
        return
    left, right = [], []
    for index, ((x, y), width) in enumerate(zip(points, widths)):
        a, b = points[max(0,index-1)], points[min(len(points)-1,index+1)]
        dx, dy = b[0]-a[0], b[1]-a[1]
        length = max(.01, math.hypot(dx, dy))
        nx, ny = -dy/length, dx/length
        left.append((x+nx*width, y+ny*width))
        right.append((x-nx*width, y-ny*width))
    layer.polygon(left+list(reversed(right)), color, alpha)


def paint_aura(layer, pose, seconds, strength=1, *, front=False):
    """Approved two-second loop: separate wisps, open center and tiny embers."""
    cycle = seconds/LOOP_SECONDS*math.tau
    scale = pose.size/164
    x, feet = pose.x, pose.feet
    height = 194*scale
    pulse = .88+.12*math.sin(cycle)
    strength *= fx.clamp(pose.alpha)
    if front:
        for i in range(7):
            age = (seconds/LOOP_SECONDS+i/7) % 1
            side = -1 if i%2 else 1
            xx = x+side*(58+8*math.sin(cycle+i*2.1))*scale
            yy = feet+3-age*157*scale
            alpha = math.sin(age*math.pi)**1.2*.58*strength
            layer.rect(xx, yy, 2, 3 if i%3 else 4, '#ff7c88', alpha)
        return
    layer.draw.ellipse((x-74*scale, feet-7*scale, x+74*scale, feet+8*scale),
                       fill=fx.Effects.color('#f1244b', .065*strength*pulse))
    for side in (-1, 1):
        for k in range(4):
            phase = cycle+side*.55+k*2.15
            age = (seconds/LOOP_SECONDS+k/4+(0 if side<0 else .12)) % 1
            bottom = .025+age*.37
            top = bottom+.52+.04*(k%2)
            points, widths = [], []
            for j in range(27):
                p = j/26
                v = bottom+(top-bottom)*p
                belly = 36+57*math.sin(min(1,v)*math.pi)**.75
                wobble = math.sin(v*14-phase)*7+math.sin(v*24+phase*2)*3
                curl = 8*math.sin(p*math.pi*1.4)*p
                xx = x+side*(belly+(k-1.5)*4+wobble-curl)*scale
                yy = feet+3-v*height
                points.append((xx, yy))
                taper = math.sin(p*math.pi)**.65*(1-.43*p)
                widths.append((3.4+(k%2)*.8)*scale*taper)
            flicker = (.85+.15*math.sin(phase+1.1))*math.sin(age*math.pi)**.6*strength
            ribbon(layer, points, [w*2.6 for w in widths], '#c8123b', .085*flicker)
            ribbon(layer, points, [w*1.6 for w in widths], '#ee244c', .18*flicker)
            ribbon(layer, points, widths, '#ff4e69', .44*flicker)
            lo, hi = (9+k*2, 15+k*2)
            layer.line(points[lo:hi], '#ff969e', 1, .42*flicker)
    for i in range(3):
        age = (seconds/LOOP_SECONDS+i/3) % 1
        direction = -1 if i%2 else 1
        xx = x+direction*(31+18*math.sin(age*math.pi))*scale
        yy = feet-(122+age*61)*scale
        alpha = math.sin(age*math.pi)*.38*strength
        points = [(xx,yy+13*scale), (xx-direction*3*scale,yy+8*scale),
                  (xx+direction*4*scale,yy+3*scale), (xx+direction*2*scale,yy-2*scale)]
        layer.line(points, '#fa4869', 2, alpha)
        layer.rect(xx+direction*2*scale, yy-2*scale, 2, 2, '#ffadb0', alpha*.8)
