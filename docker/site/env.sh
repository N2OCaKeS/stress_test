#!/bin/bash

# Настройки Flask
export FLASK_APP=web_app
export FLASK_ENV=production
export FLASK_DEBUG=False

# Пути и конфиги
export CPATH="/home/u/git/stress_test/docker/site/"
export NGINX_DC="docker-compose.nginx.yml"
export LOCUST_CONF="${CPATH}Kuznechik/.locust.conf"
export VENV="/home/u/python/Python-3.12.1/venv/bin/"

# Docker и Gunicorn
export APP_CONTAINERS=("flask" "nginx")
export LOAD_DOCKER_CONTAINERS=("master" "site_worker_1" "site_worker_2" "site_worker_3" "site_worker_4" "site_worker_5")
export WORKER=5

# Динамически вычисляемые значения (через команды)
export NGINX_V=$(nginx -v 2>&1 | cut -d '/' -f2 | tr -d '[:space:]')
export SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
export SYS_KERNEL=$(uname -r | tr -d '[:space:]')
export PACKAGE_VERSIONS="docker_$(docker --version | awk '{print $3}' | sed 's/,//'), docker-compose_$(docker-compose --version | awk '{print $4}' | sed 's/,//')"
RESULTS_DIR="${CPATH}results/docker_web_${SYS_VERSION}_${SYS_KERNEL}"

