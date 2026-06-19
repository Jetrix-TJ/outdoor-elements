"""Postgres-backed structured storage (SQLAlchemy).

Holds the per-job and per-stage *data* (job status + page selection, Gemini
config, Stage-2 results, pricing) as JSONB. Large binaries (the uploaded PDF,
overlay PNGs, thumbnails, edit mask .npz) stay on the filesystem under jobs/,
referenced by job_id — Postgres is not a blob store.

Connection comes from DATABASE_URL (see docker-compose.yml):
    postgresql+psycopg2://oe:oe@localhost:5432/outdoor_elements
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import (DateTime, Float, ForeignKey, Index, Integer, String,
                        create_engine, func)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg2://oe:oe@localhost:5433/outdoor_elements")

# JSONB on Postgres, plain JSON on any other backend (e.g. SQLite in tests).
_Json = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[dict | None] = mapped_column(_Json, nullable=True)   # status.json
    config: Mapped[dict | None] = mapped_column(_Json, nullable=True)   # config.json
    prices: Mapped[dict | None] = mapped_column(_Json, nullable=True)   # prices.json
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(),
                               onupdate=func.now())


class Stage2Result(Base):
    __tablename__ = "stage2_results"
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("jobs.job_id"),
                                        primary_key=True)
    page: Mapped[int] = mapped_column(Integer, primary_key=True)
    data: Mapped[dict | None] = mapped_column(_Json, nullable=True)


class Zone(Base):
    """One detected connected region, individually addressable by `id`.

    Geometry (the contour polygon, in base-page PDF points) is the source of
    truth: the overlay and per-code totals are rebuilt from the active zones, so
    delete-by-id just flips `status`.
    """
    __tablename__ = "zones"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("jobs.job_id"),
                                        nullable=False)
    page: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String, nullable=False)
    hex: Mapped[str | None] = mapped_column(String(7), nullable=True)
    area_sqft: Mapped[float | None] = mapped_column(Float, nullable=True)
    perimeter_lf: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry: Mapped[list | None] = mapped_column(_Json, nullable=True)  # polygons in PDF pts
    bbox: Mapped[list | None] = mapped_column(_Json, nullable=True)      # fractional [x0,y0,x1,y1]
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="active")
    created_at = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at = mapped_column(DateTime(timezone=True), server_default=func.now(),
                               onupdate=func.now())

    __table_args__ = (Index("ix_zones_job_page_status", "job_id", "page", "status"),)


engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

_ready = False


def init_db() -> None:
    """Create tables if missing (idempotent)."""
    global _ready
    Base.metadata.create_all(engine)
    _ready = True


@contextmanager
def session():
    """Transactional session; ensures tables exist on first use."""
    if not _ready:
        init_db()
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
