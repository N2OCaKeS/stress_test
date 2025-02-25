#!/bin/bash

http(){
    docker-compose -f docker-compose.web-app.yml build --no-cache
    docker-compose -f docker-compose.web-app.yml up -d
}


case $1 in
    http)
        http
        ;;
esac