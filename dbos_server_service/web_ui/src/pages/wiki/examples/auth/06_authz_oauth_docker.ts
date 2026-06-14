import type { ApiSection } from "../types";

export const AUTHZ_OAUTH_DOCKER: ApiSection = {
  id: "authz-oauth-docker",
  title: "Авторизация, OAuth2 и Docker-registry",
  service: "auth",
  description:
    "Service-to-service introspect и service-access (закрыты SERVICE_API_KEY, " +
    "не user-токеном), OAuth2 client management + authorization-code/client-credentials/" +
    "refresh flow с PKCE, и Docker registry token-auth с per-department конфигом.",
  examples: [
    // ── authorization (service-to-service) ──────────────────────────────────
    {
      id: "authz-introspect",
      title: "Introspect токена (service-to-service)",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/authorization/introspect",
      auth: "SERVICE_API_KEY (Bearer), не user-токен",
      description:
        "Главная точка валидации для клиентских сервисов. Принимает любой токен " +
        "(JWT / PAT / bot), парсит его, перечитывает владельца из БД (is_banned, " +
        "status) и считает effective service_roles с INTERSECT. Чувствительные поля " +
        "берутся не из JWT payload, а из свежего состояния — забаненный юзер с валидной " +
        "подписью вернёт active=false. Закрыт сервисным ключом, а не пользовательским " +
        "Bearer: это внутренняя ручка, чтобы нельзя было brute-force'ить ворованные токены.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'service_key="{{TOKEN}}"  # SERVICE_API_KEY (в dev-стеке dev-introspect-api-key)\n' +
        'subject_token="..."       # токен, который проверяем\n\n' +
        'curl -X POST "$base_url/api/auth/v1/authorization/introspect" \\\n' +
        '  -H "Authorization: Bearer $service_key" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"token\\":\\"$subject_token\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'service_key = "{{TOKEN}}"  # SERVICE_API_KEY, не user-токен\n' +
        'subject_token = "..."       # токен, который проверяем\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/authorization/introspect",\n' +
        '    headers={"Authorization": f"Bearer {service_key}"},\n' +
        '    json={"token": subject_token},  # caller_ip опционален (нужен bot multi-IP детектору)\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, data["active"], data.get("subject_type"), data.get("service_roles"))',
      notes:
        "Ответ: active, subject_type (user/bot/oauth_client), sub, username, department_id, " +
        "platform_role, is_banned, allowed_services, service_roles, groups, exp, must_change_password. " +
        "active=false → токен невалидный/истёкший/отозванный/владелец забанен. " +
        "401 INVALID_SERVICE_TOKEN — нет/неверный SERVICE_API_KEY (в strict-режиме ещё нужен X-Service-Identity).",
    },
    {
      id: "authz-service-access",
      title: "Проверка доступа к сервису",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/authorization/service-access",
      auth: "SERVICE_API_KEY (Bearer)",
      description:
        "Лёгкая обёртка над introspect: отвечает на конкретный вопрос «есть ли у субъекта " +
        "доступ к сервису X» и возвращает его роли именно для этого сервиса. Используется, " +
        "когда не нужен полный introspect-ответ — например server_service проверяет права " +
        "юзера на себя. Закрыт тем же сервисным ключом.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'service_key="{{TOKEN}}"  # SERVICE_API_KEY\n' +
        'subject_token="..."\n\n' +
        'curl -X POST "$base_url/api/auth/v1/authorization/service-access" \\\n' +
        '  -H "Authorization: Bearer $service_key" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"subject_token\\":\\"$subject_token\\",\\"service_name\\":\\"server_service\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'service_key = "{{TOKEN}}"  # SERVICE_API_KEY\n' +
        'subject_token = "..."\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/authorization/service-access",\n' +
        '    headers={"Authorization": f"Bearer {service_key}"},\n' +
        '    json={"subject_token": subject_token, "service_name": "server_service"},\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, data["allowed"], data["service_roles"])',
      notes:
        "Ответ: allowed (bool), department_id, service_roles (список ролей для запрошенного сервиса). " +
        "allowed=false, если департамент субъекта не подключён к сервису или у него нет ролей.",
    },

    // ── oauth2 ───────────────────────────────────────────────────────────────
    {
      id: "oauth-create-client",
      title: "Создать OAuth2-клиента (client_secret один раз)",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/oauth2/clients",
      auth: "Bearer (account_admin или department_admin своего отдела)",
      description:
        "Регистрирует OAuth2-клиента и генерит client_secret. Plaintext secret приходит " +
        "ровно один раз в ответе на создание — сохрани его сразу, в БД лежит только хэш. " +
        "Все redirect_uris обязаны быть https (или http://localhost для native/CLI). " +
        "grant_types — любая комбинация authorization_code / client_credentials / refresh_token. " +
        "Для is_public=true (SPA/CLI) секрет не выдаётся (client_secret=null) и PKCE с S256 обязателен.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'dep_id="dep_..."  # отдел, к которому привязываем клиента\n\n' +
        'curl -X POST "$base_url/api/auth/v1/oauth2/clients" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\n' +
        '    \\"name\\": \\"my-app\\",\n' +
        '    \\"department_id\\": \\"$dep_id\\",\n' +
        '    \\"redirect_uris\\": [\\"https://app.example.com/callback\\"],\n' +
        '    \\"allowed_scopes\\": [\\"server.read\\", \\"server.write\\"],\n' +
        '    \\"grant_types\\": [\\"authorization_code\\", \\"client_credentials\\", \\"refresh_token\\"],\n' +
        '    \\"is_public\\": false\n' +
        '  }"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'dep_id = "dep_..."  # отдел, к которому привязываем клиента\n\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/oauth2/clients",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        '    json={\n' +
        '        "name": "my-app",\n' +
        '        "department_id": dep_id,\n' +
        '        "redirect_uris": ["https://app.example.com/callback"],\n' +
        '        "allowed_scopes": ["server.read", "server.write"],\n' +
        '        "grant_types": ["authorization_code", "client_credentials", "refresh_token"],\n' +
        '        "is_public": False,\n' +
        '    },\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, data["client_id"])\n' +
        'client_secret = data["client_secret"]  # показывается ОДИН раз — сохрани',
      notes:
        "201. Ответ: id, client_id, client_secret (plaintext, один раз), department_id, name, " +
        "redirect_uris, allowed_scopes, grant_types, is_active, is_public, created_at. " +
        "422: grant_types пустой; authorization_code без redirect_uris; redirect_uri не https/с fragment.",
    },
    {
      id: "oauth-list-clients",
      title: "Список OAuth2-клиентов",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/oauth2/clients?department_id=dep_...",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Список зарегистрированных клиентов с учётом scope смотрящего: account_admin видит все, " +
        "department_admin — только своего отдела. Опциональный фильтр department_id. " +
        "client_secret в списке не возвращается (он есть только в ответе на создание).",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n\n' +
        'curl "$base_url/api/auth/v1/oauth2/clients?department_id=dep_..." \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/auth/v1/oauth2/clients",\n' +
        '    params={"department_id": "dep_..."},  # опционально\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, [c["client_id"] for c in r.json()])',
      notes:
        "Удалить клиента — DELETE /api/auth/v1/oauth2/clients/{id} (soft-delete, is_active=false). " +
        "Выпущенные коды и токены не удаляются мгновенно — отбиваются на introspect по is_active и TTL.",
    },
    {
      id: "oauth-authorize",
      title: "Authorize endpoint (302 redirect, PKCE)",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/oauth2/authorize",
      auth: "Bearer (любой залогиненный юзер)",
      description:
        "Старт authorization-code flow. Выдаёт одноразовый authorization code (TTL 5 мин) и " +
        "отвечает 302-редиректом на redirect_uri?code=...&state=.... response_type только code " +
        "(implicit и hybrid сознательно запрещены). PKCE (code_challenge + S256) опционален для " +
        "confidential, обязателен для public клиентов. state echo'ится байт-в-байт. " +
        "Полную раскадровку flow с PKCE-обменом см. на вкладке «Сценарии».",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'client_id="..."\n\n' +
        '# PKCE: challenge = base64url(sha256(verifier)) без паддинга\n' +
        'verifier=$(openssl rand -base64 32 | tr \'+/\' \'-_\' | tr -d \'=\')\n' +
        'challenge=$(printf %s "$verifier" | openssl dgst -binary -sha256 | openssl base64 | tr \'+/\' \'-_\' | tr -d \'=\')\n\n' +
        '# -i показывает заголовки; Location несёт ?code=...&state=...\n' +
        'curl -i -G "$base_url/api/auth/v1/oauth2/authorize" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  --data-urlencode "client_id=$client_id" \\\n' +
        '  --data-urlencode "redirect_uri=https://app.example.com/callback" \\\n' +
        '  --data-urlencode "scope=server.read server.write" \\\n' +
        '  --data-urlencode "state=xyz123" \\\n' +
        '  --data-urlencode "response_type=code" \\\n' +
        '  --data-urlencode "code_challenge=$challenge" \\\n' +
        '  --data-urlencode "code_challenge_method=S256"',
      python:
        'import requests, hashlib, base64, secrets\n' +
        'from urllib.parse import urlparse, parse_qs\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'client_id = "..."\n\n' +
        '# PKCE: challenge = base64url(sha256(verifier)) без паддинга\n' +
        'verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()\n' +
        'challenge = base64.urlsafe_b64encode(\n' +
        '    hashlib.sha256(verifier.encode()).digest()\n' +
        ').rstrip(b"=").decode()\n\n' +
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
        '    allow_redirects=False,  # сами читаем Location\n' +
        ')\n' +
        'qs = parse_qs(urlparse(r.headers["location"]).query)\n' +
        'code = qs["code"][0]\n' +
        'print(r.status_code, code, qs.get("state"))  # 302',
      notes:
        "302 → Location: redirect_uri?code=...&state=.... " +
        "400 UNSUPPORTED_RESPONSE_TYPE (не code); 401 OAUTH_CLIENT_INVALID; " +
        "403 REDIRECT_URI_MISMATCH / GRANT_TYPE_NOT_ALLOWED / PKCE_REQUIRED / PKCE_METHOD_INVALID. " +
        "m2m-JWT (actor_type=oauth_client) сюда не пускают — нужен живой пользователь.",
    },
    {
      id: "oauth-token",
      title: "Token endpoint (три grant_type)",
      method: "POST",
      path: "{{BASE_URL}}/api/auth/v1/oauth2/token",
      auth: "client_id + client_secret (для confidential)",
      description:
        "Один endpoint на три grant_type. authorization_code: меняет одноразовый code (+ PKCE " +
        "code_verifier) на access_token и refresh_token. refresh_token: атомарная ротация — " +
        "старый refresh гасится, в ответе всегда новый; повторное предъявление старого убивает " +
        "всю цепочку. client_credentials: m2m по client_secret без участия пользователя, " +
        "refresh_token не выдаётся.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'client_id="..."\n' +
        'client_secret="..."\n\n' +
        '# A) authorization_code (+ PKCE verifier)\n' +
        'curl -X POST "$base_url/api/auth/v1/oauth2/token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"grant_type\\":\\"authorization_code\\",\\"code\\":\\"<code>\\",\\"redirect_uri\\":\\"https://app.example.com/callback\\",\\"client_id\\":\\"$client_id\\",\\"client_secret\\":\\"$client_secret\\",\\"code_verifier\\":\\"<verifier>\\"}"\n\n' +
        '# B) refresh_token (ротация)\n' +
        'curl -X POST "$base_url/api/auth/v1/oauth2/token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"grant_type\\":\\"refresh_token\\",\\"refresh_token\\":\\"<refresh>\\",\\"client_id\\":\\"$client_id\\",\\"client_secret\\":\\"$client_secret\\"}"\n\n' +
        '# C) client_credentials (m2m)\n' +
        'curl -X POST "$base_url/api/auth/v1/oauth2/token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"grant_type\\":\\"client_credentials\\",\\"client_id\\":\\"$client_id\\",\\"client_secret\\":\\"$client_secret\\"}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'client_id = "..."\n' +
        'client_secret = "..."\n\n' +
        '# A) authorization_code (+ PKCE verifier из шага /authorize)\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/oauth2/token",\n' +
        '    json={\n' +
        '        "grant_type": "authorization_code",\n' +
        '        "code": "<code>",\n' +
        '        "redirect_uri": "https://app.example.com/callback",\n' +
        '        "client_id": client_id,\n' +
        '        "client_secret": client_secret,\n' +
        '        "code_verifier": "<verifier>",\n' +
        '    },\n' +
        ')\n' +
        'tok = r.json()\n' +
        'print(r.status_code, tok["access_token"], tok.get("refresh_token"))\n\n' +
        '# B) refresh_token (ротация — сохрани новый refresh)\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/oauth2/token",\n' +
        '    json={\n' +
        '        "grant_type": "refresh_token",\n' +
        '        "refresh_token": tok["refresh_token"],\n' +
        '        "client_id": client_id,\n' +
        '        "client_secret": client_secret,\n' +
        '    },\n' +
        ')\n' +
        'print(r.status_code, r.json()["refresh_token"])  # новый refresh\n\n' +
        '# C) client_credentials (m2m, без refresh_token)\n' +
        'r = requests.post(\n' +
        '    f"{base_url}/api/auth/v1/oauth2/token",\n' +
        '    json={\n' +
        '        "grant_type": "client_credentials",\n' +
        '        "client_id": client_id,\n' +
        '        "client_secret": client_secret,\n' +
        '    },\n' +
        ')\n' +
        'print(r.status_code, r.json()["access_token"])',
      notes:
        "200: access_token, token_type=Bearer, expires_in, scope, refresh_token (null для client_credentials). " +
        "INVALID_GRANT (code не найден/истёк/использован); REFRESH_TOKEN_INVALID/EXPIRED/RACE; " +
        "401 OAUTH_CLIENT_INVALID (неверный client_id/secret); 403 GRANT_TYPE_NOT_ALLOWED; UNSUPPORTED_GRANT_TYPE.",
    },

    // ── docker registry ──────────────────────────────────────────────────────
    {
      id: "docker-token",
      title: "Docker registry token (Basic → scoped JWT)",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/docker/token",
      auth: "Basic (username:password или botname:dbos_bot_...)",
      description:
        "Реализация Docker token-auth protocol: docker login/pull/push ходит сюда автоматически. " +
        "Принимает Basic-кредлы (пароль юзера или bot-токен как пароль), валидирует их через тот " +
        "же lockout-pipeline, что и /login, и при успехе отдаёт RS256-подписанный JWT со scope " +
        "repository:<name>:pull/push. Пароль идёт открыто по TLS (Basic) — это штатный путь, " +
        "base64 в Basic — транспортная кодировка, не шифрование. Анонимный pull разрешён, если " +
        "registry с pull_policy=all и scope чисто pull.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'dep_id="dep_..."\n\n' +
        '# Basic admin:1234 (или botname:dbos_bot_... для bot-канала)\n' +
        'curl -G "$base_url/api/auth/v1/docker/token" \\\n' +
        '  -u "admin:1234" \\\n' +
        '  --data-urlencode "service=registry.example.com" \\\n' +
        '  --data-urlencode "scope=repository:$dep_id/myimage:pull,push"',
      python:
        'import requests\n' +
        'from requests.auth import HTTPBasicAuth\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'dep_id = "dep_..."\n\n' +
        '# Basic admin:1234 (или (botname, "dbos_bot_...") для bot-канала)\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/auth/v1/docker/token",\n' +
        '    auth=HTTPBasicAuth("admin", "1234"),\n' +
        '    params={\n' +
        '        "service": "registry.example.com",\n' +
        '        "scope": f"repository:{dep_id}/myimage:pull,push",\n' +
        '    },\n' +
        ')\n' +
        'data = r.json()\n' +
        'print(r.status_code, data["expires_in"], data["issued_at"])\n' +
        'jwt = data["token"]  # == data["access_token"], Docker принимает оба имени',
      notes:
        "200: token, access_token (то же значение), expires_in (~5 мин), issued_at (ISO). " +
        "401 MISSING_CREDENTIALS/INVALID_CREDENTIALS; 429 ACCOUNT_TEMPORARILY_LOCKED (5 неудач → 15 мин); " +
        "403 PUSH_DEPT_MISMATCH (push в чужой отдел). Штатный PAT для docker непригоден " +
        "(403 PAT_SCOPE_DENIES_DOCKER) — используй пароль юзера или bot-токен.",
    },
    {
      id: "docker-config-put",
      title: "Создать / заменить Docker registry конфиг отдела",
      method: "PUT",
      path: "{{BASE_URL}}/api/auth/v1/docker/registry/{department_id}",
      auth: "Bearer (account_admin или department_admin своего отдела)",
      description:
        "PUT с replace-семантикой: включает registry для отдела и перезаписывает весь конфиг. " +
        "pull_policy=all — pull открыт всей платформе и анонимам; restricted — только из " +
        "pull_user_ids. Push всегда ограничен push_user_ids. После этого юзеры отдела смогут " +
        "получать scoped JWT через /docker/token.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'dep_id="dep_..."\n\n' +
        'curl -X PUT "$base_url/api/auth/v1/docker/registry/$dep_id" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"pull_policy\\":\\"all\\",\\"pull_user_ids\\":[],\\"push_user_ids\\":[\\"usr_...\\"]}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'dep_id = "dep_..."\n\n' +
        'r = requests.put(\n' +
        '    f"{base_url}/api/auth/v1/docker/registry/{dep_id}",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        '    json={\n' +
        '        "pull_policy": "all",       # или "restricted" + pull_user_ids\n' +
        '        "pull_user_ids": [],\n' +
        '        "push_user_ids": ["usr_..."],  # whitelist для push\n' +
        '    },\n' +
        ')\n' +
        'print(r.status_code, r.json()["is_enabled"])',
      notes:
        "200: department_id, is_enabled, pull_policy, pull_user_ids, push_user_ids, created_at, updated_at. " +
        "При restricted pull_user_ids обязателен. department_admin может править только свой отдел.",
    },
    {
      id: "docker-config-get",
      title: "Получить Docker registry конфиг отдела",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/docker/registry/{department_id}",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Текущий конфиг registry для отдела: включён ли, политика pull, whitelist'ы pull/push.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'dep_id="dep_..."\n\n' +
        'curl "$base_url/api/auth/v1/docker/registry/$dep_id" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'dep_id = "dep_..."\n\n' +
        'r = requests.get(\n' +
        '    f"{base_url}/api/auth/v1/docker/registry/{dep_id}",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, r.json())',
      notes: "Тело идентично ответу PUT. Если registry для отдела не настраивался — 404.",
    },
    {
      id: "docker-config-patch",
      title: "Обновить Docker registry конфиг (частично)",
      method: "PATCH",
      path: "{{BASE_URL}}/api/auth/v1/docker/registry/{department_id}",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "PATCH — частичный update, передавай только меняющиеся поля. Можно переключить " +
        "pull_policy, обновить whitelist'ы или включить/выключить registry через is_enabled.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'dep_id="dep_..."\n\n' +
        'curl -X PATCH "$base_url/api/auth/v1/docker/registry/$dep_id" \\\n' +
        '  -H "Authorization: Bearer $token" \\\n' +
        '  -H "Content-Type: application/json" \\\n' +
        '  -d "{\\"pull_policy\\":\\"restricted\\",\\"pull_user_ids\\":[\\"usr_...\\"]}"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'dep_id = "dep_..."\n\n' +
        'r = requests.patch(\n' +
        '    f"{base_url}/api/auth/v1/docker/registry/{dep_id}",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        '    json={"pull_policy": "restricted", "pull_user_ids": ["usr_..."]},\n' +
        ')\n' +
        'print(r.status_code, r.json()["pull_policy"])',
      notes:
        "Поля PATCH: pull_policy, pull_user_ids, push_user_ids, is_enabled — все опциональны. " +
        "is_enabled=false временно гасит выдачу токенов, не теряя конфиг.",
    },
    {
      id: "docker-config-delete",
      title: "Отключить Docker registry для отдела",
      method: "DELETE",
      path: "{{BASE_URL}}/api/auth/v1/docker/registry/{department_id}",
      auth: "Bearer (account_admin / department_admin)",
      description:
        "Soft-disable: ставит is_enabled=false, сама запись конфига остаётся (whitelist'ы " +
        "сохраняются). Юзеры отдела перестанут получать scoped JWT. Включить обратно — PATCH " +
        "is_enabled=true или PUT.",
      curl:
        'base_url="{{BASE_URL}}"\n' +
        'token="{{TOKEN}}"\n' +
        'dep_id="dep_..."\n\n' +
        'curl -X DELETE "$base_url/api/auth/v1/docker/registry/$dep_id" \\\n' +
        '  -H "Authorization: Bearer $token"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n' +
        'token = "{{TOKEN}}"\n' +
        'dep_id = "dep_..."\n\n' +
        'r = requests.delete(\n' +
        '    f"{base_url}/api/auth/v1/docker/registry/{dep_id}",\n' +
        '    headers={"Authorization": f"Bearer {token}"},\n' +
        ')\n' +
        'print(r.status_code, r.json())  # {"ok": true}',
      notes: "200 {\"ok\": true}. Запись не удаляется физически — это деактивация, не drop.",
    },
    {
      id: "docker-certs",
      title: "RSA public key (PEM) для registry rootcertbundle",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/docker/certs",
      auth: "Публично",
      description:
        "Отдаёт self-signed X.509 cert в PEM из той же RSA-пары, которой подписываются " +
        "docker-токены. Docker registry требует именно certificate (не голый public key) в " +
        "auth.token.rootcertbundle, чтобы извлечь ключ и проверять JWT. Сохрани в файл и пропиши " +
        "путь в config registry.",
      curl:
        'base_url="{{BASE_URL}}"\n\n' +
        'curl "$base_url/api/auth/v1/docker/certs" -o registry-auth.pem\n' +
        '# затем в registry config.yml: auth.token.rootcertbundle: /path/registry-auth.pem',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n\n' +
        'r = requests.get(f"{base_url}/api/auth/v1/docker/certs")\n' +
        'print(r.status_code)\n' +
        'with open("registry-auth.pem", "w") as f:\n' +
        '    f.write(r.text)  # -----BEGIN CERTIFICATE-----',
      notes:
        "Content-Type application/x-pem-file, тело начинается с -----BEGIN CERTIFICATE-----. " +
        "Для JWT-aware инструментов есть JWKS-форма — /api/auth/v1/docker/jwks.",
    },
    {
      id: "docker-jwks",
      title: "JWKS — public keys для верификации JWT",
      method: "GET",
      path: "{{BASE_URL}}/api/auth/v1/docker/jwks",
      auth: "Публично",
      description:
        "Стандартный JWKS (RFC 7517) с тем же RSA public key в JSON-форме — для инструментов, " +
        "которые валидируют docker-JWT по JWKS, а не по PEM-сертификату.",
      curl:
        'base_url="{{BASE_URL}}"\n\n' +
        'curl "$base_url/api/auth/v1/docker/jwks"',
      python:
        'import requests\n\n' +
        'base_url = "{{BASE_URL}}"\n\n' +
        'r = requests.get(f"{base_url}/api/auth/v1/docker/jwks")\n' +
        'print(r.status_code, [k["kid"] for k in r.json()["keys"]])',
      notes:
        'Ответ: {"keys": [{kty: "RSA", kid, use: "sig", alg: "RS256", n, e}]}. ' +
        "Тот же ключ, что в /docker/certs, только в JWK-формате.",
    },
  ],
};
