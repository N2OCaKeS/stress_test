import type { ApiSection } from "../types";

export const ACCOUNTS: ApiSection = {
  id: "server-accounts",
  title: "Server-аккаунты",
  service: "server",
  description:
    "OS-учётки (root/postgres/...), привязанные к одному или нескольким серверам одного отдела. Пароль общий на все привязанные серверы, шифруется at-rest (AES-256-GCM) и в обычной карточке не отдаётся. Ключевой момент — пароль на запись передаётся как password_b64 = base64(plaintext): create и rotate_password принимают password_b64, GET с действием view_password возвращает password_b64, который надо декодировать обратно. Политика по декодированному plaintext: минимум 8 символов, буквы и цифры. Битый base64 → 422. Управление пользователем на самом боксе (useradd/usermod/chpasswd/userdel) — отдельные worker-dispatch эндпоинты.",
  examples: [
    {
      id: "account-create",
      title: "Создать аккаунт (на нескольких серверах)",
      method: "POST",
      path: "/api/server/v1/server-accounts",
      auth: "Bearer + (server_account, *, create); has_sudo=true дополнительно требует grant_sudo",
      description:
        "Заводит OS-аккаунт и привязывает его к списку server_ids (≥1, все в одном отделе). Пароль общий на все серверы — либо передаётся в body как password_b64 = base64(plaintext), либо генерируется сервером (secrets.token_urlsafe(32)), если поле опущено. Переданный пароль декодируется на приёме и проходит политику по plaintext (≥8 символов, буквы+цифры). В ответе password_b64 всегда null — пароль наружу при создании не отдаётся. Idempotency-Key не читается: повтор упрётся в UNIQUE по (server_id, login) → 409.",
      curl: `# пароль кодируется в base64 ПЕРЕД отправкой
password='Secret-Passw0rd'
PW_B64=$(printf '%s' "$password" | base64)

curl -X POST {{BASE_URL}}/api/server/v1/server-accounts \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "server_ids": ["srv_e9863d5a8bc4ae51d1b819dd7965d460"],
    "login": "svc_postgres",
    "password_b64": "'"$PW_B64"'",
    "has_sudo": false,
    "unix_groups": ["docker"],
    "shell": "/bin/bash"
  }'`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

# пароль кодируется в base64 ПЕРЕД отправкой
password = "Secret-Passw0rd"
password_b64 = base64.b64encode(password.encode()).decode()

resp = requests.post(
    f"{base_url}/api/server/v1/server-accounts",
    headers={"Authorization": f"Bearer {token}"},
    json={
        # минимум один сервер; все в одном отделе с вызывающим
        "server_ids": ["srv_e9863d5a8bc4ae51d1b819dd7965d460"],
        "login": "svc_postgres",
        # опустишь password_b64 — сервер сгенерит случайный пароль сам
        "password_b64": password_b64,
        "has_sudo": False,  # True требует action grant_sudo
        "unix_groups": ["docker"],
        "shell": "/bin/bash",
    },
)
resp.raise_for_status()
account = resp.json()

print(account["id"])  # acc_… — нужен для get/patch/rotate/delete
# password_b64 в ответе всегда None: пароль при создании не отдаётся`,
      notes:
        "Ответ 201: карточка ServerAccountResponse с password_b64=null. Битый base64 в password_b64 → 422 (VALIDATION_ERROR, loc=body.password_b64). Слабый пароль (декод < 8 символов или без цифр/букв) → 422 WEAK_PASSWORD. has_sudo=true без grant_sudo → 403. Чужой/несуществующий сервер → 404 SERVER_NOT_FOUND. Дубль (server_id, login) → 409.",
    },
    {
      id: "account-list",
      title: "Список аккаунтов сервера",
      method: "GET",
      path: "/api/server/v1/server-accounts",
      auth: "Bearer + (server_account, *, view)",
      description:
        "Возвращает аккаунты, привязанные к серверу server_id (обязательный query), отсортированные по created_at DESC. Два envelope'а: cursor-пагинация (cursor=true или after=<token>, ответ { items, next_cursor, has_more }) и legacy offset/limit (ответ { items, total, limit, offset }). password_b64 в элементах списка всегда null — пароль через список не раскрывается. Cross-dept сервер скрыт за 404.",
      curl: `# cursor-пагинация (рекомендуется)
curl "{{BASE_URL}}/api/server/v1/server-accounts?server_id=srv_e9863d5a8bc4ae51d1b819dd7965d460&cursor=true&limit=50" \\
  -H "Authorization: Bearer {{TOKEN}}"

# следующая страница — подставить next_cursor из ответа
curl "{{BASE_URL}}/api/server/v1/server-accounts?server_id=srv_e9863d5a8bc4ae51d1b819dd7965d460&after=<next_cursor>" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_e9863d5a8bc4ae51d1b819dd7965d460"

resp = requests.get(
    f"{base_url}/api/server/v1/server-accounts",
    headers={"Authorization": f"Bearer {token}"},
    params={"server_id": server_id, "cursor": "true", "limit": 50},
)
resp.raise_for_status()
page = resp.json()

for acc in page["items"]:
    print(acc["id"], acc["login"], acc["server_ids"])

# докрутить остальные страницы
if page["has_more"]:
    next_cursor = page["next_cursor"]
    # повторить запрос с params={"server_id": ..., "after": next_cursor}`,
      notes:
        "Envelope зависит от режима: cursor → { items, next_cursor, has_more }; offset (по умолчанию) → { items, total, limit, offset }. limit 1..500 (default 100). Битый after → 400 INVALID_CURSOR. Нет view → 403. Сервер не найден / чужой dept → 404 SERVER_NOT_FOUND.",
    },
    {
      id: "account-get",
      title: "Карточка аккаунта (раскрыть пароль)",
      method: "GET",
      path: "/api/server/v1/server-accounts/{account_id}",
      auth: "Bearer + (view ИЛИ view_password); пароль отдаётся только держателю view_password",
      description:
        "Карточка доступна по view или view_password. Держателю action view_password в поле password_b64 приходит base64(plaintext) — его надо декодировать обратно. Без view_password (только view) password_b64 = null. Сырого password_encrypted в ответе нет никогда. Раскрытие пароля пишет CRITICAL audit server_account.password_revealed и проходит per-IP+account rate-limit (PASSWORD_REVEAL_RATE_LIMIT, default 10/min) поверх глобального.",
      curl: `resp=$(curl -s "{{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4" \\
  -H "Authorization: Bearer {{TOKEN}}")

# password_b64 декодируется обратно в plaintext
echo "$resp" | python3 -c "import sys,json,base64; pb=json.load(sys.stdin)['password_b64']; print(base64.b64decode(pb).decode() if pb else 'нет view_password')"`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

resp = requests.get(
    f"{base_url}/api/server/v1/server-accounts/{account_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
account = resp.json()

# держателю view_password приходит base64(plaintext); иначе None
password_b64 = account["password_b64"]
if password_b64:
    password = base64.b64decode(password_b64).decode()
    print("plaintext:", password)
else:
    print("без view_password пароль не раскрывается")`,
      notes:
        "Ответ 200: ServerAccountResponse. password_b64 заполнен только при view_password (тогда — CRITICAL audit + reveal-rate-limit). Нет ни view, ни view_password → 403. Аккаунт не найден / чужой dept → 404 ACCOUNT_NOT_FOUND. Сломанный ciphertext (только при view_password) → 500 DECRYPT_FAILED. Перебор reveal-лимита → 429 RATE_LIMIT_EXCEEDED.",
    },
    {
      id: "account-patch",
      title: "Обновить поля аккаунта",
      method: "PATCH",
      path: "/api/server/v1/server-accounts/{account_id}",
      auth: "Bearer + (server_account, *, update); подъём has_sudo false→true требует grant_sudo",
      description:
        "Частичное обновление has_sudo / unix_groups / linked_user_id / shell / home_dir. Пароль здесь не меняется — для пароля есть rotate_password; привязка серверов — отдельные /servers под-операции. Подъём has_sudo false→true требует action grant_sudo (admin-only); снятие sudo допустимо обычным update. При изменении OS-управляемых полей (has_sudo / unix_groups / shell) правка fan-out'ится задачей account.update_on_host на все серверы, где аккаунт присутствует. No-op PATCH (значение уже стоит) fan-out не запускает.",
      curl: `curl -X PATCH {{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4 \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "shell": "/bin/bash",
    "unix_groups": ["docker", "wheel"]
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

resp = requests.patch(
    f"{base_url}/api/server/v1/server-accounts/{account_id}",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "shell": "/bin/bash",
        # перезаписывает список групп целиком (POSIX-имена)
        "unix_groups": ["docker", "wheel"],
    },
)
resp.raise_for_status()
print(resp.json())  # обновлённая карточка`,
      notes:
        "Ответ 200: обновлённая карточка. is_active через PATCH не меняется (поля в схеме нет). unix_groups валидируются POSIX-паттерном — иначе 422. Нет update (или grant_sudo при подъёме sudo) → 403. Аккаунт не найден / чужой dept → 404.",
    },
    {
      id: "account-bind-servers",
      title: "Привязать аккаунт к серверам",
      method: "POST",
      path: "/api/server/v1/server-accounts/{account_id}/servers",
      auth: "Bearer + (server_account, *, update)",
      description:
        "Добавляет связки аккаунт ↔ сервер. Body — { server_ids: [...] }. Все новые серверы обязаны быть в том же отделе, что и аккаунт (cross-dept → 404). Уже привязанные серверы игнорируются (идемпотентно). Если login занят на одном из новых серверов другим аккаунтом → 409. На боксе пользователь этой операцией не заводится — для этого worker-dispatch /provision.",
      curl: `curl -X POST {{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/servers \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"server_ids": ["srv_e9863d5a8bc4ae51d1b819dd7965d460"]}'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

resp = requests.post(
    f"{base_url}/api/server/v1/server-accounts/{account_id}/servers",
    headers={"Authorization": f"Bearer {token}"},
    json={"server_ids": ["srv_e9863d5a8bc4ae51d1b819dd7965d460"]},
)
resp.raise_for_status()
print(resp.json()["server_ids"])  # обновлённый список привязок`,
      notes:
        "Ответ 200: карточка с обновлённым server_ids. Дубли в server_ids дедуплицируются. Нет update → 403. Аккаунт / сервер не найден / чужой dept → 404. Login занят на одном из серверов → 409.",
    },
    {
      id: "account-unbind-servers",
      title: "Отвязать аккаунт от серверов",
      method: "DELETE",
      path: "/api/server/v1/server-accounts/{account_id}/servers",
      auth: "Bearer + (server_account, *, update)",
      description:
        "Снимает связки аккаунт ↔ сервер. Body — { server_ids: [...] }. Нельзя снять последнюю связку: аккаунт всегда живёт хотя бы на одном сервере, иначе 409 ACCOUNT_NO_SERVERS. На реальном сервере OS-аккаунт этой операцией не удаляется — для этого worker-dispatch /deprovision.",
      curl: `curl -X DELETE {{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/servers \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"server_ids": ["srv_0000000000000000000000000000beef"]}'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

resp = requests.delete(
    f"{base_url}/api/server/v1/server-accounts/{account_id}/servers",
    headers={"Authorization": f"Bearer {token}"},
    json={"server_ids": ["srv_0000000000000000000000000000beef"]},
)
resp.raise_for_status()
print(resp.json()["server_ids"])`,
      notes:
        "Ответ 200: карточка с обновлённым server_ids. Попытка снять последний сервер → 409 ACCOUNT_NO_SERVERS. Нет update → 403. Аккаунт не найден / чужой dept → 404.",
    },
    {
      id: "account-rotate-password",
      title: "Ротация пароля (только в БД, без SSH)",
      method: "POST",
      path: "/api/server/v1/server-accounts/{account_id}/rotate_password",
      auth: "Bearer + (server_account, *, rotate_password)",
      description:
        "Меняет общий пароль аккаунта в хранилище server_service (новый ciphertext), без apply'я на серверы по SSH. Body опционален: передашь password_b64 = base64(plaintext) — он декодируется, проходит политику (≥8 символов, буквы+цифры) и сохраняется; опустишь body / передашь пустой — сервер сгенерит secrets.token_urlsafe(32). Plaintext в ответ НЕ возвращается ни в одном случае (ответ — только id/login/rotated_at). Чтобы пароль реально применился на боксах — worker-dispatch /rotate. Audit — CRITICAL.",
      curl: `# свой пароль — в base64
password='Rotated-Pass99'
PW_B64=$(printf '%s' "$password" | base64)

curl -X POST {{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/rotate_password \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"password_b64": "'"$PW_B64"'"}'

# или пусть сервер сгенерит сам (пустое тело)
curl -X POST {{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/rotate_password \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{}'`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

# вариант 1: задать свой пароль (кодируем в base64)
password = "Rotated-Pass99"
password_b64 = base64.b64encode(password.encode()).decode()
resp = requests.post(
    f"{base_url}/api/server/v1/server-accounts/{account_id}/rotate_password",
    headers={"Authorization": f"Bearer {token}"},
    json={"password_b64": password_b64},
)
resp.raise_for_status()
print(resp.json())  # {id, login, rotated_at} — без plaintext

# вариант 2: пустое тело → сервер сгенерит случайный пароль
resp = requests.post(
    f"{base_url}/api/server/v1/server-accounts/{account_id}/rotate_password",
    headers={"Authorization": f"Bearer {token}"},
    json={},
)
resp.raise_for_status()`,
      notes:
        "Ответ 200: { id, login, rotated_at }. Plaintext наружу не отдаётся — забрать новый пароль можно только через GET с view_password. Битый base64 → 422 (VALIDATION_ERROR); слабый пароль → 422 WEAK_PASSWORD. Нет rotate_password → 403. Аккаунт не найден → 404. Перебор per-IP лимита (ACCOUNT_ROTATE_PASSWORD_RATE_LIMIT, 10/min) → 429.",
    },
    {
      id: "account-delete",
      title: "Удалить аккаунт",
      method: "DELETE",
      path: "/api/server/v1/server-accounts/{account_id}",
      auth: "Bearer + (server_account, *, delete)",
      description:
        "Hard-delete строки аккаунта в БД (связки аккаунт ↔ сервер уходят каскадом). На реальных серверах OS-пользователь этой операцией НЕ удаляется — для этого нужен отдельный worker-dispatch /deprovision до удаления записи.",
      curl: `curl -X DELETE {{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4 \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

resp = requests.delete(
    f"{base_url}/api/server/v1/server-accounts/{account_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "Ответ 200: { ok: true }. Повторный GET после удаления → 404 ACCOUNT_NOT_FOUND. Нет delete → 403. Аккаунт не найден / чужой dept → 404.",
    },
    {
      id: "account-provision-dispatch",
      title: "Завести OS-пользователя на сервере (useradd)",
      method: "POST",
      path: "/api/server/v1/server-accounts/{account_id}/provision",
      auth: "Bearer + (server_account, *, create)",
      description:
        "Worker-dispatch: ставит задачу account.provision — worker заходит на сервер по SSH и делает useradd (логин/пароль/sudo/группы/shell/home берёт из аккаунта), затем колбэчит статус (present_on_server=True). Query server_id обязателен и должен быть среди привязанных. Идемпотентно — если пользователь уже есть, worker не падает. Для discovered-аккаунта без сохранённого пароля — 409 ACCOUNT_HAS_NO_PASSWORD, обойти можно force_password=true.",
      curl: `curl -X POST "{{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/provision?server_id=srv_e9863d5a8bc4ae51d1b819dd7965d460" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

resp = requests.post(
    f"{base_url}/api/server/v1/server-accounts/{account_id}/provision",
    headers={"Authorization": f"Bearer {token}"},
    params={"server_id": "srv_e9863d5a8bc4ae51d1b819dd7965d460"},
)
resp.raise_for_status()
task = resp.json()
print(task["operation"], task["server_id"], task["task_id"])  # provision … tsk_…`,
      notes:
        "Ответ 202: { operation: 'provision', server_id, task_id, status }. Нет create → 403. Аккаунт не найден / server_id не привязан → 404. Списанный сервер / idempotent-конфликт / discovered без пароля → 409 (ACCOUNT_HAS_NO_PASSWORD обходится force_password=true). Worker недоступен → 503.",
    },
    {
      id: "account-update-on-host-dispatch",
      title: "Синхронизировать атрибуты на сервере (usermod)",
      method: "POST",
      path: "/api/server/v1/server-accounts/{account_id}/update_on_host",
      auth: "Bearer + (server_account, *, update)",
      description:
        "Worker-dispatch: ставит задачу account.update_on_host — worker делает usermod, синхронизируя sudo/группы/shell аккаунта на конкретном боксе. Query server_id обязателен и должен быть привязан. Пароль этой операцией не меняется (для пароля — /rotate). Идемпотентно. Обычно вызывается автоматически как fan-out при PATCH OS-управляемых полей, но доступен и явно (например, добить отставший сервер).",
      curl: `curl -X POST "{{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/update_on_host?server_id=srv_e9863d5a8bc4ae51d1b819dd7965d460" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

resp = requests.post(
    f"{base_url}/api/server/v1/server-accounts/{account_id}/update_on_host",
    headers={"Authorization": f"Bearer {token}"},
    params={"server_id": "srv_e9863d5a8bc4ae51d1b819dd7965d460"},
)
resp.raise_for_status()
print(resp.json()["task_id"])  # tsk_…`,
      notes:
        "Ответ 202: { operation: 'update', server_id, task_id, status }. Нет update → 403. Аккаунт не найден / server_id не привязан → 404. Списанный сервер / idempotent-конфликт → 409. Worker недоступен → 503.",
    },
    {
      id: "account-rotate-dispatch",
      title: "Ротация пароля на серверах (SSH chpasswd)",
      method: "POST",
      path: "/api/server/v1/server-accounts/{account_id}/rotate",
      auth: "Bearer + (server_account, *, rotate_password)",
      description:
        "Worker-dispatch end-to-end: worker генерит новый пароль, применяет его по SSH (chpasswd) и колбэчит в server_service, который шифрует и сохраняет. В отличие от /rotate_password (меняет только запись в БД) — этот реально меняет пароль на боксах. Два режима: точечный (query server_id=<srv> — один привязанный сервер) и массовый (без server_id — по задаче на каждый привязанный сервер; списанные и уже-в-очереди серверы попадают в skipped, один битый не валит весь батч). Plaintext клиенту не возвращается.",
      curl: `# массовая ротация — на все привязанные серверы
curl -X POST {{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/rotate \\
  -H "Authorization: Bearer {{TOKEN}}"

# точечная — только указанный сервер
curl -X POST "{{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/rotate?server_id=srv_e9863d5a8bc4ae51d1b819dd7965d460" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

# массовая ротация: server_id опущен → задача на каждый привязанный сервер
resp = requests.post(
    f"{base_url}/api/server/v1/server-accounts/{account_id}/rotate",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
result = resp.json()

for t in result["tasks"]:
    print("queued:", t["server_id"], t["task_id"])
for s in result["skipped"]:
    print("skipped:", s["server_id"], s["reason"])  # decommissioned | idempotent_conflict | worker_unreachable

if result["partial_failure"]:
    # часть серверов уже получила задачу, остаток нет — см. next_action
    print("partial:", result["next_action"])`,
      notes:
        "Ответ 202: { mode, status, tasks[], skipped[], partial_failure, next_action }. Нет rotate_password → 403. Аккаунт не найден / server_id не привязан → 404. Все серверы списаны / точечный idempotent / NO_LINKED_SERVERS → 409. Батч > MASS_ROTATION_MAX_SERVERS → 413 MASS_ROTATION_TOO_LARGE. Перебор лимита (MASS_ROTATE_DISPATCH_RATE_LIMIT, 5/min) → 429. Redis/worker down → 503.",
    },
    {
      id: "account-deprovision-dispatch",
      title: "Удалить OS-пользователя с сервера (userdel)",
      method: "POST",
      path: "/api/server/v1/server-accounts/{account_id}/deprovision",
      auth: "Bearer + (server_account, *, delete)",
      description:
        "Worker-dispatch: ставит задачу account.deprovision — worker делает userdel (опционально userdel --remove при remove_home=true), затем колбэчит статус (present_on_server=False). Query server_id обязателен и должен быть привязан; remove_home (query, default false) — удалять ли home-директорию. Идемпотентно — если пользователя уже нет, worker не падает. Связку аккаунт ↔ сервер эта операция НЕ снимает (для отвязки — DELETE /servers).",
      curl: `curl -X POST "{{BASE_URL}}/api/server/v1/server-accounts/acc_a6a30466eb0e42f4926782acd5c3b5e4/deprovision?server_id=srv_e9863d5a8bc4ae51d1b819dd7965d460&remove_home=true" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
account_id = "acc_a6a30466eb0e42f4926782acd5c3b5e4"

resp = requests.post(
    f"{base_url}/api/server/v1/server-accounts/{account_id}/deprovision",
    headers={"Authorization": f"Bearer {token}"},
    params={
        "server_id": "srv_e9863d5a8bc4ae51d1b819dd7965d460",
        "remove_home": "true",  # userdel --remove
    },
)
resp.raise_for_status()
print(resp.json()["task_id"])  # tsk_…`,
      notes:
        "Ответ 202: { operation: 'deprovision', server_id, task_id, status }. Нет delete → 403. Аккаунт не найден / server_id не привязан → 404. Списанный сервер / idempotent-конфликт → 409. Worker недоступен → 503. Связка аккаунт↔сервер остаётся — это только удаление с бокса.",
    },
  ],
};
