# Docker Registry

## Описание

Этот каталог содержит проект разворачивания приватного Docker Registry с web UI.

Состав:

- `docker-registry` - сам registry (`registry:2`) с TLS и token auth через `allta_auth`
- `docker-registry-ui` - web UI (`joxit/docker-registry-ui`)
- `docker-registry-ui-gateway` - nginx-gateway для UI с авторизацией через `allta_auth`

TLS-сертификат общий для всех сервисов и берётся из `/var/allta_services/certs` (`allta-api.crt`/`allta-api.key`).

Оркестрация выполняется через `docker/docker-compose.yml`, а lifecycle через `install.sh`.

## Требования

- Linux-хост с `sudo`
- установленный Docker Engine
- установленный `docker-compose` (CLI-команда `docker-compose`)

Примечание: `install.sh precond` включает/запускает `docker.service` и добавляет текущего пользователя в группу `docker`.

## Быстрый старт

1. Перейти в каталог проекта:

   ```bash
   cd docker_registry
   ```

2. Подготовить окружение и systemd unit:

   ```bash
   sudo ./install.sh precond
   ```

   Если текущий пользователь ранее не состоял в группе `docker`, перелогиньтесь в shell-сессии перед запуском Docker-команд без `sudo`.

3. Отредактировать env-файлы:

   ```bash
   cd /var/allta_services/config
   ls -1 env.docker_registry*
   ```

4. Запустить сервис:

   ```bash
   sudo systemctl start docker_registry.service
   ```

5. (Опционально) включить автозапуск:

   ```bash
   sudo systemctl enable docker_registry.service
   ```

## Конфигурация

После `precond` шаблоны env копируются в `/var/allta_services/config`:

- `env.docker_registry`
- `env.docker_registry_ui`

### Ключевые переменные

`env.docker_registry`:

- `REGISTRY_HTTP_ADDR` - адрес, на котором слушает registry внутри контейнера
- `REGISTRY_HTTP_TLS_CERTIFICATE` / `REGISTRY_HTTP_TLS_KEY` - пути до TLS-файлов
- `REGISTRY_AUTH=token`
- `REGISTRY_AUTH_TOKEN_REALM` - endpoint выдачи токена (`allta_auth`)
- `REGISTRY_AUTH_TOKEN_SERVICE` - service для токена (должен совпадать с `REGISTRY_TOKEN_SERVICE` в `allta_auth`)
- `REGISTRY_AUTH_TOKEN_ISSUER` - issuer токена (должен совпадать с `REGISTRY_TOKEN_ISSUER` в `allta_auth`)
- `REGISTRY_AUTH_TOKEN_ROOTCERTBUNDLE` - сертификат с публичным ключом подписи токена (из `${REGISTRY_KEYS_PATH}/auth-registry.crt`)
- `REGISTRY_STORAGE_DELETE_ENABLED=true` - разрешение удаления образов/тегов

`env.docker_registry_ui`:

- `NGINX_PROXY_PASS_URL=https://<host>:21503` - прокси на registry
- `PULL_URL=<host>:21503` - адрес pull/push для клиентов
- `SINGLE_REGISTRY`, `REGISTRY_TITLE` - параметры UI
- `DELETE_IMAGES=false` - удаление образов через UI отключено

## Порты и точки входа

- Registry API: `https://<host>:21503/v2/`
- Web UI: `https://<host>:21502/` (доступ только после авторизации в `allta_auth`)

Ожидаемое поведение:

- `https://<host>:21503/v2/` без логина: HTTP `401 Unauthorized` (нормальная проверка доступности).
- `https://<host>:21502/` без логина: редирект на OAuth-логин `allta_auth`, после входа возврат в UI.
- Доступ в UI определяется `required_permission` OAuth-клиента `allta-docker-ui` в `allta_auth`.
- Если `required_permission` пустой, войти может любой авторизованный пользователь.
- Удаление образов через UI запрещено.

## Команды управления

Скрипт `install.sh`:

- `sudo ./install.sh precond` - подготовка каталогов/конфигов + генерация systemd unit
- `sudo ./install.sh start` - `docker-compose up --build -d`
- `sudo ./install.sh stop` - `docker-compose down`
- `sudo ./install.sh reinstall` - `down -v` + удаление данных registry + пересоздание каталогов
- `sudo ./install.sh remove` - полное удаление: контейнеров, локальных image, systemd unit, env-файлов и данных

Команды через systemd:

```bash
sudo systemctl status docker_registry.service
sudo systemctl restart docker_registry.service
sudo systemctl stop docker_registry.service
```

## Каталоги данных

- `/var/allta_services/config` - env-файлы проекта
- `/var/allta_services/volumes/docker_registry_data` - данные registry
- `/var/allta_services/certs` - общий TLS-сертификат (`allta-api.crt`, `allta-api.key`)
- `/var/allta_services/secrets` - ключи подписи/проверки токенов (`auth-registry.crt`, `registry_signing.key`)

## Проверка работоспособности

Проверка контейнеров:

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | grep docker-registry
```

Проверка API:

```bash
curl -k -I https://127.0.0.1:21503/v2/
```

Диагностический скрипт внутри контейнера registry:

```bash
docker exec -it docker-registry /tmp/check-registry.sh
```

## Работа с registry (push/pull)

1. Логин:

   ```bash
   docker login <host>:21503
   ```

2. Публикация образа:

   ```bash
   docker pull nginx:latest
   docker tag nginx:latest <host>:21503/nginx:latest
   docker push <host>:21503/nginx:latest
   ```

3. Загрузка:

   ```bash
   docker pull allta.devos.astralinux.ru:21503/nginx:latest
   ```

Примечание: используется self-signed CA. Для production-клиентов добавьте `ca.crt` в доверенные сертификаты Docker на клиентских хостах.

## Утилиты

`utils/upload_images.sh`:

- Пакетная загрузка списка образов из `utils/base_images.txt`
- Загрузка одного конкретного образа

Примеры:

```bash
cd utils
./upload_images.sh base
./upload_images.sh python:3.12
```

Перед использованием проверьте значение `REGISTRY=...` в `utils/upload_images.sh`.

`utils/find_used_images_in_repo.sh`:

- Сканирует локальные и remote-ветки git-репозитория
- Находит используемые docker-образы в Dockerfile/YAML/`docker pull`
- Сохраняет список в `~/images.txt`

Запуск:

```bash
cd utils
./find_used_images_in_repo.sh
```

## Обновление проекта

1. Обновить код:

   ```bash
   git pull
   ```

2. При изменениях в compose/контейнерах перезапустить сервис:

   ```bash
   sudo systemctl restart docker_registry.service
   ```

3. При появлении новых env-переменных:

   ```bash
   sudo ./install.sh precond
   cd /var/allta_services/config
   # дополнить нужные env-файлы вручную
   sudo systemctl restart docker_registry.service
   ```
