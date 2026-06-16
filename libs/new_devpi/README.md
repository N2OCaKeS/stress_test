# new_devpi

Самодостаточный devpi-сервис: один HTTP-контейнер (без TLS), вся настройка —
в рантайме из переменных окружения. Хранит python-пакеты проекта (`allta` и др.)
и автоматически подхватывает новые релизы из ветки `libs`.

## Чем отличается от старого devpi_service

- **Один env-файл.** Все креды и параметры лежат в `env.devpi`, который
  разворачивается в `/var/allta_services/config/env.devpi` из шаблона
  `env/example/example.env.devpi` (префикс `example.` отбрасывается).
- **Нет хардкода в образе.** `Dockerfile` статичный — только установка пакетов.
  Ни паролей, ни пользователей, ни индексов на этапе сборки. Вся инициализация
  (`devpi-init`, создание индексов и пользователя) выполняется в `entrypoint.sh`
  при старте контейнера, идемпотентно.
- **git-токен через config_api.** `new_release.py` берёт токен только из
  config_api по HTTP. Никаких `tokens.json`, никакого `GIT_TOKEN` из окружения.

## Конфигурация: env.devpi

| Переменная | Назначение |
|---|---|
| `DEVPI_DATA_DIR` | Каталог хранилища пакетов на хосте (bind-mount в `/data`). По умолчанию `/home/partimag/devpi`. |
| `DEVPI_PORT` | Порт API и веб-интерфейса (по умолчанию 3141). |
| `DEVPI_ROOT_PASSWORD` | Пароль пользователя `root` devpi. Обязателен. |
| `DEVPI_USER` | Дополнительный пользователь (опционально). |
| `DEVPI_PASSWORD` | Пароль дополнительного пользователя. |
| `DEVPI_RELEASE_INDEX` | Имя релизного индекса (по умолчанию `release` → `root/release`). |
| `DEVPI_DEV_INDEX` | Имя dev-индекса пользователя (по умолчанию `dev`). |
| `DEVPI_INDEX_BASES` | Базовый индекс (по умолчанию `root/pypi`). |
| `ALLTA_CONFIG_API_URL` | Базовый URL config_api для получения git-токена. |
| `ALLTA_API_TOKEN` | Bearer-JWT для авторизации в config_api. |
| `DEVPI_GIT_TOKEN_NAME` | Имя токена в config_api (по умолчанию `git_token`). |
| `ALLTA_API_VERIFY_TLS` | Проверять ли TLS config_api (`0` — отключено по умолчанию). |
| `DEVPI_BASE_VERSION` | Базовая версия для сброса репозитория (по умолчанию `0.0.1`). |
| `DEVPI_INDEX_URL` | Целевой индекс для загрузки внутри контейнера. |

## Настройка в рантайме

`Dockerfile` ничего не настраивает — только ставит `devpi-server`,
`devpi-web`, `devpi-client`, `sphinx`, `git`, `curl`. При старте `entrypoint.sh`:

1. Если серверная директория пуста — `devpi-init` с паролем `DEVPI_ROOT_PASSWORD`.
2. Запускает `devpi-server` в фоне, ждёт готовности (до 60 секунд).
3. Логинится под `root`, создаёт `root/<release>`, если его нет.
4. Если заданы `DEVPI_USER`/`DEVPI_PASSWORD` — создаёт пользователя и его
   dev-индекс, затем возвращается под `root`.
5. Запускает `new_release.py` (мониторинг релизов) и держит контейнер на сервере.

Все шаги идемпотентны: повторный старт не ломает существующие индексы и данные.

## Поток git-токена через config_api

`new_release.py` обращается к
`GET {ALLTA_CONFIG_API_URL}/api/config/v1/config/tokens/details/{DEVPI_GIT_TOKEN_NAME}`
с заголовком `Authorization: Bearer {ALLTA_API_TOKEN}`. Из JSON-ответа берётся
токен (`token`/`git_token`/`value`) и, при наличии, имя пользователя
(`username`/`git_username`/`login`). config_api — единственный источник: при
отсутствии URL/токена, HTTP-ошибке или пустом ответе скрипт падает с понятной
ошибкой. Файлового фолбэка и `GIT_TOKEN` из env больше нет.

## Управление: install.sh

| Команда | Действие |
|---|---|
| `precond` | Установить зависимости (docker, docker-compose), создать каталоги, скопировать `env.devpi`, поставить systemd-unit. |
| `start` | `docker-compose up --build -d` (через systemd `devpi.service`). |
| `stop` | `docker-compose down`. |
| `update` | Сохранить текущий commit, бэкап cred, `git pull --ff-only`, синхронизировать env с шаблоном, перезапустить. |
| `revert` | Откатиться на сохранённый commit и восстановить cred из бэкапа. |
| `reinstall` | Полностью переустановить с удалением хранилища пакетов. |
| `remove` | Удалить контейнер, образ, unit и `env.devpi`. **Данные пакетов сохраняются**; для их удаления — `remove --purge` (с подтверждением). |

## Хранилище

Пакеты хранятся на хосте в `DEVPI_DATA_DIR` (по умолчанию `/home/partimag/devpi`),
примонтированном как bind в `/data` контейнера. Именованных volume'ов нет.

## Массовая загрузка

Каталог `bulk_load/` содержит инструментарий для пакетной заливки python-пакетов
в индекс (см. `bulk_load/README.md` и `bulk_load/inventory-report.md`).
