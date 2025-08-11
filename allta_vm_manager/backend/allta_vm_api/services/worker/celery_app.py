# app/celery_app.py
from celery import Celery
from utils.config import settings



celery_app = Celery("allta", broker=settings.BROKER_URL, backend=settings.RESULT_BACKEND)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,                 # подтверждать после выполнения
    worker_prefetch_multiplier=int(settings.CELERY_PREFETCH),
    broker_transport_options={
        "visibility_timeout": 6 * 3600   # 6h — чтобы невзятые/упавшие задачи возвращались в очередь
    },
)