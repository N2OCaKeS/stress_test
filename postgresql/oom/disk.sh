#!/bin/bash
set -e

echo "=== Расширение диска ==="

# Устанавливаем необходимые утилиты
sudo apt update
sudo apt install cloud-guest-utils -y

# Определяем физический диск и раздел, на котором находится LVM
# Находим физический раздел, используемый в LVM
PV_DEV=$(sudo pvs --noheadings -o pv_name | head -1 | xargs)
echo "Physical Volume: $PV_DEV"

# Из /dev/sda получаем диск (/dev/sda) и номер раздела 
DISK=$(echo $PV_DEV | sed 's/[0-9]*$//')
PART_NUM=$(echo $PV_DEV | grep -o '[0-9]*$')
echo "Диск: $DISK, номер раздела: $PART_NUM"

# Расширяем физический раздел
if [ -n "$PART_NUM" ]; then
    sudo growpart $DISK $PART_NUM || true
else
    echo "Не удалось определить номер раздела"
    exit 1
fi

# Расширяем Physical Volume в LVM
sudo pvresize $PV_DEV

# Расширяем Logical Volume с корнем
sudo lvextend -l +100%FREE /dev/mapper/VG712-lv_root

# Расширяем файловую систему
sudo resize2fs /dev/mapper/VG712-lv_root

echo "=== Результат ==="
df -h /
echo "=== Расширение завершено ==="
