#!/bin/bash

set -vx

WORKER=5
NGINX_DC="docker-compose.nginx.yml"
LOAD_DOCKER_CONTAINERS=("master" "site_worker_1" "site_worker_2" "site_worker_3" "site_worker_4" "site_worker_5")
APP_CONTAINERS=("flask" "nginx")
ALL_CONTAINERS=("${LOAD_DOCKER_CONTAINERS[@]}" "${APP_CONTAINERS[@]}")
CPATH="/home/u/git/stress_test/docker/site/"
LPATH="/home/u/git/stress_test/docker/libs/"
VENV="/home/u/python/Python-3.12.1/venv/bin/"

NGINX_V=$(nginx -v 2>&1 | cut -d '/' -f2 | tr -d '[:space:]')
SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
SYS_KERNEL=$(uname -r | tr -d '[:space:]')
DOCKER_VERSION=$(dpkg -l | grep -E '^ii[[:space:]]+docker.io[[:space:]]' | awk '{print $3}' | sed 's/,//')
COMPOSE_VERSION=$(dpkg -l | grep docker-compose-v2 | awk '{print $3}' | sed 's/,//')
PACKAGE_VERSIONS="docker_${DOCKER_VERSION}, docker-compose-v2_${COMPOSE_VERSION}"

RESULTS_DIR="./results/docker_web_${SYS_VERSION}_${SYS_KERNEL}"
mkdir -p "$RESULTS_DIR"
LOCUST_CONF="${CPATH}Kuznechik/.locust.conf"
# ИНФО
sudo echo -e "${SYS_VERSION}\n${SYS_KERNEL}\n${PACKAGE_VERSIONS}" > "${CPATH}results/INFO.txt"

# Для теста nginx_docker docker
check_locust_containers() {
    local mode="$1"
    local containers_to_check=("${@:2}")



    echo "Ожидание 10 секунд перед проверкой контейнеров..."
    sleep 10

    TIMEOUT=90
    START_TIME=$(date +%s)

    while true; do
        all_running=true

	for name in "${containers_to_check[@]}"; do
            status=$(docker inspect --format='{{.State.Status}}' "$name" 2>/dev/null)

            if [ "$status" != "running" ]; then
                echo "Контейнер '$name' не работает (статус: $status). Перезапускаем..."
                docker restart "$name"
                all_running=false
                sleep 10
            else
                echo "Контейнер '$name' работает."
            fi
        done

        if $all_running; then
            echo "Все контейнеры работают."
            break
        fi

        CURRENT_TIME=$(date +%s)
        ELAPSED_TIME=$((CURRENT_TIME - START_TIME))
        if [ $ELAPSED_TIME -ge $TIMEOUT ]; then
            echo "Время ожидания истекло. Прерывание цикла."
            if [[ "$mode" == "docker" ]]; then
	            echo "1. FULL-DOCKER_ERROR, nginx_docker MODE=locust" && exit 3
	        elif [[ "$mode" == "locust" ]]; then
	            echo "2. HALF-DOCKER_ERROR, nginx_docker MODE=docker" && exit 2
	        fi
        fi
    done
}

# Все без контейнеров
nginx_server() {
    RESULTS_DIR_SERVER="${RESULTS_DIR}/nginx_server"
    mkdir -p "$RESULTS_DIR_SERVER"

    # Получаем версию nginx
    sed -i "s|image: nginx.*|image: nginx:${NGINX_V}|" "${CPATH}docker-compose.nginx.yml"

    echo "Остановка всех сервисов (Nginx, Docker)"
    sudo systemctl stop nginx.service
    sudo docker compose -f ${CPATH}${NGINX_DC} down
    sudo docker volume prune -f
    echo "Настройка Nginx"
    sudo rm -f /etc/nginx/conf.d/*
    [ -f "/etc/nginx/sites-enabled/default" ] && sudo rm /etc/nginx/sites-enabled/default


    # Настройка локального прокси
    sudo sed -i 's|proxy_set_header Host .*;|proxy_set_header Host localhost;|' ${CPATH}nginx-config/web-app.conf
    sudo sed -i 's|http://flask|http://127.0.0.1|g' ${CPATH}nginx-config/web-app.conf
    sudo sed -i "s|alias .*web_app/static/;|alias ${CPATH}web_app/static/;|" "${CPATH}nginx-config/web-app.conf"

    if [ -f "${CPATH}nginx-config/web-app.conf" ]; then
        sudo cp ${CPATH}/nginx-config/web-app.conf /etc/nginx/conf.d/
        sudo chown www-data:www-data /etc/nginx/conf.d/web-app.conf
        sudo chmod 644 /etc/nginx/conf.d/web-app.conf
    fi

    echo "Проверка конфигурации Nginx"
    sudo nginx -t && sudo systemctl restart nginx

    echo "Остановка старых процессов Gunicorn и Locust"
    sudo pkill -f "gunicorn" || true
    sudo pkill -f "locust" || true
    sleep 3

    echo "Запуск Gunicorn в фоне"
    sudo nohup ${VENV}gunicorn -w 4 -b 127.0.0.1:8000 --chdir ${CPATH}web_app --log-level=debug wsgi:application > /tmp/gunicorn.log 2>&1 &
    sleep 3

    echo "Обновляем конфиг Locust под localhost..."
    sed -i "s|^csv = .*|csv = ${RESULTS_DIR_SERVER}/results|g" "$LOCUST_CONF"
    sed -i "s|^html = .*|html = ${RESULTS_DIR_SERVER}/results.html|g" "$LOCUST_CONF"
    sed -i "s|^host = .*|host = http://127.0.0.1|g" "$LOCUST_CONF"

    echo "Запуск Locust и ожидание завершения"
    sudo ${VENV}locust -f ${CPATH}Kuznechik/locustfile.py \
        --config "$LOCUST_CONF" --processes ${WORKER} --headless --run-time 2m > /tmp/locust.log 2>&1

    echo "Завершено: NGINX server"
}


nginx_docker(){
    MODE="$1"  # Режим работы: "docker" или "locust"

    RESULTS_DIR_DOCKER="${RESULTS_DIR}/nginx_docker"
    RESULTS_DIR_DOCKER_LOCUST_PROC="${RESULTS_DIR}/locust_proc"
    mkdir -p "$RESULTS_DIR_DOCKER"
    mkdir -p "$RESULTS_DIR_DOCKER_LOCUST_PROC"

    # Обновляем версию nginx в docker-compose
    sed -i "s|image: nginx.*|image: nginx:${NGINX_V}|" "${CPATH}${NGINX_DC}"

    echo "Остановка старого окружения..."
    sudo systemctl stop nginx.service
    sudo docker compose -f "${CPATH}${NGINX_DC}" down
    sudo docker volume prune -f
    sudo pkill -f "gunicorn" || true
    sudo pkill -f "locust" || true
    sudo pkill -f "nginx" || true
    sleep 2

    echo "Настройка конфигурации для docker..."
    sudo sed -i 's|proxy_set_header Host .*;|proxy_set_header Host $host;|' "${CPATH}nginx-config/web-app.conf"
    sudo sed -i 's|http://127.0.0.1|http://flask|g' "${CPATH}nginx-config/web-app.conf"
    sudo sed -i "s|alias ${CPATH}web_app/static/;|alias /app/web_app/static/;|" "${CPATH}nginx-config/web-app.conf"

    echo "Запуск Docker-сервисов..."
    # Ветвление по режиму:
    # Все в контейнерах
    if [[ "$MODE" == "docker" ]]; then
	sed -i "s|^csv = .*|csv = ${RESULTS_DIR_DOCKER}/results|g" "$LOCUST_CONF"
        sed -i "s|^html = .*|html = ${RESULTS_DIR_DOCKER}/results.html|g" "$LOCUST_CONF"
        sed -i "s|^host = .*|host = http://nginx|g" "$LOCUST_CONF"
        # Стандартный docker-режим: поднимаем контейнеры через docker-compose и ждём завершения работы контейнера master
        sudo docker compose -f ${CPATH}${NGINX_DC} up -d --build --scale worker=${WORKER}
        check_locust_containers "docker" "${ALL_CONTAINERS[@]}"
        echo "Docker Nginx запущен!"

        echo "Ожидание завершения работы Locust master..."
        while docker ps | grep -q master; do
            sleep 5
        done
        echo "Контейнер master завершил работу."

    # В контейнерах только приложение
    elif [[ "$MODE" == "locust" ]]; then
	sed -i "s|^csv = .*|csv = ${RESULTS_DIR_DOCKER_LOCUST_PROC}/results|g" "$LOCUST_CONF"
    	sed -i "s|^html = .*|html = ${RESULTS_DIR_DOCKER_LOCUST_PROC}/results.html|g" "$LOCUST_CONF"
    	sed -i "s|^host = .*|host = http://172.24.0.2|g" "$LOCUST_CONF"
        # Альтернативный режим: поднимаем только контейнеры Flask и Nginx,
        
	# затем запускаем Locust в headless-режиме через виртуальное окружение
        sudo docker compose -f ${CPATH}${NGINX_DC} up -d --build flask nginx
	check_locust_containers "locust" "${APP_CONTAINERS[@]}"
	echo "Контейнеры Flask и Nginx запущены (локальный режим)"
        
        echo "Запуск Locust и ожидание завершения..."
        sudo ${VENV}locust -f ${CPATH}Kuznechik/locustfile.py \
            --config "$LOCUST_CONF" --processes ${WORKER} --headless --run-time 2m > /tmp/locust.log 2>&1
        echo "Locust завершил выполнение."

    else
        echo "Неизвестный режим запуска: $MODE"
        return 1
    fi

    # Сброс версии nginx в файле docker-compose после запуска, если требуется
    sed -i "s|image: nginx.*|image: nginx|" "${CPATH}docker-compose.nginx.yml"
    echo "Завершено: NGINX docker"
}


close_and_delete(){
    docker stop $(docker ps -q)
    docker container prune -f
    echo Контейнеры остановлены и удалены
}


delete_all_img(){
    docker system prune -a -f
}


case $1 in
    delete)
        delete_all_img
        ;;
    close)
        close_and_delete
        ;;
    nd)
        nginx_docker docker
        ;;
    nl)
        nginx_docker locust
        ;;
    ns)
	    nginx_server
	    ;;
    final)
	    nginx_server
	    nginx_docker docker
	    nginx_docker locust
	    echo "Тест выполнился"
	    close_and_delete
	    ;;
esac

