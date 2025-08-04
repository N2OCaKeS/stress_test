# app/celery_app.py
from celery import Celery
from utils.config import settings

celery_app = Celery(
    "my_project",
    broker=settings.BROKER_URL,
    backend=settings.BACKEND_URL
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='Europe/Moscow',
    enable_utc=True,
)
