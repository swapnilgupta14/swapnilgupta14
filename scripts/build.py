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
import math
import os
import re
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
STATE = os.path.join(ROOT, "scripts", "state.json")
LOGIN = "swapnilgupta14"
W = 840  # GitHub's profile README column is ~830px, so 840 renders ~1:1

# Type scale: 12px sentences, 14px accents/subheadings, 16px headings.
# Display type (name, marquee, big numbers, sign-off) sits outside it.
TEXT, SUB, HEAD = 12, 14, 16

# One left-aligned grid for every section: four columns with a 24px gutter.
# Spec columns, stack items, cards and stats all start on these lines.
GUT = 24
COL = [0, 216, 432, 648]
LABEL = ("mono-m", TEXT, 1.2)  # uppercase mono labels

# ─────────────────────────────────────────────────────────────── CONTENT ──

HERO = dict(
    kicker="SWAPNILGUPTA.IN",
    status="CURRENTLY AT SIMPPLR",
    name="Swapnil Gupta",
    lines=[
        [("Full-stack developer working across AI/ML, web & mobile,", "sans", "fg2")],
        [("and genuinely curious about ", "sans", "fg2"), ("all things tech.", "serif-i", "accent")],
    ],
    specs=[
        ("ROLE", "Full-Stack Developer"),
        ("FOCUS", "AI/ML, web & mobile"),
        ("STACK", "TypeScript & Python"),
        ("BASED", "Earth-616"),
    ],
)

# Two rows scrolling in opposite directions: what I do, then what I use.
# The README twin of the "Ask" composer on swapnilgupta.in: same prompts,
# same 4.5s rotation. README.md links it to ?ask=1#ask on the site.
COMPOSER = dict(
    prompts=["Ask about my experience", "Ask about my projects", "What do I work on at Simpplr?",
             "Explore my work, skills & writing", "Ask sAI, the assistant on this site", "How do I get in touch?"],
    heading=[("Have a question? ", "sans-m", "fg"), ("Ask sAI", "sans-m", "accentText")],
    sub="The assistant on my site knows my projects, experience and writing.",
    slot=4.5,
    width=COL[3] - GUT,  # three grid columns (~75%), ending a gutter before BASED
)

MARQUEE = dict(
    themes=["Full-stack development", "AI & machine learning", "LLM apps & agents",
            "Web & mobile", "Web3", "Design-minded engineering"],
    tools=["TypeScript", "Python", "React", "Next.js", "Node.js", "FastAPI", "LLM APIs",
           "TensorFlow", "React Native", "Expo", "Tailwind CSS", "MySQL"],
)

ABOUT = [
    [("I work across the whole stack — ", "fg2"), ("models, APIs and interfaces", "accentText"),
     (" — mostly in TypeScript and Python, for the web and mobile.", "fg2")],
    [("Outside the day job I’m usually deep in ", "fg2"), ("what’s next", "accentText"),
     (": LLM agents, RAG and Web3.", "fg2")],
]

# (label, [(item, daily_driver)])
STACK = [
    ("LANGUAGES", [("TypeScript", 1), ("Python", 1), ("JavaScript", 1), ("C++", 0), ("SQL", 0)]),
    ("FRONTEND", [("React", 1), ("Next.js", 1), ("Tailwind CSS", 1), ("Redux Toolkit", 0), ("SCSS", 0), ("GSAP", 0), ("Framer Motion", 0)]),
    ("BACKEND", [("Node.js", 1), ("FastAPI", 1), ("Express", 0), ("MySQL", 0)]),
    ("AI & ML", [("LLM APIs", 1), ("TensorFlow", 0), ("Keras", 0), ("Pandas", 0), ("Streamlit", 0)]),
    ("MOBILE", [("React Native", 1), ("Expo", 1), ("NativeWind", 0)]),
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
    dict(key="stocks", name="Stock Trend Predictor", domain="github.com", glyph="chart",
         blurb="LSTM-based stock forecasting trained on historical prices, served as a Streamlit app.",
         tags="PYTHON · DEEP LEARNING"),
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
        self.out, self.defs, self.css = [], [], []
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

    def label(self, x, y, s, fill="fg3", align="start"):
        face, size, tracking = LABEL
        return self.text(x, y, s, face, size, fill, tracking, align)

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

    def sparkle(self, cx, cy, r):
        k = r * 0.22
        self.add(f'<path d="M{n(cx)} {n(cy - r)}Q{n(cx + k)} {n(cy - k)} {n(cx + r)} {n(cy)}Q{n(cx + k)} {n(cy + k)} '
                 f'{n(cx)} {n(cy + r)}Q{n(cx - k)} {n(cy + k)} {n(cx - r)} {n(cy)}Q{n(cx - k)} {n(cy - k)} {n(cx)} {n(cy - r)}Z" '
                 f'{self.paint("fill", "accent")}/>')

    def edge_fade(self, uid, h):
        """Mask that fades content out at the left and right edges."""
        self.defs.append(
            f'<linearGradient id="{uid}g"><stop offset="0" stop-color="#fff" stop-opacity="0"/>'
            f'<stop offset=".09" stop-color="#fff"/><stop offset=".91" stop-color="#fff"/>'
            f'<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>'
            f'<mask id="{uid}" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{n(h)}">'
            f'<rect width="{W}" height="{n(h)}" fill="url(#{uid}g)"/></mask>')
        return f' mask="url(#{uid})"'

    def render(self):
        faces, classes = [], []
        for key in sorted(self.chars):
            f = FONTS[key]
            faces.append(f"@font-face{{font-family:'{f.family}';src:url(data:font/woff2;base64,{f.woff2(self.chars[key])}) "
                         f"format('woff2');font-style:{f.style};font-weight:{f.weight}}}")
            classes.append(f".{key}{{font-family:'{f.family}',{f.fallback};font-style:{f.style};font-weight:{f.weight}}}")
        css = "\n".join(faces + classes + self.css) + MOTION
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{n(self.w)}" height="{n(self.h)}" '
                f'viewBox="0 0 {n(self.w)} {n(self.h)}" fill="none">\n<style>{css}</style>\n{defs}\n'
                + "\n".join(self.out) + "\n</svg>\n")


def wrap(runs, width, size):
    """Greedy word wrap over styled runs (text, face, fill[, size]) -> lines of runs."""
    words = []
    for run in runs:
        text, face, fill = run[:3]
        sz = run[3] if len(run) > 3 else size
        for i, word in enumerate(text.split(" ")):
            words.append((("" if i == 0 else " ") + word, face, fill, sz))
    lines, line, lw = [], [], 0.0
    for word, face, fill, sz in words:
        ww = FONTS[face].width(word, sz)
        if line and lw + ww > width and word.startswith(" "):
            lines.append(line)
            line, lw, word = [], 0.0, word[1:]
            ww = FONTS[face].width(word, sz)
        if line and line[-1][1:] == [face, fill, sz]:
            line[-1][0] += word
        else:
            line.append([word, face, fill, sz])
        lw += ww
    if line:
        lines.append(line)
    return [[tuple(r) for r in ln if r[0]] for ln in lines]


def marquee(s, uid, pieces, speed, reverse=False):
    """Seamless loop: `pieces` is a list of (draw(x), width); the cycle is
    repeated until it covers the canvas twice, then slid by one cycle."""
    cycle = sum(w for _, w in pieces)
    s.add(f'<g class="{uid}">')
    x = 0.0
    for _ in range(int(W // cycle) + 2):
        for draw, w in pieces:
            draw(x)
            x += w
    s.add("</g>")
    edge = "from" if reverse else "to"
    s.css.append(f".{uid}{{animation:{uid} {n(cycle / speed)}s linear infinite}}"
                 f"@keyframes {uid}{{{edge}{{transform:translateX(-{n(cycle)}px)}}}}")


def section_header(svg, num, title, y=22, meta=None):
    with svg.g("rise"):
        w1 = svg.text(0, y, num, "mono-m", SUB, "accentText", 0.5)
        w2 = svg.text(w1 + 10, y, title, "sans-m", HEAD, "fg", -0.1)
        right = W
        if meta:
            right = W - svg.label(W, y, meta, align="end") - 16
    svg.hline(w1 + w2 + 26, right, y - 5, "line2", "grow", 0.15)

# ──────────────────────────────────────────────────────────────── PIECES ──

def views_badge(s, views, cx, cy, r=52):
    """Circular seal: the label runs round a slowly turning ring and the
    count rolls up in the middle under a small, glancing eye."""
    face, size, tracking = "mono-m", 10.5, 1.2
    ring = r - 13  # radius of the text baseline
    s.defs.append(f'<path id="ring" d="M{n(cx - ring)} {n(cy)}a{ring} {ring} 0 1 1 {2 * ring} 0a{ring} {ring} 0 1 1 {-2 * ring} 0"/>')
    label = "PROFILE VIEWS"
    s.chars[face].update(label + " \u2022")
    parts = "".join(f'<tspan {s.paint("fill", "fg2")}>{label}</tspan><tspan {s.paint("fill", "accent")}> \u2022 </tspan>'
                    for _ in range(2))
    s.css.append(f".spin{{transform-origin:{n(cx)}px {n(cy)}px;animation:spin 26s linear infinite}}"
                 "@keyframes spin{to{transform:rotate(360deg)}}"
                 ".look{animation:look 5s ease-in-out infinite}"
                 "@keyframes look{0%,35%,100%{transform:translateX(0)}45%,60%{transform:translateX(-1.6px)}70%,90%{transform:translateX(1.6px)}}")
    with s.g("fade", 0.4):
        s.add(f'<circle cx="{n(cx)}" cy="{n(cy)}" r="{r - .5}" {s.paint("fill", "surface")} {s.paint("stroke", "line")}/>')
        s.add(f'<circle cx="{n(cx)}" cy="{n(cy)}" r="{ring - 11}" {s.paint("stroke", "line2")}/>')
        s.add(f'<g class="spin"><text class="{face}" font-size="{size}" letter-spacing="{n(tracking)}">'
              f'<textPath href="#ring" xlink:href="#ring" textLength="{n(2 * math.pi * ring)}" lengthAdjust="spacing">'
              f'{parts}</textPath></text></g>')
        ex, ey = cx - 7, cy - 15
        s.add(f'<g transform="translate({n(ex)} {n(ey)}) scale(.875)" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
              f'<path d="M1 8s2.6-5 7-5 7 5 7 5-2.6 5-7 5-7-5-7-5Z" {s.paint("stroke", "fg2")}/>'
              f'<g class="look"><circle cx="8" cy="8" r="2.3" {s.paint("fill", "accent")}/></g></g>')
    value = f"{views:,}"
    vw = FONTS["sans-m"].width(value, SUB)
    with s.g("rise", 0.5):
        stat_number(s, cx - vw / 2, cy + 13, value, SUB, 0.7, "views", face="sans-m")


def hero(theme, views):
    s = Svg(theme, W, 0)
    with s.g("rise"):
        kw = s.label(0, 20, HERO["kicker"])
        s.text(kw + 3, 20, "↗", "sans", 12, "accentText")
        sw = s.label(W, 20, HERO["status"], "fg2", align="end")
        cx = W - sw - 11
        s.add(f'<circle cx="{n(cx)}" cy="15.8" r="3.5" {s.paint("fill", "accent")} class="pulse"/>')
        s.add(f'<circle cx="{n(cx)}" cy="15.8" r="3.5" {s.paint("fill", "accent")}/>')
    s.hline(0, W, 40, "line", "grow", 0.1)
    with s.g("rise", 0.12):
        s.text(-3, 150, HERO["name"], "serif", 83, "fg", -0.8)
    accent = SUB + 2  # the italic serif runs small; +2px matches Geist's x-height
    for i, line in enumerate(HERO["lines"]):
        with s.g("rise", 0.28 + i * 0.1):
            runs = [(t, f, c, accent if f.startswith("serif") else SUB) for t, f, c in line]
            s.rich(0, 190 + i * 22, runs, SUB)
    # hand-drawn underline under the italic phrase
    last = HERO["lines"][-1]
    ly = 190 + (len(HERO["lines"]) - 1) * 22
    views_badge(s, views, W - 53, (40 + ly + 40) / 2)  # centred between the two hairlines
    x0 = FONTS["sans"].width(last[0][0], SUB)
    x1 = x0 + FONTS["serif-i"].width(last[1][0].rstrip("."), accent)
    s.add(f'<path d="M{n(x0)} {ly + 5} Q{n((x0 + x1) / 2)} {ly + 8} {n(x1 + 3)} {ly + 4}" pathLength="1" '
          f'{s.paint("stroke", "accent")} stroke-width="1.3" stroke-linecap="round" class="draw" style="animation-delay:1s"/>')
    s.hline(0, W, ly + 40, "line", "grow", 0.35)
    for i, (label, value) in enumerate(HERO["specs"]):
        with s.g("rise", 0.5 + i * 0.08):
            s.label(COL[i], ly + 70, label)
            s.text(COL[i], ly + 94, value, "sans", HEAD, "fg")
    s.h = ly + 104  # tight bottom: the composer sits right under the specs
    return s


def composer(theme):
    """Ask sAI: heading and a one-line explainer flush left, then a chat
    bar spanning the first three grid columns. In the bar the site's prompts
    type out one by one behind a caret; all motion is CSS keyframes on one
    shared cycle, so it loops with no script. Padding gives equal visible
    space above the heading and below the bar."""
    width, h = COMPOSER["width"], 50  # width of the bar itself
    top, bottom = 58, 64  # + hero tail and README margins = ~80px of air on both sides
    y_head = top + 12
    y_sub = y_head + 22
    y0 = y_sub + 20
    cy = y0 + h / 2
    s = Svg(theme, W, y0 + h + bottom)
    prompts, slot = COMPOSER["prompts"], COMPOSER["slot"]
    cycle = len(prompts) * slot
    pct = lambda t: n(max(0.0, t) / cycle * 100)
    f, x0 = FONTS["sans"], 44

    s.css.append(".twinkle{transform-box:fill-box;transform-origin:50% 50%;animation:twinkle 2.4s ease-in-out infinite}"
                 "@keyframes twinkle{0%,100%{transform:scale(1) rotate(0)}50%{transform:scale(.72) rotate(45deg)}}")
    with s.g("rise", 0.1):
        s.rich(0, y_head, COMPOSER["heading"], HEAD)
    with s.g("rise", 0.2):
        s.text(0, y_sub, COMPOSER["sub"], "sans", TEXT, "fg3")

    with s.g("rise", 0.3):
        s.add(f'<rect x=".5" y="{n(y0 + .5)}" width="{n(width - 1)}" height="{h - 1}" rx="{n(h / 2 - .5)}" '
              f'{s.paint("fill", "surface")} {s.paint("stroke", "line")}/>')
        s.add('<g class="twinkle">')
        s.sparkle(24, cy, 6)
        s.add("</g>")

    caret = []
    for i, prompt in enumerate(prompts):
        w, k = f.width(prompt, SUB), len(prompt)
        t0 = i * slot
        typed = t0 + 0.045 * k + 0.15
        fade0, fade1 = t0 + slot - 0.55, t0 + slot - 0.25
        frames = [f"0%,{pct(t0 - 0.01)}%{{opacity:0}}"] if i else []
        frames += [f"{pct(t0)}%{{opacity:1;clip-path:inset(-4px 100% -6px 0);animation-timing-function:steps({k},end)}}",
                   f"{pct(typed)}%{{clip-path:inset(-4px 0 -6px 0)}}",
                   f"{pct(fade0)}%{{opacity:1}}", f"{pct(fade1)}%,100%{{opacity:0}}"]
        s.css.append(f".p{i}{{animation:p{i} {n(cycle)}s linear infinite}}@keyframes p{i}{{{''.join(frames)}}}")
        s.chars["sans"].update(prompt)
        hidden = ' opacity="0"' if i else ""
        s.add(f'<text x="{x0}" y="{n(cy + 5)}" class="sans p{i}" font-size="{SUB}" {s.paint("fill", "fg2")}{hidden}>{esc(prompt)}</text>')
        nxt = (i + 1) * slot
        caret += [f"{pct(t0)}%{{transform:translateX(0);opacity:1;animation-timing-function:steps({k},end)}}",
                  f"{pct(typed)}%{{transform:translateX({n(w + 2)}px)}}",
                  f"{pct(fade0)}%{{opacity:1}}", f"{pct(fade1)}%{{opacity:0}}",
                  f"{pct(nxt - 0.01)}%{{transform:translateX({n(w + 2)}px);opacity:0}}"]
    s.css.append(f".caret{{animation:caret {n(cycle)}s linear infinite}}@keyframes caret{{{''.join(caret)}}}")
    w0 = f.width(prompts[0], SUB)
    s.add(f'<g class="caret" transform="translate({n(w0 + 2)} 0)"><rect x="{x0}" y="{n(cy - 8)}" width="1.6" height="16" rx=".8" '
          f'{s.paint("fill", "accent")} class="blink"/></g>')

    bx, br = width - 7 - 18, 18
    ink = "#14110F"
    s.add(f'<circle cx="{n(bx)}" cy="{n(cy)}" r="{br}" {s.paint("fill", "accent")}/>'
          f'<g class="nudge"><path d="M{n(bx - 5)} {n(cy + 5)}L{n(bx + 3.5)} {n(cy - 3.5)}M{n(bx - 2.5)} {n(cy - 3.5)}H{n(bx + 3.5)}V{n(cy + 2.5)}" '
          f'stroke="{ink}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></g>')

    # hand-drawn arrow in the empty fourth column, looping in to point at the bar
    ex = width + 12  # arrow tip, a little clear of the bar's edge
    loop = (f"M{n(ex + 170)} {n(cy - 30)}"
            f"C{n(ex + 150)} {n(cy - 44)} {n(ex + 110)} {n(cy - 40)} {n(ex + 104)} {n(cy - 20)}"
            f"C{n(ex + 99)} {n(cy - 4)} {n(ex + 122)} {n(cy + 2)} {n(ex + 124)} {n(cy - 12)}"
            f"C{n(ex + 126)} {n(cy - 28)} {n(ex + 88)} {n(cy - 26)} {n(ex + 64)} {n(cy - 12)}"
            f"C{n(ex + 44)} {n(cy - 1)} {n(ex + 22)} {n(cy + 4)} {n(ex)} {n(cy + 1)}")
    head = f"M{n(ex + 11)} {n(cy - 8)}L{n(ex)} {n(cy + 1)}L{n(ex + 12)} {n(cy + 9)}"
    s.css.append(".point{animation:point 2.6s ease-in-out 3s infinite}"
                 "@keyframes point{0%,100%{transform:translateX(0)}50%{transform:translateX(-5px)}}")
    stroke = f'{s.paint("stroke", "accent")} stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"'
    s.add(f'<g class="point"><path d="{loop}" pathLength="1" {stroke} class="draw" style="animation-delay:.9s"/>'
          f'<path d="{head}" pathLength="1" {stroke} class="draw" style="animation-delay:2.2s;animation-duration:.5s"/></g>')
    return s


def band(theme):
    """Two marquees between hairlines: themes drifting left, mono tools drifting right."""
    h = 128
    s = Svg(theme, W, h)
    s.hline(0, W, 0, "line2")
    s.hline(0, W, h - 1, "line2")
    mask = s.edge_fade("edge", h)
    pieces = []
    for i, phrase in enumerate(MARQUEE["themes"]):
        fill = "fg" if i % 2 == 0 else "fg3"
        w = FONTS["serif"].width(phrase, 34)
        pieces.append((lambda x, p=phrase, c=fill: s.text(x, 62, p, "serif", 34, c), w))
        pieces.append((lambda x: s.sparkle(x + 22, 50, 6), 44))
    s.add(f"<g{mask}>")
    marquee(s, "mqa", pieces, 28)
    tools = []
    for tool in MARQUEE["tools"]:
        label = tool.upper()
        w = FONTS["mono-m"].width(label, TEXT, 1.6)
        tools.append((lambda x, t=label: s.text(x, 97, t, "mono-m", TEXT, "fg3", 1.6), w))
        tools.append((lambda x: s.text(x + 13, 97, "·", "mono-m", TEXT, "dim"), 32))
    marquee(s, "mqb", tools, 20, reverse=True)
    s.add("</g>")
    return s


def about(theme):
    s = Svg(theme, W, 0)
    section_header(s, "01", "About")
    y = last = 70
    for para in ABOUT:
        for line in wrap([(t, "sans", c) for t, c in para], 540, SUB):
            with s.g("rise", 0.1 + (y - 70) / 24 * 0.07):
                s.rich(0, y, line, SUB)
            last = y
            y += 24
        y += 12
    s.h = last + 22
    return s


def stack(theme):
    rows = len(STACK)
    top, step = 70, 40
    s = Svg(theme, W, top + (rows - 1) * step + 20)
    section_header(s, "02", "Stack", meta="BRIGHT = DAILY DRIVERS")
    f = FONTS["sans"]
    for i, (label, items) in enumerate(STACK):
        y = top + i * step
        with s.g("rise", 0.1 + i * 0.07):
            s.label(0, y, label)
            x = COL[1]
            for j, (item, daily) in enumerate(items):
                if j:
                    s.text(x + 8, y, "/", "sans", SUB, "dim")
                    x += 8 + f.width("/", SUB) + 8
                x += s.text(x, y, item, "sans", SUB, "fg" if daily else "fg3")
        if i < rows - 1:
            s.hline(0, W, y + 17, "line2", "grow", 0.2 + i * 0.07)
    return s


def work_header(theme):
    s = Svg(theme, W, 34)
    section_header(s, "03", "Selected work", meta="EVERY CARD OPENS THE PROJECT")
    return s


def glyph(s, kind, x, y, k=0.7):
    st = s.paint("stroke", "fg2")
    ac = s.paint("stroke", "accent")
    fa = s.paint("fill", "accent")
    g = (f'<g transform="translate({n(x)} {n(y)}) scale({n(k)})" stroke-width="1.8" '
         f'stroke-linecap="round" stroke-linejoin="round">')
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
        for px, py in pts[:3]:
            g += f'<circle cx="{px}" cy="{py}" r="3.4" {st}/>'
        g += f'<circle cx="21" cy="22" r="3.4" {fa} class="pulse"/><circle cx="21" cy="22" r="3.4" {fa}/>'
    elif kind == "chart":
        g += (f'<path d="M2 36H38" {st} stroke-opacity=".5"/>'
              f'<path d="M3 29L11 22L17 26L25 13L31 17" pathLength="1" {st} class="draw" style="animation-delay:.3s"/>'
              f'<path d="M31 17L37 7" pathLength="1" {ac} stroke-dasharray="2 3" class="fade" style="animation-delay:1.5s"/>'
              f'<circle cx="37" cy="7" r="2.6" {fa} class="fade" style="animation-delay:1.7s"/>')
    s.add(g + "</g>")


def card(theme, item, left):
    cw, ch, pad = COL[2] - GUT, 196, 22  # left card 0..408, right card 432..840
    s = Svg(theme, W / 2, ch)
    ox = 0 if left else COL[2] - W / 2
    with s.g("fade"):
        s.add(f'<rect x="{n(ox + .5)}" y=".5" width="{cw - 1}" height="{ch - 1}" rx="14" '
              f'{s.paint("fill", "surface")} {s.paint("stroke", "line")}/>')
    glyph(s, item["glyph"], ox + pad, 20)
    ax, ay = ox + cw - pad - 12, 34
    s.add(f'<circle cx="{n(ax)}" cy="{ay}" r="12" {s.paint("stroke", "line")}/>'
          f'<g class="nudge"><path d="M{n(ax - 3.5)} {ay + 3.5}L{n(ax + 3.5)} {ay - 3.5}M{n(ax - 2)} {ay - 3.5}H{n(ax + 3.5)}V{ay + 2}" '
          f'{s.paint("stroke", "accentText")} stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></g>')
    with s.g("rise", 0.15):
        s.text(ox + pad, 88, item["name"], "sans-m", HEAD, "fg", -0.1)
    with s.g("rise", 0.25):
        for i, line in enumerate(wrap([(item["blurb"], "sans", "fg2")], cw - 2 * pad, TEXT)[:2]):
            s.rich(ox + pad, 110 + i * 18, line, TEXT)
    s.hline(ox + pad, ox + cw - pad, 148, "line2")
    with s.g("rise", 0.35):
        tw = s.label(ox + pad, 174, item["tags"])
        dw = s.text(ox + cw - pad, 174, item["domain"], "mono", TEXT, "fg2", align="end")
    if tw + dw + 16 > cw - 2 * pad:
        sys.exit(f"{item['name']}: tags and domain overlap; shorten one of them")
    return s


def stat_number(s, x, base, value, size, delay, uid, face="serif"):
    """Odometer: each digit is a 0-9 strip that rolls up to its value."""
    f = FONTS[face]
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
                parts.append(f'<text x="{n(cx + (cw - kw) / 2)}" y="{n(base + k * step)}" class="{face}" '
                             f'font-size="{n(size)}" {s.paint("fill", "fg")}>{k}</text>')
            parts.append("</g>")
            s.chars[face].update("0123456789")
        else:
            parts.append(f'<text x="{n(cx)}" y="{n(base)}" class="{face}" font-size="{n(size)}" {s.paint("fill", "fg")}>{esc(ch)}</text>')
            s.chars[face].add(ch)
        cx += cw
    parts.append("</g>")
    s.add("".join(parts))


def monster(s, cols, stepx, cell, top, step_t=0.05, pause=1.2, regrow=0.6):
    """A small horned monster walks the heatmap column by column (down, up,
    down...) on a SMIL motion path. Every lit cell runs the same `eat` loop,
    offset by the moment the monster reaches it, so it pops and vanishes as
    the monster passes and grows back `regrow` of a lap later, before the
    next pass. Returns (eat_at(col, row) -> seconds, monster markup)."""
    entry = 30
    cx = lambda c: c * stepx + cell / 2
    cy = lambda r: top + r * stepx + cell / 2
    d = [f"M{n(cx(0) - entry)} {n(cy(0))}H{n(cx(0))}"]
    for c in range(cols):
        d.append(f"V{n(cy(6 if c % 2 == 0 else 0))}")
        if c < cols - 1:
            d.append(f"H{n(cx(c + 1))}")
    d.append(f"H{n(cx(cols - 1) + entry)}")
    speed = stepx / step_t
    move = (2 * entry + (7 * cols - 1) * stepx) / speed
    lap = move + pause

    def eat_at(c, r):
        steps = c * 7 + (r if c % 2 == 0 else 6 - r)
        return (entry + steps * stepx) / speed

    pct = lambda t: n(t / lap * 100)
    s.css.append(
        f".eat{{transform-box:fill-box;transform-origin:50% 50%;animation:eat {n(lap)}s linear infinite}}"
        f"@keyframes eat{{0%{{transform:scale(1);opacity:1}}{pct(.08)}%{{transform:scale(1.45);opacity:1}}"
        f"{pct(.3)}%{{transform:scale(0);opacity:0}}{pct(lap * regrow)}%{{transform:scale(0);opacity:0}}"
        f"{pct(lap * regrow + .5)}%{{transform:scale(1);opacity:1}}100%{{transform:scale(1);opacity:1}}}}"
        ".bob{animation:bob .3s ease-in-out infinite alternate}@keyframes bob{from{transform:translateY(-1.2px)}}"
        ".chomp{transform-box:fill-box;transform-origin:50% 50%;animation:chomp .15s ease-in-out infinite alternate}"
        "@keyframes chomp{from{transform:scaleY(.2)}}"
        "@media (prefers-reduced-motion:reduce){.monster{display:none}}")
    ink, white = "#14110F", "#FFFFFF"
    markup = (
        f'<g class="monster"><g><animateMotion dur="{n(lap)}s" repeatCount="indefinite" calcMode="linear" '
        f'keyPoints="0;1;1" keyTimes="0;{n(move / lap)};1" path="{"".join(d)}"/><g class="bob">'
        f'<path d="M-5.6 -4.6L-7 -10.5L-2.4 -6.6ZM5.6 -4.6L7 -10.5L2.4 -6.6Z" {s.paint("fill", "accent")}/>'
        f'<circle r="7.6" {s.paint("fill", "accent")}/>'
        f'<circle cx="-2.8" cy="-1.9" r="2.4" fill="{white}"/><circle cx="2.8" cy="-1.9" r="2.4" fill="{white}"/>'
        f'<circle cx="-2.5" cy="-1.4" r="1.15" fill="{ink}"/><circle cx="3.1" cy="-1.4" r="1.15" fill="{ink}"/>'
        f'<ellipse cy="3.4" rx="3.3" ry="1.9" fill="{ink}" class="chomp"/>'
        f'</g></g></g>')
    return eat_at, markup


def numbers(theme, data):
    s = Svg(theme, W, 0)
    section_header(s, "04", "In numbers", meta=f"SYNCED {data['synced']}")
    stats = [("CONTRIBUTIONS · 1Y", data["total"]), ("ACTIVE DAYS · 1Y", data["active"]),
             ("LONGEST STREAK", data["streak"]), ("PUBLIC REPOS", data["repos"])]
    for i, (label, value) in enumerate(stats):
        x = COL[i]
        with s.g("rise", 0.1 + i * 0.08):
            stat_number(s, x - 1, 106, f"{value:,}", 58, 0.25 + i * 0.1, f"n{i}")
            s.label(x, 134, label)

    # contribution heatmap
    weeks = data["weeks"]
    stepx = W / len(weeks)
    cell = stepx - 3.4
    top = 192
    last_label = -99
    for wi, week in enumerate(weeks):
        first = dt.date.fromisoformat(week[0][0])
        if first.day <= 7 and wi * stepx - last_label > 40 and wi > 0:
            s.text(wi * stepx, top - 10, first.strftime("%b").upper(), "mono", TEXT, "fg3", 0.6)
            last_label = wi * stepx
    levels = [None, 0.28, 0.5, 0.75, 1]
    eat_at, critter = monster(s, len(weeks), stepx, cell, top)
    for wi, week in enumerate(weeks):
        cells = []
        for date, level in week:
            row = (dt.date.fromisoformat(date).weekday() + 1) % 7
            x, y = wi * stepx, top + row * stepx
            # every slot keeps an empty cell, so an eaten one leaves the grid intact
            cells.append(f'<rect x="{n(x)}" y="{n(y)}" width="{n(cell)}" height="{n(cell)}" rx="2.5" {s.paint("fill", "cell0")}/>')
            if level:
                cells.append(f'<rect x="{n(x)}" y="{n(y)}" width="{n(cell)}" height="{n(cell)}" rx="2.5" '
                             f'{s.paint("fill", "accent")} fill-opacity="{levels[level]}" class="eat" '
                             f'style="animation-delay:{n(eat_at(wi, row))}s"/>')
        s.add(f'<g class="fade" style="animation-delay:{n(0.4 + wi * 0.018)}s">{"".join(cells)}</g>')
    s.add(critter)
    ly = top + 7 * stepx + 24
    with s.g("fade", 1.2):
        s.label(0, ly, "PAST 12 MONTHS")
        mw = s.label(W, ly, "MORE", align="end") - LABEL[2]
        lx = W - mw - 10 - 5 * 14
        for k in range(5):
            fill = s.paint("fill", "cell0") if k == 0 else f'{s.paint("fill", "accent")} fill-opacity="{levels[k]}"'
            s.add(f'<rect x="{n(lx + k * 14)}" y="{n(ly - 9.5)}" width="10" height="10" rx="2" {fill}/>')
        s.label(lx - 10, ly, "LESS", align="end")

    # languages
    ly2 = ly + 30
    s.hline(0, W, ly2, "line2", "grow", 0.6)
    langs = data["languages"]
    with s.g("rise", 0.7):
        s.label(0, ly2 + 38, "LANGUAGES · BY CODE VOLUME")
    fills = [("accent", 1), ("fg", 0.78), ("fg", 0.52), ("fg", 0.32), ("fg", 0.16)]
    bx, by, gap = 0.0, ly2 + 54, 4
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
            s.add(f'<circle cx="{n(lx + 3.5)}" cy="{n(by + 27.5)}" r="3.5" {s.paint("fill", tok)} fill-opacity="{op}"/>')
            lx += 12
            lx += s.text(lx, by + 32, name, "sans", TEXT, "fg2") + 6
            lx += s.text(lx, by + 32, f"{pct:.1f}%", "mono", TEXT, "fg3") + 24
    s.h = by + 44
    return s


def contact(theme):
    s = Svg(theme, W, 150)
    section_header(s, "05", "Contact")
    size = 54
    while sum(FONTS[f].width(t, size) for t, f, _ in CONTACT["title"]) > W and size > 30:
        size -= 1
    with s.g("rise", 0.1):
        s.rich(-2, 100, CONTACT["title"], size)
    with s.g("rise", 0.25):
        s.text(0, 132, CONTACT["line"], "sans", TEXT, "fg2")
    return s


def icon(s, kind, x, y):
    st = s.paint("stroke", "fg2")
    g = (f'<g transform="translate({n(x)} {n(y)}) scale(.875)" stroke-width="1.5" '
         f'stroke-linecap="round" stroke-linejoin="round">')
    if kind == "globe":
        g += (f'<circle cx="8" cy="8" r="7" {st}/><ellipse cx="8" cy="8" rx="3" ry="7" {st}/>'
              f'<path d="M1 8h14" {st}/>')
    elif kind == "in":
        g += (f'<rect x="1" y="1" width="14" height="14" rx="3" {st}/>'
              f'<path d="M5 7v5M5 4.6v.1M8 12V7m0 2.4c0-1.5 3.6-2 3.6 .4V12" {st}/>')
    elif kind == "mail":
        g += f'<rect x="1" y="3" width="14" height="10" rx="2" {st}/><path d="M1.5 4l6.5 5 6.5-5" {st}/>'
    elif kind == "up":
        g += f'<path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" {st}/>'
    s.add(g + "</g>")


def button(theme, label, kind, arrow="↗"):
    tw = FONTS["sans-m"].width(label, SUB)
    aw = FONTS["sans"].width(arrow, SUB)
    w = 16 + 14 + 9 + tw + 8 + aw + 16
    s = Svg(theme, w + 8, 40)
    with s.g("fade"):
        s.add(f'<rect x=".5" y=".5" width="{n(w - 1)}" height="39" rx="19.5" {s.paint("fill", "surface")} {s.paint("stroke", "line")}/>')
        icon(s, kind, 16, 13)
        s.text(39, 25, label, "sans-m", SUB, "fg")
        with s.g("nudge"):
            s.text(39 + tw + 8, 25, arrow, "sans", SUB, "accentText")
    return s

# ────────────────────────────────────────────────────────────────── DATA ──

QUERY = """query($login:String!){user(login:$login){
  pub: repositories(ownerAffiliations:OWNER,privacy:PUBLIC){totalCount}
  own: repositories(ownerAffiliations:OWNER,isFork:false,privacy:PUBLIC,first:100){nodes{
    languages(first:10,orderBy:{field:SIZE,direction:DESC}){edges{size node{name}}}}}
  contributionsCollection{contributionCalendar{totalContributions weeks{contributionDays{date contributionCount contributionLevel}}}}
}}"""

VIEWS_URL = f"https://komarev.com/ghpvc/?username={LOGIN}&style=flat-square"
LEVEL = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}


def github_token():
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("Set GITHUB_TOKEN (or log in with `gh auth login`) to fetch profile data.")


def profile_views():
    """README.md embeds an invisible komarev pixel that counts each profile
    visit. Reading the counter also bumps it, so state.json remembers how many
    reads we have made and subtracts them. Only CI reads; local builds reuse
    the last value so they never skew the count."""
    with open(STATE) as fh:
        state = json.load(fh)
    if os.environ.get("CI") == "true":
        req = urllib.request.Request(VIEWS_URL, headers={"User-Agent": "profile-readme-build"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = int(re.findall(r">([\d,]+)</text>", r.read().decode())[-1].replace(",", ""))
            state["reads"] += 1
            state["views"] = raw - state["reads"]
            with open(STATE, "w") as fh:
                json.dump(state, fh, indent=2)
                fh.write("\n")
        except Exception as err:  # counter outage: keep last known value
            print(f"views: keeping {state['views']} ({err})")
    return state["views"]


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
        views=profile_views(),
        synced=dt.datetime.now(dt.timezone.utc).strftime("%d %b %Y").upper(),
    )

# ────────────────────────────────────────────────────────────────── MAIN ──

def main():
    data = fetch()
    for theme in THEMES:
        files = {
            "hero": hero(theme, data["views"]),
            "composer": composer(theme),
            "band": band(theme),
            "about": about(theme),
            "stack": stack(theme),
            "work": work_header(theme),
            "numbers": numbers(theme, data),
            "contact": contact(theme),
            "top": button(theme, "Back to top", "up", "↑"),
        }
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
