import type { ApiFlow } from "../types";

export const SECRET_FLOWS: ApiFlow[] = [
  {
    id: "flow-cross-dep-grant",
    title: "Cross-dep доступ: create → dept-grant → ACL → reveal",
    description:
      "Канонический сценарий cross_department credential. Владеющий отдел " +
      "хранит общий секрет (например, сервисный токен Jira), а другой отдел " +
      "должен иметь возможность его прочитать. Это двухступенчатый allow: " +
      "сначала dep_admin отдела-владельца выдаёт DeptGrant отделу-получателю " +
      "(разрешение «вы можете раздавать доступ к этой кред'е внутри своего " +
      "отдела»), и только ПОСЛЕ этого dep_admin получателя может выдать RoleACL " +
      "своим ролям. Пока DeptGrant'а нет — получатель кред'у вообще не видит " +
      "(404), а попытка выдать ACL упирается в 422 DEPT_GRANT_REQUIRED. Секрет " +
      "везде ездит как base64: на create секрет кладётся в secret_b64, на reveal " +
      "приходит обратно в secret_b64 — его надо декодировать. Все три участника " +
      "(владелец-dep_admin, получатель-dep_admin, рядовой reader получателя) — " +
      "разные пользователи с разными токенами.",
    steps: [
      {
        title: "1. Владелец создаёт cross_department credential",
        description:
          "dep_admin отдела-владельца (или admin secret_service этого отдела) " +
          "создаёт кред'у со scope=cross_department и owner_dept_id своего " +
          "отдела. Секрет кодируется в base64 ПЕРЕД отправкой и кладётся в " +
          "secret_b64. В ответе plaintext не возвращается — только метаданные " +
          "с id (cred_...). Токен берём из auth_service /login.",
        curl:
          'BASE="{{BASE_URL}}"\n' +
          'OWNER_TOKEN="{{TOKEN}}"   # dep_admin отдела-владельца\n' +
          'OWNER_DEPT="dep_077eca256cd84c0192c905430ca9df36"\n\n' +
          "# секрет кодируется в base64 перед отправкой\n" +
          "SECRET_B64=$(printf '%s' 'cross-dep-secret-XYZ' | base64)\n\n" +
          'curl -s -X POST "$BASE/api/secret/v1/credentials" \\\n' +
          '  -H "Authorization: Bearer $OWNER_TOKEN" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d \'{\n' +
          '    "name": "jira-shared-token",\n' +
          '    "service": "jira",\n' +
          '    "scope": "cross_department",\n' +
          '    "login": "svc",\n' +
          '    "secret_b64": "\'"$SECRET_B64"\'",\n' +
          '    "owner_dept_id": "\'"$OWNER_DEPT"\'"\n' +
          '  }\'\n' +
          '# 201: {"id":"cred_...","scope":"cross_department",...} — запомни id',
        python:
          "import base64, requests\n\n" +
          'base_url = "{{BASE_URL}}"\n' +
          'owner_token = "{{TOKEN}}"   # dep_admin отдела-владельца\n' +
          'owner_dept = "dep_077eca256cd84c0192c905430ca9df36"\n\n' +
          "# секрет кодируется в base64 перед отправкой\n" +
          'secret_b64 = base64.b64encode(b"cross-dep-secret-XYZ").decode()\n\n' +
          "r = requests.post(\n" +
          '    f"{base_url}/api/secret/v1/credentials",\n' +
          '    headers={"Authorization": f"Bearer {owner_token}"},\n' +
          "    json={\n" +
          '        "name": "jira-shared-token",\n' +
          '        "service": "jira",\n' +
          '        "scope": "cross_department",\n' +
          '        "login": "svc",\n' +
          '        "secret_b64": secret_b64,\n' +
          '        "owner_dept_id": owner_dept,\n' +
          "    },\n" +
          ")\n" +
          "assert r.status_code == 201, r.text\n" +
          'cred_id = r.json()["id"]\n' +
          "print(cred_id)  # cred_...",
      },
      {
        title: "2. (до гранта) получатель не видит кред'у / ACL запрещён",
        description:
          "Пока DeptGrant'а нет, dep_admin отдела-получателя кред'у не " +
          "резолвит — GET и попытка выдать RoleACL отвечают 404 " +
          "CREDENTIAL_NOT_FOUND (видимость закрыта раньше, чем проверка " +
          "гранта). Именно это и закрывает DeptGrant на следующем шаге. " +
          "Документированный код 422 DEPT_GRANT_REQUIRED всплывает, когда " +
          "получатель уже может разрешить кред'у, но гранта всё ещё нет.",
        curl:
          'RECIP_ADMIN_TOKEN="{{TOKEN}}"   # dep_admin отдела-получателя\n' +
          'RECIP_DEPT="dep_45dd16bf6b0245a59eb9c1e3ee20c6d5"\n' +
          'CRED_ID="cred_..."\n\n' +
          '# без DeptGrant\'а — выдать ACL нельзя\n' +
          'curl -s -X POST "$BASE/api/secret/v1/credentials/$CRED_ID/acl" \\\n' +
          '  -H "Authorization: Bearer $RECIP_ADMIN_TOKEN" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d \'{"dept_id":"\'"$RECIP_DEPT"\'","role_name":"reader","can_read":true}\'\n' +
          '# 404 CREDENTIAL_NOT_FOUND (кред\'а ещё не видна получателю)',
        python:
          'recip_admin_token = "{{TOKEN}}"   # dep_admin отдела-получателя\n' +
          'recip_dept = "dep_45dd16bf6b0245a59eb9c1e3ee20c6d5"\n\n' +
          "r = requests.post(\n" +
          '    f"{base_url}/api/secret/v1/credentials/{cred_id}/acl",\n' +
          '    headers={"Authorization": f"Bearer {recip_admin_token}"},\n' +
          '    json={"dept_id": recip_dept, "role_name": "reader", "can_read": True},\n' +
          ")\n" +
          "# 404 CREDENTIAL_NOT_FOUND — без DeptGrant'а кред'а не видна;\n" +
          "# как только получатель сможет её резолвить без гранта — будет\n" +
          "# 422 DEPT_GRANT_REQUIRED\n" +
          "print(r.status_code, r.json()[\"error_code\"])",
      },
      {
        title: "3. Владелец выдаёт DeptGrant отделу-получателю",
        description:
          "dep_admin отдела-владельца (или admin secret_service владеющего " +
          "отдела) выдаёт DeptGrant на recipient_dept_id. Это и есть тот самый " +
          "allow, который закрывает DEPT_GRANT_REQUIRED: теперь dep_admin " +
          "получателя сможет раздавать RoleACL внутри своего отдела. Роль " +
          "reader должна существовать в каталоге service_role_definitions " +
          "отдела-получателя (создаётся account_admin'ом / dep_admin'ом в " +
          "auth_service).",
        curl:
          'curl -s -X POST "$BASE/api/secret/v1/credentials/$CRED_ID/dept-grants" \\\n' +
          '  -H "Authorization: Bearer $OWNER_TOKEN" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d \'{"recipient_dept_id":"\'"$RECIP_DEPT"\'"}\'\n' +
          '# 201: {"id":"dgr_...","cred_id":"cred_...","recipient_dept_id":"dep_..."}',
        python:
          "r = requests.post(\n" +
          '    f"{base_url}/api/secret/v1/credentials/{cred_id}/dept-grants",\n' +
          '    headers={"Authorization": f"Bearer {owner_token}"},\n' +
          '    json={"recipient_dept_id": recip_dept},\n' +
          ")\n" +
          "assert r.status_code == 201, r.text\n" +
          'print(r.json()["id"])  # dgr_...',
      },
      {
        title: "4. Получатель выдаёт RoleACL своей роли (теперь 201)",
        description:
          "С существующим DeptGrant'ом dep_admin отдела-получателя выдаёт " +
          "RoleACL: роль reader его отдела получает can_read на эту кред'у. " +
          "Теперь любой пользователь отдела-получателя с ролью reader сможет " +
          "сделать reveal. dept_id в теле — это отдел-получатель.",
        curl:
          'curl -s -X POST "$BASE/api/secret/v1/credentials/$CRED_ID/acl" \\\n' +
          '  -H "Authorization: Bearer $RECIP_ADMIN_TOKEN" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d \'{"dept_id":"\'"$RECIP_DEPT"\'","role_name":"reader","can_read":true}\'\n' +
          '# 201: {"id":"acl_...","role_name":"reader","can_read":true,...}',
        python:
          "r = requests.post(\n" +
          '    f"{base_url}/api/secret/v1/credentials/{cred_id}/acl",\n' +
          '    headers={"Authorization": f"Bearer {recip_admin_token}"},\n' +
          '    json={"dept_id": recip_dept, "role_name": "reader", "can_read": True},\n' +
          ")\n" +
          "assert r.status_code == 201, r.text\n" +
          'print(r.json()["id"])  # acl_...',
      },
      {
        title: "5. Reader получателя делает reveal и декодирует секрет",
        description:
          "Рядовой пользователь отдела-получателя с ролью reader (отдельный " +
          "токен, не dep_admin) дёргает /reveal. Ответ — { login, secret_b64 }, " +
          "где secret_b64 = base64(plaintext); декодируем обратно. Первый " +
          "reveal даёт CRITICAL-аудит tokens.revealed; повторы в 5-минутном " +
          "окне — INFO tokens.revealed_throttled.",
        curl:
          'READER_TOKEN="{{TOKEN}}"   # рядовой reader отдела-получателя\n\n' +
          'curl -s -X POST "$BASE/api/secret/v1/credentials/$CRED_ID/reveal" \\\n' +
          '  -H "Authorization: Bearer $READER_TOKEN"\n' +
          '# 200: {"login":"svc","secret_b64":"Y3Jvc3MtZGVwLXNlY3JldC1YWVo="}\n' +
          '# декод plaintext:\n' +
          '#   echo "Y3Jvc3MtZGVwLXNlY3JldC1YWVo=" | base64 -d  ->  cross-dep-secret-XYZ',
        python:
          'reader_token = "{{TOKEN}}"   # рядовой reader отдела-получателя\n\n' +
          "r = requests.post(\n" +
          '    f"{base_url}/api/secret/v1/credentials/{cred_id}/reveal",\n' +
          '    headers={"Authorization": f"Bearer {reader_token}"},\n' +
          ")\n" +
          "assert r.status_code == 200, r.text\n" +
          "body = r.json()\n" +
          'plaintext = base64.b64decode(body["secret_b64"]).decode()\n' +
          'print(body["login"], plaintext)  # svc cross-dep-secret-XYZ',
      },
    ],
    notes:
      "Ключевой инвариант: DEPT_GRANT_REQUIRED закрывается именно DeptGrant'ом " +
      "от владельца (шаг 3), а не ролью получателя. Порядок жёсткий: dept-grant → " +
      "ACL → reveal. Роль (reader) обязана существовать в каталоге " +
      "service_role_definitions отдела-получателя — иначе ACL отвечает " +
      "INVALID_SERVICE_ROLE (валидируется в auth_service). DELETE dept-grant'а " +
      "каскадно сносит все RoleACL отдела-получателя по этой кред'е.",
  },
  {
    id: "flow-lifecycle-user-deleted",
    title: "Lifecycle-каскад: удаление юзера → блокировка его кред",
    description:
      "Показывает, что происходит с personal-кред'ами при удалении их " +
      "владельца в auth_service. В проде это полностью автоматический " +
      "service-to-service callback: DELETE /users/{id} в auth_service после " +
      "коммита сам дёргает /internal/lifecycle/user-deleted в secret_service. " +
      "Здесь мы воспроизводим его вручную, чтобы было видно эффект: кред'а с " +
      "RoleACL-grantee'ями уходит в blocked (её потом transfer'нут), а " +
      "orphan-кред'а (без grant'ов) удаляется. Аутентификация — НЕ user-JWT, а " +
      "shared service-bearer + X-Service-Identity: auth_service.",
    steps: [
      {
        title: "1. (контекст) у юзера есть personal-кред'а с grantee",
        description:
          "Допустим, в отделе есть пользователь (usr_THROWAWAY) с personal-" +
          "кред'ой, на которую он сам выдал RoleACL (count > 0). Наличие " +
          "grantee'ев — то, что отличает blocked от hard-delete: кред'у есть " +
          "кому передать, поэтому её сохранят в blocked, а не снесут.",
        curl:
          '# подготовка обычными user-эндпоинтами (см. раздел Credentials / RoleACL):\n' +
          '#   POST /credentials  scope=personal       -> cred_THROWAWAY\n' +
          '#   POST /credentials/cred_THROWAWAY/acl     -> RoleACL (count > 0)\n' +
          '# дальше — этот юзер удаляется в auth_service.',
        python:
          "# подготовка обычными user-эндпоинтами (раздел Credentials / RoleACL):\n" +
          "#   POST /credentials  scope=personal       -> cred_THROWAWAY\n" +
          "#   POST /credentials/cred_THROWAWAY/acl     -> RoleACL (count > 0)\n" +
          "# затем юзер удаляется в auth_service.\n" +
          'user_id = "usr_271b5d82bb42445b82beb20378184f56"',
      },
      {
        title: "2. Internal-callback user-deleted (shared service-bearer)",
        description:
          "auth_service (или мы вручную) шлёт событие в secret_service. " +
          "Аутентификация — Bearer <SECRET_INTERNAL_API_KEY> (dev = " +
          "dev-introspect-api-key) + обязательный X-Service-Identity: " +
          "auth_service. Тело несёт user_id удалённого и actor_* того, кто " +
          "инициировал удаление (для аудита). Ответ — счётчики.",
        curl:
          'BASE="{{BASE_URL}}"\n' +
          'INTERNAL_KEY="{{INTERNAL_KEY}}"   # dev: dev-introspect-api-key\n' +
          'USER_ID="usr_271b5d82bb42445b82beb20378184f56"\n' +
          'ACTOR_ID="usr_45c4c368a51a4dc8992ba81697da0086"\n\n' +
          'curl -s -X POST "$BASE/api/secret/v1/internal/lifecycle/user-deleted" \\\n' +
          '  -H "Authorization: Bearer $INTERNAL_KEY" \\\n' +
          '  -H "X-Service-Identity: auth_service" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d \'{"user_id":"\'"$USER_ID"\'","actor_id":"\'"$ACTOR_ID"\'","actor_username":"admin"}\'\n' +
          '# 200: {"blocked_count":1,"deleted_count":0,...}',
        python:
          'base_url = "{{BASE_URL}}"\n' +
          'internal_key = "{{INTERNAL_KEY}}"   # dev: dev-introspect-api-key\n' +
          'user_id = "usr_271b5d82bb42445b82beb20378184f56"\n' +
          'actor_id = "usr_45c4c368a51a4dc8992ba81697da0086"\n\n' +
          "r = requests.post(\n" +
          '    f"{base_url}/api/secret/v1/internal/lifecycle/user-deleted",\n' +
          "    headers={\n" +
          '        "Authorization": f"Bearer {internal_key}",\n' +
          '        "X-Service-Identity": "auth_service",\n' +
          "    },\n" +
          '    json={"user_id": user_id, "actor_id": actor_id, "actor_username": "admin"},\n' +
          ")\n" +
          "assert r.status_code == 200, r.text\n" +
          "summary = r.json()\n" +
          'print(summary["blocked_count"], summary["deleted_count"])  # 1 0',
      },
      {
        title: "3. Проверка: кред'а теперь blocked",
        description:
          "GET кред'ы показывает status=blocked, blocked_reason=" +
          "owner_user_deleted. Метаданные всё ещё читаемы (для аудита/transfer'а), " +
          "но reveal и write по ней запрещены — recover делается через " +
          "transfer новому владельцу admin'ом secret_service. Orphan-кред'ы " +
          "(deleted_count) в выдаче GET уже не существуют.",
        curl:
          'ADMIN_TOKEN="{{TOKEN}}"   # admin secret_service отдела-владельца\n' +
          'CRED_ID="cred_..."\n\n' +
          'curl -s "$BASE/api/secret/v1/credentials/$CRED_ID" \\\n' +
          '  -H "Authorization: Bearer $ADMIN_TOKEN"\n' +
          '# {"status":"blocked","blocked_reason":"owner_user_deleted",...}',
        python:
          'admin_token = "{{TOKEN}}"   # admin secret_service отдела-владельца\n' +
          'cred_id = "cred_..."\n\n' +
          "r = requests.get(\n" +
          '    f"{base_url}/api/secret/v1/credentials/{cred_id}",\n' +
          '    headers={"Authorization": f"Bearer {admin_token}"},\n' +
          ")\n" +
          "card = r.json()\n" +
          'print(card["status"], card["blocked_reason"])  # blocked owner_user_deleted',
      },
    ],
    notes:
      "Без X-Service-Identity: auth_service (или с чужим именем / неверным " +
      "ключом) callback отвечает 401 INTERNAL_AUTH_REQUIRED. Аудит на каскаде: " +
      "tokens.owner_user_deleted_block (WARNING) на blocked + tokens.delete " +
      "(WARNING) на orphan. blocked-кред'у возвращает к жизни POST " +
      "/credentials/{id}/transfer (новый владелец из числа grantee'ев).",
  },
];
