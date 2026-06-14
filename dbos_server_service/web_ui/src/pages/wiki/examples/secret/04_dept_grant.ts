import type { ApiSection } from "../types";

export const DEPT_GRANT: ApiSection = {
  id: "dept-grant",
  title: "Dept-Grant",
  service: "secret",
  description:
    "DeptGrant управляет cross-department доступом к credential'у со scope=cross_department. Это разрешение «dep_admin отдела-получателя может выдавать RoleACL внутри своего отдела на эту креду». Без существующего DeptGrant(cred_id, recipient_dept_id) попытка выдать RoleACL для отдела-получателя падает в 422 DEPT_GRANT_REQUIRED — это и есть правило DEPT_GRANT_REQUIRED. Выдаёт и снимает DeptGrant только сторона-владелец: dep_admin отдела-владельца или носитель admin-роли secret_service того же отдела (account_admin к содержимому кред НЕ подпущен и DeptGrant выдавать не может). Применимо строго к cross_department: для personal/department кред — 422 DEPT_GRANT_NOT_APPLICABLE. UNIQUE по (cred_id, recipient_dept_id); recipient не может совпадать с owner. Revoke каскадно сносит все RoleACL(cred_id, dept_id=recipient_dept_id). Все пути — под /api/secret/v1, grant'ы вложены в конкретную креду: /credentials/{cred_id}/dept-grants. id грант'а имеет префикс dgr_.",
  examples: [
    {
      id: "dept-grant-create",
      title: "Выдать DeptGrant отделу-получателю",
      method: "POST",
      path: "/api/secret/v1/credentials/{cred_id}/dept-grants",
      auth: "Bearer dep_admin отдела-владельца ИЛИ admin secret_service того же отдела",
      description:
        "Разрешает dep_admin'у отдела recipient_dept_id выдавать RoleACL на эту cross_department-креду внутри своего отдела. cred_id берётся из POST /credentials (scope=cross_department). recipient_dept_id — id отдела-получателя (1..64 символа), должен отличаться от owner_dept_id кред'ы. Сам по себе DeptGrant ещё не даёт чтение секрета — recipient-сторона затем выдаёт RoleACL (can_read/can_write) своим пользователям. account_admin для этой операции не годится: grant_dept требует именно роль уровня отдела-владельца.",
      curl: `curl -X POST {{BASE_URL}}/api/secret/v1/credentials/cred_8a58e88d580783ce83f920f0fc0e9fcc/dept-grants \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "recipient_dept_id": "dep_315ffa42593e4636aca2afda9dc1cc8a"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

# cred_id — cross_department credential, владелец — твой отдел
cred_id = "cred_8a58e88d580783ce83f920f0fc0e9fcc"

resp = requests.post(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/dept-grants",
    headers={"Authorization": f"Bearer {token}"},
    json={
        # отдел-получатель; должен отличаться от owner_dept_id кред'ы
        "recipient_dept_id": "dep_315ffa42593e4636aca2afda9dc1cc8a",
    },
)
resp.raise_for_status()
grant = resp.json()

print(grant["id"])  # dgr_… — нужен для revoke
# теперь dep_admin отдела-получателя может выдавать RoleACL на эту креду`,
      notes:
        "Ответ 201: DeptGrantRead = { id (dgr_…), cred_id, recipient_dept_id, granted_by_user_id, granted_at }. Креда не cross_department (personal/department) → 422 DEPT_GRANT_NOT_APPLICABLE. recipient_dept_id == owner_dept_id → 422 DEPT_GRANT_RECIPIENT_IS_OWNER. Повторный grant на тот же отдел → 409 DEPT_GRANT_DUPLICATE (UNIQUE по cred_id+recipient_dept_id). Не dep_admin/admin отдела-владельца → 403 CREDENTIAL_ACCESS_DENIED. Неизвестный cred_id → 404 CREDENTIAL_NOT_FOUND.",
    },
    {
      id: "dept-grant-list",
      title: "Список DeptGrant'ов кред'ы",
      method: "GET",
      path: "/api/secret/v1/credentials/{cred_id}/dept-grants",
      auth: "Bearer dep_admin отдела-владельца / dep_admin отдела-получателя / admin secret_service отдела-владельца",
      description:
        "Возвращает все DeptGrant'ы, выданные на эту cross_department-креду — по одному на каждый отдел-получатель. Видеть список может сторона-владелец (dep_admin или admin secret_service отдела-владельца), а также dep_admin отдела-получателя (чтобы понимать, на какие креды у него есть grant). Пагинации нет — плоский { items }.",
      curl: `curl {{BASE_URL}}/api/secret/v1/credentials/cred_8a58e88d580783ce83f920f0fc0e9fcc/dept-grants \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_8a58e88d580783ce83f920f0fc0e9fcc"

resp = requests.get(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/dept-grants",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
grants = resp.json()["items"]

for g in grants:
    print(g["id"], "->", g["recipient_dept_id"])`,
      notes:
        "Ответ 200: DeptGrantList = { items: DeptGrantRead[] }. Пустой список → { \"items\": [] }. Неизвестный cred_id → 404 CREDENTIAL_NOT_FOUND. Нет права видеть креду → 403 CREDENTIAL_ACCESS_DENIED.",
    },
    {
      id: "dept-grant-revoke",
      title: "Снять DeptGrant (cascade RoleACL)",
      method: "DELETE",
      path: "/api/secret/v1/credentials/{cred_id}/dept-grants/{grant_id}",
      auth: "Bearer dep_admin отдела-владельца ИЛИ admin secret_service того же отдела",
      description:
        "Отзывает разрешение у отдела-получателя. Каскадно сносит ВСЕ RoleACL(cred_id, dept_id=recipient_dept_id) — то есть все пользователи отдела-получателя моментально теряют доступ к этой креде. grant_id — id из POST/GET (префикс dgr_). Операция доступна той же стороне-владельцу, что и выдача; account_admin не подпущен.",
      curl: `curl -X DELETE \\
  {{BASE_URL}}/api/secret/v1/credentials/cred_8a58e88d580783ce83f920f0fc0e9fcc/dept-grants/dgr_a7e7a333754b371c59646dda28d1fb93 \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_8a58e88d580783ce83f920f0fc0e9fcc"
grant_id = "dgr_a7e7a333754b371c59646dda28d1fb93"

resp = requests.delete(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/dept-grants/{grant_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}
# каскад: все RoleACL отдела-получателя на эту креду снесены`,
      notes:
        "Ответ 200: OkResponse = { \"ok\": true }. Неизвестный grant_id → 404 DEPT_GRANT_NOT_FOUND. Неизвестный cred_id → 404 CREDENTIAL_NOT_FOUND. Не dep_admin/admin отдела-владельца → 403 CREDENTIAL_ACCESS_DENIED. Повторный revoke того же grant_id → 404 DEPT_GRANT_NOT_FOUND (идемпотентно по факту отсутствия).",
    },
  ],
};
