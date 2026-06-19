"""Gemini auto-config builder — ported from the lead's `generate_config.py`.

Scores PDF pages to find material-plan sheets, renders candidates to JPEG, and
asks Gemini to emit a machine-readable qto_config.json (sheets, scale, tag
pattern/placement, which codes are AREA materials, materials dict).

Model: gemini-3.5-flash via google.generativeai (the lead used 3.1-pro-preview,
not available on our key). Prompt text preserved verbatim.
"""
from __future__ import annotations

import json
import re

import fitz  # PyMuPDF
import google.generativeai as genai

MODEL_NAME = "gemini-3.5-flash"

# ── page scoring ────────────────────────────────────────────────────────────
SCALE_RE = re.compile(
    r"""(?:SCALE\s*[:\-]?\s*)?
        (?:
            1["'″]?\s*[=:]\s*(\d+)['′\-]?\s*0?["']?  |  # 1"=20'-0" or 1=20
            1\s*/\s*(\d+)["'″]?\s*=\s*1['′\-]         # 1/8"=1'-0"
        )
    """,
    re.VERBOSE | re.IGNORECASE,
)
SHEET_RE = re.compile(
    r'\b([LPC][STP]?[\-\.]?\d{2,4}[A-Z]?)\b'
    r'|\b(L[\-\.]?\d[\-\.]\d{2,3})\b'
    r'|\b([A-Z]\d{3})\b'
)
CODE_RE = re.compile(r'\b([A-Z]{1,3}-\d{1,2})\b|\b([A-Z]\d{1,2})\b|\bM\.?\d{1,2}\b')
PLAN_KEYWORDS = re.compile(
    r'\b(HARDSCAPE|MATERIALS?\s+PLAN|LANDSCAPE\s+PLAN|SITE\s+PLAN|PLANTING\s+PLAN)\b', re.I)
SCHED_KEYWORDS = re.compile(r'\b(SCHEDULE|LEGEND|MATERIALS?\s+LIST)\b', re.I)
SKIP_KEYWORDS = re.compile(
    r'\b(STRUCTURAL\s+DETAIL|MECHANICAL\s+PLAN|PLUMBING\s+PLAN|ELECTRICAL\s+PLAN|CIVIL\s+PLAN)\b', re.I)


def score_page(page: fitz.Page) -> dict:
    text = page.get_text("text")
    score = 0
    if SKIP_KEYWORDS.search(text):
        score -= 5
    scale_m = SCALE_RE.search(text)
    scale_in_per_ft = None
    if scale_m:
        score += 4
        denom = scale_m.group(1) or scale_m.group(2)
        if denom:
            scale_in_per_ft = 1.0 / int(denom)
    sheet_m = SHEET_RE.search(text)
    sheet_id = next((g for g in (sheet_m.groups() if sheet_m else []) if g), None)
    if PLAN_KEYWORDS.search(text):
        score += 3
    is_schedule = bool(SCHED_KEYWORDS.search(text))
    if is_schedule:
        score += 1
    score += min(len(CODE_RE.findall(text)) // 3, 5)
    return {"score": score, "sheet_id": sheet_id, "scale_in_per_ft": scale_in_per_ft,
            "is_schedule": is_schedule, "text_snippet": text[:200].replace("\n", " ")}


def find_key_pages(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    scored = []
    for i, page in enumerate(doc):
        info = score_page(page)
        info["page_idx"] = i
        scored.append(info)
    doc.close()
    positive = [p for p in scored if p["score"] > 0]
    positive.sort(key=lambda x: -x["score"])
    cap = 8 if n_pages <= 20 else 12
    return {"schedule": [], "plans": positive[:cap]}


def render_page_jpeg(pdf_path: str, page_idx: int, dpi: int = 96) -> bytes:
    doc = fitz.open(pdf_path)
    page = doc[page_idx]
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    jpeg = pix.tobytes("jpeg", jpg_quality=85)
    doc.close()
    return jpeg


# ── Gemini prompts (verbatim from generate_config.py) ───────────────────────
SYSTEM_PROMPT = """\
You are an expert at reading landscape architecture PDF drawings.
You will analyze rendered images of pages from a landscape PDF and output a
machine-readable JSON configuration for an automated area take-off script.

Output ONLY valid JSON — no markdown code fences, no explanation, just the raw JSON object.
"""

USER_PROMPT_TEMPLATE = r"""The images below are pages from a landscape architecture PDF.
Page labels (e.g. "PAGE 7 (idx 6)") are printed on each image.

Your task is to extract the configuration for an automated quantity take-off script.

─── WHAT TO EXTRACT ───────────────────────────────────────────────────────────

1. MATERIAL PLAN SHEETS — pages that show a hardscape/materials plan with:
   • A drawing scale (e.g. 1"=20'-0", 1/8"=1'-0")
   • Material callout tags (small codes like M.5, CO-1, A1 etc.) placed on zones
   For each plan sheet output:
     • "sheet_id": the sheet number from the title block (e.g. "LS101", "L1.01")
     • "page": the 1-based page number shown in the label
     • "title": the sheet title (e.g. "Hardscape Plan - North")
     • "scale_in_per_ft": numeric scale factor
         1"=20'-0"  → 0.05    1"=10'-0"  → 0.1    1"=5'-0"   → 0.2
         1/16"=1'-0" → 0.0625  1/8"=1'-0"  → 0.125  1/4"=1'-0" → 0.25

2. TAG FORMAT — examine the material callout tags visible on the plan:
   • What do they look like?  Examples: M.5, M5, (M.5), CO-1, CO1, A1, B12
   • "tag_pattern": a Python regex that FULLY matches one tag.
       - M.N / M-N / (M.N) style  → "^\\(?M[-.]?(\\d{1,2})\\)?$"  (set tag_numeric_only: true)
       - XX-N style (CO-1, WA-2)  → "^([A-Z]{1,3}-[1-9]\\d*)$"
       - A1 / B12 style           → "^([A-Z]\\d{1,2})$"
   • "tag_numeric_only": true only for M.N style (the prefix "M." is added by the script);
     false for all other styles

3. TAG PLACEMENT — look carefully at where the tag text sits relative to the colored zone:
   • "zone_interior" — the tag code is printed INSIDE the material area
   • "leader_endpoint" — the tag is outside the area, connected by a leader/arrow line
   This matters for the algorithm: if "leader_endpoint", set phase1_min_zone_sf to 50.

4. PLAN CLIP — does the drawing content (including callout tags) extend BELOW the
   visible page border into a "pasteboard" / title strip area?
   • If YES (tags exist below the page border line), set plan_clip_bottom_pct to 1.10
   • If NO, use 0.92

5. MATERIALS SCHEDULE — if a schedule/legend page is shown, extract ALL material codes
   and their descriptions into the "materials" dict.

6. ZONE DETECTION CODES — which material codes represent SURFACE AREAS (paving,
   concrete, turf, gravel, decking, pool plaster)?  Only these should have their
   polygon areas measured.  Point/linear features (fences, gates, lights, drains,
   handrails, site furnishings, bollards, signs) should NOT get area measurements.
   • "zone_detection_codes": list of code strings that ARE area materials.
   • Omit codes for: fences (FE-*), lights (LI-*), drains (DR-*), handrails (R-*),
     site furnishings (SF-*), bollards, signs, grill stations, fireplaces, pool
     fittings (handrails, lifts), edging (ED-*).
   • Include codes for: concrete (CO-*), pavers (PV-*), gravel/DG (AG-*), turf (MI-*
     when it's turf/court slab), decking (WO-*), stone veneer/retaining walls (WA-*)
     where they represent filled areas, pool plaster/water surface (PO-* plaster only).

─── OUTPUT FORMAT ─────────────────────────────────────────────────────────────

{
  "sheets": {
    "LS101": {
      "title": "Hardscape Plan - North",
      "scale_in_per_ft": 0.05,
      "page": 7
    }
  },
  "tag_pattern": "^([A-Z]{1,3}-[1-9]\\d*)$",
  "tag_numeric_only": false,
  "tag_placement": "zone_interior",
  "plan_clip_bottom_pct": 0.92,
  "plan_clip_right_pct": 0.80,
  "phase1_min_zone_sf": 0,
  "phase2_radius_ft": 24,
  "zone_detection_codes": ["CO-1", "CO-2", "AG-1", "PV-1"],
  "materials": {
    "CO-1": "Standard Gray Concrete",
    "CO-2": "Integral Color Concrete"
  }
}

Rules:
• plan_clip_right_pct: use 0.80 unless there is a visible schedule/legend on the LEFT
  side (not right), in which case identify the approximate left boundary of the plan
  area and set plan_clip_left_pct accordingly (default 0).
• phase1_min_zone_sf: set to 50 when tag_placement is "leader_endpoint", else 0.
• phase2_radius_ft: use 24 for zone_interior; use 60 for leader_endpoint drawings.
• If a scale graphic bar is present but no text scale, estimate from the bar.
• Include ONLY material/hardscape plan sheets — not detail, section, or grading sheets.
• For the materials dict, include ALL codes visible in any schedule/legend.
• zone_detection_codes: list only codes that genuinely represent filled surface areas.

Output ONLY the raw JSON object — no markdown, no preamble, no explanation.
"""


def call_gemini(api_key: str, images_with_labels: list[tuple[bytes, str]]) -> str:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(
        MODEL_NAME, system_instruction=SYSTEM_PROMPT,
        generation_config={"temperature": 0.1, "max_output_tokens": 8192},
    )
    parts: list = [USER_PROMPT_TEMPLATE]
    for jpeg, label in images_with_labels:
        parts.append(f"\n[{label}]\n")
        parts.append({"mime_type": "image/jpeg", "data": jpeg})
    return model.generate_content(parts).text


def extract_json(text: str) -> dict:
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text.strip(), flags=re.MULTILINE)
    return json.loads(text.strip())


def finalize_config(cfg: dict) -> dict:
    cfg.setdefault("tag_pattern", r"^([A-Z]{1,3}-[1-9]\d*)$")
    cfg.setdefault("tag_numeric_only", False)
    cfg.setdefault("tag_placement", "zone_interior")
    cfg.setdefault("plan_clip_bottom_pct", 0.92)
    cfg.setdefault("plan_clip_right_pct", 0.80)
    cfg.setdefault("plan_clip_left_pct", 0.0)
    cfg.setdefault("plan_clip_top_pct", 0.05)
    cfg.setdefault("phase1_min_zone_sf", 50 if cfg.get("tag_placement") == "leader_endpoint" else 0)
    cfg.setdefault("phase2_radius_ft", 60 if cfg.get("tag_placement") == "leader_endpoint" else 24)
    cfg.setdefault("materials", {})
    cfg.setdefault("sheets", {})
    if "zone_detection_codes" in cfg and not cfg["zone_detection_codes"]:
        del cfg["zone_detection_codes"]
    for sn, info in cfg["sheets"].items():
        if "scale_in_per_ft" in info:
            info["scale_in_per_ft"] = float(info["scale_in_per_ft"])
    return cfg


def build_config(pdf_path: str, api_key: str) -> dict:
    """Render candidate plan pages, call Gemini, return the finalized config."""
    key = find_key_pages(pdf_path)
    pages = sorted({p["page_idx"] for p in key["plans"]})[:12]
    images = [(render_page_jpeg(pdf_path, i, dpi=96), f"PAGE {i + 1} (idx {i})") for i in pages]
    raw = call_gemini(api_key, images)
    cfg = finalize_config(extract_json(raw))
    cfg["source"] = "gemini"
    return cfg
