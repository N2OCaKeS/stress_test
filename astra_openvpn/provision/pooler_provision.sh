#!/bin/bash

INTERFACE="eth1"
IP="172.16.0.2"  # Пример статического IP (должен быть вне DHCP-диапазона!)
NETMASK="255.255.128.0"
GATEWAY="172.16.0.1"  # Адрес сервера (из XML)

# Проверка интерфейса
if ! ip link show $INTERFACE > /dev/null 2>&1; then
    echo "Интерфейс $INTERFACE не найден!"
    ip link show
    exit 1
fi

# Статическая настройка
ip addr add $IP/$NETMASK dev $INTERFACE
ip link set $INTERFACE up
ip route add default via $GATEWAY dev $INTERFACE

# Проверка
echo "Настройки $INTERFACE:"
ip a show $INTERFACE
echo "Маршруты:"
ip route show
