from app.celery_app import celery_app


@celery_app.task
def create():
    pass

@celery_app.task
def delete():
    pass

@celery_app.task
def revert():
    pass