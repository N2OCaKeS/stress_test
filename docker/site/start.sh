#!/bin/bash

on_high_server(){
    apt install docker.io docker-compose
    sed -i 's/^POSTGRES_HOST=127.0.0.1$/POSTGRES_HOST=postgres/' .env
    docker-compose -f docker-compose.v2.yml up --build -d
}


on_localhost(){
    sed -i 's/^POSTGRES_HOST=postgres$/POSTGRES_HOST=127.0.0.1/' .env
    flask run
}


case $1 in
    local)
        on_localhost
        ;;
    server)
        on_high_server
        ;;
esac