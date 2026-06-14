import type { ApiSection } from "../types";

export const TOKENS_BOTS: ApiSection = {
  id: "tokens-bots",
  title: "Токены и боты",
  service: "auth",
  description:
    "Personal Access Tokens (PAT) — личные opaque-токены пользователя; bot-аккаунты — service-account'ы отдела с собственными токенами и service-ролями. И PAT, и bot-токены отдаются raw ровно один раз при создании (в БД лежит только hash) — сохраняй сразу.",
  examples: [
    // ----- tokens (PAT) -----
    {
      id: "pat-create",
      title: "Создать PAT",
      method: "POST",
      path: "/api/auth/v1/tokens",
      auth: "Любой залогиненный юзер (создаёт PAT только себе)",
      description:
        "Генерит opaque-токен с префиксом dbos_pat_… Raw-значение в поле token возвращается ОДИН РАЗ — в БД только hash. allowed_services задаёт scope (минимум один сервис). Срок жизни — либо expires_at (абсолютный момент), либо ttl_seconds (offset от now); ровно один из двух, оба сразу — 422. Бессрочные PAT запрещены, верхняя граница — 6 месяцев (иначе INVALID_EXPIRATION).",
      curl: `curl -X POST {{BASE_URL}}/api/auth/v1/tokens \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "ci-pipeline",
    "allowed_services": ["server_service"],
    "ttl_seconds": 3600
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.post(
    f"{base_url}/api/auth/v1/tokens",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "ci-pipeline",
        # минимум один сервис; токеном можно ходить только в эти сервисы
        "allowed_services": ["server_service"],
        # ttl_seconds ИЛИ expires_at (один из двух), max 6 месяцев
        "ttl_seconds": 3600,
    },
)
resp.raise_for_status()
data = resp.json()

# raw-токен показывается единственный раз — сохрани его сейчас
print(data["token"])      # dbos_pat_…
print(data["token_id"])   # pat_… — нужен для revoke`,
      notes:
        "Ответ 201: { token_id, token, name, expires_at }. Поле token (dbos_pat_…) больше нигде не отдаётся. Для OAuth2 authorization_code JWT дополнительно требуется auth_service в approved scope'ах, иначе 403 OAUTH_SCOPE_INSUFFICIENT.",
    },
    {
      id: "pat-list",
      title: "Список своих PAT",
      method: "GET",
      path: "/api/auth/v1/tokens",
      auth: "Любой залогиненный юзер (видит только свои)",
      description:
        "Возвращает только метаданные токенов: token_prefix, allowed_services, created/expires/last_used/revoked. Raw-значения никогда не отдаются. Отозванные токены остаются в списке с заполненным revoked_at.",
      curl: `curl {{BASE_URL}}/api/auth/v1/tokens \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.get(
    f"{base_url}/api/auth/v1/tokens",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()

for item in resp.json():
    print(item["token_id"], item["token_prefix"], item["allowed_services"])`,
      notes:
        "Элемент: { token_id, name, token_prefix, allowed_services, created_at, expires_at, last_used_at, revoked_at }.",
    },
    {
      id: "pat-revoke",
      title: "Отозвать свой PAT",
      method: "DELETE",
      path: "/api/auth/v1/tokens/{token_id}",
      auth: "Любой залогиненный юзер (только свои токены)",
      description:
        "Помечает PAT как revoked (revoked_at). token_id — из ответа создания (pat_…) или из списка. Юзер может отзывать только свои токены.",
      curl: `curl -X DELETE {{BASE_URL}}/api/auth/v1/tokens/pat_74ac2901372f4bc7a243be3bac863781 \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
token_id = "pat_74ac2901372f4bc7a243be3bac863781"

resp = requests.delete(
    f"{base_url}/api/auth/v1/tokens/{token_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes: "Ответ 200: { ok: true }.",
    },
    // ----- bots -----
    {
      id: "bot-create",
      title: "Создать бота",
      method: "POST",
      path: "/api/auth/v1/bots",
      auth: "account_admin (любой отдел) или department_admin (только свой)",
      description:
        "Создаёт service-account внутри отдела. department_id обязателен — бот привязан к отделу. allowed_services — белый список сервисов, куда бот сможет ходить через свои токены. Имя глобально уникально (по нему ищет docker basic-auth). Тело строгое (extra=forbid): лишние ключи, например service_roles, дают 422 — роли назначаются отдельным эндпоинтом.",
      curl: `curl -X POST {{BASE_URL}}/api/auth/v1/bots \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "ci-runner-bot",
    "department_id": "dep_9eab089d0271115c232a734001c004e0",
    "allowed_services": ["server_service"],
    "description": "CI runner для отдела НТ"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

# department_id берётся из GET /api/auth/v1/departments
department_id = "dep_9eab089d0271115c232a734001c004e0"

resp = requests.post(
    f"{base_url}/api/auth/v1/bots",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "ci-runner-bot",
        "department_id": department_id,
        "allowed_services": ["server_service"],
        "description": "CI runner для отдела НТ",
    },
)
resp.raise_for_status()
bot = resp.json()
print(bot["bot_id"])  # bot_… — нужен для всех вложенных операций`,
      notes:
        "Ответ 201: { bot_id, name, department_id, allowed_services, description, status, is_active, created_at }. Ошибки: BOT_NAME_TAKEN (409), DEPARTMENT_NOT_FOUND (404).",
    },
    {
      id: "bot-list",
      title: "Список ботов",
      method: "GET",
      path: "/api/auth/v1/bots",
      auth: "account_admin (все отделы) или department_admin (только свой)",
      description:
        "Пагинация через limit/offset; общее число — в заголовке X-Total-Count. Фильтр department_id уважается для account_admin (сузить выдачу по отделу); для department_admin игнорируется — он и так залочен на свой отдел.",
      curl: `curl "{{BASE_URL}}/api/auth/v1/bots?limit=20&offset=0&department_id=dep_9eab089d0271115c232a734001c004e0" \\
  -H "Authorization: Bearer {{TOKEN}}" -i`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
department_id = "dep_9eab089d0271115c232a734001c004e0"

resp = requests.get(
    f"{base_url}/api/auth/v1/bots",
    headers={"Authorization": f"Bearer {token}"},
    params={"limit": 20, "offset": 0, "department_id": department_id},
)
resp.raise_for_status()

total = int(resp.headers["X-Total-Count"])
print("всего ботов:", total)
for bot in resp.json():
    print(bot["bot_id"], bot["name"], bot["status"])`,
      notes:
        "Ответ 200: массив BotResponse. Общее число записей — в заголовке X-Total-Count (для постраничного обхода).",
    },
    {
      id: "bot-get",
      title: "Получить бота",
      method: "GET",
      path: "/api/auth/v1/bots/{bot_id}",
      auth: "account_admin (любой) или department_admin (только свой отдел)",
      description: "Читает одного бота по bot_id.",
      curl: `curl {{BASE_URL}}/api/auth/v1/bots/bot_1b528fd5d9cc45d7b98b43c7ce3dd619 \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
bot_id = "bot_1b528fd5d9cc45d7b98b43c7ce3dd619"

resp = requests.get(
    f"{base_url}/api/auth/v1/bots/{bot_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())`,
      notes:
        "Ошибки: BOT_NOT_FOUND (404), BOT_ACCESS_DENIED (403) — department_admin запросил чужого бота.",
    },
    {
      id: "bot-update",
      title: "Обновить бота",
      method: "PATCH",
      path: "/api/auth/v1/bots/{bot_id}",
      auth: "account_admin (любой) или department_admin (только свой отдел)",
      description:
        "Частичный апдейт — отправляй только меняющиеся поля. Активность управляется через status (\"active\"/\"disabled\"), а НЕ через is_active (is_active — read-only в ответе; лишний ключ в теле молча игнорируется, апдейт пройдёт no-op). allowed_services при передаче заменяется полностью.",
      curl: `curl -X PATCH {{BASE_URL}}/api/auth/v1/bots/bot_1b528fd5d9cc45d7b98b43c7ce3dd619 \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "status": "disabled",
    "description": "временно отключён"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
bot_id = "bot_1b528fd5d9cc45d7b98b43c7ce3dd619"

resp = requests.patch(
    f"{base_url}/api/auth/v1/bots/{bot_id}",
    headers={"Authorization": f"Bearer {token}"},
    json={
        # деактивация — через status, не через is_active
        "status": "disabled",
        "description": "временно отключён",
    },
)
resp.raise_for_status()
print(resp.json()["status"], resp.json()["is_active"])  # disabled False`,
      notes:
        "Ответ 200: обновлённый BotResponse. status=\"disabled\" → is_active=false. Обратно — { \"status\": \"active\" }.",
    },
    {
      id: "bot-delete",
      title: "Удалить бота",
      method: "DELETE",
      path: "/api/auth/v1/bots/{bot_id}",
      auth: "Только account_admin",
      description:
        "Физическое удаление (hard-delete) бота вместе со всеми зависимыми записями — токенами, service-ролями, членствами в группах (каскад). В отличие от PATCH status=disabled запись не остаётся. department_admin удалить бота не может — для отключения у него есть мягкий disable через PATCH.",
      curl: `curl -X DELETE {{BASE_URL}}/api/auth/v1/bots/bot_1b528fd5d9cc45d7b98b43c7ce3dd619 \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
bot_id = "bot_1b528fd5d9cc45d7b98b43c7ce3dd619"

resp = requests.delete(
    f"{base_url}/api/auth/v1/bots/{bot_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "Ответ 200: { ok: true }. Ошибки: BOT_NOT_FOUND (404), BOT_DELETE_FORBIDDEN (403) — actor не account_admin.",
    },
    {
      id: "bot-token-create",
      title: "Выдать боту токен",
      method: "POST",
      path: "/api/auth/v1/bots/{bot_id}/tokens",
      auth: "account_admin или department_admin своего отдела",
      description:
        "Генерит opaque-токен бота с префиксом dbos_bot_… Raw-значение в поле token возвращается ОДИН РАЗ — в БД только hash. expires_at опционально (null = бессрочно).",
      curl: `curl -X POST {{BASE_URL}}/api/auth/v1/bots/bot_1b528fd5d9cc45d7b98b43c7ce3dd619/tokens \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "ci-runner-token",
    "expires_at": "2026-12-01T00:00:00Z"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
bot_id = "bot_1b528fd5d9cc45d7b98b43c7ce3dd619"

resp = requests.post(
    f"{base_url}/api/auth/v1/bots/{bot_id}/tokens",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "ci-runner-token",
        "expires_at": "2026-12-01T00:00:00Z",  # null = бессрочно
    },
)
resp.raise_for_status()
data = resp.json()

# raw bot-токен показывается единственный раз
print(data["token"])     # dbos_bot_…
print(data["token_id"])  # btk_… — нужен для revoke`,
      notes:
        "Ответ 201: { token_id, token, name, expires_at }. Поле token (dbos_bot_…) больше нигде не отдаётся.",
    },
    {
      id: "bot-token-list",
      title: "Список токенов бота",
      method: "GET",
      path: "/api/auth/v1/bots/{bot_id}/tokens",
      auth: "account_admin или department_admin своего отдела",
      description:
        "Только метаданные: token_prefix, created/expires/last_used/revoked. Raw-значения никогда не возвращаются.",
      curl: `curl {{BASE_URL}}/api/auth/v1/bots/bot_1b528fd5d9cc45d7b98b43c7ce3dd619/tokens \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
bot_id = "bot_1b528fd5d9cc45d7b98b43c7ce3dd619"

resp = requests.get(
    f"{base_url}/api/auth/v1/bots/{bot_id}/tokens",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()

for tk in resp.json():
    print(tk["token_id"], tk["token_prefix"], tk["revoked_at"])`,
      notes:
        "Элемент: { token_id, name, token_prefix, created_at, expires_at, last_used_at, revoked_at }.",
    },
    {
      id: "bot-token-revoke",
      title: "Отозвать токен бота",
      method: "DELETE",
      path: "/api/auth/v1/bots/{bot_id}/tokens/{token_id}",
      auth: "account_admin или department_admin своего отдела",
      description:
        "Помечает конкретный токен бота как revoked. token_id (btk_…) — из ответа создания или из списка. Отзыв необратим.",
      curl: `curl -X DELETE \\
  {{BASE_URL}}/api/auth/v1/bots/bot_1b528fd5d9cc45d7b98b43c7ce3dd619/tokens/btk_0bc939a0c06444559ee1702573fc1212 \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
bot_id = "bot_1b528fd5d9cc45d7b98b43c7ce3dd619"
token_id = "btk_0bc939a0c06444559ee1702573fc1212"

resp = requests.delete(
    f"{base_url}/api/auth/v1/bots/{bot_id}/tokens/{token_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes: "Ответ 200: { ok: true }.",
    },
    {
      id: "bot-roles-assign",
      title: "Назначить боту service-роли",
      method: "POST",
      path: "/api/auth/v1/bots/{bot_id}/roles",
      auth: "account_admin или department_admin своего отдела",
      description:
        "Replace-семантика по (bot_id, service_name): список roles полностью заменяет текущий набор ролей бота для этого сервиса. Сервис должен быть и в allowed_services бота, и в наборе сервисов отдела. Роли валидируются по каталогу ServiceRoleDefinition отдела.",
      curl: `curl -X POST {{BASE_URL}}/api/auth/v1/bots/bot_1b528fd5d9cc45d7b98b43c7ce3dd619/roles \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "service_name": "server_service",
    "roles": ["worker_bot"]
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
bot_id = "bot_1b528fd5d9cc45d7b98b43c7ce3dd619"

resp = requests.post(
    f"{base_url}/api/auth/v1/bots/{bot_id}/roles",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "service_name": "server_service",
        # полностью заменяет текущие роли бота для этого сервиса
        "roles": ["worker_bot"],
    },
)
resp.raise_for_status()
print(resp.json())  # {"service_name": "server_service", "roles": ["worker_bot"]}`,
      notes:
        "Ответ 201: { service_name, roles }. Ошибки: BOT_NOT_FOUND (404), BOT_INACTIVE (409), BOT_ROLE_MGMT_FORBIDDEN (403), SERVICE_NOT_ALLOWED_FOR_DEPARTMENT (403), SERVICE_NOT_IN_BOT_ALLOWED (403), INVALID_SERVICE_ROLE (422). Текущие роли — GET /api/auth/v1/bots/{bot_id}/roles; снять все для сервиса — DELETE /api/auth/v1/bots/{bot_id}/roles/{service_name}.",
    },
  ],
};
