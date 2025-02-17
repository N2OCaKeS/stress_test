#!/bin/bash
docker_prepare(){
    sudo usermod -aG docker u
    sudo apt-get install docker.io docker-compose python3-venv apache2-utils pip postgresql postgresql-contrib libpq-dev- y
    sudo service postgresql start
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --upgrade pip
    pip install --upgrade setuptools wheel
    pip install -r requirements.txt
}


load_test(){
    python3 main.py --test load -ram 100M -t 1 -c 5
}


setup_db(){
    echo "postgres:1" | sudo chpasswd
    su - postgres -c 'psql'
    CREATE DATABASE mydb;
    CREATE ROLE u with password '1';
    ALTER ROLE "u" WITH LOGIN;
    GRANT ALL PRIVILEGES ON DATABASE "mydb" to u;
    ALTER USER u CREATEDB;
    \c mydb
    GRANT ALL ON schema public TO u;
}

migrate(){
    flask db init
    flask db migrate
    flask db upgrade
}


case $1 in 
    load)
        docker_prepare
        load_test
        ;;
    prepare)
        docker_prepare
        ;;
esac