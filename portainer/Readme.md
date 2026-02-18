# Portainer

Сервис по управлению контейнерами и их отслеживанию

## Автоконфигурация (bootstrap)

Контейнер `allta-portainer` при старте автоматически:
- создаёт администратора (если его ещё нет) из env
- (опционально) создаёт базового пользователя из env
- настраивает OAuth авторизацию (Generic OAuth) под `allta_auth`

Переменные берутся из env-файла, который подключается в `docker-compose.yml` через `env_file`.
По умолчанию это: `${CRED_PATH}/env.portainer` (обычно `/var/allta_services/config/env.portainer`).

Шаблон и требования к паролям смотри в `portainer/env/example/example.env.portainer` (копируется в `${CRED_PATH}` на `./install.sh precond`).

OAuth по умолчанию настраивается на:
- `client_id`: `allta-portainer`
- `client_secret`: читается из файла, смонтированного в контейнер
  `PORTAINER_OAUTH_CLIENT_SECRET_FILE=/run/secrets/oauth_clients/allta-portainer.secret`
- URL’ы allta_auth:
  - `PORTAINER_OAUTH_AUTHORIZATION_URL`
  - `PORTAINER_OAUTH_ACCESS_TOKEN_URL`
  - `PORTAINER_OAUTH_RESOURCE_URL`

## Установка

```bash
./install.sh precond
```

## Запуск

```bash
sudo nano /var/allta_services/config/env.portainer
./install.sh start
```
