#!/usr/bin/env bash
set -euo pipefail

REGISTRY="allta.devos.astralinux.ru:21503"   # <-- УКАЖИ СВОЙ РЕГИСТРИ
BASE_FILE="base_images.txt"

if [[ $# -ne 1 ]]; then
    echo "Использование:"
    echo "  ./up_images.sh base"
    echo "  ./up_images.sh python:3.12"
    exit 1
fi

MODE="$1"

log() {
    echo "[$(date +'%H:%M:%S')] $1"
}

process_image() {
    local IMAGE="$1"

    if [[ -z "$IMAGE" ]]; then
        return
    fi

    # убираем пробелы
    IMAGE="$(echo "$IMAGE" | xargs)"

    # пропускаем комментарии
    [[ "$IMAGE" =~ ^# ]] && return

    log "Pull $IMAGE"
    docker pull "$IMAGE"

    # формируем имя в нашем registry
    # пример:
    # python:3.12 → registry.local/python:3.12
    TARGET_IMAGE="${REGISTRY}/${IMAGE}"

    log "Tag → $TARGET_IMAGE"
    docker tag "$IMAGE" "$TARGET_IMAGE"

    log "Push → $TARGET_IMAGE"
    docker push "$TARGET_IMAGE"

    log "Remove local images"
    docker rmi "$TARGET_IMAGE" || true
    docker rmi "$IMAGE" || true

    log "Done: $IMAGE"
    echo
}

if [[ "$MODE" == "base" ]]; then

    if [[ ! -f "$BASE_FILE" ]]; then
        echo "Файл $BASE_FILE не найден"
        exit 1
    fi

    log "Читаю $BASE_FILE"

    while IFS= read -r IMAGE; do
        process_image "$IMAGE"
    done < "$BASE_FILE"

else
    process_image "$MODE"
fi

log "Все операции завершены"
