#!/bin/bash

set -e

LOG_DIR="logs"
RES_DIR="results"

if [ ! -d "$LOG_DIR" ]; then
    echo "Папка $LOG_DIR не найдена"
    exit 1
fi

if [ ! -d "$RES_DIR" ]; then
    echo "Папка $RES_DIR не найдена"
    exit 1
fi

pushd "$LOG_DIR" || { echo "Папка не найдена"; exit 1; }
files=$(find . -maxdepth 1 -type f ! -name "osbench.log" | wc -l)
find . -maxdepth 1 -type f ! -name "osbench.log" -delete
popd

pushd "$RES_DIR" || { echo "Папка не найдена"; exit 1; }
files2=$(find . -maxdepth 1 -type f | wc -l)
find . -maxdepth 1 -type f -delete
popd

echo "Удалено $files файлов в $LOG_DIR (кроме osbench.log)"
echo "Удалено $files2 файлов в $RES_DIR"
