#!/usr/bin/env bash

set -euo pipefail

###############################################################################
# CONFIG
###############################################################################

# Длительность сбора метрик
DURATION_SEC=600

# Интервал сбора
INTERVAL_SEC=1

# Запас поверх DURATION_SEC для внутреннего timeout у каждого генератора
# нагрузки — это подстраховка на случай, если cleanup() не выполнится
# (например, скрипт убит -9), а не бюджет на сам тест: генераторы стартуют
# ДО долгой подготовки (fallocate под RAM-нагрузку, pre-create диска), так
# что отсчёт их собственного timeout уже идёт, пока идёт подготовка. На
# больших MEM_LOAD_PERCENT одно только выделение памяти может занять больше
# минуты — при маленьком запасе генератор гарантированно умрёт по timeout
# ещё до конца сбора метрик, и это выглядит как "нагрузка внезапно исчезла".
GENERATOR_TIMEOUT_BUFFER_SEC=600

# Доля ядер (%), которая грузится непрерывно на 100%. Нагрузка статична —
# без duty-cycle: cpu_workers = round(nproc * LOAD_TARGET_PERCENT / 100)
# ядер работают на 100% весь тест, остальные простаивают, в сумме по хосту
# получается стабильно ~LOAD_TARGET_PERCENT% CPU.
LOAD_TARGET_PERCENT=75

# Выходные файлы
OUTPUT_DIR=$2
IDLE_HOST_CSV="${OUTPUT_DIR}/idle_host.csv"
IDLE_OS_CSV="${OUTPUT_DIR}/idle_os.csv"
LOAD_HOST_CSV="${OUTPUT_DIR}/load_host.csv"
LOAD_OS_CSV="${OUTPUT_DIR}/load_os.csv"

###############################################################################
# LOAD CONFIG
###############################################################################

# CPU: непрерывная нагрузка (dd) на долю ядер, см. LOAD_TARGET_PERCENT
ENABLE_CPU_LOAD=1

# RAM: разовое выделение памяти в tmpfs перед стартом сбора метрик
ENABLE_MEM_LOAD=1

# Процент MemTotal, занимаемый RAM-нагрузкой.
MEM_LOAD_PERCENT=75

# Дисковая нагрузка
ENABLE_DISK_LOAD=1

# Каталог и файл для дисковой нагрузки
DISK_DIR="/var/tmp"
DISK_TEST_FILE="${DISK_DIR}/os_resource_test.disk"

# Размер рабочего файла
DISK_FILE_SIZE="1G"

# Размер блока dd
DISK_BS="4k"

# Сетевая нагрузка, только loopback
ENABLE_NET_LOAD=1

# Порт nc-приёмника на 127.0.0.1
NET_LOOPBACK_PORT=15201

# Дочерние bash-воркеры получают эти переменные через окружение
export DISK_TEST_FILE DISK_BS NET_LOOPBACK_PORT

###############################################################################
# INTERNAL
###############################################################################

NCPU="$(nproc)"

CPU_PID=""
DISK_WRITE_PID=""
DISK_READ_PID=""
NET_LISTENER_PID=""
NET_SENDER_PID=""
DISK_DEVICE=""

RAM_FILE="/dev/shm/os_resource_test.ram"
RAM_FILE_KB=0

# Заполняется, только если пришлось увеличивать /dev/shm под нужный размер —
# тогда в cleanup() возвращаем исходный лимит.
SHM_ORIG_SIZE_KB=""

declare -A LAST_IO_PID
declare -A LAST_IO_VALUE

mkdir -p "$OUTPUT_DIR"

###############################################################################
# HELPERS
###############################################################################

die()
{
    echo "ERROR: $*" >&2
    exit 1
}

check_common_dependencies()
{
    local cmd

    for cmd in awk date findmnt readlink ps nproc pgrep; do
        command -v "$cmd" >/dev/null 2>&1 || die "Не найдена команда: $cmd"
    done
}

check_load_dependencies()
{
    local cmd

    for cmd in dd timeout nc nproc pgrep setsid fallocate; do
        command -v "$cmd" >/dev/null 2>&1 || die "Не найдена команда: $cmd"
    done
}

###############################################################################
# DISK
###############################################################################

detect_disk_device()
{
    local source
    local resolved

    source="$(findmnt -n -o SOURCE --target "$DISK_DIR" 2>/dev/null || true)"

    if [[ -z "$source" ]]; then
        return
    fi

    if [[ "$source" == /dev/* ]]; then
        resolved="$(readlink -f "$source" 2>/dev/null || echo "$source")"
        DISK_DEVICE="$(basename "$resolved")"
    fi

    if [[ -n "$DISK_DEVICE" ]] &&
       [[ ! -r "/sys/class/block/${DISK_DEVICE}/stat" ]]; then
        DISK_DEVICE=""
    fi
}

read_disk_stats()
{
    if [[ -n "$DISK_DEVICE" ]] &&
       [[ -r "/sys/class/block/${DISK_DEVICE}/stat" ]]; then

        awk '{
            print $3, $7
        }' "/sys/class/block/${DISK_DEVICE}/stat"
    else
        echo "0 0"
    fi
}

###############################################################################
# NETWORK
###############################################################################

read_network_stats()
{
    awk '
        NR > 2 {
            interface = $1
            gsub(":", "", interface)

            if (interface != "lo") {
                rx += $2
                tx += $10
            }
        }

        END {
            print rx + 0, tx + 0
        }
    ' /proc/net/dev
}

read_network_lo_stats()
{
    awk '
        NR > 2 {
            interface = $1
            gsub(":", "", interface)

            if (interface == "lo") {
                rx = $2
                tx = $10
            }
        }

        END {
            print rx + 0, tx + 0
        }
    ' /proc/net/dev
}

###############################################################################
# CPU / SYSTEM
###############################################################################

read_system_stats()
{
    awk '
        /^cpu / {
            user=$2
            nice=$3
            cpu_sys=$4
            idle=$5
            iowait=$6
            irq=$7
            softirq=$8
            steal=$9
        }

        /^ctxt / {
            ctxt=$2
        }

        /^intr / {
            intr=$2
        }

        /^procs_running / {
            running=$2
        }

        /^procs_blocked / {
            blocked=$2
        }

        END {
            print \
                user, nice, cpu_sys, idle, iowait, \
                irq, softirq, steal, \
                ctxt, intr, running, blocked
        }
    ' /proc/stat
}

###############################################################################
# MEMORY
###############################################################################

read_memory_stats()
{
    awk '
        /^MemTotal:/ {
            total=$2
        }

        /^MemAvailable:/ {
            available=$2
        }

        /^Buffers:/ {
            buffers=$2
        }

        /^Cached:/ {
            cached=$2
        }

        /^SwapTotal:/ {
            swap_total=$2
        }

        /^SwapFree:/ {
            swap_free=$2
        }

        /^Slab:/ {
            slab=$2
        }

        /^KernelStack:/ {
            kernel_stack=$2
        }

        /^PageTables:/ {
            page_tables=$2
        }

        /^Shmem:/ {
            shmem=$2
        }

        END {
            used = total - available
            swap_used = swap_total - swap_free

            printf "%d %d %d %d %d %d %d %d %d %d\n", \
                total, \
                available, \
                used, \
                swap_total, \
                swap_used, \
                buffers, \
                cached, \
                slab, \
                kernel_stack + page_tables, \
                shmem
        }
    ' /proc/meminfo
}

###############################################################################
# LOAD AVERAGE
###############################################################################

read_loadavg()
{
    awk '{
        split($4, tasks, "/")

        print \
            $1, \
            $2, \
            $3, \
            tasks[1], \
            tasks[2]
    }' /proc/loadavg
}

###############################################################################
# АТРИБУЦИЯ ГЕНЕРАТОРОВ НАГРУЗКИ
###############################################################################

read_proc_io_field()
{
    local pid="$1"
    local field="$2"
    local value

    if [[ ! -r "/proc/${pid}/io" ]]; then
        echo 0
        return
    fi

    value="$(
        awk -v f="${field}:" '
            $1 == f {
                print $2
                found = 1
            }

            END {
                if (!found)
                    print 0
            }
        ' "/proc/${pid}/io" 2>/dev/null
    )"

    echo "${value:-0}"
}

# Воркеры диска и сети крутят dd в бесконечном цикле, и каждая новая
# итерация — новый процесс с новым PID (dd упирается в конец файла или
# заканчивает один прогон через пайп), поэтому счётчик /proc/pid/io каждый
# раз стартует с нуля. Функция находит текущий живой dd в трекнутой pgid и
# считает дельту относительно предыдущего сэмпла для этого же PID; если PID
# сменился — берёт значение текущего процесса как есть (небольшая, ожидаемая
# погрешность на границе перезапуска).
sample_worker_io_delta()
{
    local name="$1"
    local pgid="$2"
    local field="$3"

    local pid
    local value
    local delta

    if [[ -z "$pgid" ]]; then
        echo 0
        return
    fi

    pid="$(pgrep -g "$pgid" -x dd 2>/dev/null | tail -n1)"

    if [[ -z "$pid" ]]; then
        echo 0
        return
    fi

    value="$(read_proc_io_field "$pid" "$field")"

    if [[ "${LAST_IO_PID[$name]:-}" == "$pid" ]]; then
        delta=$((value - ${LAST_IO_VALUE[$name]:-0}))
    else
        delta="$value"
    fi

    if (( delta < 0 )); then
        delta=0
    fi

    LAST_IO_PID[$name]="$pid"
    LAST_IO_VALUE[$name]="$value"

    echo "$delta"
}

get_pgid_set_cpu_pct()
{
    local pgid
    local pgids=()

    for pgid in "$@"; do
        [[ -n "$pgid" ]] && pgids+=("$pgid")
    done

    if [[ ${#pgids[@]} -eq 0 ]]; then
        echo "0.00"
        return
    fi

    ps -eo pgid=,pcpu= 2>/dev/null |
        awk -v list="${pgids[*]}" '
            BEGIN {
                n = split(list, arr, " ")
                for (i = 1; i <= n; i++) {
                    want[arr[i]] = 1
                }
            }

            {
                if ($1 in want) {
                    sum += $2
                }
            }

            END {
                printf "%.2f", sum + 0
            }
        '
}

###############################################################################
# LOAD GENERATORS
###############################################################################

start_cpu_load()
{
    if [[ "$ENABLE_CPU_LOAD" -ne 1 ]]; then
        return
    fi

    local ncpu
    local cpu_workers
    local timeout_sec
    local worker_body
    local body
    local i

    ncpu="$NCPU"

    cpu_workers="$(
        awk -v n="$ncpu" -v p="$LOAD_TARGET_PERCENT" \
            'BEGIN {
                w = int(n * p / 100 + 0.5)
                if (w < 1)
                    w = 1
                print w
            }'
    )"

    timeout_sec=$((DURATION_SEC + GENERATOR_TIMEOUT_BUFFER_SEC))

    echo "Запускаю CPU-нагрузку:"
    echo "  Ядер всего:  $ncpu"
    echo "  Нагружено:   $cpu_workers (${LOAD_TARGET_PERCENT}% от nproc, непрерывно на 100%)"
    echo

    # Никакого duty-cycle: воркер крутит dd подряд без sleep, само ядро
    # загружено постоянно. Средняя нагрузка по хосту регулируется числом
    # воркеров (долей ядер), а не временем работы каждого.
    worker_body='while true; do dd if=/dev/zero of=/dev/null bs=1M status=none; done'

    body=""

    for ((i=0; i<cpu_workers; i++)); do
        body+="{ ${worker_body} ; } & "
    done

    body+="wait"

    setsid timeout "$timeout_sec" bash -c "$body" \
        >/tmp/os_resource_cpu.log 2>&1 &

    CPU_PID=$!

    echo "CPU load PID/PGID: $CPU_PID"
}

ensure_shm_capacity()
{
    local needed_kb="$1"
    local shm_size_kb
    local new_size_kb

    shm_size_kb="$(df -k /dev/shm | awk 'NR==2 { print $2 }')"

    if (( needed_kb <= shm_size_kb )); then
        return
    fi

    # По умолчанию /dev/shm ограничен половиной RAM — если MEM_LOAD_PERCENT
    # больше этого, дефолтный лимит tmpfs не даст выделить нужный объём.
    # Расширяем tmpfs с запасом 5%, оригинальный размер вернём в cleanup().
    SHM_ORIG_SIZE_KB="$shm_size_kb"
    new_size_kb=$(( needed_kb + needed_kb / 20 ))

    echo "  /dev/shm тесен (${shm_size_kb}K), расширяю до ${new_size_kb}K"

    mount -o "remount,size=${new_size_kb}k" /dev/shm ||
        die "Не удалось расширить /dev/shm под RAM-нагрузку (нужны права root)"
}

start_mem_load()
{
    if [[ "$ENABLE_MEM_LOAD" -ne 1 ]]; then
        return
    fi

    local mem_total_kb
    local count_mb

    mem_total_kb="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)"
    count_mb=$(( mem_total_kb * MEM_LOAD_PERCENT / 100 / 1024 ))
    RAM_FILE_KB=$(( count_mb * 1024 ))

    echo "Выделяю RAM-нагрузку:"
    echo "  Файл:   $RAM_FILE"
    echo "  Размер: ${count_mb}M (${MEM_LOAD_PERCENT}% от MemTotal)"
    echo

    ensure_shm_capacity "$RAM_FILE_KB"

    # fallocate резервирует страницы в tmpfs сразу на уровне ядра, без
    # копирования через userspace — на больших объёмах на порядок быстрее dd
    # (иначе выделение десятков ГБ блокирует старт теста на пару минут).
    fallocate -l "${count_mb}M" "$RAM_FILE"
}

disk_file_size_to_mb()
{
    local size="$1"
    local num
    local unit

    if [[ "$size" =~ ^([0-9]+)([GgMmKk]?)$ ]]; then
        num="${BASH_REMATCH[1]}"
        unit="${BASH_REMATCH[2]}"
    else
        die "Некорректный формат размера: $size"
    fi

    case "$unit" in
        G|g)
            echo $((num * 1024))
            ;;
        M|m|"")
            echo "$num"
            ;;
        K|k)
            echo $(( (num + 1023) / 1024 ))
            ;;
    esac
}

start_disk_load()
{
    if [[ "$ENABLE_DISK_LOAD" -ne 1 ]]; then
        return
    fi

    local size_mb
    local timeout_sec
    local write_body
    local read_body

    size_mb="$(disk_file_size_to_mb "$DISK_FILE_SIZE")"
    timeout_sec=$((DURATION_SEC + GENERATOR_TIMEOUT_BUFFER_SEC))

    echo "Готовлю дисковую нагрузку:"
    echo "  Файл:   $DISK_TEST_FILE"
    echo "  Размер: $DISK_FILE_SIZE"
    echo "  Блок:   $DISK_BS"
    echo

    dd if=/dev/zero of="$DISK_TEST_FILE" bs=1M count="$size_mb" status=none

    # Непрерывное повторение dd по кругу, без sleep между итерациями —
    # интенсивность и так ограничена самим диском, троттлить нечем и незачем.
    # shellcheck disable=SC2016 -- переменные раскрываются в дочернем bash, не здесь
    write_body='while true; do dd if=/dev/zero of="$DISK_TEST_FILE" bs="$DISK_BS" oflag=direct conv=notrunc status=none; done'
    # shellcheck disable=SC2016 -- переменные раскрываются в дочернем bash, не здесь
    read_body='while true; do dd if="$DISK_TEST_FILE" of=/dev/null bs="$DISK_BS" iflag=direct status=none; done'

    setsid timeout "$timeout_sec" bash -c "$write_body" \
        >/tmp/os_resource_disk_write.log 2>&1 &

    DISK_WRITE_PID=$!

    setsid timeout "$timeout_sec" bash -c "$read_body" \
        >/tmp/os_resource_disk_read.log 2>&1 &

    DISK_READ_PID=$!

    echo "Disk write PID/PGID: $DISK_WRITE_PID"
    echo "Disk read  PID/PGID: $DISK_READ_PID"
}

start_net_load()
{
    if [[ "$ENABLE_NET_LOAD" -ne 1 ]]; then
        return
    fi

    local listener_timeout
    local sender_timeout
    local listener_body
    local sender_body

    listener_timeout=$((DURATION_SEC + GENERATOR_TIMEOUT_BUFFER_SEC))
    sender_timeout=$((DURATION_SEC + GENERATOR_TIMEOUT_BUFFER_SEC))

    echo "Запускаю сетевую нагрузку (loopback):"
    echo "  Порт: $NET_LOOPBACK_PORT"
    echo

    # nc -l без -k завершается после каждого разъединения клиента, поэтому
    # приёмник перезапускается в цикле, чтобы принимать соединения от
    # отправителя на протяжении всего теста.
    # shellcheck disable=SC2016 -- $NET_LOOPBACK_PORT раскрывается в дочернем bash
    listener_body='while true; do nc -l 127.0.0.1 "$NET_LOOPBACK_PORT" | dd of=/dev/null bs=1M status=none; done'

    setsid timeout "$listener_timeout" bash -c "$listener_body" \
        >/tmp/os_resource_net_listener.log 2>&1 &

    NET_LISTENER_PID=$!

    sender_body="$(cat <<'EOF'
while true; do
    dd if=/dev/zero bs=1M status=none | nc -q0 127.0.0.1 "$NET_LOOPBACK_PORT"
done
EOF
)"

    setsid timeout "$sender_timeout" bash -c "$sender_body" \
        >/tmp/os_resource_net_sender.log 2>&1 &

    NET_SENDER_PID=$!

    echo "Net listener PID/PGID: $NET_LISTENER_PID"
    echo "Net sender   PID/PGID: $NET_SENDER_PID"
}

###############################################################################
# CLEANUP
###############################################################################

cleanup()
{
    local pid

    for pid in "$CPU_PID" "$DISK_WRITE_PID" "$DISK_READ_PID" \
               "$NET_LISTENER_PID" "$NET_SENDER_PID"; do
        if [[ -n "$pid" ]]; then
            kill -TERM -- "-${pid}" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done

    if [[ -n "${RAM_FILE:-}" ]] && [[ -f "$RAM_FILE" ]]; then
        rm -f "$RAM_FILE"
    fi

    if [[ -n "${SHM_ORIG_SIZE_KB:-}" ]]; then
        mount -o "remount,size=${SHM_ORIG_SIZE_KB}k" /dev/shm 2>/dev/null || true
    fi

    if [[ -n "${DISK_TEST_FILE:-}" ]] && [[ -f "$DISK_TEST_FILE" ]]; then
        rm -f "$DISK_TEST_FILE"
    fi
}

trap cleanup EXIT
trap 'exit 130' INT TERM HUP

###############################################################################
# CSV
###############################################################################

write_csv_header()
{
    local csv="$1"
    local kind="$2"

    local header

    header=\
"elapsed_sec,\
timestamp,\
cpu_user_pct,\
cpu_system_pct,\
cpu_irq_pct,\
cpu_iowait_pct,\
cpu_steal_pct,\
cpu_idle_pct,\
cpu_used_pct,\
load_1m,\
load_5m,\
load_15m,\
tasks_running,\
tasks_total,\
mem_total_kb,\
mem_available_kb,\
mem_used_kb,\
mem_used_pct,\
swap_total_kb,\
swap_used_kb,\
swap_used_pct,\
buffers_kb,\
cached_kb,\
slab_kb,\
kernel_memory_kb,\
shmem_kb,\
context_switches_per_sec,\
interrupts_per_sec,\
procs_running,\
procs_blocked,\
disk_read_kbps,\
disk_write_kbps,\
net_rx_kbps,\
net_tx_kbps,\
net_lo_rx_kbps,\
net_lo_tx_kbps"

    if [[ "$kind" == "host" ]]; then
        header="${header},\
loadgen_cpu_pct,\
loadgen_mem_kb,\
loadgen_disk_write_kbps,\
loadgen_disk_read_kbps,\
loadgen_net_lo_kbps"
    fi

    echo "$header" > "$csv"
}

###############################################################################
# METRIC COLLECTION
###############################################################################

collect_metrics()
{
    local host_csv="$1"
    local os_csv="$2"

    local prev_cpu
    local prev_disk
    local prev_net
    local prev_net_lo

    local prev_ns
    local start_ns

    prev_cpu="$(read_system_stats)"
    prev_disk="$(read_disk_stats)"
    prev_net="$(read_network_stats)"
    prev_net_lo="$(read_network_lo_stats)"

    prev_ns="$(date +%s%N)"
    start_ns="$prev_ns"

    echo
    echo "Сбор метрик:"
    echo "  Интервал:     ${INTERVAL_SEC} сек"
    echo "  Длительность: ${DURATION_SEC} сек"
    echo "  Host CSV:     $host_csv"
    echo "  OS CSV:       $os_csv"

    if [[ -n "$DISK_DEVICE" ]]; then
        echo "  Disk device:  $DISK_DEVICE"
    else
        echo "  Disk device:  не определён"
    fi

    echo

    for ((sample=1; sample<=DURATION_SEC; sample++)); do

        #######################################################################
        # Ждём ровно следующую секундную точку
        #######################################################################

        local target_ns
        local now_ns
        local sleep_ns
        local sleep_sec

        target_ns=$((start_ns + sample * INTERVAL_SEC * 1000000000))

        while true; do
            now_ns="$(date +%s%N)"

            if (( now_ns >= target_ns )); then
                break
            fi

            sleep_ns=$((target_ns - now_ns))

            sleep_sec="$(
                awk -v ns="$sleep_ns" '
                    BEGIN {
                        printf "%.6f", ns / 1000000000
                    }
                '
            )"

            sleep "$sleep_sec"
        done

        now_ns="$(date +%s%N)"

        #######################################################################
        # CPU / SYSTEM
        #######################################################################

        local current_cpu

        current_cpu="$(read_system_stats)"

        read -r \
            prev_user \
            prev_nice \
            prev_system \
            prev_idle \
            prev_iowait \
            prev_irq \
            prev_softirq \
            prev_steal \
            prev_ctxt \
            prev_intr \
            _ \
            _ <<< "$prev_cpu"

        read -r \
            user \
            nice \
            system \
            idle \
            iowait \
            irq \
            softirq \
            steal \
            ctxt \
            intr \
            procs_running \
            procs_blocked <<< "$current_cpu"

        local delta_user
        local delta_nice
        local delta_system
        local delta_idle
        local delta_iowait
        local delta_irq
        local delta_softirq
        local delta_steal
        local delta_total

        delta_user=$((user - prev_user))
        delta_nice=$((nice - prev_nice))
        delta_system=$((system - prev_system))
        delta_idle=$((idle - prev_idle))
        delta_iowait=$((iowait - prev_iowait))
        delta_irq=$((irq - prev_irq))
        delta_softirq=$((softirq - prev_softirq))
        delta_steal=$((steal - prev_steal))

        delta_total=$((
            delta_user +
            delta_nice +
            delta_system +
            delta_idle +
            delta_iowait +
            delta_irq +
            delta_softirq +
            delta_steal
        ))

        if (( delta_total <= 0 )); then
            delta_total=1
        fi

        local cpu_user_pct
        local cpu_system_pct
        local cpu_irq_pct
        local cpu_iowait_pct
        local cpu_steal_pct
        local cpu_idle_pct
        local cpu_used_pct

        cpu_user_pct="$(
            awk -v v="$((delta_user + delta_nice))" \
                -v t="$delta_total" \
                'BEGIN { printf "%.2f", (v/t)*100 }'
        )"

        cpu_system_pct="$(
            awk -v v="$delta_system" \
                -v t="$delta_total" \
                'BEGIN { printf "%.2f", (v/t)*100 }'
        )"

        cpu_irq_pct="$(
            awk -v v="$((delta_irq + delta_softirq))" \
                -v t="$delta_total" \
                'BEGIN { printf "%.2f", (v/t)*100 }'
        )"

        cpu_iowait_pct="$(
            awk -v v="$delta_iowait" \
                -v t="$delta_total" \
                'BEGIN { printf "%.2f", (v/t)*100 }'
        )"

        cpu_steal_pct="$(
            awk -v v="$delta_steal" \
                -v t="$delta_total" \
                'BEGIN { printf "%.2f", (v/t)*100 }'
        )"

        cpu_idle_pct="$(
            awk -v v="$delta_idle" \
                -v t="$delta_total" \
                'BEGIN { printf "%.2f", (v/t)*100 }'
        )"

        cpu_used_pct="$(
            awk -v idle="$cpu_idle_pct" \
                'BEGIN { printf "%.2f", 100 - idle }'
        )"

        #######################################################################
        # Реальное время между двумя замерами
        #######################################################################

        local delta_ns
        local delta_sec

        delta_ns=$((now_ns - prev_ns))

        delta_sec="$(
            awk -v ns="$delta_ns" '
                BEGIN {
                    printf "%.6f", ns / 1000000000
                }
            '
        )"

        #######################################################################
        # MEMORY
        #######################################################################

        read -r \
            mem_total \
            mem_available \
            mem_used \
            swap_total \
            swap_used \
            buffers \
            cached \
            slab \
            kernel_memory \
            shmem <<< "$(read_memory_stats)"

        local mem_used_pct
        local swap_used_pct

        mem_used_pct="$(
            awk \
                -v used="$mem_used" \
                -v total="$mem_total" \
                'BEGIN {
                    if (total > 0)
                        printf "%.2f", used/total*100
                    else
                        printf "0.00"
                }'
        )"

        swap_used_pct="$(
            awk \
                -v used="$swap_used" \
                -v total="$swap_total" \
                'BEGIN {
                    if (total > 0)
                        printf "%.2f", used/total*100
                    else
                        printf "0.00"
                }'
        )"

        #######################################################################
        # LOADAVG
        #######################################################################

        read -r \
            load1 \
            load5 \
            load15 \
            tasks_running \
            tasks_total <<< "$(read_loadavg)"

        #######################################################################
        # CTXT / INTERRUPTS
        #######################################################################

        local ctxt_per_sec
        local intr_per_sec

        ctxt_per_sec="$(
            awk \
                -v value="$((ctxt - prev_ctxt))" \
                -v sec="$delta_sec" \
                'BEGIN { printf "%.2f", value/sec }'
        )"

        intr_per_sec="$(
            awk \
                -v value="$((intr - prev_intr))" \
                -v sec="$delta_sec" \
                'BEGIN { printf "%.2f", value/sec }'
        )"

        #######################################################################
        # DISK
        #######################################################################

        local current_disk

        current_disk="$(read_disk_stats)"

        read -r prev_read_sectors prev_write_sectors <<< "$prev_disk"
        read -r read_sectors write_sectors <<< "$current_disk"

        local disk_read_kbps
        local disk_write_kbps

        disk_read_kbps="$(
            awk \
                -v sectors="$((read_sectors - prev_read_sectors))" \
                -v sec="$delta_sec" \
                'BEGIN {
                    printf "%.2f", (sectors * 512 / 1024) / sec
                }'
        )"

        disk_write_kbps="$(
            awk \
                -v sectors="$((write_sectors - prev_write_sectors))" \
                -v sec="$delta_sec" \
                'BEGIN {
                    printf "%.2f", (sectors * 512 / 1024) / sec
                }'
        )"

        #######################################################################
        # NETWORK (внешние интерфейсы, без lo)
        #######################################################################

        local current_net

        current_net="$(read_network_stats)"

        read -r prev_rx prev_tx <<< "$prev_net"
        read -r rx tx <<< "$current_net"

        local net_rx_kbps
        local net_tx_kbps

        net_rx_kbps="$(
            awk \
                -v bytes="$((rx - prev_rx))" \
                -v sec="$delta_sec" \
                'BEGIN {
                    printf "%.2f", (bytes / 1024) / sec
                }'
        )"

        net_tx_kbps="$(
            awk \
                -v bytes="$((tx - prev_tx))" \
                -v sec="$delta_sec" \
                'BEGIN {
                    printf "%.2f", (bytes / 1024) / sec
                }'
        )"

        #######################################################################
        # NETWORK (loopback)
        #######################################################################

        local current_net_lo

        current_net_lo="$(read_network_lo_stats)"

        read -r prev_lo_rx prev_lo_tx <<< "$prev_net_lo"
        read -r lo_rx lo_tx <<< "$current_net_lo"

        local net_lo_rx_kbps
        local net_lo_tx_kbps

        net_lo_rx_kbps="$(
            awk \
                -v bytes="$((lo_rx - prev_lo_rx))" \
                -v sec="$delta_sec" \
                'BEGIN {
                    printf "%.2f", (bytes / 1024) / sec
                }'
        )"

        net_lo_tx_kbps="$(
            awk \
                -v bytes="$((lo_tx - prev_lo_tx))" \
                -v sec="$delta_sec" \
                'BEGIN {
                    printf "%.2f", (bytes / 1024) / sec
                }'
        )"

        #######################################################################
        # АТРИБУЦИЯ ГЕНЕРАТОРОВ НАГРУЗКИ
        #######################################################################

        local loadgen_cpu_pct
        local loadgen_mem_kb
        local loadgen_disk_write_delta=0
        local loadgen_disk_read_delta=0
        local loadgen_net_delta=0
        local loadgen_disk_write_kbps
        local loadgen_disk_read_kbps
        local loadgen_net_lo_kbps

        loadgen_cpu_pct="$(
            get_pgid_set_cpu_pct \
                "$CPU_PID" "$DISK_WRITE_PID" "$DISK_READ_PID" \
                "$NET_LISTENER_PID" "$NET_SENDER_PID"
        )"

        if (( RAM_FILE_KB > 0 )); then
            loadgen_mem_kb=$RAM_FILE_KB
        else
            loadgen_mem_kb=0
        fi

        if [[ -n "$DISK_WRITE_PID" ]]; then
            loadgen_disk_write_delta="$(
                sample_worker_io_delta disk_write "$DISK_WRITE_PID" write_bytes
            )"
        fi

        if [[ -n "$DISK_READ_PID" ]]; then
            loadgen_disk_read_delta="$(
                sample_worker_io_delta disk_read "$DISK_READ_PID" read_bytes
            )"
        fi

        if [[ -n "$NET_SENDER_PID" ]]; then
            loadgen_net_delta="$(
                sample_worker_io_delta net_sender "$NET_SENDER_PID" wchar
            )"
        fi

        loadgen_disk_write_kbps="$(
            awk -v b="$loadgen_disk_write_delta" -v sec="$delta_sec" \
                'BEGIN { printf "%.2f", (b/1024)/sec }'
        )"

        loadgen_disk_read_kbps="$(
            awk -v b="$loadgen_disk_read_delta" -v sec="$delta_sec" \
                'BEGIN { printf "%.2f", (b/1024)/sec }'
        )"

        loadgen_net_lo_kbps="$(
            awk -v b="$loadgen_net_delta" -v sec="$delta_sec" \
                'BEGIN { printf "%.2f", (b/1024)/sec }'
        )"

        #######################################################################
        # OS-МЕТРИКИ (host за вычетом вклада генераторов)
        #######################################################################

        local os_cpu_used_pct
        local os_cpu_idle_pct
        local os_mem_used_kb
        local os_mem_used_pct
        local os_disk_write_kbps
        local os_disk_read_kbps
        local os_net_lo_rx_kbps
        local os_net_lo_tx_kbps

        # loadgen_cpu_pct — сумма ps %CPU по процессам (шкала 0..NCPU*100,
        # как top/ps считают многоядерную нагрузку), а cpu_used_pct — доля
        # по всему хосту (0..100). Делим на число ядер, чтобы вычитать
        # величины в одной шкале.
        os_cpu_used_pct="$(
            awk -v h="$cpu_used_pct" -v l="$loadgen_cpu_pct" -v n="$NCPU" \
                'BEGIN { v = h - (l / n); if (v < 0) v = 0; printf "%.2f", v }'
        )"

        os_cpu_idle_pct="$(
            awk -v used="$os_cpu_used_pct" \
                'BEGIN { printf "%.2f", 100 - used }'
        )"

        os_mem_used_kb=$((mem_used - loadgen_mem_kb))

        if (( os_mem_used_kb < 0 )); then
            os_mem_used_kb=0
        fi

        os_mem_used_pct="$(
            awk \
                -v used="$os_mem_used_kb" \
                -v total="$mem_total" \
                'BEGIN {
                    if (total > 0)
                        printf "%.2f", used/total*100
                    else
                        printf "0.00"
                }'
        )"

        os_disk_write_kbps="$(
            awk -v h="$disk_write_kbps" -v l="$loadgen_disk_write_kbps" \
                'BEGIN { v = h - l; if (v < 0) v = 0; printf "%.2f", v }'
        )"

        os_disk_read_kbps="$(
            awk -v h="$disk_read_kbps" -v l="$loadgen_disk_read_kbps" \
                'BEGIN { v = h - l; if (v < 0) v = 0; printf "%.2f", v }'
        )"

        os_net_lo_rx_kbps="$(
            awk -v h="$net_lo_rx_kbps" -v l="$loadgen_net_lo_kbps" \
                'BEGIN { v = h - l; if (v < 0) v = 0; printf "%.2f", v }'
        )"

        os_net_lo_tx_kbps="$(
            awk -v h="$net_lo_tx_kbps" -v l="$loadgen_net_lo_kbps" \
                'BEGIN { v = h - l; if (v < 0) v = 0; printf "%.2f", v }'
        )"

        #######################################################################
        # CSV
        #######################################################################

        local timestamp

        timestamp="$(date --iso-8601=seconds)"

        # Первый столбец CSV — целые секунды от начала теста (номер сэмпла:
        # 1, 2, 3 ... DURATION_SEC), не дробное время.
        echo \
"${sample},\
${timestamp},\
${cpu_user_pct},\
${cpu_system_pct},\
${cpu_irq_pct},\
${cpu_iowait_pct},\
${cpu_steal_pct},\
${cpu_idle_pct},\
${cpu_used_pct},\
${load1},\
${load5},\
${load15},\
${tasks_running},\
${tasks_total},\
${mem_total},\
${mem_available},\
${mem_used},\
${mem_used_pct},\
${swap_total},\
${swap_used},\
${swap_used_pct},\
${buffers},\
${cached},\
${slab},\
${kernel_memory},\
${shmem},\
${ctxt_per_sec},\
${intr_per_sec},\
${procs_running},\
${procs_blocked},\
${disk_read_kbps},\
${disk_write_kbps},\
${net_rx_kbps},\
${net_tx_kbps},\
${net_lo_rx_kbps},\
${net_lo_tx_kbps},\
${loadgen_cpu_pct},\
${loadgen_mem_kb},\
${loadgen_disk_write_kbps},\
${loadgen_disk_read_kbps},\
${loadgen_net_lo_kbps}" >> "$host_csv"

        echo \
"${sample},\
${timestamp},\
${cpu_user_pct},\
${cpu_system_pct},\
${cpu_irq_pct},\
${cpu_iowait_pct},\
${cpu_steal_pct},\
${os_cpu_idle_pct},\
${os_cpu_used_pct},\
${load1},\
${load5},\
${load15},\
${tasks_running},\
${tasks_total},\
${mem_total},\
${mem_available},\
${os_mem_used_kb},\
${os_mem_used_pct},\
${swap_total},\
${swap_used},\
${swap_used_pct},\
${buffers},\
${cached},\
${slab},\
${kernel_memory},\
${shmem},\
${ctxt_per_sec},\
${intr_per_sec},\
${procs_running},\
${procs_blocked},\
${os_disk_read_kbps},\
${os_disk_write_kbps},\
${net_rx_kbps},\
${net_tx_kbps},\
${os_net_lo_rx_kbps},\
${os_net_lo_tx_kbps}" >> "$os_csv"

        #######################################################################
        # PREVIOUS
        #######################################################################

        prev_cpu="$current_cpu"
        prev_disk="$current_disk"
        prev_net="$current_net"
        prev_net_lo="$current_net_lo"
        prev_ns="$now_ns"

        printf "\r[%3d/%3d] %s CPU=%s%% (loadgen=%s%%) RAM=%s%%" \
            "$sample" \
            "$DURATION_SEC" \
            "$timestamp" \
            "$cpu_used_pct" \
            "$loadgen_cpu_pct" \
            "$mem_used_pct"
    done

    echo
}

###############################################################################
# MAIN
###############################################################################

usage()
{
    cat <<EOF

Использование:

    $0 idle
    $0 load

Режимы:

    idle
        Только сбор системных метрик в течение ${DURATION_SEC} секунд,
        без запуска генераторов нагрузки.
        Пишет idle_host.csv и idle_os.csv (совпадают, вклад генераторов
        нулевой, но схема файлов та же, что и в режиме load).

    load
        Запуск нагрузки на CPU/RAM/диск/сеть (loopback) стандартными
        утилитами ОС (dd, timeout, nc) и сбор тех же метрик в течение
        ${DURATION_SEC} секунд.
        Пишет load_host.csv (вся нагрузка на хосте, включая сами
        генераторы) и load_os.csv (то же самое за вычетом вклада
        генераторов — приблизительно "остальная система").

Виды нагрузки (ENABLE_CPU_LOAD / ENABLE_MEM_LOAD / ENABLE_DISK_LOAD /
ENABLE_NET_LOAD) — статичная нагрузка, без вкл/выкл-циклов:

    CPU     непрерывный dd на nproc*LOAD_TARGET_PERCENT/100 ядер (100%
            на каждом из них, остальные ядра простаивают)
    RAM     разовое выделение MEM_LOAD_PERCENT от MemTotal в /dev/shm
    Диск    два непрерывных dd (чтение и запись) в DISK_TEST_FILE
    Сеть    nc + dd на loopback, 127.0.0.1:\$NET_LOOPBACK_PORT

EOF
}

main()
{
    if [[ $# -ne 1 ]]; then
        usage
        exit 1
    fi

    local mode="$1"
    local host_csv
    local os_csv

    check_common_dependencies
    detect_disk_device

    case "$mode" in

        idle)
            host_csv="$IDLE_HOST_CSV"
            os_csv="$IDLE_OS_CSV"

            echo "MODE: IDLE"
            ;;

        load)
            host_csv="$LOAD_HOST_CSV"
            os_csv="$LOAD_OS_CSV"

            echo "MODE: LOAD"

            check_load_dependencies

            start_cpu_load
            start_mem_load
            start_disk_load
            start_net_load

            # Небольшая пауза только чтобы процессы успели стартовать.
            sleep 1
            ;;

        *)
            usage
            exit 1
            ;;
    esac

    write_csv_header "$host_csv" "host"
    write_csv_header "$os_csv" "os"

    collect_metrics "$host_csv" "$os_csv"

    echo
    echo "Готово."
    echo "Host CSV: $host_csv"
    echo "OS CSV:   $os_csv"
}

main "$1"
