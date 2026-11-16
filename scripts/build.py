"""Build every SVG used by the GitHub profile README.

The profile is a sibling of the work gallery (gcarbs1.github.io/work-gallery):
same palette, same typefaces and the same contour-line relief in the hero.
GitHub renders README images without JavaScript or web fonts, so

  * all text is converted to outlines (HarfBuzz shaping + font glyphs),
  * the relief is traced here (marching squares) and animated with CSS only,
  * project covers are embedded in the card SVGs as base64 JPEG.

Run:  python scripts/build.py            (from the repository root)
Needs: uharfbuzz, fonttools, numpy, pillow
"""
from __future__ import annotations

import base64
import io
import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import uharfbuzz as hb
from PIL import Image

# 1. Paths, palette and content ------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONTS = ROOT / ".fonts"
GALLERY = ROOT.parent / "work-gallery" / "html" / "assets"   # cover images

FONT_URLS = {
    "Bricolage.ttf": "https://github.com/google/fonts/raw/main/ofl/bricolagegrotesque/BricolageGrotesque%5Bopsz%2Cwdth%2Cwght%5D.ttf",
    "Inter.ttf": "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz%2Cwght%5D.ttf",
    "Mono.ttf": "https://github.com/google/fonts/raw/main/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
}

BG = "#000000"
CREAM = "#f0e6cc"          # text and contour ink
BEIGE = "#d9d1c4"          # rules and borders
LINE = "rgba(217,209,196,.2)"
MUTED_OPACITY = .64
LIGHT_INK = "#1b1915"      # headings on GitHub's light theme

GALLERY_URL = "https://gcarbs1.github.io/work-gallery/"

PROJECTS = [  # order and wording follow the work gallery
    dict(slug="where-the-rain-went", title="Where the Rain Went", tools="Python · Pandas · SVG",
         cover="pacific-dataviz-challenge-cover.png", poster=True),
    dict(slug="distance-vs-top-speed", title="Distance vs Top Speed at the 2026 World Cup",
         tools="Python · PyMuPDF · JavaScript", cover="fwc-match-reports-cover.png", poster=True),
    dict(slug="land-cover-brazil", title="Land Cover and Land Use in Brazil", tools="R · ggplot2 · Earth Engine",
         cover="brazil-land-cover-cover.png", poster=True),
    dict(slug="young-players-minutes", title="How Young Players Earn Their Minutes", tools="Python · Pandas · Canvas",
         cover="young-players-minutes-cover.png", featured=True),
    dict(slug="player-birthplaces", title="Brazilian League Player Birthplaces", tools="Python · Pandas · D3.js",
         cover="brasileirao-birthplaces-cover.png"),
    dict(slug="cbf-match-reports", title="CBF Match Report Analysis", tools="Python · D3.js · Cloudflare Workers",
         cover="cbf-match-report-analysis-cover.png"),
    dict(slug="color-palette-selector", title="Color Palette Selector", tools="JavaScript · SVG · Canvas",
         cover="color-palette-selector-cover.png"),
    dict(slug="body-pose", title="Visual Exploration of Body Pose Data", tools="Python · JavaScript · Three.js",
         cover="skillcorner-body-pose-cover.png"),
    dict(slug="birthday-paradox", title="The Birthday Paradox in the Brasileirão", tools="R · ggplot2 · httr2",
         cover="football-birthday-paradox-cover.png"),
    dict(slug="offensive-synergy", title="A Measure of Offensive Synergy", tools="Python · XGBoost · Matplotlib",
         cover="offensive-synergy-cover.png"),
]

# Time is in decimal years; education is known to the year only.
NOW = 2026 + 10.5 / 12          # mid-November 2026
TIMELINE = {
    "Education": [
        dict(start=2023.0, end=2026.0, when="2023 — 2026", title="BSc, Statistics and Data Science",
             org="University of São Paulo"),
        dict(start=2026.0, end=None, when="2026 — present", title="MSc, Applied and Computational Mathematics",
             org="University of São Paulo"),
    ],
    "Experience": [
        dict(start=2024 + 1 / 12, end=2024 + 10 / 12, when="Feb — Oct 2024", title="Data Analyst Intern",
             org="Prometrics.club"),
        dict(start=2024 + 10 / 12, end=2026 + 6 / 12, when="Nov 2024 — Jun 2026", title="Data Intern",
             org="Red Bull Bragantino"),
        dict(start=2026 + 6 / 12, end=None, when="Jul 2026 — present", title="Junior Data Analyst",
             org="Red Bull Bragantino"),
    ],
}

CONTACTS = [
    dict(slug="linkedin", label="LinkedIn"),
    dict(slug="gallery", label="Work gallery"),
    dict(slug="email", label="Email"),
]


# 2. Text as outlines -----------------------------------------------------------
def ensure_fonts() -> None:
    FONTS.mkdir(exist_ok=True)
    for name, url in FONT_URLS.items():
        if not (FONTS / name).exists():
            urllib.request.urlretrieve(url, FONTS / name)


class _Pen:
    """Collects glyph outlines as SVG path commands in final coordinates."""

    def __init__(self, x: float, y: float, s: float):
        self.x, self.y, self.s, self.d = x, y, s, []

    def _p(self, px, py):
        return f"{self.x + px * self.s:.2f} {self.y - py * self.s:.2f}"

    def moveTo(self, p): self.d.append("M" + self._p(*p))
    def lineTo(self, p): self.d.append("L" + self._p(*p))
    def curveTo(self, *ps): self.d.append("C" + " ".join(self._p(*p) for p in ps))
    def qCurveTo(self, *ps):
        ps = list(ps)
        while len(ps) > 2:   # implied on-curve points (TrueType)
            c, n = ps[0], ps[1]
            mid = ((c[0] + n[0]) / 2, (c[1] + n[1]) / 2)
            self.d.append("Q" + self._p(*c) + " " + self._p(*mid))
            ps = ps[1:]
        self.d.append("Q" + self._p(*ps[0]) + " " + self._p(*ps[1]))
    def closePath(self): self.d.append("Z")
    def endPath(self): pass


@dataclass
class Type:
    file: str
    variations: dict

    def __post_init__(self):
        face = hb.Face(hb.Blob.from_file_path(str(FONTS / self.file)))
        self.upem = face.upem
        self.font = hb.Font(face)
        self.font.set_variations(self.variations)

    def _shape(self, text):
        buf = hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        hb.shape(self.font, buf, {"kern": True, "liga": True})
        return buf.glyph_infos, buf.glyph_positions

    def width(self, text, size, tracking=0.0):
        _, pos = self._shape(text)
        adv = sum(p.x_advance for p in pos) * size / self.upem
        return adv + tracking * size * max(len(pos) - 1, 0)

    def path(self, text, size, x, y, tracking=0.0, anchor="start"):
        """Return (path d, advance width). y is the baseline."""
        w = self.width(text, size, tracking)
        if anchor == "middle":
            x -= w / 2
        elif anchor == "end":
            x -= w
        s = size / self.upem
        infos, pos = self._shape(text)
        pen_x, out = 0.0, []
        for info, p in zip(infos, pos):
            pen = _Pen(x + (pen_x + p.x_offset) * s, y - p.y_offset * s, s)
            self.font.draw_glyph_with_pen(info.codepoint, pen)
            out.extend(pen.d)
            pen_x += p.x_advance + tracking * self.upem
        return "".join(out), w

    def wrap(self, text, size, max_width, tracking=0.0):
        lines, line = [], ""
        for word in text.split():
            trial = f"{line} {word}".strip()
            if line and self.width(trial, size, tracking) > max_width:
                lines.append(line)
                line = word
            else:
                line = trial
        return lines + [line]


def text(t: Type, s, size, x, y, fill=CREAM, opacity=1.0, tracking=0.0, anchor="start", extra=""):
    d, _ = t.path(s, size, x, y, tracking, anchor)
    op = f' fill-opacity="{opacity:g}"' if opacity < 1 else ""
    return f'<path d="{d}" fill="{fill}"{op}{extra}/>'


# 3. Relief (port of the gallery hero, html/app.js) ----------------------------
HILLS = [
    dict(u=.36, v=-.02, sx=.2, sy=.26, w=1, a=.4),
    dict(u=.66, v=.34, sx=.13, sy=.17, w=.72, a=-.3),
    dict(u=.1, v=.52, sx=.18, sy=.14, w=.58, a=.9),
    dict(u=-.48, v=.56, sx=.24, sy=.18, w=.5, a=.2),
    dict(u=.82, v=-.38, sx=.17, sy=.2, w=.52, a=-.8),
    dict(u=-.15, v=-.48, sx=.3, sy=.2, w=.42, a=.5),
    dict(u=-.74, v=-.06, sx=.16, sy=.22, w=.38, a=1.2),
]


def relief_field(half_x, half_y, cell, seconds, breathe=.08):
    xs = np.arange(-half_x, half_x + cell, cell)
    ys = np.arange(-half_y, half_y + cell, cell)
    X, Y = np.meshgrid(xs, ys)
    t = seconds * breathe
    v = np.zeros_like(X)
    for k, h in enumerate(HILLS):
        turn = h["a"] + t * .3 * (1 if k % 2 else -1)
        hx = (h["u"] + math.sin(t * (1 + k * .23) + k * 1.9) * .08) * half_x
        hy = (h["v"] + math.cos(t * (.8 + k * .17) + k * 2.7) * .09) * half_y
        w = h["w"] * (.8 + .2 * math.sin(t * (1.3 + k * .11) + k))
        ix = 1 / (h["sx"] * half_x * (1 + .08 * math.sin(t + k)))
        iy = 1 / (h["sy"] * half_y)
        dx, dy = X - hx, Y - hy
        a = (dx * math.cos(turn) + dy * math.sin(turn)) * ix
        b = (-dx * math.sin(turn) + dy * math.cos(turn)) * iy
        v += w * np.exp(-.5 * (a * a + b * b))
    return xs, ys, 1 - np.exp(-1.6 * v)


# edges: 0 top (a-b), 1 right (b-c), 2 bottom (d-c), 3 left (a-d)
CASES = [[], [3, 0], [0, 1], [3, 1], [1, 2], [3, 0, 1, 2], [0, 2], [3, 2],
         [2, 3], [0, 2], [0, 1, 2, 3], [1, 2], [1, 3], [0, 1], [3, 0], []]


def contour_lines(xs, ys, z, level):
    """Marching squares, joined into polylines (keyed by grid edge)."""
    ny, nx = z.shape
    cell = xs[1] - xs[0]
    segs = []
    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b, c, d = z[j, i], z[j, i + 1], z[j + 1, i + 1], z[j + 1, i]
            code = (a > level) | (b > level) << 1 | (c > level) << 2 | (d > level) << 3
            if code in (0, 15):
                continue
            pr = CASES[code]
            for p in range(0, len(pr), 2):
                segs.append(((i, j, pr[p]), (i, j, pr[p + 1])))

    def key(e):
        i, j, k = e
        return {0: ("h", i, j), 2: ("h", i, j + 1), 3: ("v", i, j), 1: ("v", i + 1, j)}[k]

    def point(k):
        kind, i, j = k
        if kind == "h":
            a, b = z[j, i], z[j, i + 1]
            return xs[0] + (i + (level - a) / (b - a)) * cell, ys[0] + j * cell
        a, d = z[j, i], z[j + 1, i]
        return xs[0] + i * cell, ys[0] + (j + (level - a) / (d - a)) * cell

    adj: dict = {}
    for n, (p, q) in enumerate(segs):
        for e in (key(p), key(q)):
            adj.setdefault(e, []).append(n)
    used = [False] * len(segs)
    lines = []
    for n0 in range(len(segs)):
        if used[n0]:
            continue
        used[n0] = True
        chain = [key(segs[n0][0]), key(segs[n0][1])]
        for direction in (1, 0):        # extend forwards, then backwards
            while True:
                end = chain[-1] if direction else chain[0]
                nxt = [m for m in adj[end] if not used[m]]
                if not nxt:
                    break
                m = nxt[0]
                used[m] = True
                e1, e2 = key(segs[m][0]), key(segs[m][1])
                other = e2 if e1 == end else e1
                chain.append(other) if direction else chain.insert(0, other)
        lines.append([point(k) for k in chain])
    return lines


def simplify(points, tol):
    """Ramer–Douglas–Peucker."""
    if len(points) < 3:
        return points
    p = np.asarray(points)
    a, b = p[0], p[-1]
    ab = b - a
    norm = np.hypot(*ab)
    if norm == 0:
        dist = np.hypot(*(p - a).T)
    else:
        dist = np.abs(ab[0] * (p[:, 1] - a[1]) - ab[1] * (p[:, 0] - a[0])) / norm
    k = int(np.argmax(dist))
    if dist[k] <= tol:
        return [points[0], points[-1]]
    return simplify(points[:k + 1], tol)[:-1] + simplify(points[k:], tol)


class Camera:
    def __init__(self, cx, cy, tilt=56, yaw=0, perspective=2400):
        self.cx, self.cy, self.persp = cx, cy, perspective
        t, y = math.radians(tilt), math.radians(yaw)
        self.cosT, self.sinT, self.cosY, self.sinY = math.cos(t), math.sin(t), math.cos(y), math.sin(y)

    def project(self, x, y, z):
        xr = x * self.cosY - y * self.sinY
        yr = x * self.sinY + y * self.cosY
        depth = yr * self.sinT + z * self.cosT
        f = self.persp / (self.persp - depth)
        return self.cx + xr * f, self.cy + (yr * self.cosT - z * self.sinT) * f


def poly_d(lines):
    out = []
    for ln in lines:
        if len(ln) < 2:
            continue
        out.append("M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in ln))
    return "".join(out)


def terrain(width, height, *, levels=14, index_every=5, relief=.4, cell=6, seconds=0.0, yaw=0.0,
            cy=.56, spread=(.72, .62), tol=.35, stroke=1.0):
    """Projected contour levels: list of dict(d, z_shift, opacity, width, index)."""
    cam = Camera(width / 2, height * cy, yaw=yaw)
    half_x = width * spread[0]
    half_y = height * spread[1] / math.cos(math.radians(56))
    xs, ys, z = relief_field(half_x, half_y, cell, seconds)
    out = []
    for k in range(levels):
        share = (k + 1) / (levels + 1)
        zpx = share * relief * height
        lines = []
        for ln in contour_lines(xs, ys, z, share):
            run = []
            for x, y in ln:
                px, py = cam.project(x, y, zpx)
                if -40 < px < width + 40 and -40 < py < height + 40:
                    run.append((px, py))
                    continue
                if len(run) > 1:
                    lines.append(simplify(run, tol))
                run = []
            if len(run) > 1:
                lines.append(simplify(run, tol))
        index = k % index_every == index_every - 1
        out.append(dict(
            k=k, d=poly_d(lines), index=index,
            opacity=round(.14 + .55 * share + (.12 if index else 0), 3),
            width=(2.0 if index else 1.1) * stroke,
            rise=round(zpx * cam.sinT, 1),
        ))
    return out


# 4. SVG helpers ----------------------------------------------------------------
def svg(w, h, body, title, style=""):
    st = f"<style>{style}</style>" if style else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
            f'role="img" aria-label="{title}"><title>{title}</title>{st}{body}</svg>\n')


def save(name, content):
    path = ASSETS / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"  {name:<34} {len(content.encode()) / 1024:7.1f} KB")


EASE = "cubic-bezier(.22,1,.36,1)"
REDUCED = "@media (prefers-reduced-motion: reduce){*{animation:none!important}}"


# 5. Hero -------------------------------------------------------------------------
def build_hero(T):
    W, H = 1280, 620
    name, eyebrow, mono = T["display"], T["eyebrow"], T["mono"]

    # Text block (lower left): eyebrow + two-line name, as in the gallery hero
    size = 150
    x0, base1 = 84, 372
    base2 = base1 + size * .84
    eb_size, eb_track = 15, .16
    eb_y = base1 - size * .78 - 26
    items = ["DATA ANALYSIS", "DATA SCIENCE", "DATA VISUALIZATION"]
    parts, x = [], x0
    for n, item in enumerate(items):
        d, w = eyebrow.path(item, eb_size, x, eb_y, eb_track)
        parts.append(f'<path d="{d}" fill="{CREAM}"/>')
        x += w + 14
        if n < len(items) - 1:
            parts.append(f'<circle cx="{x + 3:.1f}" cy="{eb_y - 5:.1f}" r="2.4" fill="{CREAM}"/>')
            x += 20
    eyebrow_w = x - x0
    d1, w1 = name.path("Gabriel", size, x0, base1, -.055)
    d2, w2 = name.path("Carbinatto", size, x0 + .16 * size, base2, -.055)
    clear = dict(x=x0 - 40, y=eb_y - 30, w=max(w1, w2 + .16 * size, eyebrow_w) + 80,
                 h=base2 + 44 - (eb_y - 30))

    levels = terrain(W, H, seconds=118.0, yaw=0, cy=.6, spread=(.85, .75), stroke=1.25)

    paths, glows, css = [], [], []
    for lv in levels:
        k = lv["k"]
        paths.append(f'<path id="l{k}" class="lv l{k}" d="{lv["d"]}" stroke-width="{lv["width"]}" '
                     f'stroke-opacity="{lv["opacity"]}"/>')
        glows.append(f'<g class="gl g{k}"><use href="#l{k}" stroke-width="10" stroke-opacity=".16"/>'
                     f'<use href="#l{k}" stroke-width="2.6"/></g>')
        css.append(f"@keyframes r{k}{{from{{opacity:0;transform:translateY({lv['rise']}px)}}}}"
                   f".l{k}{{animation-name:r{k};animation-delay:{k * 70}ms}}"
                   f".g{k}{{animation-delay:{2.6 + k * .5:.2f}s}}")

    style = (
        f".lv{{animation:rise 2.4s {EASE} backwards}}"
        f".gl{{opacity:0;animation:scan 12s linear infinite}}"
        f"@keyframes scan{{0%{{opacity:0}}3%{{opacity:1}}9%{{opacity:0}}100%{{opacity:0}}}}"
        f".fade{{animation:show 1.4s {EASE} .5s backwards}}"
        f"@keyframes show{{from{{opacity:0}}}}"
        + "".join(css) + REDUCED
    )

    legend_y = H - 34
    legend = "".join([
        text(mono, "FIG. 01 — A PROFILE, IN RELIEF", 11, x0, legend_y, opacity=.5, tracking=.12),
        text(mono, "14 CONTOUR LEVELS · INDEX EVERY 5TH", 11, W - x0, legend_y, opacity=.5, tracking=.12,
             anchor="end"),
    ])
    body = f"""
<defs>
  <linearGradient id="depth" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#fff" stop-opacity=".45"/><stop offset=".45" stop-color="#fff" stop-opacity=".9"/>
    <stop offset="1" stop-color="#fff"/>
  </linearGradient>
  <filter id="feather" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="26"/></filter>
  <mask id="m" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{H}">
    <rect width="{W}" height="{H}" fill="url(#depth)"/>
    <rect x="{clear['x']:.0f}" y="{clear['y']:.0f}" width="{clear['w']:.0f}" height="{clear['h']:.0f}" fill="#000" filter="url(#feather)"/>
    <rect y="{H - 70}" width="{W}" height="70" fill="#000" filter="url(#feather)"/>
  </mask>
</defs>
<rect width="{W}" height="{H}" fill="{BG}"/>
<g mask="url(#m)" fill="none" stroke="{CREAM}" stroke-linecap="round" stroke-linejoin="round">
  {''.join(paths)}
  {''.join(glows)}
</g>
<g class="fade">{''.join(parts)}<path d="{d1}" fill="{CREAM}"/><path d="{d2}" fill="{CREAM}"/>{legend}</g>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" fill="none" stroke="{LINE}"/>
"""
    save("hero.svg", svg(W, H, body, "Gabriel Carbinatto — data analysis, data science, data visualization",
                         style))


# 6. Section headings (one file per GitHub theme) ---------------------------------
def build_heading(T, label, slug):
    W, H, size = 1280, 104, 58
    for theme, ink in (("dark", CREAM), ("light", LIGHT_INK)):
        d, w = T["display_h2"].path(label, size, 0, 64, -.045)
        rule = f'<rect y="80" width="{w:.1f}" height="3" fill="{ink}"/>'
        save(f"headings/{slug}-{theme}.svg", svg(W, H, f'<path d="{d}" fill="{ink}"/>{rule}', label))


# 7. Project cards ----------------------------------------------------------------
def cover_b64(file, max_w, max_h):
    im = Image.open(GALLERY / file).convert("RGB")
    im.thumbnail((max_w, max_h), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=84, optimize=True, progressive=True)
    return base64.b64encode(buf.getvalue()).decode(), im.size


def build_card(T, p):
    poster, featured = p.get("poster", False), p.get("featured", False)
    if poster:
        W, mw, mh, px_scale = 600, 600, 849, 600 / 280        # three across
    elif featured:
        W, mw, mh, px_scale = 1280, 1280, 800, 1280 / 880     # full width
    else:
        W, mw, mh, px_scale = 800, 800, 500, 800 / 430        # two across
    b64, (iw, ih) = cover_b64(p["cover"], mw * 2, mh * 2)

    title_size, tools_size = 17.5 * px_scale, 9.5 * px_scale
    gap = 14 * px_scale
    pad = 14 * px_scale
    lines = T["card"].wrap(p["title"], title_size, W - 2 * pad)
    y = mh + gap + title_size * .95
    title_paths = []
    for ln in lines:
        title_paths.append(text(T["card"], ln, title_size, pad, y, tracking=-.025))
        y += title_size * 1.2
    tools_y = y - title_size * 1.2 + title_size * .45 + tools_size * 1.6
    tools = text(T["mono"], p["tools"].upper(), tools_size, pad, tools_y, opacity=MUTED_OPACITY, tracking=.08)
    H = math.ceil(tools_y + pad * .9)

    border = "" if poster else f'<rect x=".5" y=".5" width="{mw - 1}" height="{mh - 1}" fill="none" stroke="{LINE}"/>'
    media_bg = "" if poster else f'<rect width="{mw}" height="{mh}" fill="#e5e8e6"/>'
    body = (f'<rect width="{W}" height="{H}" fill="{BG}"/>{media_bg}'
            f'<image href="data:image/jpeg;base64,{b64}" width="{mw}" height="{mh}" '
            f'preserveAspectRatio="xMidYMid {"meet" if poster else "slice"}"/>{border}'
            + "".join(title_paths) + tools)
    save(f"cards/{p['slug']}.svg", svg(W, H, body, p["title"]))


# 8. Timeline ---------------------------------------------------------------------
def build_timeline(T):
    W = 1280
    x_left, x_right = 84, W - 150
    t0, t1 = 2023.0, 2027.0
    X = lambda t: x_left + (t - t0) / (t1 - t0) * (x_right - x_left)
    mono, card, label = T["mono"], T["card"], T["display_h3"]

    parts, css = [], []
    top = 64                      # lane title baseline
    lane_h = 232
    for li, (lane, items) in enumerate(TIMELINE.items()):
        ty = top + li * lane_h
        ly = ty + 34              # track
        parts.append(text(label, lane, 26, x_left, ty, tracking=-.02))
        parts.append(f'<rect x="{x_left}" y="{ly}" width="{x_right - x_left}" height="1" fill="{LINE}"/>')
        # light travelling along the track (as in the gallery timeline)
        parts.append(f'<clipPath id="lane{li}"><rect x="{x_left}" y="{ly - 2}" width="{x_right - x_left}" '
                     f'height="5"/></clipPath>')
        parts.append(f'<g clip-path="url(#lane{li})"><rect class="flow f{li}" x="{x_left - 200}" y="{ly - .5}" '
                     f'width="200" height="2" fill="url(#flow)"/></g>')
        css.append(f".f{li}{{animation-delay:{-li * 3.5}s}}")
        starts = [X(it["start"]) for it in items]
        for n, it in enumerate(items):
            xs = starts[n]
            xe = X(it["end"] if it["end"] else NOW)
            fill = "url(#open)" if it["end"] is None else CREAM
            parts.append(f'<rect x="{xs:.1f}" y="{ly - 1}" width="{xe - xs:.1f}" height="3" fill="{fill}" '
                         f'fill-opacity=".85"/>')
            delay = -(li * 2 + n) * 2.33
            parts.append(f'<g class="pt" style="animation-delay:{delay:.2f}s">'
                         f'<circle cx="{xs:.1f}" cy="{ly + .5}" r="8" fill="{BG}"/>'
                         f'<circle cx="{xs:.1f}" cy="{ly + .5}" r="5" fill="{BG}" stroke="{CREAM}" '
                         f'stroke-width="1.4"/></g>')
            room = (starts[n + 1] if n + 1 < len(items) else W - 40) - xs - 28
            y = ly + 40
            parts.append(text(mono, it["when"].upper(), 11.5, xs, y, tracking=.08))
            y += 30
            for ln in card.wrap(it["title"], 19, room):
                parts.append(text(card, ln, 19, xs, y))
                y += 25
            parts.append(text(card, it["org"], 15, xs, y + 1, opacity=MUTED_OPACITY))
    # axis
    axis_y = top + 2 * lane_h - 30
    for yr in range(int(t0), int(t1) + 1):
        xx = X(yr)
        parts.append(f'<rect x="{xx:.1f}" y="{axis_y}" width="1" height="6" fill="{BEIGE}" fill-opacity=".5"/>')
        parts.append(text(mono, str(yr), 11.5, xx, axis_y + 24, opacity=.5, tracking=.08, anchor="middle"))
    parts.append(f'<rect x="{x_left}" y="{axis_y}" width="{x_right - x_left}" height="1" fill="{LINE}"/>')
    xn = X(NOW)
    parts.append(f'<rect x="{xn:.1f}" y="{top - 22}" width="1" height="{axis_y - top + 22}" fill="{CREAM}" '
                 f'fill-opacity=".28"/>')
    parts.append(text(mono, "NOW", 11.5, xn - 8, top - 8, opacity=.6, tracking=.12, anchor="end"))
    H = axis_y + 56

    style = (f".flow{{animation:flow 7s linear infinite}}"
             f"@keyframes flow{{from{{transform:translateX(0)}}to{{transform:translateX({x_right - x_left + 200}px)}}}}"
             f".pt{{transform-box:fill-box;transform-origin:center;animation:pt 7s {EASE} infinite}}"
             f"@keyframes pt{{0%,78%,100%{{transform:scale(1)}}84%{{transform:scale(1.25)}}91%{{transform:scale(1)}}}}"
             + "".join(css) + REDUCED)
    defs = (f'<defs><linearGradient id="flow"><stop offset="0" stop-color="{BEIGE}" stop-opacity="0"/>'
            f'<stop offset=".5" stop-color="{BEIGE}"/><stop offset="1" stop-color="{BEIGE}" stop-opacity="0"/>'
            f'</linearGradient><linearGradient id="open"><stop offset="0" stop-color="{CREAM}"/>'
            f'<stop offset=".7" stop-color="{CREAM}"/><stop offset="1" stop-color="{CREAM}" stop-opacity="0"/>'
            f'</linearGradient></defs>')
    body = (defs + f'<rect width="{W}" height="{H}" fill="{BG}"/>' + "".join(parts) +
            f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" fill="none" stroke="{LINE}"/>')
    save("timeline.svg", svg(W, H, body, "Education and experience timeline", style))


# 9. Contact buttons and footer --------------------------------------------------
def build_buttons(T):
    W, H, size = 300, 76, 19
    for c in CONTACTS:
        lbl = text(T["card"], c["label"], size, 26, H / 2 + size * .36)
        arrow = text(T["card"], "↗", size, W - 26, H / 2 + size * .36, anchor="end")
        body = (f'<rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" fill="{BG}" stroke="{BEIGE}" '
                f'stroke-opacity=".35" stroke-width="1.5"/>{lbl}{arrow}')
        save(f"buttons/{c['slug']}.svg", svg(W, H, body, c["label"]))


def build_footer(T):
    W, H = 1280, 300
    levels = terrain(W, H * 2.4, levels=10, index_every=5, relief=.18, cell=7, seconds=40.0, yaw=8,
                     cy=.3, spread=(.8, .5), stroke=1.6)
    paths = "".join(f'<path d="{lv["d"]}" stroke-width="{lv["width"]}" stroke-opacity="{lv["opacity"] * .7:.3f}"/>'
                    for lv in levels)
    t = T["mono"]
    body = f"""
<defs><linearGradient id="fd" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".55" stop-color="#fff" stop-opacity=".85"/>
  <stop offset="1" stop-color="#fff"/></linearGradient>
  <mask id="fm" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{H}"><rect width="{W}" height="{H}" fill="url(#fd)"/></mask></defs>
<rect width="{W}" height="{H}" fill="{BG}"/>
<g mask="url(#fm)" fill="none" stroke="{CREAM}" stroke-linecap="round">{paths}</g>
<rect x="84" y="{H - 62}" width="{W - 168}" height="1" fill="{LINE}"/>
{text(t, "DATA ANALYSIS · DATA SCIENCE · DATA VISUALIZATION", 11, 84, H - 34, opacity=MUTED_OPACITY, tracking=.08)}
{text(t, "SÃO PAULO, BRAZIL", 11, W - 84, H - 34, opacity=MUTED_OPACITY, tracking=.08, anchor="end")}
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" fill="none" stroke="{LINE}"/>
"""
    save("footer.svg", svg(W, H, body, "Data analysis · Data science · Data visualization"))


# 10. Run ------------------------------------------------------------------------
def main():
    ensure_fonts()
    T = {
        "display": Type("Bricolage.ttf", {"wght": 760, "opsz": 96}),
        "display_h2": Type("Bricolage.ttf", {"wght": 700, "opsz": 72}),
        "display_h3": Type("Bricolage.ttf", {"wght": 700, "opsz": 28}),
        "eyebrow": Type("Inter.ttf", {"wght": 700, "opsz": 14}),
        "card": Type("Inter.ttf", {"wght": 520, "opsz": 20}),
        "mono": Type("Mono.ttf", {"wght": 600}),
    }
    print("Writing assets/")
    build_hero(T)
    for label, slug in (("Selected work", "work"), ("Path", "path"), ("Contact", "contact")):
        build_heading(T, label, slug)
    for p in PROJECTS:
        build_card(T, p)
    build_timeline(T)
    build_buttons(T)
    build_footer(T)


if __name__ == "__main__":
    main()
