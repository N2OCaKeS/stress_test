import type { ApiSection } from "./types";

export const BASICS: ApiSection = {
  id: "basics",
  title: "Основы",
  service: "basics",
  description: "Base URL, заголовки, формат ошибок и аутентификация.",
  examples: [
    {
      id: "basics-base-url",
      title: "Base URL и префиксы сервисов",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/health",
      auth: "Публично",
      description:
        "Все запросы идут на один base URL. У каждого сервиса свой префикс: " +
        "auth_service — /api/auth/v1, loging_service — /api/logging/v1, " +
        "server_service — /api/server/v1. По умолчанию {{BASE_URL}} — адрес этой " +
        "страницы (текущий origin); для локального dev-стека укажи http://localhost:8000. " +
        "Проверить, что сервис жив, можно через /health.",
      curl: 'base_url="{{BASE_URL}}"\ncurl "$base_url/api/auth/v1/health"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'r = requests.get(f"{base_url}/api/auth/v1/health")\n' +
        'print(r.status_code, r.json())',
      notes:
        "Ответ: {\"status\": \"ok\", \"service\": \"auth_service\"}. " +
        "/health не ходит в БД — это liveness-проба. Для readiness (проверка БД) есть /ready.",
    },
    {
      id: "basics-auth-flow",
      title: "Поток аутентификации: login → access_token → Bearer",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/login",
      auth: "Публично (выдаёт токен)",
      description:
        "Базовый сценарий: логинишься по username/password, в ответ получаешь " +
        "access_token (короткоживущий JWT) и refresh_token. Дальше access_token " +
        "кладёшь в заголовок Authorization: Bearer <token> на всех защищённых ручках. " +
        "Пароль уходит в plaintext по TLS — base64 не нужен.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'username="admin"\n' +
        'password="1234"\n\n' +
        '# 1. логинимся, достаём access_token\n' +
        'token=$(curl -s -X POST "$base_url/api/auth/v1/login" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"username\\":\\"$username\\",\\"password\\":\\"$password\\"}" \\\n' +
        '  | python3 -c "import sys,json;print(json.load(sys.stdin)[\'access_token\'])")\n\n' +
        '# 2. ходим с Bearer-токеном\n' +
        'curl "$base_url/api/auth/v1/me" -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'username = "admin"\n' +
        'password = "1234"\n\n' +
        '# 1. логинимся, достаём access_token\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/login",\n' +
        '    json={"username": username, "password": password},\n' +
        ')\n' +
        'token = r.json()["access_token"]\n' +
        'print(r.status_code, token)\n\n' +
        '# 2. ходим с Bearer-токеном\n' +
        'me = requests.get(\n' +
        '    f"{base_url}/api/auth/v1/me",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(me.status_code, me.json())',
      notes:
        "access_token живёт ~10 минут (в dev-стеке — 60). expires_in в ответе — TTL в секундах. " +
        "Когда токен протух, не логинься заново — обнови его через refresh_token (см. соседнюю карточку).",
    },
    {
      id: "basics-refresh",
      title: "Refresh-токен: обновить access без повторного логина",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/refresh",
      auth: "Публично (refresh — opaque secret)",
      description:
        "Когда access_token истёк, меняешь refresh_token на новую пару токенов. " +
        "Старый refresh инвалидируется атомарно — в ответе всегда приходит новый, " +
        "его и сохраняй для следующего обновления. Повторное использование старого " +
        "refresh убивает всю сессию (reuse-detection).",
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
        'print(r.status_code, data["access_token"])\n' +
        'new_refresh = data["refresh_token"]  # сохрани на следующий раз',
      notes:
        "Ответ 200: access_token, refresh_token (новый), token_type, expires_in. " +
        "Ошибки: REFRESH_TOKEN_INVALID / REFRESH_TOKEN_EXPIRED / REFRESH_TOKEN_RACE (все 401). " +
        "На REFRESH_TOKEN_RACE (параллельный refresh уже ротировал сессию) — просто повтори с новым refresh.",
    },
    {
      id: "basics-pagination",
      title: "Пагинация: limit / offset и X-Total-Count",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/users?limit=50&offset=0",
      auth: "Bearer",
      description:
        "List-эндпоинты пагинируются через query-параметры limit (1..200, default 50) " +
        "и offset (>= 0, default 0). Тело ответа — плоский список. Полное число записей " +
        "под текущий фильтр отдаётся в заголовке X-Total-Count, не в теле — по нему " +
        "считаешь, сколько ещё страниц листать.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n\n' +
        '# -D - печатает заголовки ответа, среди них X-Total-Count\n' +
        'curl -D - "$base_url/api/auth/v1/users?limit=2&offset=0" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/auth/v1/users",\n' +
        '    params={"limit": 2, "offset": 0},\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'total = r.headers["X-Total-Count"]\n' +
        'print(r.status_code, "total:", total, "on page:", len(r.json()))',
      notes:
        "limit вне диапазона 1..200 или отрицательный offset → 422 VALIDATION_ERROR. " +
        "Дефолтный limit=50 обычно отдаёт малые наборы целиком.",
    },
    {
      id: "basics-error-format",
      title: "Формат ошибки: error_code и message",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/login",
      auth: "Публично",
      description:
        "Все ошибки приходят единым envelope'ом. Стабильное машиночитаемое поле — " +
        "error_code (например INVALID_CREDENTIALS, REFRESH_TOKEN_INVALID, " +
        "SERVICE_ACCESS_DENIED), завязывайся на него, а не на текст message. " +
        "Поле error — общая категория, message — человекочитаемое описание, " +
        "details — контекст ошибки, request_id — для поиска в логах.",
      curl:
        'base_url="{{BASE_URL}}"\n\n' +
        '# заведомо неверный пароль — увидим envelope ошибки\n' +
        'curl -X POST "$base_url/api/auth/v1/login" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d \'{"username":"admin","password":"wrong"}\'',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n\n' +
        '# заведомо неверный пароль — увидим envelope ошибки\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/login",\n' +
        '    json={"username": "admin", "password": "wrong"},\n' +
        ')\n' +
        'err = r.json()\n' +
        'print(r.status_code, err["error_code"], err["message"])',
      notes:
        "Пример тела: {\"error\": \"unauthorized\", \"error_code\": \"INVALID_CREDENTIALS\", " +
        "\"message\": \"Invalid username or password\", \"details\": {}, " +
        "\"request_id\": \"req_...\", \"timestamp\": \"...\"}. " +
        "Каталог стабильных error_code — в auth_service/API_ENDPOINTS.md.",
    },
  ],
};
