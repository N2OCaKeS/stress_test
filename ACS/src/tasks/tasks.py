from celery import Celery

celery = Celery("tasks", broker="redis://redis:5370", backend="redis://redis:5370")

celery.autodiscover_tasks(['src.clonezilla_snap.clonezilla_func', 'src.add_tuning.temp'])
