# Outdoor Elements — UI Design & Product Audit

> Audit of the current front-end design language, theme, tokens, components, and
> interaction patterns, plus a short product reference. Reflects the state after
> the Google Material light-theme restyle and the Vortex-style edit UX.
> **Date:** 2026-06-18

---

## 1. Product (short reference)

**Outdoor Elements — AI Takeoff** is a proof-of-concept that reads a landscape /
hardscape construction drawing (PDF) and produces a material **takeoff** — how
many square feet of each material — then lets a human **correct** it and
**price** it.

**Who it's for:** construction estimators (Outdoor Elements, a Houston pool &
landscape builder).

**The flow (3 stages):**
1. **Stage 1 — Upload & select pages.** Drop a drawing set; a deterministic
   selector keeps only the takeoff plan sheets; a config panel lets the user
   review/correct each sheet's scale.
2. **Stage 2 — Detect & measure surfaces.** The line-width zone engine colors
   each material zone and measures its area; the user can manually **select and
   delete** wrong zones.
3. **Stage 3 — Measure, compare & price.** Shows our output vs. the human QTO,
   and a **costed estimate** (quantity × unit rate → total).

**Stack:** React + Vite (plain CSS), FastAPI + Celery/Redis backend, local-JSON
file storage. No database.

---

## 2. Design language

- **Google Material, light only.** White background, blue accents, generous
  white space, card-based information architecture. No dark mode.
- **Tone:** clean, helpful, modern; accent color used sparingly to draw the eye
  to actions, headings, and category labels.
- **Source:** design tokens/patterns extracted from a Material visual style
  guide (typography, color, spacing, card/button/chip/stat specs, motion, a11y).

---

## 3. Design tokens (implemented in `styles.css :root`)

### Color

| Token | Hex | Role |
|---|---|---|
| `--primary` | `#1a73e8` | CTAs, links, card titles, selection |
| `--primary-hover` | `#1557b0` | primary button hover |
| `--accent` | `#4285f4` | hero highlight, step badges |
| `--danger` | `#ea4335` | delete / errors (sparing) |
| `--bg` / `--surface` | `#ffffff` | page + card surface |
| `--surface-alt` | `#f1f3f4` | table headers, banded sections |
| `--surface-tint` | `#e8f0fe` | selected/hover card, chips |
| `--text` | `#202124` | primary text (never pure black) |
| `--text-secondary` | `#3c4043` | body copy |
| `--muted` | `#80868b` | captions, meta |
| `--border` | `#e0e3e7` | card borders, hairlines |
| `--divider` | `#bdc1c6` | dividers only (not text) |
| status | green `#1e8e3e` / amber `#b06000` / red `#d93025` | match / near / over |

### Type, spacing, shape, motion

- **Fonts:** Google Sans Text / Google Sans (display) / Roboto fallback; Material
  Symbols Outlined for icons. Loaded in `index.html`.
- **Scale:** hero 34px (blue accent phrase), section/card title 16px Medium,
  body 14–16px, caption 11–13px, **pricing total 18px** (Google Sans).
- **Spacing:** 8pt rhythm; page max-width **1200px**, padding 40/24px.
- **Radius:** card `12px`, button `8px`, chip/selection bar `999px`.
- **Shadow:** card `0 1px 2px / 0 2px 6px rgba(60,64,67,.05–.08)`; hover lifts.
- **Motion:** `180ms cubic-bezier(.2,0,0,1)` card/hover, 120–150ms controls.

---

## 4. Components (audit)

| Component | Pattern | Status |
|---|---|---|
| **Buttons** | primary (blue), secondary (white+blue border), ghost/tertiary; focus ring `0 0 0 3px rgba(26,115,232,.25)` | ✅ |
| **Chips** | page picker pills — white, blue text, `#e8f0fe` hover | ✅ |
| **Step badges** | stage rail — 28px numbered circles; blue=current, green ✓=done | ✅ |
| **Cards** | page cards, config / pricing / side panels — white, 12px radius, soft shadow, hover-lift | ✅ |
| **Tables** | compare / validation / pricing / config — `#f1f3f4` header, hairlines, status colors | ✅ |
| **Stat / banners** | verdict (MAPE) banners with green/amber tints; blue pricing total | ✅ |
| **Dropzone** | white, dashed `#dadce0`, hover blue + `#e8f0fe` tint | ✅ |
| **Edit UX** | Vortex-style: click zones → blue selection boxes + code tag → floating **"N selected · 🗑 Delete · ✕ Clear"** pill bar; per-material 🗑; ↶ Undo | ✅ |
| **Pricing** | editable $/sq-ft rates → cost + total | ✅ |
| **Lightbox** | white surface modal, 12px radius, soft shadow | ✅ |
| **Icons** | Material Symbols Outlined (delete/undo/edit/close/check), `aria-label` on icon-only buttons | ✅ |

---

## 5. Layout & screens

- **Shell:** centered 1200px column; header (hero + sub), horizontal **stage
  rail**, then the active stage's content.
- **Stage 1:** summary bar (kept badge) + responsive card **grid** of selected
  sheets (thumbnails) + the **config panel** (scale review table).
- **Stage 2:** two-column **`.s2grid`** — drawing overlay (left, with the edit
  selection layer) + side panel (Edit/Undo, material list with per-row trash).
- **Stage 3:** verdict banner + comparison table + **costed-estimate** table.

---

## 6. Motion & interaction

- Card hover lift; chip/selection transitions; visible focus rings.
- Edit: click-to-select (toggle), batch delete, one-level undo.
- No bounce, parallax, or scroll-jacking.

---

## 7. Accessibility

- Body ≥14px; icon-only buttons carry `aria-label`; focus visible on all
  controls. Contrast pairs are AA+ (`#1a73e8` on white 4.54; `#3c4043` 11.6).
  `#bdc1c6` used only for dividers, never text.

---

## 8. Audit findings

**Strengths**
- Single, consistent token system; every screen shares the same Material
  vocabulary (cards, chips, step badges, tables).
- White + blue brand applied uniformly; sparing accent use reads as intentional.
- Edit UX matches a recognized production pattern (Vortex multi-select + floating
  delete bar) — discoverable and safe (select → confirm → undo).
- Accessible defaults (focus, aria-labels, contrast).

**Gaps / opportunities**
- **No pan/zoom on the drawing.** The Stage-2 overlay is a rendered image, so
  there's no zoom-to-inspect or true vector canvas (Vortex's right-side
  tool palette of pan/zoom/draw is intentionally omitted to avoid dead controls).
- **No responsive / mobile pass.** Layout targets desktop; `.s2grid` and tables
  are not yet tuned for narrow viewports.
- **No empty/skeleton/loading states beyond a spinner.** Stage transitions could
  use skeletons.
- **Icon set minimal.** Could add line-icon tiles to card headers (the guide's
  `#e8f0fe` tile pattern) for stronger category cues.
- **No export.** Costed estimate is on-screen only (no CSV/PDF yet).
- **Single-theme.** Light only by design — fine for the POC.

**Recommended next UI steps (priority order)**
1. Pan/zoom the Stage-2 drawing (true canvas) — biggest usability win for editing.
2. Export the costed estimate (CSV/PDF).
3. Responsive pass for ≤1024px.
4. Card-header icon tiles + skeleton loaders for polish.

---

## 9. Where the UI lives (file map)

| File | Responsibility |
|---|---|
| `frontend/index.html` | Google Sans + Material Symbols font links |
| `frontend/src/styles.css` | all tokens + component styles (single stylesheet) |
| `frontend/src/App.jsx` | screens, stage flow, edit/pricing interactions |
| `frontend/src/api.js` | API client (upload, stage2, pick/remove/batch, pricing) |

*Plain CSS + React/Vite — no Tailwind, no component library.*
