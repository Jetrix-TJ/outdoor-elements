"""Parse the OE estimate PDF swimming pool scope of work into structured items.

Phase 1 (deterministic): regex extracts numbered items, sub-items, quantities,
total price, and area SF from the raw PDF text.
Phase 2 (Gemini): classify each item type. Graceful fallback: type stays None.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import fitz


def _extract_text(pdf_path: str | Path) -> str:
    with fitz.open(str(pdf_path)) as doc:
        return "\n".join(pg.get_text() for pg in doc)


def _find_section(text: str, section_name: str) -> str | None:
    """Return the text block starting at section_name through the next all-caps header.

    Uses an inline (?i) flag only for the header match so that the next-header
    lookahead stays case-sensitive — this prevents mid-paragraph sentences that
    start with a capital letter (e.g. 'Maintenance manuals.') from being mistaken
    for all-caps section headers.
    """
    escaped = re.escape(section_name.upper())
    pattern = (
        rf"(?m)^(?i:{escaped})[^\n]*\n"           # header line (case-insensitive)
        rf"(.*?)"                                   # body (lazy)
        rf"(?=^(?=.{{1,60}}\n)[A-Z][A-Z &/()0-9]{{3,}}[^\n]*\n|\Z)"  # next ALL-CAPS header (≤60 chars) or EOF
    )
    m = re.search(pattern, text, re.DOTALL)
    if not m:
        return None
    start = m.start()
    end = m.end()
    return text[start:end]


def _parse_sf(header_text: str) -> int | None:
    m = re.search(r"\(?([\d,]+)\s*SF\)?", header_text, re.I)
    return int(m.group(1).replace(",", "")) if m else None


def _parse_total(section_text: str, section_name: str) -> float | None:
    m = re.search(
        rf"(?i){re.escape(section_name)}\s+Total:?\s*\$?([\d,]+\.\d{{2}})",
        section_text,
    )
    return float(m.group(1).replace(",", "")) if m else None


def _parse_items(section_text: str) -> list[dict]:
    """Split section into numbered items, each with optional sub-items (a–h)."""
    # Strip total lines (e.g. "Swimming Pool Total: $549,863.52") before splitting,
    # so the last numbered item doesn't absorb the total line into its text.
    section_text = re.sub(r"(?im)^\w.*Total:?\s*\$[\d,]+\.\d{2}[^\n]*\n?", "", section_text)
    # Split on lines that start a new numbered item
    item_blocks = re.split(r"(?m)^(?=\d{1,2}\.\s)", section_text)
    items = []
    for block in item_blocks:
        block = block.strip()
        if not block:
            continue
        m = re.match(r"^(\d{1,2})\.\s+(.+)", block, re.DOTALL)
        if not m:
            continue
        number = int(m.group(1))
        body = m.group(2).strip()

        # sub-items: lines starting with "a." through "h."
        sub_items: list[dict] = []
        for sm in re.finditer(
            r"(?m)^([a-h])\.\s+(.+?)(?=^[a-h]\.\s|\Z)", body, re.DOTALL
        ):
            label = sm.group(1)
            desc_raw = sm.group(2).strip().replace("\n", " ")
            # optional leading quantity: "12 – ..." or "1 – ..." or "1 · ..." (PyMuPDF renders em-dash as ·)
            qty_m = re.match(r"^(\d+)\s*[–\-·]\s*(.+)", desc_raw)
            if qty_m:
                qty = int(qty_m.group(1))
                desc = qty_m.group(2).strip()
            else:
                qty = 1
                desc = desc_raw
            sub_items.append({"label": label, "qty": qty,
                               "description": desc, "unit": "ea"})

        # item text: everything up to the first sub-item (or full body)
        split_m = re.search(r"(?m)^[a-h]\.\s", body)
        item_text = body[:split_m.start()].strip() if split_m else body
        item_text = " ".join(item_text.split())  # collapse whitespace

        items.append({
            "number": number,
            "text": item_text,
            "type": None,
            "sub_items": sub_items,
        })
    return items


def _classify_with_gemini(items: list[dict], api_key: str) -> list[dict]:
    """Classify each item type via Gemini. Falls back silently on any error."""
    import google.generativeai as genai  # imported lazily — only needed with api_key
    payload = [{"number": it["number"], "text": it["text"][:200]} for it in items]
    prompt = (
        "Classify each numbered scope-of-work item for a swimming pool construction project.\n"
        "Valid types: labor, material, equipment, warranty, testing.\n\n"
        "Items:\n" + json.dumps(payload, indent=2) + "\n\n"
        "Respond with a JSON array: [{\"number\": 1, \"type\": \"labor\"}, ...]\n"
        "Output ONLY the raw JSON array, no markdown, no explanation."
    )
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-3.5-flash",
            generation_config={"temperature": 0, "max_output_tokens": 1024},
        )
        raw = model.generate_content(prompt).text
        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
        classifications = {
            entry["number"]: entry["type"] for entry in json.loads(raw.strip())
        }
        for item in items:
            item["type"] = classifications.get(item["number"])
    except Exception:  # noqa: BLE001
        pass  # leave type=None — panel renders without badges
    return items


def parse_pool_scope(
    pdf_path: str | Path,
    section_name: str = "SWIMMING POOL",
    api_key: str | None = None,
) -> dict | None:
    """Parse the named scope section from an estimate PDF.

    Returns structured dict or None if section not found.
    Pass api_key to enable Gemini type classification; omit for text-only parse.
    """
    text = _extract_text(pdf_path)
    section = _find_section(text, section_name)
    if not section:
        return None

    header_line = section.split("\n")[0]
    area_sf = _parse_sf(header_line)
    total_price = _parse_total(section, section_name)
    items = _parse_items(section)

    if api_key and items:
        items = _classify_with_gemini(items, api_key)

    return {
        "scope_type": section_name,
        "area_sf": area_sf,
        "total_price": total_price,
        "items": items,
    }
