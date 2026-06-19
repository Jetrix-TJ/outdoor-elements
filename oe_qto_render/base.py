"""Render the base plan PDF page to a lightened gray PNG for the underlay.

Matches how AQ0.0 is composed: the base linework sits underneath in light gray,
the color overlay on top. Output is raster (the base only); all overlay content
stays vector.
"""
from __future__ import annotations

import numpy as np

from . import style
from .model import Base


def render_gray_base(base: Base) -> tuple[bytes, int, int]:
    """Return (png_bytes, px_w, px_h): the base page desaturated and lightened
    toward white by `style.BASE_GRAY_LIGHTEN`."""
    import fitz
    from PIL import Image

    doc = fitz.open(base.pdf)
    page = doc[base.page_index]
    pix = page.get_pixmap(dpi=style.BASE_DPI)
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    rgb = arr[:, :, :3].astype(np.float32)
    doc.close()

    # luminance -> gray, then lighten toward white
    gray = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
    k = style.BASE_GRAY_LIGHTEN
    light = gray * (1 - k) + 255 * k
    out = np.repeat(light[:, :, None], 3, axis=2).clip(0, 255).astype(np.uint8)

    import io
    buf = io.BytesIO()
    Image.fromarray(out).save(buf, format="PNG")
    return buf.getvalue(), pix.width, pix.height
