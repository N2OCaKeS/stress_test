#!/bin/bash
set -e  # Останавливаемся при ошибке

echo "=== Расширение диска ==="

# Устанавливаем необходимые утилиты
sudo apt update
sudo apt install cloud-guest-utils -y

# Определяем корневой раздел
ROOT_DEV=$(df / | awk 'NR==2 {print $1}')
echo "Корневой раздел: $ROOT_DEV"

# Определяем основной диск (убираем последнюю цифру из /dev/sda3 → /dev/sda)
DISK=$(echo $ROOT_DEV | sed 's/[0-9]*$//')
PART_NUM=$(echo $ROOT_DEV | grep -o '[0-9]*$')
echo "Диск: $DISK, номер раздела: $PART_NUM"

# Расширяем раздел
sudo growpart $DISK $PART_NUM || true

# Если используется LVM
if command -v pvresize &> /dev/null; then
    # Определяем physical volume
    PV=$(sudo pvs --noheadings -o pv_name | head -1 | xargs)
    if [ -n "$PV" ]; then
        sudo pvresize $PV
        
        # Расширяем logical volume с корнем
        LV=$(sudo lvs --noheadings -o lv_name,lv_attr | grep '^-' | head -1 | awk '{print $1}')
        VG=$(sudo vgs --noheadings -o vg_name | head -1 | xargs)
        
        if [ -n "$LV" ] && [ -n "$VG" ]; then
            sudo lvextend -l +100%FREE /dev/$VG/$LV
            sudo resize2fs /dev/$VG/$LV || sudo xfs_growfs /
        fi
    fi
fi

echo "=== Результат ==="
df -h /
echo "=== Расширение завершено ==="
