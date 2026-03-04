#!/bin/bash

HOST="127.0.0.1"
PORT="9001"

# Функция для отправки запросов
spam() {
    local duration=$1
    end_time=$(( $(date +%s) + duration ))
    
    while [[ $(date +%s) -lt $end_time ]]; do
        # Открываем TCP-соединение и сразу закрываем
        timeout 0.1 telnet "$HOST" "$PORT" 2>/dev/null >/dev/null
    done
}

# Число потоков (по умолчанию — 200)
THREADS="${1:-200}"

# Продолжительность нагрузки в секундах (по умолчанию — бесконечно)
DURATION="${2:-0}"  # Если DURATION равен нулю, работа продолжается бесконечно

if [[ $DURATION -eq 0 ]]; then
    echo "Скрипт запустится БЕССРОЧНО!"
else
    echo "Продолжительность нагрузки задана: $DURATION секунд."
fi

echo "Запуск $THREADS потоков спама на $HOST:$PORT"

for ((i=1; i<=THREADS; i++)); do
    spam "$DURATION" &
    if ((i % 50 == 0)); then
        echo "Запущено $i потоков"
    fi
done

echo "Все $THREADS потоков запущены. PID основного процесса: $$"
echo "Для остановки нажмите Ctrl+C или выполните: kill $$"

wait

