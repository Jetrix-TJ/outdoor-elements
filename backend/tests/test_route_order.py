"""Route-order regression: the literal POST /stage2/all must be registered
before the parameterised POST /stage2/{page}. FastAPI matches in declaration
order, so the wrong order makes "all" hit {page} and fail int parsing (422)."""
from backend.main import app


def _post_index(path: str) -> int:
    """Index of the first APIRoute that serves POST on `path`."""
    for i, r in enumerate(app.routes):
        if getattr(r, "path", None) == path and "POST" in getattr(r, "methods", set()):
            return i
    raise AssertionError(f"no POST route for {path}")


def test_stage2_all_registered_before_stage2_page():
    all_idx = _post_index("/api/jobs/{job_id}/stage2/all")
    page_idx = _post_index("/api/jobs/{job_id}/stage2/{page}")
    assert all_idx < page_idx, (
        "POST /stage2/all must come before POST /stage2/{page} or 'all' is "
        "captured by {page} and 422s on int parsing"
    )
