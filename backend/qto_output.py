"""Render a human-style QTO OUTPUT for a Stage-2 page: the colored plan next to a
legend of every takeoff item — area (sq ft), linear (ft) and counts/plants (each)
— so the result looks like the human takeoff sheet, not just a side panel.

    from backend import qto_output
    png = qto_output.render_qto(job_id, page)   # path to the composed PNG
"""
from __future__ import annotations

import colorsys

from PIL import Image, ImageDraw, ImageFont

from . import store


def _font(size: int, bold: bool = False):
    names = (["arialbd.ttf", "Arialbd.ttf"] if bold else ["arial.ttf", "Arial.ttf"]) + \
            ["DejaVuSans.ttf"]
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _hex_rgb(h: str | None) -> tuple:
    h = (h or "#8a8a8a").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (138, 138, 138)


def _shade(i: int) -> tuple:
    """A distinct color per count row (so plant species are visually separable)."""
    r, g, b = colorsys.hsv_to_rgb((i * 0.137) % 1.0, 0.55, 0.85)
    return (int(r * 255), int(g * 255), int(b * 255))


def render_qto(job_id: str, page: int, out_path=None):
    """Compose the plan overlay + a takeoff legend into one QTO-style image."""
    s = store.read_stage2(job_id, page) or {}
    overlay = store.stage2_dir(job_id) / (s.get("overlay") or "")
    plan = (Image.open(overlay).convert("RGB") if overlay.exists()
            else Image.new("RGB", (1200, 900), "white"))

    groups = s.get("groups") or []
    takeoff = s.get("takeoff") or []

    # ---- build legend sections -------------------------------------------------
    sections: list[tuple[str, list]] = []
    # AREAS — prefer the colored groups (have hex + sqft)
    areas = [(_hex_rgb(g.get("hex")), g.get("label", ""), f"{(g.get('sqft') or 0):,.1f} sq ft")
             for g in groups if g.get("sqft")]
    if not areas:
        areas = [(_hex_rgb(None), t.get("code") or t.get("name", ""),
                  f"{t['quantity']:,.1f} sq ft")
                 for t in takeoff if t.get("unit") == "area" and t.get("quantity")]
    if areas:
        sections.append(("SURFACE AREAS (sq ft)", areas))
    # LINEAR
    lin = [(_hex_rgb("#1a73e8"), (t.get("code") or t.get("name", "")),
            f"{t['quantity']:,.1f} ft")
           for t in takeoff if t.get("unit") == "linear" and t.get("quantity")]
    if lin:
        sections.append(("WALLS / LINEAR (ft)", lin))
    # PLANTS / COUNTS
    plants = [t for t in takeoff if t.get("source") == "planting" and t.get("quantity")]
    counts = [t for t in takeoff
              if t.get("unit") == "count" and t.get("quantity") and t.get("source") != "planting"]
    if plants:
        rows = [(_shade(i), (t.get("code") or ""), f"{t['quantity']:,} ea  {t.get('name','')[:24]}")
                for i, t in enumerate(plants)]
        sections.append((f"PLANTS — per species ({sum(t['quantity'] for t in plants):,} total)", rows))
    if counts:
        rows = [(_shade(i + 50), (t.get("code") or t.get("name", "")), f"{t['quantity']:,} ea")
                for i, t in enumerate(counts)]
        sections.append(("COUNTS (each)", rows))

    # ---- draw the legend panel -------------------------------------------------
    LW, pad, rh = 460, 22, 26
    title_f, head_f, row_f = _font(26, True), _font(15, True), _font(14)
    n_rows = sum(len(rws) for _, rws in sections)
    legend_h = pad + 44 + sum(34 + len(rws) * rh + 10 for _, rws in sections) + pad
    H = max(plan.height, legend_h)
    # scale the plan to the canvas height (keep aspect)
    scale = H / plan.height
    plan_w = int(plan.width * scale)
    plan = plan.resize((plan_w, H))

    canvas = Image.new("RGB", (plan_w + LW, H), "white")
    canvas.paste(plan, (0, 0))
    d = ImageDraw.Draw(canvas)
    x0 = plan_w
    d.rectangle([x0, 0, x0 + LW, H], fill=(250, 250, 251))
    d.line([x0, 0, x0, H], fill=(220, 220, 224), width=1)
    y = pad
    d.text((x0 + pad, y), "QTO — AI Takeoff", font=title_f, fill=(20, 20, 24)); y += 40
    d.text((x0 + pad, y), s.get("message", "")[:54], font=row_f, fill=(120, 120, 128)); y += 22
    for head, rows in sections:
        d.text((x0 + pad, y), head, font=head_f, fill=(40, 40, 48)); y += 26
        d.line([x0 + pad, y, x0 + LW - pad, y], fill=(225, 225, 230), width=1); y += 8
        for rgb, label, qty in rows:
            d.rectangle([x0 + pad, y + 4, x0 + pad + 14, y + 18], fill=rgb,
                        outline=(150, 150, 150))
            d.text((x0 + pad + 22, y + 2), str(label)[:8], font=_font(13, True), fill=(30, 30, 36))
            d.text((x0 + pad + 80, y + 2), str(qty), font=row_f, fill=(26, 115, 232))
            y += rh
        y += 10

    out_path = str(out_path) if out_path else str(store.stage2_dir(job_id) / f"qto_p{page}.png")
    canvas.save(out_path)
    return out_path
