#!/bin/bash

BRIDGE="br0"
VM="$1"

TMP_XML="/tmp/${VM}_bridge.xml"

# Остановить ВМ если работает
STATE=$(virsh -c qemu:///system domstate "$VM" 2>/dev/null)
if [[ "$STATE" != "выключен" ]]; then
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
awk -v bridge="$BRIDGE" '
BEGIN {ins=0}
/<devices>/ {
    print
    print "    <interface type=\"bridge\">"
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




