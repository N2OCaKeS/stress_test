import type { ApiSection } from "../types";

export const SERVICES_LOG: ApiSection = {
  id: "services-log",
  title: "Сервисы и их события",
  service: "loging",
  description:
    "Реестр сервисов-источников аудита. GET /services отдаёт агрегат по каждому сервису, у которого есть хотя бы одно событие в журнале (event_count + last_event_at) — список заведомо короткий, без пагинации (has_more всегда false, limit/offset null). GET /services/{service}/events отдаёт каталог action'ов конкретного сервиса (то, что сервис заявил, что умеет эмитить, через POST /services/{service}/events) — с пагинацией и default_severity. Обе ручки read-only под user-JWT: доступны только loging_admin / loging_reader, обе роли видят cross-dept агрегат целиком. account_admin / department_admin к чтению аудита не допускаются. Лимит — AUDIT_QUERY_RATE_LIMIT (per-IP).",
  examples: [
    {
      id: "services-list",
      title: "Список сервисов-источников событий",
      method: "GET",
      path: "/api/logging/v1/services",
      auth: "Bearer (loging_admin / loging_reader)",
      description:
        "Возвращает по каждому сервису, у которого есть события в audit_events: service (имя), event_count (сколько событий в журнале) и last_event_at (когда писал последний раз). Сортировка и фильтрация по отделам не применяются — read-роли глобальные, агрегат отдаётся cross-dept. Пагинации нет: список короткий (десятки сервисов на платформе), поэтому has_more=false всегда, limit/offset=null, total=len(items). Удобно как первый шаг: понять, кто вообще пишет в аудит, прежде чем фильтровать /events?service=...",
      curl: `curl -s "{{BASE_URL}}/api/logging/v1/services" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.get(
    f"{base_url}/api/logging/v1/services",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
data = resp.json()

for s in data["items"]:
    print(s["service"], s["event_count"], s["last_event_at"])
# has_more всегда False, limit/offset — None: список влезает целиком`,
      notes:
        "Ответ 200: { items: [{service, event_count, last_event_at}], total, has_more=false, limit=null, offset=null }. Нет/неверный токен → 401. Роль не loging_admin/loging_reader → 403 INSUFFICIENT_ROLE. auth_service недоступен (introspect) → 503. Перебор per-IP лимита → 429.",
    },
    {
      id: "service-events-catalog",
      title: "Каталог action'ов конкретного сервиса",
      method: "GET",
      path: "/api/logging/v1/services/{service}/events",
      auth: "Bearer (loging_admin / loging_reader)",
      description:
        "Постранично отдаёт зарегистрированный action-каталог сервиса (service_events): action, description, default_severity, registered_at, updated_at. Это не сами события, а словарь того, что сервис заявил, что умеет эмитить (его наполняет сам сервис через POST /services/{service}/events с SERVICE_API_KEY). Полезно, чтобы посмотреть, какие action'ы и с какой дефолтной severity бывают у сервиса — например при настройке правил (POST /rules) на эти action'ы. Имя сервиса нормализуется на входе (NFKC + lower), так что регистр и Unicode-омоглифы не мешают найти каталог.",
      curl: `service="auth_service"

curl -s "{{BASE_URL}}/api/logging/v1/services/$service/events?limit=50&offset=0" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
service = "auth_service"

resp = requests.get(
    f"{base_url}/api/logging/v1/services/{service}/events",
    headers={"Authorization": f"Bearer {token}"},
    params={"limit": 50, "offset": 0},
)
resp.raise_for_status()
page = resp.json()

for ev in page["items"]:
    print(ev["action"], ev["default_severity"], "-", ev["description"])

# докрутить остаток, если has_more:
if page["has_more"]:
    pass  # повторить с offset = offset + limit`,
      notes:
        "Ответ 200: { service, items: [{action, description, default_severity, registered_at, updated_at}], total, has_more, limit, offset }. limit 1..MAX_QUERY_LIMIT (default 100), offset ≥0. has_more = offset + len(items) < total. У сервиса без зарегистрированного каталога items пустой, total=0 (не 404). Нет/неверный токен → 401, роль не подходит → 403 INSUFFICIENT_ROLE, introspect упал → 503, перебор лимита → 429.",
    },
  ],
};
