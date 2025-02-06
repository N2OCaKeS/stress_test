#!/bin/bash
# Скрипт для запуска Apache Bench
echo "Starting load test..."
ab -n 100000000 -c 100 http://nginx/