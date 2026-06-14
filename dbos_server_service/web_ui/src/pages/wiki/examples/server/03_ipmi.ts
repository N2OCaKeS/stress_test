import type { ApiSection } from "../types";

export const IPMI: ApiSection = {
  id: "ipmi",
  title: "IPMI-контроллеры",
  service: "server",
  description:
    "BMC-контроллеры серверов (iDRAC / iLO / IPMI / Redfish), связь с сервером 1:1 (UNIQUE на server_id). Регистрация и просмотр карточки идут по server_id (POST/GET/PATCH/DELETE /servers/{id}/ipmi); список и ротация — по controller_id (/ipmi-controllers). Пароль BMC на входе принимается ТОЛЬКО как password_b64 = base64(plaintext): декодируется на приёме, проходит политику по plaintext (минимум 8 символов, буквы и цифры) и шифруется через secrets_service.encrypt() до записи. В ответах сырой пароль не отдаётся; base64(plaintext) виден только в карточке GET /servers/{id}/ipmi и только держателю action view_credentials. Power-операции и ротация — асинхронные: endpoint валидирует запрос и ставит задачу worker'у (202 + task_id), фактический результат зависит от живого BMC.",
  examples: [
    {
      id: "ipmi-create",
      title: "Зарегистрировать IPMI-контроллер",
      method: "POST",
      path: "/api/server/v1/servers/{server_id}/ipmi",
      auth: "Bearer + (ipmi_controller, *, create)",
      description:
        "Создаёт запись BMC для сервера (1:1, UNIQUE на server_id). Пароль передаётся как password_b64 = base64.b64encode(plaintext): декодируется, проходит политику по plaintext и шифруется перед записью. Сервер должен существовать и принадлежать своему департаменту. Повторная регистрация того же сервера → 409 IPMI_DUPLICATE. Idempotency-Key этот POST не читает — повтор отбивается через UNIQUE. В ответе password_b64 всегда null (пароль не возвращается на создании).",
      curl: `SERVER_ID="srv_..."
# пароль кодируется в base64 ИЗ plaintext'а
PASSWORD_B64=$(printf '%s' 'BmcSecret123' | base64)

curl -X POST {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/ipmi \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d "{
    \\"kind\\": \\"redfish\\",
    \\"endpoint_url\\": \\"https://10.10.10.10\\",
    \\"username\\": \\"bmc_admin\\",
    \\"password_b64\\": \\"$PASSWORD_B64\\"
  }"`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

plaintext = "BmcSecret123"
password_b64 = base64.b64encode(plaintext.encode()).decode()

resp = requests.post(
    f"{base_url}/api/server/v1/servers/{server_id}/ipmi",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "kind": "redfish",          # idrac / ilo / ipmi / redfish
        "endpoint_url": "https://10.10.10.10",
        "username": "bmc_admin",
        "password_b64": password_b64,  # base64(plaintext), не сам plaintext
    },
)
resp.raise_for_status()
data = resp.json()

print(data["id"])           # ipm_… — controller_id для rotate/list
print(data["password_b64"]) # null — пароль на создании не возвращается`,
      notes:
        "Ответ 201: карточка контроллера (id ipm_…, server_id, kind, endpoint_url, username, password_b64=null, created_at). Битый base64 в password_b64 → 422. Слабый plaintext → 422 (политика). Чужой/несуществующий сервер → 404; нет create → 403; уже есть контроллер → 409 IPMI_DUPLICATE.",
    },
    {
      id: "ipmi-list",
      title: "Список IPMI-контроллеров",
      method: "GET",
      path: "/api/server/v1/ipmi-controllers",
      auth: "Bearer + (ipmi_controller, *, view)",
      description:
        "Страница контроллеров серверов своего департамента (JOIN с servers даёт dept-фильтр). Два режима пагинации: cursor (cursor=true или after=<token>, envelope {items, next_cursor, has_more}) и legacy offset/limit (envelope {items, total, limit, offset}). Поле password_b64 в списке ВСЕГДА null — plaintext отдаётся только в одиночной карточке GET /servers/{id}/ipmi. Канонический путь kebab-case; /ipmi_controllers (underscore) — скрытый legacy-алиас на тот же handler.",
      curl: `# offset/limit (legacy envelope)
curl "{{BASE_URL}}/api/server/v1/ipmi-controllers?limit=50&offset=0" \\
  -H "Authorization: Bearer {{TOKEN}}"

# cursor-envelope
curl "{{BASE_URL}}/api/server/v1/ipmi-controllers?cursor=true&limit=50" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.get(
    f"{base_url}/api/server/v1/ipmi-controllers",
    headers={"Authorization": f"Bearer {token}"},
    params={"limit": 50, "offset": 0},
)
resp.raise_for_status()
data = resp.json()

for ctrl in data["items"]:
    print(ctrl["id"], ctrl["kind"], ctrl["endpoint_url"], ctrl["last_status"])`,
      notes:
        "200: страница контроллеров. Битый after-токен → 400 INVALID_CURSOR. Нет view на ipmi_controller → 403.",
    },
    {
      id: "ipmi-get",
      title: "Карточка контроллера (с паролем при view_credentials)",
      method: "GET",
      path: "/api/server/v1/servers/{server_id}/ipmi",
      auth: "Bearer + (ipmi_controller, *, view) либо view_credentials",
      description:
        "Возвращает kind / endpoint_url / username / last_probed_at / last_status. Если вызывающий держит action view_credentials, поле password_b64 несёт base64(plaintext) — раскодируй стандартным base64.b64decode; иначе password_b64=null. Раскрытие пароля пишет CRITICAL audit ipmi_controller.credentials_revealed и режется per-IP+server rate-limit'ом PASSWORD_REVEAL_RATE_LIMIT (default 10/min). Cross-dept сервер скрыт за 404; сервер без контроллера → 404 NO_IPMI_CONTROLLER.",
      curl: `SERVER_ID="srv_..."

curl {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/ipmi \\
  -H "Authorization: Bearer {{TOKEN}}"

# при наличии view_credentials password_b64 непустой:
#   echo "<password_b64>" | base64 -d`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

resp = requests.get(
    f"{base_url}/api/server/v1/servers/{server_id}/ipmi",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
data = resp.json()

print(data["kind"], data["endpoint_url"], data["username"])

# password_b64 непустой только при view_credentials
if data.get("password_b64"):
    plaintext = base64.b64decode(data["password_b64"]).decode()
    print("BMC password:", plaintext)`,
      notes:
        "200: карточка. password_b64 — base64(plaintext) только при view_credentials, иначе null. Нет ни view, ни view_credentials → 403. Сервер не найден / чужой dept / нет контроллера → 404. Слишком частый reveal → 429 RATE_LIMIT_EXCEEDED. Сломанный ciphertext → 500 DECRYPT_FAILED.",
    },
    {
      id: "ipmi-credentials-meta",
      title: "Метаданные credentials (без plaintext)",
      method: "GET",
      path: "/api/server/v1/servers/{server_id}/ipmi/credentials",
      auth: "Bearer + (ipmi_controller, *, view_credentials)",
      description:
        "Возвращает kind / endpoint_url / username / password_rotated_at БЕЗ plaintext-пароля. Plaintext доступен только worker'у через internal endpoint. Это INFO-аудит (ipmi_controller.view_credentials_meta), в отличие от CRITICAL reveal в карточке GET /servers/{id}/ipmi.",
      curl: `SERVER_ID="srv_..."

curl {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/ipmi/credentials \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

resp = requests.get(
    f"{base_url}/api/server/v1/servers/{server_id}/ipmi/credentials",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
data = resp.json()

print(data["username"], data["endpoint_url"], data["password_rotated_at"])`,
      notes:
        "200: метаданные без пароля. Нет view_credentials → 403. Сервер не найден / чужой dept / нет контроллера → 404 NO_IPMI_CONTROLLER.",
    },
    {
      id: "ipmi-update",
      title: "Обновить контроллер (без пароля)",
      method: "PATCH",
      path: "/api/server/v1/servers/{server_id}/ipmi",
      auth: "Bearer + (ipmi_controller, *, update)",
      description:
        "Частичное обновление kind / endpoint_url / username. Пароль через PATCH НЕ меняется — для смены пароля есть worker-dispatch POST /ipmi-controllers/{id}/rotate (BMC apply + verify, отдельный CRITICAL audit).",
      curl: `SERVER_ID="srv_..."

curl -X PATCH {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/ipmi \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "username": "bmc_admin2",
    "endpoint_url": "https://10.10.10.11"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

resp = requests.patch(
    f"{base_url}/api/server/v1/servers/{server_id}/ipmi",
    headers={"Authorization": f"Bearer {token}"},
    json={"username": "bmc_admin2"},  # любое подмножество kind/endpoint_url/username
)
resp.raise_for_status()
print(resp.json()["username"])`,
      notes:
        "200: обновлённая карточка. Нет update → 403. Сервер не найден / чужой dept / нет контроллера → 404.",
    },
    {
      id: "ipmi-power-on",
      title: "Включить питание (worker-dispatch)",
      method: "POST",
      path: "/api/server/v1/servers/{server_id}/ipmi/power/on",
      auth: "Bearer + (server, *, power_on)",
      description:
        "Публикует задачу power.on в taskiq-broker. Synchronous-валидации до dispatch'а: сервер существует, dept совпадает, статус != DECOMMISSIONED, есть запись в ipmi_controllers. Возвращает 202 + task_id. Опциональный header Idempotency-Key — повторный POST с тем же ключом вернёт тот же task_id. power/off — то же, но hard-off (ForceOff / chassis power off, без graceful); power/reboot — power-cycle через BMC.",
      curl: `SERVER_ID="srv_..."

curl -X POST {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/ipmi/power/on \\
  -H "Authorization: Bearer {{TOKEN}}"

# с дедупликацией повторов:
#   -H "Idempotency-Key: $(uuidgen)"
# off / reboot — те же пути:
#   .../ipmi/power/off    .../ipmi/power/reboot`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

resp = requests.post(
    f"{base_url}/api/server/v1/servers/{server_id}/ipmi/power/on",
    headers={
        "Authorization": f"Bearer {token}",
        # "Idempotency-Key": "<uuid>",  # опционально, дедуп повторов
    },
)
resp.raise_for_status()  # 202
print(resp.json())  # {"task_id": "tsk_...", "status": "queued"}`,
      notes:
        "202 = задача принята И физически выполнима (контроллер есть), но фактический результат зависит от живого BMC: на сервере без реального BMC задача worker'а упадёт в runtime. off/reboot — пути .../ipmi/power/off и .../ipmi/power/reboot, тот же набор валидаций и кодов. Нет power-action / чужой dept → 403; сервер или контроллер не найден → 404 (SERVER_NOT_FOUND / NO_IPMI_CONTROLLER); decommissioned / idempotent-конфликт → 409; worker недоступен → 503.",
    },
    {
      id: "ipmi-power-cached",
      title: "Кэшированное состояние питания",
      method: "GET",
      path: "/api/server/v1/servers/{server_id}/ipmi/power",
      auth: "Bearer + (server, *, view)",
      description:
        "Читает servers.power_state + last_probed_at контроллера БЕЗ обращения к worker'у/BMC — cached-view для дашбордов. TTL у поля нет: значение перетирается worker'ом при очередном power.{on,off,reboot,status} callback'е, между обновлениями может быть устаревшим. Для живого опроса — POST /servers/{id}/power/status.",
      curl: `SERVER_ID="srv_..."

curl {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/ipmi/power \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

resp = requests.get(
    f"{base_url}/api/server/v1/servers/{server_id}/ipmi/power",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
data = resp.json()
print(data["power_state"], data["last_probed_at"])  # on / off / unknown`,
      notes:
        "200: {server_id, power_state (on/off/unknown), last_probed_at}. Без BMC-probe — может быть unknown/устаревшим. Нет view → 403; сервер не найден / чужой dept → 404. Путь именно .../ipmi/power (под IPMI-префиксом), а не .../power.",
    },
    {
      id: "ipmi-power-status",
      title: "Live-опрос питания через BMC (worker-dispatch)",
      method: "POST",
      path: "/api/server/v1/servers/{server_id}/power/status",
      auth: "Bearer + (server, *, power_status)",
      description:
        "Публикует задачу power.status: worker через Redfish (HTTPS) либо ipmitool опрашивает PowerState BMC и кладёт результат в task.result. Это live-probe (в отличие от кэшированного GET .../ipmi/power). Тот же набор валидаций, что у power.on (включая обязательное наличие ipmi_controller). Возвращает 202 + task_id.",
      curl: `SERVER_ID="srv_..."

curl -X POST {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/power/status \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

resp = requests.post(
    f"{base_url}/api/server/v1/servers/{server_id}/power/status",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()  # 202
print(resp.json())  # {"task_id": "tsk_...", "status": "queued"}
# результат probe читается отдельно по task_id, зависит от живого BMC`,
      notes:
        "202 = задача принята; реальный PowerState приходит только от живого BMC — на контейнерном тестовом сервере без BMC worker-задача упадёт. Путь — /servers/{id}/power/status (НЕ под /ipmi-префиксом). Нет power_status / чужой dept → 403; сервер или контроллер не найден → 404; decommissioned / idempotent → 409; worker недоступен → 503.",
    },
    {
      id: "ipmi-rotate",
      title: "Ротация IPMI-пароля (worker-dispatch)",
      method: "POST",
      path: "/api/server/v1/ipmi-controllers/{controller_id}/rotate",
      auth: "Bearer + (ipmi_controller, *, rotate_credentials)",
      description:
        "Канонический путь ротации BMC-пароля. Пароль в теле НЕ передаётся — worker сам генерит новый, заходит на BMC старым, применяет новый (Redfish PATCH / ipmitool), под новым делает read-only verify и только после verify шлёт ciphertext во внутренний callback /internal/.../credentials_rotated (verify-then-store). Verify не прошёл — storage не коммитится, задача FAILED. Идёт по controller_id (ipm_…), не по server_id. Возвращает 202 + task_id. Поддерживает Idempotency-Key. Rate-limit IPMI_ROTATE_PER_SERVER_RATE_LIMIT (5/min, ключ IP+controller_id).",
      curl: `CONTROLLER_ID="ipm_..."

curl -X POST {{BASE_URL}}/api/server/v1/ipmi-controllers/$CONTROLLER_ID/rotate \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
controller_id = "ipm_..."  # из POST .../ipmi или GET /ipmi-controllers

resp = requests.post(
    f"{base_url}/api/server/v1/ipmi-controllers/{controller_id}/rotate",
    headers={"Authorization": f"Bearer {token}"},
    # тела нет — новый пароль генерит worker, не клиент
)
resp.raise_for_status()  # 202
print(resp.json())  # {"task_id": "tsk_...", "status": "queued"}`,
      notes:
        "202 = задача принята; фактический apply/verify зависит от живого BMC — без него задача FAILED, и пароль в БД не меняется. Нет rotate_credentials / чужой dept → 403; контроллер не найден → 404; decommissioned / idempotent → 409; перебор частоты → 429; worker недоступен → 503.",
    },
    {
      id: "ipmi-rotate-deprecated",
      title: "Старая ротация /credentials/rotate (снята, 410 GONE)",
      method: "POST",
      path: "/api/server/v1/servers/{server_id}/ipmi/credentials/rotate",
      auth: "Bearer + (ipmi_controller, *, rotate_credentials) — но всегда 410",
      description:
        "DEPRECATED. Любой вызов (включая bot / worker_bot, fallback'а нет) отбивается 410 GONE с CRITICAL-аудитом. Endpoint писал plaintext в password_encrypted БЕЗ apply/verify на BMC — любой держатель grant'а (включая скомпрометированный bot) мог молча разорвать out-of-band доступ к стойкам. Используй вместо него POST /ipmi-controllers/{id}/rotate (worker dispatch).",
      curl: `SERVER_ID="srv_..."

# демонстрационно — всегда вернёт 410 GONE:
curl -X POST {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/ipmi/credentials/rotate \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{}'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

# снятый маршрут: всегда 410 GONE
resp = requests.post(
    f"{base_url}/api/server/v1/servers/{server_id}/ipmi/credentials/rotate",
    headers={"Authorization": f"Bearer {token}"},
    json={},
)
print(resp.status_code)              # 410
print(resp.json()["error_code"])     # IPMI_ROTATE_USER_FACING_DEPRECATED`,
      notes:
        "410 IPMI_ROTATE_USER_FACING_DEPRECATED для любого caller'а — миграция на POST /ipmi-controllers/{id}/rotate. Перебор частоты → 429. Приведён только чтобы зафиксировать deprecated-контракт; в реальном коде не вызывать.",
    },
    {
      id: "ipmi-delete",
      title: "Удалить IPMI-контроллер",
      method: "DELETE",
      path: "/api/server/v1/servers/{server_id}/ipmi",
      auth: "Bearer + (ipmi_controller, *, delete)",
      description:
        "Hard-delete записи BMC (CRITICAL audit). После удаления любые power-операции на сервере отбиваются 404 NO_IPMI_CONTROLLER до повторной регистрации.",
      curl: `SERVER_ID="srv_..."

curl -X DELETE {{BASE_URL}}/api/server/v1/servers/$SERVER_ID/ipmi \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
server_id = "srv_..."

resp = requests.delete(
    f"{base_url}/api/server/v1/servers/{server_id}/ipmi",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "200: {ok:true}. Нет delete → 403. Сервер не найден / чужой dept / нет контроллера → 404.",
    },
  ],
};
