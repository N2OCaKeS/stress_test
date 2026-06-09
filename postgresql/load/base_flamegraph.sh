#!/bin/bash
set -e


TEST_DIR="/home/u/test"
PERF_DATA="perf.data"
THREADS="150"
FILE_COUNT="20000"
ARCH_LOOP="5"

ERROR="\033[31m[ERROR]:\033[39m"
INFO="\033[32m[INFO]:\033[39m"
ARR="\033[33m➜\033[39m"


if ! command -v perf &> /dev/null; then
    echo -e "$ERROR perf не установлен. Установите linux-tools-common и linux-tools-$(uname -r)"
    exit 1
fi

if [[ ! -x ./load2noarch ]]; then
    echo "$ERROR Нагрузочный скрипт 'load2noarch' не найден или не исполняемый"
    exit 1
fi

if [[ ! -f ../libs/stackcollapse-perf.pl || ! -f ../libs/flamegraph.pl ]]; then
    echo "$ERROR Не найдены Perl-скрипты в ../libs/"
    exit 1
fi


if [[ -z $1 ]]; then
    echo "Укажите наименование файла Flame Graph (тип данных - svg)"
    read FILE_NAME
else
    FILE_NAME=$1
fi
echo "$INFO Наименование файла Flame Graph: $FILE_NAME"


echo "=== Подготовка директории ==="
sudo mkdir -p "$TEST_DIR" && \
sudo chmod -R 777 "$TEST_DIR" && \
echo "$INFO Директория готова"

echo ""
echo "=== Запуск профилирования ==="
sudo perf record -g -a -o "$PERF_DATA" -- ./load2noarch "$TEST_DIR" "$THREADS" "$FILE_COUNT" "$ARCH_LOOP" && \
echo "$INFO Профилирование завершено, данные сохранены в $PERF_DATA"

echo ""
echo "=== Генерация Flame Graph ==="
sudo perf script -i "$PERF_DATA" | \
    perl ../libs/stackcollapse-perf.pl | \
    perl ../libs/flamegraph.pl > "$FILE_NAME" && \
echo "$INFO Flame Graph создан: $FILE_NAME"

echo ""
echo "=== Информация о сэмплах ==="
SAMPLE_INFO=$(sudo perf script --header-only -i "$PERF_DATA" | grep "sample")
echo "$SAMPLE_INFO"

DURATION_MS=$(echo "$SAMPLE_INFO" | grep "sample duration" | awk '{print $4}')
if [[ -n "$DURATION_MS" ]]; then
    DURATION_SEC=$(echo "scale=1; $DURATION_MS / 1000" | bc)
    echo "   $ARR Время работы нагрузочного скрипта: $DURATION_SEC сек"
fi
echo "$INFO Данные о сэмплах получены"

