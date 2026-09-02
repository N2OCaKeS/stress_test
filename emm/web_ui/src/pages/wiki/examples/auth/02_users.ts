import type { ApiSection } from "../types";

export const USERS: ApiSection = {
  id: "users",
  title: "Пользователи",
  service: "auth",
  description:
    "CRUD пользователей, назначение service-ролей, бан/разбан, разблокировка " +
    "lockout'а, сброс и самостоятельная смена пароля, группы и снимок прав. " +
    "Почти всё требует роли account_admin или department_admin (своего отдела); " +
    "/users — только account_admin. Пароль в plaintext поверх TLS: минимум 12 " +
    "символов, обязательно буквы и цифры, иначе 422.",
  examples: [
    {
      id: "users-list",
      title: "Список пользователей (глобально, с пагинацией)",
      method: "GET",
      path: "/users",
      auth: "Bearer (account_admin)",
      description:
        "Глобальный список пользователей всех отделов. Только account_admin. " +
        "Пагинация через limit (1..200, default 50) и offset (>=0). Полное число " +
        "записей под текущий фильтр — в заголовке X-Total-Count. По умолчанию " +
        "возвращаются только active-юзеры; include_banned=true снимает фильтр.",
      notes:
        "department_admin сюда не пускают (нужен GET /users/department/{id}). " +
        "limit вне 1..200 или отрицательный offset → 422.",
      curl: `TOKEN="{{TOKEN}}"

curl "{{BASE_URL}}/api/auth/v1/users?limit=50&offset=0" \\
  -H "Authorization: Bearer $TOKEN"

# заголовок X-Total-Count несёт полное число записей:
curl -sD - -o /dev/null "{{BASE_URL}}/api/auth/v1/users?limit=50&offset=0" \\
  -H "Authorization: Bearer $TOKEN" | grep -i x-total-count`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

headers = {"Authorization": f"Bearer {TOKEN}"}
params = {"limit": 50, "offset": 0}

resp = requests.get(f"{BASE_URL}/api/auth/v1/users", headers=headers, params=params)
resp.raise_for_status()

total = resp.headers.get("X-Total-Count")
users = resp.json()
print(f"total={total}, на странице={len(users)}")
for u in users:
    print(u["user_id"], u["username"], u["status"])`,
    },
    {
      id: "users-list-status",
      title: "Список с фильтром по статусу",
      method: "GET",
      path: "/users",
      auth: "Bearer (account_admin)",
      description:
        "Пост-фильтр по статусу: status=active | banned | blocked. Если задан — " +
        "include_banned неявно становится true. Невалидное значение → 422 " +
        "INVALID_STATUS_FILTER.",
      curl: `TOKEN="{{TOKEN}}"

# только забаненные:
curl "{{BASE_URL}}/api/auth/v1/users?status=banned&limit=50" \\
  -H "Authorization: Bearer $TOKEN"

# active + banned + blocked (снять фильтр is_active):
curl "{{BASE_URL}}/api/auth/v1/users?include_banned=true&limit=50" \\
  -H "Authorization: Bearer $TOKEN"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

headers = {"Authorization": f"Bearer {TOKEN}"}

# status задан => include_banned неявно True
resp = requests.get(
    f"{BASE_URL}/api/auth/v1/users",
    headers=headers,
    params={"status": "banned", "limit": 50},
)
resp.raise_for_status()
print([u["username"] for u in resp.json()])`,
    },
    {
      id: "users-by-department",
      title: "Пользователи конкретного отдела",
      method: "GET",
      path: "/users/department/{department_id}",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Список юзеров одного отдела. account_admin видит любой отдел; " +
        "department_admin — только свой (чужой → 404, чтобы не было ID-oracle). " +
        "Те же query-параметры limit / offset / include_banned / status, что и у " +
        "GET /users; X-Total-Count считается в рамках отдела.",
      notes:
        "Несуществующий или чужой (для DA) department_id → 404 DEPARTMENT_NOT_FOUND.",
      curl: `TOKEN="{{TOKEN}}"
DEPARTMENT_ID="dep_9eab089d0271115c232a734001c004e0"

curl "{{BASE_URL}}/api/auth/v1/users/department/$DEPARTMENT_ID?limit=50&offset=0" \\
  -H "Authorization: Bearer $TOKEN"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
department_id = "dep_9eab089d0271115c232a734001c004e0"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.get(
    f"{BASE_URL}/api/auth/v1/users/department/{department_id}",
    headers=headers,
    params={"limit": 50, "offset": 0},
)
resp.raise_for_status()
print("total в отделе:", resp.headers.get("X-Total-Count"))
for u in resp.json():
    print(u["username"], u["status"])`,
    },
    {
      id: "users-create",
      title: "Создать пользователя",
      method: "POST",
      path: "/users",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Создаёт юзера с паролем (хэшируется Argon2id). department_id обязателен " +
        "для всех, кроме account_admin. department_admin может создавать только " +
        "в своём отделе и не account_admin. Опциональные initial_roles выдают " +
        "service-роли в той же транзакции. platform_role может задать только " +
        "account_admin.",
      notes:
        "Пароль ≥12 символов, буквы + цифры (иначе 422). По умолчанию новый юзер " +
        "обязан сменить пароль на первом входе; must_change_password=false снимает " +
        "это, но доступен только account_admin'у. Ошибки: 409 USER_ALREADY_EXISTS, " +
        "404 DEPARTMENT_NOT_FOUND, 403 DEPARTMENT_ACCESS_DENIED / " +
        "PLATFORM_ROLE_ASSIGNMENT_DENIED / CANNOT_BYPASS_PASSWORD_CHANGE.",
      curl: `TOKEN="{{TOKEN}}"
USERNAME="wiki_usr_alice"
PASSWORD="Passw0rd-12345"
DEPARTMENT_ID="dep_9eab089d0271115c232a734001c004e0"

curl -X POST "{{BASE_URL}}/api/auth/v1/users" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "username": "'"$USERNAME"'",
    "password": "'"$PASSWORD"'",
    "email": "alice@example.com",
    "department_id": "'"$DEPARTMENT_ID"'",
    "initial_roles": [
      { "service_name": "server_service", "roles": ["admin"] }
    ]
  }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

username = "wiki_usr_alice"
password = "Passw0rd-12345"  # >=12 символов, буквы + цифры
department_id = "dep_9eab089d0271115c232a734001c004e0"

headers = {"Authorization": f"Bearer {TOKEN}"}
payload = {
    "username": username,
    "password": password,
    "email": "alice@example.com",
    "department_id": department_id,
    "initial_roles": [
        {"service_name": "server_service", "roles": ["admin"]},
    ],
}

resp = requests.post(f"{BASE_URL}/api/auth/v1/users", headers=headers, json=payload)
resp.raise_for_status()  # 201 Created
user = resp.json()
print("создан:", user["user_id"], user["username"])`,
    },
    {
      id: "users-get",
      title: "Получить пользователя по id",
      method: "GET",
      path: "/users/{user_id}",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Карточка одного юзера (тот же UserResponse, что в списке, с именем " +
        "отдела). Для админ-UI, чтобы не таскать весь список ради одной строки.",
      notes: "Несуществующий user_id → 404 USER_NOT_FOUND.",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

curl "{{BASE_URL}}/api/auth/v1/users/$USER_ID" \\
  -H "Authorization: Bearer $TOKEN"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.get(f"{BASE_URL}/api/auth/v1/users/{user_id}", headers=headers)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "users-update",
      title: "Обновить пользователя (PATCH)",
      method: "PATCH",
      path: "/users/{user_id}",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Частичный апдейт — отправляй только меняемые поля: email, " +
        "department_id, status (UserStatus enum), platform_role (PlatformRole " +
        "enum). account_admin — любой юзер; department_admin — только свой отдел, " +
        "не account_admin.",
      notes:
        "Ошибки: 404 USER_NOT_FOUND; 403 USER_UPDATE_FORBIDDEN (DA в чужой отдел / " +
        "привилегированные поля), STATUS_CHANGE_REQUIRES_ACCOUNT_ADMIN (не-AA " +
        "меняет status на/с BANNED), PLATFORM_ROLE_ASSIGNMENT_DENIED.",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

curl -X PATCH "{{BASE_URL}}/api/auth/v1/users/$USER_ID" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{ "email": "alice.new@example.com" }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}
# отправляем только те поля, которые меняем
resp = requests.patch(
    f"{BASE_URL}/api/auth/v1/users/{user_id}",
    headers=headers,
    json={"email": "alice.new@example.com"},
)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "users-assign-roles",
      title: "Назначить service-роли пользователю",
      method: "POST",
      path: "/users/{user_id}/roles",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Replace-семантика для пары (user, service): переданный список roles " +
        "заменяет текущий набор для service_name. Чтобы снять все роли — передай " +
        "пустой список. Сервис должен быть в allowed_services отдела, а роли — " +
        "определены в каталоге (department, service).",
      notes:
        "Ошибки: 404 USER_NOT_FOUND; 409 USER_INACTIVE; 403 " +
        "USER_ROLE_UPDATE_FORBIDDEN / SERVICE_NOT_ALLOWED_FOR_DEPARTMENT; 422 " +
        "INVALID_SERVICE_ROLE (роль не в каталоге).",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

curl -X POST "{{BASE_URL}}/api/auth/v1/users/$USER_ID/roles" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{ "service_name": "server_service", "roles": ["admin"] }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.post(
    f"{BASE_URL}/api/auth/v1/users/{user_id}/roles",
    headers=headers,
    json={"service_name": "server_service", "roles": ["admin"]},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
    },
    {
      id: "users-permissions",
      title: "Полный снимок прав пользователя",
      method: "GET",
      path: "/users/{user_id}/permissions",
      auth: "Bearer (account_admin / department_admin / сам юзер)",
      description:
        "Три слоя в одном ответе: direct_service_roles (прямые роли), groups " +
        "(группы юзера с их service-access и ролями) и effective view " +
        "(allowed_services + service_roles, INTERSECT). account_admin — любой; " +
        "department_admin — свой отдел; обычный юзер — только себя.",
      notes: "Ошибки: 404 USER_NOT_FOUND; 403 PERMISSION_DENIED.",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

curl "{{BASE_URL}}/api/auth/v1/users/$USER_ID/permissions" \\
  -H "Authorization: Bearer $TOKEN"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.get(
    f"{BASE_URL}/api/auth/v1/users/{user_id}/permissions", headers=headers
)
resp.raise_for_status()
perms = resp.json()
print("прямые роли:", perms["direct_service_roles"])
print("effective:", perms["service_roles"])`,
    },
    {
      id: "users-groups-list",
      title: "Группы, в которых состоит пользователь",
      method: "GET",
      path: "/users/{user_id}/groups",
      auth: "Bearer (account_admin / department_admin / сам юзер)",
      description:
        "Список групп юзера. account_admin — любой; department_admin — только " +
        "свой отдел (чужой → 404, не 403, чтобы избежать ID-enum oracle); " +
        "обычный юзер — только себя.",
      notes: "Несуществующий user_id → 404 USER_NOT_FOUND (не пустой список).",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

curl "{{BASE_URL}}/api/auth/v1/users/$USER_ID/groups" \\
  -H "Authorization: Bearer $TOKEN"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.get(
    f"{BASE_URL}/api/auth/v1/users/{user_id}/groups", headers=headers
)
resp.raise_for_status()
print(resp.json())  # list[UserGroupsResponse]`,
    },
    {
      id: "users-reset-password",
      title: "Сбросить пароль пользователю (admin)",
      method: "POST",
      path: "/users/{user_id}/reset-password",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Admin-смена чужого пароля: записывает новый Argon2id-хэш и сразу " +
        "revoke'ит все активные сессии и PAT юзера. account_admin — любой; " +
        "department_admin — только свой отдел. Для самостоятельной смены — " +
        "POST /users/me/password.",
      notes:
        "new_password ≥12 символов, буквы + цифры. Ошибки: 404 USER_NOT_FOUND; " +
        "403 USER_RESET_PASSWORD_FORBIDDEN; 401 ACTOR_VANISHED.",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"
NEW_PASSWORD="NewPassw0rd-67890"

curl -X POST "{{BASE_URL}}/api/auth/v1/users/$USER_ID/reset-password" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{ "new_password": "'"$NEW_PASSWORD"'" }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"
new_password = "NewPassw0rd-67890"  # >=12 символов, буквы + цифры

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.post(
    f"{BASE_URL}/api/auth/v1/users/{user_id}/reset-password",
    headers=headers,
    json={"new_password": new_password},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
    },
    {
      id: "users-self-password",
      title: "Сменить собственный пароль",
      method: "POST",
      path: "/users/me/password",
      auth: "Bearer (user-context)",
      description:
        "Self-reset с обязательным подтверждением old_password. После успеха все " +
        "активные сессии юзера (включая текущую) revoke'ятся — нужен повторный " +
        "login; PAT остаются валидными. m2m-токены (oauth_client) сюда не " +
        "пускают.",
      notes:
        "new_password ≥12 символов, буквы + цифры. Ошибки: 401 " +
        "INVALID_OLD_PASSWORD (инкрементит lockout-счётчик), 422 SAME_PASSWORD, " +
        "429 ACCOUNT_TEMPORARILY_LOCKED.",
      curl: `# токен здесь — самого юзера, а НЕ админа (меняем собственный пароль)
TOKEN="{{TOKEN}}"
OLD_PASSWORD="Passw0rd-12345"
NEW_PASSWORD="Passw0rd-67890"

curl -X POST "{{BASE_URL}}/api/auth/v1/users/me/password" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{ "old_password": "'"$OLD_PASSWORD"'", "new_password": "'"$NEW_PASSWORD"'" }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"  # токен самого юзера, не админа

old_password = "Passw0rd-12345"
new_password = "Passw0rd-67890"  # >=12 символов, буквы + цифры

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.post(
    f"{BASE_URL}/api/auth/v1/users/me/password",
    headers=headers,
    json={"old_password": old_password, "new_password": new_password},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}; после этого нужен повторный login`,
    },
    {
      id: "users-ban",
      title: "Забанить пользователя",
      method: "POST",
      path: "/users/{user_id}/ban",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Permanent или temporary бан. Снимает все активные сессии + PAT юзера + " +
        "bot-токены принадлежащих ему ботов. temporary требует expires_at в " +
        "будущем; permanent — expires_at запрещён. account_admin — любой; " +
        "department_admin — только юзер своего отдела (платформенного забанить " +
        "не может).",
      notes:
        "Ошибки: 404 USER_NOT_FOUND; 422 CANNOT_BAN_SELF и cross-field " +
        "инварианты (expires_at в прошлом / permanent с expires_at / temporary " +
        "без expires_at); 403 DEPT_MISMATCH.",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

# permanent:
curl -X POST "{{BASE_URL}}/api/auth/v1/users/$USER_ID/ban" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{ "ban_type": "permanent", "reason": "policy violation" }'

# temporary (expires_at в будущем, ISO-8601 UTC):
curl -X POST "{{BASE_URL}}/api/auth/v1/users/$USER_ID/ban" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{ "ban_type": "temporary", "reason": "cooldown", "expires_at": "2026-12-31T23:59:59Z" }'`,
      python: `import requests
from datetime import datetime, timedelta, timezone

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}

# temporary бан на 1 час
expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
resp = requests.post(
    f"{BASE_URL}/api/auth/v1/users/{user_id}/ban",
    headers=headers,
    json={"ban_type": "temporary", "reason": "cooldown", "expires_at": expires_at},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
    },
    {
      id: "users-unban",
      title: "Снять бан с пользователя",
      method: "POST",
      path: "/users/{user_id}/unban",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Отзывает активный ban. Сессии не восстанавливает — юзеру надо " +
        "логиниться заново. account_admin — любой; department_admin — только " +
        "свой отдел (симметрично ban-у).",
      notes:
        "Ошибки: 404 USER_NOT_FOUND / BAN_NOT_FOUND (нет активного бана); 403 " +
        "DEPT_MISMATCH.",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

curl -X POST "{{BASE_URL}}/api/auth/v1/users/$USER_ID/unban" \\
  -H "Authorization: Bearer $TOKEN"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.post(
    f"{BASE_URL}/api/auth/v1/users/{user_id}/unban", headers=headers
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
    },
    {
      id: "users-unlock",
      title: "Снять brute-force lockout с пользователя",
      method: "POST",
      path: "/users/{user_id}/unlock",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Сбрасывает счётчик неудачных логинов (failed_login_attempts) и окно " +
        "lockout'а (locked_until) — после этого юзер логинится сразу, не " +
        "дожидаясь истечения 15-минутной блокировки. Бан и статус не " +
        "затрагиваются. Idempotent: разлочить незалоченного → 200 (no-op).",
      notes: "Ошибки: 404 USER_NOT_FOUND; 403 DEPT_MISMATCH.",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

curl -X POST "{{BASE_URL}}/api/auth/v1/users/$USER_ID/unlock" \\
  -H "Authorization: Bearer $TOKEN"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.post(
    f"{BASE_URL}/api/auth/v1/users/{user_id}/unlock", headers=headers
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
    },
    {
      id: "users-delete",
      title: "Hard-delete пользователя",
      method: "DELETE",
      path: "/users/{user_id}",
      auth: "Bearer (account_admin)",
      description:
        "Физически сносит строку в users; ORM-cascade уносит сессии, PAT, Ban, " +
        "service-роли и членства в группах. Боты, созданные юзером, НЕ трогаются " +
        "(бот — dept-owned). После commit'а secret_service получает best-effort " +
        "notify, блокирующий personal credentials удалённого юзера. reason " +
        "обязателен (1..256 символов).",
      notes:
        "Только account_admin (department_admin не пускают). Ошибки: 404 " +
        "USER_NOT_FOUND; 422 LAST_ACCOUNT_ADMIN (нельзя снести последнего " +
        "активного account_admin); 403 ROLE_REQUIRED.",
      curl: `TOKEN="{{TOKEN}}"
USER_ID="usr_48c112e2b62145789e014ea19074edc2"

curl -X DELETE "{{BASE_URL}}/api/auth/v1/users/$USER_ID" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{ "reason": "Q3 reorg / left company" }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
user_id = "usr_48c112e2b62145789e014ea19074edc2"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/users/{user_id}",
    headers=headers,
    json={"reason": "Q3 reorg / left company"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
    },
  ],
};
