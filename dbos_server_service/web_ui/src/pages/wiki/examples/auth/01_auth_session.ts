import type { ApiSection } from "../types";

export const AUTH_SESSION: ApiSection = {
  id: "auth-session",
  title: "Аутентификация и сессия",
  service: "auth",
  examples: [
    {
      id: "auth-login",
      title: "Логин по username / password",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/login",
      auth: "Публично",
      description:
        "Проверяет пароль (Argon2id) и выдаёт пару access JWT + refresh (opaque) " +
        "плюс свежий identity-контекст. После 5 неудачных попыток подряд аккаунт " +
        "лочится на 15 минут. Пароль — plaintext по TLS.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'username="admin"\n' +
        'password="1234"\n\n' +
        'curl -X POST "$base_url/api/auth/v1/login" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"username\\":\\"$username\\",\\"password\\":\\"$password\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'username = "admin"\n' +
        'password = "1234"\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/login",\n' +
        '    json={"username": username, "password": password},\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, data["access_token"])\n' +
        'print("refresh:", data["refresh_token"])',
      notes:
        "Ответ 200: access_token, refresh_token, token_type=\"Bearer\", expires_in (сек), identity. " +
        "Ошибки: INVALID_CREDENTIALS (401), USER_BANNED (401), USER_BLOCKED (401), " +
        "ACCOUNT_TEMPORARILY_LOCKED (429 + retry_after_seconds).",
    },
    {
      id: "auth-refresh",
      title: "Обновить access по refresh",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/refresh",
      auth: "Публично (refresh — opaque secret)",
      description:
        "Меняет refresh на новую пару access + refresh. Старый refresh инвалидируется " +
        "атомарно (CAS). Повторное использование уже ротированного refresh трактуется " +
        "как reuse и убивает всю сессию.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'refresh_token="{{TOKEN}}"  # refresh из ответа /login\n\n' +
        'curl -X POST "$base_url/api/auth/v1/refresh" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"refresh_token\\":\\"$refresh_token\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'refresh_token = "{{TOKEN}}"  # refresh из ответа /login\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/refresh",\n' +
        '    json={"refresh_token": refresh_token},\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, data.get("access_token"))',
      notes:
        "Ответ 200: access_token, refresh_token (новый), token_type, expires_in. " +
        "Ошибки (все 401): REFRESH_TOKEN_INVALID (не найден / отозван / reuse → kill-switch), " +
        "REFRESH_TOKEN_EXPIRED, REFRESH_TOKEN_RACE (параллельный refresh уже ротировал — повтори с новым). " +
        "В web-UI refresh обычно едет в HttpOnly cookie dbos_refresh, тело тогда можно слать пустым.",
    },
    {
      id: "auth-logout",
      title: "Logout — отозвать refresh",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/logout",
      auth: "Публично",
      description:
        "Помечает переданный refresh как отозванный. Access не отзывается явно — он " +
        "короткоживущий и просто истечёт. Идемпотентно: повторный logout или logout " +
        "несуществующего refresh всё равно вернёт {\"ok\": true}.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'refresh_token="{{TOKEN}}"  # refresh, который отзываем\n\n' +
        'curl -X POST "$base_url/api/auth/v1/logout" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"refresh_token\\":\\"$refresh_token\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'refresh_token = "{{TOKEN}}"  # refresh, который отзываем\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/logout",\n' +
        '    json={"refresh_token": refresh_token},\n' +
        ')\n' +
        'print(r.status_code, r.json())',
      notes:
        "Ответ 200: {\"ok\": true}. refresh_token опционален — при пустом теле берётся из " +
        "cookie dbos_refresh; если refresh нет нигде, ответ всё равно ok + cookie очищается.",
    },
    {
      id: "auth-me",
      title: "Текущий identity-контекст",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/me",
      auth: "Bearer",
      description:
        "Возвращает свежий снимок identity текущего токена: department, platform_role, " +
        "allowed_services, service_roles, groups, is_banned. Данные перечитываются из БД, " +
        "не из JWT payload — поэтому бан или отзыв роли видны сразу.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n\n' +
        'curl "$base_url/api/auth/v1/me" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/auth/v1/me",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'me = r.json()\n' +
        'print(r.status_code, me["username"], me["platform_role"])',
      notes:
        "Ответ 200 — IdentityContext: user_id, username, display_name, email, department_id, " +
        "department_name, allowed_services, service_roles, groups, is_banned, platform_role, " +
        "subject_type, must_change_password. Без валидного Bearer — 401.",
    },
    {
      id: "auth-token-oauth2-form",
      title: "OAuth2 Password form (для Swagger Authorize)",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/token",
      auth: "Публично",
      description:
        "Тот же логин, что /login, но тело — form-urlencoded (username/password) вместо JSON. " +
        "Нужен только кнопке Authorize в Swagger UI. Скрыт из публичной схемы; снаружи " +
        "используй /login. Ответ — такой же LoginResponse.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'username="admin"\n' +
        'password="1234"\n\n' +
        'curl -X POST "$base_url/api/auth/v1/token" \\\n' +
        '  -H "Content-Type: application/x-www-form-urlencoded" \\\n' +
        '  --data-urlencode "username=$username" \\\n' +
        '  --data-urlencode "password=$password"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'username = "admin"\n' +
        'password = "1234"\n\n' +
        '# data=... отправляет form-urlencoded, а не JSON\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/token",\n' +
        '    data={"username": username, "password": password},\n' +
        ')\n' +
        'print(r.status_code, r.json()["access_token"])',
      notes:
        "Ответ 200: тот же LoginResponse, что у /login. Под общим rate-limit'ом с /login " +
        "(LOGIN_RATE_LIMIT, default 10/minute), чтобы не обходить лимит через /token.",
    },
    {
      id: "auth-health",
      title: "Liveness-проба",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/health",
      auth: "Публично",
      description:
        "Простой liveness-чек: отвечает, пока процесс жив. В БД не ходит. " +
        "Используется как livenessProbe в k8s. Из аудита health-запросы исключены.",
      curl: 'base_url="{{BASE_URL}}"\ncurl "$base_url/api/auth/v1/health"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'r = requests.get(f"{base_url}/api/auth/v1/health")\n' +
        'print(r.status_code, r.json())',
      notes: "Ответ 200: {\"status\": \"ok\", \"service\": \"auth_service\"}.",
    },
    {
      id: "auth-ready",
      title: "Readiness-проба",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/ready",
      auth: "Публично",
      description:
        "Readiness-чек: пингует БД через короткий SELECT 1. Если БД доступна — 200 со " +
        "status=ready и operational-счётчиками; если нет — 503 со status=not_ready и " +
        "reason=db_unreachable (k8s ingress тогда не льёт трафик в pod).",
      curl: 'base_url="{{BASE_URL}}"\ncurl "$base_url/api/auth/v1/ready"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'r = requests.get(f"{base_url}/api/auth/v1/ready")\n' +
        'print(r.status_code, r.json())',
      notes:
        "Ответ 200: {\"status\": \"ready\", \"service\": \"auth_service\", \"counters\": " +
        "{\"audit_dropped_429\": 0, \"failed_login_24h\": 0, \"lockout_active\": 0}}. " +
        "При недоступной БД — 503 + {\"status\": \"not_ready\", \"reason\": \"db_unreachable\"}.",
    },
  ],
};
