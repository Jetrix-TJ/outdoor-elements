"""Prototype: FastSAM region segmentation prompted by material labels.

Renders L1.01, extracts the M.x/W.x callout label positions, and prompts FastSAM
with each label point to get a region mask, then colors each by material.
"""
import re
import numpy as np
import cv2
import pymupdf

PDF = r"C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\2811 Kirby - LANDSCAPE.pdf"
PAGE = 2
TARGET_W = 1500  # FastSAM works on a modest image size

pg = pymupdf.open(PDF)[PAGE]
pw, ph = pg.rect.width, pg.rect.height
dpi = int(round(TARGET_W / (pw / 72.0)))
pix = pg.get_pixmap(dpi=dpi)
img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
H, W = img.shape[:2]
s = dpi / 72.0
print("render", W, "x", H)

code_re = re.compile(r"^(M|W)\.\d+[A-Za-z]?$")
inleg = lambda cx, cy: (0.18 * pw < cx < 0.50 * pw) and (0.38 * ph < cy < 0.78 * ph)
labels = []
for w in pg.get_text("words"):
    t = w[4].strip().strip("()")
    if code_re.match(t):
        cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
        if not inleg(cx, cy):
            labels.append((t, int(cx * s), int(cy * s)))
codes = sorted({t for t, _, _ in labels})
PAL = [(255,193,7),(33,150,243),(76,175,80),(233,30,99),(156,39,176),(0,188,212),
       (121,85,72),(255,87,34),(63,81,181),(205,220,57),(0,150,136),(244,67,54),
       (255,152,0),(96,125,139),(139,195,74)]
col = {c: PAL[i % len(PAL)] for i, c in enumerate(codes)}
print(f"{len(labels)} labels, {len(codes)} materials")

cv2.imwrite("outputs/_sam_in.png", cv2.cvtColor(img, cv2.COLOR_RGB2BGR))

from ultralytics import FastSAM
model = FastSAM("FastSAM-s.pt")

faded = cv2.addWeighted(img, 0.5, np.full_like(img, 255), 0.5, 0)
out = faded.copy()
areas = {}
for code in codes:
    pts = [[x, y] for t, x, y in labels if t == code]
    if not pts:
        continue
    try:
        res = model("outputs/_sam_in.png", device="cpu", retina_masks=True,
                    imgsz=1024, conf=0.3, iou=0.9, points=pts, labels=[1] * len(pts), verbose=False)
        masks = res[0].masks
        if masks is None:
            continue
        m = masks.data.cpu().numpy().any(0)
        m = cv2.resize(m.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST).astype(bool)
        c = np.array(col[code])
        out[m] = (0.55 * c + 0.45 * faded[m]).astype(np.uint8)
        areas[code] = int(m.sum())
    except Exception as e:
        print("  ", code, "FAILED:", type(e).__name__, str(e)[:80])

for t, x, y in labels:
    cv2.circle(out, (x, y), 3, (0, 0, 0), -1)
cv2.imwrite("outputs/_sam_out.png", cv2.cvtColor(out, cv2.COLOR_RGB2BGR))
print("colored", len(areas), "materials -> outputs/_sam_out.png")
