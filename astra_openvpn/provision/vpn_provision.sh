#!/bin/bash

# Настройка интерфейса (имя может быть eth1, ens7 и т.д.)
INTERFACE="eth1"
IP="192.168.100.2"
NETMASK="255.255.255.0"
GATEWAY="192.168.100.1"

# Проверяем, есть ли интерфейс
if ! ip link show $INTERFACE > /dev/null 2>&1; then
    echo "Интерфейс $INTERFACE не найден! Доступные интерфейсы:"
    ip link show
    exit 1
fi

# Назначаем IP
ip addr add $IP/$NETMASK dev $INTERFACE || true
ip link set $INTERFACE up

# Добавляем маршрут (если нужно)
ip route add default via $GATEWAY dev $INTERFACE || true

# Проверяем
echo "Интерфейс $INTERFACE настроен:"
ip a show $INTERFACE
