# Базовый image для CronJob'а автоматической ротации секретов.
#
# Запускает bash-скрипты scripts/k8s/rotate_*.sh + test_rotation_safety.sh
# in-cluster (через SA-токен). Image НЕ содержит самих скриптов — они
# приезжают в pod через ConfigMap rotation-scripts (см.
# k8s/121-rotation-cronjob.yaml, k8s/kustomization.yaml configMapGenerator).
#
# Что нужно из бинарников (по итогам аудита scripts/k8s/rotate_*.sh):
#   bash       — все скрипты #!/usr/bin/env bash, set -euo pipefail
#   kubectl    — патчи Secret'ов, exec в postgres/redis, rollout restart
#   jq         — парсинг JSON Secret'ов и kubectl get -o json
#   openssl    — генерация новых ключей/паролей (rand -hex / -base64)
#   curl       — дёрганье /reencrypt_outbox/seed и /migration_status в pod'ах сервисов
#   coreutils  — date, base64, mktemp (Alpine BusyBox-версии хватает)
#   findutils  — find используется в test_rotation_safety.sh для бэкап-каталогов
#
# Base — alpine:3.20: ~7 МБ, kubectl ~50 МБ. Image целиком ~80 МБ.
#
# Версия kubectl фиксируется через ARG KUBECTL_VERSION (см. Make-target
# k8s-rotation-image). Версию надо держать совместимой с серверной частью k3s
# (skew ±1 minor, см. https://kubernetes.io/releases/version-skew-policy/).

FROM alpine:3.20

ARG KUBECTL_VERSION=v1.30.5

RUN apk add --no-cache \
        bash \
        jq \
        openssl \
        curl \
        ca-certificates \
        coreutils \
        findutils \
        tzdata \
    && curl -fsSL -o /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && kubectl version --client=true --output=yaml

# В closed-network инсталляциях kubectl можно положить рядом с Dockerfile и
# COPY-ить файлом — но базовая сборка идёт онлайн с dl.k8s.io. Если нужна
# офлайн-сборка, замени RUN на:
#   COPY kubectl /usr/local/bin/kubectl
#   RUN chmod +x /usr/local/bin/kubectl

# Скрипты приедут через volumeMount из ConfigMap; рабочая директория
# фиксируется чтобы относительные пути в скриптах не сюрпризили.
WORKDIR /scripts

# CronJob переопределит command/args, но default — показ списка доступных
# скриптов, на случай если кто-то запустит `docker run dbos/tools:...` руками.
CMD ["bash", "-c", "ls -la /scripts/ 2>/dev/null || echo 'mount scripts to /scripts'"]
