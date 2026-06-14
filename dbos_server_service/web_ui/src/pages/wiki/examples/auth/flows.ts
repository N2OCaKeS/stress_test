import type { ApiFlow } from "../types";

export const AUTH_FLOWS: ApiFlow[] = [
  {
    id: "flow-oauth-pkce",
    title: "OAuth2 authorization_code + PKCE",
    description:
      "Полный authorization-code flow с PKCE (RFC 7636) для public/native клиента: " +
      "генерим code_verifier и code_challenge, получаем authorization code на /authorize, " +
      "меняем его вместе с verifier на /token и используем выданный access_token. PKCE " +
      "защищает от перехвата кода: токен выдаётся только тому, кто знает verifier.",
    steps: [
      {
        title: "1. Сгенерировать code_verifier и code_challenge",
        description:
          "verifier — случайная строка (43–128 символов base64url). " +
          "challenge = base64url(sha256(verifier)) без паддинга, метод S256. " +
          "verifier держим у себя, наружу уходит только challenge.",
        curl:
          '# verifier: случайные 32 байта в base64url\n' +
          'verifier=$(openssl rand -base64 32 | tr \'+/\' \'-_\' | tr -d \'=\')\n' +
          '# challenge = base64url(sha256(verifier))\n' +
          'challenge=$(printf %s "$verifier" | openssl dgst -binary -sha256 | openssl base64 | tr \'+/\' \'-_\' | tr -d \'=\')\n' +
          'echo "verifier=$verifier"\n' +
          'echo "challenge=$challenge"',
        python:
          'import hashlib, base64, secrets\n\n' +
          'verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()\n' +
          'challenge = base64.urlsafe_b64encode(\n' +
          '    hashlib.sha256(verifier.encode()).digest()\n' +
          ').rstrip(b"=").decode()\n' +
          'print("verifier", verifier)\n' +
          'print("challenge", challenge)',
      },
      {
        title: "2. GET /authorize → достать code из redirect",
        description:
          "Залогиненный юзер (Bearer access_token) открывает /authorize с client_id, " +
          "redirect_uri, scope, state, response_type=code и PKCE-параметрами. Ответ — 302 " +
          "на redirect_uri?code=...&state=.... Не идём по редиректу, а вытаскиваем code из " +
          "Location. state потом сверяем с тем, что отправляли.",
        curl:
          'base_url="{{BASE_URL}}"\n' +
          'token="{{TOKEN}}"   # access_token залогиненного юзера\n' +
          'client_id="..."\n\n' +
          'curl -i -G "$base_url/api/auth/v1/oauth2/authorize" \\\n' +
          '  -H "Authorization: Bearer $token" \\\n' +
          '  --data-urlencode "client_id=$client_id" \\\n' +
          '  --data-urlencode "redirect_uri=https://app.example.com/callback" \\\n' +
          '  --data-urlencode "scope=server.read server.write" \\\n' +
          '  --data-urlencode "state=xyz123" \\\n' +
          '  --data-urlencode "response_type=code" \\\n' +
          '  --data-urlencode "code_challenge=$challenge" \\\n' +
          '  --data-urlencode "code_challenge_method=S256"\n' +
          '# из заголовка Location скопируй значение code=...',
        python:
          'import requests\n' +
          'from urllib.parse import urlparse, parse_qs\n\n' +
          'base_url = "{{BASE_URL}}"\n' +
          'token = "{{TOKEN}}"   # access_token залогиненного юзера\n' +
          'client_id = "..."\n\n' +
          'r = requests.get(\n' +
          '    f"{base_url}/api/auth/v1/oauth2/authorize",\n' +
          '    headers={"Authorization": f"Bearer {token}"},\n' +
          '    params={\n' +
          '        "client_id": client_id,\n' +
          '        "redirect_uri": "https://app.example.com/callback",\n' +
          '        "scope": "server.read server.write",\n' +
          '        "state": "xyz123",\n' +
          '        "response_type": "code",\n' +
          '        "code_challenge": challenge,\n' +
          '        "code_challenge_method": "S256",\n' +
          '    },\n' +
          '    allow_redirects=False,\n' +
          ')\n' +
          'qs = parse_qs(urlparse(r.headers["location"]).query)\n' +
          'assert qs["state"][0] == "xyz123"  # защита от CSRF\n' +
          'code = qs["code"][0]\n' +
          'print(r.status_code, code)  # 302',
      },
      {
        title: "3. POST /token → обменять code + verifier на токены",
        description:
          "grant_type=authorization_code, передаём code, тот же redirect_uri, client_id " +
          "(+ client_secret для confidential) и code_verifier. Сервер пересчитывает " +
          "sha256(verifier) и сверяет с сохранённым challenge. В ответе access_token и " +
          "refresh_token. Code одноразовый — повторный обмен убьёт выданный токен.",
        curl:
          'base_url="{{BASE_URL}}"\n' +
          'client_id="..."\n' +
          'client_secret="..."   # для confidential; public не шлёт\n\n' +
          'curl -X POST "$base_url/api/auth/v1/oauth2/token" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d "{\\"grant_type\\":\\"authorization_code\\",\\"code\\":\\"<code>\\",\\"redirect_uri\\":\\"https://app.example.com/callback\\",\\"client_id\\":\\"$client_id\\",\\"client_secret\\":\\"$client_secret\\",\\"code_verifier\\":\\"$verifier\\"}"',
        python:
          'import requests\n\n' +
          'base_url = "{{BASE_URL}}"\n' +
          'client_id = "..."\n' +
          'client_secret = "..."   # для confidential; public не шлёт\n\n' +
          'r = requests.post(\n' +
          '    f"{base_url}/api/auth/v1/oauth2/token",\n' +
          '    json={\n' +
          '        "grant_type": "authorization_code",\n' +
          '        "code": code,\n' +
          '        "redirect_uri": "https://app.example.com/callback",\n' +
          '        "client_id": client_id,\n' +
          '        "client_secret": client_secret,\n' +
          '        "code_verifier": verifier,\n' +
          '    },\n' +
          ')\n' +
          'data = r.json()\n' +
          'access_token = data["access_token"]\n' +
          'refresh_token = data["refresh_token"]\n' +
          'print(r.status_code, data["expires_in"], data["scope"])',
      },
      {
        title: "4. Использовать access_token",
        description:
          "Полученный access_token кладём в Authorization: Bearer на запросы к сервисам. " +
          "Клиентский сервис при каждом запросе валидирует его через introspect. Когда " +
          "истечёт — обновляем через refresh_token grant (см. сценарий ротации).",
        curl:
          'base_url="{{BASE_URL}}"\n' +
          'access_token="<из шага 3>"\n\n' +
          'curl "$base_url/api/auth/v1/me" \\\n' +
          '  -H "Authorization: Bearer $access_token"',
        python:
          'import requests\n\n' +
          'base_url = "{{BASE_URL}}"\n\n' +
          'r = requests.get(\n' +
          '    f"{base_url}/api/auth/v1/me",\n' +
          '    headers={"Authorization": f"Bearer {access_token}"},\n' +
          ')\n' +
          'print(r.status_code, r.json())',
      },
    ],
    notes:
      "PKCE обязателен для public-клиентов (is_public=true) с методом S256; для confidential " +
      "опционален, но рекомендуется. Code живёт 5 минут и одноразовый. " +
      "INVALID_GRANT — code истёк/использован/не совпал verifier.",
  },
  {
    id: "flow-login-introspect",
    title: "Логин → introspect (проверка прав клиентским сервисом)",
    description:
      "Как клиентский сервис проверяет права пользователя. Юзер логинится в auth_service и " +
      "получает access_token, ходит с ним в сторонний сервис, а тот валидирует токен через " +
      "/authorization/introspect под своим SERVICE_API_KEY и читает effective service_roles. " +
      "Чувствительные права в JWT не лежат — они пересчитываются из БД на каждом introspect, " +
      "поэтому бан/смена роли действуют немедленно.",
    steps: [
      {
        title: "1. Юзер логинится → access_token",
        description:
          "Обычный /login по username/password. В ответе access_token (короткоживущий JWT) и " +
          "refresh_token. Пароль уходит plaintext по TLS.",
        curl:
          'base_url="{{BASE_URL}}"\n\n' +
          'curl -X POST "$base_url/api/auth/v1/login" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d \'{"username":"admin","password":"1234"}\'',
        python:
          'import requests\n\n' +
          'base_url = "{{BASE_URL}}"\n\n' +
          'r = requests.post(\n' +
          '    f"{base_url}/api/auth/v1/login",\n' +
          '    json={"username": "admin", "password": "1234"},\n' +
          ')\n' +
          'access_token = r.json()["access_token"]\n' +
          'print(r.status_code, access_token[:24], "...")',
      },
      {
        title: "2. Юзер предъявляет токен клиентскому сервису",
        description:
          "Дальше юзер ходит в сторонний сервис (например server_service) с Authorization: " +
          "Bearer <access_token>. Этот шаг — на стороне самого пользователя; клиентский сервис " +
          "получает токен из заголовка входящего запроса.",
        curl:
          'base_url="{{BASE_URL}}"\n' +
          'access_token="<из шага 1>"\n\n' +
          '# запрос к клиентскому сервису (пример)\n' +
          'curl "{{BASE_URL}}/api/server/v1/servers" \\\n' +
          '  -H "Authorization: Bearer $access_token"',
        python:
          'import requests\n\n' +
          'base_url = "{{BASE_URL}}"\n\n' +
          '# запрос к клиентскому сервису (пример)\n' +
          'r = requests.get(\n' +
          '    f"{base_url}/api/server/v1/servers",\n' +
          '    headers={"Authorization": f"Bearer {access_token}"},\n' +
          ')\n' +
          'print(r.status_code)',
      },
      {
        title: "3. Клиентский сервис делает introspect",
        description:
          "Получив user-токен, клиентский сервис вызывает /authorization/introspect под СВОИМ " +
          "SERVICE_API_KEY (а не под user-токеном). В ответе active, identity и effective " +
          "service_roles, пересчитанные из БД. По ним сервис решает, пускать ли запрос.",
        curl:
          'base_url="{{BASE_URL}}"\n' +
          'service_key="dev-introspect-api-key"   # SERVICE_API_KEY клиентского сервиса\n' +
          'user_token="<токен из входящего запроса>"\n\n' +
          'curl -X POST "$base_url/api/auth/v1/authorization/introspect" \\\n' +
          '  -H "Authorization: Bearer $service_key" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d "{\\"token\\":\\"$user_token\\"}"',
        python:
          'import requests\n\n' +
          'base_url = "{{BASE_URL}}"\n' +
          'service_key = "dev-introspect-api-key"   # SERVICE_API_KEY клиентского сервиса\n' +
          'user_token = access_token                 # токен из входящего запроса\n\n' +
          'r = requests.post(\n' +
          '    f"{base_url}/api/auth/v1/authorization/introspect",\n' +
          '    headers={"Authorization": f"Bearer {service_key}"},\n' +
          '    json={"token": user_token},\n' +
          ')\n' +
          'info = r.json()\n' +
          'if not info["active"] or info["is_banned"]:\n' +
          '    raise PermissionError("token rejected")\n' +
          'roles = info["service_roles"].get("server_service", [])\n' +
          'print(r.status_code, info["username"], roles)',
      },
      {
        title: "4. Решение по effective service_roles",
        description:
          "Сервис сверяет роли из introspect с требованием эндпоинта. Можно и точечно — через " +
          "/authorization/service-access, который сразу отдаёт allowed + роли для одного сервиса, " +
          "без полного introspect-ответа.",
        curl:
          'base_url="{{BASE_URL}}"\n' +
          'service_key="dev-introspect-api-key"\n' +
          'user_token="<токен из входящего запроса>"\n\n' +
          'curl -X POST "$base_url/api/auth/v1/authorization/service-access" \\\n' +
          '  -H "Authorization: Bearer $service_key" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d "{\\"subject_token\\":\\"$user_token\\",\\"service_name\\":\\"server_service\\"}"',
        python:
          'import requests\n\n' +
          'base_url = "{{BASE_URL}}"\n' +
          'service_key = "dev-introspect-api-key"\n' +
          'user_token = access_token\n\n' +
          'r = requests.post(\n' +
          '    f"{base_url}/api/auth/v1/authorization/service-access",\n' +
          '    headers={"Authorization": f"Bearer {service_key}"},\n' +
          '    json={"subject_token": user_token, "service_name": "server_service"},\n' +
          ')\n' +
          'res = r.json()\n' +
          'print(r.status_code, res["allowed"], res["service_roles"])',
      },
    ],
    notes:
      "introspect/service-access закрыты SERVICE_API_KEY — это внутренние ручки, юзер их сам " +
      "не дёргает. Privilege-данные не кешируются в JWT и перечитываются из БД на каждом " +
      "introspect, поэтому отзыв роли, бан и смена департамента вступают в силу мгновенно.",
  },
  {
    id: "flow-refresh-rotation",
    title: "Ротация refresh_token",
    description:
      "Когда access_token истёк, не логинимся заново, а меняем refresh_token на свежую пару. " +
      "Ротация атомарная: старый refresh гасится, в ответе всегда новый — его и сохраняем. " +
      "Повторное предъявление уже использованного refresh трактуется как компрометация и " +
      "убивает всю цепочку токенов (reuse-detection).",
    steps: [
      {
        title: "1. Предъявить refresh_token",
        description:
          "grant_type=refresh_token, передаём refresh_token и client_id (+ client_secret для " +
          "confidential). client_id обязателен — по нему сверяется принадлежность токена клиенту.",
        curl:
          'base_url="{{BASE_URL}}"\n' +
          'client_id="..."\n' +
          'client_secret="..."\n' +
          'refresh_token="<из предыдущего обмена>"\n\n' +
          'curl -X POST "$base_url/api/auth/v1/oauth2/token" \\\n' +
          '  -H "Content-Type: application/json" \\\n' +
          '  -d "{\\"grant_type\\":\\"refresh_token\\",\\"refresh_token\\":\\"$refresh_token\\",\\"client_id\\":\\"$client_id\\",\\"client_secret\\":\\"$client_secret\\"}"',
        python:
          'import requests\n\n' +
          'base_url = "{{BASE_URL}}"\n' +
          'client_id = "..."\n' +
          'client_secret = "..."\n' +
          'refresh_token = "<из предыдущего обмена>"\n\n' +
          'r = requests.post(\n' +
          '    f"{base_url}/api/auth/v1/oauth2/token",\n' +
          '    json={\n' +
          '        "grant_type": "refresh_token",\n' +
          '        "refresh_token": refresh_token,\n' +
          '        "client_id": client_id,\n' +
          '        "client_secret": client_secret,\n' +
          '    },\n' +
          ')\n' +
          'data = r.json()\n' +
          'print(r.status_code, data["access_token"][:24], "...")',
      },
      {
        title: "2. Сохранить новый refresh, выбросить старый",
        description:
          "В ответе и новый access_token, и новый refresh_token. Старый refresh уже невалиден — " +
          "перезаписываем его новым значением. Если по ошибке предъявить старый ещё раз, сервер " +
          "вернёт REFRESH_TOKEN_INVALID и погасит всю цепочку (reuse-detection).",
        curl:
          '# из ответа шага 1 берём оба новых значения\n' +
          'new_access=$(printf %s "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin)[\'access_token\'])")\n' +
          'new_refresh=$(printf %s "$resp" | python3 -c "import sys,json;print(json.load(sys.stdin)[\'refresh_token\'])")\n' +
          'echo "сохрани new_refresh на следующий раз: $new_refresh"',
        python:
          '# data — ответ из шага 1\n' +
          'access_token = data["access_token"]\n' +
          'refresh_token = data["refresh_token"]  # перезаписываем — старый мёртв\n' +
          'print("новый refresh сохранён, старый больше не использовать")',
      },
    ],
    notes:
      "Доступно только клиентам с grant refresh_token. " +
      "REFRESH_TOKEN_INVALID / REFRESH_TOKEN_EXPIRED / REFRESH_TOKEN_RACE — все 401. " +
      "На REFRESH_TOKEN_RACE (параллельная ротация уже прошла) просто повтори с актуальным refresh.",
  },
];
