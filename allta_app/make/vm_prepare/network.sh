#!/bin/bash

# Использование: ./recreate_bridge_with_mac.sh <bridge_name>
# Пример: ./recreate_bridge_with_mac.sh br0

declare -A vm_mac_map=(
    ["virtual-station1"]="080027ABCD01"
    ["virtual-station2"]="080027ABCD02"
    ["virtual-station3"]="080027ABCD03"
    ["virtual-station4"]="080027ABCD04"
)

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
        virsh -c qemu:///system -c qemu:///system destroy "$VM"
        while [[ $(virsh -c qemu:///system -c qemu:///system domstate "$VM" 2>/dev/null) != "shut off" ]]; do
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



