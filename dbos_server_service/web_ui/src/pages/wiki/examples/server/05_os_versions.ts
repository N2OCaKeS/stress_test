import type { ApiSection } from "../types";

export const OS_VERSIONS: ApiSection = {
  id: "os-versions",
  title: "Версии ОС",
  service: "server",
  description:
    "Глобальный каталог версий ОС (astra-1.7, ubuntu-22.04, ...) с привязкой " +
    "репозиториев. Каталог не привязан к отделу. Чтение публичное — без токена " +
    "(анонимный read под отдельным per-IP rate-limit'ом OS_VERSIONS_ANON_RATE_LIMIT, " +
    "default 100/min); запись (create/update/delete) — под матрицей прав, action " +
    "(os_version, *, create|update|delete). Карточка версии: id (префикс osv_), name " +
    "(UNIQUE, 1..128), description, repositories (список http(s)-URL, ≤64), " +
    "discovered_at, updated_at.",
  examples: [
    {
      id: "os-versions-list",
      title: "Список версий (offset-пагинация, legacy)",
      method: "GET",
      path: "/os-versions",
      auth: "Public (без токена)",
      description:
        "Глобальный каталог. Legacy offset/limit envelope: {items, total, limit, " +
        "offset}. limit 1..500 (default 100), offset >=0. Чтение публичное — Bearer " +
        "не нужен; авторизованный запрос тоже проходит. Для новых интеграций лучше " +
        "cursor-пагинация (см. соседний пример).",
      notes:
        "Анонимный read — под отдельным per-IP " +
        "лимитом 100/min (OS_VERSIONS_ANON_RATE_LIMIT).",
      curl: `# read публичный — токен не требуется
curl "{{BASE_URL}}/api/server/v1/os-versions?limit=50&offset=0"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"

# read публичный — заголовок Authorization не нужен
resp = requests.get(
    f"{BASE_URL}/api/server/v1/os-versions",
    params={"limit": 50, "offset": 0},
)
resp.raise_for_status()

page = resp.json()
print("total:", page["total"], "limit:", page["limit"], "offset:", page["offset"])
for v in page["items"]:
    print(v["id"], v["name"], v["repositories"])`,
    },
    {
      id: "os-versions-list-cursor",
      title: "Список версий (cursor-пагинация, рекомендуемая)",
      method: "GET",
      path: "/os-versions",
      auth: "Public (без токена)",
      description:
        "Keyset-пагинация: cursor=true (или сразу after=<token>) переключает на " +
        "envelope {items, next_cursor, has_more}. next_cursor — opaque base64url, " +
        "подставляется в ?after=<token> для следующей страницы; null/has_more=false — " +
        "конец каталога. after опускается на первой странице. limit тот же 1..500.",
      notes:
        "Битый/недекодируемый after → 400 INVALID_CURSOR. Анонимный режим — тот же " +
        "per-IP лимит, что и у offset-варианта.",
      curl: `# первая страница cursor-режима
curl "{{BASE_URL}}/api/server/v1/os-versions?cursor=true&limit=2"

# следующая страница — подставить next_cursor предыдущего ответа в after
curl "{{BASE_URL}}/api/server/v1/os-versions?after=<next_cursor>&limit=2"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"

# пройти весь каталог постранично через cursor
after = None
collected = []
while True:
    params = {"limit": 2, "cursor": "true"}
    if after is not None:
        params["after"] = after
    resp = requests.get(f"{BASE_URL}/api/server/v1/os-versions", params=params)
    resp.raise_for_status()
    page = resp.json()
    collected.extend(page["items"])
    if not page["has_more"]:
        break
    after = page["next_cursor"]

print("всего собрано:", len(collected))`,
    },
    {
      id: "os-versions-get",
      title: "Карточка версии по id",
      method: "GET",
      path: "/os-versions/{os_version_id}",
      auth: "Public (без токена)",
      description:
        "Полная карточка версии по id (префикс osv_). Публичный read. Несуществующий " +
        "id → 404 OS_VERSION_NOT_FOUND.",
      curl: `OSV_ID="osv_d54bacd2ae5a441ba155a6feaf936615"

curl "{{BASE_URL}}/api/server/v1/os-versions/$OSV_ID"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
osv_id = "osv_d54bacd2ae5a441ba155a6feaf936615"

resp = requests.get(f"{BASE_URL}/api/server/v1/os-versions/{osv_id}")
resp.raise_for_status()

v = resp.json()
print(v["name"], v["description"])
print("репозитории:", v["repositories"])`,
    },
    {
      id: "os-versions-get-by-name",
      title: "Карточка версии по имени",
      method: "GET",
      path: "/os-versions/by-name/{name}",
      auth: "Public (без токена)",
      description:
        "Поиск версии по UNIQUE-имени (astra-1.7, ubuntu-22.04, ...). Удобно, когда " +
        "под рукой каноничное имя, а не osv_-id. Публичный read. Имя не найдено → " +
        "404 OS_VERSION_NOT_FOUND.",
      curl: `OS_NAME="wiki_os_alpine-edge"

curl "{{BASE_URL}}/api/server/v1/os-versions/by-name/$OS_NAME"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
os_name = "wiki_os_alpine-edge"

resp = requests.get(f"{BASE_URL}/api/server/v1/os-versions/by-name/{os_name}")
resp.raise_for_status()

v = resp.json()
print(v["id"], v["name"])`,
    },
    {
      id: "os-versions-create",
      title: "Зарегистрировать новую версию",
      method: "POST",
      path: "/os-versions",
      auth: "Bearer + (os_version, *, create)",
      description:
        "Добавляет запись в каталог. Тело: name (обязателен, UNIQUE, 1..128), " +
        "description (опц.), repositories (опц., список http(s)-URL, ≤64, каждый ≤2048 " +
        "символов). Ответ 201 — полная карточка с присвоенным osv_-id и " +
        "discovered_at/updated_at.",
      notes:
        "Повтор name → 409 OS_VERSION_DUPLICATE. Невалидный repository-URL (не http(s) " +
        "или без хоста) → 422 VALIDATION_ERROR. Idempotency-Key этот endpoint не " +
        "читает — защита от дублей через UNIQUE(name). Нет роли с create → 403 " +
        "PERMISSION_DENIED.",
      curl: `TOKEN="{{TOKEN}}"

curl -X POST "{{BASE_URL}}/api/server/v1/os-versions" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "wiki_os_alpine-edge",
    "description": "Wiki sample",
    "repositories": ["https://dl-cdn.alpinelinux.org/alpine/edge/main"]
  }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"

headers = {"Authorization": f"Bearer {TOKEN}"}
os_name = "wiki_os_alpine-edge"
body = {
    "name": os_name,
    "description": "Wiki sample",
    "repositories": ["https://dl-cdn.alpinelinux.org/alpine/edge/main"],
}

resp = requests.post(
    f"{BASE_URL}/api/server/v1/os-versions", headers=headers, json=body,
)
resp.raise_for_status()  # 409 OS_VERSION_DUPLICATE если name занят

created = resp.json()
print("создано:", created["id"], created["name"])`,
    },
    {
      id: "os-versions-update",
      title: "Обновить версию (частично)",
      method: "PATCH",
      path: "/os-versions/{os_version_id}",
      auth: "Bearer + (os_version, *, update)",
      description:
        "Частичное обновление: любое из name / description / repositories. " +
        "repositories заменяется списком целиком (не мёрджится). Все поля опциональны.",
      notes:
        "Смена name на занятое → 409 OS_VERSION_DUPLICATE. Несуществующий id → 404 " +
        "OS_VERSION_NOT_FOUND. Невалидный repository-URL → 422. Нет роли update → 403.",
      curl: `TOKEN="{{TOKEN}}"
OSV_ID="osv_d54bacd2ae5a441ba155a6feaf936615"

curl -X PATCH "{{BASE_URL}}/api/server/v1/os-versions/$OSV_ID" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{
    "description": "updated desc",
    "repositories": ["https://dl-cdn.alpinelinux.org/alpine/v3.20/main"]
  }'`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
osv_id = "osv_d54bacd2ae5a441ba155a6feaf936615"

headers = {"Authorization": f"Bearer {TOKEN}"}
patch = {
    "description": "updated desc",
    "repositories": ["https://dl-cdn.alpinelinux.org/alpine/v3.20/main"],
}

resp = requests.patch(
    f"{BASE_URL}/api/server/v1/os-versions/{osv_id}", headers=headers, json=patch,
)
resp.raise_for_status()

v = resp.json()
print("updated_at:", v["updated_at"], "repos:", v["repositories"])`,
    },
    {
      id: "os-versions-delete",
      title: "Удалить версию",
      method: "DELETE",
      path: "/os-versions/{os_version_id}",
      auth: "Bearer + (os_version, *, delete)",
      description:
        "Hard-delete записи каталога. Ответ {ok: true}. FK servers.os_version_id " +
        "ondelete=RESTRICT: если хоть один сервер ссылается на версию — удалить " +
        "нельзя.",
      notes:
        "Версия используется хотя бы одним сервером → 409 OS_VERSION_IN_USE. " +
        "Несуществующий id → 404 OS_VERSION_NOT_FOUND. Нет роли delete → 403.",
      curl: `TOKEN="{{TOKEN}}"
OSV_ID="osv_d54bacd2ae5a441ba155a6feaf936615"

curl -X DELETE "{{BASE_URL}}/api/server/v1/os-versions/$OSV_ID" \\
  -H "Authorization: Bearer $TOKEN"`,
      python: `import requests

BASE_URL = "{{BASE_URL}}"
TOKEN = "{{TOKEN}}"
osv_id = "osv_d54bacd2ae5a441ba155a6feaf936615"

headers = {"Authorization": f"Bearer {TOKEN}"}
resp = requests.delete(
    f"{BASE_URL}/api/server/v1/os-versions/{osv_id}", headers=headers,
)
resp.raise_for_status()  # 409 OS_VERSION_IN_USE если на версию ссылается сервер
print(resp.json())  # {"ok": true}`,
    },
  ],
};
