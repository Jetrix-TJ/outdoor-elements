"""One-shot migration: import existing filesystem JSON jobs into Postgres.

Reads each jobs/<job_id>/ folder's old JSON files (status.json, config.json,
prices.json, stage2/p<N>.json) and upserts them via the now-Postgres-backed
store. Binaries (PDF, thumbs, overlays, masks) stay on disk. Idempotent — the
store write_* calls upsert, so re-running is safe.

    python -m backend.migrate_jobs
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import db, store

_STAGE2 = re.compile(r"^p(\d+)\.json$")


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def migrate() -> dict:
    db.init_db()
    counts = {"jobs": 0, "status": 0, "config": 0, "prices": 0, "stage2": 0, "skipped": 0}
    for d in sorted(store.JOBS_DIR.iterdir()):
        if not d.is_dir():
            continue
        job_id = d.name
        touched = False

        status = _load(d / "status.json")
        if status is not None:
            store.write_status(job_id, status); counts["status"] += 1; touched = True

        config = _load(d / "config.json")
        if config is not None:
            store.write_config(job_id, config); counts["config"] += 1; touched = True

        prices = _load(d / "prices.json")
        if prices is not None:
            store.write_prices(job_id, prices); counts["prices"] += 1; touched = True

        s2dir = d / "stage2"
        if s2dir.is_dir():
            for f in sorted(s2dir.glob("p*.json")):
                m = _STAGE2.match(f.name)
                data = _load(f)
                if m and data is not None:
                    store.write_stage2(job_id, int(m.group(1)), data)
                    counts["stage2"] += 1; touched = True

        if touched:
            counts["jobs"] += 1
        else:
            counts["skipped"] += 1
    return counts


if __name__ == "__main__":
    c = migrate()
    print("Migration complete:")
    for k, v in c.items():
        print(f"  {k:8}: {v}")
