"""Tests for the ported Gemini auto-config builder."""
import os
from pathlib import Path

from dotenv import load_dotenv

from backend import gemini_config

load_dotenv(Path(__file__).resolve().parents[1].parent / ".env")
PDF = r"C:\Users\absar\Downloads\2811 Kirby\2811 Kirby\2811 Kirby - LANDSCAPE.pdf"


def test_find_key_pages_picks_material_plans():
    key = gemini_config.find_key_pages(PDF)
    idxs = [p["page_idx"] for p in key["plans"]]
    assert 2 in idxs  # L1.01 is page index 2


def test_build_config_returns_sheets_and_materials():
    cfg = gemini_config.build_config(PDF, os.environ["GEMINI_API_KEY"])
    assert cfg["sheets"], cfg
    assert any(k.startswith("M") for k in cfg.get("materials", {}))
    for k in ("tag_pattern", "plan_clip_right_pct", "phase2_radius_ft"):
        assert k in cfg
