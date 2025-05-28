#!/bin/bash

# Использование: ./recreate_bridge_with_mac.sh <bridge_name>
# Пример: ./recreate_bridge_with_mac.sh br0

declare -A vm_mac_map=(
    ["virtual-station1"]="08:00:27:AB:CD:01"
    ["virtual-station2"]="08:00:27:AB:CD:02"
    ["virtual-station3"]="08:00:27:AB:CD:03"
    ["virtual-station4"]="08:00:27:AB:CD:04"
    ["work-station1"]="08:00:27:AB:CD:05"
    ["work-station2"]="08:00:27:AB:CD:06"
)

BRIDGE="br0"
PHY_IF="eth2"
echo "[*] Создаём /etc/network/interfaces для bridge $BRIDGE ..."

sudo cp /etc/network/interfaces /etc/network/interfaces.bak
sudo tee /etc/network/interfaces > /dev/null <<EOF
auto lo
iface lo inet loopback

auto $BRIDGE
iface $BRIDGE inet static
    address 10.177.103.205
    netmask 255.255.255.0
    gateway 10.177.103.254
    dns-nameservers 10.177.128.198 10.177.180.246 10.177.181.142
    bridge_ports $PHY_IF
    bridge_stp off
    bridge_fd 0
    bridge_maxwait 0

iface $PHY_IF inet manual
EOF

echo "[*] Применяем новые сетевые настройки..."

# Отключаем старую сеть, поднимаем мост
sudo ifdown $PHY_IF || true
sudo ifdown $BRIDGE || true
sudo ifup $BRIDGE


echo "[+] Сеть перезапущена. Проверь IP: ip a show $BRIDGE"


BRIDGE=$1

if [[ -z "$BRIDGE" ]]; then
    echo "Использование: $0 <bridge_name>"
    exit 1
fi

for VM in "${!vm_mac_map[@]}"; do
    MAC="${vm_mac_map[$VM]}"
    echo "============================"
    echo "[*] Работаем с ВМ: $VM (MAC: $MAC)"

    TMP_XML="/tmp/${VM}_bridge.xml"

    # Остановить ВМ если работает
    STATE=$(virsh -c qemu:///system domstate "$VM" 2>/dev/null)
    if [[ "$STATE" != "shut off" ]]; then
        echo "  ...Останавливаем $VM"
        virsh -c qemu:///system destroy "$VM"
        while [[ $(virsh -c qemu:///system domstate "$VM" 2>/dev/null) != "выключен" ]]; do
            echo "    ...Ожидание выключения $VM"
            sleep 2
        done
    fi

    # Экспортируем XML-конфиг
    virsh -c qemu:///system dumpxml "$VM" > "$TMP_XML"

    # Удаляем все interface секции и вставляем новый bridge-интерфейс с нужным MAC
    awk -v mac="$MAC" -v bridge="$BRIDGE" '
    BEGIN {ins=0}
    /<devices>/ {
        print
        print "    <interface type=\"bridge\">"
        print "      <mac address=\""mac"\"/>"
        print "      <source bridge=\""bridge"\"/>"
        print "      <model type=\"virtio\"/>"
        print "      <address type=\"pci\" domain=\"0x0000\" bus=\"0x01\" slot=\"0x00\" function=\"0x0\"/>"
        print "    </interface>"
        ins=1
        next
    }
    /^ *<interface /, /^ *<\/interface>/ {next}
    {print}
    ' "$TMP_XML" > "${TMP_XML}.mod"

    # Обновляем определение ВМ
    virsh -c qemu:///system define "${TMP_XML}.mod"

    # Запускаем обратно ВМ
    virsh -c qemu:///system start "$VM"

    echo "[+] $VM теперь использует только bridge $BRIDGE с MAC $MAC."
done

echo "============================"
echo "[!] Массовое пересоздание сети завершено."



