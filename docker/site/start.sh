#!/bin/bash

localhost="localhost"
WORKER=5
NGINX_DC="docker-compose.nginx.yml"
LOAD_DOCKER_CONTAINERS=("master" "site_worker_1" "site_worker_2" "site_worker_3" "site_worker_4" "site_worker_5")
APP_CONTAINERS=("flask" "nginx")
CPATH="/home/u/git/stress_test/docker/site/"
VENV="/home/u/python/Python-3.12.1/venv/bin/"
NGINX_V=$(nginx -v 2>&1 | cut -d '/' -f2 | tr -d '[:space:]')

SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
SYS_KERNEL=$(uname -r | tr -d '[:space:]')
DOCKER_VERSION=$(dpkg -l | grep -E '^ii[[:space:]]+docker.io[[:space:]]' | awk '{print $3}' | sed 's/,//')
COMPOSE_VERSION=$(dpkg -l | grep docker-compose | awk '{print $3}' | sed 's/,//')
PACKAGE_VERSIONS="docker_${DOCKER_VERSION}, docker-compose_${COMPOSE_VERSION}"

RESULTS_DIR="./results/docker_web_${SYS_VERSION}_${SYS_KERNEL}"
mkdir -p "$RESULTS_DIR"
LOCUST_CONF="${CPATH}Kuznechik/.locust.conf"
# ИНФО
sudo echo -e "${SYS_VERSION}\n${SYS_KERNEL}\n${PACKAGE_VERSIONS}" > "${CPATH}results/INFO.txt"

check_locust_containers() {
    echo "Ожидание 10 секунд перед проверкой контейнеров..."
    sleep 10

    while true; do
        all_running=true

        for name in "${LOAD_DOCKER_CONTAINERS[@]}"; do
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
    done
}


wait_for_lines_or_stable() {
    local file="$1"
    local min_lines="$2"
    local timeout="${3:-120}"
    local stable_after="${4:-10}"

    echo "Ожидание записи файла: $file (до $timeout сек или $min_lines строк)"

    local start_time=$(date +%s)
    local last_mod_time=0
    local stable_seconds=0

    while true; do
        [ -f "$file" ] || { sleep 1; continue; }

        current_lines=$(wc -l < "$file")
        current_mod_time=$(stat -c %Y "$file")

        if (( current_lines >= min_lines )); then
            if (( current_mod_time == last_mod_time )); then
                ((stable_seconds++))
            else
                stable_seconds=0
                last_mod_time=$current_mod_time
            fi

            if (( stable_seconds >= stable_after )); then
                echo "Файл стабилен и содержит $current_lines строк."
                break
            fi
        fi

        now=$(date +%s)
        if (( now - start_time >= timeout )); then
            echo "⚠ Время ожидания истекло ($timeout сек)."
            break
        fi
        sleep 1
    done
}


nginx_server() {
    RESULTS_DIR_SERVER="${RESULTS_DIR}/nginx_server"
    mkdir -p "$RESULTS_DIR_SERVER"

    # Получаем версию nginx
    sed -i "s|image: nginx.*|image: nginx:${NGINX_V}|" "${CPATH}docker-compose.nginx.yml"

    echo "Остановка всех сервисов (Nginx, Docker)"
    sudo systemctl stop nginx.service
    sudo docker-compose -f ${CPATH}${NGINX_DC} down
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
    RESULTS_DIR_DOCKER="${RESULTS_DIR}/nginx_docker"
    mkdir -p "$RESULTS_DIR_DOCKER"

    sed -i "s|image: nginx.*|image: nginx:${NGINX_V}|" "${CPATH}${NGINX_DC}"

    echo "Остановка старого окружения..."
    sudo systemctl stop nginx.service
    sudo docker-compose -f ${CPATH}${NGINX_DC} down
    sudo docker volume prune -f
    sudo pkill -f "gunicorn" || true
    sudo pkill -f "locust" || true
    sleep 2

    echo "Настройка конфигурации для docker..."
    sudo sed -i 's|proxy_set_header Host .*;|proxy_set_header Host $host;|' ${CPATH}nginx-config/web-app.conf
    sudo sed -i 's|http://127.0.0.1|http://flask|g' ${CPATH}nginx-config/web-app.conf
    sudo sed -i "s|alias ${CPATH}web_app/static/;|alias /app/web_app/static/;|" "${CPATH}nginx-config/web-app.conf"

    sed -i "s|^csv = .*|csv = ${RESULTS_DIR_DOCKER}/results|g" "$LOCUST_CONF"
    sed -i "s|^html = .*|html = ${RESULTS_DIR_DOCKER}/results.html|g" "$LOCUST_CONF"
    sed -i "s|^host = .*|host = http://nginx|g" "$LOCUST_CONF"

    echo "Запуск Docker-сервисов..."
    sudo docker-compose -f ${CPATH}${NGINX_DC} up -d --build --scale worker=${WORKER}
    check_locust_containers
    echo "Docker Nginx запущен!"

    echo "Ожидание завершения работы Locust master..."
    while docker ps | grep -q "master"; do
        sleep 5
    done
    echo "Контейнер master завершил работу."

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
#nd - nginx в контейнерах
    nd)
        nginx_docker
        ;;
#ns - nginx в сервисах
    ns)
        bash prepare.sh
	    nginx_server
	    ;;
    final)
	    bash ${CPATH}prepare.sh
	    nginx_server
	    nginx_docker
	    echo "Тест выполнился"
            source ${VENV}activate && python3 new_report.py	
	    ;;
esac

