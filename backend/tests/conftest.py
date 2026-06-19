"""Test setup: point the store at an isolated throwaway SQLite DB so unit tests
don't need (or touch) the real Postgres. Must run before backend.db is imported,
which pytest guarantees by importing conftest first."""
import os
import tempfile

_fd, _path = tempfile.mkstemp(suffix=".db")
os.close(_fd)
os.environ["DATABASE_URL"] = "sqlite:///" + _path.replace("\\", "/")
