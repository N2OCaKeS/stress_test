import type { ApiSection } from "../types";

export const DEPTS_SERVICES_ROLES: ApiSection = {
  id: "departments-services-roles",
  title: "Департаменты, сервисы и роли",
  service: "auth",
  description:
    "Управление отделами, регистром платформенных сервисов, выдачей отделу " +
    "доступа к сервису и каталогом ролей в scope (отдел × сервис). " +
    "Базовый путь — /api/auth/v1. Все операции требуют account_admin " +
    "(каталог ролей дополнительно доступен department_admin своего отдела).",
  examples: [
    // ---------------------------------------------------------------- departments
    {
      id: "departments-list",
      title: "Список отделов",
      method: "GET",
      path: "/api/auth/v1/departments",
      auth: "Bearer (account_admin)",
      description:
        "Все отделы платформы. Каждый элемент несёт user_count — число " +
        "привязанных к отделу пользователей (по department_id, без фильтра " +
        "is_active; боты не считаются).",
      curl: `curl -sS {{BASE_URL}}/api/auth/v1/departments \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/departments",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
resp.raise_for_status()
for dept in resp.json():
    print(dept["department_id"], dept["name"], dept["user_count"])`,
      notes:
        "Только account_admin. department_admin свой отдел видит через GET /me. " +
        "Ответ — плоский список DepartmentResponse.",
    },
    {
      id: "departments-create",
      title: "Создать отдел",
      method: "POST",
      path: "/api/auth/v1/departments",
      auth: "Bearer (account_admin)",
      description:
        "Создаёт отдел. name — человеческое имя, уникальное по платформе " +
        "(1..128 символов, обрезается по краям).",
      curl: `curl -sS -X POST {{BASE_URL}}/api/auth/v1/departments \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"name": "wiki_dsr_dept"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_name = "wiki_dsr_dept"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/departments",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"name": dept_name},
)
resp.raise_for_status()
dept = resp.json()
print("department_id:", dept["department_id"])`,
      notes:
        "201 при успехе. DEPARTMENT_ALREADY_EXISTS (409) — отдел с таким name уже есть.",
    },
    {
      id: "departments-update",
      title: "Обновить name / description отдела",
      method: "PATCH",
      path: "/api/auth/v1/departments/{department_id}",
      auth: "Bearer (account_admin)",
      description:
        "Точечный апдейт name и/или description. Оба поля опциональны, но " +
        "хотя бы одно обязано присутствовать — пустое тело отбивается 422.",
      curl: `curl -sS -X PATCH {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"description": "Нагрузочное тестирование"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"

resp = requests.patch(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"description": "Нагрузочное тестирование"},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "Ошибки: DEPARTMENT_NOT_FOUND (404), EMPTY_UPDATE (422) — оба поля не " +
        "переданы, DEPARTMENT_ALREADY_EXISTS (409) — name занят другим отделом, " +
        "ROLE_REQUIRED (403).",
    },
    {
      id: "departments-services-list",
      title: "Список сервисов с активным grant'ом для отдела",
      method: "GET",
      path: "/api/auth/v1/departments/{department_id}/services",
      auth: "Bearer (AnyAdmin)",
      description:
        "Возвращает список service_name с активным DepartmentServiceAccess — " +
        "сервисы, к которым отдел реально подключён.",
      curl: `curl -sS {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
resp.raise_for_status()
print(resp.json())  # ["config_service", ...]`,
      notes:
        "Read-only листинг доступен любому админу (AnyAdmin), scope не " +
        "ограничивается. DEPARTMENT_NOT_FOUND (404).",
    },
    {
      id: "departments-services-grant",
      title: "Выдать отделу access к сервису",
      method: "POST",
      path: "/api/auth/v1/departments/{department_id}/services",
      auth: "Bearer (account_admin)",
      description:
        "Создаёт DepartmentServiceAccess. Без этой связки юзеры/боты отдела " +
        "не смогут получить роль для сервиса. При выдаче доступа автоматически " +
        "сеется системная роль admin в scope (отдел, сервис).",
      curl: `curl -sS -X POST {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"service_name": "wiki_dsr_svc"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"
service_name = "wiki_dsr_svc"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"service_name": service_name},
)
resp.raise_for_status()
print(resp.json())  # {"department_id": ..., "service_name": ..., "enabled": true}`,
      notes:
        "201 при успехе. Только account_admin. Сервис должен быть " +
        "зарегистрирован (см. POST /services).",
    },
    {
      id: "departments-services-revoke",
      title: "Отозвать у отдела access к сервису",
      method: "DELETE",
      path: "/api/auth/v1/departments/{department_id}/services/{service_name}",
      auth: "Bearer (account_admin)",
      description:
        "Снимает DepartmentServiceAccess. После revoke роли юзеров/ботов на " +
        "этот сервис формально остаются, но эффективно отбрасываются на " +
        "INTERSECT в introspect / effective view.",
      curl: `curl -sS -X DELETE \\
  {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services/wiki_dsr_svc \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"
service_name = "wiki_dsr_svc"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services/{service_name}",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes: "Только account_admin. Возвращает {\"ok\": true}.",
    },
    {
      id: "departments-delete",
      title: "Hard-delete отдела",
      method: "DELETE",
      path: "/api/auth/v1/departments/{department_id}",
      auth: "Bearer (account_admin)",
      description:
        "Жёсткое удаление отдела: row в departments сносится физически. " +
        "CASCADE-FK уносят DepartmentServiceAccess, ServiceRoleDefinition, " +
        "UserGroup (с membership'ами и role-bindings), DepartmentDockerRegistry; " +
        "боты и oauth_clients отдела удаляются явно. reason обязателен (1..256).",
      curl: `curl -sS -X DELETE {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"reason": "Q3 reorg / department closed"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"reason": "Q3 reorg / department closed"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "Ошибки: DEPARTMENT_NOT_FOUND (404), USERS_REMAIN_IN_DEPT (422) — в " +
        "отделе остался хотя бы один активный юзер (сначала перевести их через " +
        "PATCH /users/{id} или снести через DELETE /users/{id}), ROLE_REQUIRED " +
        "(403).",
    },
    // ---------------------------------------------------------------- services
    {
      id: "services-list",
      title: "Список платформенных сервисов",
      method: "GET",
      path: "/api/auth/v1/services",
      auth: "Bearer (account_admin)",
      description:
        "Регистр всех известных сервисов платформы (auth_service, " +
        "loging_service, server_service, secret_service и т.д.).",
      curl: `curl -sS {{BASE_URL}}/api/auth/v1/services \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/services",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
resp.raise_for_status()
for svc in resp.json():
    print(svc["service_name"], svc["is_active"])`,
      notes: "Только account_admin. Ответ — список ServiceResponse.",
    },
    {
      id: "services-create",
      title: "Зарегистрировать сервис",
      method: "POST",
      path: "/api/auth/v1/services",
      auth: "Bearer (account_admin)",
      description:
        "Создаёт запись PlatformService. service_name — стабильный машинный " +
        "ID, lower-snake_case, начинается с буквы (regex ^[a-z][a-z0-9_]+$, " +
        "2..64 символа). После создания можно выдавать отделам access.",
      curl: `curl -sS -X POST {{BASE_URL}}/api/auth/v1/services \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"service_name": "wiki_dsr_svc", "description": "demo service"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

service_name = "wiki_dsr_svc"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/services",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"service_name": service_name, "description": "demo service"},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "201 при успехе. SERVICE_ALREADY_EXISTS (409) — такой service_name уже " +
        "зарегистрирован. display_name в теле нет — только service_name + " +
        "description.",
    },
    {
      id: "services-delete",
      title: "Удалить сервис из регистра",
      method: "DELETE",
      path: "/api/auth/v1/services/{service_name}",
      auth: "Bearer (account_admin)",
      description:
        "Снимает сервис с регистрации. Каскадно деактивирует все " +
        "DepartmentServiceAccess, UserServiceRole, BotServiceRole и " +
        "ServiceRoleDefinition для этого сервиса; не валится 409 при наличии " +
        "связей.",
      curl: `curl -sS -X DELETE {{BASE_URL}}/api/auth/v1/services/wiki_dsr_svc \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

service_name = "wiki_dsr_svc"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/services/{service_name}",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "SERVICE_NOT_FOUND (404).",
    },
    // ---------------------------------------------------------------- service roles
    {
      id: "service-roles-list",
      title: "Список ролей в scope (отдел × сервис)",
      method: "GET",
      path: "/api/auth/v1/departments/{department_id}/services/{service_name}/roles",
      auth: "Bearer (account_admin / department_admin / юзер с access)",
      description:
        "Все ServiceRoleDefinition в scope (department, service). Системная " +
        "роль admin (is_system=true) присутствует всегда — она сеется при " +
        "выдаче отделу access к сервису.",
      curl: `curl -sS \\
  {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services/wiki_dsr_svc/roles \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"
service_name = "wiki_dsr_svc"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services/{service_name}/roles",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
resp.raise_for_status()
for role in resp.json():
    print(role["role_name"], "system" if role["is_system"] else "custom")`,
      notes:
        "Доступ: account_admin / department_admin своего отдела / юзер с access " +
        "к сервису.",
    },
    {
      id: "service-roles-create",
      title: "Создать определение роли",
      method: "POST",
      path: "/api/auth/v1/departments/{department_id}/services/{service_name}/roles",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Регистрирует роль в scope (отдел, сервис). Уникальность по тройке " +
        "(department_id, service_name, role_name). role_name — lower-snake_case " +
        "(regex ^[a-z][a-z0-9_]+$, 2..64). После создания роль можно назначать " +
        "юзерам/ботам/группам.",
      curl: `curl -sS -X POST \\
  {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services/wiki_dsr_svc/roles \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"role_name": "wiki_dsr_role", "description": "demo role"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"
service_name = "wiki_dsr_svc"
role_name = "wiki_dsr_role"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services/{service_name}/roles",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"role_name": role_name, "description": "demo role"},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "201 при успехе. Ошибки: SERVICE_ROLE_ALREADY_EXISTS (409), " +
        "SERVICE_NOT_GRANTED_FOR_DEPARTMENT (403) — у отдела нет access к " +
        "сервису, SERVICE_ROLE_MGMT_FORBIDDEN (403) — нет прав управления " +
        "ролями в scope. В теле только role_name + description (display_name нет).",
    },
    {
      id: "service-roles-update",
      title: "Обновить description роли",
      method: "PATCH",
      path: "/api/auth/v1/departments/{department_id}/services/{service_name}/roles/{role_name}",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Patch определения роли. Меняется только description (role_name " +
        "переименовать нельзя — это ключ).",
      curl: `curl -sS -X PATCH \\
  {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services/wiki_dsr_svc/roles/wiki_dsr_role \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"description": "updated description"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"
service_name = "wiki_dsr_svc"
role_name = "wiki_dsr_role"

resp = requests.patch(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services/{service_name}/roles/{role_name}",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"description": "updated description"},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "SERVICE_ROLE_NOT_FOUND (404), SERVICE_ROLE_SYSTEM_LOCKED (403) — " +
        "системную роль (is_system=true) менять нельзя.",
    },
    {
      id: "service-roles-delete",
      title: "Удалить определение роли",
      method: "DELETE",
      path: "/api/auth/v1/departments/{department_id}/services/{service_name}/roles/{role_name}",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Удаляет ServiceRoleDefinition из scope. Системная роль admin " +
        "(is_system=true) защищена и не удаляется.",
      curl: `curl -sS -X DELETE \\
  {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services/wiki_dsr_svc/roles/wiki_dsr_role \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"
service_name = "wiki_dsr_svc"
role_name = "wiki_dsr_role"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services/{service_name}/roles/{role_name}",
    headers={"Authorization": f"Bearer {TOKEN}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "SERVICE_ROLE_SYSTEM_LOCKED (403) — нельзя снести системную " +
        "(is_system=true), SERVICE_ROLE_NOT_FOUND (404).",
    },
    {
      id: "service-roles-assign",
      title: "Bulk-назначение роли списку юзеров",
      method: "POST",
      path: "/api/auth/v1/departments/{department_id}/services/{service_name}/roles/{role_name}/assign",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "За один запрос выдаёт роль role_name всем юзерам из user_ids. " +
        "Идемпотентно создаёт UserServiceRole для каждого; юзеры из чужого " +
        "отдела отбрасываются. Максимум 200 user_ids за запрос.",
      curl: `curl -sS -X POST \\
  {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services/wiki_dsr_svc/roles/wiki_dsr_role/assign \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"user_ids": ["usr_aaa", "usr_bbb"]}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"
service_name = "wiki_dsr_svc"
role_name = "wiki_dsr_role"
user_ids = ["usr_aaa", "usr_bbb"]

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services/{service_name}/roles/{role_name}/assign",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"user_ids": user_ids},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "Idempotent. user_ids — max 200 (иначе 422). Возвращает {\"ok\": true}.",
    },
    {
      id: "service-roles-revoke",
      title: "Bulk-снятие роли с группы юзеров",
      method: "POST",
      path: "/api/auth/v1/departments/{department_id}/services/{service_name}/roles/{role_name}/revoke",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Снимает роль role_name со всех юзеров из user_ids. Idempotent, " +
        "симметрично assign. Максимум 200 user_ids за запрос.",
      curl: `curl -sS -X POST \\
  {{BASE_URL}}/api/auth/v1/departments/dep_xxxxxxxx/services/wiki_dsr_svc/roles/wiki_dsr_role/revoke \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"user_ids": ["usr_aaa", "usr_bbb"]}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

dept_id = "dep_xxxxxxxx"
service_name = "wiki_dsr_svc"
role_name = "wiki_dsr_role"
user_ids = ["usr_aaa", "usr_bbb"]

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/departments/{dept_id}/services/{service_name}/roles/{role_name}/revoke",
    headers={"Authorization": f"Bearer {TOKEN}"},
    json={"user_ids": user_ids},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "Idempotent. user_ids — max 200 (иначе 422). Возвращает {\"ok\": true}.",
    },
  ],
};
