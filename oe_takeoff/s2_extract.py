"""Stage 2 - extract the sheet image + text layer (adapts Grodsky s2_extract)."""
from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import numpy as np

from . import config


def render_page(pdf_path: Path, page: int, dpi: int) -> np.ndarray:
    """Render a PDF page to an RGB numpy array (H, W, 3)."""
    doc = fitz.open(pdf_path)
    pix = doc[page].get_pixmap(dpi=dpi)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    rgb = np.ascontiguousarray(arr[:, :, :3])
    doc.close()
    return rgb


def page_size_inches(pdf_path: Path, page: int) -> tuple[float, float]:
    doc = fitz.open(pdf_path)
    r = doc[page].rect
    doc.close()
    return r.width / 72.0, r.height / 72.0


def text_words(pdf_path: Path, page: int) -> list[tuple]:
    """Words as (x0, y0, x1, y1, text, block, line, word) in PDF points."""
    doc = fitz.open(pdf_path)
    words = doc[page].get_text("words")
    doc.close()
    return words


def vector_drawings(pdf_path: Path, page: int) -> list[dict]:
    """Filled vector paths (PyMuPDF get_drawings) - exact polygon geometry."""
    doc = fitz.open(pdf_path)
    draws = doc[page].get_drawings()
    doc.close()
    return draws


def save_png(rgb: np.ndarray, out: Path) -> Path:
    import cv2
    cv2.imwrite(str(out), cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    return out


def extract(page: int = config.DEFAULT_PAGE_INDEX, sheet: str | None = None) -> dict:
    """Render a sheet page and capture its text + vector layers."""
    pdf, dpi = config.PDF_PATH, config.RENDER_DPI
    if not pdf.exists():
        raise FileNotFoundError(f"Drawing PDF not found: {pdf}")
    label = sheet or f"p{page}"
    rgb = render_page(pdf, page, dpi)
    w_in, h_in = page_size_inches(pdf, page)
    words = text_words(pdf, page)
    drawings = vector_drawings(pdf, page)
    raw_png = save_png(rgb, config.OUTPUT_DIR / f"sheet_{label.replace('.', '_')}.png")
    return {
        "page": page,
        "sheet": label,
        "rgb": rgb,
        "words": words,
        "drawings": drawings,
        "page_w_in": w_in,
        "page_h_in": h_in,
        "raw_png": raw_png,
        "ppi": dpi / 72.0,  # render pixels per PDF point-inch
    }
