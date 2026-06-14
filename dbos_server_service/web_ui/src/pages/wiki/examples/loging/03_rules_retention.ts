import type { ApiSection } from "../types";

export const RULES_RETENTION: ApiSection = {
  id: "rules-retention",
  title: "Правила и retention",
  service: "loging",
  description:
    "Две admin-only зоны loging_service. Правила (audit_rules) — это severity-override / suppress фильтры, через которые прогоняется каждое входящее событие на ingest: SUPPRESS дропает событие (ingest вернёт 204), ALLOW записывает и обрывает цепочку, OVERRIDE_SEVERITY меняет severity и идёт дальше. Retention — политика срока хранения: глобальная (одна строка) либо набор строк по severity×service. Фоновая ротация раз в сутки в 00:00 MSK удаляет события старше retain_days. Всё под /api/logging/v1. Чтение и запись правил и retention требуют строго platform_role=loging_admin — ни loging_reader, ни account_admin, ни department_admin сюда не пускаются (для правил даже GET закрыт для reader, чтобы не раскрывать топологию мониторинга). События самого loging_service всегда обходят rule engine и защищены от ротации.",
  examples: [
    {
      id: "rules-list",
      title: "Список правил",
      method: "GET",
      path: "/api/logging/v1/rules",
      auth: "Bearer (loging_admin)",
      description:
        "Постранично отдаёт правила, отсортированные по priority DESC. Envelope: { items, total, has_more, limit, offset }. На таблице audit_rules строк мало, поэтому total считается всегда. Доступ строго loging_admin — loging_reader сюда не пускается (в отличие от GET /events): состав SUPPRESS/OVERRIDE-правил раскрывает топологию мониторинга. limit 1..MAX_QUERY_LIMIT (default 100), offset с 0. Идёт в per-IP rate-limit AUDIT_QUERY_RATE_LIMIT.",
      curl: `curl "{{BASE_URL}}/api/logging/v1/rules?limit=50&offset=0" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.get(
    f"{base_url}/api/logging/v1/rules",
    headers={"Authorization": f"Bearer {token}"},
    params={"limit": 50, "offset": 0},
)
resp.raise_for_status()
page = resp.json()

print(page["total"], "правил всего")
for rule in page["items"]:
    print(rule["id"], rule["priority"], rule["effect"], rule["match_action"])

# докрутить страницы по offset, пока has_more == True`,
      notes:
        "Envelope: { items, total, has_more, limit, offset }. items отсортированы priority DESC. Роль не loging_admin → 403 INSUFFICIENT_ROLE. Превышен per-IP лимит → 429 (Retry-After). auth_service недоступен → 503.",
    },
    {
      id: "rules-create",
      title: "Создать правило",
      method: "POST",
      path: "/api/logging/v1/rules",
      auth: "Bearer (loging_admin)",
      description:
        "Заводит правило фильтрации. Критерии match_* (None = «любое значение»): match_service (точное имя сервиса, snake_case [a-z_]), match_action (точное имя ИЛИ glob с одной звёздочкой на сегмент: 'user.*' матчит 'user.login', но не 'user.login.extra'), match_status (success|failure|denied|warning), match_severity (один из шести уровней), match_allowed (bool). Эффект effect: SUPPRESS (дроп — ingest вернёт 204), DROP (alias к SUPPRESS, в БД и ответе всегда SUPPRESS), ALLOW (записать и оборвать цепочку), OVERRIDE_SEVERITY (сменить severity и продолжить). Для OVERRIDE_SEVERITY обязателен effect_severity; для остальных эффектов effect_severity задавать нельзя. priority 1..1000 (выше = раньше). Точное (не glob) match_action валидируется против реестра service_events — нельзя завести правило на незарегистрированный action (если реестр непустой); глоб эту проверку обходит. Префиксы logging.*/logging_rule.*/audit.* запрещены — self-audit всё равно минует rule engine.",
      curl: `curl -X POST {{BASE_URL}}/api/logging/v1/rules \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "wiki_override_server_success",
    "description": "wiki_ пример: понизить severity успешных server-операций",
    "is_active": true,
    "priority": 50,
    "match_service": "server_service",
    "match_action": "server.*",
    "match_status": "success",
    "effect": "OVERRIDE_SEVERITY",
    "effect_severity": "WARNING"
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.post(
    f"{base_url}/api/logging/v1/rules",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "wiki_override_server_success",
        "description": "wiki_ пример правила",
        "is_active": True,
        "priority": 50,
        # любой match_* можно опустить → критерий «любое значение»
        "match_service": "server_service",
        "match_action": "server.*",  # glob обходит проверку по реестру
        "match_status": "success",
        # effect_severity обязателен только для OVERRIDE_SEVERITY
        "effect": "OVERRIDE_SEVERITY",
        "effect_severity": "WARNING",
    },
)
resp.raise_for_status()
rule = resp.json()
print(rule["id"])  # rl_… — нужен для get/patch/delete

# SUPPRESS-правило: effect="SUPPRESS", effect_severity опустить
# DROP принимается как alias, в ответе всё равно вернётся "SUPPRESS"`,
      notes:
        "Ответ 201: RuleResponse (id вида rl_<hex>). effect=OVERRIDE_SEVERITY без effect_severity → 422 EFFECT_SEVERITY_REQUIRED. effect≠OVERRIDE_SEVERITY с заданным effect_severity → 422 EFFECT_SEVERITY_NOT_ALLOWED. Точное match_action не в реестре → 422 UNKNOWN_MATCH_ACTION. Дубль name → 409 RULE_NAME_CONFLICT. Невалидный charset name/match_service/match_action → 422 VALIDATION_ERROR. Write на /rules без rate-limit (admin-операция). Изменение правил сбрасывает in-memory кеш rule engine.",
    },
    {
      id: "rules-get",
      title: "Получить правило по ID",
      method: "GET",
      path: "/api/logging/v1/rules/{rule_id}",
      auth: "Bearer (loging_admin)",
      description:
        "Возвращает одно правило (RuleResponse) по opaque ID вида rl_<hex>. Доступ строго loging_admin. Идёт в тот же per-IP rate-limit AUDIT_QUERY_RATE_LIMIT.",
      curl: `curl "{{BASE_URL}}/api/logging/v1/rules/rl_78a0c1508d824d9986a27668b9447ad3" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
rule_id = "rl_78a0c1508d824d9986a27668b9447ad3"

resp = requests.get(
    f"{base_url}/api/logging/v1/rules/{rule_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
rule = resp.json()
print(rule["name"], rule["effect"], rule["effect_severity"])`,
      notes:
        "200: RuleResponse. Нет такого правила → 404 RULE_NOT_FOUND. Невалидный формат rule_id → 422. Роль не loging_admin → 403. Превышен лимит → 429.",
    },
    {
      id: "rules-patch",
      title: "Обновить правило",
      method: "PATCH",
      path: "/api/logging/v1/rules/{rule_id}",
      auth: "Bearer (loging_admin)",
      description:
        "Partial-update: меняет только переданные поля, остальные сохраняются. Все поля RuleCreate доступны для правки (name, description, is_active, priority, match_*, effect, effect_severity). Инвариант effect ↔ effect_severity проверяется ПОСЛЕ мёржа с текущим состоянием правила: если итоговый effect=OVERRIDE_SEVERITY, итоговый effect_severity не должен быть пустым; при переключении на SUPPRESS/ALLOW/DROP нужно явно сбросить effect_severity в null. DROP в effect нормализуется в SUPPRESS. После обновления сбрасывается кеш rule engine.",
      curl: `# выключить правило, поднять приоритет и переключить на SUPPRESS
curl -X PATCH {{BASE_URL}}/api/logging/v1/rules/rl_78a0c1508d824d9986a27668b9447ad3 \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "is_active": false,
    "priority": 10,
    "effect": "SUPPRESS",
    "effect_severity": null
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
rule_id = "rl_78a0c1508d824d9986a27668b9447ad3"

resp = requests.patch(
    f"{base_url}/api/logging/v1/rules/{rule_id}",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "is_active": False,
        "priority": 10,
        # переключаясь с OVERRIDE_SEVERITY на SUPPRESS, явно гасим severity
        "effect": "SUPPRESS",
        "effect_severity": None,
    },
)
resp.raise_for_status()
rule = resp.json()
print(rule["effect"], rule["effect_severity"], rule["is_active"])`,
      notes:
        "200: обновлённый RuleResponse. Итоговый effect=OVERRIDE_SEVERITY без effect_severity → 422 EFFECT_SEVERITY_REQUIRED. Итоговый effect≠OVERRIDE_SEVERITY с непустым effect_severity → 422 EFFECT_SEVERITY_NOT_ALLOWED. Незарегистрированный точный match_action → 422 UNKNOWN_MATCH_ACTION. Нет правила → 404 RULE_NOT_FOUND. Конфликт нового name → 409 RULE_NAME_CONFLICT. Write без rate-limit.",
    },
    {
      id: "rules-delete",
      title: "Удалить правило",
      method: "DELETE",
      path: "/api/logging/v1/rules/{rule_id}",
      auth: "Bearer (loging_admin)",
      description:
        "Полностью удаляет правило по ID и сбрасывает кеш rule engine. Удаление пишется в self-audit безусловно (logging_rule.delete), минуя SUPPRESS-правила.",
      curl: `curl -X DELETE {{BASE_URL}}/api/logging/v1/rules/rl_78a0c1508d824d9986a27668b9447ad3 \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -i`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"
rule_id = "rl_78a0c1508d824d9986a27668b9447ad3"

resp = requests.delete(
    f"{base_url}/api/logging/v1/rules/{rule_id}",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.status_code)  # 204 — тела нет`,
      notes:
        "204 No Content (тела нет). Нет такого правила → 404 RULE_NOT_FOUND. Роль не loging_admin → 403. Write без rate-limit. Повторный DELETE того же ID → 404 (не идемпотентен, в отличие от DELETE /retention).",
    },
    {
      id: "retention-get",
      title: "Текущая retention-политика",
      method: "GET",
      path: "/api/logging/v1/retention",
      auth: "Bearer (loging_admin)",
      description:
        "Возвращает активную retention-политику (RetentionPolicyResponse) или null, если ротация не настроена (события хранятся вечно). При filtered-режиме активным может быть набор строк severity×service — GET отдаёт одну представительную строку набора, поэтому для просмотра всего набора ориентируйся на тот PUT, которым он был задан. Доступ строго loging_admin. Идёт в per-IP rate-limit AUDIT_QUERY_RATE_LIMIT.",
      curl: `curl "{{BASE_URL}}/api/logging/v1/retention" \\
  -H "Authorization: Bearer {{TOKEN}}"`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.get(
    f"{base_url}/api/logging/v1/retention",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
policy = resp.json()  # может быть None

if policy is None:
    print("ротация не настроена — события хранятся вечно")
else:
    print(policy["retain_days"], policy["severity"], policy["service"])`,
      notes:
        "200: RetentionPolicyResponse либо null. Поля ответа: id (rp_<hex>), retain_days, description, is_active, severity, service, created_at, updated_at. Роль не loging_admin → 403. Превышен лимит → 429.",
    },
    {
      id: "retention-put",
      title: "Задать / заменить retention-политику",
      method: "PUT",
      path: "/api/logging/v1/retention",
      auth: "Bearer (loging_admin)",
      description:
        "Создаёт или заменяет активную политику целиком: гасит весь прежний активный набор и ставит новый (всё в одной транзакции). Тело RetentionPolicyCreate: retain_days ∈ [30, 3650] (минимум 30 — compliance-порог для security-логов), description (≤256), is_active (default true). Опциональные фильтры: severity_filter (список из шести уровней, None/[] = все) и service_filter (список сервисов snake_case [a-z_], None/[] = все). Если оба фильтра пустые — одна глобальная политика на ВСЕ события. Если заданы — Cartesian expansion: одна строка на каждую пару (severity, service). loging_service в service_filter запрещён (его аудит защищён от ротации). Всегда возвращает 200 (в т.ч. при первой настройке).",
      curl: `# глобальная политика: хранить всё 90 дней
curl -X PUT {{BASE_URL}}/api/logging/v1/retention \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "retain_days": 90,
    "description": "wiki_ пример глобальной retention",
    "is_active": true
  }'

# filtered: ERROR/CRITICAL событий server_service хранить год
curl -X PUT {{BASE_URL}}/api/logging/v1/retention \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "retain_days": 365,
    "severity_filter": ["ERROR", "CRITICAL"],
    "service_filter": ["server_service"]
  }'`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

# глобальная политика — фильтры опущены → применяется ко всем событиям
resp = requests.put(
    f"{base_url}/api/logging/v1/retention",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "retain_days": 90,  # 30..3650
        "description": "wiki_ пример retention",
        "is_active": True,
    },
)
resp.raise_for_status()
policy = resp.json()
print(policy["id"], policy["retain_days"])

# filtered: одна строка на каждую пару (severity, service)
resp = requests.put(
    f"{base_url}/api/logging/v1/retention",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "retain_days": 365,
        "severity_filter": ["ERROR", "CRITICAL"],
        "service_filter": ["server_service"],  # loging_service тут запрещён
    },
)
resp.raise_for_status()`,
      notes:
        "200: RetentionPolicyResponse (при filtered-режиме — одна представительная строка набора). retain_days вне [30, 3650] → 422 VALIDATION_ERROR. loging_service в service_filter → 422. Невалидное имя сервиса в фильтре → 422. PUT заменяет весь активный набор, а не мёржит. Write без rate-limit. Фоновая ротация — 00:00 MSK; ручного apply-эндпоинта нет.",
    },
    {
      id: "retention-delete",
      title: "Отключить retention (хранить вечно)",
      method: "DELETE",
      path: "/api/logging/v1/retention",
      auth: "Bearer (loging_admin)",
      description:
        "Идемпотентно помечает is_active=false у ВСЕХ активных политик (в filtered-режиме — весь набор severity×service сразу), чтобы фоновая ротация полностью остановилась и события хранились вечно. Если активных политик нет — всё равно 204.",
      curl: `curl -X DELETE {{BASE_URL}}/api/logging/v1/retention \\
  -H "Authorization: Bearer {{TOKEN}}" \\
  -i`,
      python: `import requests

base_url = "{{BASE_URL}}"
token = "{{TOKEN}}"

resp = requests.delete(
    f"{base_url}/api/logging/v1/retention",
    headers={"Authorization": f"Bearer {token}"},
)
resp.raise_for_status()
print(resp.status_code)  # 204 — даже если активной политики не было`,
      notes:
        "204 No Content. Идемпотентен: повтор на пустом активе тоже 204 (в отличие от DELETE /rules/{id}). Роль не loging_admin → 403. Write без rate-limit. После DELETE GET /retention вернёт null.",
    },
  ],
};
