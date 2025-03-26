#!/bin/bash

localhost="localhost"
NGINX_DC="docker-compose.nginx.yml"
LOAD_DOCKER_CONTAINERS=("master" "site_worker_1" "site_worker_2" "site_worker_3")
APP_CONTAINERS=("postgres" "pgbouncer" "redis" "flask" "nginx")
CPATH="/home/u/git/stress_test/docker/site/"
VENV="/home/u/python/Python-3.12.1/venv/bin/"

# passwords for pg
md5_pass_postgres=$(echo -n "1postgres" | md5sum | awk '{print "md5"$1}')
md5_pass_u=$(echo -n "1u" | md5sum | awk '{print "md5"$1}')

# collect results base directory
SYS_VERSION=$(cat /etc/astra/build_version | tr -d '[:space:]')
SYS_KERNEL=$(uname -r | tr -d '[:space:]')
RESULTS_DIR="./results/docker_web_${SYS_VERSION}_${SYS_KERNEL}"
mkdir -p "$RESULTS_DIR"
HLOCUST_CONF="${CPATH}Kuznechik/for_host/.locust.conf"
DLOCUST_CONF="${CPATH}Kuznechik/for_docker/.locust.conf"


psql_settings(){
    sudo -u postgres psql <<EOF
ALTER USER postgres WITH ENCRYPTED PASSWORD '${md5_pass_postgres}';
CREATE DATABASE mydb;
CREATE ROLE u WITH LOGIN ENCRYPTED PASSWORD '${md5_pass_u}';
GRANT ALL PRIVILEGES ON DATABASE mydb TO u;
ALTER USER u CREATEDB;
\c mydb
GRANT ALL ON schema public TO u;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
EOF
}


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


apache2_server(){
    sudo docker-compose -f docker-compose.apache2.yml down
    sudo apt-get install apache2 apache2-utils -y   

    sudo cp ${CPATH}/pg/pg_hba.conf /etc/postgresql/*/main/pg_hba.conf

    psql_settings
    
    sudo sed -i 's#sys.path.insert(0, "/app")#sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))\nsys.path.insert(0, "/home/u/git/stress_test/docker/site")#' ${CPATH}web_app/wsgi.py
    sudo sed -i 's/^\(local\s\+all\s\+postgres\s\+\).*/\1md5/' /etc/postgresql/${PG_VERSION}/main/pg_hba.conf
    sudo sed -i 's/^\(host\s\+all\s\+all\s\+127.0.0.1\/32\s\+\).*/\1md5/' /etc/postgresql/${PG_VERSION}/main/pg_hba.conf
    sudo sed -i 's/^\(host\s\+all\s\+all\s\+::1\/128\s\+\).*/\1md5/' /etc/postgresql/${PG_VERSION}/main/pg_hba.conf
    sudo sed -i 's/^REDIS_HOST = "redis"/REDIS_HOST = "127.0.0.1"/' ${CPATH}web_app/config.py
    sudo cp pg/postgresql.conf /etc/postgresql/${PG_VERSION}/main/postgresql.conf 
    sudo echo "data_directory = '/var/lib/postgresql/15/main'" | sudo tee -a /etc/postgresql/${PG_VERSION}/main/postgresql.conf

    sudo chown -R postgres:postgres /var/lib/postgresql/
    sudo chmod -R 700 /var/lib/postgresql/
    sudo systemctl restart postgresql

    sudo cp pg/pgbouncer/pgbouncer_host/* /etc/pgbouncer/
    sudo systemctl restart pgbouncer
    sudo redis-cli flushall
    sudo systemctl start redis
    sudo rm -f /etc/apache2/sites-available/*
    sudo cp apache-config/config_host/apache2.conf /etc/apache2/apache2.conf
    sudo cp apache-config/config_host/web-app.conf /etc/apache2/sites-available/
    sudo chown www-data:www-data /etc/apache2/sites-available/web-app.conf
    sudo chmod 644 /etc/apache2/sites-available/web-app.conf
    sudo chown -R www-data:www-data /home/u/git/stress_test/docker/site/
    sudo chmod -R 755 ${CPATH}
    sudo a2enmod wsgi
    sudo a2ensite web-app
    sudo apachectl configtest
    sudo apachectl -M | grep astra
    sudo systemctl restart apache2
    sleep 5
    sudo -u postgres psql -d mydb -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"
    sudo docker-compose -f ./docker-storage/docker-compose.locust.yml up --build -d
    check_containers "docker-compose.locust.yml" "${LOAD_DOCKER_CONTAINERS[@]}"
}


apache2_docker(){
    sed -i '/sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))/ {
    N
    /sys.path.insert(0, "\/home\/u\/git\/stress_test\/docker\/site")/ {
        s#sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))\nsys.path.insert(0, "/home/u/git/stress_test/docker/site")#sys.path.insert(0, "/app")#
    }
}' /home/u/git/stress_test/docker/site/web_app/wsgi.py
    sudo systemctl stop apache2.service redis-server.service redis.service postgresql.service pgbouncer.service
    sudo apt install -y docker.io docker-compose
    sudo sed -i 's/^REDIS_HOST = "127.0.0.1"/REDIS_HOST = "redis"/' web_app/config.py
    sudo docker-compose -f ./docker-storage/docker-compose.apache2.yml up --build -d
    check_containers "docker-compose.locust.yml" "${APP_CONTAINERS[@]}"
    check_containers "docker-compose.apache2.yml" "${LOAD_DOCKER_CONTAINERS[@]}"

    # docker-compose -f docker-compose.apache2.yml logs -f --tail=100
    # --scale worker=8
}


nginx_server(){
    # Определяем директорию для результатов теста nginx_server
    RESULTS_DIR_SERVER="${RESULTS_DIR}/nginx_server"
    mkdir -p "$RESULTS_DIR_SERVER"

    # Подставляем пути в конфиге locust для сохранения результатов
    sed -i "s|^csv = .*|csv = ${RESULTS_DIR_SERVER}/results|g" "$HLOCUST_CONF"
    sed -i "s|^html = .*|html = ${RESULTS_DIR_SERVER}/results.html|g" "$HLOCUST_CONF"
    
    PG_VERSION=$(psql --version | awk '{print $3}' | cut -d'.' -f1)
    export PG_VERSION
    export PGPASSWORD="1"
    
    echo "Остановка всех сервисов (Apache, Nginx, Docker)..."
    sudo systemctl stop apache2.service nginx.service
    sudo docker-compose -f ${NGINX_DC} down
    sudo docker volume prune -f

    echo "Проверка наличия Nginx..."
    if ! command -v nginx &> /dev/null; then
        sudo apt-get install -y nginx
    fi

    echo "Копирование конфигов PostgreSQL..."
    if [ -f "${CPATH}pg/postgresql.conf" ]; then
        sudo cp ${CPATH}pg/postgresql.conf /etc/postgresql/*/main/postgresql.conf
    fi
    if [ -f "${CPATH}pg/pg_hba.conf" ]; then
        sudo cp ${CPATH}pg/pg_hba.conf /etc/postgresql/*/main/pg_hba.conf
    fi
 
    echo "Настройка PostgreSQL..."
    sudo sed -i '/data_directory/d' /etc/postgresql/*/main/postgresql.conf
    echo "data_directory = '/var/lib/postgresql/${PG_VERSION}/main'" | sudo tee -a /etc/postgresql/*/main/postgresql.conf
    sudo chmod 755 /var/lib/postgresql/
    sudo chmod 700 /var/lib/postgresql/*/main
    sudo chown -R postgres:postgres /var/lib/postgresql/
    # запуск postgresql
    if systemctl list-units --type=service | grep -q 'postgresql@'; then
        echo "Перезапуск всех кластеров PostgreSQL..."
        for cluster in $(pg_lsclusters | awk 'NR>1 {print $1"-"$2}'); do
            sudo systemctl restart postgresql@$cluster
        done
    else
        echo "Перезапуск postgresql.service (нет поддержки кластеров)..."
        sudo systemctl restart postgresql
    fi
    psql_settings

    sudo -u postgres psql -d mydb -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"

    echo "Настройка Redis и PgBouncer..."
    sudo sed -i 's/^REDIS_HOST = "redis"/REDIS_HOST = "127.0.0.1"/' ${CPATH}web_app/config.py
    sudo cp ${CPATH}pg/pgbouncer/pgbouncer_host/* /etc/pgbouncer/
    # запуск pgbouncer
    if [[ "$SYS_VERSION" == 1.8* ]]; then
        sudo systemctl restart pgbouncer.service
    else
        sudo systemctl stop pgbouncer
        sudo -u postgres nohup pgbouncer /etc/pgbouncer/pgbouncer.ini > /tmp/pgbouncer.log 2>&1 &
    fi
    sleep 2
    sudo redis-cli flushall
    sudo systemctl start redis

    echo "Настройка Nginx..."
    sudo rm -f /etc/nginx/conf.d/*
    [ -f "/etc/nginx/sites-enabled/default" ] && sudo rm /etc/nginx/sites-enabled/default

    sudo sed -i 's/proxy_set_header Host $host;/proxy_set_header Host localhost;/' ${CPATH}nginx-config/web-app.conf
    sudo sed -i 's|http://flask|http://127.0.0.1|g' ${CPATH}nginx-config/web-app.conf
    sudo sed -i "s|alias /app/web_app/static/;|alias ${CPATH}web_app/static/;|" "${CPATH}nginx-config/web-app.conf"

    if [ -f "${CPATH}nginx-config/web-app.conf" ]; then
        sudo cp ${CPATH}/nginx-config/web-app.conf /etc/nginx/conf.d/
        sudo chown www-data:www-data /etc/nginx/conf.d/web-app.conf
        sudo chmod 644 /etc/nginx/conf.d/web-app.conf
    fi

    echo "Проверка конфигурации Nginx..."
    sudo nginx -t && sudo systemctl restart nginx

    echo "Настройка базы данных..."
    sudo -u postgres psql -d mydb -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"

    echo "Выполнение миграций Flask..."
    if [ ! -d "${CPATH}migrations" ]; then
        sudo ${VENV}flask --app ${CPATH}web_app db init
    fi
    sudo ${VENV}flask --app ${CPATH}web_app db migrate || true
    sudo ${VENV}flask --app ${CPATH}web_app db upgrade || true

    echo "Остановка старых процессов Gunicorn и Locust..."
    sudo pkill -f "gunicorn" || true
    sudo pkill -f "locust" || true
    sleep 3

    echo "Запуск Gunicorn в фоне..."
    sudo nohup ${VENV}gunicorn -w 4 -b 127.0.0.1:8000 --chdir ${CPATH}web_app --log-level=debug wsgi:application > /tmp/gunicorn.log 2>&1 &
    sleep 3

    echo "Запуск Locust в фоне..."
    sudo nohup ${VENV}locust -f ${CPATH}Kuznechik/for_host/locustfile.py \
        --config "$HLOCUST_CONF" --processes 3 > /tmp/locust.log 2>&1 &

    echo "Настройка завершена, сервер запущен!"
    sleep 2
    # Откат конфигурации
    sed -i "s|^csv = .*|csv = /app/Kuznechik/for_host/results/results|g" "$HLOCUST_CONF"
    sed -i "s|^html = .*|html = /app/Kuznechik/for_host/results/results.html|g" "$HLOCUST_CONF"
}


nginx_docker(){
    # Определяем директорию для результатов теста nginx_docker
    RESULTS_DIR_DOCKER="${RESULTS_DIR}/nginx_docker"
    mkdir -p "$RESULTS_DIR_DOCKER"

    sudo systemctl stop apache2.service redis-server.service redis.service postgresql.service pgbouncer.service nginx.service
    sudo docker-compose -f ${NGINX_DC} down 
    sudo docker volume prune -f
    echo "Остановка старых процессов Gunicorn и Locust..."
    sudo pkill -f "gunicorn" || true
    sudo pkill -f "locust" || true
    sudo pkill -f "pgbouncer" || true
    sleep 2

    sudo sed -i 's/proxy_set_header Host localhost;/proxy_set_header Host $host;/' ${CPATH}nginx-config/web-app.conf
    sudo sed -i 's/http:\/\/127.0.0.1/http:\/\/flask/g' ${CPATH}nginx-config/web-app.conf
    sudo sed -i "s|alias ${CPATH}web_app/static/;|alias /app/web_app/static/;|" "${CPATH}nginx-config/web-app.conf"

    # Подстановка новых путей в .locust.conf для docker-теста
    sed -i "s|^csv = .*|csv = ${RESULTS_DIR_DOCKER}/results|g" "$DLOCUST_CONF"
    sed -i "s|^html = .*|html = ${RESULTS_DIR_DOCKER}/results.html|g" "$DLOCUST_CONF"
    sed -i 's|http://127.0.0.1|http:\/\/nginx|g' "$DLOCUST_CONF"
    sed -i 's|http://flask|http:\/\/nginx|g' "$DLOCUST_CONF"
    sudo sed -i 's/^REDIS_HOST = "127.0.0.1"/REDIS_HOST = "redis"/' ${CPATH}web_app/config.py
    sudo docker-compose -f ${NGINX_DC} up -d --build --scale worker=3
    check_locust_containers

    # Откат конфигурации .locust.conf для docker-теста
    sed -i "s|^csv = .*|csv = /app/Kuznechik/for_docker/results/results|g" "$DLOCUST_CONF"
    sed -i "s|^html = .*|html = /app/Kuznechik/for_docker/results/results.html|g" "$DLOCUST_CONF"
}


close_and_delete(){
    docker stop $(docker ps -q)
    docker container prune -f
    echo Контейнеры остановлены и удалены
}


pg_remove(){
    sudo systemctl stop postgresql
    sudo systemctl stop pgbouncer

    sudo apt-get --purge remove postgresql postgresql-* -y

    sudo rm -rf /etc/postgresql/
    sudo rm -rf /var/lib/postgresql/
    sudo rm -rf /var/log/postgresql/

    sudo apt-get --purge remove pgbouncer -y

    sudo rm -rf /etc/pgbouncer/
    sudo rm -rf /var/log/pgbouncer/

    sudo apt-get autoremove -y

    sudo apt-get clean

    dpkg -l | grep -E 'postgresql|pgbouncer'
    
    rm -rf ${CPATH}migrations

    sudo systemctl stop apache2.service redis-server.service redis.service postgresql.service pgbouncer.service nginx.service
    sudo pkill -f "gunicorn" || true
    sudo pkill -f "locust" || true
    sudo pkill -f "pgbouncer" || true
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
    pgrm)
	pg_remove
	;;
    local)
        on_localhost
        ;;
#a2d - apache2 в контейнерах
    a2d)
        apache2_docker
        ;;
#a2s - apache2 в сервисах
    a2s)   
        venv
        apache2_server
        ;;
#nd - nginx в контейнерах
    nd)
        nginx_docker
        ;;
#ns - nginx в сервисах
    ns)
	nginx_server
	;;
    final)
	bash prepare.sh
	nginx_server
	sleep 350
	nginx_docker
	sleep 350
	echo "Тест выполнился"	
	;;
esac

