# Dev Environment

Запуск: `make dev` — пересоздаёт БД с нуля и наполняет данными.
Остановка: `make dev-stop`

## Учётные данные

**Все пароли: `1234`**

| Логин          | Пароль | Роль             | Доступ                                       |
| -------------- | ------ | ---------------- | -------------------------------------------- |
| `admin`        | `1234` | account_admin    | Полный доступ к auth_service                 |
| `loging_admin` | `1234` | loging_admin     | Управление loging_service (правила, события) |
| `nt_admin`     | `1234` | department_admin | Администратор отдела НТ                      |
| `nt_developer` | `1234` | пользователь     | Отдел НТ                                     |
| `nt_viewer`    | `1234` | пользователь     | Отдел НТ                                     |

## Сервисы

| Сервис         | URL                   | Docs                       |
| -------------- | --------------------- | -------------------------- |
| auth_service   | http://localhost:8000 | http://localhost:8000/docs |
| loging_service | http://localhost:8001 | http://localhost:8001/docs |
| server_service | http://localhost:8002 | http://localhost:8002/docs |
| server_worker  | taskiq worker (без HTTP), `redis://localhost:6379/0` | — |

## Swagger UI — вход

**auth_service** `/docs` → Authorize → OAuth2Password → `admin` / `1234`

**loging_service** `/docs` → Authorize → OAuth2Password → `loging_admin` / `1234`

**server_service** `/docs` → Authorize → Bearer JWT (получить через auth_service `/login`)

## Платформа

| Объект  | Данные                                        |
| ------- | --------------------------------------------- |
| Отдел   | НТ — Нагрузочное тестирование                 |
| Сервисы | config_service, server_service, loging_service |
| Роли    | reader, operator, admin (для каждого сервиса); worker_bot (least-privilege на server_service) |
| Бот     | nt-deploy-bot (отдел НТ)                      |

## Правила логирования (активные)

| Правило                  | Эффект                         | Приоритет      |
| ------------------------ | ------------------------------ | -------------- |
| escalate-all-denied      | denied → CRITICAL              | 900            |
| escalate-auth-failures   | auth failure → CRITICAL        | 800            |
| escalate-user-bans       | user.ban → CRITICAL            | 700            |
| escalate-password-resets | user.password_reset → CRITICAL | 700            |
| suppress-health-checks   | http.client_error → SUPPRESS   | 50 (выключено) |

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
