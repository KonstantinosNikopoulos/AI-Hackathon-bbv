"""Draws the radar as a self-contained SVG (no extra library). Hover a blip to see its name and reason."""
import hashlib
import html
import math
import random

from config import QUADRANTS, RINGS

# Validated categorical palette (light / dark). Aqua is below 3:1 on light, so blips always carry a number
# and every blip is also listed in the legend and the table.
QUADRANT_STYLE = {
    "Techniques": {"light": "#2a78d6", "dark": "#3987e5", "ink": "#ffffff"},
    "Tools": {"light": "#eb6834", "dark": "#d95926", "ink": "#ffffff"},
    "Platforms": {"light": "#1baf7a", "dark": "#199e70", "ink": "#0b0b0b"},
    "Languages & Frameworks": {"light": "#4a3aa7", "dark": "#9085e9", "ink": "#ffffff"},
}
# Math angles (degrees, counter-clockwise from 3 o'clock) for each quadrant.
QUADRANT_ANGLES = {"Tools": (0, 90), "Techniques": (90, 180), "Platforms": (180, 270),
                   "Languages & Frameworks": (270, 360)}
RING_RADII = [0.0, 0.34, 0.58, 0.80, 1.0]   # band edges as a share of the radius: Adopt | Trial | Assess | Hold


def number_blips(technologies):
    """Stable numbering: by quadrant, then ring, then name."""
    order = sorted(
        technologies,
        key=lambda t: (QUADRANTS.index(t["quadrant"]) if t["quadrant"] in QUADRANTS else 9,
                       RINGS.index(t["ring"]) if t["ring"] in RINGS else 9, t["name"].lower()),
    )
    return [dict(t, number=i) for i, t in enumerate(order, start=1)]


def _place(numbered, cx, cy, radius, blip_r):
    placed = []
    for t in numbered:
        a0, a1 = QUADRANT_ANGLES.get(t["quadrant"], (0, 90))
        ring = RINGS.index(t["ring"]) if t["ring"] in RINGS else 2
        r0, r1 = RING_RADII[ring] * radius, RING_RADII[ring + 1] * radius
        rng = random.Random(int(hashlib.md5(t["name"].encode()).hexdigest()[:8], 16))
        best, best_gap = None, -1
        for _ in range(80):
            pad_r = blip_r + 3
            r = rng.uniform(max(r0 + pad_r, pad_r * 1.6), r1 - pad_r)
            # keep blips away from the axes: angular padding grows near the centre
            pad_a = min(math.degrees((blip_r + 26) / max(r, 1)), 38)
            ang = math.radians(rng.uniform(a0 + pad_a, a1 - pad_a))
            x, y = cx + r * math.cos(ang), cy - r * math.sin(ang)
            gap = min([math.hypot(x - px, y - py) for px, py, _ in placed] or [999])
            if gap > best_gap:
                best, best_gap = (x, y), gap
            if gap >= 2 * blip_r + 6:
                break
        placed.append((best[0], best[1], t))
    return placed


def radar_html(technologies, statuses=None, size=720):
    statuses = statuses or {}
    numbered = number_blips(technologies)
    cx = cy = size / 2
    radius = size / 2 - 34
    blip_r = 11
    esc = html.escape

    parts = [f'<svg viewBox="0 0 {size} {size}" role="img" aria-label="Technology radar with {len(numbered)} technologies" '
             f'xmlns="http://www.w3.org/2000/svg">']
    # ring bands, outermost first
    for i in range(len(RINGS), 0, -1):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{RING_RADII[i] * radius:.1f}" class="band b{i % 2}"/>')
    parts.append(f'<line x1="{cx - radius}" y1="{cy}" x2="{cx + radius}" y2="{cy}" class="axis"/>')
    parts.append(f'<line x1="{cx}" y1="{cy - radius}" x2="{cx}" y2="{cy + radius}" class="axis"/>')
    # ring names along the vertical axis (top and bottom)
    for i, ring in enumerate(RINGS):
        mid = (RING_RADII[i] + RING_RADII[i + 1]) / 2 * radius
        for y in (cy - mid, cy + mid):
            parts.append(f'<text x="{cx}" y="{y + 4:.1f}" class="ring-label">{ring.upper()}</text>')
    # quadrant names in the corners
    corners = {"Techniques": (14, 26, "start"), "Tools": (size - 14, 26, "end"),
               "Platforms": (14, size - 14, "start"), "Languages & Frameworks": (size - 14, size - 14, "end")}
    for q, (x, y, anchor) in corners.items():
        sw = x if anchor == "start" else x - 12
        tx = x + 18 if anchor == "start" else x - 18
        parts.append(f'<rect x="{sw}" y="{y - 11}" width="12" height="12" rx="3" class="q-{QUADRANTS.index(q)}"/>'
                     f'<text x="{tx}" y="{y}" text-anchor="{anchor}" class="q-label">{esc(q)}</text>')

    for x, y, t in _place(numbered, cx, cy, radius, blip_r):
        q = QUADRANTS.index(t["quadrant"]) if t["quadrant"] in QUADRANTS else 0
        status = statuses.get(t["name"], "")
        tip = f"{t['number']}. {t['name']} | {t['ring']} · {t['quadrant']}"
        if status:
            tip += f" · {status}"
        if t.get("reason"):
            tip += f"\n{t['reason']}"
        parts.append(f'<g class="blip"><title>{esc(tip)}</title>')
        if status == "New":
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{blip_r + 4}" class="new-ring q-stroke-{q}"/>')
        shape = (f'<rect x="{x - blip_r:.1f}" y="{y - blip_r:.1f}" width="{2 * blip_r}" height="{2 * blip_r}" rx="4" '
                 f'class="dot q-{q}"/>') if t.get("is_seed") else \
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{blip_r}" class="dot q-{q}"/>'
        parts.append(shape)
        parts.append(f'<text x="{x:.1f}" y="{y + 4:.1f}" class="num ink-{q}">{t["number"]}</text></g>')
    parts.append("</svg>")

    light = "".join(f".q-{i}{{fill:{QUADRANT_STYLE[q]['light']}}}.q-stroke-{i}{{stroke:{QUADRANT_STYLE[q]['light']}}}"
                    f".ink-{i}{{fill:{QUADRANT_STYLE[q]['ink']}}}" for i, q in enumerate(QUADRANTS))
    dark = "".join(f".q-{i}{{fill:{QUADRANT_STYLE[q]['dark']}}}.q-stroke-{i}{{stroke:{QUADRANT_STYLE[q]['dark']}}}"
                   f".ink-{i}{{fill:#ffffff}}" for i, q in enumerate(QUADRANTS))
    style = f"""<style>
{light}
:root{{--surface:#fcfcfb;--band0:#f1f0ec;--band1:#fcfcfb;--grid:#d6d5d0;--text:#0b0b0b;--muted:#52514e}}
@media (prefers-color-scheme: dark){{:root{{--surface:#1a1a19;--band0:#242423;--band1:#1a1a19;--grid:#3d3d3a;--text:#ffffff;--muted:#c3c2b7}}
{dark}}}
html,body{{margin:0;background:var(--surface);font-family:"Source Sans Pro",system-ui,sans-serif}}
svg{{width:100%;height:auto;display:block}}
.band{{stroke:var(--grid);stroke-width:1}}.b0{{fill:var(--band0)}}.b1{{fill:var(--band1)}}
.axis{{stroke:var(--grid);stroke-width:1}}
.ring-label{{font-size:11px;font-weight:600;letter-spacing:.08em;fill:var(--muted);text-anchor:middle;paint-order:stroke;stroke:var(--surface);stroke-width:4px}}
.q-label{{font-size:15px;font-weight:600;fill:var(--text)}}
.dot{{stroke:var(--surface);stroke-width:2}}
.new-ring{{fill:none;stroke-width:2;stroke-dasharray:3 3}}
.num{{font-size:11px;font-weight:700;text-anchor:middle;pointer-events:none}}
.blip{{cursor:default}}.blip:hover .dot{{stroke:var(--text);stroke-width:2.5}}
</style>"""
    return style + "".join(parts), numbered
