import type { ApiSection } from "../types";

export const ADMIN_ENCRYPTION: ApiSection = {
  id: "secret-admin-encryption",
  title: "Шифрование (ротация ключа)",
  service: "secret",
  description:
    "Ротация мастер-ключа шифрования secret_service из UI. Зеркало " +
    "server_service (UI общий): ключи — инфраструктура, а не бизнес-данные, " +
    "поэтому три ручки под /api/secret/v1/admin/encryption доступны только " +
    "платформенному владельцу (account_admin) и вынесены из-под общего " +
    "platform-guard'а. Ответы несут только статус/версии — никаких секретов. " +
    "Новый ключ генерит auth_service (POST /admin/service-keys/generate), UI " +
    "присылает его в rotate. Типовой цикл: rotate → поллить migration_status до " +
    "remaining_legacy=0 и outbox_pending_count=0 → retire старой версии.",
  examples: [
    {
      id: "secret-admin-encryption-status",
      title: "Прогресс ре-шифрации credentials (поллинг)",
      method: "GET",
      path: "/api/secret/v1/admin/encryption/migration_status",
      auth: "Bearer + platform-роль account_admin",
      description:
        "Read-only прогресс ре-шифрации credentials под активную версию ключа. " +
        "UI поллит после rotate, чтобы показать remaining_legacy/total_rows/" +
        "migrated_pct и понять, когда можно retire старую версию " +
        "(remaining_legacy=0 и outbox_pending_count=0).",
      curl: `curl "{{BASE_URL}}/api/secret/v1/admin/encryption/migration_status" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.get(
    f"{base_url}/api/secret/v1/admin/encryption/migration_status",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
st = resp.json()

print("active_version:", st["active_version"])
print("remaining_legacy:", st["remaining_legacy"], "/ total_rows:", st["total_rows"])
print("migrated_pct:", st["migrated_pct"], "outbox_pending:", st["outbox_pending_count"])`,
      notes:
        "Ответ 200: { active_version, total_rows, by_version, remaining_legacy, migrated_pct, outbox_pending_count, mode, force_active, ... }. " +
        "retire безопасен только когда remaining_legacy=0 и outbox_pending_count=0. " +
        "401 при отсутствии/битом токене; 403 ACCOUNT_ADMIN_REQUIRED, если роль не account_admin.",
    },
    {
      id: "secret-admin-encryption-rotate",
      title: "Ротация мастер-ключа (rotate)",
      method: "POST",
      path: "/api/secret/v1/admin/encryption/rotate",
      auth: "Bearer + platform-роль account_admin",
      description:
        "Рантайм-ротация мастер-ключа без простоя. Тело: { new_key_b64, mode }. " +
        "new_key_b64 — новый материал в base64 (32 байта после декода, из " +
        "auth_service POST /admin/service-keys/generate). mode: lazy (default) — " +
        "фоновая перешифровка без простоя; force — maintenance-окно (сервис " +
        "отвечает 503 на всё, кроме status/health/ready, пока перешифровка не " +
        "завершится). Новая версия сразу активна, старые токены остаются " +
        "читаемыми, фоновая ре-шифрация идёт через reencrypt-outbox. Идемпотентно: " +
        "повтор с тем же материалом версию не плодит.",
      curl: `# new_key_b64 — из auth_service POST /admin/service-keys/generate
curl -X POST "{{BASE_URL}}/api/secret/v1/admin/encryption/rotate" \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{"new_key_b64": "<base64-32-байта>", "mode": "lazy"}'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

# new_key_b64 генерит auth_service: POST /api/auth/v1/admin/service-keys/generate
new_key_b64 = "<base64-32-байта>"

resp = requests.post(
    f"{base_url}/api/secret/v1/admin/encryption/rotate",
    headers={"Authorization": f"Bearer {token}"},
    json={"new_key_b64": new_key_b64, "mode": "lazy"},  # или "force"
)
resp.raise_for_status()
data = resp.json()

print("new_version:", data["new_version"], "previous:", data["previous_version"])
print("idempotent:", data["idempotent"], "mode:", data["mode"])
# дальше поллить migration_status до remaining_legacy=0, затем retire старой версии`,
      notes:
        "Ответ 200: { new_version, previous_version, seeded, idempotent, mode }. " +
        "400 ROTATE_KEY_INVALID — new_key_b64 не base64/не 32 байта. 401 без токена; 403 ACCOUNT_ADMIN_REQUIRED. " +
        "Аудит secrets.admin_encryption_rotate (CRITICAL).",
    },
    {
      id: "secret-admin-encryption-retire",
      title: "Убрать старую версию ключа (retire)",
      method: "POST",
      path: "/api/secret/v1/admin/encryption/retire/{version}",
      auth: "Bearer + platform-роль account_admin",
      description:
        "Убирает старую версию мастер-ключа из keystore. Разрешено только когда " +
        "на версии 0 строк (полная ре-шифрация завершена) и она не активна — " +
        "иначе 409. После retire старый материал недоступен, расшифровать токены " +
        "этой версии станет нельзя. version — целое ≥1.",
      curl: `# version — старая версия, у которой migration_status показал remaining_legacy=0
curl -X POST "{{BASE_URL}}/api/secret/v1/admin/encryption/retire/1" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
version = 1  # старая версия, уже полностью перешифрованная

resp = requests.post(
    f"{base_url}/api/secret/v1/admin/encryption/retire/{version}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
data = resp.json()
print("version:", data["version"], "retired:", data["retired"])`,
      notes:
        "Ответ 200: { version, retired, remaining_on_version }. retired=false — версии уже не было (идемпотентный повтор). " +
        "409 KEYSTORE_CANNOT_RETIRE_ACTIVE / KEYSTORE_VERSION_IN_USE. " +
        "401 без токена; 403 ACCOUNT_ADMIN_REQUIRED. Аудит secrets.admin_encryption_retire (CRITICAL).",
    },
  ],
};
