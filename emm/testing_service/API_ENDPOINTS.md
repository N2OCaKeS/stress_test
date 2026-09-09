# testing_service API endpoints

> **Версия сервиса:** `0.1.0` (см. `pyproject.toml`, OpenAPI `version` в `src/main.py`).
> **Базовый префикс:** `/api/testing/v1`.
> **Статус реализации:** волна 1 (каркас) — только health/ready. Домен появится по волнам `obsidian/ALLTA MIGRATION.md` §16.
> **Аудит-события:** перечислены в `AUDIT_EVENTS.md`.

## Общие правила

- В production только HTTPS (TLS-guard middleware отбивает cleartext).
- User-facing эндпоинты (появятся с доменом) требуют Bearer JWT/PAT/bot-token, выдаваемый `auth_service`. Identity ресолвится через `/api/auth/v1/authorization/introspect` на каждый запрос.
- Internal-эндпоинты (появятся в волне 2/5 — callback от `server_service` для `prepare-for-test`) будут закрыты shared bearer `SERVICE_API_KEY`/`SERVICE_API_KEYS`, `include_in_schema=False`.
- Health/ready публичны (без auth, без rate-limit).
- Все значимые действия публикуются в `loging_service` — каталог см. `AUDIT_EVENTS.md`.

## Health

| Метод | Path | Доступ | Описание |
|---|---|---|---|
| `GET` | `/health` | public | Liveness — без проверок зависимостей. |
| `GET` | `/ready` | public | Readiness — БД обязательна, Redis best-effort. |

## Формат ошибки

```json
{
  "error": "forbidden",
  "error_code": "SERVICE_ACCESS_DENIED",
  "message": "...",
  "details": {},
  "request_id": "req_123",
  "timestamp": "2026-09-09T12:00:00Z"
}
```

## Домен (появится по волнам)

Легаси-совместимые пути (`GET /rest/api/get-repo-path-as-json` и т.д., §12 плана миграции) и весь остальной каталог (глобальные переменные, тесты, стенды, очередь, логи, СТП, отчёты) — по мере реализации соответствующей волны.
