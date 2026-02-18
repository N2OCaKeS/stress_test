# Allta auth

Централизованный сервис для авторизации в allta

## Информация

### Открытые порты

Иные порты кроме как открых закрыты и доступ к сервисам осуществляется только через внутренние сети docker. Многие контейнеры разделены на различные сети для исключения конфликтов.

| Порт  | Сервис | Для чего нужен |
| :---: | :----: | -------------- |
| 21500 | nginx  | Прокси для api |

### Сервисы входящие в состав

|    Сервис    |                   Описание                   |                                         Точка входа                                          |                                      Документация                                      |
| :----------: | :------------------------------------------: | :------------------------------------------------------------------------------------------: | :------------------------------------------------------------------------------------: |
|  ALLTA Auth  |        Единый сервис для авторизации         |   [http://allta.devos.astralinux.ru:21500/api/auth/](http://allta.devos.astralinux.ru/api/auth/)   | [http://allta.devos.astralinux.ru/api/docs](http://allta.devos.astralinux.ru/api/docs) |
| ALLTA Config | Сервис для получения конфигурационных файлов | [http://allta.devos.astralinux.ru:21500/api/config/](http://allta.devos.astralinux.ru/api/config/) | [http://allta.devos.astralinux.ru/api/docs](http://allta.devos.astralinux.ru/api/docs) |



## Установка

1. Запустить скрипт:

    ```bash
    mkdir folder_git_for_allta_auth # Можно использовать любое название данное используется как пример
    cd folder_git_for_allta_auth
    git clone ssh://git@git.astralinux.ru:7999/qa/stress_test.git
    git checkout allta_auth
    ```

2. Необходимо запустить скрипт установки зависимостей и генерации конфигураций

    ```bash
    sudo ./install.sh precond
    ```

3. Необходимо настроить креды, для этого надо выполниться следующее

    ```bash
    cd /var/allta_services/config
    nano env.allta_auth_db
    nano env.allta_auth_api
    nano env.allta_config_api
    ```

## Интеграции

### Docker Registry

- Token endpoint: `/api/auth/v1/integrations/registry/token`
- Политика доступа:
  - анонимные пользователи могут получать `pull` scope (если `REGISTRY_ALLOW_ANON_PULL=true`);
  - `push/delete` scope доступны пользователям с правом `docker`
    (по умолчанию это администраторы).
- В docker-compose добавлен init-сервис `authservice-auth-init`, который перед запуском `authservice-auth-api`
  создаёт ключ подписи и сертификат для Registry в `${REGISTRY_KEYS_PATH}` (по умолчанию `/var/allta_services/secrets`).
- Для Auth API укажите `REGISTRY_TOKEN_PRIVATE_KEY_PATH=/run/secrets/registry/registry_signing.key`.

Пример конфигурации `distribution/registry`:

```yaml
auth:
  token:
    realm: "https://<HOST>/api/auth/v1/integrations/registry/token"
    service: "registry.example.com"
    issuer: "allta-auth"
    # для RS256/ES256: путь к public cert bundle
    # rootcertbundle: /certs/auth-registry-ca.pem
```

### OAuth и интеграции сервисов

- Проверка bearer/cookie токена: `GET /api/auth/v1/integrations/whoami`
- Проверка Basic/Bearer для reverse-proxy интеграций: `GET /api/auth/v1/integrations/basic/verify`
- Общий OAuth2 для любых сервисов:
  - Authorization URL: `GET /api/auth/v1/integrations/oauth/authorize`
  - Access Token URL: `POST /api/auth/v1/integrations/oauth/token`
  - UserInfo URL: `GET /api/auth/v1/integrations/oauth/userinfo`
  - `authorize` возвращает встроенную страницу входа в черно-оранжевой теме, заголовок берется из `display_name` OAuth-клиента.
  - Поддерживаются client auth варианты `client_secret_basic` и `client_secret_post`.
- OAuth-клиенты управляются из БД:
  - `GET/POST /api/auth/v1/admin/oauth/clients/`
  - `PATCH/DELETE /api/auth/v1/admin/oauth/clients/{oauth_client_id}`
- Для каждого OAuth-клиента можно задать:
  - список разрешенных `redirect_uri_prefixes`
  - `required_permission` (если задано, пользователь без этого права не пройдет логин)
  - `display_name` для заголовка страницы входа
- При инициализации автоматически создаются клиенты:
  - `allta-redis` (право `redis.commander`)
  - `allta-docs` (право `docs.api`)
  - `allta-flower` (право `flower`)
  - `allta-docker-ui` (право `docker`, URL `allta.devos.astralinux.ru:21502`)
  - `allta-devpi` (право `devpi`, URL `allta.devos.astralinux.ru:3141`)
- Секреты OAuth-клиентов генерируются init-контейнером автоматически и сохраняются:
  - `${REGISTRY_KEYS_PATH}/oauth_clients/<client_id>.secret`
  - `${REGISTRY_KEYS_PATH}/oauth_clients/clients.env`
  Эти файлы можно монтировать в другие compose-проекты как `read_only`.
- Проверка доступа для управления инфраструктурой:
  - `GET /api/auth/v1/integrations/basic/verify?permission=server.manage`
  - `GET /api/auth/v1/integrations/basic/verify?permission=vm.manage`
- В ответе `basic/verify` дополнительно выставляются заголовки:
  - `X-Auth-User`
  - `X-Auth-User-Id`
  - `X-Auth-Role`

Пример настроек для любого OAuth-сервиса:
- `Client ID`: из записи OAuth-клиента в БД
- `Client Secret`: из `clients.env` или `<client_id>.secret`
- `Authorization URL`: `https://allta.devos.astralinux.ru:21500/api/auth/v1/integrations/oauth/authorize`
- `Access Token URL`: `https://allta.devos.astralinux.ru:21500/api/auth/v1/integrations/oauth/token`
- `Resource URL`: `https://allta.devos.astralinux.ru:21500/api/auth/v1/integrations/oauth/userinfo`
- `User identifier`: `preferred_username` (или `login`)
- `Scopes`: `profile`

Для Flower/Redis Commander за Nginx рекомендуется схема `nginx + oauth2-proxy`.
Готовые шаблоны:
- `allta_auth/docker_auth/allta_nginx/template/services/30-flower-oauth2-proxy.conf.example`
- `allta_auth/docker_auth/allta_nginx/template/services/31-redis-commander-oauth2-proxy.conf.example`

### DevPI

- Проверка прав на операции: `GET /api/auth/v1/integrations/devpi/authorize?action=read|write|upload|delete`
- При `DEVPI_WRITE_REQUIRES_ADMIN=true` write-операции доступны только пользователям с правом `devpi`.

### Config API tokens

- Для `GET /api/config/v1/config/tokens` требуется право `config.tokens`.
- В `allta_config_api` это настраивается через `TOKENS_READ_PERMISSION` (по умолчанию `config.tokens`).

## RBAC

В сервисе добавлены таблицы:
- `permissions` (права, например: `docker`, `portainer`, `devpi`, `config.tokens`, `server.manage`, `vm.manage`, `flower`, `redis.commander`, `docs.api`)
- `roles` и `role_permissions`
- `groups`, `group_permissions`, `user_groups`
- `oauth_clients` (OAuth-клиенты интеграций)

По умолчанию:
- роль `admin` имеет все права
- роль `user` имеет право `portainer`
- роль `guest` не имеет прав
- каждый новый пользователь автоматически добавляется в группу `guest`
- группа `infra_managers` даёт права `server.manage` и `vm.manage`
- legacy поле `is_admin` удалено, используйте `role` и права

Админские endpoint’ы для управления правами:
- `GET/POST /api/auth/v1/admin/access/permissions`
- `GET/POST/PATCH /api/auth/v1/admin/access/roles`
- `POST/DELETE /api/auth/v1/admin/access/roles/{role_id}/permissions/{permission_id}`
- `GET/POST/PATCH /api/auth/v1/admin/access/groups`
- `POST/DELETE /api/auth/v1/admin/access/groups/{group_id}/permissions/{permission_id}`
- `POST/DELETE /api/auth/v1/admin/access/groups/{group_id}/users/{user_id}`
- `GET/POST/PATCH/DELETE /api/auth/v1/admin/oauth/clients/*`

## Совместимость

Существующие endpoints `/api/auth/login`, `/api/auth/logout`, `/api/auth/verify`, `/api/auth/v1/user/*`, `/api/auth/v1/admin/*` сохранены.
Старые `.../integrations/portainer/oauth/*` удалены, используйте `.../integrations/oauth/*`.
