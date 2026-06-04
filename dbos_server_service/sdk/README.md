# sdk/ — канонический источник для копи-паста

**Это НЕ pip-пакет и НЕ shared import.** Каталог хранит «эталонные» версии
кода, который сейчас руками дублируется по всем четырём сервисам
(`auth_service`, `loging_service`, `server_service`, `server_worker`).

Каждый сервис депло́ится самостоятельно: общий PYTHONPATH между ними не
тянется (`auth_service/src` и `loging_service/src` — изолированные деревья).
Поэтому когда оператору нужно «общий код» — он **берёт нужный файл из `sdk/`
и копирует** внутрь сервиса (`src/services/`, `src/utils/` — куда исторически
лёг такой код).

## Правило обновления

Меняешь модуль в `sdk/` — **обновляешь во всех сервисах вручную**. Никакой
автоматики, никаких симлинков, никаких post-install хуков. Цена ленивости —
расхождение реализаций → расхождение поведения → CVE-class баг типа
«в server_service маскируется пароль, в loging_service — нет».

Обратный поток (правка в сервисе → перенос в `sdk/`) тоже руками: если в
конкретной волне фиксов выяснилось, что одна из копий стала лучше, прежде
чем закрывать таск надо синхронизировать `sdk/` и остальные сервисы.

## Что лежит сейчас

| Файл | Где используется (на момент создания) | Что это |
|------|---------------------------------------|---------|
| `audit_client.py` | (новый компонент для будущих сервисов) | HTTP-клиент для отправки audit-событий в `loging_service`. |
| `bearer.py` | `auth_service`, `loging_service`, `server_service`, `server_worker` (через `audit_client.py`) | Inbound: `_extract_bearer(request)` + shape-precheck (`_is_token_shape_valid`); самый консервативный вариант — из `server_service/src/dependencies/auth.py`. Outbound: `bearer_header(token)` — эталон для `<service>/src/core/http.py` копий во всех 4 сервисах. |
| `extract_client_ip.py` | `auth_service/src/services/audit_context.py`, `server_service/src/services/audit_context.py` | Безопасный парсер `X-Forwarded-For`/`X-Real-IP` с allow-list доверенных proxy. |
| `security_headers.py` | `auth_service/src/main.py`, `loging_service/src/main.py`, `server_service/src/main.py` | `SecurityHeadersMiddleware` (HSTS опционально, X-Frame, CSP, Referrer-Policy, Permissions-Policy). |
| `redaction.py` | `auth_service/src/services/redaction.py`, `loging_service/src/utils/redaction.py`, `server_service/src/services/redaction.py` | Dict/list redactor: маскирует password/token/secret/hash/credential по имени ключа и JWT/argon2/bcrypt по форме значения. Версия `loging_service` — самая консервативная (всегда схлопывает контейнер на ключе-классификаторе). |

`server_worker/src/utils/redaction.py` — **отдельный модуль**, маскирует
free-form строки ошибок (URL-credentials, ipmitool-args). Под копи-паст
не годится: разные API. В sdk/ его не выносим.

## Чего здесь НЕТ и не должно появиться

- Импортов из `src.*` любого сервиса — модули должны быть автономными.
- Конфига через `get_settings()` — параметры передаются аргументами.
- Тестов — тесты сервиса проверяют его собственную копию.
- `__init__.py`-реэкспортов — каждый файл копируется автономно.
