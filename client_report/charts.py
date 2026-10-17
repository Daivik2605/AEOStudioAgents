"""Charts drawn as inline SVG by Python. No scripts, no external files, no colour needed: bars differ by darkness and
hatching, every bar carries its number, and a range is a whisker with white edges so it shows on dark and light bars alike.
"""

from __future__ import annotations

from html import escape

INK, MID, LIGHT, PAPER = "#1b1b1b", "#6d6d6d", "#cfcfcf", "#ffffff"


def _short(text: str, n: int) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _defs(prefix: str) -> str:
    return (f'<defs><pattern id="{prefix}-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<rect width="6" height="6" fill="{LIGHT}"/><line x1="0" y1="0" x2="0" y2="6" stroke="{MID}" stroke-width="2"/></pattern></defs>')


def share_of_voice_svg(rows: list[dict], *, alt: str, prefix: str = "sov") -> str:
    """rows: [{name, answers, is_client}], already in rank order. The client's bar is dark; the others are light."""
    if not rows:
        return ""
    width, label_w, bar_max, row_h = 640, 210, 330, 26
    top = max(r["answers"] for r in rows) or 1
    height = row_h * len(rows) + 10
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(alt)}" width="100%">', f"<title>{escape(alt)}</title>"]
    for i, r in enumerate(rows):
        y = 5 + i * row_h
        length = max(1.5, bar_max * r["answers"] / top)
        fill = INK if r["is_client"] else LIGHT
        weight = "700" if r["is_client"] else "400"
        out.append(f'<text x="{label_w - 8}" y="{y + 15}" text-anchor="end" font-size="12" font-weight="{weight}" fill="{INK}">{escape(_short(r["name"], 30))}</text>')
        out.append(f'<rect x="{label_w}" y="{y + 3}" width="{length:.1f}" height="16" fill="{fill}" stroke="{INK}" stroke-width="1"/>')
        out.append(f'<text x="{label_w + length + 6:.1f}" y="{y + 15}" font-size="12" fill="{INK}">{r["answers"]}</text>')
    out.append("</svg>")
    return "".join(out)


def _x(value: float, left: float, span: float) -> float:
    return left + span * max(0.0, min(100.0, value)) / 100.0


def change_bars_svg(rows: list[dict], *, series_labels: list[str], alt: str, prefix: str = "chg") -> str:
    """rows: [{label, series: [{value, low, high} or None, ...]}] with one series entry per label (1 or 2).
    Values are percentages (0-100). Series 0 is hatched light grey, series 1 is solid dark."""
    rows = [r for r in rows if any(s and s["value"] is not None for s in r["series"])]
    if not rows:
        return ""
    width, left, span, bar_h, gap = 680, 20, 330, 15, 3
    n = len(series_labels)
    block = 22 + n * (bar_h + gap) + 10
    legend_h = 24
    height = legend_h + block * len(rows) + 28
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(alt)}" width="100%">', f"<title>{escape(alt)}</title>", _defs(prefix)]
    # legend
    for k, label in enumerate(series_labels):
        lx = left + k * 220
        fill = f"url(#{prefix}-hatch)" if k == 0 else INK
        out.append(f'<rect x="{lx}" y="4" width="14" height="12" fill="{fill}" stroke="{INK}"/><text x="{lx + 20}" y="14" font-size="12" fill="{INK}">{escape(label)}</text>')
    for i, r in enumerate(rows):
        y0 = legend_h + i * block
        out.append(f'<text x="{left}" y="{y0 + 14}" font-size="12" font-weight="600" fill="{INK}">{escape(_short(r["label"], 70))}</text>')
        for k, s in enumerate(r["series"]):
            y = y0 + 20 + k * (bar_h + gap)
            if not s or s["value"] is None:
                out.append(f'<text x="{left}" y="{y + 12}" font-size="11" fill="{MID}">no figure</text>')
                continue
            fill = f"url(#{prefix}-hatch)" if k == 0 else INK
            x1 = _x(s["value"], left, span)
            out.append(f'<rect x="{left}" y="{y}" width="{max(1.0, x1 - left):.1f}" height="{bar_h}" fill="{fill}" stroke="{INK}" stroke-width="1"/>')
            text = f'{s["value"]:.1f}%'
            if s.get("low") is not None and s.get("high") is not None:
                lo, hi = _x(s["low"], left, span), _x(s["high"], left, span)
                cy = y + bar_h / 2
                for stroke, w in ((PAPER, 4.5), (INK, 1.6)):
                    out.append(f'<line x1="{lo:.1f}" y1="{cy}" x2="{hi:.1f}" y2="{cy}" stroke="{stroke}" stroke-width="{w}"/>'
                               f'<line x1="{lo:.1f}" y1="{cy - 5}" x2="{lo:.1f}" y2="{cy + 5}" stroke="{stroke}" stroke-width="{w}"/>'
                               f'<line x1="{hi:.1f}" y1="{cy - 5}" x2="{hi:.1f}" y2="{cy + 5}" stroke="{stroke}" stroke-width="{w}"/>')
                text += f' ({s["low"]:.1f}–{s["high"]:.1f})'
            out.append(f'<text x="{left + span + 14}" y="{y + 12}" font-size="12" fill="{INK}">{escape(text)}</text>')
    axis_y = legend_h + block * len(rows) + 6
    for tick in (0, 25, 50, 75, 100):
        x = _x(tick, left, span)
        out.append(f'<line x1="{x:.1f}" y1="{axis_y - 4}" x2="{x:.1f}" y2="{axis_y}" stroke="{MID}"/>'
                   f'<text x="{x:.1f}" y="{axis_y + 14}" text-anchor="middle" font-size="10" fill="{MID}">{tick}%</text>')
    out.append(f'<line x1="{left}" y1="{axis_y}" x2="{left + span}" y2="{axis_y}" stroke="{MID}"/>')
    out.append("</svg>")
    return "".join(out)
