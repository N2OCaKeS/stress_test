import type { ApiSection } from "../types";

export const ADMIN_ENCRYPTION: ApiSection = {
  id: "server-admin-encryption",
  title: "Шифрование (ротация ключа)",
  service: "server",
  description:
    "Ротация мастер-ключа шифрования server_service из UI. Ключи — " +
    "инфраструктура, а не бизнес-данные серверов, поэтому эти три ручки под " +
    "/api/server/v1/admin/encryption доступны только платформенному владельцу " +
    "(account_admin) и намеренно вынесены из-под platform_admin_guard, который " +
    "блокирует account_admin на всех остальных server_service-эндпоинтах. " +
    "Ответы несут только статус/версии — никаких паролей или секретов. Новый " +
    "ключ генерит auth_service (POST /admin/service-keys/generate), UI присылает " +
    "его в rotate. Типовой цикл: rotate → поллить migration_status до " +
    "remaining=0 → retire старой версии.",
  examples: [
    {
      id: "admin-encryption-status",
      title: "Прогресс ре-шифрации (для поллинга)",
      method: "GET",
      path: "/api/server/v1/admin/encryption/migration_status",
      auth: "Bearer + platform-роль account_admin",
      description:
        "Read-only прогресс ре-шифрации секретов под активную версию ключа. UI " +
        "поллит этот endpoint после rotate, чтобы показать remaining/total и " +
        "понять, когда можно retire старую версию (remaining=0 и outbox.pending=0).",
      curl: `curl "{{BASE_URL}}/api/server/v1/admin/encryption/migration_status" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.get(
    f"{base_url}/api/server/v1/admin/encryption/migration_status",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
st = resp.json()

print("active_version:", st["active_version"])
print("remaining:", st["remaining"], "/ total:", st["total"])
print("by_version:", st["by_version"])
print("outbox:", st["outbox"])  # pending/failed — сигнал для дренера/оператора`,
      notes:
        "Ответ 200: { remaining, total, active_version, by_version, app_env, outbox, ... }. " +
        "retire безопасен только когда remaining=0 и outbox.pending=0. " +
        "401 при отсутствии/битом токене; 403 ACCOUNT_ADMIN_REQUIRED, если роль не account_admin.",
    },
    {
      id: "admin-encryption-rotate",
      title: "Ротация мастер-ключа (rotate)",
      method: "POST",
      path: "/api/server/v1/admin/encryption/rotate",
      auth: "Bearer + platform-роль account_admin",
      description:
        "Рантайм-ротация мастер-ключа без простоя. Тело: { new_key_b64, mode }. " +
        "new_key_b64 — новый материал в base64 (32 байта после декода, из " +
        "auth_service POST /admin/service-keys/generate). mode: lazy (default) — " +
        "фоновая троттлящаяся перешифровка, сервис остаётся доступен; force — " +
        "maintenance-режим (сервис отвечает 503 на всё, кроме статуса и health, " +
        "пока дренер не осушит миграцию; по завершении режим снимается сам). Новая " +
        "версия сразу активна (новые токены под ней), старые остаются читаемыми, " +
        "фоновая ре-шифрация публикуется через reencrypt-outbox. Идемпотентно: " +
        "повтор с тем же материалом новую версию не плодит.",
      curl: `# new_key_b64 — из auth_service POST /admin/service-keys/generate
curl -X POST "{{BASE_URL}}/api/server/v1/admin/encryption/rotate" \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"new_key_b64": "<base64-32-байта>", "mode": "lazy"}'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

# new_key_b64 генерит auth_service: POST /api/auth/v1/admin/service-keys/generate
new_key_b64 = "<base64-32-байта>"

resp = requests.post(
    f"{base_url}/api/server/v1/admin/encryption/rotate",
    headers={"Authorization": f"Bearer {token}"},
    json={"new_key_b64": new_key_b64, "mode": "lazy"},  # или "force"
)
resp.raise_for_status()
data = resp.json()

print("new_version:", data["new_version"], "previous:", data["previous_version"])
print("idempotent:", data["idempotent"], "mode:", data["mode"], "force_active:", data["force_active"])
# дальше поллить migration_status до remaining=0, затем retire старой версии`,
      notes:
        "Ответ 200: { new_version, previous_version, seeded, idempotent, mode, force_active }. " +
        "force_active=true (только при mode=force) означает, что сервис ушёл в maintenance до осушения. " +
        "422 ROTATE_KEY_INVALID — new_key_b64 не base64/не 32 байта. 401 без токена; 403 ACCOUNT_ADMIN_REQUIRED. " +
        "Аудит encryption.admin_rotate (CRITICAL).",
    },
    {
      id: "admin-encryption-retire",
      title: "Убрать старую версию ключа (retire)",
      method: "POST",
      path: "/api/server/v1/admin/encryption/retire/{version}",
      auth: "Bearer + platform-роль account_admin",
      description:
        "Убирает старую версию мастер-ключа из keystore. Разрешено только когда " +
        "на версии 0 строк (полная ре-шифрация завершена) и она не активна — " +
        "иначе 409. После retire старый материал недоступен, расшифровать токены " +
        "этой версии станет нельзя. version — целое ≥1, номер убираемой версии.",
      curl: `# version — старая версия, у которой migration_status показал remaining=0
curl -X POST "{{BASE_URL}}/api/server/v1/admin/encryption/retire/1" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
version = 1  # старая версия, уже полностью перешифрованная

resp = requests.post(
    f"{base_url}/api/server/v1/admin/encryption/retire/{version}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
data = resp.json()
print("version:", data["version"], "retired:", data["retired"])`,
      notes:
        "Ответ 200: { version, retired, remaining_on_version }. retired=false — версии уже не было (идемпотентный повтор). " +
        "409 KEYSTORE_CANNOT_RETIRE_ACTIVE (версия активна) / KEYSTORE_VERSION_IN_USE (на ней ещё есть строки). " +
        "401 без токена; 403 ACCOUNT_ADMIN_REQUIRED. Аудит encryption.admin_retire (CRITICAL).",
    },
  ],
};
