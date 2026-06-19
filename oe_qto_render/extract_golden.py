"""One-off: parse the reference sheet AQ0.0 (page 3 of `2811 KIRBY QTO.pdf`) into
a golden plan-data JSON. This gives a genuine data instance + a visual-diff
target. Run:

    python -m oe_qto_render.extract_golden <qto_pdf> <out_json> [page_index]

Geometry is grouped by the locked element colors. The two magenta-stroke
elements (planter, drain line) share `#FF00DB`; they are split spatially by a
length-fraction heuristic. Totals are the authoritative legend values.
"""
from __future__ import annotations

import json
import math
import sys

import fitz

from . import style

# Authoritative legend totals read from AQ0.0 (the "measured" data for the golden).
TOTALS = {
    "coping": 199.92, "total_sf": 1109.23, "tanning_ledge": 290.47,
    "waterline": 180.17, "bench": 43.29, "steps": 48.39, "toe_tile": 94.64,
    "planter": 27.71, "stone_steppers": 41.09, "lights": 12, "drain_line": "46.6",
    "light_run": 658.52, "d_markers": 8, "skimmers": 3,
}
# stroke color -> element key (linear). #FF00DB handled separately (planter+drain).
STROKE_KEY = {
    "#FF9800": "coping", "#F0FF00": "waterline", "#00ECFF": "bench",
    "#CDDC39": "steps", "#3F51B5": "toe_tile", "#E91E63": "light_run",
}
FILL_KEY = {"#FF00DB": "total_sf", "#009688": "tanning_ledge", "#FFEB3B": "stone_steppers"}
POINT_FILL_KEY = {"#000000": "lights", "#03A9F4": "d_markers", "#00FFA0": "skimmers"}


def _hex(c):
    return "#%02X%02X%02X" % tuple(round(x * 255) for x in c) if c else None


def _polylines(items):
    """Drawing items -> list of polylines (each a list of [x,y])."""
    runs, cur = [], []
    for it in items:
        if it[0] == "l":
            if not cur:
                cur = [[it[1].x, it[1].y]]
            cur.append([it[2].x, it[2].y])
        elif it[0] == "c":
            if not cur:
                cur = [[it[1].x, it[1].y]]
            cur.append([it[4].x, it[4].y])
        else:
            if len(cur) >= 2:
                runs.append(cur)
            cur = []
    if len(cur) >= 2:
        runs.append(cur)
    return runs


def _seg_len(seg):
    return sum(math.hypot(seg[i + 1][0] - seg[i][0], seg[i + 1][1] - seg[i][1])
               for i in range(len(seg) - 1))


def _rect_polygon(rect):
    return [[rect.x0, rect.y0], [rect.x1, rect.y0],
            [rect.x1, rect.y1], [rect.x0, rect.y1]]


def extract(pdf_path: str, page_index: int = 3) -> dict:
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    W, H = page.rect.width, page.rect.height
    rotation = page.rotation
    # exclude the legend region (its swatches share the element colors)
    lx0, ly0, lx1, ly1 = 0.585 * W, 0.355 * H, 0.685 * W, 0.62 * H

    def in_legend(r):
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        return lx0 < cx < lx1 and ly0 < cy < ly1

    # detect the original legend box (white rounded rect ~276x505) -> its top-left
    legend_origin = None
    for d in page.get_drawings():
        if _hex(d.get("fill")) == "#FFFFFF":
            r = d["rect"]
            if 268 < r.width < 286 and 490 < r.height < 520:
                legend_origin = [round(r.x0, 2), round(r.y0, 2)]
                break

    segments: dict[str, list] = {k: [] for k in style.ORDER}
    polygons: dict[str, list] = {k: [] for k in style.ORDER}
    points: dict[str, list] = {k: [] for k in style.ORDER}
    magenta_strokes: list = []
    leader_segs: list = []

    for d in page.get_drawings():
        r = d["rect"]
        if in_legend(r):
            continue
        stroke = _hex(d.get("color"))
        fill = _hex(d.get("fill"))
        items = d.get("items", [])
        # linear strokes
        if stroke and d.get("width"):
            if stroke == "#FF00DB":
                magenta_strokes.extend(_polylines(items))
            elif stroke in STROKE_KEY:
                key = STROKE_KEY[stroke]
                pls = _polylines(items)
                if key == "light_run":
                    leader_segs.extend(pls)
                else:
                    segments[key].extend(pls)
        # area fills
        if fill in FILL_KEY:
            for it in items:
                if it[0] == "re":
                    polygons[FILL_KEY[fill]].append(_rect_polygon(it[1]))
                elif it[0] in ("l", "c"):
                    pass
            pls = _polylines(items)
            for pl in pls:
                if len(pl) >= 3:
                    polygons[FILL_KEY[fill]].append(pl)
        # point dots
        if fill in POINT_FILL_KEY and r.width < 30:
            points[POINT_FILL_KEY[fill]].append([(r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2])

    # split magenta strokes -> planter (shorter) + drain_line by length fraction
    total_len = sum(_seg_len(s) for s in magenta_strokes)
    planter_frac = 27.71 / (27.71 + 46.6)
    magenta_strokes.sort(key=_seg_len)
    acc = 0.0
    for s in magenta_strokes:
        if acc < planter_frac * total_len:
            segments["planter"].append(s); acc += _seg_len(s)
        else:
            segments["drain_line"].append(s)

    # light-run origin = endpoint shared by the most leaders
    origin = _shared_origin(leader_segs)

    # dimension labels: green Verdana spans
    dim_labels = []
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            for sp in ln.get("spans", []):
                col = sp["color"]; rgb = ((col >> 16) & 255, (col >> 8) & 255, col & 255)
                if rgb[1] > 120 and rgb[0] < 160 and rgb[2] < 160 and "ft" in sp["text"]:
                    bb = sp["bbox"]
                    dim_labels.append({"text": sp["text"].strip(),
                                       "pos": [bb[0], bb[3]], "angle": 0.0})
    doc.close()

    elements: dict[str, dict] = {}
    for key in style.ORDER:
        el = style.BY_KEY[key]
        ed: dict = {"total": TOTALS[key]}
        if el.geometry.value == "linear" and key != "light_run":
            ed["segments"] = segments[key]
        elif el.geometry.value == "area":
            ed["polygons"] = polygons[key]
        elif el.geometry.value == "point":
            ed["points"] = points[key]
        if key == "light_run":
            ed["origin"] = origin
        elements[key] = ed

    return {
        "base": {"pdf": pdf_path, "page_index": page_index,
                 "width": W, "height": H, "rotation": rotation},
        "elements": elements,
        "dimension_labels": dim_labels,
        "legend_origin": legend_origin,
    }


def _shared_origin(leaders):
    ends = []
    for s in leaders:
        ends.append(tuple(s[0])); ends.append(tuple(s[-1]))
    best, best_n = None, -1
    for p in ends:
        n = sum(1 for q in ends if math.hypot(p[0] - q[0], p[1] - q[1]) < 25)
        if n > best_n:
            best, best_n = p, n
    return list(best) if best else [0.0, 0.0]


def main():
    pdf = sys.argv[1]
    out = sys.argv[2]
    page = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    data = extract(pdf, page)
    with open(out, "w") as f:
        json.dump(data, f, indent=2)
    counts = {k: (len(v.get("segments") or v.get("polygons") or v.get("points") or []))
              for k, v in data["elements"].items()}
    print("wrote", out)
    print("geometry counts:", counts)
    print("light_run origin:", data["elements"]["light_run"]["origin"])
    print("dim labels:", len(data["dimension_labels"]))


if __name__ == "__main__":
    main()
