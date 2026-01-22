#!/bin/bash

# Проверка наличия аргументов
if [[ $# -ne 2 ]]; then
    echo "Usage: $0 {read|write} {vda|vdb}"
    exit 1
fi

# Аргументы командной строки
operation=$1   # read или write
device=$2      # vda или vdb

# Функция для выполнения теста записи (write)
perform_write_test() {
    local device=$1
    local size=$2

    # Выполняем тест записи с помощью fio
    result=$(sudo fio --name=test --ioengine=libaio --direct=1 --rw=randwrite --norandommap \
        --randrepeat 0 --size="$size" --numjobs=1 --bs=4k --iodepth=128 --time_based \
        --runtime=10 --group_reporting --filename="/dev/$device" --minimal | awk -F';' '{print $49}')

    echo "$size $result"
}

# Функция для выполнения теста чтения (read)
perform_read_test() {
    local device=$1
    local size=$2

    # Выполняем тест чтения с помощью fio
    result=$(sudo fio --name=test --ioengine=libaio --direct=1 --rw=randread --norandommap \
        --randrepeat 0 --size="$size" --numjobs=1 --bs=4k --iodepth=128 --time_based \
        --runtime=10 --group_reporting --filename="/dev/$device" --minimal | awk -F';' '{print $8}')

    echo "$size $result"
}

# Массив размеров для тестирования
sizes=("64G" "128G" "256G" "512G" "1T")

# Проверка операции
case $operation in
    read)
        for size in "${sizes[@]}"; do
            perform_read_test "$device" "$size"
        done
        ;;
    write)
        for size in "${sizes[@]}"; do
            perform_write_test "$device" "$size"
        done
        ;;
    *)
        echo "Invalid operation. Use 'read' or 'write'."
        exit 1
        ;;
esac