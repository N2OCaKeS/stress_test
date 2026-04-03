#!/bin/bash

# ============================================
# Скрипт для тестирования XFS на /dev/vdb
# ============================================

DISK="/dev/vdb"
MOUNT_POINT="/opt"
SRC_DIR="$MOUNT_POINT/src"
DST_DIR="$MOUNT_POINT/dest"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}=== Настройка XFS на $DISK ===${NC}"

# Проверка, не смонтирован ли диск
if mount | grep -q "$DISK"; then
    echo "Диск $DISK уже смонтирован, размонтирую..."
    sudo umount $DISK
fi

# Форматирование в XFS
echo -e "${YELLOW}Форматирование $DISK в XFS...${NC}"
sudo mkfs.xfs -f $DISK

# Создание точки монтирования
sudo mkdir -p $MOUNT_POINT

# Монтирование
echo -e "${YELLOW}Монтирование в $MOUNT_POINT...${NC}"
sudo mount $DISK $MOUNT_POINT

# Права доступа
sudo chown -R $(whoami):$(whoami) $MOUNT_POINT

# Создание каталогов
mkdir -p $SRC_DIR $DST_DIR

echo -e "${GREEN}Готово! Диск смонтирован.${NC}"
df -h $MOUNT_POINT

# ============================================
# Генерация файлов 
# ============================================

echo ""
echo -e "${GREEN}=== Генерация файлов в $SRC_DIR ===${NC}"

cd $SRC_DIR

# Вариант 1: Использовать seq для чисел без ведущих нулей
for i in $(seq 0 1000); do
    printf -v filename "file%04d.bin" $i
    dd if=/dev/urandom of=$filename bs=10M count=1 status=none 2>/dev/null
    if [ $((i % 100)) -eq 0 ]; then
        echo "Создано файлов: $i"
    fi
done

# Или Вариант 2: Проще, без форматирования номера
# for i in {0..1000}; do
#     dd if=/dev/urandom of=file${i}.bin bs=10M count=1 status=none 2>/dev/null
#     if [ $((i % 100)) -eq 0 ]; then
#         echo "Создано файлов: $i"
#     fi
# done

echo -e "${GREEN}Создано 1001 файл!${NC}"
du -sh $SRC_DIR

# ============================================
# Копирование файлов
# ============================================

echo ""
echo -e "${GREEN}=== Копирование файлов из src в dest ===${NC}"

# Очищаем dest перед копированием (опционально)
rm -f $DST_DIR/*.bin 2>/dev/null

# Копирование
for file in $SRC_DIR/file*.bin; do
    cp "$file" $DST_DIR/
done

# Или проще одной командой:
# cp $SRC_DIR/*.bin $DST_DIR/

echo -e "${GREEN}Копирование завершено!${NC}"
echo "Файлов в src:  $(ls $SRC_DIR/*.bin 2>/dev/null | wc -l)"
echo "Файлов в dest: $(ls $DST_DIR/*.bin 2>/dev/null | wc -l)"
df -h $MOUNT_POINT

echo ""
echo -e "${GREEN}=== Всё готово! ===${NC}"
