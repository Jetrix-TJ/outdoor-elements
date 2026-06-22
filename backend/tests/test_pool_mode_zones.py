"""Test that detect_pool returns zone geometry and uses shared renderer."""
import fitz


def _make_pool_pdf(out_path: str, pool_rect, spa_rect, page_size=(612, 792)):
    """Create a minimal PDF with two thick-bordered rectangles (pool + spa)."""
    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    # thick black border for pool
    page.draw_rect(fitz.Rect(*pool_rect), color=(0, 0, 0), width=3, fill=None)
    # thick black border for spa
    page.draw_rect(fitz.Rect(*spa_rect), color=(0, 0, 0), width=3, fill=None)
    doc.save(out_path)
    doc.close()


def test_detect_pool_returns_zones(tmp_path):
    from backend import pool_mode
    pdf = str(tmp_path / "pool.pdf")
    out_png = str(tmp_path / "overlay.png")
    # pool ~200x150 pts at 150dpi; spa ~80x60 pts
    _make_pool_pdf(pdf,
                   pool_rect=(50, 50, 250, 200),
                   spa_rect=(300, 50, 380, 110))
    targets = {"POOL": 1.0, "SPA": 0.1}  # rough SF — just need match
    result = pool_mode.detect_pool(pdf, 0, targets, out_png, dpi=72)
    assert "zones" in result
    zones = result["zones"]
    assert len(zones) >= 1
    for z in zones:
        assert "id" in z
        assert "code" in z
        assert "hex" in z and z["hex"].startswith("#")
        assert "geometry" in z and len(z["geometry"]) > 0
        assert "area_sqft" in z
        assert "source" in z and z["source"] == "pool"
        assert "perimeter_lf" in z
        assert "bbox" in z and len(z["bbox"]) == 4
        assert z["status"] == "active"


def test_detect_pool_overlay_exists(tmp_path):
    from backend import pool_mode
    pdf = str(tmp_path / "pool.pdf")
    out_png = tmp_path / "overlay_p0.png"
    _make_pool_pdf(pdf, pool_rect=(50, 50, 250, 200), spa_rect=(300, 50, 380, 110))
    pool_mode.detect_pool(pdf, 0, {"POOL": 1.0}, str(out_png), dpi=72)
    assert out_png.exists()


def test_detect_pool_zone_geometry_in_pdf_points(tmp_path):
    from backend import pool_mode
    pdf = str(tmp_path / "pool.pdf")
    out_png = str(tmp_path / "overlay.png")
    _make_pool_pdf(pdf, pool_rect=(50, 50, 250, 200), spa_rect=(300, 50, 380, 110))
    result = pool_mode.detect_pool(pdf, 0, {"POOL": 1.0}, out_png, dpi=72)
    for z in result["zones"]:
        for poly in z["geometry"]:
            for pt in poly:
                # points should be in PDF-point range (page is 612x792 pts)
                assert 0 <= pt[0] <= 612, f"x out of range: {pt[0]}"
                assert 0 <= pt[1] <= 792, f"y out of range: {pt[1]}"
