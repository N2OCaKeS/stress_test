#!/bin/bash

high_server="10.177.103.205"
localhost="localhost"

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
    sudo apt install -y docker.io docker-compose
    # sed -i 's/^ServerName 10.177.103.205$/ServerName localhost/' Dockerfile.flask
    # sed -i 's/^ServerName 10.177.103.205$/ServerName localhost/' ./apache-config/web-app.conf
    # # sed -i 's/^ServerName 10.177.103.205$/ServerRoot localhost/' ./apache-config/httpd.conf
    # sed -i 's/^POSTGRES_HOST=127.0.0.1$/POSTGRES_HOST=postgres/' .env
    docker-compose -f docker-compose.v2.yml up --build -d 
    docker-compose -f docker-compose.v2.yml logs -f --tail=100

}
# --scale worker=8

close_and_delete(){
    docker stop $(docker ps -q)
    docker container prune -f
    docker rmi $(docker images -q)
    echo Контейнеры остановлены и удалены
}


case $1 in
    close)
        close_and_delete
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
esac
