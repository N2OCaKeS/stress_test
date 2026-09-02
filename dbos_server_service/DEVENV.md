# Dev Environment

Запуск: `make seed` — чистая БД + миграции + сид (`scripts/seed_dev.py`), стек должен быть поднят (`make up`).
Остановка стека: `make down` (volumes сохранятся) / `make down-v` (с volumes).

## Учётные данные

**Все пароли init-пользователей: `1`** (у всех `must_change_password=false`).

| Логин            | Пароль | Роль             | Доступ                                            |
| ---------------- | ------ | ---------------- | ------------------------------------------------- |
| `admin`          | `1` | account_admin    | Глобальный администратор платформы                |
| `loging_admin1`  | `1` | loging_admin     | Управление loging_service (правила, retention)    |
| `dep_admin1`     | `1` | department_admin | Администратор отдела НТ + `admin` на всех сервисах |
| `loging_reader1` | `1` | loging_reader    | Чтение аудит-событий по всем департаментам        |
| `user1`          | `1` | regular          | Отдел НТ                                           |

## Сервисы

| Сервис         | URL                   | Docs                       |
| -------------- | --------------------- | -------------------------- |
| auth_service   | http://localhost:8000 | http://localhost:8000/docs |
| loging_service | http://localhost:8001 | http://localhost:8001/docs |
| server_service | http://localhost:8002 | http://localhost:8002/docs |
| secret_service | http://localhost:8003 | http://localhost:8003/docs |
| server_worker  | taskiq worker (без HTTP), `redis://localhost:6379/0` | — |

## Swagger UI — вход

**auth_service** `/docs` → Authorize → OAuth2Password → `admin` / `1`

**loging_service** `/docs` → Authorize → OAuth2Password → `loging_admin1` / `1`

**server_service** / **secret_service** `/docs` → Authorize → Bearer JWT (получить через auth_service `/login`)

## Платформа

| Объект   | Данные                                                                                    |
| -------- | ----------------------------------------------------------------------------------------- |
| Отдел    | НТ — Нагрузочное тестирование (`dep_admin1`, `user1`)                                      |
| Сервисы  | auth_service, server_service, loging_service, secret_service (доступ отдела ко всем 4)     |
| Роли     | `admin` (системная, на каждом сервисе); `worker_bot` (least-privilege на server_service)   |
| Сервер   | test-server-01 → контейнер `test_server` (ssh `:2222`), OS `openssh-server-latest`         |
| OS-аккаунт | `tester` / `tester1234` на test-server-01 (sudo, пароль AES-GCM-зашифрован)              |
| Бот      | `worker_bot_nt` (отдел НТ, роль `worker_bot`, PAT в `.dev/.worker_pat`)                    |
| Секреты  | `dev_jira_token`, `dev_postgres_password` (dept НТ), `dev_loadgen_secret` (personal user1) |

После seed'а перезапусти worker, чтобы он подхватил свежий PAT:
`docker compose -f docker-compose.dev.yml restart server_worker`.

## Тесты

```bash
make test-dev-all                                          # все тесты (auth + logging + server + worker + integration)
make test-dev TEST=loging_service/tests/test_rules.py     # конкретный файл
make test-dev-auth                                         # только auth_service
make test-dev-logging                                      # только loging_service
make test-dev-server                                       # только server_service
make test-dev-worker                                       # только server_worker
make test-dev-integration                                  # интеграционные
```
