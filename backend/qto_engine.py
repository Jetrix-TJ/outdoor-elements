"""QTO zone-detection engine — ported from the lead's `outdoor_qto.py`.

Key idea: separate ZONE BOUNDARIES from HATCH by vector line WIDTH (not pixel
brightness). Thick paths (>= 0.35pt) are zone walls; thin paths are hatch/texture.
Rasterize walls only -> connected components label each enclosed zone -> each
material callout tag claims its zone (Phase 1) -> leftover fragments assigned by
distance + hatch-coverage scoring (Phase 2). Pixel area x scale -> sq ft.

Faithful port; constants/thresholds unchanged from the source.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import cv2
import fitz  # PyMuPDF
import numpy as np
from PIL import Image

_CALLOUT_BUBBLE_MAX_PX = 2000   # zones smaller than this are callout bubbles


# ── geometry helpers ────────────────────────────────────────────────────────
def _bezier_pts(p1, p2, p3, p4, steps: int = 20) -> list[tuple[float, float]]:
    pts = []
    for i in range(steps + 1):
        t = i / steps
        t2 = t * t; t3 = t2 * t
        c = 1 - t; c2 = c * c; c3 = c2 * c
        x = c3 * p1.x + 3 * c2 * t * p2.x + 3 * c * t2 * p3.x + t3 * p4.x
        y = c3 * p1.y + 3 * c2 * t * p2.y + 3 * c * t2 * p3.y + t3 * p4.y
        pts.append((x, y))
    return pts


def _make_pt_transform(rotation: int, rect):
    W, H = rect.width, rect.height
    if rotation == 90:
        return lambda x, y: (W - y, x)
    if rotation == 180:
        return lambda x, y: (W - x, H - y)
    if rotation == 270:
        return lambda x, y: (y, H - x)
    return lambda x, y: (x, y)


def _is_parallel_hatch(items: list) -> bool:
    """True if this path is a parallel-line hatch group (wood grain, DG, grids),
    not a zone-boundary polygon."""
    lines = []
    for item in items:
        if item[0] == "l" and len(item) >= 3:
            p1, p2 = item[1], item[2]
            dx, dy = p2.x - p1.x, p2.y - p1.y
            L = math.hypot(dx, dy)
            if L > 0:
                lines.append((dx / L, dy / L))
    if len(lines) < 4:
        return False
    rdx, rdy = lines[0]
    parallel = sum(1 for dx, dy in lines if abs(dx * rdx + dy * rdy) > 0.985)
    return parallel >= len(lines) * 0.8


# ── rasterization by line width ─────────────────────────────────────────────
def render_thick_boundaries(pdf_path, page_idx, dpi, min_lw=0.35, min_extent=3.0):
    """Raster of zone-boundary lines only (width >= min_lw, bbox >= min_extent,
    excluding parallel hatch). White (255) bg, black (0) lines."""
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    r = page.rect
    sc = dpi / 72.0
    W, H = int(r.width * sc), int(r.height * sc)
    _t = _make_pt_transform(page.rotation, r)
    img = np.ones((H, W), dtype=np.uint8) * 255

    for d in page.get_drawings():
        lw = d.get("width") or 0.0
        if lw < min_lw:
            continue
        rect_d = d.get("rect")
        if rect_d is not None and max(rect_d.width, rect_d.height) < min_extent:
            continue
        if _is_parallel_hatch(d.get("items", [])):
            continue

        lw_px = max(1, int(round(lw * sc)))
        pts_run: list[tuple[int, int]] = []

        def flush():
            if len(pts_run) >= 2:
                arr = np.array(pts_run, dtype=np.int32).reshape((-1, 1, 2))
                cv2.polylines(img, [arr], False, 0, lw_px, cv2.LINE_AA)
            pts_run.clear()

        for item in d["items"]:
            kind = item[0]
            if kind == "l":
                x1, y1 = _t(item[1].x, item[1].y)
                x2, y2 = _t(item[2].x, item[2].y)
                pts_run.append((int(x1 * sc), int(y1 * sc)))
                pts_run.append((int(x2 * sc), int(y2 * sc)))
            elif kind == "c":
                flush()
                for (x, y) in _bezier_pts(item[1], item[2], item[3], item[4]):
                    tx, ty = _t(x, y)
                    pts_run.append((int(tx * sc), int(ty * sc)))
                flush()
            elif kind == "re":
                flush()
                rc = item[1]
                x0, y0 = _t(rc.x0, rc.y0)
                x1, y1 = _t(rc.x1, rc.y1)
                cv2.rectangle(img, (int(min(x0, x1) * sc), int(min(y0, y1) * sc)),
                              (int(max(x0, x1) * sc), int(max(y0, y1) * sc)), 0, lw_px)
            else:
                flush()
        flush()

    doc.close()
    return img


def render_hatch_lines(pdf_path, page_idx, dpi, max_lw=0.49):
    """Raster of thin hatch/fill paths only (0.10 <= width <= max_lw) for the
    Phase-2 hatch-coverage signature. White bg, black hatch."""
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    r = page.rect
    sc = dpi / 72.0
    W, H = int(r.width * sc), int(r.height * sc)
    _t = _make_pt_transform(page.rotation, r)
    img = np.ones((H, W), dtype=np.uint8) * 255

    for d in page.get_drawings():
        lw = d.get("width") or 0.0
        if lw > max_lw or lw < 0.10:
            continue
        lw_px = max(1, int(round(lw * sc)))
        pts_run: list[tuple[int, int]] = []

        def flush():
            if len(pts_run) >= 2:
                arr = np.array(pts_run, dtype=np.int32).reshape((-1, 1, 2))
                cv2.polylines(img, [arr], False, 0, lw_px, cv2.LINE_AA)
            pts_run.clear()

        for item in d["items"]:
            kind = item[0]
            if kind == "l":
                x1, y1 = _t(item[1].x, item[1].y)
                x2, y2 = _t(item[2].x, item[2].y)
                pts_run.append((int(x1 * sc), int(y1 * sc)))
                pts_run.append((int(x2 * sc), int(y2 * sc)))
            elif kind == "c":
                flush()
                for x, y in _bezier_pts(item[1], item[2], item[3], item[4]):
                    tx, ty = _t(x, y)
                    pts_run.append((int(tx * sc), int(ty * sc)))
                flush()
            elif kind == "re":
                flush()
                rc = item[1]
                x0, y0 = _t(rc.x0, rc.y0)
                x1, y1 = _t(rc.x1, rc.y1)
                cv2.rectangle(img, (int(min(x0, x1) * sc), int(min(y0, y1) * sc)),
                              (int(max(x0, x1) * sc), int(max(y0, y1) * sc)), 0, lw_px)
            else:
                flush()
        flush()

    doc.close()
    return img


def preprocess_for_fill(boundary_img: np.ndarray) -> np.ndarray:
    """Thick-boundary image -> binary barrier (255 = fillable interior, 0 = wall),
    closing small gaps so boundaries are continuous."""
    _, dark = cv2.threshold(boundary_img, 200, 255, cv2.THRESH_BINARY_INV)
    k3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dark = cv2.dilate(dark, k3, iterations=3)
    return cv2.bitwise_not(dark)


# ── scale + tags ────────────────────────────────────────────────────────────
def px_to_sqft(pixels: int, dpi: int, scale_in_per_ft: float) -> float:
    ft_per_px = (1.0 / dpi) / scale_in_per_ft
    return pixels * ft_per_px ** 2


def extract_tags(pdf_path, page_idx, clip_pct: dict, tag_re, numeric_only: bool) -> list[dict]:
    """Material callout tags from the text layer inside the plan clip.
    Returns list of {code, x, y} (PDF points)."""
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    r = page.rect
    plan_clip = fitz.Rect(
        r.width * clip_pct.get("left", 0.0),
        r.height * clip_pct.get("top", 0.05),
        r.width * clip_pct.get("right", 0.80),
        r.height * clip_pct.get("bottom", 0.92),
    )
    words = page.get_text("words", clip=plan_clip)
    doc.close()
    tags = []
    for w in words:
        x0, y0, x1, y1, word = w[0], w[1], w[2], w[3], w[4]
        m = tag_re.fullmatch(word.strip())
        if m:
            code = f"M.{int(m.group(1))}" if numeric_only else m.group(1)
            tags.append({"code": code, "x": (x0 + x1) / 2.0, "y": (y0 + y1) / 2.0})
    return tags


# ── palette ─────────────────────────────────────────────────────────────────
_PALETTE = [
    (230, 168, 23), (91, 155, 213), (112, 173, 71), (255, 87, 51),
    (155, 89, 182), (26, 188, 156), (231, 76, 60), (52, 152, 219),
    (243, 156, 18), (46, 204, 113), (211, 84, 0), (142, 68, 173),
    (22, 160, 133), (192, 57, 43), (41, 128, 185), (39, 174, 96),
]


def code_color_rgb(code: str) -> tuple[int, int, int]:
    m = re.search(r"\d+", code)
    num = (int(m.group()) - 1) if m else 0
    return _PALETTE[num % len(_PALETTE)]


# ── zone detection (Phase 1 claim + Phase 2 scoring) ────────────────────────
def detect_zones(binary, tags, pt_to_px, dpi, scale_in_per_ft=1 / 16,
                 hatch_dark=None, phase1_min_zone_sf=0, phase2_radius_ft=24,
                 phase1_max_zone_sf=0, phase2_force_expand_codes=None,
                 phase2_skip_codes=None):
    """Label all connected white regions once; each tag claims its component
    (Phase 1, spiral search for callout-bubble tags); leftover components are
    assigned by zone-expansion (simple sheets) or distance+hatch scoring
    (complex sheets). Returns (zone_masks{code->mask}, zone_px{code->px})."""
    h, w = binary.shape
    MAX_ZONE_PX = int(h * w * 0.20)

    n_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=4)

    p1_radius_in = 3.0 if phase1_min_zone_sf > 0 else 1.5
    search_radius = int(dpi * p1_radius_in)
    px_per_sf = (scale_in_per_ft * dpi) ** 2
    P1_MIN_PX = max(_CALLOUT_BUBBLE_MAX_PX, int(phase1_min_zone_sf * px_per_sf))
    # Per-zone Phase-1 cap: a tag skips any component bigger than this and keeps
    # spiralling to find the next smaller valid zone (fixes a small material's
    # tag claiming one giant merged component, e.g. M.15 river rock = 3326 SF).
    P1_MAX_PX = int(phase1_max_zone_sf * px_per_sf) if phase1_max_zone_sf > 0 else MAX_ZONE_PX

    zone_masks: dict[str, np.ndarray] = {}
    code_labels: dict[str, set] = {}
    claimed_lbl: dict[int, str] = {}
    p1_found_tags: set = set()   # (tx, ty, code) of tags that claimed a Phase-1 zone

    # Phase 1: per-tag spiral claim
    for tag in tags:
        code = tag["code"]
        tx = min(max(int(tag["x"] * pt_to_px), 0), w - 1)
        ty = min(max(int(tag["y"] * pt_to_px), 0), h - 1)
        candidates = [(tx, ty)]
        for rad_step in range(4, search_radius, 4):
            for angle_deg in range(0, 360, 10):
                rad = math.radians(angle_deg)
                cx = tx + int(rad_step * math.cos(rad))
                cy = ty + int(rad_step * math.sin(rad))
                if 0 <= cx < w and 0 <= cy < h:
                    candidates.append((cx, cy))

        seen_lbls: set[int] = set()
        best_lbl = None
        best_count = 0
        for cx, cy in candidates:
            lbl = int(labels[cy, cx])
            if lbl in seen_lbls:
                continue
            seen_lbls.add(lbl)
            count = int(stats[lbl, cv2.CC_STAT_AREA])
            if count <= P1_MIN_PX or count > P1_MAX_PX:
                continue
            owner = claimed_lbl.get(lbl)
            if owner is not None and owner != code:
                continue
            if lbl in code_labels.get(code, set()):
                continue
            best_lbl = lbl
            best_count = count
            break

        if best_lbl is not None:
            claimed_lbl[best_lbl] = code
            code_labels.setdefault(code, set()).add(best_lbl)
            p1_found_tags.add((tx, ty, code))
            zone_mask = (labels == best_lbl).astype(np.uint8) * 255
            zone_masks[code] = (np.maximum(zone_masks[code], zone_mask)
                                if code in zone_masks else zone_mask)

    # Phase 2: assign leftover fragments
    n_unique_codes = len({t["code"] for t in tags})
    p2_added = 0
    if n_unique_codes <= 1:
        EXPAND_PX, EXPAND_STEPS = 20, 3
        exp_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * EXPAND_PX + 1, 2 * EXPAND_PX + 1))
        for _ in range(EXPAND_STEPS):
            step_added = 0
            for code, mask in list(zone_masks.items()):
                expanded = cv2.dilate(mask, exp_kernel, iterations=1)
                for lbl in np.unique(labels[expanded > 0]):
                    lbl = int(lbl)
                    if lbl == 0 or lbl in claimed_lbl:
                        continue
                    count = int(stats[lbl, cv2.CC_STAT_AREA])
                    if count <= _CALLOUT_BUBBLE_MAX_PX or count > MAX_ZONE_PX:
                        continue
                    claimed_lbl[lbl] = code
                    code_labels.setdefault(code, set()).add(lbl)
                    zone_masks[code] = np.maximum(zone_masks[code],
                                                  (labels == lbl).astype(np.uint8) * 255)
                    step_added += 1; p2_added += 1
            if step_added == 0:
                break
    else:
        PHASE2_MAX_DIST = int(phase2_radius_ft * scale_in_per_ft * dpi)
        ALPHA, MAX_COV_DIFF = 0.5, 0.40
        P1_SKIP_SF = 3000
        P1_SKIP_PX = P1_SKIP_SF * (scale_in_per_ft * dpi) ** 2
        p1_code_px = {code: int(np.sum(mask > 0)) for code, mask in zone_masks.items()}
        skip_p2_codes = {code for code, px in p1_code_px.items() if px > P1_SKIP_PX}
        if phase2_skip_codes:
            skip_p2_codes |= set(phase2_skip_codes)

        # Pre-expand pass: codes fragmented into thin strips by their own fill
        # pattern (e.g. wood grain renders like a boundary) bridge those narrow
        # barriers and reclaim their adjacent strips BEFORE the distance-scoring
        # loop assigns them to a nearby wrong code.
        if phase2_force_expand_codes:
            PRE_EXPAND_PX, PRE_EXPAND_STEPS = 8, 30
            pre_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * PRE_EXPAND_PX + 1,) * 2)
            for fcode in phase2_force_expand_codes:
                if fcode not in zone_masks:
                    continue
                for _ in range(PRE_EXPAND_STEPS):
                    step_n = 0
                    expanded = cv2.dilate(zone_masks[fcode], pre_k, iterations=1)
                    for lbl_e in np.unique(labels[expanded > 0]):
                        lbl_e = int(lbl_e)
                        if lbl_e == 0 or lbl_e in claimed_lbl:
                            continue
                        count_e = int(stats[lbl_e, cv2.CC_STAT_AREA])
                        if count_e <= _CALLOUT_BUBBLE_MAX_PX or count_e > MAX_ZONE_PX:
                            continue
                        claimed_lbl[lbl_e] = fcode
                        code_labels.setdefault(fcode, set()).add(lbl_e)
                        zone_masks[fcode] = np.maximum(zone_masks[fcode],
                                                       (labels == lbl_e).astype(np.uint8) * 255)
                        step_n += 1
                        p2_added += 1
                    if step_n == 0:
                        break

        code_cov: dict[str, float] = {}
        if hatch_dark is not None:
            for code, mask in zone_masks.items():
                comp_px = int(np.sum(mask > 0))
                if comp_px == 0:
                    code_cov[code] = 0.0
                    continue
                hatch_px = int(np.sum((hatch_dark > 0) & (mask > 0)))
                code_cov[code] = hatch_px / comp_px

        # Only tags that actually claimed a Phase-1 zone act as Phase-2 anchors.
        # A tag that failed Phase-1 (e.g. a leader endpoint pointing into a wrong
        # zone) would otherwise pull unclaimed fragments toward an incorrect code.
        tag_px = [(min(max(int(t["x"] * pt_to_px), 0), w - 1),
                   min(max(int(t["y"] * pt_to_px), 0), h - 1), t["code"]) for t in tags
                  if (min(max(int(t["x"] * pt_to_px), 0), w - 1),
                      min(max(int(t["y"] * pt_to_px), 0), h - 1), t["code"]) in p1_found_tags]

        for lbl in range(1, n_labels):
            if lbl in claimed_lbl:
                continue
            count = int(stats[lbl, cv2.CC_STAT_AREA])
            if count <= _CALLOUT_BUBBLE_MAX_PX or count > MAX_ZONE_PX:
                continue
            ccx, ccy = float(centroids[lbl][0]), float(centroids[lbl][1])

            if hatch_dark is not None:
                bx0 = int(stats[lbl, cv2.CC_STAT_LEFT]); by0 = int(stats[lbl, cv2.CC_STAT_TOP])
                bx1 = bx0 + int(stats[lbl, cv2.CC_STAT_WIDTH]); by1 = by0 + int(stats[lbl, cv2.CC_STAT_HEIGHT])
                lbl_sl = (labels[by0:by1, bx0:bx1] == lbl)
                hatch_sl = (hatch_dark[by0:by1, bx0:bx1] > 0)
                bb_px = int(lbl_sl.sum())
                comp_cov = int((lbl_sl & hatch_sl).sum()) / max(bb_px, 1)
            else:
                comp_cov = 0.0

            best_score, best_code = -1.0, None
            seen_codes: set[str] = set()
            for tx, ty, code in tag_px:
                if code in skip_p2_codes or code in seen_codes:
                    continue
                d = math.hypot(tx - ccx, ty - ccy)
                if d > PHASE2_MAX_DIST:
                    continue
                seen_codes.add(code)
                dist_score = 1.0 - d / PHASE2_MAX_DIST
                if hatch_dark is not None and code in code_cov:
                    cov_score = max(0.0, 1.0 - abs(comp_cov - code_cov[code]) / MAX_COV_DIFF)
                else:
                    cov_score = 0.5
                score = ALPHA * dist_score + (1.0 - ALPHA) * cov_score
                if score > best_score:
                    best_score, best_code = score, code

            if best_code is None:
                continue
            claimed_lbl[lbl] = best_code
            code_labels.setdefault(best_code, set()).add(lbl)
            zone_mask = (labels == lbl).astype(np.uint8) * 255
            zone_masks[best_code] = (np.maximum(zone_masks[best_code], zone_mask)
                                     if best_code in zone_masks else zone_mask)
            p2_added += 1

    zone_px = {code: int(np.sum(mask > 0)) for code, mask in zone_masks.items()}
    return zone_masks, zone_px


# ── per-sheet orchestrator ──────────────────────────────────────────────────
def run_sheet(pdf_path, page_idx, sheet_cfg: dict, out_png, dpi: int = 150) -> dict:
    """Full takeoff for one sheet using a per-sheet config. Returns
    {areas{code->sqft}, overlay(filename), tags(int), scale_in_per_ft}."""
    scale = float(sheet_cfg["scale_in_per_ft"])
    tag_re = re.compile(sheet_cfg["tag_pattern"], re.I)
    clip = sheet_cfg.get("clip", {"top": 0.05, "bottom": 0.92, "left": 0.0, "right": 0.80})

    tags = extract_tags(pdf_path, page_idx, clip, tag_re, sheet_cfg.get("tag_numeric_only", True))
    uniq: list[dict] = []
    for t in tags:
        if not any(u["code"] == t["code"] and abs(u["x"] - t["x"]) < 10 and abs(u["y"] - t["y"]) < 10
                   for u in uniq):
            uniq.append(t)

    # Tag position overrides (PDF points): relocate or add a tag when its printed
    # text-center is off the zone interior (leader-endpoint callouts in margins).
    overrides = sheet_cfg.get("tag_position_overrides") or {}
    if overrides:
        omap = {k: (float(v[0]), float(v[1])) for k, v in overrides.items()}
        for t in uniq:
            if t["code"] in omap:
                t["x"], t["y"] = omap[t["code"]]
        existing = {t["code"] for t in uniq}
        for code, (ox, oy) in omap.items():
            if code not in existing:
                uniq.append({"code": code, "x": ox, "y": oy})

    zcodes = sheet_cfg.get("zone_detection_codes")
    zset = set(zcodes) if zcodes else None
    zone_tags = [t for t in uniq if (zset is None or t["code"] in zset)]

    boundary = render_thick_boundaries(pdf_path, page_idx, dpi)
    hatch = render_hatch_lines(pdf_path, page_idx, dpi)
    binary = preprocess_for_fill(boundary)
    h, w = binary.shape
    rclip = int(w * clip.get("right", 0.80))
    lclip = int(w * clip.get("left", 0.0))
    tclip = int(h * clip.get("top", 0.0))
    bclip = int(h * clip.get("bottom", 1.0))
    hatch_dark = (hatch < 200).astype(np.uint8)
    # 4-side plan-area clip: drop the title block (right), the legend column
    # (left, if set), and the top/bottom margins so margin marks never seed or
    # collect into a zone.
    for arr in (binary, hatch_dark):
        arr[:, rclip:] = 0
        if lclip > 0:
            arr[:, :lclip] = 0
        if tclip > 0:
            arr[:tclip, :] = 0
        if bclip < h:
            arr[bclip:, :] = 0

    pe_codes = sheet_cfg.get("phase2_force_expand_codes")
    sk_codes = sheet_cfg.get("phase2_skip_codes")
    zone_masks, zone_px = detect_zones(
        binary, zone_tags, dpi / 72.0, dpi, scale, hatch_dark,
        float(sheet_cfg.get("phase1_min_zone_sf", 0)),
        float(sheet_cfg.get("phase2_radius_ft", 24)),
        phase1_max_zone_sf=float(sheet_cfg.get("phase1_max_zone_sf", 0)),
        phase2_force_expand_codes=set(pe_codes) if pe_codes else None,
        phase2_skip_codes=set(sk_codes) if sk_codes else None,
    )

    # ── Phase 3b: hatch-CC for hatch materials whose label is far from the zone ──
    # For codes in `hatch_cc_codes` (gravel / DG / turf with a leader-arrow callout
    # whose text label sits away from the zone): transform the text-space tag coord
    # to draw-space via _make_pt_transform, find the nearest hatch pixel, then take
    # the connected component of the *dilated* hatch image there. Overrides the
    # Phase 1/2 result for that code. (Companion to the lead's Phase 3 gray-fill,
    # triggered by `hatch_detect_codes`; Phase 3b is triggered by `hatch_cc_codes`.)
    phase3b_codes = set(sheet_cfg.get("hatch_cc_codes") or [])
    if phase3b_codes:
        px_per_sf = (scale * dpi) ** 2
        pt_to_px = dpi / 72.0
        doc = fitz.open(pdf_path)
        t3b = _make_pt_transform(doc[page_idx].rotation, doc[page_idx].rect)
        doc.close()
        p3b_min_sf = float(sheet_cfg.get("hatch_min_zone_sf") or 50.0)
        _hmax = sheet_cfg.get("hatch_max_zone_sf")
        p3b_max_sf = float(_hmax) if _hmax else h * w / px_per_sf * 0.30
        p3b_search_px = int(dpi * float(sheet_cfg.get("hatch_zone_search_in") or 3.0))
        k3b = int(sheet_cfg.get("hatch_cc_k") or 8)
        kern3b = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * k3b + 1, 2 * k3b + 1))
        dil3b = cv2.dilate(hatch_dark, kern3b)
        n3b, lbl3b, st3b, _ = cv2.connectedComponentsWithStats(dil3b, connectivity=8)
        for code in phase3b_codes:
            code_tags = [t for t in zone_tags if t["code"] == code]
            if not code_tags:
                continue
            best_px3b, best_mask3b = 0, None
            for tag in code_tags:
                vx, vy = t3b(tag["x"], tag["y"])
                zx = min(max(int(vx * pt_to_px), 0), w - 1)
                zy = min(max(int(vy * pt_to_px), 0), h - 1)
                y0, y1 = max(0, zy - p3b_search_px), min(h, zy + p3b_search_px)
                x0, x1 = max(0, zx - p3b_search_px), min(w, zx + p3b_search_px)
                ys_h, xs_h = np.where(hatch_dark[y0:y1, x0:x1] > 0)
                if not len(xs_h):
                    continue
                d_h = np.sqrt((xs_h + x0 - zx) ** 2 + (ys_h + y0 - zy) ** 2)
                ni = int(np.argmin(d_h))
                seed_x, seed_y = int(xs_h[ni] + x0), int(ys_h[ni] + y0)
                if not dil3b[seed_y, seed_x]:
                    continue
                cc_id = int(lbl3b[seed_y, seed_x])
                area_px = int(st3b[cc_id, cv2.CC_STAT_AREA])
                if p3b_min_sf <= area_px / px_per_sf <= p3b_max_sf and area_px > best_px3b:
                    best_px3b = area_px
                    best_mask3b = (lbl3b == cc_id).astype(np.uint8) * 255
            if best_mask3b is not None:
                old3b = zone_px.get(code, 0) / px_per_sf
                zone_px[code] = best_px3b
                zone_masks[code] = best_mask3b
                print(f"    Phase 3b {code}: {best_px3b / px_per_sf:.0f} SF via hatch-CC "
                      f"(Phase 1/2 was {old3b:.0f} SF)")
            else:
                print(f"    Phase 3b {code}: no valid hatch component found "
                      f"(kept Phase 1/2)")

    areas = {code: round(px_to_sqft(px, dpi, scale), 1) for code, px in zone_px.items()}

    doc = fitz.open(pdf_path)
    pix = doc[page_idx].get_pixmap(dpi=dpi)
    doc.close()
    base = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
    if base.shape[:2] != (h, w):
        base = cv2.resize(base, (w, h))
    over = base.copy()
    for code, mask in zone_masks.items():
        over[mask > 0] = code_color_rgb(code)
    blended = cv2.addWeighted(base, 0.45, over, 0.55, 0)
    if blended.shape[1] > 2200:
        nh = int(blended.shape[0] * 2200 / blended.shape[1])
        blended = cv2.resize(blended, (2200, nh))
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(blended).save(out_png)

    return {"areas": areas, "overlay": out_png.name, "tags": len(zone_tags),
            "scale_in_per_ft": scale, "masks": zone_masks}
