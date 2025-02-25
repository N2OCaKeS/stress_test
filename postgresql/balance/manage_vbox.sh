#!/bin/bash

# Проверяем, передан ли аргумент
if [ -z "$1" ]; then
    echo "Использование: $0 {snapshot_new|snapshot_delete|snapshot_restore|snapshot_list|poweroff|delete}"
    exit 1
fi

# Получаем список всех ВМ
VMS=$(VBoxManage list vms | awk -F '"' '{print $2}')

if [ -z "$VMS" ]; then
    echo "Виртуальные машины не найдены."
    exit 1
fi

# Функция для подсветки текста
highlight() {
    local text="$1"
    local color="$2"
    case "$color" in
    "red") echo -e "\033[31m$text\033[0m" ;;          # красный
    "green") echo -e "\033[32m$text\033[0m" ;;        # зеленый
    "yellow") echo -e "\033[33m$text\033[0m" ;;       # желтый
    "blue") echo -e "\033[34m$text\033[0m" ;;         # синий
    "magenta") echo -e "\033[35m$text\033[0m" ;;      # маджента
    "cyan") echo -e "\033[36m$text\033[0m" ;;         # циан
    "white") echo -e "\033[37m$text\033[0m" ;;        # белый
    "gray") echo -e "\033[90m$text\033[0m" ;;         # серый
    "purple") echo -e "\033[35m$text\033[0m" ;;       # пурпурный
    "orange") echo -e "\033[38;5;214m$text\033[0m" ;; # оранжевый
    "pink") echo -e "\033[38;5;13m$text\033[0m" ;;    # розовый
    *) echo "$text" ;;                                # без подсветки
    esac
}

# Функция для получения статуса ВМ
get_vm_status() {
    VM=$1
    STATUS=$(VBoxManage showvminfo "$VM" --machinereadable | grep -i '^VMState=' | cut -d= -f2 | tr -d '"')

    # Добавляем проверку на возможные статусы
    case "$STATUS" in
    "running") echo "running" ;;
    "poweroff") echo "poweroff" ;;
    "paused") echo "paused" ;;
    "saved") echo "saved" ;;
    "") echo "unknown" ;; # Если статус не найден, выводим unknown
    *) echo "unknown" ;;  # Если статус не совпадает с ожидаемыми, выводим unknown
    esac
}

# Функция для вывода таблицы статусов ВМ
print_vm_statuses() {


    # Получаем список всех ВМ
    VMS=$(VBoxManage list vms | awk -F '"' '{print $2}')

    if [ -z "$VMS" ]; then
        echo "Виртуальные машины не найдены."
        exit 1
    fi

    # Вывод заголовков таблицы
    echo "Статусы виртуальных машин:"
    echo "-------------------------------------------------------------"
    printf "%-30s %-10s\n" "Название ВМ" "Статус"
    echo "-------------------------------------------------------------"

    # Проходим по списку ВМ и выводим их статус
    for VM in $VMS; do
        STATUS=$(get_vm_status "$VM")

        # Сортировка по цветам в зависимости от статуса
        case "$STATUS" in
        "running") COLOR="green" ;;
        "poweroff") COLOR="yellow" ;;
        "paused") COLOR="cyan" ;;
        "saved") COLOR="magenta" ;;
        "unknown") COLOR="red" ;;
        esac

        # Выводим название ВМ и её статус с подсветкой
        printf "%-30s %s\n" "$VM" "$(highlight "$STATUS" "$COLOR")"
    done
}

# Проверяем статус ВМ
if [ "$1" == "status" ]; then
    print_vm_statuses
    exit 0
fi

# Работа со снимками или управление ВМ
if [ "$1" == "snapshot_list" ]; then
    for VM in $VMS; do
        echo "Список снимков для ВМ: $VM"

        SNAPSHOTS=($(VBoxManage snapshot "$VM" list --machinereadable | grep '^SnapshotName' | cut -d '=' -f2 | tr -d '"'))

        if [ ${#SNAPSHOTS[@]} -eq 0 ]; then
            echo "Нет снимков для ВМ $VM"
        else
            # Для каждой ВМ печатаем список снимков с подсветкой
            for i in "${!SNAPSHOTS[@]}"; do
                case $((i % 10)) in
                0) COLOR="red" ;;
                1) COLOR="green" ;;
                2) COLOR="yellow" ;;
                3) COLOR="blue" ;;
                4) COLOR="magenta" ;;
                5) COLOR="cyan" ;;
                6) COLOR="white" ;;
                7) COLOR="gray" ;;
                8) COLOR="purple" ;;
                9) COLOR="orange" ;;
                esac
                # Выводим номер снимка с подсветкой
                highlight "$((i + 1)). ${SNAPSHOTS[$i]}" "$COLOR"
            done
        fi
    done
    exit 0
fi

if [ "$1" == "snapshot_restore" ]; then
    # Выводим список снимков для первой ВМ
    FIRST_VM=$(echo $VMS | awk '{print $1}')
    SNAPSHOTS=($(VBoxManage snapshot "$FIRST_VM" list --machinereadable | grep '^SnapshotName' | cut -d '=' -f2 | tr -d '"'))

    if [ ${#SNAPSHOTS[@]} -eq 0 ]; then
        echo "Нет доступных снимков для ВМ $FIRST_VM."
        exit 1
    fi

    # Печатаем список снимков с номерами
    echo "Список снимков для всех ВМ:"
    for i in "${!SNAPSHOTS[@]}"; do
        echo "[$((i + 1))] ${SNAPSHOTS[$i]}"
    done

    # Запрашиваем номер снимка для восстановления
    echo "Введите номер снимка для восстановления:"
    read SNAPSHOT_INDEX

    if [ "$SNAPSHOT_INDEX" -le 0 ] || [ "$SNAPSHOT_INDEX" -gt "${#SNAPSHOTS[@]}" ]; then
        echo "Ошибка: Неверный номер снимка."
        exit 1
    fi

    SELECTED_SNAPSHOT=${SNAPSHOTS[$((SNAPSHOT_INDEX - 1))]}

    # Применяем восстановление ко всем ВМ
    for VM in $VMS; do
        VBoxManage snapshot "$VM" restore "$SELECTED_SNAPSHOT"
        echo "ВМ $VM восстановлена к снимку: $SELECTED_SNAPSHOT"
    done
    exit 0
fi

if [ "$1" == "snapshot_delete" ]; then
    # Выводим список снимков для первой ВМ
    FIRST_VM=$(echo $VMS | awk '{print $1}')
    SNAPSHOTS=($(VBoxManage snapshot "$FIRST_VM" list --machinereadable | grep '^SnapshotName' | cut -d '=' -f2 | tr -d '"'))

    if [ ${#SNAPSHOTS[@]} -eq 0 ]; then
        echo "Нет доступных снимков для ВМ $FIRST_VM."
        exit 1
    fi

    # Печатаем список снимков с номерами
    echo "Список снимков для всех ВМ:"
    for i in "${!SNAPSHOTS[@]}"; do
        echo "[$((i + 1))] ${SNAPSHOTS[$i]}"
    done

    # Запрашиваем номер снимка для удаления
    echo "Введите номер снимка для удаления:"
    read SNAPSHOT_INDEX

    if [ "$SNAPSHOT_INDEX" -le 0 ] || [ "$SNAPSHOT_INDEX" -gt "${#SNAPSHOTS[@]}" ]; then
        echo "Ошибка: Неверный номер снимка."
        exit 1
    fi

    SELECTED_SNAPSHOT=${SNAPSHOTS[$((SNAPSHOT_INDEX - 1))]}

    # Удаляем выбранный снимок для всех ВМ
    for VM in $VMS; do
        VBoxManage snapshot "$VM" delete "$SELECTED_SNAPSHOT"
        echo "Для ВМ $VM удален снимок: $SELECTED_SNAPSHOT"
    done
    exit 0
fi

if [ "$1" == "snapshot_new" ]; then
    echo "Введите имя нового снимка (оставьте пустым для имени по умолчанию):"
    read CUSTOM_SNAPSHOT_NAME
    if [ -z "$CUSTOM_SNAPSHOT_NAME" ]; then
        CUSTOM_SNAPSHOT_NAME="snapshot_$(date +%Y%m%d_%H%M%S)"
    fi
    for VM in $VMS; do
        VBoxManage snapshot "$VM" take "$CUSTOM_SNAPSHOT_NAME" --live
        echo "Создан новый снимок: $CUSTOM_SNAPSHOT_NAME для ВМ: $VM"
    done
    exit 0
fi

for VM in $VMS; do
    echo "Обрабатывается ВМ: $VM"
    case "$1" in
    poweroff)
        VBoxManage controlvm "$VM" poweroff
        echo "Выключена ВМ: $VM"
        ;;
    poweron)
        VBoxManage startvm "$VM" --type headless
        echo "Включена ВМ: $VM"
        ;;
    delete)
        VBoxManage controlvm "$VM" poweroff
        VBoxManage unregistervm "$VM" --delete
        echo "Удалена ВМ: $VM"
        ;;
    status) ;;
    *)
        echo "Неизвестная команда: $1"
        exit 1
        ;;
    esac
done
