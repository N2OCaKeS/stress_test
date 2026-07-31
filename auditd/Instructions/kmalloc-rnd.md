# Инструкция по воспроизведению утечки kmalloc-rnd в подсистеме аудита

## Окружение

- Ядро: 6.12
- Условие: активный аудит, нагрузка 1000-5000 событий/сек

## Шаг 1. Подготовка
Загрузить модуль slub_debug для доступа к slabinfo
```bash
sudo modprobe slub_debug 2>/dev/null
```

Если модуля нет — включить через параметр ядра:
`Добавить в /etc/default/grub: GRUB_CMDLINE_LINUX="slub_debug=U"`
```bash
sudo update-grub && sudo reboot
```

Смонтировать debugfs для доступа к статистике slab
```bash
sudo mount -t debugfs none /sys/kernel/debug 2>/dev/null
```

## Шаг 2. Включение аудита и добавление правил

sudo auditctl -D
sudo auditctl -a exit,always -F arch=b64 -S open,openat,close,stat
sudo auditctl -e 1
sudo auditctl -l
sudo auditctl -s | grep enabled

## Шаг 3. Запуск генератора событий

Вариант А (высокая нагрузка, если audit_rate меньше 1000):

```bash
for i in {1..12}; do
    while true; do find / -maxdepth 3 -type f -exec stat {} \; >/dev/null 2>&1; done &
done
```

Вариант Б (умеренная нагрузка, если audit_rate больше 5000).
Сначала остановить процессы варианта А:

```bash
pkill -f "find / -maxdepth"
```

Затем запустить:

```bash
for i in {1..4}; do
    while true; do
        find /etc -maxdepth 3 -type f -exec stat {} \; >/dev/null 2>&1
        sleep 0.01
    done &
done
```

## Шаг 4. Мониторинг

Запустить в отдельном терминале:

```bash
cat > /tmp/monitor.sh << 'EOF'
#!/bin/bash
echo "=== Аудит ==="
echo "Статус: $(sudo auditctl -s 2>/dev/null | grep enabled)"
events=$(sudo ausearch --start 10-seconds-ago 2>/dev/null | wc -l)
echo "Событий за 10 сек: $events"
echo ""
echo "=== SUnreclaim ==="
grep SUnreclaim /proc/meminfo
echo ""
echo "=== kmalloc-rnd (суммарно) ==="

total_objects=0
total_kb=0

for cache in /sys/kernel/slab/kmalloc-rnd-*; do
    if [ -f "$cache/objects" ]; then
        objects=$(sudo cat "$cache/objects" 2>/dev/null)
        total_objects=$((total_objects + objects))
    fi
done

# Получаем размер объекта из любого кэша 4k
if [ -f /sys/kernel/slab/kmalloc-rnd-01-4k/object_size ]; then
    obj_size=$(sudo cat /sys/kernel/slab/kmalloc-rnd-01-4k/object_size)
    total_kb=$((total_objects * obj_size / 1024))
fi

echo "Всего объектов: $total_objects"
echo "Размер объекта: ${obj_size:-?} bytes"
echo "Суммарно памяти: ${total_kb} KB"

echo ""
echo "=== Топ-5 кэшей по объектам ==="
for cache in /sys/kernel/slab/kmalloc-rnd-*; do
    if [ -f "$cache/objects" ]; then
        objects=$(sudo cat "$cache/objects" 2>/dev/null)
        cache_name=$(basename "$cache")
        echo "$objects $cache_name"
    fi
done | sort -rn | head -5
EOF

chmod +x /tmp/monitor.sh
watch -n 10 /tmp/monitor.sh
```

Настройка нагрузки:

- Если audit_rate меньше 1000: увеличить потоки с 12 до 16 или 24
- Если audit_rate больше 5000: уменьшить потоки с 12 до 8 или добавить sleep 0.05

## Шаг 5. Наблюдение

Ожидаемый результат при подтверждении бага:

- SUnreclaim линейно растёт со временем
- Количество объектов в kmalloc-rnd стабильно увеличивается
- Рост не прекращается при неизменной нагрузке

Пример через час работы:

SUnreclaim:      27310592 kB    (значительно выросло)
kmalloc-rnd         853456  27310592  (объекты растут непрерывно)

## Шаг 6. Остановка и очистка

```bash
pkill -f "find / -maxdepth"
pkill -f "find /etc -maxdepth"
sudo auditctl -D
sudo auditctl -e 0
sudo systemctl stop auditd
```

## Краткая справка

sudo auditctl -s                      — статистика аудита (audit_rate = событий/сек)
grep SUnreclaim /proc/meminfo         — объём несбрасываемой памяти slab
sudo grep "^kmalloc-rnd" /proc/slabinfo — объекты и размер проблемного кэша
pkill -f "find /"                     — остановить генераторы событий
sudo auditctl -e 0                    — экстренно отключить аудит



## Воспроизведение одним скриптом

```bash
#!/bin/bash

sudo systemctl start auditd 2>/dev/null
sudo auditctl -D 2>/dev/null
sudo auditctl -a exit,always -F arch=b64 -S open,openat,close,stat -k leak_test 2>/dev/null
sudo auditctl -e 1 2>/dev/null

start_sunreclaim=$(grep SUnreclaim /proc/meminfo | awk '{print $2}')
start_time=$(date +%s)

echo "=== Старт: $(date) ==="
echo "SUnreclaim начальный: ${start_sunreclaim} KB"
echo ""

#for i in {1..24}; do
#    while true; do find / -maxdepth 5 -type f -exec stat {} \; >/dev/null 2>&1; done &
#done

for i in {1..96}; do
    while true; do find /etc -type f -exec stat {} \; >/dev/null 2>&1; done &
done

echo "Генераторы запущены, ждём 1 минуту..."
sleep 60

while true; do
    current=$(grep SUnreclaim /proc/meminfo | awk '{print $2}')
    elapsed=$((($(date +%s) - start_time) / 60))
    growth=$((current - start_sunreclaim))
    echo "$(date +%H:%M:%S) | +${elapsed} мин | SUnreclaim: ${current} KB (+${growth} KB)"
    sleep 60
done
```

