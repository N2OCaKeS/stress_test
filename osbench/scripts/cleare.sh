#!/bin/bash

set -e

LOG_DIR="logs"

if [ ! -d "$LOG_DIR" ]; then
    echo "❌ Папка $LOG_DIR не найдена"
    exit 1
fi

pushd "$LOG_DIR" || { echo "❌ Папка не найдена"; exit 1; }
files=$(find . -maxdepth 1 -type f ! -name "osbench.log" | wc -l)
find . -maxdepth 1 -type f ! -name "osbench.log" -delete
popd
echo "Удалено $files файлов в $LOG_DIR (кроме osbench.log)"

