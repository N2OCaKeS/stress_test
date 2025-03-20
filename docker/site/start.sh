#!/bin/bash

high_server="10.177.103.205"
localhost="localhost"
NGINX_DC="docker-compose.nginx.yml"
LOAD_DOCKER_CONTAINERS=("master" "site_worker_1" "site_worker_2" "site_worker_3")
APP_CONTAINERS=("postgres" "pgbouncer" "redis" "flask" "nginx")
CPATH="/home/u/git/stress_test/docker/site/"
VENV=".venv/bin/"
PG_VERSION=$(psql --version | awk '{print $3}' | cut -d'.' -f1)
export PG_VERSION
md5_pass_postgres=$(echo -n "1postgres" | md5sum | awk '{print "md5"$1}')
md5_pass_u=$(echo -n "1u" | md5sum | awk '{print "md5"$1}')


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


venv(){
    # usermod -aG docker u
    # apt-get install python3 docker.io docker-compose python3-venv python3.11-venv apache2-utils pip postgresql postgresql-contrib libpq-dev -y
    sudo apt update
    sudo apt-get install libpq-dev gcc python3-dev python3-pip python3.11-venv libapache2-mod-wsgi-py3 docker.io docker-compose nginx  postgresql postgresql-contrib redis-server pgbouncer apache2 apache2-utils -y 
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install --upgrade setuptools wheel
    pip install -r requirements.txt
}


check_containers() {
    compose_file=$1       # Файл docker-compose
    shift                 # Убираем первый аргумент из списка
    expected_containers=("$@")  # Оставшиеся аргументы - это имена контейнеров

    echo "Ожидание 10 секунд перед проверкой контейнеров..."
    sleep 10

    while true; do
        all_running=true  # Флаг, указывающий, что все контейнеры запущены

        for container in "${expected_containers[@]}"; do
            if ! docker ps --format "{{.Names}}" | grep -q "^${container}$"; then
                echo "Контейнер $container не запущен. Перезапускаем весь стек..."
                docker-compose -f "$compose_file" up -d
                all_running=false  # Хотя бы один контейнер не запущен
                echo "Ждем 10 секунд после перезапуска стека..."
                sleep 10
                break  # Выходим из цикла for, чтобы начать проверку заново
            else
                echo "Контейнер $container работает."
            fi
        done

        # Если все контейнеры запущены, выходим из цикла
        if $all_running; then
            echo "Все контейнеры успешно запущены."
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
    if [ -f "${CPATH}/pg/postgresql.conf" ]; then
        sudo cp ${CPATH}/pg/postgresql.conf /etc/postgresql/15/main/postgresql.conf
    fi
    if [ -f "${CPATH}/pg/pg_hba.conf" ]; then
        sudo cp ${CPATH}/pg/pg_hba.conf /etc/postgresql/15/main/pg_hba.conf
    fi

    echo "Настройка PostgreSQL..."
    sudo sed -i '/data_directory/d' /etc/postgresql/15/main/postgresql.conf
    echo "data_directory = '/var/lib/postgresql/15/main'" | sudo tee -a /etc/postgresql/15/main/postgresql.conf
    psql_settings
    sudo chown -R postgres:postgres /var/lib/postgresql/
    sudo chmod -R 700 /var/lib/postgresql/
    sudo systemctl restart postgresql
    sudo -u postgres psql -d mydb -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;"

    echo "Настройка Redis и PgBouncer..."
    sudo sed -i 's/^REDIS_HOST = "redis"/REDIS_HOST = "127.0.0.1"/' ${CPATH}web_app/config.py
    sudo cp ${CPATH}/pg/pgbouncer/pgbouncer_host/* /etc/pgbouncer/
    sudo systemctl restart pgbouncer
    sudo redis-cli flushall
    sudo systemctl start redis

    echo "Настройка Nginx..."
    sudo rm -f /etc/nginx/conf.d/*
    [ -f "/etc/nginx/sites-enabled/default" ] && sudo rm /etc/nginx/sites-enabled/default

    sudo sed -i 's/proxy_set_header Host $host;/proxy_set_header Host localhost;/' ${CPATH}nginx-config/web-app.conf
    sudo sed -i 's|http://flask|http://127.0.0.1|g' ${CPATH}nginx-config/web-app.conf
    sudo sed -i "s|alias /app/web_app/static/;|alias ${CPATH}web_app/static/;|" "${CPATH}nginx-config/web-app.conf"

    if [ -f "${CPATH}/nginx-config/web-app.conf" ]; then
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
        sudo ${CPATH}${VENV}flask --app ${CPATH}web_app db init
    fi
    sudo ${CPATH}${VENV}flask --app ${CPATH}web_app db migrate || true
    sudo ${CPATH}${VENV}flask --app ${CPATH}web_app db upgrade || true

    echo "Остановка старых процессов Gunicorn и Locust..."
    sudo pkill -f "gunicorn" || true
    sudo pkill -f "locust" || true
    sleep 3

    echo "Запуск Gunicorn в фоне..."
    sudo nohup ${CPATH}${VENV}gunicorn -w 4 -b 127.0.0.1:8000 --chdir ${CPATH}web_app --log-level=debug wsgi:application > /tmp/gunicorn.log 2>&1 &
    sleep 3

    echo "Запуск Locust в фоне..."
    sudo nohup ${CPATH}${VENV}locust -f ${CPATH}Kuznechik/for_host/locustfile.py \
        --config ${CPATH}Kuznechik/for_host/.locust.conf --processes 3 > /tmp/locust.log 2>&1 &

    echo "Настройка завершена, сервер запущен!"
}


nginx_docker(){
    sudo systemctl stop apache2.service redis-server.service redis.service postgresql.service pgbouncer.service nginx.service
    sudo docker-compose -f ${NGINX_DC} down 
    sudo docker volume prune -f
    echo "Остановка старых процессов Gunicorn и Locust..."
    sudo pkill -f "gunicorn" || true
    sudo pkill -f "locust" || true
    sleep 2

    sudo sed -i 's/proxy_set_header Host localhost;/proxy_set_header Host $host;/' ${CPATH}nginx-config/web-app.conf
    sudo sed -i 's/http:\/\/127.0.0.1/http:\/\/flask/g' ${CPATH}nginx-config/web-app.conf
    sudo sed -i "s|alias ${CPATH}web_app/static/;|alias /app/web_app/static/;|" "${CPATH}nginx-config/web-app.conf"
    sed -i 's|http://127.0.0.1|http://nginx|g' ./Kuznechik/for_docker/.locust.conf 
    sed -i 's|http://flask|http://nginx|g' ./Kuznechik/for_docker/.locust.conf 
    sudo sed -i 's/^REDIS_HOST = "127.0.0.1"/REDIS_HOST = "redis"/' web_app/config.py
    sudo docker-compose -f ${NGINX_DC} up --build -d
    check_containers "${NGINX_DC}" "${APP_CONTAINERS[@]}"
    check_containers "${NGINX_DC}" "${LOAD_DOCKER_CONTAINERS[@]}"
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
	venv
	nginx_server
	;;
    final)
	venv
	nginx_server
	sleep 400
	nginx_docker
	echo "Тест выполнился"	
	;;
esac

