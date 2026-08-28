# new_devpi

Самодостаточный devpi-сервис: один HTTP-контейнер (без TLS), вся настройка —
в рантайме из переменных окружения. Хранит python-пакеты проекта (`allta` и др.)
и автоматически подхватывает новые релизы из ветки `libs`.

По умолчанию сервис запускается как полностью изолированный репозиторий:
`root/pypi` создаётся как локальный stage, а не как внешний PyPI mirror. `pip`
увидит только те пакеты, которые были физически загружены в devpi.

## Чем отличается от старого devpi_service

- **Один env-файл.** Все креды и параметры лежат в `env.devpi`, который
  разворачивается в `/var/allta_services/config/env.devpi` из шаблона
  `env/example/example.env.devpi` (префикс `example.` отбрасывается).
- **Нет хардкода в образе.** `Dockerfile` статичный — только установка пакетов.
  Ни паролей, ни пользователей, ни индексов на этапе сборки. Вся инициализация
  (`devpi-init`, создание индексов и пользователя) выполняется в `entrypoint.sh`
  при старте контейнера, идемпотентно.
- **Авторизация через Allta Auth API.** Локальные devpi-пользователи создаются
  автоматически после успешной проверки в `allta_auth_api`.
- **git-токен через config_api.** `new_release.py` берёт токен только из
  config_api по HTTP. Никаких `tokens.json`, никакого `GIT_TOKEN` из окружения.

## Конфигурация: env.devpi

| Переменная | Назначение |
|---|---|
| `DEVPI_DATA_DIR` | Каталог хранилища пакетов на хосте (bind-mount в `/data`). По умолчанию `/home/partimag/devpi`. |
| `DEVPI_PORT` | Порт API и веб-интерфейса (по умолчанию 3141). |
| `DEVPI_ROOT_PASSWORD` | Пароль пользователя `root` devpi. Обязателен. |
| `DEVPI_DISABLE_ROOT_PYPI` | `1` — не создавать штатный внешний mirror `root/pypi` при первичной инициализации. По умолчанию `1`. |
| `DEVPI_RESTRICT_MODIFY` | Кто может создавать/менять пользователей и индексы. По умолчанию `root`; upload по `acl_upload` не блокирует. |
| `DEVPI_LOCAL_UPLOAD_USER` | Локальный пользователь для наполнения `root/pypi` и автоматической загрузки библиотек. По умолчанию `allta`. |
| `DEVPI_LOCAL_UPLOAD_PASSWORD` | Пароль локального upload-пользователя. Обязателен, если задан `DEVPI_LOCAL_UPLOAD_USER`. |
| `DEVPI_AUTOCREATE_USERS` | `1` — создавать локального devpi-пользователя после успешной внешней авторизации. |
| `DEVPI_AUTH_API_URL` | Базовый URL Allta Auth API. По умолчанию `https://allta.devos.astralinux.ru:21500`. |
| `DEVPI_AUTH_API_VERIFY_TLS` | Проверять TLS сертификат Allta Auth API. По умолчанию `0`. |
| `DEVPI_AUTH_API_AUTHORIZE_PATH` | Endpoint проверки devpi-доступа. По умолчанию `/api/auth/v1/integrations/devpi/authorize`. |
| `DEVPI_AUTH_ACCEPT_RAW_TOKEN` | `1` — принимать пароль devpi-login как raw Bearer token Allta CLI, если он передан без префикса `Bearer `. По умолчанию `1`. |
| `DEVPI_ACL_UPLOAD` | Upload-principals для `root/release` и `root/test`. По умолчанию `root,allta,:devpi_upload`. |
| `DEVPI_PYPI_INDEX` | Имя локального PyPI-cache индекса (по умолчанию `pypi` → `root/pypi`). |
| `DEVPI_PYPI_ACL_UPLOAD` | Кто может upload в `root/pypi`. По умолчанию только `allta`. |
| `DEVPI_RELEASE_INDEX` | Имя стабильного индекса (по умолчанию `release` → `root/release`). |
| `DEVPI_TEST_INDEX` | Имя тестового индекса (по умолчанию `test` → `root/test`). |
| `DEVPI_DEV_INDEX` | Имя личного dev-индекса пользователя (по умолчанию `dev` → `<user>/dev`). |
| `DEVPI_RELEASE_INDEX_BASES` | Базовые индексы stable. По умолчанию `root/pypi`. |
| `DEVPI_TEST_INDEX_BASES` | Базовые индексы test. По умолчанию `root/release`. |
| `DEVPI_RELEASE_VOLATILE` | `False` — запретить перезапись уже загруженных stable-версий. |
| `DEVPI_TEST_VOLATILE` | `True` — разрешить перезаливку тестовых версий. |
| `DEVPI_AUTH_CREATE_USER_INDEX` | `1` — создавать `<user>/dev` при первом успешном входе пользователя. |
| `DEVPI_AUTH_USER_INDEX_BASES` | Базовые индексы личных dev-индексов. По умолчанию `root/test`. |
| `DEVPI_AUTH_USER_INDEX_VOLATILE` | `True` — разрешить перезаливку в личных dev-индексах. |
| `ALLTA_CONFIG_API_URL` | Базовый URL config_api для получения git-токена. |
| `ALLTA_API_TOKEN` | Bearer-JWT для авторизации в config_api. |
| `DEVPI_GIT_TOKEN_NAME` | Имя токена в config_api (по умолчанию `git_token`). |
| `ALLTA_API_VERIFY_TLS` | Проверять ли TLS config_api (`0` — отключено по умолчанию). |
| `DEVPI_BASE_VERSION` | Базовая версия для сброса репозитория (по умолчанию `0.0.1`). |
| `DEVPI_GIT_BRANCH` | Единственная git-ветка, которую клонирует и мониторит автозагрузчик. По умолчанию `libs`. |
| `DEVPI_UPLOAD_USER` / `DEVPI_UPLOAD_PASSWORD` | Пользователь devpi для автозагрузки пакетов. По умолчанию локальный `allta`. |
| `DEVPI_INDEX_URL` | Целевой индекс для стабильных автозагрузок внутри контейнера. По умолчанию `root/release`. |
| `DEVPI_TEST_INDEX_URL` | Целевой индекс для dev-автозагрузок внутри контейнера. По умолчанию `root/test`. |

## Настройка в рантайме

`Dockerfile` ничего не настраивает — только ставит `devpi-server`,
`devpi-web`, `devpi-client`, `sphinx`, `git`, `curl`. При старте `entrypoint.sh`:

1. Если серверная директория пуста — `devpi-init` с паролем `DEVPI_ROOT_PASSWORD`.
2. Запускает `devpi-server` в фоне, ждёт готовности (до 60 секунд).
3. Логинится под `root`, создаёт локального пользователя `allta`, затем создаёт
   и настраивает `root/pypi`, `root/release` и `root/test`.
4. Запускает devpi с плагином Allta Auth: пользователи создаются при первом
   успешном входе, а личный `<user>/<dev>` индекс создаётся сразу за ними.
5. Запускает `new_release.py` (мониторинг релизов) и держит контейнер на сервере.

Все шаги идемпотентны: повторный старт не ломает существующие индексы и данные.

## Структура индексов

| Индекс | Назначение | Bases | Volatile | Кто может upload |
|---|---|---|---|---|
| `root/pypi` | Локальное хранилище пакетов, заранее взятых из PyPI | пусто | `False` | только `allta` |
| `root/release` | Стабильные релизы | `root/pypi` | `False` | `root`, `allta`, пользователи с `devpi_write` |
| `root/test` | Тестовые сборки и изменённые stable-версии для проверки | `root/release` | `True` | `root`, `allta`, пользователи с `devpi_write` |
| `<user>/dev` | Личный индекс разработчика | `root/test` | `True` | только сам пользователь |

Главное правило изоляции: `root/pypi` должен оставаться stage без внешних bases,
а не mirror. Тогда devpi не сможет дотянуть отсутствующий пакет из PyPI через
наследование индексов.
`DEVPI_RESTRICT_MODIFY=root` дополнительно не даёт обычным пользователям
создавать свои mirror-индексы.

Пример установки только из stable:

```bash
pip install --index-url http://devpi.example:3141/root/release/+simple/ allta
```

Пример тестирования feature-сборки:

```bash
devpi use http://devpi.example:3141/root/test
devpi login user --password 'allta_auth_password_or_token'
devpi upload
pip install --index-url http://devpi.example:3141/root/test/+simple/ allta
```

При успешном `devpi login` плагин проверяет пользователя через
`GET {DEVPI_AUTH_API_URL}{DEVPI_AUTH_API_AUTHORIZE_PATH}?action=read`.
В качестве devpi-пароля можно передать обычный пароль Allta Auth, строку
`Bearer <token>` или сам token из локальной сессии Allta CLI после `allta login`.
Если Allta Auth возвращает `capabilities.devpi_write=true`, пользователь получает
devpi-группу `:devpi_upload` и может загружать в `root/release`/`root/test`.
Если `devpi_write=false`, пользователь всё равно получает личный `<user>/dev`,
но не получает upload-доступ к общим `root/release` и `root/test`.

## Автозагрузка релизов

`new_release.py` смотрит commit message в ветке `libs`:

```text
allta_lib v1.2.3
allta_lib v1.2.3.4
allta_lib v1.2.3.4 feature-name
dev allta_lib v1.2.3
dev allta_lib v1.2.3.4 feature-name
```

Stable-релиз распознаётся только как `allta_lib vX.Y.Z` и загружается в
`DEVPI_INDEX_URL` (`root/release`). Dev-сборка распознаётся как версия из
четырёх числовых частей `allta_lib vX.Y.Z.N` с любым текстовым хвостом после
версии, либо как commit message с префиксом `dev allta_lib v<version>`, где
`<version>` — любая числовая dotted-версия. Dev-сборки загружаются в
`DEVPI_TEST_INDEX_URL` (`root/test`).

Автозагрузчик получает git-токен через config_api, клонирует только ветку
`DEVPI_GIT_BRANCH`, собирает все commit hash с подходящим commit message и
сравнивает их со state-файлом. Если hash для уже известной версии изменился,
скрипт переключается на этот commit и перезаливает библиотеку в соответствующий
индекс. Загрузка выполняется пользователем `DEVPI_UPLOAD_USER` (по умолчанию
`allta`), а не `root`.

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
