import type { ApiSection } from "../types";

export const REVEAL_TRANSFER_RECOVER: ApiSection = {
  id: "reveal-transfer-recover",
  title: "Reveal / Transfer / Recover",
  service: "secret",
  description:
    "Три CRITICAL-операции над одной credential'ой. reveal расшифровывает секрет и отдаёт его как secret_b64 = base64(plaintext) — клиент обязан декодировать обратно (round-trip к тому, что положили в secret_b64 при create). На reveal висит скользящий 5-минутный throttle per (actor, cred): первый вызов в окне пишет CRITICAL-аудит tokens.revealed, повторные — INFO tokens.revealed_throttled, но HTTP-ответ обоих — 200 с секретом. transfer и recover работают ТОЛЬКО над заблокированной (status=blocked) кред'ой и только под admin-ролью secret_service владеющего отдела (account_admin к содержимому секретов не подпущен и transfer/recover делать не может). transfer переназначает владельца и снимает блокировку; recover просто снимает блокировку в окне 30 дней с момента blocked_at. Кред'у блокирует не пользователь, а каскад lifecycle (удаление owner-юзера/отдела, отзыв доступа отдела к secret_service) — отсюда и сценарий «восстановить осиротевший секрет».",
  examples: [
    {
      id: "reveal",
      title: "Раскрыть секрет (decode secret_b64)",
      method: "POST",
      path: "/api/secret/v1/credentials/{cred_id}/reveal",
      auth: "Bearer + RoleACL.can_read (или owner для personal-scope)",
      description:
        "Расшифровывает секрет и возвращает { login, secret_b64 }, где secret_b64 = base64(plaintext). Клиент декодирует его обратно — это round-trip к строке, которую закодировали в secret_b64 при create/PATCH (UTF-8, включая многобайтовые символы). Тело запроса пустое. Throttle — скользящее 5-мин окно per (actor, cred): первый reveal пишет CRITICAL tokens.revealed, повторный в окне — INFO tokens.revealed_throttled с count в details; HTTP оба раза 200 с секретом (счётчик не блокирует выдачу, а только меняет severity аудита). Перед decrypt'ом проверяется окно валидности [valid_from, valid_to]: до valid_from → 410 SECRET_NOT_YET_VALID, после valid_to → 410 SECRET_EXPIRED (metadata-GET при этом остаётся 200 — UI показывает «продлите токен»). Заблокированная кред'а → 410 CREDENTIAL_BLOCKED.",
      curl: `cred_id="cred_7d168ddf415fc1fae3df404f31f54423"

# reveal отдаёт secret_b64 = base64(plaintext) — декодируем обратно
curl -s -X POST {{BASE_URL}}/api/secret/v1/credentials/$cred_id/reveal \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  | python3 -c "import sys,json,base64; r=json.load(sys.stdin); print('login:', r['login']); print('secret:', base64.b64decode(r['secret_b64']).decode())"`,
      python: `import base64
import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_7d168ddf415fc1fae3df404f31f54423"

resp = requests.post(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/reveal",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
data = resp.json()

# secret_b64 — это base64(plaintext); декодируем обратно в исходную строку
login = data["login"]              # str | None
secret = base64.b64decode(data["secret_b64"]).decode()
print(login, secret)

# повторный reveal в течение 5 минут вернёт тот же 200 + секрет,
# но в аудите это уже tokens.revealed_throttled (INFO) с count в details`,
      notes:
        "Ответ 200: { login: str | null, secret_b64: \"<base64(plaintext)>\" }. Первый reveal в 5-мин окне → CRITICAL tokens.revealed; повторный → INFO tokens.revealed_throttled (count в details), HTTP всё равно 200 с секретом. Нет права на read → 403 CREDENTIAL_ACCESS_DENIED. Кред'а не найдена / cross-dept miss → 404 CREDENTIAL_NOT_FOUND. status=blocked → 410 CREDENTIAL_BLOCKED (blocked_reason/blocked_at в details). Reveal до valid_from → 410 SECRET_NOT_YET_VALID; после valid_to → 410 SECRET_EXPIRED (оба пишут INFO tokens.revealed_blocked_by_validity). Битый ciphertext/AAD → 422 DECRYPT_FAILED; внутренняя ошибка → 500 DECRYPT_INTERNAL_ERROR; ключ версии шифротекста не в env → 503 ENCRYPTION_KEY_MISSING.",
    },
    {
      id: "transfer",
      title: "Передать владельца заблокированной кред'ы",
      method: "POST",
      path: "/api/secret/v1/credentials/{cred_id}/transfer",
      auth: "Bearer + admin secret_service владеющего отдела (НЕ account_admin)",
      description:
        "Переназначает владельца заблокированной кред'ы и одновременно снимает блокировку (status → active, blocked_at/blocked_reason → null). Работает ТОЛЬКО над status=blocked (active → 422 CREDENTIAL_NOT_BLOCKED). Тело TransferRequest требует РОВНО одно из owner-полей плюс обязательный reason (1..256, попадает в CRITICAL-аудит): для personal-scope — new_owner_user_id, для department/cross_department — new_owner_dept_id. Передашь не то поле под scope → 422 INVALID_TRANSFER_TARGET; задашь оба или ни одного → 422 VALIDATION_ERROR (валидатор схемы). Доступ — только под service-ролью admin в secret_service того отдела, что владеет кред'ой; account_admin намеренно не допущен, владельца восстанавливает департамент-уровень. Под капотом FOR UPDATE на строке, чтобы параллельные transfer'ы не назначили непредсказуемого владельца.",
      curl: `cred_id="cred_7d168ddf415fc1fae3df404f31f54423"

# personal-scope: новый владелец — user; department/cross_department: new_owner_dept_id
curl -s -X POST {{BASE_URL}}/api/secret/v1/credentials/$cred_id/transfer \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "new_owner_user_id": "usr_a8c91154ae3a9a3d2ebebbd2ca10f81a",
    "reason": "reassign after owner left"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_7d168ddf415fc1fae3df404f31f54423"

# ровно одно из owner-полей + обязательный reason
# personal  -> new_owner_user_id
# department/cross_department -> new_owner_dept_id
resp = requests.post(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/transfer",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "new_owner_user_id": "usr_a8c91154ae3a9a3d2ebebbd2ca10f81a",
        "reason": "reassign after owner left",
    },
)
resp.raise_for_status()
cred = resp.json()
print(cred["owner_user_id"], cred["status"])  # новый владелец, status=active`,
      notes:
        "Ответ 200: CredentialRead с новым владельцем и снятой блокировкой (status=active, blocked_at/blocked_reason=null). Только над status=blocked — для active → 422 CREDENTIAL_NOT_BLOCKED. Тело: ровно одно из new_owner_user_id / new_owner_dept_id + reason (1..256). Оба/ни одного owner-поля → 422 VALIDATION_ERROR; не то поле под scope (например new_owner_dept_id для personal) → 422 INVALID_TRANSFER_TARGET. Не admin secret_service владеющего отдела (или account_admin) → 403 CREDENTIAL_ACCESS_DENIED. Кред'а не найдена → 404 CREDENTIAL_NOT_FOUND. Коллизия имени с активной кред'ой на новом владельце → 409 NAME_DUPLICATE.",
    },
    {
      id: "recover",
      title: "Снять блокировку (окно 30 дней)",
      method: "POST",
      path: "/api/secret/v1/credentials/{cred_id}/recover",
      auth: "Bearer + admin secret_service владеющего отдела (НЕ account_admin)",
      description:
        "Снимает блокировку с заблокированной кред'ы без смены владельца (status → active, blocked_at/blocked_reason → null). Тело пустое. Доступно в окне BLOCKED_RETENTION_DAYS (default 30 дней) с момента blocked_at — позже now - blocked_at > 30 дней → 422 RECOVER_WINDOW_EXPIRED (фоновый sweep к этому моменту обычно уже снёс кред'у). Работает только над status=blocked: active → 422 CREDENTIAL_NOT_BLOCKED. Доступ — как у transfer: только service-роль admin в secret_service владеющего отдела; account_admin не допущен. Если владелец кред'ы был удалён (osиротевший секрет) — сначала transfer на нового владельца, потом эта кред'а уже active; recover применим, когда владелец на месте, а блокировка пришла, например, от временного отзыва доступа.",
      curl: `cred_id="cred_7d168ddf415fc1fae3df404f31f54423"

# тело пустое; снимает status=blocked в окне 30 дней
curl -s -X POST {{BASE_URL}}/api/secret/v1/credentials/$cred_id/recover \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
cred_id = "cred_7d168ddf415fc1fae3df404f31f54423"

# тело не нужно — recover просто переводит blocked -> active
resp = requests.post(
    f"{base_url}/api/secret/v1/credentials/{cred_id}/recover",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
cred = resp.json()
print(cred["status"], cred["blocked_at"])  # active, None`,
      notes:
        "Ответ 200: CredentialRead со снятой блокировкой (status=active, blocked_at/blocked_reason=null), владелец не меняется. Только над status=blocked — для active → 422 CREDENTIAL_NOT_BLOCKED. Просрочено окно (now - blocked_at > BLOCKED_RETENTION_DAYS, default 30 дней) → 422 RECOVER_WINDOW_EXPIRED. Не admin secret_service владеющего отдела (или account_admin) → 403 CREDENTIAL_ACCESS_DENIED. Кред'а не найдена → 404 CREDENTIAL_NOT_FOUND. Коллизия имени с уже-активной кред'ой → 409 NAME_DUPLICATE.",
    },
  ],
};
