#!/bin/bash

FUNC=parsec_file_open

TRACE_DIR=/sys/kernel/tracing

# Очистить и настроить
echo 0 > $TRACE_DIR/tracing_on
echo > $TRACE_DIR/trace
echo function_graph > $TRACE_DIR/current_tracer
echo $FUNC > $TRACE_DIR/set_ftrace_filter
echo 1 > $TRACE_DIR/options/funcgraph-abstime
echo 1 > $TRACE_DIR/options/latency-format

# Запуск теста
echo 1 > $TRACE_DIR/tracing_on
./load2noarch /home/u/test/ 150 20000 5
echo 0 > $TRACE_DIR/tracing_on

# Извлечь все длительности и суммировать
cat $TRACE_DIR/trace | awk -v fname="$FUNC" '
$0 ~ fname {
    match($0, /([0-9]+\.[0-9]+) us/, arr)
    if (arr[1] != "") {
        total += arr[1]
        count++
    }
}
END {
    printf "Вызовов %s: %d\n", fname, count
    printf "Суммарное время: %.3f us (%.3f s)\n", total, total/1000000
}'