"""Parse the OE estimate PDF for per-surface area targets used as the
estimate-guided guide (currently pool/spa for pool mode).

The estimate lists items like "(POOL) ... 1,109 SF" and "(SPA) ... 161 SF".
"""
from __future__ import annotations

import re

import fitz


def parse_pool_targets(pdf_path: str) -> dict[str, float]:
    """Return {"POOL": sqft, "SPA": sqft} from the OE estimate (whatever it has)."""
    doc = fitz.open(pdf_path)
    text = re.sub(r"\s+", " ", " ".join(pg.get_text() for pg in doc))
    doc.close()
    out: dict[str, float] = {}
    for key in ("POOL", "SPA"):
        m = re.search(rf"\b{key}\b[^.]{{0,40}}?([\d,]+)\s*SF", text, re.I)
        if m:
            out[key] = float(m.group(1).replace(",", ""))
    return out
