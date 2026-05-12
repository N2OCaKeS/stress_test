# Тесты auth_service

## Стек и подход

- Фреймворк: `pytest`
- БД: реальный PostgreSQL (не мок) — транзакция откатывается после каждого теста
- HTTP: `TestClient` (FastAPI / httpx) для интеграционных тестов
- Фикстуры: `conftest.py` создаёт тестовую БД, admin-пользователя, department, platform service

---

## AUTH-TEST-001 · Security helpers (`core/security.py`)

> Реализовано в `tests/core/test_security.py` — 26 тестов.

- [x] `hash_password` возвращает валидный Argon2id хеш
- [x] `verify_password` возвращает True для корректного пароля
- [x] `verify_password` возвращает False для неправильного пароля
- [x] `create_access_token` создаёт декодируемый JWT с нужными claims (sub, exp, allowed_services, service_roles)
- [x] `decode_access_token` бросает исключение для просроченного токена
- [x] `decode_access_token` бросает исключение для токена с неверной подписью
- [x] `generate_refresh_token` возвращает уникальные значения при повторном вызове
- [x] `generate_pat` возвращает токен с префиксом `dbos_pat_`
- [x] `generate_bot_token` возвращает токен с префиксом `dbos_bot_`
- [x] `hash_opaque_token` даёт одинаковый результат для одного входа

---

## AUTH-TEST-010 · Login (`POST /api/auth/v1/login`)

- [ ] Успешный логин возвращает `access_token`, `refresh_token`, `identity`
- [ ] Identity содержит корректные `allowed_services` и `service_roles`
- [ ] Неверный пароль → 401 `INVALID_CREDENTIALS`
- [ ] Несуществующий пользователь → 401 `INVALID_CREDENTIALS` (не раскрывать, что user не найден)
- [ ] Заблокированный пользователь (status=blocked) → 403 `USER_BLOCKED`
- [ ] Забаненный пользователь (status=banned) → 403 `USER_BANNED`
- [ ] После 5 неудачных попыток → 429 `ACCOUNT_TEMPORARILY_LOCKED` с `retry_after_seconds`
- [ ] После lockout истекает — логин снова проходит
- [ ] account_admin логинится без department_id и получает `allowed_services=[]`
- [ ] Ответ содержит заголовок `X-Request-ID`

---

## AUTH-TEST-020 · Refresh (`POST /api/auth/v1/refresh`)

- [x] Успешный refresh возвращает новый `access_token` и новый `refresh_token`
- [x] Старый refresh_token после ротации больше не принимается
- [x] Повторное использование старого (уже ротированного) токена → 401 `REFRESH_TOKEN_INVALID` + все сессии пользователя отзываются
- [x] Просроченный refresh_token → 401 `REFRESH_TOKEN_EXPIRED` (`test_refresh_truly_expired_session_returns_expired_error`)
- [x] Несуществующий refresh_token → 401 `REFRESH_TOKEN_INVALID`
- [x] Refresh для забаненного пользователя → 403 (`tests/users/test_ban.py::test_banned_user_refresh_fails`)

---

## AUTH-TEST-030 · Logout (`POST /api/auth/v1/logout`)

- [ ] Logout отзывает refresh_token
- [ ] После logout refresh тем же токеном → 401
- [ ] Logout с невалидным токеном → 200 (idempotent, не раскрывает информацию)

---

## AUTH-TEST-040 · Me (`GET /api/auth/v1/me`)

- [x] Возвращает корректный identity context для обычного пользователя
- [x] Возвращает `allowed_services` и `service_roles` из актуального состояния БД
- [x] Без токена → 401
- [x] С просроченным токеном → 401 `ACCESS_TOKEN_EXPIRED` (`test_me_with_truly_expired_jwt_returns_401`)

> **Найден баг:** `/me` использует `HTTPException` напрямую, поэтому envelope ответа — `{"detail": {...}}` вместо плоского `{"error_code": ..., ...}` как у остальных эндпоинтов (см. также AUTH-TEST-110 ниже). Тест эту разницу временно учитывает, но envelope нужно унифицировать в `src/dependencies/auth.py`.

---

## AUTH-TEST-050 · Сервисы (`/api/auth/v1/services`)

- [x] `POST /services` — account_admin создаёт сервис → 201 с корректным ответом
- [x] `POST /services` — дублирующий service_name → 409 `SERVICE_ALREADY_EXISTS`
- [x] `POST /services` — не account_admin → 403 `ROLE_REQUIRED`
- [x] `GET /services` — публичный список активных сервисов
- [x] `DELETE /services/{name}` — account_admin удаляет сервис → 200
- [x] `DELETE /services/{name}` — после удаления у всех отделов отзывается доступ (`test_delete_service_revokes_department_access`)
- [x] `DELETE /services/{name}` — после удаления user_service_roles деактивируются (`test_delete_service_deactivates_user_service_roles`)
- [x] `DELETE /services/{name}` — несуществующий сервис → 404

---

## AUTH-TEST-060 · Отделы и доступ к сервисам (`/api/auth/v1/departments`)

- [ ] `POST /departments` — account_admin создаёт отдел → 201
- [ ] `POST /departments` — дублирующее name → 409
- [ ] `GET /departments` — возвращает список активных отделов
- [ ] `POST /departments/{id}/services` — выдать отделу доступ → 201 `enabled=true`
- [ ] `POST /departments/{id}/services` — несуществующий отдел → 404
- [ ] `POST /departments/{id}/services` — несуществующий сервис → 404
- [ ] `POST /departments/{id}/services` — повторный grant → 409
- [ ] `DELETE /departments/{id}/services/{name}` — отозвать доступ → 200
- [ ] `DELETE /departments/{id}/services/{name}` — после отзыва логин пользователя не содержит этот сервис

---

## AUTH-TEST-070 · Пользователи (`/api/auth/v1/users`)

- [ ] `POST /users` — account_admin создаёт пользователя с department → 201
- [ ] `POST /users` — account_admin создаёт account_admin без department → 201
- [ ] `POST /users` — department_admin создаёт пользователя в своём отделе → 201
- [ ] `POST /users` — department_admin создаёт пользователя в чужом отделе → 403
- [ ] `POST /users` — дублирующий username → 409
- [ ] `POST /users` — несуществующий department_id → 404
- [ ] `POST /users` — обычный пользователь без department_id → 422
- [x] `PATCH /users/{id}` — обновление email, status, department_id, 404 на чужого, 403 для dept_admin/regular, 422 на invalid email (`tests/users/test_update.py`, 7 тестов)
- [ ] `POST /users/{id}/roles` — назначить роли в сервисе → 200
- [ ] `POST /users/{id}/roles` — сервис не разрешён отделу → 403 `SERVICE_NOT_ALLOWED_FOR_DEPARTMENT`
- [ ] `POST /users/{id}/roles` — department_admin назначает роли в чужом отделе → 403
- [ ] `POST /users/{id}/reset-password` — сбрасывает пароль + отзывает все сессии и PAT
- [ ] `POST /users/{id}/ban` — аккаунт блокируется, активные сессии отзываются
- [ ] `POST /users/{id}/ban` — повторный бан → 409 `BAN_ALREADY_ACTIVE`
- [ ] `POST /users/{id}/unban` — снимает бан, status возвращается в active
- [ ] `POST /users/{id}/unban` — нет активного бана → 404

---

## AUTH-TEST-080 · Personal Access Tokens (`/api/auth/v1/tokens`)

- [x] `POST /tokens` — создать PAT → 201, токен виден только в этом ответе
- [x] `POST /tokens` — дублирующее name → 409 `TOKEN_NAME_ALREADY_EXISTS`
- [x] `GET /tokens` — список своих PAT без секретных значений
- [x] `DELETE /tokens/{id}` — отозвать свой PAT → 200
- [x] `DELETE /tokens/{id}` — повторный revoke → 409 `TOKEN_ALREADY_REVOKED`
- [x] `DELETE /tokens/{id}` — чужой PAT → 404
- [x] PAT работает в introspect после создания
- [x] PAT не работает в introspect после revoke
- [x] Просроченный PAT → introspect возвращает `active=false`

---

## AUTH-TEST-090 · Bots и bot tokens (`/api/auth/v1/bots`)

- [x] `POST /bots` — account_admin создаёт бота → 201
- [x] `POST /bots` — department_admin создаёт бота в своём отделе → 201
- [x] `POST /bots` — department_admin создаёт бота в чужом отделе → 403
- [x] `GET /bots` — account_admin видит всех ботов
- [x] `GET /bots` — department_admin видит только ботов своего отдела
- [x] `PATCH /bots/{id}` — обновить статус/описание → 200 (`test_admin_updates_bot_*`, cross-dept 403)
- [x] `POST /bots/{id}/tokens` — создать bot token → 201, показывается один раз
- [x] `POST /bots/{id}/tokens` — дублирующее name → 409
- [x] `GET /bots/{id}/tokens` — список токенов без секретных значений (`test_list_bot_tokens_excludes_raw_secret`)
- [x] `DELETE /bots/{id}/tokens/{tid}` — отозвать токен → 200 (`test_revoke_bot_token_returns_200`)
- [x] `DELETE /bots/{id}/tokens/{tid}` — повторный revoke → 409 (`test_double_revoke_bot_token_returns_409`)
- [x] bot token работает в introspect
- [x] bot token не работает после revoke

---

## AUTH-TEST-100 · Authorization (`/api/auth/v1/authorization`)

- [x] `POST /introspect` — валидный JWT → `active=true`, subject_type=user, корректные roles
- [x] `POST /introspect` — валидный PAT → `active=true`, subject_type=user
- [x] `POST /introspect` — валидный bot token → `active=true`, subject_type=bot
- [x] `POST /introspect` — невалидный токен → `active=false`
- [x] `POST /introspect` — просроченный JWT → `active=false` (`test_expired_jwt_is_inactive`)
- [x] `POST /introspect` — просроченный PAT → `active=false` (`test_expired_pat_is_inactive`)
- [x] `POST /service-access` — у пользователя есть доступ → `allowed=true` + roles
- [x] `POST /service-access` — у пользователя нет доступа к сервису → `allowed=false`
- [x] `POST /service-access` — у отдела нет доступа к сервису → `allowed=false`
- [x] `POST /service-access` — невалидный токен → `allowed=false`

---

## AUTH-TEST-110 · Error format

> Реализовано в `tests/error_format/test_error_envelope.py` — 7 тестов.
> Баг envelope-формата в `src/dependencies/auth.py` ИСПРАВЛЕН: `HTTPException` → `AuthenticationError`/`AuthorizationError`, теперь все 401/403/404/409/422/500 ответы имеют единый плоский формат.

- [x] Все ошибки содержат поля: `error`, `error_code`, `message`, `details`, `request_id`, `timestamp` (401/403/INVALID_CREDENTIALS)
- [x] Pydantic validation error → 422 с `error_code=VALIDATION_ERROR`
- [x] `X-Request-ID` из запроса прокидывается в ответ (заголовок + envelope)
- [x] Сгенерированный `X-Request-ID` (`req_<12hex>`) присутствует в ответе если не передан в запросе

---

## Порядок реализации

1. `conftest.py` — фикстуры: `db_session`, `client`, `account_admin_token`, `department`, `service`
2. AUTH-TEST-001 (security helpers — юнит-тесты, быстрые)
3. AUTH-TEST-010–030 (login/refresh/logout — критический path)
4. AUTH-TEST-050–060 (services/departments — admin operations)
5. AUTH-TEST-070 (users + roles)
6. AUTH-TEST-080–090 (PAT + bots)
7. AUTH-TEST-100 (authorization/introspect)
8. AUTH-TEST-110 (error format)

---

## Эндпоинты вне AUTH-TEST реестра (покрытие)

| Группа | Файлы | Гэпы |
| --- | --- | --- |
| `oauth2` (clients + authorization_code + client_credentials) | `tests/oauth2/test_clients.py` (29 тестов) | refresh_token grant в коде не реализован — тестировать нечего |
| `service_roles` (включая bulk) | `tests/services/test_service_roles.py`, `tests/services/test_bulk_roles.py` (14 + 8) | — |
| `groups` (включая user-group роли) | `tests/groups/test_groups.py` (20) | — |
| `docker_registry` (config + token + push/pull policies) | `tests/docker/test_docker_config.py`, `tests/docker/test_docker_token.py` (10 + 21) | — |
