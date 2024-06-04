#!/bin/bash

# cd src

if [[ "${1}" == "celery" ]]; then
#   celery --app=src.tasks.tasks:celery worker -l INFO/
    sleep 30
    apt-get update && apt-get install sshpass -y
    sleep 30
    celery --app=src.clonezilla_snap.clonezilla_func:celery worker -l INFO
elif [[ "${1}" == "flower" ]]; then
#   celery --app=src.tasks.tasks:celery flower
    sleep 30
    celery --app=src.clonezilla_snap.clonezilla_func:celery flower
fi