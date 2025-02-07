#!/bin/bash
# Скрипт для запуска Apache Bench
echo "Waiting for the server to start..."
sleep 10

echo "Starting load test..."
ab -n 100000000 -c 100 http://172.21.0.2/ 