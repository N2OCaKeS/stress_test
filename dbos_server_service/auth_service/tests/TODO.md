# Тесты auth_service

## Стек и подход

- Фреймворк: `pytest`
- БД: реальный PostgreSQL (не мок) — транзакция откатывается после каждого теста
- HTTP: `TestClient` (FastAPI / httpx) для интеграционных тестов
- Фикстуры: `conftest.py` создаёт тестовую БД, admin-пользователя, department, platform service

---

## AUTH-TEST-001 · Security helpers (`core/security.py`)

- [ ] `hash_password` возвращает валидный Argon2id хеш
- [ ] `verify_password` возвращает True для корректного пароля
- [ ] `verify_password` возвращает False для неправильного пароля
- [ ] `create_access_token` создаёт декодируемый JWT с нужными claims (sub, exp, allowed_services, service_roles)
- [ ] `decode_access_token` бросает исключение для просроченного токена
- [ ] `decode_access_token` бросает исключение для токена с неверной подписью
- [ ] `generate_refresh_token` возвращает уникальные значения при повторном вызове
- [ ] `generate_pat` возвращает токен с префиксом `dbos_pat_`
- [ ] `generate_bot_token` возвращает токен с префиксом `dbos_bot_`
- [ ] `hash_opaque_token` даёт одинаковый результат для одного входа

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

- [ ] Успешный refresh возвращает новый `access_token` и новый `refresh_token`
- [ ] Старый refresh_token после ротации больше не принимается
- [ ] Повторное использование старого (уже ротированного) токена → 401 `REFRESH_TOKEN_INVALID` + все сессии пользователя отзываются
- [ ] Просроченный refresh_token → 401 `REFRESH_TOKEN_EXPIRED`
- [ ] Несуществующий refresh_token → 401 `REFRESH_TOKEN_INVALID`
- [ ] Refresh для забаненного пользователя → 403

---

## AUTH-TEST-030 · Logout (`POST /api/auth/v1/logout`)

- [ ] Logout отзывает refresh_token
- [ ] После logout refresh тем же токеном → 401
- [ ] Logout с невалидным токеном → 200 (idempotent, не раскрывает информацию)

---

## AUTH-TEST-040 · Me (`GET /api/auth/v1/me`)

- [ ] Возвращает корректный identity context для обычного пользователя
- [ ] Возвращает `allowed_services` и `service_roles` из актуального состояния БД
- [ ] Без токена → 401
- [ ] С просроченным токеном → 401 `ACCESS_TOKEN_EXPIRED`

---

## AUTH-TEST-050 · Сервисы (`/api/auth/v1/services`)

- [ ] `POST /services` — account_admin создаёт сервис → 201 с корректным ответом
- [ ] `POST /services` — дублирующий service_name → 409 `SERVICE_ALREADY_EXISTS`
- [ ] `POST /services` — не account_admin → 403 `ROLE_REQUIRED`
- [ ] `GET /services` — публичный список активных сервисов
- [ ] `DELETE /services/{name}` — account_admin удаляет сервис → 200
- [ ] `DELETE /services/{name}` — после удаления у всех отделов отзывается доступ
- [ ] `DELETE /services/{name}` — после удаления user_service_roles деактивируются
- [ ] `DELETE /services/{name}` — несуществующий сервис → 404

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
- [ ] `PATCH /users/{id}` — обновление email, status → 200
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

- [ ] `POST /tokens` — создать PAT → 201, токен виден только в этом ответе
- [ ] `POST /tokens` — дублирующее name → 409 `TOKEN_NAME_ALREADY_EXISTS`
- [ ] `GET /tokens` — список своих PAT без секретных значений
- [ ] `DELETE /tokens/{id}` — отозвать свой PAT → 200
- [ ] `DELETE /tokens/{id}` — повторный revoke → 409 `TOKEN_ALREADY_REVOKED`
- [ ] `DELETE /tokens/{id}` — чужой PAT → 404
- [ ] PAT работает в introspect после создания
- [ ] PAT не работает в introspect после revoke
- [ ] Просроченный PAT → introspect возвращает `active=false`

---

## AUTH-TEST-090 · Bots и bot tokens (`/api/auth/v1/bots`)

- [ ] `POST /bots` — account_admin создаёт бота → 201
- [ ] `POST /bots` — department_admin создаёт бота в своём отделе → 201
- [ ] `POST /bots` — department_admin создаёт бота в чужом отделе → 403
- [ ] `GET /bots` — account_admin видит всех ботов
- [ ] `GET /bots` — department_admin видит только ботов своего отдела
- [ ] `PATCH /bots/{id}` — обновить статус/описание → 200
- [ ] `POST /bots/{id}/tokens` — создать bot token → 201, показывается один раз
- [ ] `POST /bots/{id}/tokens` — дублирующее name → 409
- [ ] `GET /bots/{id}/tokens` — список токенов без секретных значений
- [ ] `DELETE /bots/{id}/tokens/{tid}` — отозвать токен → 200
- [ ] `DELETE /bots/{id}/tokens/{tid}` — повторный revoke → 409
- [ ] bot token работает в introspect
- [ ] bot token не работает после revoke

---

## AUTH-TEST-100 · Authorization (`/api/auth/v1/authorization`)

- [ ] `POST /introspect` — валидный JWT → `active=true`, subject_type=user, корректные roles
- [ ] `POST /introspect` — валидный PAT → `active=true`, subject_type=user
- [ ] `POST /introspect` — валидный bot token → `active=true`, subject_type=bot
- [ ] `POST /introspect` — невалидный токен → `active=false`
- [ ] `POST /introspect` — просроченный JWT → `active=false`
- [ ] `POST /service-access` — у пользователя есть доступ → `allowed=true` + roles
- [ ] `POST /service-access` — у пользователя нет доступа к сервису → `allowed=false`
- [ ] `POST /service-access` — у отдела нет доступа к сервису → `allowed=false`
- [ ] `POST /service-access` — невалидный токен → `allowed=false`

---

## AUTH-TEST-110 · Error format

- [ ] Все ошибки содержат поля: `error`, `error_code`, `message`, `request_id`, `timestamp`
- [ ] Pydantic validation error → 422 с `error_code=VALIDATION_ERROR`
- [ ] `X-Request-ID` из запроса прокидывается в ответ
- [ ] Сгенерированный `X-Request-ID` присутствует в ответе если не передан в запросе

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
