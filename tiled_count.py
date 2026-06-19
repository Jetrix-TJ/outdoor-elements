"""Prototype: tiled Gemini counting of count-items (SF.x furniture, PL.x planters).

Borrows the Grodsky/Vortex recipe: tile the dense page, run Gemini vision per
tile with a focused prompt, aggregate across tiles, compare to the QTO legend.
"""
import os, re, json, time
from pathlib import Path
import numpy as np, cv2, pymupdf
from dotenv import load_dotenv; load_dotenv(".env")
import google.generativeai as genai
genai.configure(api_key=os.environ["GEMINI_API_KEY"])

PDF = r"C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\2811 Kirby - LANDSCAPE.pdf"
PAGE = 2
ROWS, COLS = 4, 4

# count items + ground truth from the QTO legend
ITEMS = {"SF.1": "Bike Rack", "SF.2A": "Bollard - Removable", "SF.2B": "Bollard - Fixed",
         "SF.3": "Bollard", "PL.1": "Planter A", "PL.2": "Planter B", "PL.3": "Planter C",
         "PL.4": "Planter D", "PL.7": "Planter G", "PL.8": "Planter H"}
GT = {"SF.1": 7, "SF.2A": 16, "SF.2B": 9, "SF.3": 41, "PL.1": 3, "PL.2": 8,
      "PL.3": 8, "PL.4": 9, "PL.7": 4, "PL.8": 11}

pg = pymupdf.open(PDF)[PAGE]
pix = pg.get_pixmap(dpi=200)
img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
H, W = img.shape[:2]
# crop off the right title-block strip
img = img[:, : int(W * 0.83)]
H, W = img.shape[:2]
print(f"page {W}x{H}, tiling {ROWS}x{COLS}")

model = genai.GenerativeModel("gemini-2.5-flash")
item_list = "\n".join(f"  {c}: {n}" for c, n in ITEMS.items())
prompt = (
    "This is a TILE of a landscape site plan. Count the SITE FURNITURE / PLANTER "
    "symbols visible in THIS tile. Item types (from the legend):\n" + item_list +
    "\nBollards are small filled circles/dots; bike racks are small U/line racks; "
    "planters are labeled PL.x outlines. Count only fully-visible instances. "
    'Return ONLY JSON {"SF.3": n, "PL.1": n, ...} for items you actually see (omit zeros).'
)

totals = {c: 0 for c in ITEMS}
th, tw = H // ROWS, W // COLS
for r in range(ROWS):
    for cl in range(COLS):
        tile = img[r*th:(r+1)*th, cl*tw:(cl+1)*tw]
        ok, buf = cv2.imencode(".png", cv2.cvtColor(tile, cv2.COLOR_RGB2BGR))
        try:
            resp = model.generate_content([prompt, {"mime_type": "image/png", "data": buf.tobytes()}])
            m = re.search(r"\{.*\}", resp.text or "", re.S)
            d = json.loads(m.group(0)) if m else {}
            for c, v in d.items():
                if c in totals and isinstance(v, (int, float)):
                    totals[c] += int(v)
        except Exception as e:
            print(f"  tile {r},{cl} failed: {str(e)[:60]}")
        time.sleep(0.4)

print(f"\n{'code':7}{'name':22}{'QTO':>5}{'Gemini':>8}{'err':>7}")
errs = []
for c in ITEMS:
    g = totals[c]; gt = GT[c]
    e = (g - gt) / gt * 100 if gt else 0
    errs.append(abs(e))
    print(f"{c:7}{ITEMS[c][:22]:22}{gt:>5}{g:>8}{e:>+6.0f}%")
print(f"\nMean abs error: {sum(errs)/len(errs):.0f}%")
