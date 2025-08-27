import os
from celery import Celery
from celery.signals import worker_ready
from utils.config import settings
REDIS_URL = settings.BROKER_URL

app = Celery(
    "vm_api",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["bridge", "tasks.server", "tasks.vm", "tasks.snapshot"],
)

app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    worker_send_task_events=True,
    task_send_sent_event=True,    
)

@worker_ready.connect
def _start_bridge(**_):
    from bridge import consume_loop
    consume_loop()