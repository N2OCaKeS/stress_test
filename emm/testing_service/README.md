# testing_service

## Назначение

`testing_service` — перенос функционала легаси `allta_app` на конвенции emm: каталог тестов, стенды, очередь запуска, конструктор команд, СТП (Zephyr Scale), интеграция с Confluence, changelog-фильтрация, логи прогонов, department-отчёты. Полный план миграции — `emm/obsidian/ALLTA MIGRATION.md`.

**Текущий статус — каркас плюс первые доменные каталоги** (что именно готово — `STATUS.md`). Воркер-задач пока нет. Этот README описывает то, что реально есть в коде; по мере реализации волн (`obsidian/ALLTA MIGRATION.md` §16) секции наполнятся.

## Компоненты

- **`api`** (`src/main.py`) — FastAPI-приложение, `/api/testing/v1/health` + `/api/testing/v1/ready`. Слоистая структура `api → services → repositories → models` заводится по мере появления домена.
- **`worker`** — отдельный top-level сервис `../testing_worker` (свой `pyproject.toml`/`Dockerfile`/`Makefile`), по образцу `server_worker` относительно `server_service`. Пока без реальных задач — собственный SSH-worker появится в волне 5. У воркера пока нет своей БД.
- **`postgres`** — `testing_db`. Миграций пока 0 — модели появятся вместе с доменом (волна 3+).
- **`redis`** — db-index `/3` на общем redis-контейнере (`/0` — `server_worker` broker, `/1` — `auth_service` rate-limit в prod, `/2` — `secret_service` rate-limit; `/1` занят только в prod-стеке, но нумерация держится единой между dev/prod, чтобы не путаться). `testing_service` сам ходит в Redis только best-effort пингом в `/ready` (и опционально как backend rate-limit'а) — брокер задач слушает `testing_worker`.
- **Auth** — через `auth_service` (introspect). **Audit** — публикация в `loging_service`, инфраструктура готова, доменных событий пока нет.

## Технологии

- `Python 3.12`
- `FastAPI` + `SQLAlchemy 2.0 async` + `Alembic`
- `PostgreSQL` (отдельный кластер), `Redis`
- `Docker`, `Kubernetes`

Воркер (`taskiq` + `taskiq-redis`) — отдельный пакет, см. `../testing_worker/README.md`.

## Healthcheck

Два публичных endpoint'а под k8s probe'ы (без auth, без rate-limit).

### `GET /api/testing/v1/health` — liveness

Сервис жив, процесс не повис. Не трогает БД, не зовёт зависимости.

```json
{ "status": "ok", "timestamp": "2026-09-09T12:34:56.000+00:00" }
```

### `GET /api/testing/v1/ready` — readiness

БД обязательна для зелёного `status=ok`; иначе `status=degraded` (HTTP всё равно 200). Redis (taskiq-брокер testing_worker'а) — best-effort, фейл не валит ready.

### Redis-стэш кред тестового пользователя

Пароль и SSH-приватный ключ, пришедшие от server_service в callback prepare-for-test, лежат в Redis (`REDIS_URL`) до claim'а воркера не дольше `CREDS_STASH_TTL_SECONDS` (10 минут) и читаются ровно один раз. Значение шифруется AES-256-GCM (HKDF-SHA256 от `CREDS_STASH_ENCRYPTION_KEY`, свой nonce на запись, AAD привязан к Redis-ключу записи). В production/staging ключ обязателен (min 32 символа, `openssl rand -base64 32`) — без него сервис не стартует; в dev/test пустое значение заменяется встроенным dev-ключом с предупреждением в логе. Ротация: поднять `CREDS_STASH_ENCRYPTION_KEY_VERSION`, прежний ключ положить в `CREDS_STASH_ENCRYPTION_KEY__v<N-1>`. Запись в старом (нешифрованном) формате, чужой ключ или повреждение читаются как «нет записи»: `claim` переводит элемент в failed («creds stash missing or expired»), дальше обычный retry.

```json
{
  "status": "ok",
  "timestamp": "2026-09-09T12:34:56.000+00:00",
  "db": true,
  "redis_connected": true,
  "audit_dropped_429_total": 0,
  "audit_outbox_dlq_total": 0,
  "counters": { "audit_dropped_429": 0, "audit_outbox_dlq": 0 }
}
```

`audit_dropped_429_total` — события, не доехавшие даже до таблицы `audit_outbox` (имя ключа историческое, см. AUDIT_EVENTS.md § «Доставка»); `audit_outbox_dlq_total` — строки, выброшенные дренажом в DLQ. Оба счётчика per-process.

## Локальный запуск

Через корневой `Makefile` (см. `make help`):

```bash
make run-testing          # api на :8004, foreground, авто-перезагрузка
make run-testing-worker   # testing_worker (taskiq worker), foreground
make test-testing         # тесты api в Docker (tests/docker-compose.test.yml)
make test-testing-worker  # тесты testing_worker в Docker
```

Стандартный dev-стек — `docker-compose.dev.yml` в корне репозитория (`make up`/`make dev`).

## Импорт каталога тестов и стендов

`scripts/import_catalog.py` — одноразовый инструмент первого наполнения (§13 плана
миграции): читает JSON/YAML-файл с разделами `stands`/`tests` и заводит записи через
обычный сервисный слой (`services/test_stand.py`, `services/test_definition.py`,
`services/test_command_arg.py`) — та же бизнес-логика и аудит, что у HTTP-API, без
параллельного пути записи в БД. Формат файла и комментарии, откуда брать реальные
данные — см. `scripts/import_catalog.example.yaml`.

```bash
cd emm/testing_service
PYTHONPATH=. DATABASE_URL=postgresql+psycopg://... AUTH_SERVICE_URL=... \
    python scripts/import_catalog.py my_catalog.yaml --bearer-token <admin JWT>
```

`--bearer-token` (или `IMPORT_BEARER_TOKEN` в env) нужен только разделу `stands` —
`create_test_stand` резолвит `department_id` живым pass-through вызовом к
`server_service`, обойти это нельзя, не изобретая параллельный путь. Без токена
позиции `stands` пропускаются с понятной ошибкой на каждую, `tests` заводятся как
обычно. `--dry-run` — резолв и проверка идемпотентности (code/server_id уже
существует) без записи в БД и без живых вызовов к `server_service`.

Привязка теста к стенду задаётся легаси-ИМЕНЕМ стенда
(`pinned_stand_token: stand3`), а не внутренним id — id генерируется в момент
импорта, в файле его знать неоткуда. Имя резолвится через
`test_stands.legacy_token` (стенды заводятся раньше тестов). Если стенда с таким
именем в этой установке нет, тест создаётся без привязки, и в конце печатается
поимённый список таких тестов — обычный (не debug) запуск для них вернёт
`TEST_NOT_PINNED_TO_STAND`. `--stand-legacy-token` (или
`IMPORT_STAND_LEGACY_TOKEN`) присваивает имя единственной записи `stands` прямо
на импорте — dev-сиду, где стенд один и легаси-имени у него нет.

Идемпотентен: повторный запуск на уже наполненной БД пропускает существующие
`code`/`server_id` (skip, не дубль и не падение); единственное, что дозаполняется
на повторе — пустой `legacy_token` существующего стенда. Одна плохая позиция (опечатка в
`variable_code`, недостижимый `server_service`, конфликт по коду) не останавливает
обработку остальных — в конце печатается сводка `created`/`skipped`/`failed` с
текстом ошибки на каждый провал.

**Этой волной реальные данные НЕ импортированы намеренно.** 20+2 реальных стенда
требуют настоящих `server_id` из живого `server_service` конкретной инфраструктуры —
их нет в dev-окружении. Реальный каталог тестов (~30 типоспецифичных легаси-флагов
`allta_app/backup_image.py` — `-psql_aud`/`-ipa`/`-vpn`/`-mail`/`-network`/
`-astraevents`/`-olap` и т.д., ветка `dev_allta_app`) требует ручного разбора
семантики каждого флага (что именно исполняется, какие у него реальные аргументы) —
фабриковать это по одним только именам флагов означало бы риск тихо неверно
определённого теста, который обнаружится только на живом стенде. Инструмент готов
принять оба набора данных, когда они появятся — это ручная задача для того, кто
знает реальную семантику каждого теста и имеет доступ к живому `server_service`.

## Паритет с allta_app

- `tests/test_legacy_golden_parity.py` — golden-тест: для каждого из 62 тестов
  `scripts/import_catalog.allta.yaml` `dates.conf` и команда запуска из задания
  воркеру сравниваются с тем, что собрал бы легаси (`tests/legacy_parity/legacy.py` —
  дословный порт `allta_back.py`/`backup_image.py`, дословность проверяется по AST).
  Известные расхождения — `xfail(strict=True)` со ссылкой на задачу. Правка
  каталога, переменных или профиля запуска, которая меняет `dates`/команду, должна
  оставить его зелёным.
- `scripts/check_branch_argparse.py` — ручная проверка каталога против argparse
  конечных скриптов веток `stress_test` (не часть CI, ветки в репозиторий не входят).
  Как получить ветки и запустить — в docstring скрипта:

```bash
cd emm/testing_service
python scripts/check_branch_argparse.py /tmp/branches   # OK/ERROR по тестам, код 0 — все OK
```

## FreeIPA как многостендовый сценарий

Легаси (`emm/allta_app_full/backup_image.py:943-964`): откат ВМ-клиента
`work-station1` на снимок, его подготовка с ядром `5.15.0-83-generic` без смены
режима, затем на хосте ALLTA — `git_clone.py`, `git checkout freeipa` и
`cd /home/u/freeipa_test/gitipa/stress_test/freeipa && <venv> ipa_run.py {dates}`.

В новой системе это сценарий (раздел «Тестирование → Сценарии»):

1. **Профиль запуска «FreeIPA (ipa_run.py)»** (`lp_freeipa`, миграция
   `freeipa_scenario`) уже назначен тестам `freeipa.auth`,
   `freeipa.create_users`, `freeipa.plugin`. Он клонирует ветку теста
   (`freeipa`) в `/home/u/freeipa_test/gitipa/stress_test` и запускает
   легаси-строку `ipa_run.py {dates}` — dates строкой (`{{DATES_INLINE}}`), не
   файлом. Файл `/home/u/tokens.json` = `{"srv_pass": "<пароль тестовой учётки>"}`
   (его читает `ipa_conf.py`) — дополнительный файл профиля, в логах под маской.
2. **Сценарии** `freeipa.auth` / `freeipa.create_users` / `freeipa.plugin`
   (по одному на тест, `readiness: development`) созданы миграцией, если в БД
   есть стенд `stand3` (или задан отдел по умолчанию легаси-compat):
   КД — `stand3`, подготовка `full`; клиент — ВМ-стенд `stand1`, если есть
   (`revert_only`, ядро `5.15.0-83-generic`, `skip_pam_fix` — без PAM-правки,
   как в легаси); исполнитель `ipa_run.py` — КД
   (легаси запускал его с хоста ALLTA, которого в пуле нет; `ipa_run.py` ходит
   на хосты по SSH откуда угодно). Стенда нет — сценарий пустой: откройте его,
   добавьте КД (сервер), клиента (ВМ, «Только откат», ядро) и действие
   «Прогнать тест» на КД с флагом «вердикт».
3. **Проверка:** «Превью сохранённого сценария» с РЦ и ядром — у действия
   видны скрипт со строкой `ipa_run.py …` и `tokens.json` (`srv_pass` — `***`).
4. **Запуск:** «Запуск» → РЦ, ядро, режим (debug, пока сценарий не `ready`).
   Сценарий берёт брони всех стендов сразу, готовит их параллельно и
   запускает `ipa_run.py` на исполнителе; вердикт — по тесту действия.
5. **СТП и кампании.** У сценариев `stp_test_case_code` = их `code`: сценарий —
   способ запуска этого тест-кейса СТП. Пока сценарий не `ready`, кампания по
   РЦ ставит кейс как одиночный тест (как раньше); после перевода в `ready`
   кампания запускает сценарий, вердикт пишется в ячейку СТП кейса на стенде
   КД. Ручной запуск с ячейки — `POST /scenarios/{id}/runs` со
   `stp_test_run_id`.

**Адреса КД и клиента.** Ветка `freeipa` берёт их из `ipa_conf.py::HOSTS`
(`10.177.103.204`, `10.177.103.201`) — сценарий работает, только если его
стенды и есть эти хосты. Для других стендов ветке нужен ввод адресов
снаружи (например, файл). Доставка уже возможна без правок сервиса: заведите
переменные-ссылки на стенд (источник `stand_ref`, поле `host`), например
`IPA_DC_HOST` и `IPA_CLIENT_HOST`, и добавьте в профиль (или его копию для
отдела) дополнительный файл, например `/home/u/freeipa_test/hosts.json` =
`{"dc": "{{IPA_DC_HOST}}", "client": "{{IPA_CLIENT_HOST}}"}`; остаётся научить
ветку его читать.

## Копирование параметров в конструкторе

В разделе «Тесты» откройте конструктор текущего теста, нажмите «Скопировать из»,
выберите источник и нажмите «ОК». Существующие параметры заменятся копией;
порядок, литералы, ссылки на глобальные переменные и переопределения сохраняются.
Копию можно сразу редактировать. Метаданные текущего теста и исходный тест
не меняются. Пустой источник не очищает конструктор — API возвращает ошибку.

`POST /api/testing/v1/test-definitions/{test_id}/args/copy-from`, тело
`{"source_test_id": "tdef_..."}`, возвращает новый список слотов. Операция требует
права `test_definition.update`, выполняется одной транзакцией и публикует
событие `test_command_arg.copy`. Миграция БД не нужна.

## Что НЕ делает сервис (пока)

- Не хранит платформенные mgmt-креды стендов — их держит `server_service` (§5.1 плана миграции).
- Не хранит токены Jira/Confluence/Zephyr — они в `secret_service` (department-scope credential, §2.4).
- Не дублирует ACS/Clonezilla-снимки, ASTRA/ALLTA health хоста, RC/build-каталог — всё это уже в `server_service` (§0 плана миграции).
