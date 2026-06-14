import type { ApiSection } from "../types";

export const CREDENTIALS: ApiSection = {
  id: "credentials",
  title: "Credentials (CRUD)",
  service: "secret",
  description:
    "Хранилище учётных данных к внешним системам (Jira, Confluence, Git и т.п.). Каждая cred несёт метаданные (name/service/scope/login/owner) и зашифрованный at-rest секрет (AES-256-GCM + HKDF-SHA256). Ключевой момент про секрет: он передаётся ТОЛЬКО как secret_b64 = base64(plaintext). create требует secret_b64, patch принимает secret_b64 для смены секрета. Plaintext по декодированному — 1..8192 символов UTF-8; битый base64 / не-UTF-8 → 422. Карточка (GET/list) plaintext никогда не отдаёт — расшифровка живёт в отдельном endpoint'е POST /credentials/{id}/reveal (группа Reveal). Scope определяет владельца и модель доступа: personal (владелец — user), department (владелец — отдел), cross_department (отдел-владелец + явные DeptGrant/RoleACL для чужих отделов). Перед любой операцией проверяется, что отдел actor'а подключён к secret_service — иначе 403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT.",
  examples: [
    {
      id: "credential-create-personal",
      title: "Создать personal credential",
      method: "POST",
      path: "/api/secret/v1/credentials",
      auth: "Bearer user (actor_type=user); владелец — сам user",
      description:
        "Заводит личную cred'у. scope=personal → владелец берётся из identity, owner_dept_id передавать нельзя (передашь — 422). Секрет кодируется в base64 ПЕРЕД отправкой: secret_b64 = base64.b64encode(plaintext). На приёме декодируется, проверяется длина plaintext (1..8192 символов UTF-8) и шифруется. В ответе 201 — CredentialRead без plaintext. name уникален в паре (owner, service, name) среди active кред.",
      curl: `# секрет кодируется в base64 ПЕРЕД отправкой
secret='super-secret-token-123'
SECRET_B64=$(printf '%s' "$secret" | base64)

curl -X POST {{BASE_URL}}/api/secret/v1/credentials \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "jira_personal",
    "service": "jira",
    "scope": "personal",
    "login": "alice",
    "secret_b64": "'"$SECRET_B64"'"
  }'`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

# секрет кодируется в base64 ПЕРЕД отправкой
secret = "super-secret-token-123"
secret_b64 = base64.b64encode(secret.encode()).decode()

resp = requests.post(
    f"{base_url}/api/secret/v1/credentials",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "jira_personal",
        "service": "jira",
        "scope": "personal",
        # login опционален: None для токен-only кред
        "login": "alice",
        "secret_b64": secret_b64,
    },
)
resp.raise_for_status()
cred = resp.json()

print(cred["id"])  # cred_… — нужен для get/patch/delete/reveal
# plaintext в ответе НЕ возвращается — забрать секрет можно только через reveal`,
      notes:
        "Ответ 201: CredentialRead (без plaintext, owner_user_id = текущий user, owner_dept_id = null). Битый base64 в secret_b64 → 422 (VALIDATION_ERROR, msg 'secret_b64 is not valid base64'). Пустой/слишком длинный plaintext → 422 (ENCRYPT_INPUT_INVALID / PLAINTEXT_TOO_LARGE). owner_dept_id при scope=personal → 422 VALIDATION_ERROR. Дубль (owner, service, name) среди active → 409 NAME_DUPLICATE. Отдел не подключён к secret_service → 403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT. Round-trip секрета (create → reveal → base64-decode обратно в plaintext) — см. группу Reveal.",
    },
    {
      id: "credential-create-department",
      title: "Создать department credential",
      method: "POST",
      path: "/api/secret/v1/credentials",
      auth: "Bearer user из отдела-владельца (owner / dep_admin / admin secret_service отдела)",
      description:
        "Заводит cred'у, владельцем которой выступает отдел. scope=department требует owner_dept_id (отдел actor'а, подключённый к secret_service). visible_to_dept=true делает существование cred'ы видимым гостям отдела через список (но без секрета). Доступ к содержимому регулируется RoleACL внутри отдела (см. группу RoleACL). Секрет — как и в personal: secret_b64 = base64(plaintext).",
      curl: `secret='dept-shared-secret'
SECRET_B64=$(printf '%s' "$secret" | base64)

curl -X POST {{BASE_URL}}/api/secret/v1/credentials \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "confluence_team",
    "service": "confluence",
    "scope": "department",
    "login": "svc-bot",
    "secret_b64": "'"$SECRET_B64"'",
    "owner_dept_id": "dep_9eab089d0271115c232a734001c004e0",
    "visible_to_dept": true
  }'`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

secret = "dept-shared-secret"
secret_b64 = base64.b64encode(secret.encode()).decode()

resp = requests.post(
    f"{base_url}/api/secret/v1/credentials",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "confluence_team",
        "service": "confluence",
        "scope": "department",
        "login": "svc-bot",
        "secret_b64": secret_b64,
        # обязателен для department/cross_department; должен быть отделом actor'а
        "owner_dept_id": "dep_9eab089d0271115c232a734001c004e0",
        # видна гостям отдела в списке (без секрета)
        "visible_to_dept": True,
    },
)
resp.raise_for_status()
print(resp.json()["id"])  # owner_user_id = null, owner_dept_id заполнен`,
      notes:
        "Ответ 201: CredentialRead с owner_user_id=null, owner_dept_id=<отдел>. owner_dept_id обязателен для scope=department (опустишь → 422). Создать cred'у в чужом отделе нельзя — отдел actor'а должен совпадать и быть подключён к secret_service (иначе 403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT / CREDENTIAL_ACCESS_DENIED). Дубль имени → 409 NAME_DUPLICATE.",
    },
    {
      id: "credential-create-cross-department",
      title: "Создать cross_department credential",
      method: "POST",
      path: "/api/secret/v1/credentials",
      auth: "Bearer user из отдела-владельца (owner / dep_admin / admin secret_service отдела)",
      description:
        "Заводит cred'у, которую отдел-владелец сможет шарить наружу другим отделам. scope=cross_department требует owner_dept_id. Сам факт cross_department лишь разрешает позже выдать DeptGrant отделу-получателю, а уже внутри получателя — RoleACL (см. группы DeptGrant / RoleACL). До выдачи грантов cred видна и доступна только своему отделу-владельцу. Секрет — secret_b64 = base64(plaintext).",
      curl: `secret='cross-dept-ci-secret'
SECRET_B64=$(printf '%s' "$secret" | base64)

curl -X POST {{BASE_URL}}/api/secret/v1/credentials \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "git_ci",
    "service": "git",
    "scope": "cross_department",
    "login": "ci-bot",
    "secret_b64": "'"$SECRET_B64"'",
    "owner_dept_id": "dep_9eab089d0271115c232a734001c004e0"
  }'`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

secret = "cross-dept-ci-secret"
secret_b64 = base64.b64encode(secret.encode()).decode()

resp = requests.post(
    f"{base_url}/api/secret/v1/credentials",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "git_ci",
        "service": "git",
        "scope": "cross_department",
        "login": "ci-bot",
        "secret_b64": secret_b64,
        "owner_dept_id": "dep_9eab089d0271115c232a734001c004e0",
    },
)
resp.raise_for_status()
cred = resp.json()
print(cred["id"], cred["scope"])  # cred_… cross_department
# дальше: POST /credentials/{id}/dept-grants (отделу-получателю),
# затем dep_admin получателя выдаёт RoleACL внутри своего отдела`,
      notes:
        "Ответ 201: CredentialRead, scope=cross_department, owner_dept_id заполнен. owner_dept_id обязателен. Шаринг наружу — отдельные шаги: сначала DeptGrant(cred, recipient_dept), затем RoleACL внутри получателя. Без них чужой отдел получает 404 (existence скрыт). Дубль имени → 409 NAME_DUPLICATE.",
    },
    {
      id: "credential-create-validity-window",
      title: "Создать credential с окном валидности",
      method: "POST",
      path: "/api/secret/v1/credentials",
      auth: "Bearer user (actor_type=user)",
      description:
        "valid_from / valid_to (UTC, ISO-8601) ограничивают окно, в котором reveal вернёт секрет. Вне окна reveal → 410 SECRET_NOT_YET_VALID (до valid_from) или 410 SECRET_EXPIRED (после valid_to), но metadata-GET остаётся 200 — UI показывает «продлите токен». valid_to обязан быть строго в будущем (уже-expired кред создавать нельзя) и больше valid_from. Оба поля опциональны — null = open-ended с этой стороны.",
      curl: `secret='token-with-ttl'
SECRET_B64=$(printf '%s' "$secret" | base64)

curl -X POST {{BASE_URL}}/api/secret/v1/credentials \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "jira_temp_token",
    "service": "jira",
    "scope": "personal",
    "secret_b64": "'"$SECRET_B64"'",
    "valid_from": "2026-06-14T00:00:00Z",
    "valid_to": "2026-12-31T23:59:59Z"
  }'`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

secret = "token-with-ttl"
secret_b64 = base64.b64encode(secret.encode()).decode()

resp = requests.post(
    f"{base_url}/api/secret/v1/credentials",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "jira_temp_token",
        "service": "jira",
        "scope": "personal",
        "secret_b64": secret_b64,
        # UTC; naive трактуется как UTC
        "valid_from": "2026-06-14T00:00:00Z",
        "valid_to": "2026-12-31T23:59:59Z",
    },
)
resp.raise_for_status()
cred = resp.json()
print(cred["valid_from"], cred["valid_to"])`,
      notes:
        "Ответ 201: CredentialRead с заполненными valid_from/valid_to. valid_to в прошлом → 422 (refuse to create already-expired). valid_to <= valid_from → 422. Окно проверяется только на reveal — list/get не блокируются. Снять окно нельзя через PATCH (NULL не маппится) — пересоздать креду.",
    },
    {
      id: "credential-list",
      title: "Список credentials (фильтры + пагинация)",
      method: "GET",
      path: "/api/secret/v1/credentials",
      auth: "Bearer user/bot/PAT",
      description:
        "Возвращает видимые actor'у cred'ы (по scope/ACL), отсортированные created_at DESC, id DESC. Курсорная пагинация: limit (1..200, default 50) + opaque cursor из next_cursor предыдущей страницы. Фильтры query: scope (personal/department/cross_department), service, status (active/blocked). Тело — { items: CredentialRead[], next_cursor: str | null }. Для роли guest отдаётся урезанный shape CredentialGuestList (только id/name/service/scope/visible_to_dept). Plaintext в списке не отдаётся никогда.",
      curl: `# первая страница: только jira-кред, active
curl "{{BASE_URL}}/api/secret/v1/credentials?service=jira&status=active&limit=50" \\
  -H "Authorization: Bearer {{TOKEN}}"

# следующая страница — подставить next_cursor из ответа
curl "{{BASE_URL}}/api/secret/v1/credentials?service=jira&cursor=<next_cursor>" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

params = {"service": "jira", "status": "active", "limit": 50}
resp = requests.get(
    f"{base_url}/api/secret/v1/credentials",
    headers={"Authorization": f"Bearer {token}"},
    params=params,
)
resp.raise_for_status()
page = resp.json()

for c in page["items"]:
    print(c["id"], c["name"], c["scope"], c["status"])

# докрутить остальные страницы
while page["next_cursor"]:
    resp = requests.get(
        f"{base_url}/api/secret/v1/credentials",
        headers={"Authorization": f"Bearer {token}"},
        params={**params, "cursor": page["next_cursor"]},
    )
    resp.raise_for_status()
    page = resp.json()
    for c in page["items"]:
        print(c["id"], c["name"])`,
      notes:
        "Ответ 200: { items: CredentialRead[], next_cursor }. next_cursor = null на последней странице. Невалидный cursor → 400 INVALID_CURSOR. Фильтр scope=cross_department покажет только cross-dep кред'ы, видимые actor'у. Guest получает CredentialGuestList (без owner/login/timestamps). Отдел не подключён → 403 SERVICE_NOT_AVAILABLE_FOR_DEPARTMENT.",
    },
    {
      id: "credential-get",
      title: "Карточка credential (без secret)",
      method: "GET",
      path: "/api/secret/v1/credentials/{cred_id}",
      auth: "Bearer (owner / RoleACL / dep_admin / admin secret_service отдела)",
      description:
        "Метаданные одной cred'ы: id, name, service, scope, owner_user_id/owner_dept_id, login, status, created_by, created_at/updated_at, blocked_at/blocked_reason, visible_to_dept, valid_from/valid_to. Plaintext-секрета здесь нет — для расшифровки POST /credentials/{id}/reveal. Карточка blocked-кред'ы возвращает 410 CREDENTIAL_BLOCKED. Несуществующая или невидимая (cross-dep miss) → 404 (existence скрыт намеренно).",
      curl: `curl "{{BASE_URL}}/api/secret/v1/credentials/cred_5a0ca2b85102ba73f695cc3624664f04" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_5a0ca2b85102ba73f695cc3624664f04"

resp = requests.get(
    f"{base_url}/api/secret/v1/credentials/{cred_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
cred = resp.json()
print(cred["name"], cred["scope"], cred["status"])
# секрета тут нет — забрать plaintext можно только через reveal`,
      notes:
        "Ответ 200: CredentialRead. Нет прав → 403 CREDENTIAL_ACCESS_DENIED. Не найдена / cross-dep visibility miss → 404 CREDENTIAL_NOT_FOUND. status=blocked → 410 CREDENTIAL_BLOCKED (с blocked_reason/blocked_at в details). Истёкшая по valid_to cred'а здесь всё равно отдаёт 200 — окно блокирует только reveal.",
    },
    {
      id: "credential-patch",
      title: "Обновить name / login / secret",
      method: "PATCH",
      path: "/api/secret/v1/credentials/{cred_id}",
      auth: "Bearer (owner / dep_admin / admin secret_service отдела — per scope)",
      description:
        "Частичное обновление: name, login, secret_b64, valid_from, valid_to. Все поля optional. Смена секрета — передать secret_b64 = base64(plaintext); опустишь / null → секрет не меняется (старый ciphertext остаётся). Размеры/правила те же, что в create. valid_from/valid_to позволяют admin'у продлить срок; NULL через PATCH намеренно НЕ сбрасывает окно (чтобы случайный {\"valid_to\": null} не снял защиту) — чтобы убрать окно, пересоздать креду. admin не может менять content (login/secret) personal-кред живого владельца.",
      curl: `# смена секрета: новый plaintext → base64
secret='rotated-secret-456'
SECRET_B64=$(printf '%s' "$secret" | base64)

curl -X PATCH {{BASE_URL}}/api/secret/v1/credentials/cred_5a0ca2b85102ba73f695cc3624664f04 \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "login": "alice2",
    "secret_b64": "'"$SECRET_B64"'"
  }'`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_5a0ca2b85102ba73f695cc3624664f04"

# сменить только login (секрет не трогаем — secret_b64 не передаём)
resp = requests.patch(
    f"{base_url}/api/secret/v1/credentials/{cred_id}",
    headers={"Authorization": f"Bearer {token}"},
    json={"login": "alice2"},
)
resp.raise_for_status()

# сменить секрет: новый plaintext кодируем в base64
new_secret = "rotated-secret-456"
resp = requests.patch(
    f"{base_url}/api/secret/v1/credentials/{cred_id}",
    headers={"Authorization": f"Bearer {token}"},
    json={"secret_b64": base64.b64encode(new_secret.encode()).decode()},
)
resp.raise_for_status()
print(resp.json()["updated_at"])  # обновился, secret перешифрован`,
      notes:
        "Ответ 200: обновлённая CredentialRead. secret_b64 опущен/null → секрет не меняется. Битый base64 → 422 VALIDATION_ERROR (loc=body, 'secret_b64 is not valid base64'). valid_to <= valid_from → 422. Конфликт нового name → 409 NAME_DUPLICATE. Нет прав → 403 CREDENTIAL_ACCESS_DENIED. Не найдена → 404. status=blocked → 410 CREDENTIAL_BLOCKED.",
    },
    {
      id: "credential-patch-bad-base64",
      title: "Битый base64 в secret_b64 → 422",
      method: "PATCH",
      path: "/api/secret/v1/credentials/{cred_id}",
      auth: "Bearer (owner / admin)",
      description:
        "Демонстрация валидации: secret_b64 декодируется на приёме, и невалидный base64 (как и не-UTF-8 plaintext, пустой или > 8192 символов) отбивается с 422 ещё до записи в БД. Тот же контракт у POST /credentials. Полезно как негативный кейс при интеграции UI: показать пользователю, что секрет надо именно base64-кодировать, а не слать как есть.",
      curl: `# намеренно невалидный base64 → 422
curl -X PATCH {{BASE_URL}}/api/secret/v1/credentials/cred_5a0ca2b85102ba73f695cc3624664f04 \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"secret_b64": "!!!not-base64!!!"}'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_5a0ca2b85102ba73f695cc3624664f04"

resp = requests.patch(
    f"{base_url}/api/secret/v1/credentials/{cred_id}",
    headers={"Authorization": f"Bearer {token}"},
    json={"secret_b64": "!!!not-base64!!!"},
)
assert resp.status_code == 422, resp.status_code
err = resp.json()
print(err["error_code"])  # VALIDATION_ERROR
print(err["details"]["errors"][0]["msg"])  # ...secret_b64 is not valid base64`,
      notes:
        "Ответ 422: { error_code: 'VALIDATION_ERROR', details.errors[].msg = 'Value error, secret_b64 is not valid base64' }. Тот же эффект на POST /credentials. Также 422 при пустом plaintext (ENCRYPT_INPUT_INVALID), plaintext > 8192 символов (PLAINTEXT_TOO_LARGE) и не-UTF-8 декоде.",
    },
    {
      id: "credential-delete",
      title: "Удалить credential (admin override требует reason)",
      method: "DELETE",
      path: "/api/secret/v1/credentials/{cred_id}",
      auth: "Bearer (owner / dep_admin / admin secret_service отдела / account_admin)",
      description:
        "Удаляет cred'у. Owner удаляет свою без тела. Admin override (не-owner: admin secret_service своего отдела или account_admin) обязан передать reason (1..256 символов) в body, иначе 422 ADMIN_OVERRIDE_REASON_REQUIRED — это пишется в CRITICAL audit tokens.admin_override_delete. Удаление hard: повторный GET → 404.",
      curl: `# owner — без тела
curl -X DELETE {{BASE_URL}}/api/secret/v1/credentials/cred_5a0ca2b85102ba73f695cc3624664f04 \\
  -H "Authorization: Bearer {{TOKEN}}"

# admin override — обязателен reason
curl -X DELETE {{BASE_URL}}/api/secret/v1/credentials/cred_5a0ca2b85102ba73f695cc3624664f04 \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"reason": "owner left the team"}'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_5a0ca2b85102ba73f695cc3624664f04"

# вариант 1: owner удаляет свою — без тела
resp = requests.delete(
    f"{base_url}/api/secret/v1/credentials/{cred_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}

# вариант 2: admin override — reason обязателен
resp = requests.delete(
    f"{base_url}/api/secret/v1/credentials/{cred_id}",
    headers={"Authorization": f"Bearer {token}"},
    json={"reason": "owner left the team"},
)
# без reason → 422 ADMIN_OVERRIDE_REASON_REQUIRED`,
      notes:
        "Ответ 200: { ok: true }. Повторный GET после удаления → 404 CREDENTIAL_NOT_FOUND. Admin override без reason → 422 ADMIN_OVERRIDE_REASON_REQUIRED. Нет прав → 403 CREDENTIAL_ACCESS_DENIED. Не найдена → 404. Для blocked-кред'ы вместо удаления обычно используют recover/transfer (группа Reveal/Transfer/Recover).",
    },
  ],
};
