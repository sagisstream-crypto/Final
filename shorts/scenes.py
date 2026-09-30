"""Пять фэнтези-сцен. Координаты мира = экранные при cam=0; слой шириной WW начинается с x=-PAD."""
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from engine import (W, H, WW, PAD, hexc, ease, smooth_noise, sky_gradient, add_radial, disc,
                    bake_stars, to_rgba_img, ridge_layer, fog_texture, add_glow, Sprite, bezier, rot)

WX = np.arange(WW, dtype=np.float32) - PAD  # мировые x для столбцов слоя


def sx(x, speed, cam):
    return x - cam * speed


class Scene:
    layers = []  # (img, speed, y)
    caption_y = 1390

    def dynamic(self, img, t, cam):
        pass

    def light(self, L, t, cam):
        pass

    def grade(self, arr, t):
        return arr


def rim_from_alpha(img, color, shift=4, strength=0.8, blur=2):
    """Подсветка верхних краёв силуэта (контровой свет)."""
    a = np.asarray(img, np.float32)[..., 3] / 255
    sh = np.zeros_like(a)
    sh[shift:] = a[:-shift]
    edge = np.clip(a - sh, 0, 1)
    edge = np.asarray(Image.fromarray((edge * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur)), np.float32) / 255
    arr = np.asarray(img, np.float32).copy()
    arr[..., :3] += edge[..., None] * np.asarray(color, np.float32) * strength
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGBA")


# =====================================================================
# 1. Последний дракон
# =====================================================================
class DragonScene(Scene):
    def __init__(self, ev):
        self.ev = ev
        rng = np.random.default_rng(11)
        sky = sky_gradient([(0, "#03040c"), (0.3, "#0e1233"), (0.5, "#2a2150"), (0.64, "#5c3564"),
                            (0.72, "#a4575a"), (0.78, "#d98457"), (1, "#2a1426")])
        self.moon = (560 + PAD, 720, 250)
        mx, my, mr = self.moon
        bake_stars(sky, rng, 900, 1150, dim_fn=lambda x, y: min(1, max(0, (math.hypot(x - mx, y - my) - mr * 1.2) / 400)))
        add_radial(sky, mx, my, mr * 3.2, (120, 90, 110), 2.2, 0.8)
        add_radial(sky, mx, my, mr * 1.6, (255, 220, 190), 2.5, 0.6)
        disc(sky, mx, my, mr, (255, 238, 210))
        # кратеры
        for _ in range(14):
            a, d = rng.random() * 6.28, rng.random() ** 0.7 * mr * 0.8
            cr = 15 + rng.random() * 55
            add_radial(sky, mx + math.cos(a) * d, my + math.sin(a) * d, cr, (-25, -30, -20), 1.2, 1)
        sky_img = to_rgba_img(sky)
        far = ridge_layer(1180 - 330 * smooth_noise(WW, 3, 6, 3), "#5a3a66", "#2c1c3a", rim=(120, 80, 100), rim_w=4,
                          haze="#8a5a70", haze_amt=0.35)
        mid = ridge_layer(1390 - 240 * smooth_noise(WW, 8, 6, 4), "#2a1a36", "#120a1a", rim=(170, 110, 110), rim_w=3,
                          haze="#5a3050", haze_amt=0.3)
        # утёс, на котором сидит дракон
        n = smooth_noise(WW, 21, 6, 6)
        ys = np.where(WX > 220, 1300 + 18 * (n - 0.5), 1300 + (220 - WX) * 2.4 + 60 * n)
        ys = np.minimum(ys, 2400)
        cliff = ridge_layer(ys.astype(np.float32), "#140d18", "#07040a", rim=(200, 150, 140), rim_w=2.5, depth=400)
        self.fog = fog_texture(5, 500, "#a0708a", 0.45)
        self.layers = [(sky_img, 0.08, 0), (far, 0.25, 0), (mid, 0.5, 0), (self.fog, 0.7, 1150), (cliff, 1.0, 0)]
        self.ground = 1300
        self.ox = 690
        er = np.random.default_rng(5)
        self.embers = er.random((110, 5))

    # ------ дракон ------
    def draw_dragon(self, t, cam):
        tc = self.ev["climax"]
        breathe = math.sin(t * 2 * math.pi / 4.5)
        spread = ease((t - tc) / 1.6)
        bow = ease((t - tc - 2.5) / 2.5)
        col = (10, 6, 14, 255)
        colw = (22, 12, 30, 238)
        sp = Sprite(1100, 900, 560, 780)
        # хвост
        tail = bezier((170, -60), (360, 30), (470, -40), 26)
        tail += bezier((470, -40), (540, -80), (505, -135), 12)[1:]
        sp.tube(tail, 42, 5, col)
        S = (20, -285)

        def wing(offset, ang, scale, c):
            S2 = (S[0] + offset[0], S[1] + offset[1])
            pts_rel = {"E": (115, -125), "Wr": (35, -385), "th": (5, -425),
                       "T1": (330, -310), "T2": (405, -150), "T3": (335, -15), "T4": (195, 70)}
            P = {k: rot((S2[0] + v[0], S2[1] + v[1]), S2, ang, scale) for k, v in pts_rel.items()}
            wr = P["Wr"]

            def mid(a, b, k=0.28):
                m = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                return (m[0] + (wr[0] - m[0]) * k, m[1] + (wr[1] - m[1]) * k)
            body_pt = rot((S2[0] + 60, S2[1] + 175), S2, ang * 0.3, 1)
            mem = [S2, P["E"], wr, P["th"], wr, P["T1"], mid(P["T1"], P["T2"]), P["T2"],
                   mid(P["T2"], P["T3"]), P["T3"], mid(P["T3"], P["T4"]), P["T4"], body_pt]
            sp.poly(mem, c)
            bone = (c[0], c[1], c[2], 255)
            sp.line([S2, P["E"], wr], 14 * scale, bone)
            for k in ("T1", "T2", "T3", "T4"):
                sp.line([wr, P[k]], 6 * scale, bone)
            sp.line([wr, P["th"]], 8 * scale, bone)

        a = -0.05 * breathe - 0.32 * spread
        wing((-70, 15), a - 0.12, 0.92 + 0.1 * spread, (6, 3, 9, 250))
        # тело
        sp.ell(75, -95, 118, 98, col)
        sp.ell(-5, -160, 122, 108, col)
        sp.ell(-80, -225, 82, 108, col)
        # лапы
        sp.poly([(-150, 0), (-70, 0), (-62, -18), (-58, -150), (-128, -175), (-140, -60)], col)
        for cx in (-150, -128, -106):
            sp.poly([(cx, 0), (cx - 16, 4), (cx - 4, -10)], col)
        sp.poly([(10, 0), (150, 0), (175, -60), (120, -150), (40, -120)], col)
        # шея и голова
        hb = (-205 - 25 * bow, -520 + 150 * bow + 6 * breathe)
        neck = bezier((-95, -290), (-125 + 20 * bow, -440 + 60 * bow), hb, 26)
        sp.tube(neck, 54, 30, col)
        for i in range(2, 24, 4):
            p, q = neck[i], neck[i + 1]
            dx, dy = q[0] - p[0], q[1] - p[1]
            nl = math.hypot(dx, dy) + 1e-6
            nx, ny = dy / nl, -dx / nl
            r = 54 + (30 - 54) * i / 25
            nx, ny = -nx, -ny
            base = (p[0] + nx * r * 0.85, p[1] + ny * r * 0.85)
            sp.poly([(base[0] - dx * 1.5, base[1] - dy * 1.5), (base[0] + nx * 18, base[1] + ny * 18),
                     (base[0] + dx * 1.5, base[1] + dy * 1.5)], col)
        hx, hy = hb
        tilt = -0.15 + 0.55 * bow
        head = [(25, -22), (0, -34), (-40, -30), (-85, -18), (-128, -4), (-136, 8), (-122, 16), (-80, 22),
                (-40, 28), (0, 30), (26, 18)]
        horn1 = [(5, -28), (60, -82), (92, -98), (40, -38)]
        horn2 = [(15, -16), (80, -48), (104, -50), (45, -4)]
        tr = lambda pts: [rot((hx + p[0], hy + p[1]), (hx, hy), tilt) for p in pts]
        sp.poly(tr(head), col)
        sp.poly(tr(horn1), col)
        sp.poly(tr(horn2), col)
        self.eye = rot((hx - 45, hy - 12), (hx, hy), tilt)
        # передняя (ближняя) крыло
        wing((0, 0), a, 1 + 0.12 * spread, colw)
        # яйцо
        egg = (-300, -40)
        if t < tc + 0.8:
            sp.ell(egg[0], egg[1], 30, 40, (18, 10, 12, 255))
        else:
            # малыш
            k = ease((t - tc - 0.8) / 1.2)
            sp.ell(egg[0], egg[1] + 12, 30, 28, (18, 10, 12, 255))
            bx, by = egg[0] + 2, egg[1] - 10 - 22 * k
            sp.ell(bx, by, 17, 15, (14, 8, 12, 255))
            sp.poly([(bx - 8, by - 6), (bx - 34, by + 2), (bx - 8, by + 8)], (14, 8, 12, 255))
            self.baby_eye = (bx - 8, by - 3)
        self.egg = egg
        return sp

    def dynamic(self, img, t, cam):
        sp = self.draw_dragon(t, cam)
        self.origin = (sx(self.ox, 1.0, cam), self.ground)
        sp.paste_on(img, *self.origin)

    def light(self, L, t, cam):
        tc = self.ev["climax"]
        ox, oy = self.origin
        # угли
        for x0, y0, sp, ph, sz in self.embers:
            y = H + 40 - ((y0 * 2200 + t * (50 + 110 * sp)) % 2200)
            x = x0 * W + 40 * math.sin(t * (0.6 + sp) + ph * 6) + (y - H) * 0.05
            fl = 0.55 + 0.45 * math.sin(t * 7 * (0.5 + ph) + ph * 20)
            add_glow(L, x, y, 5 + sz * 10, (1.0, 0.5, 0.18), 0.55 * fl * (0.5 + 0.5 * (y / H)))
        # глаз
        ex, ey = self.eye
        add_glow(L, ox + ex, oy + ey, 14, (1.0, 0.6, 0.15), 0.9)
        # яйцо: сердцебиение
        gx, gy = ox + self.egg[0], oy + self.egg[1]
        beat = (math.sin(t * 2 * math.pi / 1.3) * 0.5 + 0.5) ** 4
        base = 0.25 + 0.5 * ease((t - (tc - 6)) / 6)
        add_glow(L, gx, gy, 70, (1.0, 0.65, 0.3), base * (0.5 + 0.8 * beat))
        if t > tc:
            dt = t - tc
            add_glow(L, gx, gy, 520, (1.0, 0.8, 0.5), 1.6 * math.exp(-dt * 1.3))
            add_glow(L, gx, gy, 160, (1.0, 0.85, 0.55), 0.8 + 0.3 * math.sin(t * 3))
            rng = np.random.default_rng(3)
            for i in range(60):
                a, v, s0 = rng.random() * 6.28, 40 + rng.random() * 160, rng.random() * 3
                tt = (dt + s0) % 3.0
                x = gx + math.cos(a) * v * tt * 0.6
                y = gy - v * tt + 20 * math.sin(a * 3 + tt)
                add_glow(L, x, y, 7, (1.0, 0.85, 0.5), 0.7 * (1 - tt / 3) * min(1, dt * 2))
            if hasattr(self, "baby_eye"):
                bx, by = self.baby_eye
                add_glow(L, ox + bx, oy + by, 8, (0.5, 0.9, 1.0), ease((dt - 1.4) / 0.6))
        # слеза
        tt = t - self.ev.get("tear", 99)
        if 0 < tt < 2.5:
            add_glow(L, ox + ex + 6, oy + ey + 10 + tt * 55, 10, (0.8, 0.9, 1.0), 0.9 * (1 - tt / 2.5))

    def grade(self, arr, t):
        return arr * np.array([1.04, 0.98, 1.0], np.float32)


# =====================================================================
# 2. Та, что зажигает звёзды
# =====================================================================
class StarsScene(Scene):
    caption_y = 1470

    def __init__(self, ev):
        self.ev = ev
        rng = np.random.default_rng(22)
        sky = sky_gradient([(0, "#02030d"), (0.35, "#0a1436"), (0.58, "#1a3163"), (0.7, "#35578a"),
                            (0.76, "#6c86ad"), (1, "#0b1428")])
        # млечный путь
        Y, X = np.mgrid[0:H, 0:WW].astype(np.float32)
        p0, p1 = np.array([-100, 250.]), np.array([WW + 100, 1050.])
        dvec = (p1 - p0) / np.linalg.norm(p1 - p0)
        dist = np.abs((X - p0[0]) * dvec[1] - (Y - p0[1]) * dvec[0])
        band = np.exp(-(dist / 170) ** 2)
        mw = smooth_noise(WW, 9, 6, 6)[None, :]
        sky += band[..., None] * (0.6 + 0.6 * mw)[..., None] * np.array([60, 50, 95], np.float32)
        del Y, X, dist
        for _ in range(2500):
            s = rng.random() * 1.0
            x = p0[0] + (p1[0] - p0[0]) * s + rng.normal(0, 110) * dvec[1]
            y = p0[1] + (p1[1] - p0[1]) * s - rng.normal(0, 110) * dvec[0]
            xi, yi = int(x), int(y)
            if 0 <= xi < WW and 0 <= yi < H:
                sky[yi, xi] = np.maximum(sky[yi, xi], 90 + rng.random() * 140)
        bake_stars(sky, rng, 1100, 1300)
        sky_img = to_rgba_img(sky)
        far = ridge_layer(1330 - 200 * smooth_noise(WW, 4, 4, 2), "#1a2d58", "#0c1834", rim=(80, 110, 170), rim_w=5,
                          haze="#4a6a9a", haze_amt=0.4)
        hill_y = (1360 + ((WX - 560) / 560) ** 2 * 260 + 20 * smooth_noise(WW, 6, 5, 8)).astype(np.float32)
        hill = ridge_layer(hill_y, "#070b1c", "#03050c", rim=(90, 120, 190), rim_w=3, depth=300)
        # трава на гребне
        d = ImageDraw.Draw(hill)
        for _ in range(900):
            x = rng.random() * WW
            y = hill_y[int(x)] + 3
            hgt = 8 + rng.random() * 26
            lean = rng.normal(0, 6)
            d.line([(x, y), (x + lean, y - hgt)], fill=(5, 8, 18, 255), width=2)
        self.hill_y = hill_y
        self.fog = fog_texture(8, 400, "#6f88b8", 0.35)
        self.layers = [(sky_img, 0.06, 0), (far, 0.3, 0), (self.fog, 0.5, 1150), (hill, 1.0, 0)]
        self.gx = 560
        self.gy = float(hill_y[560 + PAD]) + 2
        r = np.random.default_rng(7)
        self.twinkle = [(r.random() * W, r.random() ** 1.4 * 1150, r.random() * 6, 2 + r.random() * 5) for _ in range(160)]
        self.dust = r.random((50, 4))

    def dynamic(self, img, t, cam):
        wind = math.sin(t * 1.3) * 0.5 + 0.5
        col = (4, 6, 14, 255)
        sp = Sprite(420, 460, 210, 420)
        # платье
        hem = [(62 + 8 * wind, 0), (40, 4), (0, 6), (-40, 4), (-54 + 6 * wind, -2)]
        sp.poly([(-12, -262), (12, -262), (20, -215), (16, -185)] + hem + [(-18, -185), (-22, -215)], col)
        # ноги
        sp.line([(-12, 0), (-14, 14)], 8, col)
        sp.line([(12, 0), (14, 14)], 8, col)
        # опущенная рука
        sp.line([(-14, -250), (-26, -190), (-24, -150)], 9, col)
        # поднятая рука с фонарём
        hand = (70, -352)
        sp.line([(12, -252), (46, -300), hand], 9, col)
        # голова и волосы
        sp.ell(4, -292, 24, 27, col)
        hair = [(-14, -312), (10, -318), (22, -300), (8, -282), (-6, -262), (-40 - 25 * wind, -238),
                (-70 - 30 * wind, -222 + 10 * math.sin(t * 2.1)), (-40 - 10 * wind, -262), (-24, -290)]
        sp.poly(hair, col)
        # фонарь
        sp.line([hand, (72, -326)], 2, (30, 25, 20, 255))
        sp.poly([(62, -326), (82, -326), (84, -296), (60, -296)], (255, 190, 110, 255))
        sp.line([(58, -296), (86, -296)], 3, (30, 25, 20, 255))
        sp.line([(60, -328), (84, -328)], 3, (30, 25, 20, 255))
        self.lantern = (72, -311)
        self.origin = (sx(self.gx, 1.0, cam), self.gy)
        sp.paste_on(img, *self.origin)

    def light(self, L, t, cam):
        tc = self.ev["climax"]
        th = self.ev.get("hug", tc + 2.6)
        for x, y, ph, sz in self.twinkle:
            b = (0.5 + 0.5 * math.sin(t * (1.5 + ph * 0.4) + ph * 9)) ** 3
            add_glow(L, sx(x, 0.06, cam), y, sz, (0.85, 0.9, 1.0), 0.25 + 0.6 * b)
        ox, oy = self.origin
        lx, ly = ox + self.lantern[0], oy + self.lantern[1]
        fl = 0.85 + 0.1 * math.sin(t * 9) + 0.05 * math.sin(t * 23)
        add_glow(L, lx, ly, 40, (1.0, 0.8, 0.5), 1.0 * fl)
        add_glow(L, lx, ly, 180, (1.0, 0.65, 0.3), 0.45 * fl)
        add_glow(L, lx, ly + 150, 520, (1.0, 0.55, 0.25), 0.12 * fl)
        # падающая звезда
        for ts, (x0, y0) in ((5.0, (900, 160)), (13.5, (300, 120))):
            dt = t - ts
            if 0 < dt < 0.9:
                for k in range(12):
                    tt = dt - k * 0.02
                    if tt > 0:
                        add_glow(L, x0 - tt * 700 + (0 if x0 > 500 else tt * 1100), y0 + tt * 380, 6,
                                 (0.9, 0.95, 1.0), (1 - k / 12) * (1 - dt / 0.9))
        # парящие искры
        for x0, y0, s, ph in self.dust:
            y = oy - 40 - ((y0 * 600 + t * 18 * (0.5 + s)) % 600)
            x = ox + (x0 - 0.5) * 700 + 30 * math.sin(t * 0.7 + ph * 9)
            add_glow(L, x, y, 5, (1.0, 0.8, 0.5), 0.35 * (0.5 + 0.5 * math.sin(t * 2 + ph * 7)))
        # звезда спускается
        dt = t - tc
        if dt > -0.8:
            p0, p1, p2 = (860, 240), (980, 800), (lx + 20, ly - 20)
            u = ease((dt + 0.8) / 3.2)
            pts = bezier(p0, p1, p2, 80)
            idx = int(u * 79)
            for k in range(0, 30):
                j = idx - k
                if j < 0:
                    break
                px, py = pts[j]
                add_glow(L, px, py, 10 - k * 0.2, (0.8, 0.9, 1.0), (1 - k / 30) * 0.8 * min(1, dt + 0.8))
            px, py = pts[idx]
            add_glow(L, px, py, 60, (0.85, 0.92, 1.0), 1.2)
            add_glow(L, px, py, 200, (0.6, 0.75, 1.0), 0.35)
        dh = t - th
        if dh > 0:
            k = ease(dh / 1.5)
            add_glow(L, ox, oy - 160, int(140 + 120 * k), (0.9, 0.9, 1.0), 0.45 * k)
            add_glow(L, ox, oy - 160, 600, (0.6, 0.7, 1.0), 0.18 * k)
            for i in range(24):
                a = i / 24 * 6.28 + dh * 0.9
                rr = 150 + 30 * math.sin(dh * 2 + i)
                add_glow(L, ox + math.cos(a) * rr, oy - 160 + math.sin(a) * rr * 1.3, 6, (1, 0.95, 0.85), 0.8 * k)

    def grade(self, arr, t):
        return arr * np.array([0.98, 1.0, 1.06], np.float32)


# =====================================================================
# 3. Рыцарь, который вернулся
# =====================================================================
class KnightScene(Scene):
    caption_y = 640

    def __init__(self, ev):
        self.ev = ev
        rng = np.random.default_rng(33)
        sky = sky_gradient([(0, "#1a0d2e"), (0.28, "#43204e"), (0.46, "#93405e"), (0.58, "#df7050"),
                            (0.67, "#ffb36b"), (0.71, "#ffd79a"), (1, "#3a1a2a")])
        self.sun = (420 + PAD, 1300)
        add_radial(sky, *self.sun, 900, (255, 170, 90), 2.0, 0.55)
        add_radial(sky, *self.sun, 300, (255, 230, 170), 2.0, 0.8)
        disc(sky, *self.sun, 95, (255, 244, 210), 3)
        # облака-полосы
        cl = Image.new("L", (WW // 2, H // 2), 0)
        d = ImageDraw.Draw(cl)
        for _ in range(26):
            x, y = rng.random() * WW / 2, 180 + rng.random() * 420
            rx, ry = 80 + rng.random() * 200, 4 + rng.random() * 10
            d.ellipse([x - rx, y - ry, x + rx, y + ry], fill=int(90 + rng.random() * 140))
        cl = np.asarray(cl.filter(ImageFilter.GaussianBlur(6)).resize((WW, H)), np.float32)[..., None] / 255
        yy = np.linspace(0, 1, H)[:, None, None]
        ccol = np.array([255, 150, 140], np.float32) * (0.5 + 0.8 * yy)
        sky = sky * (1 - cl * 0.7) + ccol * cl * 0.7
        sky_img = to_rgba_img(sky)
        far = ridge_layer(1390 - 170 * smooth_noise(WW, 12, 5, 3), "#b05a72", "#80405a", haze="#ffb080", haze_amt=0.55)
        mid_y = (1470 - 110 * smooth_noise(WW, 13, 5, 2)).astype(np.float32)
        mid = ridge_layer(mid_y, "#4a2040", "#26102a", rim=(255, 150, 100), rim_w=3)
        # руины замка на среднем плане
        cast = Image.new("RGBA", (WW * 2, H * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(cast)
        c = (58, 26, 50, 255)
        towers = [(120, 60, 330), (210, 46, 230), (300, 70, 390), (380, 40, 180), (440, 55, 260)]
        base = float(mid_y[int(260 + PAD)]) + 10
        for x, w, h in towers:
            X = (x + PAD) * 2
            top = (base - h) * 2
            jag = [(X - w, top + rng.random() * 30)]
            for k in range(1, 6):
                jag.append((X - w + k * w * 2 / 6, top + rng.random() * 60 - (25 if k % 2 else 0)))
            jag.append((X + w, top + rng.random() * 40))
            d.polygon([(X - w, base * 2 + 40)] + jag + [(X + w, base * 2 + 40)], fill=c)
            for wy in range(int(top + 60), int(base * 2 - 60), 110):
                if rng.random() < 0.6:
                    d.rectangle([X - 8, wy, X + 8, wy + 36], fill=(0, 0, 0, 0))
                    d.ellipse([X - 8, wy - 8, X + 8, wy + 8], fill=(0, 0, 0, 0))
        d.rectangle([(90 + PAD) * 2, (base - 120) * 2, (470 + PAD) * 2, base * 2 + 40], fill=c)
        for k in range(12):
            x0 = (95 + PAD + k * 31) * 2
            d.rectangle([x0, (base - 120) * 2 - rng.random() * 40, x0 + 30, (base - 120) * 2 + 10], fill=(0, 0, 0, 0))
        cast = rim_from_alpha(cast.reduce(2), (255, 160, 110), 3, 0.7)
        fg_y = (1590 - 50 * smooth_noise(WW, 14, 5, 3)).astype(np.float32)
        fg = ridge_layer(fg_y, "#221020", "#0c050c", rim=(255, 140, 110), rim_w=2.5, depth=300)
        self.fg_y = fg_y
        # сакура
        tree = Image.new("RGBA", (WW * 2, H * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(tree)
        tx = 830 + PAD
        ty = float(fg_y[830 + PAD]) + 8
        blossoms = []

        def branch(x, y, ang, ln, th, depth):
            x2, y2 = x + math.cos(ang) * ln, y + math.sin(ang) * ln
            d.line([(x * 2, y * 2), (x2 * 2, y2 * 2)], fill=(34, 12, 26, 255), width=max(2, int(th * 2)))
            d.ellipse([x2 * 2 - th, y2 * 2 - th, x2 * 2 + th, y2 * 2 + th], fill=(34, 12, 26, 255))
            if depth == 0 or ln < 14:
                blossoms.append((x2, y2))
                return
            if depth <= 3:
                blossoms.append((x2, y2))
            n = 2 if rng.random() < 0.7 else 3
            for i in range(n):
                na = ang + (i - (n - 1) / 2) * (0.55 + rng.random() * 0.25) + rng.normal(0, 0.12)
                branch(x2, y2, na, ln * (0.68 + rng.random() * 0.15), th * 0.66, depth - 1)
        branch(tx, ty, -math.pi / 2 - 0.12, 230, 34, 8)
        pal = [(255, 183, 206), (246, 140, 180), (255, 214, 226), (230, 110, 160), (255, 160, 190)]
        for bx, by in blossoms:
            for _ in range(7):
                cx, cy = bx + rng.normal(0, 26), by + rng.normal(0, 20)
                r = 8 + rng.random() * 20
                light_side = 1.0 if cx < tx else 0.8
                c = np.array(pal[rng.integers(len(pal))]) * (light_side * (0.75 + 0.25 * rng.random()))
                d.ellipse([(cx - r) * 2, (cy - r) * 2, (cx + r) * 2, (cy + r) * 2],
                          fill=tuple(int(v) for v in np.clip(c, 0, 255)) + (235,))
        tree = rim_from_alpha(tree.reduce(2), (255, 200, 150), 3, 0.35)
        self.tree_top = min(b[1] for b in blossoms)
        # надгробие
        self.stone = (720, float(fg_y[720 + PAD]) + 6)
        sx_, sy_ = self.stone
        d2 = ImageDraw.Draw(fg)
        d2.rounded_rectangle([sx_ + PAD - 32, sy_ - 86, sx_ + PAD + 32, sy_ + 6], 26, fill=(30, 16, 28, 255))
        self.fog = fog_texture(9, 360, "#ffb090", 0.28)
        self.layers = [(sky_img, 0.08, 0), (far, 0.25, 0), (mid, 0.5, 0), (cast, 0.5, 0), (self.fog, 0.6, 1300),
                       (fg, 1.0, 0), (tree, 1.0, 0)]
        self.kx = 470
        self.ky = float(fg_y[470 + PAD]) + 6
        r = np.random.default_rng(4)
        self.petals = r.random((90, 5))
        self.dust = r.random((60, 4))

    def knight(self, t, sitting, helm):
        col = (14, 6, 12, 255)
        sp = Sprite(420, 520, 200, 470)
        wv = math.sin(t * 2.2)
        dy = 95 if sitting else 0
        # плащ
        if not sitting:
            cape = [(-18, -300), (18, -300), (-10, -200), (-40, -60), (-90 - 12 * wv, -8), (-120 - 18 * wv, 2),
                    (-70, -120), (-40, -230)]
        else:
            cape = [(-18, -300 + dy), (18, -300 + dy), (-15, -170 + dy), (-80 - 10 * wv, -95 + dy),
                    (-140 - 15 * wv, -86 + dy), (-80, -170 + dy)]
        sp.poly(cape, col)
        if not sitting:
            sp.poly([(-26, -170), (26, -170), (22, -100), (28, 0), (8, 0), (0, -95), (-8, 0), (-28, 0), (-22, -100)], col)
        else:
            # ноги вытянуты вперёд
            sp.poly([(-28, -80), (28, -80), (100, -70), (110, -40), (125, 0), (100, 0), (85, -40), (10, -35), (-30, -40)], col)
        # торс
        sp.poly([(-30, -300 + dy), (30, -300 + dy), (34, -240 + dy), (24, -165 + dy), (-24, -165 + dy), (-34, -240 + dy)], col)
        sp.ell(-30, -292 + dy, 24, 18, col)
        sp.ell(30, -292 + dy, 24, 18, col)
        # руки
        if not sitting:
            sp.line([(30, -285), (50, -220), (62, -175)], 16, col)
            sp.line([(-30, -285), (-20, -220), (40, -180)], 16, col)
        else:
            sp.line([(30, -285 + dy), (55, -230 + dy), (80, -200 + dy)], 16, col)
            sp.line([(-30, -285 + dy), (-20, -220 + dy), (20, -200 + dy)], 16, col)
        # голова: шлем или голова с волосами
        hy = -335 + dy
        bowed = 8
        if helm:
            sp.poly([(-24, hy - 30), (22, hy - 30), (30 + bowed, hy + 10), (26 + bowed, hy + 28), (-22, hy + 28)], col)
            sp.poly([(-4, hy - 30), (4, hy - 30), (-30, hy - 70 - 6 * wv), (-60, hy - 60 - 8 * wv)], col)
        else:
            sp.ell(6, hy, 23, 26, col)
            sp.poly([(-14, hy - 20), (10, hy - 28), (26, hy - 12), (-30 - 12 * wv, hy + 30), (-48 - 14 * wv, hy + 38)], col)
        # меч
        if not sitting:
            sp.line([(70, -200), (70, 8)], 7, col)
            sp.line([(52, -165), (88, -165)], 8, col)
            sp.ell(70, -200, 7, 7, col)
        else:
            sp.line([(170, -150 + dy - 60), (230, 0)], 7, col)
            if not helm:
                sp.poly([(40, -40), (82, -40), (86, -8), (36, -8)], col)  # шлем на земле
        return sp

    def dynamic(self, img, t, cam):
        t_sit = self.ev.get("sit", 99)
        t_helm = self.ev.get("helm", 99)
        helm = t < t_helm
        k = ease((t - t_sit) / 0.5)
        ox = sx(self.kx, 1.0, cam)
        if k <= 0 or k >= 1:
            self.knight(t, k >= 1, helm).paste_on(img, ox + (60 if k >= 1 else 0), self.ky)
        else:
            a = self.knight(t, False, helm).done()
            b = self.knight(t, True, helm).done()
            tmp = Image.new("RGBA", (a.width + 60, a.height), (0, 0, 0, 0))
            aa = np.asarray(a).copy()
            aa[..., 3] = (aa[..., 3] * (1 - k)).astype(np.uint8)
            bb = np.asarray(b).copy()
            bb[..., 3] = (bb[..., 3] * k).astype(np.uint8)
            tmp.alpha_composite(Image.fromarray(aa, "RGBA"), (0, 0))
            tmp.alpha_composite(Image.fromarray(bb, "RGBA"), (60, 0))
            img.alpha_composite(tmp, (int(ox - 200), int(self.ky - 470)))
        # лепестки
        d = ImageDraw.Draw(img)
        tc = self.ev["climax"]
        gust = 1 + 1.5 * ease((t - tc) / 1.0) * math.exp(-max(0, t - tc - 1) * 0.4)
        for x0, y0, s, ph, c in self.petals:
            span = H + 200
            prog = (y0 * span + t * (60 + 70 * s) * gust) % span
            y = prog - 100
            x = (x0 * (W + 400) - t * (40 + 50 * s) * gust - prog * 0.25) % (W + 400) - 200
            x += 25 * math.sin(t * 1.5 + ph * 9)
            rr = 5 + 5 * s
            sq = abs(math.cos(t * 3 * (0.5 + s) + ph * 5))
            col = (255, int(160 + 50 * c), int(190 + 30 * c), 255)
            d.ellipse([x - rr, y - rr * (0.3 + 0.6 * sq), x + rr, y + rr * (0.3 + 0.6 * sq)], fill=col)

    def light(self, L, t, cam):
        tc = self.ev["climax"]
        sx0 = sx(self.sun[0] - PAD, 0.08, cam)
        add_glow(L, sx0, self.sun[1], 260, (1.0, 0.75, 0.45), 0.25 + 0.05 * math.sin(t * 1.3)
                 + 0.35 * ease((t - (tc + 3)) / 4))
        for x0, y0, s, ph in self.dust:
            x = sx0 + (x0 - 0.5) * 1100 + 40 * math.sin(t * 0.5 + ph * 9)
            y = 1250 + (y0 - 0.5) * 700 - 15 * t * s
            add_glow(L, x, y, 4 + 3 * s, (1.0, 0.8, 0.5), 0.5 * (0.5 + 0.5 * math.sin(t * 2.5 + ph * 11)))
        st = self.stone
        if t > tc - 0.3:
            k = ease((t - tc + 0.3) / 1.2)
            add_glow(L, sx(st[0], 1.0, cam), st[1] - 50, 110, (1.0, 0.8, 0.55), 0.7 * k)
            add_glow(L, sx(st[0], 1.0, cam), st[1] - 50, 380, (1.0, 0.6, 0.4), 0.25 * k)

    def grade(self, arr, t):
        return arr * np.array([1.05, 0.98, 0.95], np.float32)


# =====================================================================
# 4. Дух леса
# =====================================================================
def pine(d, x, base, h, w, col, rng, ss=2):
    tiers = int(h / (w * 0.55)) + 3
    d.rectangle([(x - w * 0.06) * ss, (base - h * 0.2) * ss, (x + w * 0.06) * ss, base * ss], fill=col)
    for i in range(tiers):
        f = i / tiers
        ty = base - h * (0.15 + 0.85 * f)
        tw = w * (1 - f) * (0.9 + 0.2 * rng.random())
        th = h / tiers * 1.8
        d.polygon([(x * ss, (ty - th) * ss), ((x + tw) * ss, ty * ss), ((x - tw) * ss, ty * ss)], fill=col)
        for k in range(6):
            jx = x + tw * (rng.random() * 2 - 1)
            d.polygon([(jx * ss, ty * ss), ((jx - 10) * ss, (ty - 18) * ss), ((jx + 10) * ss, (ty + 6) * ss)], fill=col)


class ForestScene(Scene):
    def __init__(self, ev):
        self.ev = ev
        rng = np.random.default_rng(44)
        sky = sky_gradient([(0, "#030a0e"), (0.35, "#0a2026"), (0.65, "#123238"), (1, "#050c0e")])
        add_radial(sky, 620 + PAD, 200, 900, (60, 110, 110), 1.8, 0.6)
        add_radial(sky, 620 + PAD, 200, 200, (180, 230, 220), 2.0, 0.6)
        sky_img = to_rgba_img(sky)

        def pine_layer(seed, n, hmin, hmax, base, col, wmul, haze=None):
            r = np.random.default_rng(seed)
            im = Image.new("RGBA", (WW * 2, H * 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            xs = np.sort(r.random(n)) * WW
            for x in xs:
                h = hmin + r.random() * (hmax - hmin)
                b = base + r.random() * 30
                pine(d, x, b, h, h * wmul, col, r)
            d.rectangle([0, (base + 10) * 2, WW * 2, H * 2], fill=col)
            return im.reduce(2)
        far = pine_layer(1, 34, 500, 800, 1420, (22, 58, 62, 255), 0.2)
        mid = pine_layer(2, 20, 800, 1200, 1500, (10, 30, 34, 255), 0.22)
        near = Image.new("RGBA", (WW * 2, H * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(near)
        for x, w in ((-60 + PAD, 110), (30 + PAD, 60), (1010 + PAD, 90), (1150 + PAD, 140)):
            d.polygon([((x - w / 2) * 2, H * 2), ((x - w * 0.35) * 2, 0), ((x + w * 0.35) * 2, 0), ((x + w / 2) * 2, H * 2)],
                      fill=(2, 6, 7, 255))
            for k in range(5):
                by = rng.random() * 1200 + 200
                side = 1 if x < W / 2 + PAD else -1
                d.line([(x * 2, by * 2), ((x + side * (120 + rng.random() * 160)) * 2, (by - 60 - rng.random() * 80) * 2)],
                       fill=(2, 6, 7, 255), width=int(10 + rng.random() * 12))
        near = near.reduce(2)
        gy = (1590 - 40 * smooth_noise(WW, 15, 5, 4)).astype(np.float32)
        ground = ridge_layer(gy, "#06110f", "#020605", rim=(80, 160, 150), rim_w=2, depth=300)
        dg = ImageDraw.Draw(ground)
        for _ in range(1400):
            x = rng.random() * WW
            y = gy[int(x)] + 4
            hh = 10 + rng.random() * 34
            dg.line([(x, y), (x + rng.normal(0, 5), y - hh)], fill=(3, 9, 8, 255), width=2)
        self.gy = gy
        self.mush = [(x, float(gy[int(x + PAD)])) for x in rng.random(18) * W]
        self.fog1 = fog_texture(10, 500, "#4f8a88", 0.45)
        self.fog2 = fog_texture(11, 500, "#3a6e70", 0.5)
        # лучи
        rays = Image.new("L", (W // 2, H // 2), 0)
        dr = ImageDraw.Draw(rays)
        for k in range(7):
            x0 = (400 + k * 90 + rng.normal(0, 30)) / 2
            w = (20 + rng.random() * 40) / 2
            dr.polygon([(x0 - w, 0), (x0 + w, 0), (x0 - 330 / 2 + w * 3, H / 2), (x0 - 330 / 2 - w * 2, H / 2)],
                       fill=int(80 + rng.random() * 120))
        rays = rays.filter(ImageFilter.GaussianBlur(14)).resize((W, H))
        yy = np.linspace(1, 0.2, H)[:, None]
        self.rays = (np.asarray(rays, np.float32) / 255 * yy)[..., None] * np.array([0.5, 0.9, 0.85], np.float32)
        self.layers = [(sky_img, 0.05, 0), (far, 0.25, 0), (self.fog1, 0.4, 1000), (mid, 0.5, 0),
                       (self.fog2, 0.7, 1250), (ground, 1.0, 0), (near, 1.35, 0)]
        self.bx = 470
        self.by = float(gy[470 + PAD]) + 4
        r = np.random.default_rng(6)
        self.flies = r.random((380, 6))

    def dynamic(self, img, t, cam):
        col = (2, 5, 5, 255)
        sp = Sprite(300, 300, 150, 260)
        # мальчик на коленях, лицом вправо
        sp.poly([(-40, 0), (30, 0), (34, -14), (-10, -20), (-30, -60), (-50, -30)], col)  # ноги
        sp.poly([(-44, -60), (-24, -120), (10, -140), (22, -118), (-4, -80), (-18, -50)], col)  # спина
        sp.ell(26, -140, 19, 20, col)
        sp.poly([(10, -156), (30, -162), (44, -150), (18, -148)], col)
        sp.line([(6, -122), (40, -80), (70, -20)], 10, col)
        sp.line([(-2, -110), (30, -70), (54, -40)], 10, col)
        tb = self.ev.get("believe", 99)
        g = ease((t - tb) / 3.0)
        # росток
        stem_h = 12 + 34 * g
        sp.line([(86, 0), (86, -stem_h)], 3, (20, 70, 40, 255))
        sp.poly([(86, -stem_h), (104, -stem_h - 10 - 6 * g), (92, -stem_h + 2)], (30, 110, 60, 255))
        sp.poly([(86, -stem_h + 4), (70, -stem_h - 4 - 5 * g), (82, -stem_h + 6)], (30, 110, 60, 255))
        self.sprout = (86, -stem_h)
        self.origin = (sx(self.bx, 1.0, cam), self.by)
        sp.paste_on(img, *self.origin)

    def light(self, L, t, cam):
        tb = self.ev.get("believe", 99)
        tc = self.ev["climax"]
        gb = ease((t - tb) / 2.5)
        gc = ease((t - tc + 0.4) / 1.6)
        ox, oy = self.origin
        # дух-огонёк
        weak = 0.35 + 0.25 * (math.sin(t * 13) * math.sin(t * 5.3) > 0.3)
        k = weak * (1 - gb) + 1.2 * gb
        a = t * 0.9
        px = ox + 80 + math.cos(a) * 70 + 20 * math.sin(t * 2.3)
        py = oy - 140 + math.sin(a * 1.3) * 40 - 60 * gb
        for j in range(10):
            ta = a - j * 0.06
            add_glow(L, ox + 80 + math.cos(ta) * 70 + 20 * math.sin((t - j * 0.06) * 2.3),
                     oy - 140 + math.sin(ta * 1.3) * 40 - 60 * gb, 6, (0.6, 1.0, 0.9), 0.3 * k * (1 - j / 10))
        add_glow(L, px, py, 16, (0.8, 1.0, 0.95), 1.2 * k)
        add_glow(L, px, py, 90, (0.4, 1.0, 0.85), 0.35 * k)
        spx, spy = ox + self.sprout[0], oy + self.sprout[1]
        add_glow(L, spx, spy, 30, (0.5, 1.0, 0.6), 0.2 + 0.8 * gb)
        # светлячки
        n_active = int(14 + 366 * gc)
        for i, (x0, y0, s, ph, c, st) in enumerate(self.flies):
            if i >= n_active:
                break
            appear = 1.0 if i < 14 else ease((t - tc + 0.4 - st * 1.4) / 0.6)
            if appear <= 0:
                continue
            x = sx(x0 * (W + 300) - 150 + 60 * math.sin(t * 0.4 * (0.5 + s) + ph * 9), 0.3 + 0.7 * s, cam)
            y = 300 + y0 * 1350 + 40 * math.sin(t * 0.5 * (0.5 + c) + ph * 5) - 10 * t * s
            bl = (0.5 + 0.5 * math.sin(t * (1.5 + 2 * s) + ph * 20)) ** 2
            col = (0.75, 1.0, 0.4) if c < 0.6 else (0.4, 1.0, 0.9)
            add_glow(L, x, y, int(4 + 6 * s), col, (0.3 + 0.9 * bl) * appear * (0.6 + 0.4 * s))
        for x, y in self.mush:
            add_glow(L, sx(x, 1.0, cam), y - 4, 26, (0.3, 0.9, 1.0), 0.15 + 0.9 * gc)
        # лучи
        rk = 0.18 + 0.06 * math.sin(t * 0.7) + 0.35 * gc
        L += self.rays * (255 * rk)

    def grade(self, arr, t):
        gc = ease((t - self.ev["climax"] + 0.4) / 2.0)
        return arr * (np.array([0.95, 1.02, 1.03], np.float32) * (1 + 0.15 * gc))


# =====================================================================
# 5. Ледяное сердце
# =====================================================================
class IceScene(Scene):
    def __init__(self, ev):
        self.ev = ev
        rng = np.random.default_rng(55)
        sky = sky_gradient([(0, "#01030a"), (0.32, "#051230"), (0.55, "#0e2752"), (0.7, "#23457a"),
                            (0.76, "#4a6c9c"), (1, "#0a1830")])
        bake_stars(sky, rng, 900, 1200)
        sky_img = to_rgba_img(sky)
        far = ridge_layer(1300 - 420 * smooth_noise(WW, 31, 6, 3), "#a8bedc", "#4d6690", rim=(255, 255, 255), rim_w=4,
                          depth=700, haze="#6a86b4", haze_amt=0.3)
        mid = ridge_layer(1440 - 200 * smooth_noise(WW, 32, 6, 4), "#5a7aa8", "#253a60", rim=(220, 240, 255), rim_w=3,
                          depth=500)
        # ледяной замок
        cast = Image.new("RGBA", (WW * 2, H * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(cast)
        base = 1440
        self.cbase = base
        towers = [(540, 58, 640), (430, 40, 450), (650, 40, 470), (355, 30, 320), (725, 32, 340), (290, 22, 220),
                  (790, 22, 240)]
        towers.sort(key=lambda z: z[2])
        self.windows = []
        for x, w, h in towers:
            X = x + PAD
            top = base - h
            for k in range(int(w * 2)):
                u = k / (w * 2)
                c = np.array([205, 232, 252]) * (1 - u) + np.array([95, 135, 190]) * u
                d.line([((X - w + k) * 2, (top + 60) * 2), ((X - w + k) * 2, base * 2)], fill=tuple(int(v) for v in c) + (255,), width=2)
            d.polygon([((X - w - 6) * 2, (top + 64) * 2), (X * 2, (top - h * 0.28) * 2), ((X + w + 6) * 2, (top + 64) * 2)],
                      fill=(170, 205, 240, 255))
            d.polygon([(X * 2, (top - h * 0.28) * 2), ((X + w + 6) * 2, (top + 64) * 2), (X * 2, (top + 64) * 2)],
                      fill=(110, 150, 205, 255))
            for wy in range(int(top + 110), base - 120, 90):
                self.windows.append((x, wy))
                d.rounded_rectangle([(X - 6) * 2, wy * 2, (X + 6) * 2, (wy + 26) * 2], 10, fill=(20, 40, 70, 255))
        d.rectangle([(300 + PAD) * 2, (base - 120) * 2, (780 + PAD) * 2, (base + 30) * 2], fill=(120, 160, 210, 255))
        for k in range(16):
            x0 = (300 + PAD + k * 30) * 2
            d.rectangle([x0, (base - 140) * 2, x0 + 30, (base - 118) * 2], fill=(150, 190, 230, 255))
        d.rounded_rectangle([(515 + PAD) * 2, (base - 95) * 2, (565 + PAD) * 2, (base + 30) * 2], 50, fill=(15, 30, 55, 255))
        self.gate = (540, base - 35)
        cast = rim_from_alpha(cast.reduce(2), (230, 250, 255), 3, 0.5)
        sn_y = (1490 - 60 * smooth_noise(WW, 34, 4, 3)).astype(np.float32)
        snow = ridge_layer(sn_y, "#a9bfdf", "#3d5580", rim=(255, 255, 255), rim_w=3, depth=420)
        self.fog = fog_texture(12, 400, "#c0d6f0", 0.35)
        self.layers = [(sky_img, 0.05, 0), (far, 0.2, 0), (mid, 0.4, 0), (cast, 0.55, 0), (self.fog, 0.7, 1250),
                       (snow, 1.0, 0)]
        self.sn_y = sn_y
        r = np.random.default_rng(8)
        self.flakes = r.random((260, 5))
        self.sparkle = [(r.random() * W, 1500 + r.random() * 400, r.random() * 6) for _ in range(80)]
        self.aur_seed = r.random(8) * 6.28

    def fox(self, sp, x, y, s, t, col):
        step = math.sin(t * 6) * 4 * s
        sp.ell(x, y - 26 * s, 34 * s, 16 * s, col)
        sp.poly([(x + 26 * s, y - 34 * s), (x + 56 * s, y - 46 * s), (x + 70 * s, y - 34 * s), (x + 44 * s, y - 24 * s)], col)
        sp.poly([(x + 46 * s, y - 44 * s), (x + 48 * s, y - 64 * s), (x + 56 * s, y - 46 * s)], col)
        sp.poly([(x + 52 * s, y - 44 * s), (x + 58 * s, y - 62 * s), (x + 62 * s, y - 42 * s)], col)
        sp.poly([(x - 30 * s, y - 30 * s), (x - 80 * s, y - 50 * s + step), (x - 95 * s, y - 34 * s + step),
                 (x - 60 * s, y - 16 * s)], col)
        for lx, ph in ((-20, 0), (-10, 1), (18, 1), (26, 0)):
            o = step * (1 if ph else -1)
            sp.line([(x + lx * s, y - 20 * s), (x + lx * s + o, y)], 5 * s, col)

    def dynamic(self, img, t, cam):
        topen = self.ev.get("open", 99)
        # лисы идут к замку
        k = ease((t - 6) / 12)
        sp = Sprite(700, 260, 380, 200)
        col = (70, 40, 50, 255)
        walk = t if t < 18 else 18
        self.fox(sp, -150 + 200 * k, 0, 1.7, walk, col)
        self.fox(sp, -290 + 200 * k, 8, 1.0, walk * 1.3, col)
        yy = float(self.sn_y[int(560 + PAD)]) - 2
        sp.paste_on(img, sx(560, 1.0, cam), yy)
        # королева в воротах
        q = ease((t - topen) / 1.2)
        if q > 0:
            gx = sx(self.gate[0], 0.55, cam)
            qs = Sprite(80, 140, 40, 130)
            c = (235, 245, 255, int(255 * q))
            qs.poly([(-4, -84), (4, -84), (18, 0), (-18, 0)], c)
            qs.ell(0, -92, 7, 8, c)
            qs.poly([(-7, -100), (-5, -110), (0, -103), (5, -110), (7, -100)], (190, 225, 255, int(255 * q)))
            qs.paste_on(img, gx, self.cbase + 28)

    def aurora(self, t, inten):
        w, h = W // 8, H // 8
        x = np.arange(w, dtype=np.float32)[None, :]
        y = np.arange(h, dtype=np.float32)[:, None]
        out = np.zeros((h, w, 3), np.float32)
        cols = [(0.2, 1.0, 0.6), (0.3, 0.9, 1.0), (0.7, 0.4, 1.0)]
        for i, c in enumerate(cols):
            ph = self.aur_seed[i]
            yc = 55 + i * 18 + 14 * np.sin(x * (0.045 + 0.01 * i) + t * (0.25 + 0.05 * i) + ph) \
                + 6 * np.sin(x * 0.11 + t * 0.4 + ph * 2)
            dy = y - yc
            curtain = np.where(dy > 0, np.exp(-(dy / 5) ** 2), np.exp(dy / (22 + 8 * i)))
            streak = 0.55 + 0.45 * np.sin(x * 0.7 + np.sin(x * 0.13 + t * 0.6 + ph) * 4 + t * 0.8)
            out += curtain[..., None] * streak[..., None] * np.array(c, np.float32) * (1.0 - 0.25 * i)
        im = Image.fromarray(np.clip(out * 255 * inten, 0, 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
        return np.asarray(im, np.float32)

    def light(self, L, t, cam):
        tc = self.ev["climax"]
        topen = self.ev.get("open", 99)
        gc = ease((t - tc + 0.3) / 1.5)
        inten = 0.28 + 0.1 * math.sin(t * 0.5) + 0.25 * ease((t - topen) / 4) + 0.75 * gc + 0.5 * gc * math.exp(-max(0, t - tc) * 1.5)
        L += self.aurora(t, inten)
        warm = ease((t - topen) / 2.0)
        for x, y in self.windows:
            c = np.array([0.4, 0.8, 1.0]) * (1 - warm) + np.array([1.0, 0.75, 0.4]) * warm
            add_glow(L, sx(x, 0.55, cam), y + 13, 16, c, 0.45 + 0.4 * warm)
        gx = sx(self.gate[0], 0.55, cam)
        add_glow(L, gx, self.gate[1], 60, (1.0, 0.8, 0.5), 0.8 * warm)
        add_glow(L, gx, self.gate[1] + 40, 220, (1.0, 0.7, 0.4), 0.18 * warm)
        for x0, y0, s, ph, dr in self.flakes:
            depth = 0.3 + 0.7 * s
            y = (y0 * (H + 100) + t * (40 + 90 * depth)) % (H + 100) - 50
            x = (x0 * (W + 100) + 30 * math.sin(t * (0.5 + dr) + ph * 9) - t * 15 * depth) % (W + 100) - 50
            add_glow(L, x, y, int(2 + 5 * depth), (0.9, 0.95, 1.0), 0.35 + 0.45 * depth)
        for x, y, ph in self.sparkle:
            b = max(0, math.sin(t * 2.2 + ph * 7)) ** 8
            add_glow(L, sx(x, 1.0, cam), y, 6, (0.9, 0.95, 1.0), b * (0.8 + gc))

    def grade(self, arr, t):
        return arr * np.array([0.96, 1.0, 1.06], np.float32)


SCENES = {"01_dragon": DragonScene, "02_stars": StarsScene, "03_knight": KnightScene, "04_forest": ForestScene,
          "05_ice": IceScene}
