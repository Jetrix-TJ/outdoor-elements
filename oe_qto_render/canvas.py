"""A tiny vector drawing surface with two dependency-free backends.

The renderer draws to a `Canvas`; `SvgCanvas` emits an SVG string and
`PdfCanvas` draws onto a fitz/PyMuPDF page. Both keep geometry and text vector;
the base plan is an embedded raster image. One draw path → two vector outputs.

Coordinates are PDF points, origin top-left, y down (matches SVG and fitz).
"""
from __future__ import annotations

import base64
from typing import Literal

Anchor = Literal["start", "middle", "end"]
Point = tuple[float, float]


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


class Canvas:
    """Abstract surface. Subclasses implement the primitives."""

    def image(self, png: bytes, x: float, y: float, w: float, h: float) -> None: ...
    def polygon(self, pts, fill: str, opacity: float = 1.0) -> None: ...
    def polyline(self, pts, stroke: str, width: float) -> None: ...
    def line(self, p1: Point, p2: Point, stroke: str, width: float) -> None: ...
    def circle(self, cx: float, cy: float, r: float, fill: str) -> None: ...
    def rounded_rect(self, x, y, w, h, r, fill, stroke, stroke_width) -> None: ...
    def text(self, x, y, s, size, fill, font, anchor: Anchor = "start",
             angle: float = 0.0) -> None: ...


# ── SVG backend (canonical) ─────────────────────────────────────────────────
_FONT_FAMILY = {
    "Times New Roman": "'Times New Roman', Times, serif",
    "Verdana": "Verdana, Geneva, sans-serif",
}


class SvgCanvas(Canvas):
    def __init__(self, width: float, height: float):
        self.w = width
        self.h = height
        self._parts: list[str] = []

    def _pts(self, pts) -> str:
        return " ".join(f"{x:.3f},{y:.3f}" for x, y in pts)

    def image(self, png, x, y, w, h):
        uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        self._parts.append(
            f'<image x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
            f'href="{uri}" preserveAspectRatio="none"/>')

    def polygon(self, pts, fill, opacity=1.0):
        self._parts.append(
            f'<polygon points="{self._pts(pts)}" fill="{fill}" '
            f'fill-opacity="{opacity:.3f}" stroke="none"/>')

    def polyline(self, pts, stroke, width):
        self._parts.append(
            f'<polyline points="{self._pts(pts)}" fill="none" stroke="{stroke}" '
            f'stroke-width="{width:.3f}" stroke-linecap="round" '
            f'stroke-linejoin="round"/>')

    def line(self, p1, p2, stroke, width):
        self._parts.append(
            f'<line x1="{p1[0]:.3f}" y1="{p1[1]:.3f}" x2="{p2[0]:.3f}" '
            f'y2="{p2[1]:.3f}" stroke="{stroke}" stroke-width="{width:.3f}" '
            f'stroke-linecap="round"/>')

    def circle(self, cx, cy, r, fill):
        self._parts.append(
            f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" fill="{fill}"/>')

    def rounded_rect(self, x, y, w, h, r, fill, stroke, stroke_width):
        self._parts.append(
            f'<rect x="{x:.3f}" y="{y:.3f}" width="{w:.3f}" height="{h:.3f}" '
            f'rx="{r:.3f}" ry="{r:.3f}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{stroke_width:.3f}"/>')

    def text(self, x, y, s, size, fill, font, anchor="start", angle=0.0):
        fam = _FONT_FAMILY.get(font, font)
        a = {"start": "start", "middle": "middle", "end": "end"}[anchor]
        transform = f' transform="rotate({angle:.3f} {x:.3f} {y:.3f})"' if angle else ""
        self._parts.append(
            f'<text x="{x:.3f}" y="{y:.3f}" font-family="{fam}" '
            f'font-size="{size:.3f}" fill="{fill}" text-anchor="{a}"{transform}>'
            f'{_esc(s)}</text>')

    def tostring(self) -> str:
        body = "\n".join(self._parts)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{self.w:.3f}" height="{self.h:.3f}" '
            f'viewBox="0 0 {self.w:.3f} {self.h:.3f}">\n{body}\n</svg>\n')


# ── PDF backend (fitz, vector) ──────────────────────────────────────────────
_PDF_FONT = {"Times New Roman": "tiro", "Verdana": "helv"}  # tiro = Times-Roman


def _rgb(hexcol: str) -> tuple[float, float, float]:
    h = hexcol.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


class PdfCanvas(Canvas):
    def __init__(self, width: float, height: float):
        import fitz
        self._fitz = fitz
        self.doc = fitz.open()
        self.page = self.doc.new_page(width=width, height=height)

    def image(self, png, x, y, w, h):
        rect = self._fitz.Rect(x, y, x + w, y + h)
        self.page.insert_image(rect, stream=png)

    def polygon(self, pts, fill, opacity=1.0):
        sh = self.page.new_shape()
        sh.draw_polyline([self._fitz.Point(*p) for p in pts] +
                         [self._fitz.Point(*pts[0])])
        sh.finish(color=None, fill=_rgb(fill), fill_opacity=opacity, width=0)
        sh.commit()

    def polyline(self, pts, stroke, width):
        sh = self.page.new_shape()
        sh.draw_polyline([self._fitz.Point(*p) for p in pts])
        sh.finish(color=_rgb(stroke), width=width, lineCap=1, lineJoin=1)
        sh.commit()

    def line(self, p1, p2, stroke, width):
        sh = self.page.new_shape()
        sh.draw_line(self._fitz.Point(*p1), self._fitz.Point(*p2))
        sh.finish(color=_rgb(stroke), width=width, lineCap=1)
        sh.commit()

    def circle(self, cx, cy, r, fill):
        sh = self.page.new_shape()
        sh.draw_circle(self._fitz.Point(cx, cy), r)
        sh.finish(color=None, fill=_rgb(fill), width=0)
        sh.commit()

    def rounded_rect(self, x, y, w, h, r, fill, stroke, stroke_width):
        sh = self.page.new_shape()
        rect = self._fitz.Rect(x, y, x + w, y + h)
        sh.draw_rect(rect, radius=r / min(w, h))
        sh.finish(color=_rgb(stroke), fill=_rgb(fill), width=stroke_width)
        sh.commit()

    def text(self, x, y, s, size, fill, font, anchor="start", angle=0.0):
        fontname = _PDF_FONT.get(font, "helv")
        tl = self._fitz.get_text_length(s, fontname=fontname, fontsize=size)
        if anchor == "middle":
            x -= tl / 2
        elif anchor == "end":
            x -= tl
        morph = None
        if angle:
            pivot = self._fitz.Point(x, y)
            mat = self._fitz.Matrix(angle)
            morph = (pivot, mat)
        self.page.insert_text((x, y), s, fontsize=size, fontname=fontname,
                              color=_rgb(fill), morph=morph)

    def save(self, path: str) -> None:
        self.doc.save(path, deflate=True)
