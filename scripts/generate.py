#!/usr/bin/env python3
"""Generate the hand-drawn profile SVGs (header, works, and one merged stats image).

Python 3.11, standard library only.
Env: GITHUB_TOKEN (required), PROFILE_USER (default DRAG0NM), INCLUDE_PRIVATE=1 (optional).
"""
import base64
import datetime as dt
import json
import math
import os
import random
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from montage_path import MONTAGE_PATH  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
W = 840
LEFT, RIGHT = 40, 800

THEMES = {
    "light": dict(bg="#f6f5f2", ink="#171614", ink2="#43423f", ink3="#646360", ink4="#81807d",
                  accent="#c53443", fibre="#171614", fibre_op=0.028),
    "dark": dict(bg="#0c0b08", ink="#d8d7d5", ink2="#9f9e9c", ink3="#727170", ink4="#81807e",
                 accent="#944a4b", fibre="#d8d7d5", fibre_op=0.03),
}

SERIF = "'STIX','Noto Serif SC','Source Han Serif SC','Songti SC','SimSun',serif"
SERIF_I = "'STIX-Italic','STIX','Noto Serif SC','Source Han Serif SC','Songti SC','SimSun',serif"


def b64(name):
    return base64.b64encode((ASSETS / "fonts" / name).read_bytes()).decode()


# ---------------------------------------------------------------- rough helper
class Rough:
    """Tiny seeded rough.js-alike: wobbly, doubled, variable-width ink strokes."""

    def __init__(self, seed):
        self.r = random.Random(seed)

    def n(self, a):
        return self.r.uniform(-a, a)

    def _nodes(self, pts, wob, step=60):
        out = []
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            k = max(1, int(math.hypot(x2 - x1, y2 - y1) // step))
            for i in range(k):
                t = i / k
                out.append((x1 + (x2 - x1) * t, y1 + (y2 - y1) * t))
        out.append(pts[-1])
        return [(x + self.n(wob), y + self.n(wob)) for x, y in out]

    def stroke(self, pts, color, w=1.4, op=1.0, wob=1.2, taper=False, passes=2):
        groups = []
        for p in range(passes):
            f, o = (1.0, op) if p == 0 else (0.55, op * 0.5)
            nodes = self._nodes(pts, wob if p == 0 else wob * 1.3)
            n = len(nodes) - 1
            cw = w * f
            segs = []
            for i in range(n):
                (x1, y1), (x2, y2) = nodes[i], nodes[i + 1]
                cw = min(max(cw + self.n(0.12 * w), 0.6 * w * f), 1.4 * w * f)
                width = cw
                if taper:
                    t = (i + 1) / n
                    if t > 1 - taper:
                        width *= max(0.45, 1 - 0.55 * (t - (1 - taper)) / taper)
                dx, dy = x2 - x1, y2 - y1
                ln = math.hypot(dx, dy) or 1
                off = self.n(wob * 0.6)
                cx, cy = (x1 + x2) / 2 - dy / ln * off, (y1 + y2) / 2 + dx / ln * off
                segs.append(f'<path d="M{x1:.1f} {y1:.1f}Q{cx:.1f} {cy:.1f} {x2:.1f} {y2:.1f}" '
                            f'stroke-width="{width:.2f}"/>')
            groups.append(f'<g fill="none" stroke="{color}" stroke-linecap="round" '
                          f'stroke-linejoin="round" opacity="{o:.2f}">{"".join(segs)}</g>')
        return "".join(groups)

    def line(self, x1, y1, x2, y2, color, **kw):
        return self.stroke([(x1, y1), (x2, y2)], color, **kw)

    def ellipse(self, cx, cy, rx, ry, color, **kw):
        a0 = self.r.uniform(0, math.tau)
        pts = []
        steps = 26
        for i in range(steps + 1):
            a = a0 + math.tau * 1.1 * i / steps
            k = 1 + self.n(0.05) + 0.03 * (i / steps)
            pts.append((cx + math.cos(a) * rx * k, cy + math.sin(a) * ry * k))
        return self.stroke(pts, color, **kw)

    def hatch_under(self, pts, base, color, gap=7, op=0.35):
        """45-degree hairlines between a curve and a baseline."""
        xs = [p[0] for p in pts]
        xmin, xmax = min(xs), max(xs)

        def cy(x):
            for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
                if xa <= x <= xb:
                    return ya if xb == xa else ya + (yb - ya) * (x - xa) / (xb - xa)
            return base

        out = []
        x0 = xmin - (base - min(p[1] for p in pts))
        while x0 < xmax:
            t = max(0.0, xmin - x0)
            ts = t
            while x0 + t <= xmax and base - t >= cy(x0 + t) + 1 and t < base:
                t += 1
            if t - ts > 4:
                out.append(self.line(x0 + ts, base - ts, x0 + t, base - t, color,
                                     w=0.7, op=op, wob=0.5, passes=1))
            x0 += gap
        return "".join(out)


def smooth(pts, per=6):
    """Monotone cubic (Fritsch-Carlson) through pts, sampled densely; never overshoots."""
    n = len(pts)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(n - 1)]
    m = [d[0]] + [0 if d[i - 1] * d[i] <= 0 else (d[i - 1] + d[i]) / 2 for i in range(1, n - 1)] + [d[-1]]
    for i in range(n - 1):
        if d[i] == 0:
            m[i] = m[i + 1] = 0
        else:
            a, b = m[i] / d[i], m[i + 1] / d[i]
            r = math.hypot(a, b)
            if r > 3:
                m[i], m[i + 1] = 3 * a / r * d[i], 3 * b / r * d[i]
    out = []
    for i in range(n - 1):
        h = xs[i + 1] - xs[i]
        for j in range(per):
            t = j / per
            h00, h10, h01, h11 = 2*t**3 - 3*t**2 + 1, t**3 - 2*t**2 + t, -2*t**3 + 3*t**2, t**3 - t**2
            out.append((xs[i] + t * h, h00 * ys[i] + h10 * h * m[i] + h01 * ys[i + 1] + h11 * h * m[i + 1]))
    out.append(pts[-1])
    return out


def seed_of(obj):
    return zlib.crc32(json.dumps(obj, sort_keys=True, default=str).encode())


# ---------------------------------------------------------------- svg document
def paper(th):
    r = random.Random(7)

    def dots(w, h, n):
        s = []
        for _ in range(n):
            s.append(f'<circle cx="{r.uniform(0, w):.1f}" cy="{r.uniform(0, h):.1f}" '
                     f'r="{r.uniform(0.5, 1.2):.2f}" opacity="{r.uniform(0.6, 1):.2f}"/>')
        for _ in range(max(2, n // 4)):
            x, y = r.uniform(0, w - 8), r.uniform(0, h - 8)
            s.append(f'<path d="M{x:.1f} {y:.1f}l{r.uniform(2, 7):.1f} {r.uniform(-3, 3):.1f}" '
                     f'stroke="{th["fibre"]}" stroke-width="0.5" stroke-opacity="{th["fibre_op"] * 1.3:.3f}"/>')
        return "".join(s)

    f = th["fibre"]
    return (
        f'<pattern id="f1" width="137" height="113" patternUnits="userSpaceOnUse">'
        f'<g fill="{f}" fill-opacity="{th["fibre_op"]}">{dots(137, 113, 16)}</g></pattern>'
        f'<pattern id="f2" width="211" height="179" patternUnits="userSpaceOnUse">'
        f'<g fill="{f}" fill-opacity="{th["fibre_op"]}">{dots(211, 179, 22)}</g></pattern>'
        f'<pattern id="hl" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(98)">'
        f'<path d="M0 0V7" stroke="{f}" stroke-opacity="0.012" stroke-width="1"/></pattern>'
    )


def document(w, h, th, label, body):
    font_css = (
        "@font-face{font-family:'STIX';font-style:normal;font-weight:400 700;"
        f"src:url(data:font/woff2;base64,{FONT_REG}) format('woff2')}}"
        "@font-face{font-family:'STIX-Italic';font-style:italic;font-weight:400 700;"
        f"src:url(data:font/woff2;base64,{FONT_ITA}) format('woff2')}}"
        f".s{{font-family:{SERIF}}}.i{{font-family:{SERIF_I};font-style:italic}}"
        ".c{font-variant:small-caps;letter-spacing:.06em}"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        f'role="img" aria-label="{escape(label)}">'
        f'<title>{escape(label)}</title><style>{font_css}</style><defs>{paper(th)}</defs>'
        f'<rect width="{w}" height="{h}" fill="{th["bg"]}"/>'
        f'<rect width="{w}" height="{h}" fill="url(#f1)"/><rect width="{w}" height="{h}" fill="url(#f2)"/>'
        f'<rect width="{w}" height="{h}" fill="url(#hl)"/>{body}</svg>\n'
    )


def text(x, y, s, th, color="ink3", size=14, cls="i", anchor="start", extra=""):
    fill = th.get(color, color)
    return (f'<text x="{x}" y="{y}" class="{cls}" font-size="{size}" fill="{fill}" '
            f'text-anchor="{anchor}" {extra}>{escape(s)}</text>')


def caption(th, s):
    return text(LEFT, 38, s, th, "ink3", 14, "i c", extra='')


# ---------------------------------------------------------------- charts
def header(th, seed):
    g = Rough(seed)
    b = [text(LEFT, 98, "Drag0nM", th, "ink", 60, "i"),
         text(LEFT + 2, 136, "但行好事，莫问前程", th, "ink2", 17, "s", extra='letter-spacing="2"'),
         g.line(LEFT + 2, 162, LEFT + 50, 162, th["ink3"], w=1.2, op=0.8, wob=0.8),
         text(LEFT + 2, 196, "写一点工具，也写一点字。", th, "ink3", 15, "s")]
    b.append(f'<path transform="rotate(-4 705 96) translate(612 112)" d="{MONTAGE_PATH}" '
             f'fill="{th["accent"]}"/>')
    return document(W, 232, th, "Drag0nM", "".join(b))


# Filled in main(): the most recently pushed public repos, then the blog, which is pinned last.
RECENT_REPOS = 4
BLOG = ("落纸之间", "notes and essays, drablog.pages.dev", "https://drablog.pages.dev/")
WORKS = []


def short_desc(name, desc, limit=64):
    """A one-line blurb: drop a leading "Name — " restatement, cut at a word near the limit."""
    desc = (desc or "").strip()
    if " — " in desc and desc.split(" — ", 1)[0].lower().startswith(name.lower()):
        desc = desc.split(" — ", 1)[1]
    desc = desc.split(". ", 1)[0].rstrip(".")
    for article in ("A ", "An ", "The "):
        if desc.startswith(article):
            desc = desc[len(article):]
    if desc[1:2].islower():  # keep acronyms like "AI" as they are
        desc = desc[:1].lower() + desc[1:]
    if len(desc) > limit:
        head = desc[:limit]
        cut = head.rsplit(", ", 1)[0] if ", " in head else head.rsplit(" ", 1)[0] + "…"
        desc = cut.rstrip(",;:")
    return desc


def recent_works(repos, login):
    pub = [r for r in repos if not r["isPrivate"] and not r["isArchived"] and r["name"].lower() != login.lower()]
    pub.sort(key=lambda r: r["pushedAt"], reverse=True)
    return [(r["name"], short_desc(r["name"], r["description"]), r["url"]) for r in pub[:RECENT_REPOS]] + [BLOG]


def works_html(works):
    rows = []
    for i, (name, desc, url) in enumerate(works, 1):
        alt = escape(f"{name} — {desc}", {"\"": "&quot;"})
        rows.append(f'''<a href="{url}"><picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/work-{i}-dark.svg">
  <img src="assets/work-{i}-light.svg" alt="{alt}" width="840">
</picture></a><br>''')
    return "<p>\n" + "\n".join(rows) + "\n</p>"


def write_readme_works(works):
    """Rewrite the block between the works markers in README.md."""
    path = ROOT / "README.md"
    text_ = path.read_text(encoding="utf-8")
    start, end = "<!-- works:start -->", "<!-- works:end -->"
    i, j = text_.index(start) + len(start), text_.index(end)
    new = text_[:i] + "\n" + works_html(works) + "\n" + text_[j:]
    return write_if_changed(path, new)


def work(th, idx):
    name, desc, _ = WORKS[idx]
    g = Rough(100 + idx)
    first = idx == 0
    y = 78 if first else 32
    b = []
    if first:
        b.append(caption(th, "Works · 近作"))
        b.append(g.stroke([(24.5, y - 5.5), (28.5, y - 6.2), (31.5, y - 5.4)], th["accent"],
                          w=5.5, op=0.95, wob=0.4, passes=1))
    b.append(f'<text x="{LEFT}" y="{y}">'
             f'<tspan class="s" font-size="17" fill="{th["ink"]}">{escape(name)}</tspan>'
             f'<tspan class="s" font-size="14" fill="{th["ink4"]}" dx="12">—</tspan>'
             f'<tspan class="i" font-size="14" fill="{th["ink3"]}" dx="10">{escape(desc)}</tspan></text>')
    return document(W, 98 if first else 52, th, f"{name} — {desc}", "".join(b))


def footer_body(th):
    g = Rough(3)
    b = [g.line(LEFT + 2, 24, LEFT + 50, 24, th["ink4"], w=1.2, op=0.8, wob=0.8),
         text(RIGHT, 28, "© 2026 Drag0nM · Built with ink and GitHub Actions", th, "ink4", 13, "i", "end")]
    return "".join(b), 44


def languages_body(th, langs):
    top = langs[:6]
    g = Rough(seed_of(top))
    b = [caption(th, "Languages · 语言")]
    if not top:
        b.append(text(LEFT, 86, "No code yet.", th, "ink3", 15))
        return "".join(b), 120
    total = sum(v for _, v in langs)
    shares = [v / total for _, v in top]
    mx = max(shares)
    x0, rowh = 190, 36
    ops = [1, 0.85, 0.7, 0.58, 0.48, 0.4]
    for i, ((name, _), s) in enumerate(zip(top, shares)):
        y = 78 + i * rowh
        col = th["accent"] if i == 0 else th["ink"]
        ln = max(14, 500 * s / mx)
        k = max(4, int(ln / 7))
        pts = [(x0 + ln * j / k, y - 5 + g.n(0.8)) for j in range(k + 1)]
        b.append(text(LEFT, y, name, th, "ink", 16, "s"))
        b.append(g.stroke(pts, col, w=8.5, op=ops[i], wob=0.7, taper=min(0.5, 10 / ln)))
        b.append(text(round(x0 + ln + 14, 1), y, f"{s * 100:.1f}%", th, "ink3", 14, "i"))
    h = 78 + len(top) * rowh + 6
    return "".join(b), h


def commits_body(th, weeks, total):
    """weeks: list of (first_day:date, count) for the last 52 weeks."""
    first = next((i for i, (_, c) in enumerate(weeks) if c > 0), 0)
    start = min(max(0, first - 4), len(weeks) - 16)
    trimmed = start > 0
    weeks = weeks[start:]
    if trimmed:
        total = sum(c for _, c in weeks)
    g = Rough(seed_of([[str(d), c] for d, c in weeks]))
    top, base = 92, 205
    vals = [c for _, c in weeks]
    mx = max(max(vals), 1)
    n = len(weeks)
    xs = [LEFT + i * (RIGHT - LEFT) / (n - 1) for i in range(n)]
    ys = [base - math.sqrt(v / mx) * (base - top) for v in vals]  # sqrt: one huge week must not flatten the rest
    pts = smooth(list(zip(xs, ys)))
    pk = list(zip(xs, ys))
    b = [caption(th, "Commits · 手记")]
    b.append(g.hatch_under(pts, base, th["ink"], gap=7, op=0.3))
    b.append(g.line(LEFT - 4, base + 3, RIGHT + 4, base + 3, th["ink4"], w=1.1, op=0.9, wob=1.0))
    b.append(g.stroke(pts, th["ink"], w=1.9, op=0.92, wob=0.9))
    last_i = -10
    for i, (d, _) in enumerate(weeks):
        prev = weeks[i - 1][0].month if i else None
        if (i == 0 and d.day <= 7) or (i and d.month != prev):
            if i - last_i >= 3:
                b.append(text(round(xs[i], 1), base + 26, d.strftime("%b"), th, "ink3", 12, "i", "middle"))
                last_i = i
    if max(vals) > 0:
        pi = vals.index(max(vals))
        px, py = pk[pi]
        b.append(g.ellipse(px, py, 10, 9, th["accent"], w=1.8, op=0.95, wob=0.6))
        dirn = 1 if px <= 640 else -1
        b.append(g.line(px + dirn * 9, py - 7, px + dirn * 30, py - 17, th["accent"], w=1.2, op=0.9, wob=0.6))
        b.append(text(round(px + dirn * 34, 1), round(py - 17, 1), f"peak · {max(vals)}", th, "accent", 13,
                      "i", "start" if dirn > 0 else "end"))
    b.append(text(RIGHT, 252, (f"{total:,} contributions since {weeks[0][0].strftime('%b %Y')}" if trimmed
                              else f"{total:,} contributions in the last year"), th, "ink3", 13, "i", "end"))
    return "".join(b), 266


def community_body(th, c):
    """c: dict from fetch_community(), or None -> placeholders."""
    dash = "—"
    c = c or {}
    pr_m, pr_t = c.get("merged"), c.get("prs")
    items = [
        (str(c["commits"]) if "commits" in c else dash, c.get("commits"), "commits · 提交"),
        (f"{pr_m} / {pr_t}" if pr_m is not None else dash, pr_t, "pull requests · 合并"),
        (str(c["issues"]) if "issues" in c else dash, c.get("issues"), "issues · 议题"),
        (str(c["reviews"]) if "reviews" in c else dash, c.get("reviews"), "reviews · 评审"),
        (str(c["discussions"]) if "discussions" in c else dash, c.get("discussions"), "discussions · 讨论"),
        (str(c["answers"]) if "answers" in c else dash, c.get("answers"), "answers · 采纳"),
    ]
    g = Rough(seed_of(c) if c else 11)
    nums = [v for _, v, _ in items if v]
    top = max(nums) if nums else None
    col = (RIGHT - LEFT) / 6
    b = [caption(th, "Community · 社区")]
    for i, (label, val, cap) in enumerate(items):
        x = LEFT + i * col
        hot = top is not None and val == top and all(items[j][1] != top for j in range(i))
        b.append(text(round(x, 1), 98, label, th, "ink", 30, "s"))
        ln = 34 + g.n(3)
        b.append(g.line(x + 1, 112, x + 1 + ln, 112 + g.n(1.5), th["accent"] if hot else th["ink4"],
                        w=1.6 if hot else 1.2, op=0.95 if hot else 0.7, wob=0.8, taper=0.4))
        b.append(text(round(x, 1), 134, cap, th, "ink3", 12, "i"))
    repos = c.get("repos")
    if repos is None:
        line = "Contributed to  " + dash
    elif repos:
        line = "Contributed to  " + " · ".join(repos)
    else:
        line = "Contributed to " + dash + " nothing outside my own repositories yet"
    b.append(text(LEFT, 176, line, th, "ink3", 14, "i"))
    cm, dc = c.get("comments"), c.get("discussion_comments")
    b.append(text(LEFT, 200, f"comments {dash if cm is None else cm} · discussion replies "
                             f"{dash if dc is None else dc}", th, "ink4", 13, "i"))
    return "".join(b), 220


def stars_body(th, series, now):
    """series: list of (name, created:datetime, [starred_at:datetime...]) sorted by stars desc."""
    g = Rough(seed_of([[n, str(c), [str(s) for s in ss]] for n, c, ss in series]))
    b = [caption(th, "Stars · 星迹")]
    if not series:
        b.append(text(LEFT, 90, "No stars yet — 暂无星迹。", th, "ink3", 15, "i"))
        return "".join(b), 130
    series = series[:6]
    t0 = min(c for _, c, _ in series)
    end = dt.datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=dt.timezone.utc)
    px0, px1, top, base = LEFT + 6, 690, 80, 192
    cmax = max(len(s) for _, _, s in series)
    span = (end - t0).total_seconds() or 1

    def X(t):
        return px0 + (px1 - px0) * (t - t0).total_seconds() / span

    def Y(c):
        return base - c / cmax * (base - top)

    b.append(g.line(LEFT - 4, base + 3, px1 + 6, base + 3, th["ink4"], w=1.1, op=0.9, wob=1.0))
    shades = [th["accent"], th["ink"], th["ink2"], th["ink3"], th["ink4"], th["ink3"]]
    labels = []
    for k, (name, created, ss) in enumerate(series):
        col = shades[k]
        pts = [(X(created), Y(0))]
        cnt = 0
        for t in ss:
            pts.append((X(t), Y(cnt)))
            cnt += 1
            pts.append((X(t), Y(cnt)))
        pts.append((px1, Y(cnt)))
        b.append(g.stroke(pts, col, w=1.8 if k == 0 else 1.5, op=0.95 if k == 0 else 0.85, wob=0.9))
        stride = max(1, len(ss) // 100)
        for j in range(0, len(ss), stride):
            b.append(f'<circle cx="{X(ss[j]) + g.n(0.4):.1f}" cy="{Y(j + 1) + g.n(0.4):.1f}" r="2.4" '
                     f'fill="{col}" opacity="0.95"/>')
        nm = name if len(name) <= 18 else name[:17] + "…"
        labels.append([Y(cnt), f"{nm} · {cnt}", col])
    labels.sort(key=lambda a: a[0])
    for i in range(1, len(labels)):
        if labels[i][0] - labels[i - 1][0] < 17:
            labels[i][0] = labels[i - 1][0] + 17
    for y, s, col in labels:
        b.append(text(px1 + 12, round(y + 4, 1), s, th, col, 14, "i"))
    # time labels
    ticks = [(px0, t0, "start"), (px1, end - dt.timedelta(days=1), "end")]
    for yr in range(t0.year + 1, end.year + 1):
        t = dt.datetime(yr, 1, 1, tzinfo=dt.timezone.utc)
        if t < end and 70 < X(t) - px0 and px1 - X(t) > 70:
            ticks.append((X(t), t, "middle"))
    for x, t, anchor in ticks:
        lab = t.strftime("%b %Y") if anchor != "middle" else str(t.year)
        b.append(text(round(x, 1), base + 25, lab, th, "ink3", 12, "i", anchor))
    return "".join(b), 236


STATS_LABEL = ("Most used languages, a year of weekly contributions, community contributions, "
               "and cumulative stars per repository; © 2026 Drag0nM")
GAP = 22  # extra vertical space between stacked sections


def stats(th, langs, weeks, total, comm, series, now):
    """One continuous paper with all display-only sections stacked."""
    parts = [languages_body(th, langs), commits_body(th, weeks, total), community_body(th, comm),
             stars_body(th, series, now), footer_body(th)]
    g = Rough(5)
    out, y = [], 0
    for i, (body, h) in enumerate(parts):
        if i:
            y += GAP
            if i < len(parts) - 1:  # the footer draws its own short rule
                out.append(g.line(LEFT + 2, y - GAP // 2, LEFT + 50, y - GAP // 2, th["ink4"],
                                  w=1.2, op=0.8, wob=0.8))
        out.append(f'<g transform="translate(0,{y})">{body}</g>')
        y += h
    return document(W, y, th, STATS_LABEL, "".join(out))


# ---------------------------------------------------------------- data
def _gql_once(query, variables, token):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "profile-generator"})
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.load(r)
    if d.get("errors"):
        raise RuntimeError(json.dumps(d["errors"])[:500])
    return d["data"]


def gql_raise(query, variables, token):
    """Like gql() but raises on failure (HTTPError / RuntimeError) instead of SystemExit."""
    last = None
    for attempt in range(3):
        try:
            return _gql_once(query, variables, token)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                raise
            last = e
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(2 * (attempt + 1))
    raise last


def gql(query, variables, token):
    try:
        return gql_raise(query, variables, token)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"GraphQL failed: {e}")


Q_MAIN = """query($login:String!){ user(login:$login){ createdAt
 contributionsCollection{ contributionCalendar{ totalContributions weeks{ contributionDays{ date contributionCount }}}}
 repositories(ownerAffiliations: OWNER, isFork: false, first: 100){ nodes{ name isPrivate isArchived createdAt pushedAt stargazerCount description url
  languages(first: 10, orderBy:{field: SIZE, direction: DESC}){ edges{ size node{ name }}}}}}}"""

def parse(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


Q_STARS = """query($o:String!,$n:String!,$after:String){ repository(owner:$o,name:$n){
 stargazers(first:100, after:$after, orderBy:{field:STARRED_AT, direction:ASC}){
  pageInfo{ hasNextPage endCursor } edges{ starredAt }}}}"""


def _fail(method, name, e):
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        msg = f"HTTP {e.code} {body[:300]}"
    else:
        msg = str(e)[:300]
    print(f"stargazers[{name}] {method}: {msg}", file=sys.stderr)


def _stars_graphql(login, name, token):
    times, after = [], None
    while True:
        d = gql_raise(Q_STARS, {"o": login, "n": name, "after": after}, token)
        sg = d["repository"]["stargazers"]
        times += [parse(e["starredAt"]) for e in sg["edges"]]
        if not sg["pageInfo"]["hasNextPage"]:
            return times
        after = sg["pageInfo"]["endCursor"]


def _stars_rest(login, name, token):
    times, page = [], 1
    while True:
        url = f"https://api.github.com/repos/{login}/{name}/stargazers?per_page=100&page={page}"
        hdr = {"Accept": "application/vnd.github.star+json",
               "User-Agent": "profile-generator",
               "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            hdr["Authorization"] = f"Bearer {token}"
        items, last = None, None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=60) as r:
                    items = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404):
                    raise
                last = e
            except Exception as e:  # noqa: BLE001
                last = e
            time.sleep(2 * (attempt + 1))
        if items is None:
            raise last
        times += [parse(i["starred_at"]) for i in items]
        if len(items) < 100:
            return times
        page += 1


def fetch_star_times(login, name, token):
    methods = []
    if token:
        methods.append(("graphql", lambda: _stars_graphql(login, name, token)))
        methods.append(("rest+token", lambda: _stars_rest(login, name, token)))
    methods.append(("rest-anon", lambda: _stars_rest(login, name, None)))
    for method, fn in methods:
        try:
            times = fn()
            print(f"stargazers[{name}]: fetched {len(times)} via {method}", file=sys.stderr)
            return times
        except Exception as e:  # noqa: BLE001
            _fail(method, name, e)
    print(f"warning: could not fetch stargazers for {name}", file=sys.stderr)
    return None


Q_COMMUNITY = """query($login:String!){ user(login:$login){
 contributionsCollection{ totalCommitContributions totalIssueContributions totalPullRequestContributions
  totalPullRequestReviewContributions }
 pullRequests{ totalCount } merged: pullRequests(states: MERGED){ totalCount }
 issues{ totalCount } issueComments{ totalCount }
 repositoryDiscussions{ totalCount } repositoryDiscussionComments{ totalCount }
 answers: repositoryDiscussionComments(onlyAnswers: true){ totalCount }
 repositoriesContributedTo(first: 6, contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, PULL_REQUEST_REVIEW],
  includeUserRepositories: false){ totalCount nodes{ nameWithOwner isPrivate }}}}"""


def fetch_community(login, token):
    """Returns a dict, or None on any failure (warning printed; never the token)."""
    try:
        u = gql_raise(Q_COMMUNITY, {"login": login}, token)["user"]
        cc = u["contributionsCollection"]
        return {
            "commits": cc["totalCommitContributions"],
            "prs": u["pullRequests"]["totalCount"], "merged": u["merged"]["totalCount"],
            "issues": u["issues"]["totalCount"],
            "reviews": cc["totalPullRequestReviewContributions"],
            "discussions": u["repositoryDiscussions"]["totalCount"],
            "answers": u["answers"]["totalCount"],
            "comments": u["issueComments"]["totalCount"],
            "discussion_comments": u["repositoryDiscussionComments"]["totalCount"],
            "repos": [n["nameWithOwner"] for n in u["repositoriesContributedTo"]["nodes"]
                      if not n["isPrivate"]][:4],
        }
    except Exception as e:  # noqa: BLE001
        print(f"warning: community query failed ({type(e).__name__}: {str(e)[:200]})", file=sys.stderr)
        return None


def write_if_changed(path, content):
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8", newline="\n")
    return True


DATA = ASSETS / "data"


def write_json(name, obj):
    DATA.mkdir(exist_ok=True)
    write_if_changed(DATA / name, json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n")


def read_json(name):
    try:
        return json.loads((DATA / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required")
    login = os.environ.get("PROFILE_USER", "DRAG0NM")
    private = os.environ.get("INCLUDE_PRIVATE") == "1"

    user = gql(Q_MAIN, {"login": login}, token)["user"]
    repos = [r for r in user["repositories"]["nodes"] if private or not r["isPrivate"]]

    lang = {}
    for r in repos:
        for e in r["languages"]["edges"]:
            lang[e["node"]["name"]] = lang.get(e["node"]["name"], 0) + e["size"]
    langs = sorted(lang.items(), key=lambda kv: (-kv[1], kv[0]))

    WORKS[:] = recent_works(user["repositories"]["nodes"], login)

    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks = []
    for w in cal["weeks"]:
        days = w["contributionDays"]
        if days:
            weeks.append((dt.date.fromisoformat(days[0]["date"]), sum(d["contributionCount"] for d in days)))
    weeks = weeks[-52:]

    series, stars_failed = [], False
    for r in repos:
        if r["isPrivate"] or r["stargazerCount"] < 1:
            continue
        times = fetch_star_times(login, r["name"], token)
        if not times:
            stars_failed = True
        if times:
            series.append((r["name"], parse(r["createdAt"]), sorted(times)))
    series.sort(key=lambda s: (-len(s[2]), s[0]))

    if stars_failed:
        cached = read_json("stars.json")
        if cached is not None:
            print("warning: star fetch failed; using assets/data/stars.json", file=sys.stderr)
            series = [(c["name"], parse(c["createdAt"]), [parse(t) for t in c["starredAt"]]) for c in cached]
    else:
        write_json("stars.json", [{"name": n, "createdAt": c.isoformat(),
                                   "starredAt": [t.isoformat() for t in ss]} for n, c, ss in series])

    comm = fetch_community(login, token)
    if comm is None:
        comm = read_json("community.json")
        if comm is not None:
            print("warning: community query failed; using assets/data/community.json", file=sys.stderr)
    else:
        write_json("community.json", comm)
    now = dt.datetime.now(dt.timezone.utc)
    global FONT_REG, FONT_ITA
    FONT_REG, FONT_ITA = b64("STIX-VF.woff2"), b64("STIX-Italic-VF.woff2")

    changed = []
    for mode, th in THEMES.items():
        out = {
            "header": header(th, 1),
            **{f"work-{i + 1}": work(th, i) for i in range(len(WORKS))},
            "stats": stats(th, langs, weeks, cal["totalContributions"], comm, series, now),
        }
        for name, svg in out.items():
            if write_if_changed(ASSETS / f"{name}-{mode}.svg", svg):
                changed.append(f"{name}-{mode}.svg")
    if write_readme_works(WORKS):
        changed.append("README.md")
    print("updated:", ", ".join(changed) if changed else "nothing changed")


if __name__ == "__main__":
    main()
