#!/bin/bash

high_server="10.177.103.205"
localhost="localhost"
LOCAL_DOCKER_CONTAINERS=("master" "site_worker_1" "site_worker_2" "site_worker_3")
APP_CONTAINERS=("flask" "redis" "postgres" "pgbouncer")


venv(){
    # usermod -aG docker u
    # apt-get install python3 docker.io docker-compose python3-venv python3.11-venv apache2-utils pip postgresql postgresql-contrib libpq-dev -y
    # sudo service postgresql start
    sudo apt-get install libpq-dev gcc python3-dev python3-pip python3.11-venv libapache2-mod-wsgi-py3 -y 
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

    for container in "${expected_containers[@]}"; do
        if ! docker ps --format "{{.Names}}" | grep -q; then
            echo "Контейнер $container не запущен. Перезапускаем..."
            docker-compose -f "$compose_file" up -d
            echo "Ждем 5 секунд после попытки перезапуска контейнера "
            sleep 5
        else
            echo "Контейнер $container работает."
        fi
    done
}


app_settings(){
    sudo apt-get update
    sudo apt-get install postgresql postgresql-contrib redis-server pgbouncer apache2 apache2-utils -y  
#    export PGPASSWORD="1postgres"

#    sudo -u postgres psql -c "ALTER USER postgres WITH ENCRYPTED PASSWORD '${md5_pass}';"
#    sudo -u postgres psql -c "CREATE DATABASE mydb;"
#    sudo -u postgres psql -c "CREATE ROLE u WITH LOGIN ENCRYPTED PASSWORD '${md5_pass}';"
#    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE mydb TO u;"
#    sudo -u postgres psql -c "ALTER USER u CREATEDB;"
#    sudo -u postgres psql -d mydb -c "GRANT ALL ON schema public TO u;"

md5_pass_postgres=$(echo -n "postgres1postgres" | md5sum | awk '{print "md5"$1}')
md5_pass_u=$(echo -n "1u" | md5sum | awk '{print "md5"$1}')
    sudo cp /home/u/git/stress_test/docker/site/pg/pg_hba.conf /etc/postgresql/*/main/pg_hba.conf
    sudo -u postgres psql <<EOF
    ALTER USER postgres WITH ENCRYPTED PASSWORD '${md5_pass_postgres}';
    CREATE DATABASE mydb;
    CREATE ROLE u WITH LOGIN ENCRYPTED PASSWORD '${md5_pass_u}';
    GRANT ALL PRIVILEGES ON DATABASE mydb TO u;
    ALTER USER u CREATEDB;
    \c mydb
    GRANT ALL ON schema public TO u;
EOF
    sudo sed -i 's#sys.path.insert(0, "/app")#sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))\nsys.path.insert(0, "/home/u/git/stress_test/docker/site")#' /home/u/git/stress_test/docker/site/web_app/wsgi.py
    sudo sed -i 's/^\(local\s\+all\s\+postgres\s\+\).*/\1md5/' /etc/postgresql/*/main/pg_hba.conf
    sudo sed -i 's/^\(host\s\+all\s\+all\s\+127.0.0.1\/32\s\+\).*/\1md5/' /etc/postgresql/*/main/pg_hba.conf
    sudo sed -i 's/^\(host\s\+all\s\+all\s\+::1\/128\s\+\).*/\1md5/' /etc/postgresql/*/main/pg_hba.conf
    sudo sed -i 's/^REDIS_HOST = "redis"/REDIS_HOST = "127.0.0.1"/' /home/u/git/stress_test/docker/site/web_app/config.py
    sudo cp pg/postgresql.conf /etc/postgresql/*/main/postgresql.conf 
    sudo echo "data_directory = '/var/lib/postgresql/15/main'" | sudo tee -a /etc/postgresql/15/main/postgresql.conf

    sudo chown -R postgres:postgres /var/lib/postgresql/
    sudo chmod -R 700 /var/lib/postgresql/

    sudo systemctl restart postgresql

    sudo cp pg/pgbouncer/pgbouncer_host/* /etc/pgbouncer/
    sudo systemctl restart pgbouncer
    sudo rm -f /etc/apache2/sites-available/*
    sudo cp apache-config/config_host/apache2.conf /etc/apache2/apache2.conf
    sudo cp apache-config/config_host/web-app.conf /etc/apache2/sites-available/
    sudo chown www-data:www-data /etc/apache2/sites-available/web-app.conf
    sudo chmod 644 /etc/apache2/sites-available/web-app.conf
    sudo chown -R www-data:www-data /home/u/git/stress_test/docker/site/
    sudo chmod -R 755 /home/u/git/stress_test/docker/site/
    sudo a2enmod wsgi
    sudo a2ensite web-app
    sudo apachectl configtest
    sudo apachectl -M | grep astra
    sudo systemctl restart apache2
    sudo docker-compose -f docker-compose.locust.yml up --build -d
    check_containers "docker-compose.locust.yml" "${LOCAL_DOCKER_CONTAINERS[@]}"
}


on_high_server(){
    apt install -y docker.io docker-compose
    sed -i 's/^ServerName 10.177.103.205$/ServerName $high_server/' Dockerfile.flask
    sed -i 's/^ServerName 10.177.103.205$/ServerName $high_server/' ./apache-config/web-app.conf
    sed -i 's/^ServerName 10.177.103.205$/ServerRoot $high_server/' ./apache-config/httpd.conf
    sed -i 's/^POSTGRES_HOST=127.0.0.1$/POSTGRES_HOST=postgres/' .env
    docker-compose -f docker-compose.v2.yml up --build -d
}


on_local(){
    sed -i 's/^POSTGRES_HOST=postgres$/POSTGRES_HOST=127.0.0.1/' .env
    flask run
}


on_local_docker(){
    sed -i '/sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))/ {
    N
    /sys.path.insert(0, "\/home\/u\/git\/stress_test\/docker\/site")/ {
        s#sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))\nsys.path.insert(0, "/home/u/git/stress_test/docker/site")#sys.path.insert(0, "/app")#
    }
}' /home/u/git/stress_test/docker/site/web_app/wsgi.py
    sudo systemctl stop apache2.service redis-server.service redis.service postgresql.service pgbouncer.service
    sudo apt install -y docker.io docker-compose
    sudo sed -i 's/^REDIS_HOST = "127.0.0.1"/REDIS_HOST = "redis"/' web_app/config.py
    # sed -i 's/^ServerName 10.177.103.205$/ServerName localhost/' Dockerfile.flask
    # sed -i 's/^ServerName 10.177.103.205$/ServerName localhost/' ./apache-config/web-app.conf
    # # sed -i 's/^ServerName 10.177.103.205$/ServerRoot localhost/' ./apache-config/httpd.conf
    # sed -i 's/^POSTGRES_HOST=127.0.0.1$/POSTGRES_HOST=postgres/' .env
    sudo docker-compose -f docker-compose.v2.yml up --build -d
    check_containers "docker-compose.locust.yml" "${APP_CONTAINERS[@]}"
    check_containers "docker-compose.v2.yml" "${LOCAL_DOCKER_CONTAINERS[@]}"

    # docker-compose -f docker-compose.v2.yml logs -f --tail=100
}
# --scale worker=8

close_and_delete(){
    docker stop $(docker ps -q)
    docker container prune -f
    docker rmi $(docker images -q)
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

    sudo apt-get autoremove

    sudo apt-get clean

    dpkg -l | grep -E 'postgresql|pgbouncer'

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
    server)
        on_high_server
        ;;
    local_docker)
        on_local_docker
        ;;
    local_server)
        venv
        app_settings
        ;;
esac
