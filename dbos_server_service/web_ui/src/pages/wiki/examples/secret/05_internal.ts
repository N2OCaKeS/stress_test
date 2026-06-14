import type { ApiSection } from "../types";

export const INTERNAL: ApiSection = {
  id: "internal",
  title: "Internal lifecycle (service-to-service)",
  service: "secret",
  description:
    "Служебная плоскость secret_service. Эти ручки НЕ предназначены для обычных клиентов: их дёргает auth_service после каскадных операций (удалил пользователя / отдел / отозвал у отдела доступ к сервису), чтобы secret_service синхронно почистил свои креды, DeptGrant'ы и RoleACL. Скрыты из публичного OpenAPI (include_in_schema=False) и не появляются в /docs. Аутентификация здесь — НЕ user-JWT, а shared service-bearer: Authorization: Bearer <SECRET_INTERNAL_API_KEY> (в dev-стеке значение = dev-introspect-api-key). Lifecycle-эндпоинты дополнительно требуют заголовок X-Service-Identity: auth_service — звать их может только auth_service. Любой другой caller / неверный ключ / отсутствие заголовка → 401 INTERNAL_AUTH_REQUIRED. Каждый handler атомарен; накопленные ошибки → 500 LIFECYCLE_HANDLER_FAILED с details.errors. Здесь {{INTERNAL_KEY}} — это shared service-key (dev: dev-introspect-api-key), а не пользовательский токен.",
  examples: [
    {
      id: "internal-user-deleted",
      title: "user-deleted — каскад на personal-креды удалённого юзера",
      method: "POST",
      path: "/api/secret/v1/internal/lifecycle/user-deleted",
      auth: "Internal bearer {{INTERNAL_KEY}} + X-Service-Identity: auth_service (только auth_service)",
      description:
        "auth_service сообщает, что пользователь удалён. secret_service проходит по его personal-кредам: те, у которых есть RoleACL-grantee'и (RoleACL.count > 0), переводятся в blocked (status=blocked, blocked_reason=owner_user_deleted) — потом admin secret_service'а сможет их transfer'нуть новому владельцу; «сиротские» креды (никаких grant'ов) удаляются физически. Это внутренний callback, не клиентская операция: вызывается каскадом из DELETE /users/{id} в auth_service.",
      curl: `# {{INTERNAL_KEY}} — shared service-key (в dev = dev-introspect-api-key),
# НЕ пользовательский Bearer-токен.
curl -X POST {{BASE_URL}}/api/secret/v1/internal/lifecycle/user-deleted \\
  -H "Authorization: Bearer {{INTERNAL_KEY}}" \\
  -H "X-Service-Identity: auth_service" \\
  -H "Content-Type: application/json" \\
  -d '{
    "user_id": "usr_271b5d82bb42445b82beb20378184f56",
    "actor_id": "usr_45c4c368a51a4dc8992ba81697da0086",
    "actor_username": "admin"
  }'
# 200: {"blocked_count":1,"deleted_count":0,"dept_grants_revoked":0,"role_acls_revoked":0,"errors":[]}`,
      python: `import requests

base_url = "{{BASE_URL}}"
# shared service-key, в dev-стеке = "dev-introspect-api-key"
internal_key = "{{INTERNAL_KEY}}"

resp = requests.post(
    f"{base_url}/api/secret/v1/internal/lifecycle/user-deleted",
    headers={
        "Authorization": f"Bearer {internal_key}",
        # lifecycle-ручки принимают только auth_service
        "X-Service-Identity": "auth_service",
    },
    json={
        "user_id": "usr_271b5d82bb42445b82beb20378184f56",
        "actor_id": "usr_45c4c368a51a4dc8992ba81697da0086",
        "actor_username": "admin",  # для аудита
    },
)
resp.raise_for_status()
summary = resp.json()
# blocked_count — креды с grant'ами ушли в blocked;
# deleted_count — orphan-креды снесены физически
print(summary["blocked_count"], summary["deleted_count"])`,
      notes:
        "Response 200 LifecycleSummary: { blocked_count, deleted_count, dept_grants_revoked, role_acls_revoked, errors }. Аудит: tokens.owner_user_deleted_block (WARNING) на blocked + tokens.delete (WARNING) на orphan. Без X-Service-Identity (или с чужим именем) → 401 INTERNAL_AUTH_REQUIRED; неверный bearer → тоже 401. Ошибки внутри handler'а → 500 LIFECYCLE_HANDLER_FAILED с details.errors (транзакция откачена).",
    },
    {
      id: "internal-dept-deleted",
      title: "dept-deleted — каскад owner + recipient",
      method: "POST",
      path: "/api/secret/v1/internal/lifecycle/dept-deleted",
      auth: "Internal bearer {{INTERNAL_KEY}} + X-Service-Identity: auth_service (только auth_service)",
      description:
        "auth_service сообщает, что отдел удалён. secret_service каскадит обе ветки: (1) отдел как owner кред — все его креды переводятся в blocked (account_admin потом transfer'нёт); (2) отдел как recipient в cross_department-схеме — сносятся все DeptGrant'ы на него и все RoleACL внутри этого отдела. Внутренний callback из DELETE /departments/{id}.",
      curl: `curl -X POST {{BASE_URL}}/api/secret/v1/internal/lifecycle/dept-deleted \\
  -H "Authorization: Bearer {{INTERNAL_KEY}}" \\
  -H "X-Service-Identity: auth_service" \\
  -H "Content-Type: application/json" \\
  -d '{
    "dept_id": "dep_45dd16bf6b0245a59eb9c1e3ee20c6d5",
    "actor_id": "usr_45c4c368a51a4dc8992ba81697da0086",
    "actor_username": "admin"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
internal_key = "{{INTERNAL_KEY}}"  # dev: dev-introspect-api-key

resp = requests.post(
    f"{base_url}/api/secret/v1/internal/lifecycle/dept-deleted",
    headers={
        "Authorization": f"Bearer {internal_key}",
        "X-Service-Identity": "auth_service",
    },
    json={
        "dept_id": "dep_45dd16bf6b0245a59eb9c1e3ee20c6d5",
        "actor_id": "usr_45c4c368a51a4dc8992ba81697da0086",
        "actor_username": "admin",
    },
)
resp.raise_for_status()
summary = resp.json()
# blocked_count (как owner) + dept_grants_revoked / role_acls_revoked (как recipient)
print(summary)`,
      notes:
        "Response 200 LifecycleSummary: { blocked_count, dept_grants_revoked, role_acls_revoked, errors }. Аудит: tokens.owner_dept_deleted_block (WARNING) + tokens.dept_recipient_cascade (CRITICAL). auth_service шлёт этот callback best-effort после commit'а hard-delete'а отдела.",
    },
    {
      id: "internal-dept-service-access-revoked",
      title: "dept-service-access-revoked — снять cross-dep доступ отдела",
      method: "POST",
      path: "/api/secret/v1/internal/lifecycle/dept-service-access-revoked",
      auth: "Internal bearer {{INTERNAL_KEY}} + X-Service-Identity: auth_service (только auth_service)",
      description:
        "auth_service сообщает, что у отдела отозван доступ к secret_service (удалена запись в department_service_access). secret_service сносит все DeptGrant'ы, где этот отдел — recipient, и все его RoleACL. Собственные креды отдела остаются (через transfer возможен recover). Поле service должно быть secret_service — иначе событие игнорируется (no-op).",
      curl: `curl -X POST {{BASE_URL}}/api/secret/v1/internal/lifecycle/dept-service-access-revoked \\
  -H "Authorization: Bearer {{INTERNAL_KEY}}" \\
  -H "X-Service-Identity: auth_service" \\
  -H "Content-Type: application/json" \\
  -d '{
    "dept_id": "dep_45dd16bf6b0245a59eb9c1e3ee20c6d5",
    "service": "secret_service",
    "actor_id": "usr_45c4c368a51a4dc8992ba81697da0086",
    "actor_username": "admin"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
internal_key = "{{INTERNAL_KEY}}"  # dev: dev-introspect-api-key

resp = requests.post(
    f"{base_url}/api/secret/v1/internal/lifecycle/dept-service-access-revoked",
    headers={
        "Authorization": f"Bearer {internal_key}",
        "X-Service-Identity": "auth_service",
    },
    json={
        "dept_id": "dep_45dd16bf6b0245a59eb9c1e3ee20c6d5",
        # только secret_service распознаётся; остальное — no-op
        "service": "secret_service",
        "actor_id": "usr_45c4c368a51a4dc8992ba81697da0086",
        "actor_username": "admin",
    },
)
resp.raise_for_status()
print(resp.json())  # { dept_grants_revoked, role_acls_revoked, errors }`,
      notes:
        "Response 200 LifecycleSummary: { dept_grants_revoked, role_acls_revoked, errors }. service != secret_service → no-op (нулевые счётчики). Аудит: tokens.dept_revoke_cascade (CRITICAL). Свои креды отдела НЕ трогаются — их блокирует только dept-deleted.",
    },
  ],
};
