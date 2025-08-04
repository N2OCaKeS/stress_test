from app.celery_app import celery_app
import time

@celery_app.task
def create():
    pass

@celery_app.task
def delete():
    pass

@celery_app.task
def update():
    pass

@celery_app.task
def start():
    pass

@celery_app.task
def stop():
    pass

