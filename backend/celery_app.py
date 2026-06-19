"""Celery app for the staged takeoff pipeline.

Redis is the broker/result backend. If Redis is unreachable at startup, we flip
on `task_always_eager` so tasks run inline in the API process — Stage 1 stays
fully demoable with no Redis installed. Start Redis + a worker and it switches to
real distributed execution with zero code change.

    # real workers (once Redis is running):
    celery -A backend.celery_app worker --loglevel=info --pool=solo
"""
from __future__ import annotations

import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def _redis_alive(url: str) -> bool:
    try:
        import redis  # redis-py
        redis.Redis.from_url(url, socket_connect_timeout=0.5).ping()
        return True
    except Exception:
        return False


EAGER = not _redis_alive(REDIS_URL)

celery_app = Celery("oe_takeoff", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_always_eager=EAGER,        # inline execution when Redis is down
    task_eager_propagates=True,
    worker_pool="solo",             # Windows-friendly
)

# Make this the default app so @shared_task tasks bind to THIS broker (Redis),
# not Celery's built-in default (which assumes an amqp:// / RabbitMQ broker).
celery_app.set_default()

# Ensure task modules are registered with the app.
celery_app.autodiscover_tasks(["backend"])
import backend.tasks  # noqa: E402,F401  (registers stage1_select)
