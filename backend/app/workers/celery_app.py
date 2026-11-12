"""Celery application and queue routing for fast, heavy, planetx, and ingest tasks."""

from __future__ import annotations

import os
from typing import Any

from celery import Celery


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes"}


celery_app = Celery(
    "sphx",
    broker=os.environ.get("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=os.environ.get("CELERY_RESULT_BACKEND", "redis://localhost:6379/1"),
    include=["app.workers.tasks_fast", "app.workers.tasks_heavy"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_always_eager=_env_flag("CELERY_TASK_ALWAYS_EAGER"),
    task_routes={
        "app.workers.tasks_fast.*": {"queue": "fast"},
        "app.workers.tasks_heavy.*": {"queue": "heavy"},
        "app.workers.tasks_planetx.*": {"queue": "planetx"},
        "app.workers.tasks_ingest.*": {"queue": "ingest"},
    },
)


def is_eager_mode() -> bool:
    """Return True when Celery is configured for synchronous eager execution."""
    return bool(celery_app.conf.task_always_eager or _env_flag("CELERY_TASK_ALWAYS_EAGER"))


def dispatch_task(task: Any, *args: Any, queue: str = "fast", **kwargs: Any) -> Any:
    """Execute Celery task eagerly when configured, otherwise enqueue onto `queue`."""
    if is_eager_mode():
        return task.apply(args=args, kwargs=kwargs)
    return task.apply_async(args=args, kwargs=kwargs, queue=queue)
