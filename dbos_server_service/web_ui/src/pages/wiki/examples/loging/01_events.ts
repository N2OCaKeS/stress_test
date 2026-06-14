import type { ApiSection } from "../types";

export const EVENTS: ApiSection = {
  id: "events",
  title: "Events и статистика",
  service: "loging",
  description:
    "Чтение аудит-журнала и агрегатов плюс приём событий от сервисов. Read-эндпоинты " +
    "(GET /events, GET /events/stats) — по user JWT, допускаются только loging_admin / " +
    "loging_reader (обе роли видят журнал cross-dept). Ingest (POST /events) — только " +
    "service-to-service по SERVICE_API_KEY, пользовательский JWT не принимается. " +
    "Все пути под /api/logging/v1.",
  examples: [
    {
      id: "events-list",
      title: "Список событий с фильтрами",
      method: "GET",
      path: "{{BASE_URL}}/api/logging/v1/events",
      auth: "Bearer (loging_admin / loging_reader)",
      description:
        "Постранично отдаёт записанные события, сортировка timestamp DESC (свежие первыми). " +
        "Любая комбинация фAND-фильтров: department_id, service, severity, action, actor_id, " +
        "target_id, status, request_id, from_time, to_time, limit, offset. service и action " +
        "нормализуются на сервере зеркально ingest'у (NFKC + lower), так что ?service=AUTH_SERVICE " +
        "найдёт канонические auth_service-события. Признак следующей страницы — has_more " +
        "(дешёвый, без COUNT); точное total считается только при include_total=true.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"  # JWT loging_admin или loging_reader\n\n' +
        'curl "$base_url/api/logging/v1/events?limit=50&offset=0" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"  # JWT loging_admin или loging_reader\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/logging/v1/events",\n' +
        '    params={"limit": 50, "offset": 0},\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, "items:", len(data["items"]), "has_more:", data["has_more"])',
      notes:
        "Ответ 200 — EventListResponse: items[] (EventDetail), has_more, limit, offset, " +
        "total (null без include_total). limit 1..1000 (MAX_QUERY_LIMIT), offset 0..MAX_QUERY_OFFSET. " +
        "Отдельного X-Total-Count заголовка НЕТ — счётчик едет в теле через total. " +
        "Ошибки: MISSING_TOKEN / INVALID_TOKEN / USER_BANNED (401), INSUFFICIENT_ROLE (403), " +
        "RATE_LIMIT_EXCEEDED (429, per-user, fallback IP), 503 AUTH_SERVICE_* при недоступном introspect.",
    },
    {
      id: "events-list-filtered",
      title: "Фильтр по severity / service / status / actor / окну",
      method: "GET",
      path: "{{BASE_URL}}/api/logging/v1/events",
      auth: "Bearer (loging_admin / loging_reader)",
      description:
        "Те же items, что у базового списка, но сужено фильтрами. Окно задаётся from_time / " +
        "to_time (ISO 8601, полуоткрытый интервал [from, to)). Здесь: критичные неуспешные " +
        "события auth_service за сутки. status — один из success / failure / denied / warning. " +
        "actor_id / target_id / request_id — точное совпадение.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'from_time="2026-06-13T00:00:00Z"\n' +
        'to_time="2026-06-14T00:00:00Z"\n\n' +
        'curl -G "$base_url/api/logging/v1/events" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  --data-urlencode "service=auth_service" \\\n' +
        '  --data-urlencode "severity=CRITICAL" \\\n' +
        '  --data-urlencode "status=denied" \\\n' +
        '  --data-urlencode "from_time=$from_time" \\\n' +
        '  --data-urlencode "to_time=$to_time" \\\n' +
        '  --data-urlencode "limit=100"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n\n' +
        'params = {\n' +
        '    "service": "auth_service",\n' +
        '    "severity": "CRITICAL",        # TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL\n' +
        '    "status": "denied",            # success/failure/denied/warning\n' +
        '    "from_time": "2026-06-13T00:00:00Z",\n' +
        '    "to_time": "2026-06-14T00:00:00Z",\n' +
        '    "limit": 100,\n' +
        '    # необязательно: action, actor_id, target_id, request_id, department_id\n' +
        '}\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/logging/v1/events",\n' +
        '    params=params,\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, "matched:", len(r.json()["items"]))',
      notes:
        "Все фильтры AND-комбинируются. severity — точный уровень (не «от и выше»). " +
        "Naive datetime трактуется как UTC. action / actor_id (<=48) / target_id (<=48) / " +
        "request_id (<=64) — точное совпадение. Ошибки те же, что у базового списка.",
    },
    {
      id: "events-list-total",
      title: "Пагинация с точным total",
      method: "GET",
      path: "{{BASE_URL}}/api/logging/v1/events",
      auth: "Bearer (loging_admin / loging_reader)",
      description:
        "include_total=true заставляет посчитать точный COUNT по журналу под текущий фильтр — " +
        "поле total в ответе перестаёт быть null. По умолчанию выключено: COUNT по журналу в " +
        "миллионы строк дорог, для перелистывания достаточно has_more. Включай только когда " +
        "оператору реально нужно «всего N событий».",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n\n' +
        'curl -G "$base_url/api/logging/v1/events" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  --data-urlencode "service=auth_service" \\\n' +
        '  --data-urlencode "include_total=true" \\\n' +
        '  --data-urlencode "limit=1"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/logging/v1/events",\n' +
        '    params={"service": "auth_service", "include_total": "true", "limit": 1},\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, "total:", data["total"], "has_more:", data["has_more"])',
      notes:
        "Ответ 200: total — целое (не null) под фильтр. has_more всё равно отдаётся. " +
        "Без include_total поле total = null. Считается тем же фильтром, что и items.",
    },
    {
      id: "events-stats",
      title: "Агрегаты журнала за окно",
      method: "GET",
      path: "{{BASE_URL}}/api/logging/v1/events/stats",
      auth: "Bearer (loging_admin / loging_reader)",
      description:
        "Счётчики событий за временное окно, сгруппированные по severity, сервису и исходу " +
        "(status), плюс total. Группировку делает Postgres (COUNT GROUP BY) — строки в память " +
        "не тянутся. Окно: по умолчанию последние 24ч; либо window_hours (1..8784), либо явные " +
        "from_time/to_time (если заданы оба — window_hours игнорируется; если один край — второй " +
        "достраивается сдвигом). Фильтры — те же, что у GET /events, сужают выборку под агрегат.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n\n' +
        'curl -G "$base_url/api/logging/v1/events/stats" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  --data-urlencode "window_hours=168"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/logging/v1/events/stats",\n' +
        '    params={"window_hours": 168},  # неделя; или from_time/to_time + фильтры\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'st = r.json()\n' +
        'print(r.status_code, "total:", st["total"])\n' +
        'print("by_severity:", st["by_severity"])\n' +
        'print("by_service:", st["by_service"])\n' +
        'print("by_status:", st["by_status"])',
      notes:
        "Ответ 200 — EventStatsResponse: total, by_severity, by_service, by_status (dict имя→счётчик; " +
        "уровни без событий в map отсутствуют, нулём не подставляются), from_time/to_time (фактическое " +
        "окно UTC), truncated (true если GROUP BY отменился по statement_timeout — счётчики неполны). " +
        "Ошибки: 401 нет/неверный токен, 403 INSUFFICIENT_ROLE, 429 RATE_LIMIT_EXCEEDED.",
    },
    {
      id: "events-ingest",
      title: "Приём события аудита (ingest)",
      method: "POST",
      path: "{{BASE_URL}}/api/logging/v1/events",
      auth: "Service-key (Bearer SERVICE_API_KEY + X-Service-Identity)",
      description:
        "Сервис записывает одно событие в audit-журнал. ТОЛЬКО service-to-service: Authorization: " +
        "Bearer <SERVICE_API_KEY> плюс обязательный X-Service-Identity, который должен совпасть с " +
        "payload.service (иначе 403 SERVICE_IDENTITY_PAYLOAD_MISMATCH). Пользовательский JWT не " +
        "принимается. Перед записью событие прогоняется через rule engine — если активное правило " +
        "его подавляет (SUPPRESS), возвращается 204 без тела. Идемпотентность — по Idempotency-Key " +
        "заголовку или body-полю idempotency_key; дедуп по паре (service, idempotency_key).",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'service_key="{{SERVICE_KEY}}"  # dev: audit_auth_service_dev_key\n' +
        'ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"\n\n' +
        'curl -X POST "$base_url/api/logging/v1/events" \\\n' +
        '  -H "Authorization: Bearer $service_key" \\\n' +
        '  -H "X-Service-Identity: auth_service" \\\n' +
        '  -H "Idempotency-Key: wiki-example-$ts" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"timestamp\\":\\"$ts\\",\\"service\\":\\"auth_service\\",' +
        '\\"action\\":\\"wiki.example_ingest\\",\\"actor_type\\":\\"service\\",' +
        '\\"status\\":\\"success\\",\\"allowed\\":true,\\"severity\\":\\"INFO\\",' +
        '\\"details\\":{\\"note\\":\\"wiki doc\\"}}"',
      python:
        'import datetime\n' +
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'service_key = "{{SERVICE_KEY}}"  # dev: audit_auth_service_dev_key\n' +
        'ts = datetime.datetime.now(datetime.timezone.utc).isoformat()\n\n' +
        'payload = {\n' +
        '    "timestamp": ts,                # ISO 8601 с TZ\n' +
        '    "service": "auth_service",      # должен совпасть с X-Service-Identity\n' +
        '    "action": "wiki.example_ingest",\n' +
        '    "actor_type": "service",        # user/bot/service/anonymous/oauth_client\n' +
        '    "status": "success",            # success/failure/denied/warning\n' +
        '    "allowed": True,\n' +
        '    "severity": "INFO",             # опускай — подставит из defaults+правил\n' +
        '    "details": {"note": "wiki doc"},\n' +
        '}\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/logging/v1/events",\n' +
        '    json=payload,\n' +
        '    headers={\n' +
        '        "Authorization": f"Bearer {service_key}",\n' +
        '        "X-Service-Identity": "auth_service",\n' +
        '        "Idempotency-Key": f"wiki-example-{ts}",\n' +
        '    },\n' +
        ')\n' +
        '# 201 -> {"id", "received_at"}; 204 -> событие подавлено правилом\n' +
        'print(r.status_code, r.json() if r.content else "(suppressed)")',
      notes:
        "Ответ 201 — EventResponse {id, received_at (UTC)}; 204 если событие подавлено SUPPRESS-правилом " +
        "(без тела). Idempotency-Key опционален; если задан и в заголовке, и в теле — после NFKC должны " +
        "совпасть. Ошибки: 401 INVALID_SERVICE_KEY / MISSING_SERVICE_IDENTITY, 403 RESERVED_SERVICE_NAME " +
        "(service=loging_service) / SERVICE_IDENTITY_PAYLOAD_MISMATCH, 409 IDEMPOTENCY_KEY_CONFLICT, " +
        "413 PAYLOAD_TOO_LARGE, 400 INVALID_CONTENT_LENGTH, 422 VALIDATION_ERROR, 429 RATE_LIMIT_EXCEEDED " +
        "(per X-Service-Identity, default 100/min).",
    },
  ],
};
