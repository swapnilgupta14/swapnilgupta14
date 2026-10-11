#!/usr/bin/env python3
"""
Renders every image used by README.md into assets/dark/ and assets/light/.

    pip install -r scripts/requirements.txt
    GITHUB_TOKEN=$(gh auth token) python3 scripts/build.py

Edit words in CONTENT and colours in THEMES; never hand-edit the SVGs.
Each SVG embeds only the glyphs it uses (subset woff2), so files stay small
and render identically everywhere. .github/workflows/profile.yml re-runs
this twice a day to refresh the numbers card.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
import json
import os
import subprocess
import sys
import urllib.request
from collections import defaultdict
from contextlib import contextmanager

import uharfbuzz as hb
from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIR = os.path.join(ROOT, "scripts", "fonts")
OUT = os.path.join(ROOT, "assets")
LOGIN = "swapnilgupta14"
W = 840  # GitHub's profile README column is ~830px, so 840 renders ~1:1

# ─────────────────────────────────────────────────────────────── CONTENT ──

HERO = dict(
    kicker="SWAPNILGUPTA.IN",
    status="CURRENTLY AT SIMPPLR",
    name="Swapnil Gupta",
    lines=[
        [("Software engineer building web & mobile products", "sans", "fg2")],
        [("with an eye for the details that make them ", "sans", "fg2"), ("feel right.", "serif-i", "accent")],
    ],
    specs=[
        ("ROLE", "Software Engineer"),
        ("FOCUS", "Frontend & product UI"),
        ("PLATFORMS", "Web, iOS & Android"),
        ("BASED", "Earth-616"),
    ],
)

NAV = [("01", "About", "about"), ("02", "Stack", "stack"), ("03", "Work", "work"),
       ("04", "Numbers", "numbers"), ("05", "Contact", "contact")]

ABOUT = dict(
    paragraphs=[
        [("I’m a software engineer who lives where ", "fg2"), ("engineering meets design", "fg"),
         (". I build product interfaces for the web and mobile, and care most about ", "fg2"),
         ("the parts you feel before you notice", "fg"),
         (" — speed, motion and the states nobody screenshots.", "fg2")],
        [("Days: product engineering at ", "fg2"), ("Simpplr", "fg"),
         (". Nights: UI kits, side projects and small experiments.", "fg2")],
    ],
    index=[
        ("NOW", "Product engineering at Simpplr"),
        ("BUILDING", "Blueberry UI, a React component kit"),
        ("EXPLORING", "React Native, Expo & Web3"),
        ("OBSESSING OVER", "Web performance & motion"),
    ],
)

# (label, [(item, daily_driver)])
STACK = [
    ("LANGUAGES", [("TypeScript", 1), ("JavaScript", 1), ("Python", 0), ("C++", 0), ("SQL", 0)]),
    ("FRONTEND", [("React", 1), ("Next.js", 1), ("Tailwind CSS", 1), ("Redux Toolkit", 0), ("SCSS", 0), ("GSAP", 0), ("Framer Motion", 0)]),
    ("MOBILE", [("React Native", 1), ("Expo", 1), ("NativeWind", 0)]),
    ("BACKEND", [("Node.js", 1), ("Express", 0), ("FastAPI", 0), ("MySQL", 0)]),
    ("ML & DATA", [("TensorFlow", 0), ("Keras", 0), ("Pandas", 0), ("Streamlit", 0)]),
    ("TOOLING", [("Git", 1), ("Vite", 1), ("pnpm", 1), ("Webpack", 0), ("Vercel", 0), ("Figma", 0)]),
]

WORK = [
    dict(key="chalkpad", name="ChalkPad", domain="chalkpad-web.vercel.app", glyph="chalk",
         blurb="A collaborative whiteboard with hand-drawn rendering, built on Rough.js and perfect-freehand.",
         tags="JAVASCRIPT · CANVAS"),
    dict(key="compilex", name="CompileX", domain="cmpx.vercel.app", glyph="compile",
         blurb="Practise DSA, battle friends on live leaderboards and run code in a built-in editor.",
         tags="TYPESCRIPT · CODE EDITOR"),
    dict(key="synapse", name="Synapse", domain="org-synapse.vercel.app", glyph="graph",
         blurb="An enterprise workflow suite for projects, teams, task allocation and progress tracking.",
         tags="TYPESCRIPT · SAAS"),
    dict(key="blueberry", name="Blueberry UI", domain="blueberry-ui-site.vercel.app", glyph="layers",
         blurb="A custom React component library, packaged for npm with its own documentation site.",
         tags="REACT · DESIGN SYSTEM"),
]

CONTACT = dict(
    title=[("Let’s build something ", "serif", "fg"), ("worth using.", "serif-i", "accent")],
    line="Open to collaborations, interesting problems and good conversations.",
    buttons=[("website", "Website", "globe"), ("linkedin", "LinkedIn", "in"), ("email", "Email", "mail")],
)

IGNORED_LANGUAGES = {"Jupyter Notebook"}

# ──────────────────────────────────────────────────────────────── THEMES ──
# Backgrounds stay transparent so the page shows GitHub's own canvas
# (light, dark, dimmed or high-contrast). Tuples are (colour, opacity).

THEMES = {
    "dark": dict(fg="#EDEBE8", fg2="#A8A49F", fg3="#85827C", dim="#4A4845",
                 accent="#FF7A45", accentText="#FF7A45",
                 line=("#FFFFFF", 0.13), line2=("#FFFFFF", 0.07),
                 surface=("#FFFFFF", 0.025), cell0=("#FFFFFF", 0.07)),
    "light": dict(fg="#1A1918", fg2="#57534E", fg3="#78736D", dim="#C9C5C0",
                  accent="#E8571F", accentText="#C2410C",
                  line=("#000000", 0.13), line2=("#000000", 0.07),
                  surface=("#000000", 0.018), cell0=("#000000", 0.06)),
}

# ───────────────────────────────────────────────────────────────── FONTS ──

class Font:
    def __init__(self, file, family, style, weight, fallback):
        self.path = os.path.join(FONT_DIR, file)
        self.family, self.style, self.weight, self.fallback = family, style, weight, fallback
        self.face = hb.Face(hb.Blob.from_file_path(self.path))
        self.hb = hb.Font(self.face)

    def width(self, s, size, tracking=0.0):
        # Shaped with HarfBuzz (kerning included) so widths match the browser to ~0.1px.
        if not s:
            return 0.0
        buf = hb.Buffer()
        buf.add_str(s)
        buf.guess_segment_properties()
        hb.shape(self.hb, buf, {"kern": True, "liga": True})
        return sum(p.x_advance for p in buf.glyph_positions) * size / self.face.upem + tracking * len(s)

    def woff2(self, chars):
        tt = TTFont(self.path, recalcTimestamp=False)
        opts = subset.Options()
        opts.layout_features = ["kern", "liga", "calt"]
        opts.hinting = False
        sub = subset.Subsetter(opts)
        sub.populate(text="".join(sorted(chars)))
        sub.subset(tt)
        tt.flavor = "woff2"
        buf = io.BytesIO()
        tt.save(buf)
        return base64.b64encode(buf.getvalue()).decode()


SANS = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "'SF Mono',Menlo,Consolas,monospace"
SERIF = "Georgia,'Times New Roman',serif"
FONTS = {
    "serif": Font("InstrumentSerif-Regular.ttf", "IS", "normal", 400, SERIF),
    "serif-i": Font("InstrumentSerif-Italic.ttf", "ISI", "italic", 400, SERIF),
    "sans": Font("Geist-Regular.ttf", "G4", "normal", 400, SANS),
    "sans-m": Font("Geist-Medium.ttf", "G5", "normal", 500, SANS),
    "mono": Font("GeistMono-Regular.ttf", "GM4", "normal", 400, MONO),
    "mono-m": Font("GeistMono-Medium.ttf", "GM5", "normal", 500, MONO),
}

# ───────────────────────────────────────────────────────────────── MOTION ──
# Keyframes only define the *start* state, so the resting state is the real
# layout: renderers without animation (or with reduced motion) still show
# everything in place.

MOTION = """
.rise{animation:rise 1s cubic-bezier(.16,1,.3,1) both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}}
.fade{animation:fade 1.1s ease both}
@keyframes fade{from{opacity:0}}
.grow{transform-box:fill-box;transform-origin:0 50%;animation:grow 1.4s cubic-bezier(.65,0,.35,1) both}
@keyframes grow{from{transform:scaleX(0)}}
.draw{stroke-dasharray:1 1;animation:draw 1.6s cubic-bezier(.65,0,.35,1) both}
@keyframes draw{from{stroke-dashoffset:1}}
.pulse{transform-box:fill-box;transform-origin:50% 50%;animation:pulse 2.4s cubic-bezier(.2,.6,.4,1) infinite}
@keyframes pulse{0%{transform:scale(1);opacity:.6}70%,100%{transform:scale(3.4);opacity:0}}
.blink{animation:blink 1.1s steps(1) infinite}
@keyframes blink{50%{opacity:0}}
.nudge{animation:nudge 3s ease-in-out infinite}
@keyframes nudge{0%,50%,100%{transform:translate(0,0)}25%{transform:translate(2px,-2px)}}
.roll{animation:roll 1.8s cubic-bezier(.16,1,.3,1) both}
@keyframes roll{from{transform:translateY(0)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important}}
"""


def esc(s):
    return "".join(c if 32 <= ord(c) < 127 and c not in "&<>\"" else f"&#{ord(c)};" for c in s)


def n(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


class Svg:
    def __init__(self, theme, w, h):
        self.c = THEMES[theme]
        self.w, self.h = w, h
        self.out, self.defs = [], []
        self.chars = defaultdict(set)

    def paint(self, attr, token):
        v = self.c.get(token, token)
        if isinstance(v, tuple):
            return f'{attr}="{v[0]}" {attr}-opacity="{v[1]}"'
        return f'{attr}="{v}"'

    @contextmanager
    def g(self, cls="rise", delay=0.0, extra=""):
        style = f' style="animation-delay:{n(delay)}s"' if delay else ""
        klass = f' class="{cls}"' if cls else ""
        self.out.append(f"<g{klass}{style}{extra}>")
        yield
        self.out.append("</g>")

    def add(self, s):
        self.out.append(s)

    def text(self, x, y, s, face="sans", size=16, fill="fg", tracking=0.0, align="start", extra=""):
        f = FONTS[face]
        w = f.width(s, size, tracking)
        if align == "end":
            x -= w - tracking
        elif align == "middle":
            x -= (w - tracking) / 2
        self.chars[face].update(s)
        ls = f' letter-spacing="{n(tracking)}"' if tracking else ""
        self.out.append(f'<text x="{n(x)}" y="{n(y)}" class="{face}" font-size="{n(size)}"{ls} '
                        f'{self.paint("fill", fill)}{extra}>{esc(s)}</text>')
        return w

    def rich(self, x, y, runs, size):
        """runs: (text, face, fill[, size]); each run is placed absolutely so
        whitespace handling never shifts anything."""
        parts, cx = [], x
        for run in runs:
            text, face, fill = run[:3]
            sz = run[3] if len(run) > 3 else size
            f = FONTS[face]
            body = text.strip()
            cx += f.width(text[: len(text) - len(text.lstrip())], sz)
            self.chars[face].update(body)
            parts.append(f'<tspan x="{n(cx)}" class="{face}" font-size="{n(sz)}" {self.paint("fill", fill)}>{esc(body)}</tspan>')
            cx += f.width(text.lstrip(), sz)
        self.out.append(f'<text y="{n(y)}">{"".join(parts)}</text>')
        return cx - x

    def hline(self, x1, x2, y, token="line", cls="", delay=0.0):
        style = f' style="animation-delay:{n(delay)}s"' if delay else ""
        klass = f' class="{cls}"' if cls else ""
        self.out.append(f'<rect x="{n(x1)}" y="{n(y)}" width="{n(x2 - x1)}" height="1" {self.paint("fill", token)}{klass}{style}/>')

    def render(self):
        faces, classes = [], []
        for key in sorted(self.chars):
            f = FONTS[key]
            faces.append(f"@font-face{{font-family:'{f.family}';src:url(data:font/woff2;base64,{f.woff2(self.chars[key])}) "
                         f"format('woff2');font-style:{f.style};font-weight:{f.weight}}}")
            classes.append(f".{key}{{font-family:'{f.family}',{f.fallback};font-style:{f.style};font-weight:{f.weight}}}")
        css = "\n".join(faces + classes) + MOTION
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{n(self.w)}" height="{n(self.h)}" '
                f'viewBox="0 0 {n(self.w)} {n(self.h)}" fill="none">\n<style>{css}</style>\n{defs}\n'
                + "\n".join(self.out) + "\n</svg>\n")


def wrap(runs, face, size, width):
    """Greedy word wrap over styled runs -> list of lines (each a list of runs)."""
    f = FONTS[face]
    words = []
    for text, fill in runs:
        for i, word in enumerate(text.split(" ")):
            words.append((("" if i == 0 else " ") + word, fill))
    lines, line, lw = [], [], 0.0
    for word, fill in words:
        ww = f.width(word, size)
        if line and lw + ww > width and word.startswith(" "):
            lines.append(line)
            line, lw, word = [], 0.0, word[1:]
            ww = f.width(word, size)
        if line and line[-1][1] == fill:
            line[-1] = (line[-1][0] + word, fill)
        else:
            line.append((word, fill))
        lw += ww
    if line:
        lines.append(line)
    return [[(t, face, fill) for t, fill in ln if t] for ln in lines]


def section_header(svg, num, label, y=20, meta=None):
    with svg.g("rise"):
        w1 = svg.text(0, y, num, "mono-m", 11, "accentText", 1.6)
        w2 = svg.text(w1 + 10, y, label, "mono-m", 11, "fg3", 1.6)
        right = W
        if meta:
            mw = svg.text(W, y, meta, "mono", 10.5, "fg3", 1.2, align="end")
            right = W - mw - 14
    svg.hline(w1 + w2 + 26, right, y - 4, "line2", "grow", 0.15)

# ──────────────────────────────────────────────────────────────── PIECES ──

def hero(theme):
    s = Svg(theme, W, 344)
    with s.g("rise"):
        kw = s.text(0, 22, HERO["kicker"], "mono-m", 11, "fg3", 1.8)
        s.text(kw + 4, 22, "↗", "sans", 12, "accentText")
        sw = s.text(W, 22, HERO["status"], "mono-m", 11, "fg2", 1.8, align="end")
        cx = W - sw - 12
        s.add(f'<circle cx="{n(cx)}" cy="18" r="3.5" {s.paint("fill", "accent")} class="pulse"/>')
        s.add(f'<circle cx="{n(cx)}" cy="18" r="3.5" {s.paint("fill", "accent")}/>')
    s.hline(0, W, 42, "line", "grow", 0.1)
    with s.g("rise", 0.12):
        s.text(-4, 150, HERO["name"], "serif", 104, "fg", -1)
    for i, line in enumerate(HERO["lines"]):
        with s.g("rise", 0.28 + i * 0.1):
            runs = [(t, f, c, 24.5 if f.startswith("serif") else 21) for t, f, c in line]
            s.rich(0, 200 + i * 33, runs, 21)
    # hand-drawn underline under the italic phrase
    last = HERO["lines"][-1]
    x0 = FONTS["sans"].width(last[0][0], 21)
    x1 = x0 + FONTS["serif-i"].width(last[1][0].rstrip("."), 24.5)
    s.add(f'<path d="M{n(x0)} 241 Q{n((x0 + x1) / 2)} 245 {n(x1 + 4)} 239.5" pathLength="1" '
          f'{s.paint("stroke", "accent")} stroke-width="1.6" stroke-linecap="round" class="draw" style="animation-delay:1s"/>')
    s.hline(0, W, 274, "line", "grow", 0.35)
    for i, (label, value) in enumerate(HERO["specs"]):
        with s.g("rise", 0.5 + i * 0.08):
            x = i * 214
            s.text(x, 304, label, "mono-m", 10.5, "fg3", 1.6)
            s.text(x, 330, value, "sans", 16, "fg")
    return s


def nav_pill(theme, num, label):
    f = FONTS["mono-m"]
    nw, lw = f.width(num, 11, 1.4), f.width(label.upper(), 11, 1.4)
    w = 16 + nw + 6 + lw + 14
    s = Svg(theme, w + 8, 34)
    with s.g("fade"):
        s.add(f'<rect x=".5" y=".5" width="{n(w - 1)}" height="33" rx="16.5" {s.paint("fill", "surface")} {s.paint("stroke", "line")}/>')
        s.text(16, 21, num, "mono-m", 11, "accentText", 1.4)
        s.text(16 + nw + 6, 21, label.upper(), "mono-m", 11, "fg2", 1.4)
    return s


def about(theme):
    s = Svg(theme, W, 300)
    section_header(s, "01", "ABOUT")
    y = last = 76
    for para in ABOUT["paragraphs"]:
        for line in wrap(para, "sans", 19, 456):
            with s.g("rise", 0.1 + (y - 76) / 31 * 0.06):
                s.rich(0, y, line, 19)
            last = y
            y += 31
        y += 14
    x = 520
    for i, (label, value) in enumerate(ABOUT["index"]):
        ry = 70 + i * 58
        with s.g("rise", 0.2 + i * 0.09):
            lw = s.text(x, ry, label, "mono-m", 10.5, "fg3", 1.6)
            if i == 0:
                s.add(f'<circle cx="{n(x + lw + 8)}" cy="{ry - 3.5}" r="3" {s.paint("fill", "accent")} class="pulse"/>')
                s.add(f'<circle cx="{n(x + lw + 8)}" cy="{ry - 3.5}" r="3" {s.paint("fill", "accent")}/>')
            s.text(x, ry + 24, value, "sans", 16, "fg")
        if i < len(ABOUT["index"]) - 1:
            s.hline(x, W, ry + 40, "line2", "grow", 0.3 + i * 0.09)
    s.h = max(last, 70 + (len(ABOUT["index"]) - 1) * 58 + 24) + 24
    return s


def stack(theme):
    rows = len(STACK)
    s = Svg(theme, W, 72 + (rows - 1) * 46 + 24)
    section_header(s, "02", "STACK", meta="BRIGHT = DAILY DRIVERS")
    f = FONTS["sans"]
    for i, (label, items) in enumerate(STACK):
        y = 72 + i * 46
        with s.g("rise", 0.1 + i * 0.07):
            s.text(0, y, label, "mono-m", 10.5, "fg3", 1.6)
            x = 168
            for j, (item, daily) in enumerate(items):
                if j:
                    s.text(x + 9, y + 1, "/", "sans", 16.5, "dim")
                    x += 9 + f.width("/", 16.5) + 9
                x += s.text(x, y + 1, item, "sans", 16.5, "fg" if daily else "fg3")
        if i < rows - 1:
            s.hline(0, W, y + 21, "line2", "grow", 0.2 + i * 0.07)
    return s


def work_header(theme):
    s = Svg(theme, W, 40)
    section_header(s, "03", "SELECTED WORK", meta="EVERY CARD OPENS THE LIVE PROJECT")
    return s


def glyph(s, kind, x, y):
    st = s.paint("stroke", "fg2")
    ac = s.paint("stroke", "accent")
    fa = s.paint("fill", "accent")
    g = f'<g transform="translate({n(x)} {n(y)})" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
    if kind == "chalk":
        g += (f'<path d="M2 29C7 10 12 33 18 19S28 4 36 17" pathLength="1" {st} class="draw" style="animation-delay:.3s"/>'
              f'<circle cx="36" cy="17" r="3" {fa} class="fade" style="animation-delay:1.6s"/>')
    elif kind == "compile":
        g += (f'<rect x="1" y="5" width="38" height="30" rx="6" {st}/>'
              f'<path d="M9 15l5 5-5 5" pathLength="1" {st} class="draw" style="animation-delay:.3s"/>'
              f'<rect x="18" y="24" width="10" height="2.4" rx="1" {fa} stroke="none" class="blink"/>')
    elif kind == "graph":
        pts = [(5, 31), (20, 6), (35, 29), (21, 22)]
        for a, b in [(0, 3), (1, 3), (2, 3), (0, 1)]:
            g += (f'<path d="M{pts[a][0]} {pts[a][1]}L{pts[b][0]} {pts[b][1]}" pathLength="1" {st} '
                  f'class="draw" style="animation-delay:{.25 + a * .12:.2f}s"/>')
        for i, (px, py) in enumerate(pts[:3]):
            g += f'<circle cx="{px}" cy="{py}" r="3.4" {st} {s.paint("fill", "fg2")} fill-opacity="0"/>'
        g += f'<circle cx="21" cy="22" r="3.4" {fa} class="pulse"/><circle cx="21" cy="22" r="3.4" {fa}/>'
    elif kind == "layers":
        for i in range(3):
            g += (f'<rect x="{2 + i * 5}" y="{8 + i * 6}" width="24" height="17" rx="4" {st} '
                  f'{s.paint("fill", "surface")} class="rise" style="animation-delay:{.25 + i * .12:.2f}s"/>')
        g += f'<circle cx="34" cy="8" r="4" {fa} class="fade" style="animation-delay:.8s"/>'
    s.add(g + "</g>")


def card(theme, item, left):
    cw, ch = 412, 226
    s = Svg(theme, cw + 8, ch)
    ox = 0 if left else 8
    with s.g("fade"):
        s.add(f'<rect x="{n(ox + .5)}" y=".5" width="{cw - 1}" height="{ch - 1}" rx="16" '
              f'{s.paint("fill", "surface")} {s.paint("stroke", "line")}/>')
    glyph(s, item["glyph"], ox + 24, 22)
    ax, ay = ox + cw - 38, 38
    s.add(f'<circle cx="{n(ax)}" cy="{ay}" r="14" {s.paint("stroke", "line")}/>'
          f'<g class="nudge"><path d="M{n(ax - 4)} {ay + 4}L{n(ax + 4)} {ay - 4}M{n(ax - 2.5)} {ay - 4}H{n(ax + 4)}V{ay + 2.5}" '
          f'{s.paint("stroke", "accentText")} stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></g>')
    with s.g("rise", 0.15):
        s.text(ox + 24, 106, item["name"], "serif", 36, "fg", -0.2)
    with s.g("rise", 0.25):
        for i, line in enumerate(wrap([(item["blurb"], "fg2")], "sans", 14.5, cw - 56)[:2]):
            s.rich(ox + 24, 134 + i * 21, line, 14.5)
    s.hline(ox + 24, ox + cw - 24, 178, "line2")
    with s.g("rise", 0.35):
        tw = s.text(ox + 24, 203, item["tags"], "mono-m", 10, "fg3", 1.4)
        dw = s.text(ox + cw - 24, 203, item["domain"], "mono", 10.5, "fg2", 0.2, align="end")
    if tw + dw + 16 > cw - 48:
        sys.exit(f"{item['name']}: tags and domain overlap; shorten one of them")
    return s


def stat_number(s, x, base, value, size, delay, uid):
    """Odometer: each digit is a 0-9 strip that rolls up to its value."""
    f = FONTS["serif"]
    step = size * 1.1
    s.defs.append(f'<clipPath id="{uid}"><rect x="{n(x - 4)}" y="{n(base - size * 0.82)}" '
                  f'width="{n(f.width(value, size) + 8)}" height="{n(size * 1.08)}"/></clipPath>')
    parts = [f'<g clip-path="url(#{uid})">']
    cx = x
    for i, ch in enumerate(value):
        cw = f.width(ch, size)
        if ch.isdigit():
            d = int(ch)
            dur = 1.4 + i * 0.18
            parts.append(f'<g class="roll" transform="translate(0 {n(-d * step)})" '
                         f'style="animation-delay:{n(delay)}s;animation-duration:{n(dur)}s">')
            for k in range(10):
                kw = f.width(str(k), size)
                parts.append(f'<text x="{n(cx + (cw - kw) / 2)}" y="{n(base + k * step)}" class="serif" '
                             f'font-size="{n(size)}" {s.paint("fill", "fg")}>{k}</text>')
            parts.append("</g>")
            s.chars["serif"].update("0123456789")
        else:
            parts.append(f'<text x="{n(cx)}" y="{n(base)}" class="serif" font-size="{n(size)}" {s.paint("fill", "fg")}>{esc(ch)}</text>')
            s.chars["serif"].add(ch)
        cx += cw
    parts.append("</g>")
    s.add("".join(parts))


def numbers(theme, data):
    s = Svg(theme, W, 452)
    section_header(s, "04", "IN NUMBERS", meta=f"SYNCED {data['synced']}")
    stats = [("CONTRIBUTIONS · 1Y", data["total"]), ("ACTIVE DAYS · 1Y", data["active"]),
             ("LONGEST STREAK", data["streak"]), ("PUBLIC REPOS", data["repos"])]
    for i, (label, value) in enumerate(stats):
        x = i * 214
        with s.g("rise", 0.1 + i * 0.08):
            stat_number(s, x - 1, 108, f"{value:,}", 58, 0.25 + i * 0.1, f"n{i}")
            s.text(x, 138, label, "mono-m", 10.5, "fg3", 1.6)

    # contribution heatmap
    weeks = data["weeks"]
    stepx = W / len(weeks)
    cell = stepx - 3.4
    top = 196
    last_label = -99
    for wi, week in enumerate(weeks):
        first = dt.date.fromisoformat(week[0][0])
        if first.day <= 7 and wi * stepx - last_label > 36 and wi > 0:
            s.text(wi * stepx, top - 10, first.strftime("%b").upper(), "mono", 9.5, "fg3", 1)
            last_label = wi * stepx
    levels = [None, 0.28, 0.5, 0.75, 1]
    for wi, week in enumerate(weeks):
        cells = []
        for date, level in week:
            row = (dt.date.fromisoformat(date).weekday() + 1) % 7
            x, y = wi * stepx, top + row * stepx
            fill = s.paint("fill", "cell0") if level == 0 else f'{s.paint("fill", "accent")} fill-opacity="{levels[level]}"'
            cells.append(f'<rect x="{n(x)}" y="{n(y)}" width="{n(cell)}" height="{n(cell)}" rx="2.5" {fill}/>')
        s.add(f'<g class="fade" style="animation-delay:{n(0.4 + wi * 0.018)}s">{"".join(cells)}</g>')
    ly = top + 7 * stepx + 18
    with s.g("fade", 1.2):
        s.text(0, ly, "PAST 12 MONTHS", "mono-m", 10, "fg3", 1.4)
        mw = FONTS["mono-m"].width("MORE", 10, 1.4) - 1.4
        s.text(W, ly, "MORE", "mono-m", 10, "fg3", 1.4, align="end")
        lx = W - mw - 10 - 5 * 14
        for k in range(5):
            fill = s.paint("fill", "cell0") if k == 0 else f'{s.paint("fill", "accent")} fill-opacity="{levels[k]}"'
            s.add(f'<rect x="{n(lx + k * 14)}" y="{n(ly - 9)}" width="10" height="10" rx="2" {fill}/>')
        s.text(lx - 10, ly, "LESS", "mono-m", 10, "fg3", 1.4, align="end")

    # languages
    ly2 = ly + 26
    s.hline(0, W, ly2, "line2", "grow", 0.6)
    langs = data["languages"]
    with s.g("rise", 0.7):
        s.text(0, ly2 + 34, "LANGUAGES · BY CODE VOLUME", "mono-m", 10.5, "fg3", 1.6)
    fills = [("accent", 1), ("fg", 0.78), ("fg", 0.52), ("fg", 0.32), ("fg", 0.16)]
    bx, by, gap = 0.0, ly2 + 50, 4
    total_w = W - gap * (len(langs) - 1)
    segs = []
    for i, (name, pct) in enumerate(langs):
        w = max(4, total_w * pct / 100)
        tok, op = fills[i]
        segs.append(f'<rect x="{n(bx)}" y="{n(by)}" width="{n(w)}" height="6" rx="3" {s.paint("fill", tok)} fill-opacity="{op}"/>')
        bx += w + gap
    s.add(f'<g class="grow" style="animation-delay:.9s">{"".join(segs)}</g>')
    lx = 0.0
    with s.g("fade", 1.2):
        for i, (name, pct) in enumerate(langs):
            tok, op = fills[i]
            s.add(f'<circle cx="{n(lx + 4)}" cy="{n(by + 30)}" r="4" {s.paint("fill", tok)} fill-opacity="{op}"/>')
            lx += 14
            lx += s.text(lx, by + 34, name, "sans", 14, "fg2") + 6
            lx += s.text(lx, by + 34, f"{pct:.1f}%", "mono", 12, "fg3") + 26
    s.h = by + 48
    return s


def contact(theme):
    s = Svg(theme, W, 186)
    section_header(s, "05", "CONTACT")
    size = 54
    while sum(FONTS[f].width(t, size) for t, f, _ in CONTACT["title"]) > W and size > 30:
        size -= 1
    with s.g("rise", 0.1):
        s.rich(-2, 104, CONTACT["title"], size)
    with s.g("rise", 0.25):
        s.text(0, 146, CONTACT["line"], "sans", 17, "fg2")
    return s


def icon(s, kind, x, y):
    st = s.paint("stroke", "fg2")
    g = f'<g transform="translate({n(x)} {n(y)})" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">'
    if kind == "globe":
        g += (f'<circle cx="8" cy="8" r="7" {st}/><ellipse cx="8" cy="8" rx="3" ry="7" {st}/>'
              f'<path d="M1 8h14" {st}/>')
    elif kind == "in":
        g += (f'<rect x="1" y="1" width="14" height="14" rx="3" {st}/><path d="M5 7v5M5 4.6v.1M8 12V7m0 2.4c0-1.5 3.6-2 3.6 .4V12" {st}/>')
    elif kind == "mail":
        g += f'<rect x="1" y="3" width="14" height="10" rx="2" {st}/><path d="M1.5 4l6.5 5 6.5-5" {st}/>'
    elif kind == "up":
        g += f'<path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" {st}/>'
    s.add(g + "</g>")


def button(theme, label, kind, arrow="↗"):
    f = FONTS["sans-m"]
    tw = f.width(label, 15)
    aw = FONTS["sans"].width(arrow, 15)
    w = 18 + 16 + 10 + tw + 10 + aw + 18
    s = Svg(theme, w + 10, 46)
    with s.g("fade"):
        s.add(f'<rect x=".5" y=".5" width="{n(w - 1)}" height="45" rx="22.5" {s.paint("fill", "surface")} {s.paint("stroke", "line")}/>')
        icon(s, kind, 18, 15)
        s.text(44, 28.5, label, "sans-m", 15, "fg")
        with s.g("nudge"):
            s.text(44 + tw + 10, 28.5, arrow, "sans", 15, "accentText")
    return s

# ────────────────────────────────────────────────────────────────── DATA ──

QUERY = """query($login:String!){user(login:$login){
  pub: repositories(ownerAffiliations:OWNER,privacy:PUBLIC){totalCount}
  own: repositories(ownerAffiliations:OWNER,isFork:false,privacy:PUBLIC,first:100){nodes{
    languages(first:10,orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}}}}
  contributionsCollection{contributionCalendar{totalContributions weeks{contributionDays{date contributionCount contributionLevel}}}}
}}"""

LEVEL = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


def github_token():
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("Set GITHUB_TOKEN (or log in with `gh auth login`) to fetch profile data.")


def fetch():
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": LOGIN}}).encode(),
        headers={"Authorization": f"Bearer {github_token()}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)
    if "errors" in body:
        sys.exit(f"GitHub API error: {body['errors']}")
    user = body["data"]["user"]
    cal = user["contributionsCollection"]["contributionCalendar"]
    days = [d for w in cal["weeks"] for d in w["contributionDays"]]

    streak = best = 0
    for d in days:
        streak = streak + 1 if d["contributionCount"] else 0
        best = max(best, streak)

    sizes = defaultdict(int)
    for repo in user["own"]["nodes"]:
        for e in repo["languages"]["edges"]:
            if e["node"]["name"] not in IGNORED_LANGUAGES:
                sizes[e["node"]["name"]] += e["size"]
    total = sum(sizes.values()) or 1
    ranked = sorted(sizes.items(), key=lambda kv: -kv[1])
    langs = [(name, size * 100 / total) for name, size in ranked[:4]]
    rest = 100 - sum(p for _, p in langs)
    if rest >= 0.5:
        langs.append(("Other", rest))

    return dict(
        total=cal["totalContributions"],
        active=sum(1 for d in days if d["contributionCount"]),
        streak=best,
        repos=user["pub"]["totalCount"],
        weeks=[[(d["date"], LEVEL[d["contributionLevel"]]) for d in w["contributionDays"]] for w in cal["weeks"]],
        languages=langs,
        synced=dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y").upper(),
    )

# ────────────────────────────────────────────────────────────────── MAIN ──

def main():
    data = fetch()
    for theme in THEMES:
        files = {
            "hero": hero(theme),
            "about": about(theme),
            "stack": stack(theme),
            "work": work_header(theme),
            "numbers": numbers(theme, data),
            "contact": contact(theme),
            "top": button(theme, "Back to top", "up", "↑"),
        }
        for num, label, key in NAV:
            files[f"nav-{key}"] = nav_pill(theme, num, label)
        for i, item in enumerate(WORK):
            files[f"work-{item['key']}"] = card(theme, item, left=i % 2 == 0)
        for key, label, kind in CONTACT["buttons"]:
            files[f"btn-{key}"] = button(theme, label, kind)

        os.makedirs(os.path.join(OUT, theme), exist_ok=True)
        for name, svg in files.items():
            with open(os.path.join(OUT, theme, f"{name}.svg"), "w", encoding="utf-8") as fh:
                fh.write(svg.render())
    print(f"rendered {len(files) * len(THEMES)} svgs · {data['total']} contributions · "
          + ", ".join(f"{k} {v:.1f}%" for k, v in data["languages"]))


if __name__ == "__main__":
    main()
