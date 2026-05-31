# auth_service · каталог audit-событий

Источник истины — `src/services/audit_events.py:SERVICE_EVENTS`. При старте сервиса полный список регистрируется в loging_service через `register_events()` (вызывается в background-таске). loging_service использует `(service_name, action)` как ключ каталога — `default_severity` подсказывает, как помечать события в обычном (success) случае.

Severity-overrides: для `(action, status="failure")` loging_service обычно поднимает уровень (например, `user.login success=INFO` → `user.login failure=WARNING`). Точные правила — в `loging_service` (`severity_overrides` таблица).

Формат события (отправляется в loging_service POST `/api/logging/v1/events`):

```json
{
  "service_name": "auth_service",
  "action": "user.login",
  "subject_type": "user",
  "subject_id": "usr_abc",
  "target_type": "user",
  "target_id": "usr_abc",
  "status": "success|failure",
  "severity": "INFO",
  "request_id": "req_123",
  "ip_address": "1.2.3.4",
  "user_agent": "...",
  "details": { ... }
}
```

`details` — JSONB, специфика зависит от action.

---

## Startup

| Action | Default severity | Emitter | Notes |
|------|------|------|------|
| `service.started` | INFO | `main.py` startup | Однократно при старте процесса. |

## HTTP middleware

| Action | Default severity | Emitter | Notes |
|------|------|------|------|
| `http.access_denied` | CRITICAL | RequestAuditMiddleware | На 401/403. Покрывает любые неавторизованные попытки. |
| `http.client_error` | WARNING | RequestAuditMiddleware | На 4xx, кроме 401/403. |
| `http.server_error` | CRITICAL | RequestAuditMiddleware | На 5xx. |

## Authentication & sessions

`subject_type=user` для всех событий ниже.

| Action | Default severity | Emitter | Target | Key details |
|------|------|------|------|------|
| `user.login` | INFO | `auth_service.login` | user | `reason` на failure (`invalid_credentials`, `account_locked`, `user_banned`). |
| `user.refresh` | INFO | `auth_service.refresh` | session | `session_id`. На failure: `reason="user_not_found" \| "banned" \| "blocked" \| "expired"`. |
| `user.logout` | INFO | `auth_service.logout` | session | Идемпотент: для несуществующего тоже success. |
| `user.me` | INFO | `/me` endpoint | user | Скан собственного identity. |
| `token.refresh_reuse` | CRITICAL | `SessionRepository.rotate` | session | Reuse-detection — kill-switch на всю сессию. |
| `token.refresh_race` | INFO | `auth_service.refresh` | session | CAS-miss на параллельном `/refresh`, benign-race. `session_id`, `reason="concurrent_rotation"`. |

## Users

| Action | Default severity | Emitter | Target | Key details |
|------|------|------|------|------|
| `user.create` | INFO | `user_service.create_user` | user | `department_id`, `platform_role`, наличие `initial_roles`. |
| `user.list` | INFO | `user_service.list_users` / `list_users_by_department` | — | `count`, `scope` (`all` / `department`), `department_id` для per-dept. |
| `user.update` | INFO | `user_service.update_user` | user | Diff обновлённых полей (без password). |
| `user.roles_assign` | INFO | `user_service.assign_roles` | user | `service_name`, `roles` (новый набор). |
| `user.password_reset` | CRITICAL | `user_service.reset_password` | user | Без plaintext пароля. |
| `user.self_password_reset` | CRITICAL | `user_service.change_own_password` (`POST /users/me/password`) | user (== actor) | `caller_is_admin` (true для платформенных админ-ролей), `sessions_revoked`, `tokens_revoked=false`. Failure-вариант (`status="failure"`, `details.reason="invalid_old_password"`) эмитится при неверном `old_password` — для SIEM-сигнала о возможном угоне access-токена. |
| `user.ban` | CRITICAL | `user_service.ban_user` | user | `ban_type`, `reason`, `expires_at`, счётчики revoke'нутых PAT/bot-токенов. |
| `user.unban` | CRITICAL | `user_service.unban_user` | user | — |
| `user.ban_deactivated_via_status_change` | WARNING | `user_service.update_user` (PATCH `/users/{id}/status`) | user | Активный ban деактивирован как side-effect смены статуса (без явного unban). Логируется отдельно от `user.unban` для трассировки полу-явных деактиваций. |
| `user.permissions_view` | INFO | `GET /users/{id}/permissions` | user | Кто смотрит чьи права. |
| `user.roles_purged_on_transfer` | WARNING | `user_service.update_user` (department change) | user | Сколько ролей сброшено при переводе в другой отдел. |
| `user.groups_purged_on_transfer` | WARNING | `user_service.update_user` (department change) | user | `removed_group_ids`, `old_dept_id`, `new_dept_id` — group memberships старого отдела удалены при переводе. |
| `user.sessions_listed` | INFO | `GET /users/me/sessions` | user (== actor) | `count` активных сессий. |
| `user.sessions_revoked_all` | CRITICAL | `POST /users/me/sessions/revoke` | user (== actor) | `revoked_count`, `except_session_id`, `except_current`. PAT и bot-токены не трогаются. |
| `user.session_revoked_one` | WARNING | `DELETE /users/me/sessions/{id}` | user (== actor) | `session_id`, `was_current`. |

## Departments

| Action | Default severity | Emitter | Target | Key details |
|------|------|------|------|------|
| `department.create` | CRITICAL | `department_service.create_department` | department | `name`, `display_name`. |
| `department.list` | INFO | `GET /departments` | — | Только account_admin. |
| `department.service_grant` | CRITICAL | `department_service.grant_service_access` | department | `service_name`. |
| `department.service_revoke` | CRITICAL | `department_service.revoke_service_access` | department | `service_name`. |

## Groups

| Action | Default severity | Emitter | Target | Key details |
|------|------|------|------|------|
| `group.create` | INFO | `group_service.create_group` | group | `department_id`, `name`. |
| `group.update` | INFO | `group_service.update_group` | group | Diff. |
| `group.delete` | CRITICAL | `group_service.delete_group` | group | — |
| `group.member_add` | WARNING | `group_service.add_member` | group | `user_id`. |
| `group.member_remove` | WARNING | `group_service.remove_member` | group | `user_id`. |
| `group.bot_member_add` | WARNING | `group_service.add_bot_member` | group | `bot_id`. |
| `group.bot_member_remove` | WARNING | `group_service.remove_bot_member` | group | `bot_id`. |
| `group.service_grant` | CRITICAL | `group_service.grant_service_to_group` | group | `service_name`. |
| `group.service_revoke` | CRITICAL | `group_service.revoke_service_from_group` | group | `service_name`. |
| `group.roles_assign` | CRITICAL | `group_service.assign_group_roles` | group | `service_name`, `roles`. |
| `group.roles_revoke` | CRITICAL | `group_service.revoke_group_roles` | group | `service_name`. |

## Platform services

| Action | Default severity | Emitter | Target | Key details |
|------|------|------|------|------|
| `service.create` | CRITICAL | `platform_service_service.create_service` | service | `service_name`, `display_name`. |
| `service.delete` | CRITICAL | `platform_service_service.delete_service` | service | `service_name`. |
| `service.list` | INFO | `GET /services` | — | — |
| `service.access_check` | INFO | `authorization_service.check_service_access` | service | `service_name`, `allowed`. |

## Service roles

`target_type=service_role`. Все эмитятся `service_role_service.*`.

| Action | Default severity | Notes |
|------|------|------|
| `service_role.create` | INFO | scope `(department_id, service_name, role_name)`. |
| `service_role.update` | INFO | display_name / description. |
| `service_role.delete` | CRITICAL | Системные (`is_system=True`) защищены. |
| `service_role.bulk_assign` | INFO | `user_ids[]`, `service_name`, `role_name`. |
| `service_role.bulk_revoke` | INFO | `user_ids[]`, `service_name`, `role_name`. |

## Personal Access Tokens

`subject_type=user`, `target_type=pat`.

| Action | Default severity | Emitter | Key details |
|------|------|------|------|
| `pat.create` | INFO | `token_service.create_pat` | `name`, `allowed_services`, `expires_at`. |
| `pat.list` | INFO | `GET /tokens` | — |
| `pat.revoke` | WARNING | `token_service.revoke_pat` | `token_id`. |

## Bots

`target_type=bot` (или `bot_token` для bot-токенов).

| Action | Default severity | Emitter | Key details |
|------|------|------|------|
| `bot.create` | WARNING | `bot_service.create_bot` | `name`, `department_id`, `allowed_services`. |
| `bot.list` | INFO | `GET /bots` | `count`, `total`, `scope`, `filter_department_id_requested` (что прислал клиент), `filter_department_id_effective` (что реально применили — для dept_admin форсится на собственный отдел). |
| `bot.update` | WARNING | `bot_service.update_bot` | Diff. |
| `bot.token_create` | WARNING | `bot_service.create_bot_token` | `bot_id`, `bot_name`, `token_id`, `token_name`, `token_prefix`, `expires_at` (по умолчанию now + 6 мес). Plaintext токен в audit не уходит — caller получает его через response. |
| `bot.token_list` | INFO | `GET /bots/{id}/tokens` | — |
| `bot.token_revoke` | WARNING | `bot_service.revoke_bot_token` | `token_id`. |
| `bot.token_expired` | WARNING | `authorization_service.introspect_token` | Попытка использовать просроченный bot-токен (401 BOT_TOKEN_EXPIRED). `bot_id`, `bot_token_id`, `expires_at`. |
| `bot.roles_assign` | WARNING | `bot_service.assign_bot_roles` | `service_name`, `roles`. |
| `bot.roles_list` | INFO | `GET /bots/{id}/roles` | — |
| `bot.roles_revoke` | WARNING | `bot_service.revoke_bot_roles` | `service_name`. |
| `bot.roles_purged_on_services_narrowed` | WARNING | `bot_service.update_bot` | `bot_id`, `bot_name`, `department_id`, `removed_services` (выкинутые из `allowed_services`), `removed_role_count`. Эмитим, когда сужение `allowed_services` обнуляет роли на ушедшие сервисы. |
| `bot.suspicious_multi_ip` | CRITICAL | `bot_ip_tracker.track_bot_ip` (sidecar в `/authorization/introspect`) | `bot_id`, `bot_name`, `ips` (уникальные IP за окно), `time_window_seconds` (длина окна из `BOT_SUSPICIOUS_IP_WINDOW_SECONDS`). Эмитим, когда за окно один bot-токен видели с >=2 разных IP. |

## OAuth2

`target_type=oauth_client`.

| Action | Default severity | Emitter | Key details |
|------|------|------|------|
| `oauth_client.create` | CRITICAL | `oauth_service.create_client` | `client_id`, `department_id`, `grant_types`, `redirect_uris`. |
| `oauth_client.list` | INFO | `GET /oauth2/clients` | — |
| `oauth_client.delete` | CRITICAL | `oauth_service.delete_client` | `client_id`. |
| `oauth.authorization_code_issued` | INFO | `oauth_service.issue_authorization_code` | `client_id`, `user_id`, `scope`, наличие PKCE. |
| `oauth.code_exchanged` | INFO | `oauth_service.exchange_code` | `client_id`, `user_id`. |
| `oauth.client_credentials_token` | INFO | `oauth_service.client_credentials_token` | `client_id`. |

## Token introspection

| Action | Default severity | Emitter | Key details |
|------|------|------|------|
| `token.introspect` | INFO | `authorization_service.introspect` | `subject_type`, `active`. Service-to-service. |

## Docker Registry

`target_type=docker_registry` для config-операций, `target_type=docker_token` для issue.

| Action | Default severity | Emitter | Key details |
|------|------|------|------|
| `docker_registry.configure` | CRITICAL | `docker_registry_service.create_or_replace_config` | `department_id`, `pull_policy`. |
| `docker_registry.update` | CRITICAL | `docker_registry_service.update_config` | Diff. |
| `docker_registry.get_config` | INFO | `GET /docker/registry/{id}` | — |
| `docker_registry.disable` | CRITICAL | `docker_registry_service.delete_config` | `department_id`. |
| `docker.token_issued` | INFO | `docker_registry_service.issue_token` | `scope`, `actions` (`pull`/`push`). На failure (account_locked, invalid_credentials) эмитится `status="failure"` с `reason`/`subject_type` в `details`. |
| `docker.push_denied` | WARNING | `docker_registry_service.issue_token` | `reason` (`PUSH_DEPT_MISMATCH` / `PUSH_PERMISSION_DENIED` / `REGISTRY_NOT_FOUND` / `REGISTRY_DISABLED`), `registry_name`, `scope`. |
| `docker.pull_denied` | INFO | `docker_registry_service.issue_token` | `reason` (`PULL_PERMISSION_DENIED` / `REGISTRY_NOT_FOUND` / `REGISTRY_DISABLED`), `registry_name`, `scope`. |

---

## Notes

- Все события идут асинхронно через `audit_service.emit_audit_event` (httpx → loging_service). Failure отправки логируется как WARNING, но не валит запрос.
- При недоступном loging_service события теряются — нет retry / outbox. Это известный gap.
- `request_id` коррелируется с `X-Request-ID` заголовком — у каждого HTTP-запроса свой.

## PII в audit-trail

Платформа работает в closed-contour (внутренний контур Astra Linux, без публичного доступа). По решению владельца **`email` пользователя допустимо хранить в audit-событиях без редакции** — наравне с `username` / `department_id`. Конкретно это касается:

- `details.target_email` и `details.email_before/after` в `user.update` (когда меняется email).
- `details.email` в любых других user-management событиях, если ручка работала именно с email-полем.

Обоснование:

- Audit-журнал в loging_service и так не доступен извне — read-доступ только у `loging_admin` / `loging_reader`. Закрытый контур делает retention + access control достаточной защитой.
- Без email'а в audit'е невозможно расследовать инциденты типа «account_admin молча подменил кому-то email на свой и сбросил пароль» — `username` и `user_id` сами по себе не дают вторую идентификационную координату.

Парольная и token-плоскость защищены отдельно — пароли всегда заменяются на `<PASSWORD>` через `audit_service.sanitize_details`, токены — на `<TOKEN>`. Email под маскировку **не** попадает.
