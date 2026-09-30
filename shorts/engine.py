"""Общий движок: небо, горы, туман, свечение, спрайты-силуэты, частицы, титры."""
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1080, 1920, 30
PAD = 160
WW = W + 2 * PAD
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"


def hexc(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def ease(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def smooth_noise(n, seed, octaves=5, base=3, persistence=0.5):
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, n)
    out = np.zeros(n)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        k = int(base * 2 ** o)
        pts = rng.random(k + 2)
        xk = x * k
        i = np.floor(xk).astype(int)
        f = xk - i
        w = (1 - np.cos(np.pi * f)) / 2
        out += amp * (pts[i] * (1 - w) + pts[i + 1] * w)
        tot += amp
        amp *= persistence
    return out / tot


def sky_gradient(stops, width=WW):
    ys = np.linspace(0, 1, H)
    pos = [s[0] for s in stops]
    cols = np.array([hexc(s[1]) for s in stops])
    col = np.stack([np.interp(ys, pos, cols[:, c]) for c in range(3)], 1)
    arr = np.repeat(col[:, None, :], width, 1)
    return arr.astype(np.float32)


def add_radial(arr, cx, cy, r, color, power=2.0, strength=1.0):
    """Мягкое радиальное свечение прямо в float-массив (x в координатах массива)."""
    h, w = arr.shape[:2]
    x0, x1 = max(0, int(cx - r)), min(w, int(cx + r))
    y0, y1 = max(0, int(cy - r)), min(h, int(cy + r))
    if x0 >= x1 or y0 >= y1:
        return
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r
    g = np.clip(1 - d, 0, 1) ** power * strength
    arr[y0:y1, x0:x1] += g[..., None] * np.asarray(color, np.float32)


def disc(arr, cx, cy, r, color, soft=1.5):
    h, w = arr.shape[:2]
    x0, x1 = max(0, int(cx - r - 3)), min(w, int(cx + r + 3))
    y0, y1 = max(0, int(cy - r - 3)), min(h, int(cy + r + 3))
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    a = np.clip((r - d) / soft, 0, 1)[..., None]
    arr[y0:y1, x0:x1] = arr[y0:y1, x0:x1] * (1 - a) + np.asarray(color, np.float32) * a


def bake_stars(arr, rng, n, ymax, dim_fn=None):
    h, w = arr.shape[:2]
    for _ in range(n):
        x, y = rng.random() * w, rng.random() ** 1.3 * ymax
        b = rng.random() ** 3 * 200 + 30
        if dim_fn:
            b *= dim_fn(x, y)
        c = np.array([0.85 + 0.15 * rng.random(), 0.9, 1.0]) * b
        if b > 150:
            add_radial(arr, x, y, 4, c * 0.5, 2)
        xi, yi = int(x), int(y)
        if 0 <= yi < h and 0 <= xi < w:
            arr[yi, xi] = np.maximum(arr[yi, xi], c)


def to_rgba_img(arr, alpha=None):
    a = np.clip(arr, 0, 255).astype(np.uint8)
    if alpha is None:
        alpha = np.full(a.shape[:2], 255, np.uint8)
    else:
        alpha = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    return Image.fromarray(np.dstack([a, alpha]), "RGBA")


def ridge_layer(ys, top, bot, rim=None, rim_w=5.0, depth=500.0, haze=None, haze_amt=0.0):
    """Слой-гряда по массиву высот ys (длина WW)."""
    Y = np.arange(H, dtype=np.float32)[:, None]
    dy = Y - ys[None, :]
    alpha = np.clip(dy + 0.5, 0, 1)
    g = np.clip(dy / depth, 0, 1)[..., None]
    col = hexc(top) * (1 - g) + hexc(bot) * g
    if rim is not None:
        r = np.exp(-np.clip(dy, 0, None) / rim_w)[..., None]
        col = col + np.asarray(rim, np.float32) * r
    if haze is not None:
        hz = np.clip(1 - dy / 250, 0, 1)[..., None] * haze_amt
        col = col * (1 - hz) + hexc(haze) * hz
    return to_rgba_img(col, alpha)


def fog_texture(seed, h, color, alpha=0.5, width=WW * 2, blobs=70):
    rng = np.random.default_rng(seed)
    im = Image.new("L", (width // 4, h // 4), 0)
    d = ImageDraw.Draw(im)
    for _ in range(blobs):
        x, y = rng.random() * width / 4, (0.3 + 0.4 * rng.random()) * h / 4
        rx, ry = (40 + rng.random() * 90), (6 + rng.random() * 14)
        d.ellipse([x - rx, y - ry, x + rx, y + ry], fill=int(120 + rng.random() * 135))
    im = im.filter(ImageFilter.GaussianBlur(10)).resize((width, h), Image.BILINEAR)
    a = np.asarray(im, np.float32) / 255 * alpha
    col = np.ones((h, width, 3), np.float32) * hexc(color)
    return to_rgba_img(col, a)


# ---------- glow sprites for the additive light layer ----------
_GLOW = {}


def glow_sprite(r):
    r = max(1, int(r))
    if r not in _GLOW:
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        d = np.sqrt(xx ** 2 + yy ** 2) / r
        core = np.exp(-(d * 3.2) ** 2)
        halo = np.clip(1 - d, 0, 1) ** 3 * 0.35
        _GLOW[r] = (core + halo).astype(np.float32)
    return _GLOW[r]


def add_glow(L, x, y, r, color, inten=1.0):
    if inten <= 0.003:
        return
    g = glow_sprite(r)
    r = g.shape[0] // 2
    xi, yi = int(x), int(y)
    x0, y0 = xi - r, yi - r
    x1, y1 = x0 + g.shape[1], y0 + g.shape[0]
    h, w = L.shape[:2]
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    L[cy0:cy1, cx0:cx1] += g[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0, None] * (
        np.asarray(color, np.float32) * (255 * inten))


# ---------- supersampled vector sprite ----------
class Sprite:
    def __init__(self, cw, ch, ax, ay, ss=2):
        self.ss, self.ax, self.ay, self.cw, self.ch = ss, ax, ay, cw, ch
        self.im = Image.new("RGBA", (cw * ss, ch * ss), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.im)

    def P(self, p):
        return ((p[0] + self.ax) * self.ss, (p[1] + self.ay) * self.ss)

    def poly(self, pts, col):
        self.d.polygon([self.P(p) for p in pts], fill=tuple(col))

    def ell(self, cx, cy, rx, ry, col):
        a, b = self.P((cx - rx, cy - ry)), self.P((cx + rx, cy + ry))
        self.d.ellipse([a, b], fill=tuple(col))

    def line(self, pts, w, col):
        self.d.line([self.P(p) for p in pts], fill=tuple(col), width=int(w * self.ss), joint="curve")
        for p in (pts[0], pts[-1]):
            self.ell(p[0], p[1], w / 2, w / 2, col)

    def tube(self, pts, r0, r1, col):
        n = len(pts)
        for i, p in enumerate(pts):
            r = r0 + (r1 - r0) * i / max(1, n - 1)
            self.ell(p[0], p[1], r, r, col)

    def done(self):
        return self.im.reduce(self.ss)

    def paste_on(self, img, sx, sy):
        s = self.done()
        img.alpha_composite(s, (int(sx - self.ax), int(sy - self.ay)))


def bezier(p0, p1, p2, n=30):
    t = np.linspace(0, 1, n)[:, None]
    p0, p1, p2 = map(np.asarray, (p0, p1, p2))
    return list(map(tuple, (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2))


def rot(p, c, a, s=1.0):
    x, y = p[0] - c[0], p[1] - c[1]
    ca, sa = math.cos(a), math.sin(a)
    return (c[0] + s * (x * ca - y * sa), c[1] + s * (x * sa + y * ca))


# ---------- captions ----------
class Captions:
    def __init__(self, text, words, offset, y=1390, size=74):
        self.font = ImageFont.truetype(FONT_BOLD, size)
        self.y = y
        self.chunks = self._chunk(text, words, offset)
        self.cache = {}

    @staticmethod
    def _chunk(text, words, offset):
        low = text.lower()
        cur = 0
        items = []
        for (s, e, w) in words:
            i = low.find(w.lower(), cur)
            punct = False
            if i >= 0:
                cur = i + len(w)
                punct = cur < len(text) and text[cur] in ".,:;!?—"
            items.append([s + offset, e + offset, w.upper(), punct])
        chunks, curc = [], []
        for it in items:
            curc.append(it)
            chars = sum(len(c[2]) for c in curc) + len(curc)
            if it[3] or len(curc) >= 3 or chars >= 17:
                chunks.append(curc)
                curc = []
        if curc:
            chunks.append(curc)
        out = []
        for i, c in enumerate(chunks):
            start = c[0][0]
            end = chunks[i + 1][0][0] if i + 1 < len(chunks) else c[-1][1] + 0.8
            end = min(end, c[-1][1] + 0.9)
            out.append((start, end, c))
        return out

    def _render(self, ci, wi):
        key = (ci, wi)
        if key in self.cache:
            return self.cache[key]
        _, _, words = self.chunks[ci]
        f = self.font
        space = f.getlength(" ")
        widths = [f.getlength(w[2]) for w in words]
        tw = sum(widths) + space * (len(words) - 1)
        scale = min(1.0, 980 / tw)
        pad = 30
        im = Image.new("RGBA", (int(tw + pad * 2), 150), (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d, ds = ImageDraw.Draw(im), ImageDraw.Draw(sh)
        x = pad
        for i, w in enumerate(words):
            col = (255, 214, 110, 255) if i == wi else (255, 255, 255, 255)
            ds.text((x + 4, 30 + 6), w[2], font=f, fill=(0, 0, 0, 200), stroke_width=8, stroke_fill=(0, 0, 0, 200))
            d.text((x, 30), w[2], font=f, fill=col, stroke_width=6, stroke_fill=(15, 8, 20, 255))
            x += widths[i] + space
        sh = sh.filter(ImageFilter.GaussianBlur(7))
        sh.alpha_composite(im)
        if scale < 1:
            sh = sh.resize((int(sh.width * scale), int(sh.height * scale)), Image.LANCZOS)
        self.cache[key] = sh
        return sh

    def draw(self, img, t):
        for ci, (s, e, words) in enumerate(self.chunks):
            if s - 0.02 <= t < e:
                wi = 0
                for k, w in enumerate(words):
                    if t >= w[0] - 0.02:
                        wi = k
                im = self._render(ci, wi)
                p = min(1.0, (t - s) / 0.12)
                sc = 0.86 + 0.14 * (1 - (1 - p) ** 3)
                if sc < 0.999:
                    im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.BILINEAR)
                if p < 1:
                    a = np.asarray(im).copy()
                    a[..., 3] = (a[..., 3] * p).astype(np.uint8)
                    im = Image.fromarray(a, "RGBA")
                img.alpha_composite(im, (int(W / 2 - im.width / 2), int(self.y - im.height / 2)))
                return


class Title:
    def __init__(self, text, y=360, size=88, color=(255, 222, 160)):
        f = ImageFont.truetype(FONT_SERIF, size)
        lines = self._wrap(text, f, 900)
        lh = int(size * 1.15)
        im = Image.new("RGBA", (W, lh * len(lines) + 120), (0, 0, 0, 0))
        glow = Image.new("RGBA", im.size, (0, 0, 0, 0))
        d, dg = ImageDraw.Draw(im), ImageDraw.Draw(glow)
        for i, ln in enumerate(lines):
            x = W / 2 - f.getlength(ln) / 2
            yy = 60 + i * lh
            dg.text((x, yy), ln, font=f, fill=(255, 170, 80, 255), stroke_width=10, stroke_fill=(255, 150, 60, 255))
            d.text((x, yy), ln, font=f, fill=color + (255,), stroke_width=3, stroke_fill=(40, 20, 10, 255))
        glow = glow.filter(ImageFilter.GaussianBlur(18))
        a = np.asarray(glow).copy()
        a[..., 3] = (a[..., 3] * 0.55).astype(np.uint8)
        glow = Image.fromarray(a, "RGBA")
        glow.alpha_composite(im)
        self.im, self.y = glow, y

    @staticmethod
    def _wrap(text, f, maxw):
        words, lines, cur = text.split(), [], ""
        for w in words:
            c = (cur + " " + w).strip()
            if f.getlength(c) > maxw and cur:
                lines.append(cur)
                cur = w
            else:
                cur = c
        lines.append(cur)
        return lines

    def draw(self, img, t, t_in=0.3, t_out=3.4):
        if t > t_out + 0.6:
            return
        a = ease((t - t_in) / 0.6) * (1 - ease((t - t_out) / 0.6))
        if a <= 0:
            return
        arr = np.asarray(self.im).copy()
        arr[..., 3] = (arr[..., 3] * a).astype(np.uint8)
        dy = int((1 - a) * 20)
        img.alpha_composite(Image.fromarray(arr, "RGBA"), (0, self.y - 60 + dy))


# ---------- post ----------
_yy, _xx = np.mgrid[0:H, 0:W]
_r = np.sqrt(((_xx - W / 2) / (W * 0.62)) ** 2 + ((_yy - H * 0.5) / (H * 0.62)) ** 2)
VIGNETTE = np.clip(1 - 0.6 * _r ** 2.6, 0.25, 1).astype(np.float32)[..., None]
del _yy, _xx, _r


def bloom(arr, strength=0.45, thresh=150):
    small = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).resize((W // 8, H // 8), Image.BILINEAR)
    s = np.asarray(small, np.float32)
    s = np.clip(s - thresh, 0, None) * (255 / (255 - thresh))
    b = Image.fromarray(np.clip(s, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(5))
    b = np.asarray(b.resize((W, H), Image.BILINEAR), np.float32)
    return arr + b * strength
