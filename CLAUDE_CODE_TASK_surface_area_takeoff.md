# Claude Code Task — Surface-Area Takeoff from a Drawing (Outdoor Elements POC)

> Paste this whole file to Claude Code as the task brief. Fill in the three
> `<PATH ...>` placeholders with your real local paths before running.

---

## 1. Context

We are building a POC for **Outdoor Elements**, a Houston commercial pool &
landscape builder. The goal of the overall POC: an AI that reads construction
drawings and produces a **takeoff** (quantities of every material), then checks
itself against the real human-made takeoff (the "ground truth").

I have **three reference repos cloned locally**:
- `<PATH TO grodsky-poc>` — **the blueprint. Study and reuse this.** It is a working
  AI takeoff pipeline for an HVAC subcontractor with stages `s2..s8`
  (`backend/app/stages/`). Same approach as ours, different trade.
- `<PATH TO vortex>` — production version of the same idea (reference only; one useful
  pattern: detect geometry deterministically first, then let the LLM validate).
- `accela-ai` — empty Streamlit template, **ignore it**.

Ground-truth takeoff file: `<PATH TO 2811_KIRBY_QTO.pdf>`

**This task = implement the part my lead asked for:**
> "Build a script that runs through a drawing and outputs the annotation with the
> surface area. Let Claude compare ground-truth takeoff vs its output and improve."

---

## 2. Goal (one sentence)

Build a Python script that takes **one drawing sheet**, detects each **surface-area
material**, produces an **annotated image** showing what it found, outputs the
**measured area (sq ft) per material**, then **compares to the ground-truth QTO and
iterates to improve accuracy.**

Scope for v1: **sheet `L1.01` (page 1 of the QTO PDF), AREA items only** (the
`sq ft` materials). Counts and linear-foot items come later.

---

## 3. What to build (step by step)

Reuse Grodsky's stage patterns where possible — read `grodsky-poc/backend/app/stages/`
first (`s2_extract.py`, `s4_takeoff.py`, `s8_output.py`) and adapt, don't reinvent.

1. **Extract the sheet image** (like Grodsky `s2_extract`)
   - Use **PyMuPDF (fitz)** to render page 1 of the QTO PDF to PNG at ~200 DPI.

2. **Read the legend** to get the target area items + their colors
   - The legend lists items like `(M.5) Concrete Paver A 6,550.15 sq ft`.
   - Use **Gemini vision** to read the legend → list of `{code, name, unit}`.
   - Keep only `sq ft` (area) items for this task.

3. **Detect each material's surface region** (the hard part — try both, keep what wins)
   - **Approach A (recommended anchor): color segmentation with OpenCV.**
     Each material is a colored fill matching its legend swatch. Sample the swatch
     color, find matching regions on the plan, get their **pixel area**.
   - **Approach B: Gemini vision** — ask it to outline/estimate each material's area.
   - Combining A (deterministic) + B (validation) is the Vortex anti-hallucination pattern.

4. **Convert pixel area → real sq ft using the sheet scale**
   - The sheet states a scale (e.g. `1 inch = 10 feet`). You must calibrate
     **pixels-per-foot**: detect the **scale bar** graphic (known real length) and
     measure its pixel length, or use the drawing grid. Document the calibration.

5. **Annotate the image** (this is the "output the annotation" part)
   - Overlay the detected regions on the sheet image (colored outline / mask) and
     label each with `code + name + measured sq ft`.
   - Save as `annotated_L1_01.png`.

6. **Output structured results**
   - Save `output_L1_01.json` (and `.csv`) of rows:
     `{ "sheet":"L1.01", "code":"M.16", "name":"Beach Pebble", "measured_sqft": <num>, "unit":"area_sqft" }`
   - Use a **Pydantic** model for the row schema (like Grodsky's typed outputs).

7. **Compare to ground truth + improve** (like Grodsky `s8_output`)
   - Compare each measured area to the ground-truth values in Section 4.
   - Print a table: `code | name | ground_truth | measured | error_%`.
   - Print overall accuracy (mean absolute % error).
   - **Then iterate:** adjust color thresholds / scale calibration / prompts and
     re-run until accuracy improves. Target: **< 15% error per item first, then < 5%.**
   - Log what you changed each iteration and how accuracy moved.

---

## 4. Ground truth — Kirby `L1.01` area items (sq ft)

From the QTO legend, page 1. Compare your measured areas against these:

| Code | Name | Ground truth (sq ft) |
|------|------|----------------------|
| M.5  | Concrete Paver A | 6,550.15 |
| M.6  | Concrete Paver B | 1,760.05 |
| M.7  | Tile Paver A     | 191.42 |
| M.15 | River Rock       | 368.57 |
| M.16 | Beach Pebble     | 413.5 |

(The same sheet also has linear-ft walls/curbs and count items like bollards/planters —
**ignore those for now**; this task is surface area only.)

---

## 5. Tech constraints

- **Python 3.11+**, **PyMuPDF** (PDF→image), **Pillow/OpenCV** (image + segmentation),
  **google-generativeai** (Gemini vision), **Pydantic v2** (typed output).
- Vision model: **Gemini** (Grodsky uses `gemini-2.0-flash` for detection). Read the
  API key from an env var `GEMINI_API_KEY`; never hardcode it.
- Keep everything runnable from one command, e.g. `python run_takeoff.py`.
- Don't use browser localStorage or any web storage; this is a local script.

---

## 6. Deliverables / success criteria

- [ ] `run_takeoff.py` — runs end to end on page 1 of the QTO PDF.
- [ ] `annotated_L1_01.png` — the sheet with detected area regions outlined + labeled.
- [ ] `output_L1_01.json` and `output_L1_01.csv` — measured areas per material.
- [ ] A printed **comparison report** vs the Section 4 ground truth (per-item % error + overall accuracy).
- [ ] A short `RESULTS.md` noting the accuracy reached, what calibration/approach worked,
      and what to try next to improve.

**Definition of done for v1:** the script produces an annotated image and a measured
area for at least M.5, M.6, M.15, M.16, and reports % error vs ground truth, with an
attempt to iterate and improve the worst items.

---

## 7. How to extend later (note for the future, don't build now)

- Add **count** items (bollards, planters, plants) — easiest, highest accuracy.
- Add **linear-ft** items (curbs, coping, light runs).
- Generalize from `L1.01` to all sheets (`L1.02`, `L1.04`, `AQ0.0` pool, `L5.x` planting).
- Wire into Grodsky's full pipeline + the React review UI.

---

## 8. Pitfalls to watch

- **Area measurement is genuinely hard** — expect to spend most iteration time on
  scale calibration and color thresholds, not on the vision call.
- **Vision models hallucinate on dense plans** — that's why the color-segmentation
  anchor (Approach A) matters; trust geometry, use the LLM to label/validate.
- **Scale differs per sheet** — always read it from the sheet, don't hardcode across sheets.
- **Overlapping / similar colors** (different blues for paver A vs B) — separate them
  carefully; this is where most error will come from.
