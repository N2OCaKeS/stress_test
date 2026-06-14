import type { ApiSection } from "../types";

export const ROLE_ACL: ApiSection = {
  id: "role-acl",
  title: "Role-ACL",
  service: "secret",
  description:
    "Точечные права на конкретную credential'у внутри одного департамента. RoleACL связывает (cred_id, dept_id, role_name) с парой флагов can_read / can_write — кто (по service-роли auth) может читать секрет и кто менять. Управление ACL живёт под самой кред'ой: POST/GET/DELETE /credentials/{cred_id}/acl. Кто выдаёт ACL зависит от scope кред'ы: personal — только владелец и только в своём департаменте; department — dep_admin владеющего отдела; cross_department — для recipient-отдела сначала нужен DeptGrant, затем ACL выдаёт локальный dep_admin recipient'а. role_name — имя service-роли из auth (reader / operator / admin / кастомная); строка не сверяется с каталогом на уровне ACL, но смысл имеет только совпадение с реальной ролью actor'а при проверке доступа. Пара (dept_id, role_name) уникальна на креду. CRUD на /acl лимитируется RATE_LIMIT_ACL (default 30/minute).",
  examples: [
    {
      id: "role-acl-add",
      title: "Выдать ACL (read)",
      method: "POST",
      path: "/api/secret/v1/credentials/{cred_id}/acl",
      auth: "Bearer + grant_acl: owner (personal) / dep_admin владеющего отдела (department, cross_dep owner-side) / dep_admin recipient'а при наличии DeptGrant (cross_dep recipient-side)",
      description:
        "Создаёт RoleACL для кред'ы cred_id: «носители role_name в департаменте dept_id получают can_read и/или can_write». can_read и can_write по умолчанию false — нужно явно проставить хотя бы один, иначе ACL ничего не даёт. Для personal-кред'ы dept_id обязан совпадать с департаментом владельца (иначе 422 PERSONAL_ACL_OWNER_DEPT_ONLY). Для cross_department-кред'ы, если dept_id — НЕ owner-отдел, до выдачи ACL должен существовать DeptGrant(cred_id, dept_id), иначе 422 DEPT_GRANT_REQUIRED. Пара (dept_id, role_name) уникальна — повтор отбивается 409 ROLE_ACL_DUPLICATE. Пишет audit tokens.role_acl_added (INFO).",
      curl: `curl -X POST {{BASE_URL}}/api/secret/v1/credentials/cred_5da6155c392ff8f9d2ff24ebb3b5b135/acl \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "dept_id": "dep_9eab089d0271115c232a734001c004e0",
    "role_name": "reader",
    "can_read": true,
    "can_write": false
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_5da6155c392ff8f9d2ff24ebb3b5b135"

resp = requests.post(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/acl",
    headers={"Authorization": f"Bearer {token}"},
    json={
        # для personal — департамент владельца; для cross_dep recipient — отдел с DeptGrant
        "dept_id": "dep_9eab089d0271115c232a734001c004e0",
        # имя service-роли auth (reader / operator / admin / кастомная)
        "role_name": "reader",
        "can_read": True,
        # дефолт у обоих флагов — False; без can_read/can_write ACL бесполезен
        "can_write": False,
    },
)
resp.raise_for_status()
acl = resp.json()

print(acl["id"])  # acl_… — нужен для revoke
print(acl["granted_by_user_id"], acl["granted_at"])`,
      notes:
        "Ответ 201: RoleACLRead { id, cred_id, dept_id, role_name, can_read, can_write, granted_by_user_id, granted_at }. Дубль (dept_id, role_name) → 409 ROLE_ACL_DUPLICATE. cross_dep recipient без DeptGrant → 422 DEPT_GRANT_REQUIRED. personal с чужим dept_id → 422 PERSONAL_ACL_OWNER_DEPT_ONLY. Нет grant_acl → 403 CREDENTIAL_ACCESS_DENIED. Кред'а не найдена / чужой отдел → 404 CREDENTIAL_NOT_FOUND. Перебор лимита (RATE_LIMIT_ACL, 30/min) → 429.",
    },
    {
      id: "role-acl-add-write",
      title: "Выдать ACL (read + write)",
      method: "POST",
      path: "/api/secret/v1/credentials/{cred_id}/acl",
      auth: "Bearer + grant_acl (см. выдачу read-ACL)",
      description:
        "Тот же эндпоинт, но с can_write=true: носители role_name смогут не только revealть секрет (can_read), но и выполнять write-действия над кред'ой (update / delete / grant_acl / grant_dept / manage_status — все они требуют can_write на ACL, не просто can_read). Выдавать стоит осознанно: write-ACL фактически делает носителя роли соуправляющим кред'ой в своём департаменте. Один и тот же role_name нельзя завести дважды на один департамент — если read-ACL уже есть, его надо снять и выдать заново с нужными флагами (отдельного PATCH у ACL нет).",
      curl: `curl -X POST {{BASE_URL}}/api/secret/v1/credentials/cred_5da6155c392ff8f9d2ff24ebb3b5b135/acl \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "dept_id": "dep_9eab089d0271115c232a734001c004e0",
    "role_name": "operator",
    "can_read": true,
    "can_write": true
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_5da6155c392ff8f9d2ff24ebb3b5b135"

resp = requests.post(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/acl",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "dept_id": "dep_9eab089d0271115c232a734001c004e0",
        "role_name": "operator",
        "can_read": True,
        # write-действия (update/delete/grant_acl/grant_dept/manage_status) требуют can_write
        "can_write": True,
    },
)
resp.raise_for_status()
acl = resp.json()
print(acl["id"], acl["role_name"], acl["can_read"], acl["can_write"])`,
      notes:
        "Ответ 201: RoleACLRead с can_write=true. Поменять флаги у существующего ACL нельзя одним запросом (нет PATCH) — revoke + повторный add. Прочие коды как у выдачи read-ACL: 409 / 422 / 403 / 404 / 429.",
    },
    {
      id: "role-acl-list",
      title: "Список ACL кред'ы",
      method: "GET",
      path: "/api/secret/v1/credentials/{cred_id}/acl",
      auth: "Bearer + read (owner / dep_admin владеющего отдела / recipient dep_admin для cross_dep / admin secret_service владеющего отдела)",
      description:
        "Возвращает все RoleACL, привязанные к кред'е cred_id, без пагинации — { items: RoleACLRead[] }. Видеть список управления может любой, у кого есть read на кред'у (не только тот, кто умеет grant_acl): reader+ владеющего отдела, recipient dep_admin для cross_dep, admin secret_service отдела. Каждый элемент несёт dept_id, role_name, флаги can_read/can_write и кто/когда выдал (granted_by_user_id, granted_at). Сам секрет здесь не отдаётся — только метаданные доступа.",
      curl: `curl {{BASE_URL}}/api/secret/v1/credentials/cred_5da6155c392ff8f9d2ff24ebb3b5b135/acl \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_5da6155c392ff8f9d2ff24ebb3b5b135"

resp = requests.get(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/acl",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()

for acl in resp.json()["items"]:
    rw = []
    if acl["can_read"]:
        rw.append("read")
    if acl["can_write"]:
        rw.append("write")
    print(acl["id"], acl["dept_id"], acl["role_name"], "/".join(rw))`,
      notes:
        "Ответ 200: RoleACLList = { items: RoleACLRead[] } (пустой массив, если ACL нет). Нет read → 403 CREDENTIAL_ACCESS_DENIED. Кред'а не найдена / чужой отдел → 404 CREDENTIAL_NOT_FOUND.",
    },
    {
      id: "role-acl-revoke",
      title: "Снять ACL",
      method: "DELETE",
      path: "/api/secret/v1/credentials/{cred_id}/acl/{acl_id}",
      auth: "Bearer + grant_acl: owner / dep_admin владеющего отдела / admin secret_service отдела; recipient dep_admin может снимать только свои ACL внутри своего отдела",
      description:
        "Удаляет конкретный RoleACL по acl_id. Снятие — write-действие (требует grant_acl, то есть can_write на уровне доступа), как и выдача. Для cross_department-кред'ы ACL recipient-отдела может снять либо локальный dep_admin этого отдела, либо dep_admin владеющего отдела (он мощнее). acl_id должен принадлежать именно этой cred_id — иначе 404 ROLE_ACL_NOT_FOUND. Пишет audit tokens.role_acl_revoked (INFO). Операция точечная: каскадное снятие всех ACL recipient-отдела происходит при revoke DeptGrant'а или удалении департамента/пользователя (см. lifecycle).",
      curl: `curl -X DELETE {{BASE_URL}}/api/secret/v1/credentials/cred_5da6155c392ff8f9d2ff24ebb3b5b135/acl/acl_8a295ca1c08b171eb3d3193aef384df6 \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_5da6155c392ff8f9d2ff24ebb3b5b135"
acl_id = "acl_8a295ca1c08b171eb3d3193aef384df6"

resp = requests.delete(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/acl/{acl_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.json())  # {"ok": true}`,
      notes:
        "Ответ 200: OkResponse = { ok: true }. Неизвестный acl_id (или ACL чужой кред'ы) → 404 ROLE_ACL_NOT_FOUND. Кред'а не найдена / чужой отдел → 404 CREDENTIAL_NOT_FOUND. Нет grant_acl → 403 CREDENTIAL_ACCESS_DENIED. Перебор лимита (RATE_LIMIT_ACL, 30/min) → 429.",
    },
  ],
};
