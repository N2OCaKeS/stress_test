import type { ApiSection } from "../types";

export const GROUPS: ApiSection = {
  id: "groups",
  title: "Группы",
  service: "auth",
  description:
    "Пользовательские группы. Группа всегда привязана к отделу; внутри отдела имя уникально. " +
    "В группе состоят юзеры и боты, группе выдаётся access к сервисам и назначаются service-роли — " +
    "члены группы наследуют их через effective view (INTERSECT с access сервиса). " +
    "account_admin видит и правит любые группы, department_admin — только своего отдела.",
  examples: [
    {
      id: "group-create",
      title: "Создать группу",
      method: "POST",
      path: "/api/auth/v1/groups",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Создаёт группу в отделе. Поля name/description (не display_name); name обязателен, " +
        "уникален внутри отдела. department_id — отдел, к которому привязываем группу. " +
        "Возвращает 201 и объект группы с id (grp_…).",
      curl: `department_id="dep_xxxxxxxx"   # ID отдела (см. GET /departments)

curl -X POST {{BASE_URL}}/api/auth/v1/groups \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "department_id": "'"$department_id"'",
    "name": "wiki_grp_devs",
    "description": "Группа разработчиков"
  }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

department_id = "dep_xxxxxxxx"   # ID отдела (см. GET /departments)
group_name = "wiki_grp_devs"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/groups",
    headers=headers,
    json={
        "department_id": department_id,
        "name": group_name,
        "description": "Группа разработчиков",
    },
)
resp.raise_for_status()
group_id = resp.json()["id"]
print(group_id)`,
      notes:
        "Ошибки: GROUP_ALREADY_EXISTS (409) — имя занято в отделе; " +
        "DEPARTMENT_ACCESS_DENIED (403) — department_admin создаёт в чужом отделе; " +
        "DEPARTMENT_NOT_FOUND (404).",
    },
    {
      id: "group-list",
      title: "Список групп (пагинация)",
      method: "GET",
      path: "/api/auth/v1/groups",
      auth: "Bearer",
      description:
        "Список групп с учётом scope смотрящего: account_admin видит все, " +
        "department_admin / обычный юзер — только свой отдел. Query-параметры limit/offset, " +
        "общее число строк — в заголовке X-Total-Count.",
      curl: `curl -s -D - {{BASE_URL}}/api/auth/v1/groups?limit=20&offset=0 \\
  -H "Authorization: Bearer {{TOKEN}}"
# X-Total-Count в заголовках ответа`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/groups",
    headers=headers,
    params={"limit": 20, "offset": 0},
)
resp.raise_for_status()
total = resp.headers.get("X-Total-Count")
print(total, resp.json())`,
    },
    {
      id: "group-get",
      title: "Получить группу",
      method: "GET",
      path: "/api/auth/v1/groups/{group_id}",
      auth: "Bearer",
      description:
        "Одиночная группа по id. account_admin — любая, department_admin — только своего отдела.",
      curl: `group_id="grp_xxxxxxxx"

curl {{BASE_URL}}/api/auth/v1/groups/$group_id \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
      notes: "Ошибки: GROUP_NOT_FOUND (404); DEPARTMENT_ACCESS_DENIED (403) — чужой отдел.",
    },
    {
      id: "group-update",
      title: "Обновить группу",
      method: "PATCH",
      path: "/api/auth/v1/groups/{group_id}",
      auth: "Bearer (admin своего отдела)",
      description:
        "Патчит name и/или description. Оба поля опциональны — передавайте только то, что меняете.",
      curl: `group_id="grp_xxxxxxxx"

curl -X PATCH {{BASE_URL}}/api/auth/v1/groups/$group_id \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"description": "Группа разработчиков (обновлено)"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"

resp = requests.patch(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}",
    headers=headers,
    json={"description": "Группа разработчиков (обновлено)"},
)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "group-delete",
      title: "Удалить группу",
      method: "DELETE",
      path: "/api/auth/v1/groups/{group_id}",
      auth: "Bearer (admin своего отдела)",
      description:
        "Сносит группу. Каскадно убирает всех members/bot-members и group_service_roles/access. " +
        "Возвращает {\"ok\": true}.",
      curl: `group_id="grp_xxxxxxxx"

curl -X DELETE {{BASE_URL}}/api/auth/v1/groups/$group_id \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "group-members-list",
      title: "Состав группы (юзеры)",
      method: "GET",
      path: "/api/auth/v1/groups/{group_id}/members",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Юзеры, состоящие в группе: user_id, username, added_at. " +
        "Регулярный member группы доступа к составу не имеет.",
      curl: `group_id="grp_xxxxxxxx"

curl {{BASE_URL}}/api/auth/v1/groups/$group_id/members \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/members",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "group-members-add",
      title: "Добавить юзера в группу",
      method: "POST",
      path: "/api/auth/v1/groups/{group_id}/members",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Добавляет membership. Юзер и группа должны быть в одном отделе. Возвращает 201.",
      curl: `group_id="grp_xxxxxxxx"
user_id="usr_xxxxxxxx"

curl -X POST {{BASE_URL}}/api/auth/v1/groups/$group_id/members \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"user_id": "'"$user_id"'"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"
user_id = "usr_xxxxxxxx"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/members",
    headers=headers,
    json={"user_id": user_id},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "Ошибки: GROUP_DEPARTMENT_MISMATCH (403) — юзер из другого отдела; " +
        "GROUP_NOT_FOUND / USER_NOT_FOUND (404).",
    },
    {
      id: "group-members-remove",
      title: "Убрать юзера из группы",
      method: "DELETE",
      path: "/api/auth/v1/groups/{group_id}/members/{user_id}",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description: "Удаляет membership. Возвращает {\"ok\": true}.",
      curl: `group_id="grp_xxxxxxxx"
user_id="usr_xxxxxxxx"

curl -X DELETE {{BASE_URL}}/api/auth/v1/groups/$group_id/members/$user_id \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"
user_id = "usr_xxxxxxxx"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/members/{user_id}",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "group-bots-list",
      title: "Боты в группе",
      method: "GET",
      path: "/api/auth/v1/groups/{group_id}/bots",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description: "Боты, состоящие в группе: bot_id, name, added_at.",
      curl: `group_id="grp_xxxxxxxx"

curl {{BASE_URL}}/api/auth/v1/groups/$group_id/bots \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/bots",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "group-bots-add",
      title: "Добавить бота в группу",
      method: "POST",
      path: "/api/auth/v1/groups/{group_id}/bots",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Добавляет бота в группу. Бот и группа должны быть в одном отделе. " +
        "Бот наследует service-роли группы (∩ его allowed_services). Возвращает 201.",
      curl: `group_id="grp_xxxxxxxx"
bot_id="bot_xxxxxxxx"

curl -X POST {{BASE_URL}}/api/auth/v1/groups/$group_id/bots \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"bot_id": "'"$bot_id"'"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"
bot_id = "bot_xxxxxxxx"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/bots",
    headers=headers,
    json={"bot_id": bot_id},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "Ошибки: GROUP_DEPARTMENT_MISMATCH (403) — бот из другого отдела; " +
        "GROUP_NOT_FOUND / BOT_NOT_FOUND (404); ALREADY_GROUP_MEMBER (409).",
    },
    {
      id: "group-bots-remove",
      title: "Убрать бота из группы",
      method: "DELETE",
      path: "/api/auth/v1/groups/{group_id}/bots/{bot_id}",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Удаляет bot-membership. Роли группы перестают наследоваться сразу. " +
        "Возвращает {\"ok\": true}.",
      curl: `group_id="grp_xxxxxxxx"
bot_id="bot_xxxxxxxx"

curl -X DELETE {{BASE_URL}}/api/auth/v1/groups/$group_id/bots/$bot_id \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"
bot_id = "bot_xxxxxxxx"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/bots/{bot_id}",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
      notes: "Ошибка: MEMBER_NOT_FOUND (404).",
    },
    {
      id: "group-services-list",
      title: "Сервисы группы (service-access)",
      method: "GET",
      path: "/api/auth/v1/groups/{group_id}/services",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Сервисы, к которым у группы есть access: service_name, is_active, granted_at, granted_by. " +
        "Регулярный member группы не пускается.",
      curl: `group_id="grp_xxxxxxxx"

curl {{BASE_URL}}/api/auth/v1/groups/$group_id/services \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/services",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "Путь — /services (не /service-access). API_ENDPOINTS.md называет раздел " +
        "«service-access», но фактический сегмент пути — services.",
    },
    {
      id: "group-services-grant",
      title: "Дать группе access к сервису",
      method: "POST",
      path: "/api/auth/v1/groups/{group_id}/services",
      auth: "Bearer (admin своего отдела)",
      description:
        "Выдаёт группе access к сервису. Сервис должен быть в allowed_services отдела. " +
        "Через эту связку effective view агрегирует разрешения для членов группы. Возвращает 201.",
      curl: `group_id="grp_xxxxxxxx"

curl -X POST {{BASE_URL}}/api/auth/v1/groups/$group_id/services \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"service_name": "server_service"}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"
service_name = "server_service"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/services",
    headers=headers,
    json={"service_name": service_name},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "Ошибки: SERVICE_NOT_ALLOWED_FOR_DEPARTMENT — сервис не подключён отделу; " +
        "GROUP_SERVICE_ALREADY_GRANTED (409) — повторный grant.",
    },
    {
      id: "group-services-revoke",
      title: "Отозвать access группы к сервису",
      method: "DELETE",
      path: "/api/auth/v1/groups/{group_id}/services/{service_name}",
      auth: "Bearer (admin своего отдела)",
      description:
        "Снимает group access к сервису. group_service_roles по этому сервису тоже " +
        "отбрасываются на INTERSECT. Возвращает {\"ok\": true}.",
      curl: `group_id="grp_xxxxxxxx"
service_name="server_service"

curl -X DELETE {{BASE_URL}}/api/auth/v1/groups/$group_id/services/$service_name \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"
service_name = "server_service"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/services/{service_name}",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "group-roles-list",
      title: "Роли группы по сервисам",
      method: "GET",
      path: "/api/auth/v1/groups/{group_id}/roles",
      auth: "Bearer (account_admin / department_admin своего отдела)",
      description:
        "Service-роли группы, сгруппированные по сервису: service_name + список roles. " +
        "Регулярный member группы не пускается.",
      curl: `group_id="grp_xxxxxxxx"

curl {{BASE_URL}}/api/auth/v1/groups/$group_id/roles \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"

resp = requests.get(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/roles",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
    },
    {
      id: "group-roles-assign",
      title: "Назначить группе роли для сервиса",
      method: "POST",
      path: "/api/auth/v1/groups/{group_id}/roles",
      auth: "Bearer (admin своего отдела)",
      description:
        "Replace-семантика: полностью заменяет набор ролей группы для указанного сервиса. " +
        "Сервис должен уже быть в group_service_access. Возвращает 201.",
      curl: `group_id="grp_xxxxxxxx"

curl -X POST {{BASE_URL}}/api/auth/v1/groups/$group_id/roles \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"service_name": "server_service", "roles": ["admin"]}'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"

resp = requests.post(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/roles",
    headers=headers,
    json={"service_name": "server_service", "roles": ["admin"]},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "Ошибки: GROUP_SERVICE_ACCESS_REQUIRED (403) — нет group_service_access; " +
        "SERVICE_NOT_FOUND (404); INVALID_SERVICE_ROLE (422) — роль не определена в (department, service).",
    },
    {
      id: "group-roles-revoke",
      title: "Снять у группы все роли для сервиса",
      method: "DELETE",
      path: "/api/auth/v1/groups/{group_id}/roles/{service_name}",
      auth: "Bearer (admin своего отдела)",
      description:
        "Удаляет все GroupServiceRole для пары (group, service). Возвращает {\"ok\": true}.",
      curl: `group_id="grp_xxxxxxxx"
service_name="server_service"

curl -X DELETE {{BASE_URL}}/api/auth/v1/groups/$group_id/roles/$service_name \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
headers = {"Authorization": f"Bearer {TOKEN}"}

group_id = "grp_xxxxxxxx"
service_name = "server_service"

resp = requests.delete(
    f"{BASE_URL}/api/auth/v1/groups/{group_id}/roles/{service_name}",
    headers=headers,
)
resp.raise_for_status()
print(resp.json())`,
    },
  ],
};
