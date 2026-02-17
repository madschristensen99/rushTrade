from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "trading_terminal",
    broker=settings.celery_broker,
    backend=settings.celery_backend,
    include=[
        "app.tasks.trading_tasks",
        "app.tasks.notification_tasks",
    ]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,  # 30 minutes max
    task_soft_time_limit=25 * 60,  # 25 minutes soft limit
    worker_prefetch_multiplier=1,  # One task at a time for trading
    worker_max_tasks_per_child=1000,
    broker_connection_retry_on_startup=True,
)

# Task routing
celery_app.conf.task_routes = {
    "app.tasks.order_tasks.*": {"queue": "orders"},
    "app.tasks.notification_tasks.*": {"queue": "notifications"},
}

# Result expiration
celery_app.conf.result_expires = 3600  # 1 hour