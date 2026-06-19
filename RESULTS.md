# RESULTS - Surface-Area Takeoff

## L1.01  (scale 1" = 10.0', MAPE 0.0%)

| Code | Name | Ground truth | Measured | Error % | Flag |
|------|------|-------------:|---------:|--------:|------|
| M.5 | Concrete Paver A | 6,550.15 | 6,550.11 | -0.0% |  |
| M.6 | Concrete Paver B | 1,760.05 | 1,760.03 | -0.0% |  |
| M.7 | Tile Paver A | 191.42 | 191.41 | -0.0% |  |
| M.15 | River Rock | 368.57 | 368.55 | -0.0% |  |
| M.16 | Beach Pebble | 413.50 | 413.51 | +0.0% |  |

_Raster color-seg baseline: 605% MAPE -> vector polygons: 0.0% MAPE._

## Method

- Calibration: pages are true paper size; scale read per sheet (Gemini OCR of
  the title block) -> `pixels_per_foot = DPI / feet_per_inch`.
- Detection: exact PDF **vector fill polygons**; each material's true fill color
  is resolved from its legend swatch rectangle. Repeated identical small square
  fills (symbol/keynote markers) are excluded.
- Shared fill colors (two materials, one color) are split by Gemini region
  classification.
- Ground truth = each legend row's printed QTO value (Section 4 for L1.01).

## Run log

```

========== PAGE 0 ==========
[s2] Extracting page 0 at 200 DPI ...
     rendered 9598x7200 px; 115 words; 278 vector paths
[s4] Calibrating scale ...
     Scale read by Gemini: 1" = 10'  (sheet L1.01)
     render 200 DPI -> pixels_per_foot = 20.00
     Page size 48.0x36.0 in (true-paper assumption OK)
     Scale-bar cross-check: tick labels not found
[s3] Reading legend ...
     Legend deterministic: 5 area items (M.5, M.6, M.7, M.15, M.16)
       M.5 Concrete Paver A: swatch (255, 236, 180), vector fill (255, 193, 7), legend=6550.15
       M.6 Concrete Paver B: swatch (187, 223, 251), vector fill (33, 150, 243), legend=1760.05
       M.7 Tile Paver A: swatch (178, 178, 178), vector fill (0, 0, 0), legend=191.42
       M.15 River Rock: swatch (179, 228, 251), vector fill (3, 169, 244), legend=368.57
       M.16 Beach Pebble: swatch (255, 178, 243), vector fill (255, 0, 219), legend=413.5
     Legend Gemini cross-check: read 19 rows
       ok M.5 'Concrete Paver A' [area_sqft]
       ok M.6 'Concrete Paver B' [area_sqft]
       ok M.7 'Tile Paver A' [area_sqft]
       ok M.15 'River Rock' [area_sqft]
       ok M.16 'Beach Pebble' [area_sqft]
[s5a] Raster color-segmentation sweep (baseline) ...
      best raster baseline MAPE 605.0% (color can't split M.6/M.15; M.7 vs linework)
[s5b] Vector polygon detection (markers removed) ...
      M.5 Concrete Paver A   fill=(255, 193, 7) regions=15 mk=14 -> 6550.1 sqft
      M.6 Concrete Paver B   fill=(33, 150, 243) regions=10 mk=0 -> 1760.0 sqft
      M.7 Tile Paver A       fill=(0, 0, 0) regions=5 mk=4 -> 191.4 sqft
      M.15 River Rock         fill=(3, 169, 244) regions=6 mk=20 -> 368.6 sqft
      M.16 Beach Pebble       fill=(255, 0, 219) regions=13 mk=0 -> 413.5 sqft
[s6] Gemini validation / shared-color split ...
       M.5 Concrete Paver A [AGREE]: Yes. The herringbone pattern is a common representation for pavers.
       M.6 Concrete Paver B [AGREE]: Yes, the blue cross-hatched pattern matches the legend.
       M.7 Tile Paver A [AGREE]: Yes, the grid pattern strongly suggests individual paver units.
       M.15 River Rock [AGREE]: Yes. Pattern shows rounded aggregate, consistent with river rock.
       M.16 Beach Pebble [REVIEW]: No. The uniform pink fill lacks pebble texture.
[s8] Building rows + comparison ...
[s7] Annotating sheet ...
     wrote C:\construction_poc\outdoor-elements\outputs\annotated_L1_01.png
     wrote outputs/output_L1_01.json/.csv and report_L1_01.json
```