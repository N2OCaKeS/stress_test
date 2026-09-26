# DBOS Server Manager · деплой в k3s на одной VM

Этот каталог содержит k8s-манифесты для single-node k3s-деплоя на Astra Linux SE.
Сценарий: одна VM, она же master и worker. k3s даёт self-healing, rolling updates и откаты.

> **Чек-листы** (pre-/post-deploy, smoke) — `obsidian/infra/runbooks/Production-checklist.md`.
> **Ротация ключей и паролей** — `obsidian/infra/runbooks/Rotation.md`.
> **Backup и DR** — `obsidian/infra/runbooks/Backup-DR.md`.
> **Incident response** — `obsidian/infra/runbooks/Incident-response.md`.
> Этот README остаётся как краткая справка про конкретные манифесты и устройство каталога.

## TL;DR

```bash
make k8s-install-k3s                 # один раз, на чистой VM (sudo)
make k8s-install-cert-manager        # один раз, после k3s
make k8s-secrets DOMAIN=dbos.local   # один раз: 20-secrets.yaml + 21-keystore-secrets.yaml + 50-ingress.yaml
                                     # (для closed-net можно DOMAIN=10.177.103.102)
make deploy                          # = prod-deploy: build → secrets-check → deploy+keystore → migrate → smoke
```

`make deploy` (алиас `prod-deploy`) — одна идемпотентная команда, поднимающая
всё в правильном порядке:

1. **build** — собрать и импортировать 5 образов в k3s.
2. **secrets-check** — убедиться, что `20-secrets.yaml` / `50-ingress.yaml` на месте.
3. **deploy** (`scripts/k8s/deploy.sh`): namespace → **create-only bootstrap
   keystore-Secret'ов** (`dbos-server-encryption-keys` / `dbos-secret-encryption-keys`)
   → `kubectl apply -k` (postgres/redis/RBAC/сервисы) → ожидание готовности всех
   БД и Deployment'ов.
4. **migrate** (`make k8s-migrate`) — `alembic upgrade head` во всех 5 сервисах
   (auth/logging/server/worker/secret) через `kubectl exec`.
5. **openapi dump** + **smoke**.

Полностью с нуля (включая установку docker/k3s/cert-manager) — `make k8s-zero`.

### Durable keystore шифрования

`server_service` и `secret_service` в prod крутятся с `KEYSTORE_BACKEND=k8s`:
master-материал шифрования живёт не в env, а в отдельных k8s Secret'ах
`dbos-server-encryption-keys` / `dbos-secret-encryption-keys` (namespace `dbos`).
Поля совпадают с `K8sSecretKeyStore` (`*/src/core/keystore.py`):
`active_version` + `key_v<N>`. RBAC (`14-keystore-rbac.yaml`) даёт каждому
сервису `get`/`patch` строго на свой Secret.

- **Bootstrap.** `gen_secrets.sh` пишет `21-keystore-secrets.yaml` (gitignored,
  `chmod 600`), засевая начальную активную версию из тех же
  `SERVER_ENCRYPTION_KEY` / `SECRET_ENCRYPTION_KEY`, что и в `20-secrets.yaml`.
  `deploy.sh` применяет их **create-only** (`kubectl create`, не `apply`) ДО
  старта сервисов. Живой keystore с уже ротированными ключами **никогда не
  перетирается** — повторный деплой идемпотентен.
- **Миграция действующего деплоя на keystore** (когда `20-secrets.yaml` уже
  есть, а keystore-файла нет): `bash scripts/k8s/gen_secrets.sh --keystore-only`
  — сгенерирует `21-keystore-secrets.yaml` из существующих ключей без
  регенерации остальных секретов, затем `make k8s-deploy`.
- **Ротация.** `scripts/k8s/rotate_master_key.sh` (server) и
  `scripts/k8s/rotate_secret_master_key.sh` (secret) патчат keystore-Secret
  напрямую: `key_v<new>` + бамп `active_version` (рестарт не нужен — keystore
  читается вживую). `--finalize` выводит старые версии после
  `migration_status: complete`.
- **Backup/restore.** `backup_master_keys.sh` кладёт оба keystore-Secret'а
  целиком в `keystores/` архива; `restore_master_keys.sh --apply` подкатывает их
  обратно.

### Миграции: один механизм

Alembic-миграции гоняются **только** через `make k8s-migrate`
(`kubectl exec … alembic upgrade head`, по одному pod'у на сервис). Декларативные
Job'ы из `45-migrations.yaml` **не подключены** в `kustomization.yaml`: Job'ы
immutable и ломают повторный `apply -k`. Readiness сервисов — `SELECT 1`
(схема-независим), поэтому pod'ы доходят до Ready без схемы, а exec-миграция
отрабатывает следом, до smoke. `45-migrations.yaml` оставлен как альтернатива
под Helm/Argo PreSync-hook.

## Архитектура

```text
              VM (Astra Linux SE) + k3s
              ┌─────────────────────────────────────────────────────┐
              │  namespace: dbos                                    │
              │                                                     │
              │  ┌auth-pg┐ ┌log-pg┐ ┌srv-pg┐ ┌wrk-pg┐ ┌secret-pg┐   │
              │  │ Dpl×1 │ │ Dpl×1│ │ Dpl×1│ │ Dpl×1│ │ Dpl×1   │   │
              │  │ PVC   │ │ PVC  │ │ PVC  │ │ PVC  │ │ PVC     │   │
              │  └───┬───┘ └───┬──┘ └───┬──┘ └───┬──┘ └───┬─────┘   │
              │      │         │        │        │        │         │
              │  ┌auth-svc┐ ┌log-svc┐ ┌srv-svc┐ ┌worker┐ ┌sec-svc┐  │
              │  │ Dpl×2  │ │ Dpl×2 │ │ Dpl×2 │ │taskiq│ │ Dpl×2 │  │
              │  │ :8000  │ │ :8001 │ │ :8002 │ │ pod  │ │ :8003 │  │
              │  └───┬────┘ └──┬────┘ └──┬────┘ └──────┘ └──┬────┘  │
              │      │         │         │                  │       │
              │   /api/auth /api/logging /api/server   /api/secret  │
              │      └─────────┴─────────┴───────┬──────────┘       │
              │                                  ▼                  │
              │                          Traefik (Ingress)          │
              │                          :80 → 301 → :443           │
              │                          :443 + TLS (dbos-ingress-tls)
              └──────────────────────────────────┬──────────────────┘
                                                 ▼
              https://<host>/api/auth/...     (auth_service)
              https://<host>/api/logging/...  (loging_service)
              https://<host>/api/server/...   (server_service)
              https://<host>/api/secret/...   (secret_service)
              https://<host>/docs             (объединённый Swagger UI)
```

`<host>` — это либо DNS-имя (`dbos.example.com`), либо IP (`10.177.103.102`),
которое оператор передаёт в `make k8s-secrets DOMAIN=...`. Все API крутятся за
одним Ingress'ом на одном хосте; разделение — по path-prefix'у.

## TLS-стратегия: self-signed на Ingress

Платформа разворачивается в closed network — Let's Encrypt и ACME недоступны.
Edge-TLS на Ingress — **один** self-signed сертификат для всего платформенного
хоста, который кладёт `scripts/k8s/gen_secrets.sh` в Secret `dbos-ingress-tls`.
Traefik (k3s) подхватывает его автоматически.

```
   scripts/k8s/gen_secrets.sh
            │
            │  генерирует self-signed cert (5 лет, CN=<host>,
            │  SAN: DNS:<host> или IP:<addr>, + опц. extra SAN'ы)
            ▼
   Secret dbos-ingress-tls (kubernetes.io/tls)
            │
            ▼
   Ingress dbos-ingress (Traefik)
   - tls.secretName: dbos-ingress-tls
   - path /api/auth    → auth-service:8000
   - path /api/logging → logging-service:8001
   - path /api/server  → server-service:8002
   - path /api/secret  → secret-service:8003
   - path /docs        → dbos-swagger-ui:8080
```

**cert-manager в edge-цепочке не используется.** Issuer'ы и leaf-сертификаты,
которые могли быть в репо ранее (`auth-service-tls`, `logging-service-tls`,
`server-service-tls`), к Ingress'у не подключены — Traefik читает только
`dbos-ingress-tls`. См. также раздел «Авто-ротация сертификатов» ниже.

**Service-to-service TLS** — между подами не используется (NetworkPolicy
default-deny в namespace, plain http между ClusterIP-сервисами). Cluster-internal
вызовы (auth ↔ logging, server ↔ secret и т.п.) идут по http://*.dbos.svc.cluster.local.

**Ротация edge-сертификата** — `scripts/k8s/gen_secrets.sh` при повторном запуске
перевыпускает `dbos-ingress-tls`. Cadence/процедура — `obsidian/infra/runbooks/Rotation.md`.
Срок жизни сертификата 5 лет; ротация по compliance — отдельная задача.

## Что делает k3s за вас

- **Балансировка** — Service распределяет трафик между 2 репликами каждого сервиса.
- **Self-healing** — упавший pod автоматически пересоздаётся.
- **Rolling update** — `kubectl rollout restart deploy/<x>` запускает новые pods, ждёт их readiness, потом убивает старые. `maxUnavailable: 0` гарантирует, что в каждый момент хотя бы 1 реплика отвечает.
- **Rollback** — `kubectl rollout undo deploy/<x>` мгновенно возвращает предыдущую версию.

## Полный путь от пустой VM до работающего приложения

### 1. Установка k3s (один раз)

На VM, под root:

```bash
git clone <repo>
cd dbos_server_service
make k8s-install
```

Скрипт:

- Отключает swap
- Загружает `br_netfilter`, `overlay`
- Настраивает sysctl
- Открывает порты в ufw (если активен): 6443 (k3s API), 80/443 (Traefik)
- Ставит k3s со встроенным Traefik
- Кладёт kubeconfig в `/etc/rancher/k3s/k3s.yaml`

После установки `kubectl get nodes` должен показать `Ready`.

### 2. Генерация prod-секретов (один раз)

```bash
make k8s-secrets                              # спросит домен интерактивно
# или сразу:
bash scripts/k8s/gen_secrets.sh dbos.example.com

# Closed network без DNS — можно сразу указать IPv4:
bash scripts/k8s/gen_secrets.sh 10.177.103.102
make k8s-secrets DOMAIN=10.177.103.102

# Cert с обоими — IP сейчас, DNS-имя на будущее (повторный logon будет
# валиден и по https://10.177.103.102/, и по https://dbos.example.com/):
SAN_EXTRA="DNS:dbos.example.com" \
    bash scripts/k8s/gen_secrets.sh 10.177.103.102
```

В IP-режиме скрипт:

- кладёт `subjectAltName=IP:<addr>` в self-signed cert (вместо `DNS:`);
- убирает `host:` из `k8s/50-ingress.yaml` — Traefik принимает любой
  Host header, включая `Host: 10.177.103.102`;
- `/etc/hosts` править не нужно — обращайтесь напрямую по IP.

Создаст `k8s/20-secrets.yaml` (gitignored, `chmod 600`) со случайными:

- паролями БД per-service (`auth_user`, `logging_user`, `server_user`, `worker_user`)
- `AUTH_SECRET_KEY` для подписи JWT
- `DOCKER_RSA_PRIVATE_KEY` (RSA 2048) для Docker registry token-flow
- `LOGGING_SERVICE_API_KEY` (outbound) + `LOGGING_SERVICE_API_KEYS_JSON` (inbound per-service map) + `LOGGING_INTROSPECT_SERVICE_API_KEY`
- `SERVER_ENCRYPTION_KEY` (мастер-ключ AES-256-GCM), `SERVER_ENCRYPTION_KEY_VERSION`, `HKDF_SALT_HEX` — корни шифрования server_account/IPMI паролей
- `SERVER_SERVICE_API_KEY`, `SERVER_INBOUND_SERVICE_API_KEYS` (worker_bot map), `WORKER_BOT_TOKEN`, `WORKER_SERVICE_API_KEY`
- `REDIS_PASSWORD`
- `INITIAL_ADMIN_PASSWORD` (16 символов)

**Сохраните admin-пароль и master-key из вывода скрипта.** Кроме stdout скрипт пишет полный summary в `/tmp/dbos-secrets-<ts>.txt` (chmod 600). После переноса в password manager — `shred -u /tmp/dbos-secrets-<ts>.txt`.

> ⚠ Повторный запуск `gen_secrets.sh` перегенерирует ВСЕ секреты в `20-secrets.yaml` (включая env-seed `SERVER_ENCRYPTION_KEY`) и **перезапишет файл** `21-keystore-secrets.yaml`. На **уже работающем** кластере это НЕ меняет активный ключ: `deploy.sh` применяет keystore create-only и живой `dbos-server-encryption-keys` не трогает (env-seed при `KEYSTORE_BACKEND=k8s` не используется). Но если затем пересоздать namespace — поднимется уже новый ключ, и старые ciphertext'ы станут недешифруемыми. Для штатной смены мастер-ключа без потери данных используй `scripts/k8s/rotate_master_key.sh` (см. «Ротация мастер-ключа» ниже). Для миграции действующего деплоя на keystore без смены ключа — `gen_secrets.sh --keystore-only`.

TLS-сертификаты для Ingress больше не генерирует этот скрипт — их выпускает
cert-manager (см. шаг 2a).

### Ротация мастер-ключа шифрования

`SERVER_ENCRYPTION_KEY` — корень envelope encryption паролей `server_account`/IPMI. Wire-format ciphertext'а версионирован (`v<N>$<nonce>$<ct>`), KDF — HKDF-SHA256. Ротация без re-encrypt'а возможна: старый ключ остаётся в Secret под `SERVER_ENCRYPTION_KEY__v<old>`, новый шифрует новые строки, фоновый `secrets.reencrypt_lazy` в server-worker перешивает существующие.

Полный runbook — `obsidian/infra/runbooks/Rotation.md`, раздел «Master keys». Краткая последовательность:

```bash
# 1. Проверить, что предыдущая миграция завершена (remaining=0):
bash scripts/k8s/rotate_master_key.sh --status

# 2. Запуск (интерактивный confirm на каждом шаге):
bash scripts/k8s/rotate_master_key.sh
#    - сгенерирует новый ключ
#    - пропатчит Secret (SERVER_ENCRYPTION_KEY__v<old> ← old, KEY ← new, VERSION bump)
#    - kubectl rollout restart server-service + server-worker

# 3. Seed outbox (admin-токеном):
ADMIN_JWT=$(curl -ks -X POST https://<domain>/api/auth/v1/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"<pwd>"}' | jq -r .access_token)
kubectl -n dbos exec deploy/server-service -- \
    curl -s -X POST -H "Authorization: Bearer $ADMIN_JWT" \
    "http://localhost:8002/api/server/v1/reencrypt_outbox/seed?limit=5000"

# 4. Дождаться remaining=0 (worker крутит reencrypt_lazy по расписанию):
kubectl -n dbos exec deploy/server-service -- \
    curl -s -H "Authorization: Bearer $ADMIN_JWT" \
    http://localhost:8002/api/server/v1/migration_status | jq .

# 5. Финализация — удалить SERVER_ENCRYPTION_KEY__v<old> из Secret:
bash scripts/k8s/rotate_master_key.sh --finalize
```

**Compromise мастер-ключа** (утёк в git / чат / dump pod env) — см. `obsidian/infra/runbooks/Incident-response.md` и `obsidian/infra/runbooks/Rotation.md`. Кратко: ротация + параллельно ротировать сами пароли в системах назначения через `/server-accounts/<id>/rotate-password` и `/ipmi-controllers/<id>/rotate-credentials`.

### 2a. Edge-TLS-сертификат

Никакой отдельной установки cert-manager **не требуется** — `gen_secrets.sh`
(шаг 2) одновременно с `dbos-secrets` создаёт Secret `dbos-ingress-tls`
с self-signed сертификатом под выбранный `<host>` (DNS или IP). Traefik
подхватит его при `make k8s-deploy`.

Если нужно перевыпустить только TLS (например, сменили IP/DNS) — повторно
запусти `make k8s-secrets DOMAIN=<...>`. **Внимание:** это пересоздаст
`dbos-secrets` целиком, что вытрет master-ключи; используй точечную
ротацию TLS через прямой `kubectl create secret tls dbos-ingress-tls ...`
(см. содержимое `gen_secrets.sh`).

### 3. Сборка образов на VM (каждый раз при изменении кода)

```bash
make k8s-build
```

- `docker build` обоих сервисов
- `docker save` → tar
- `k3s ctr images import` — образы попадают в containerd k3s

В манифестах `imagePullPolicy: Never` — это критично, чтобы k3s не пытался скачать образ из docker.io.

### 4. Деплой

```bash
make k8s-deploy
```

- `kubectl apply -k k8s/` применяет всё через kustomize
- Ждёт `rollout status` для каждого deployment (timeout 5 минут)
- На первом запуске:
  1. Создаются PVC, postgres-pods, secrets, configmaps
  2. auth_service выполняет alembic-миграции, создаёт `INITIAL_ADMIN_USERNAME` (admin)
  3. logging_service выполняет свои миграции

В конце выведет:
```
auth_service    →  https://<domain>/api/auth/v1/docs
logging_service →  https://<domain>/api/logging/v1/docs
```

### 5. Доступ к hostname с клиента

Один платформенный хост (DNS-имя или IP), который оператор задал в
`make k8s-secrets DOMAIN=...`. Если DNS не разрешается из клиентской сети —
добавить запись в `/etc/hosts`:

```bash
sudo tee -a /etc/hosts <<EOF
<VM_IP>  dbos.example.com
EOF
```

В IP-режиме (`make k8s-secrets DOMAIN=10.177.103.102`) hosts-файл не нужен.

### 5a. Доверие к edge-сертификату на admin-машинах

`dbos-ingress-tls` — self-signed. Браузер и curl без `-k` сертификат не
примут, пока его не добавить в trust store как trusted (это публичный
cert, а не CA — поэтому он импортируется как «exception» / «trust this
certificate» для конкретного хоста, а не на весь TLS).

**Экспорт публичной части с VM:**

```bash
kubectl -n dbos get secret dbos-ingress-tls \
    -o jsonpath='{.data.tls\.crt}' | base64 -d > dbos-ingress.crt
```

**Импорт на Linux (Debian/Astra/Ubuntu):**

```bash
sudo cp dbos-ingress.crt /usr/local/share/ca-certificates/dbos-ingress.crt
sudo update-ca-certificates
curl https://<host>/api/auth/v1/health
```

**Импорт на Linux (RHEL/CentOS/Fedora):**

```bash
sudo cp dbos-ingress.crt /etc/pki/ca-trust/source/anchors/dbos-ingress.crt
sudo update-ca-trust
```

**Импорт в Firefox** — Settings → Privacy & Security → View Certificates →
Servers → Add Exception → ввести https://<host>/, принять.

**Импорт в Windows (PowerShell, admin):**

```powershell
Import-Certificate -FilePath dbos-ingress.crt -CertStoreLocation Cert:\LocalMachine\Root
```

Альтернатива для команд CLI — `curl -k`, `kubectl ... --insecure-skip-tls-verify`.
Для production-операций — лучше импортировать.

### 6. Проверка работоспособности

```bash
# Без -k — после импорта (5a)
curl https://<host>/api/auth/v1/health
curl https://<host>/api/logging/v1/health
curl https://<host>/api/server/v1/health
curl https://<host>/api/secret/v1/health

# Логин:
curl -X POST https://<host>/api/auth/v1/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"<пароль из make k8s-secrets>"}'

# Swagger UI:
#   https://<host>/docs                          (общий, через dbos-swagger-ui)
#   https://<host>/api/auth/v1/docs              (per-service)
#   https://<host>/api/logging/v1/docs
#   https://<host>/api/server/v1/docs
#   https://<host>/api/secret/v1/docs
```

**HTTP→HTTPS редирект:** `http://<host>/...` отвечает 301 на `https://...`. Это настроено отдельным Ingress + Traefik middleware.

### Ротация edge-сертификата

Auto-renewal'а **нет** (cert-manager не подключён к Ingress'у).
`dbos-ingress-tls` живёт 5 лет; ротировать по compliance — раз в год руками.
Процедура — `obsidian/infra/runbooks/Rotation.md`. Когда срок подходит к концу
или ключ скомпрометирован:

```bash
# Перегенерировать tls-Secret под тот же host, не трогая dbos-secrets:
HOST=<dbos.example.com или IP>
openssl req -x509 -nodes -newkey rsa:2048 -days 1825 \
    -keyout /tmp/tls.key -out /tmp/tls.crt \
    -subj "/CN=$HOST" -addext "subjectAltName=DNS:$HOST"
kubectl -n dbos create secret tls dbos-ingress-tls \
    --cert=/tmp/tls.crt --key=/tmp/tls.key --dry-run=client -o yaml \
    | kubectl apply -f -
shred -u /tmp/tls.key
# Traefik подхватит обновлённый Secret без рестарта подов.
```

### Легаси /rest/api

Скрипты на стендах ходят на `http://allta.devos.astralinux.ru/rest/api/...`
(`get-repo-path`, `get-confluence-url`, ...) по plain HTTP без токена. Их
обслуживает testing_service (`/rest/api/*`, доступ — только из подсетей
таблицы `compat_allowed_networks`, UI «Администрирование → Легаси /rest/api»).

- Имя хоста — `LEGACY_COMPAT_HOST` в `k8s/deploy.env` (пример —
  `deploy.env.example`). `make k8s-secrets` рендерит
  `52-legacy-compat-ingress.yaml` (entrypoint `web`, только `/rest/api`),
  `deploy.sh` его применяет. Пустое имя — любой Host.
- Порт 80 traefik'а должен оставаться открытым (entrypoint `web`), хотя
  `make prepare-k3s` советует его закрыть: без него маршрута нет.
- Адрес стенда должен дойти до пода неизменным. Для k3s (klipper-lb) —
  `externalTrafficPolicy: Local` у Service traefik, например через
  `/var/lib/rancher/k3s/server/manifests/traefik-config.yaml`:

  ```yaml
  apiVersion: helm.cattle.io/v1
  kind: HelmChartConfig
  metadata:
    name: traefik
    namespace: kube-system
  spec:
    valuesContent: |-
      service:
        spec:
          externalTrafficPolicy: Local
  ```

  Иначе testing_service видит адрес ноды, а не стенда: запрос получит 403
  (или отдел по умолчанию, если подсеть ноды разрешена).
- Проверка до переноса DNS (с машины из подсети стендов):
  `curl -H 'Host: allta.devos.astralinux.ru' http://<IP платформы>/rest/api/get-repo-path`.
- Перенос DNS: A-запись `allta.devos.astralinux.ru` → IP платформы. Адреса
  infocollector (`:18181`), FTP, devpi (`:3141`) и docker registry (`:21503`)
  легаси-хоста при этом **не переезжают**: для `starter.sh` из профиля
  запуска они заданы переменными `INFOCOLLECTOR_URL`, `FTP_URL`, `DEVPI_URL`,
  `DOCKER_REGISTRY`; ветки, которые обращаются к `allta.devos.astralinux.ru:3141`
  / `:21503` по имени, после переноса DNS будут ходить на платформу — эти
  сервисы нужно оставить доступными по старому имени (отдельная запись) или
  поднять на платформе.

Ограничение: манифест `63-testing-service.yaml` пока не имеет egress-политик
и Job'а миграций в `45-migrations.yaml`; `80-networkpolicy.yaml` описывает
для testing-service только вход от traefik.

## Повседневные операции

### Обновление кода

```bash
git pull
make k8s-rollout                 # пересобрать оба + rolling update
make k8s-rollout SVC=auth        # только auth
make k8s-rollout SVC=logging     # только logging
```

`kubectl rollout restart` создаст по одной новой реплике, дождётся её readiness, удалит старую. `maxUnavailable: 0` + PodDisruptionBudget гарантируют, что в каждый момент времени минимум 1 реплика отвечает на запросы.

### Откат

```bash
make k8s-rollback SVC=auth                 # на предыдущую ревизию
make k8s-rollback SVC=auth REV=3           # на конкретную ревизию
kubectl -n dbos rollout history deploy/auth-service   # посмотреть историю
```

### Логи

```bash
make k8s-logs                    # последние 100 строк auth_service + tail -f
make k8s-logs SVC=logging        # logging_service
kubectl -n dbos logs -l app=auth-service --tail=500
```

### Статус

```bash
make k8s-status                  # pods/svc/deploy/pvc
kubectl -n dbos describe pod <pod-name>   # детали по конкретному pod
kubectl -n dbos top pods                  # CPU/RAM (требует metrics-server)
```

### Бэкапы Postgres

Прод-сценарий — CronJob'ы внутри кластера (`k8s/100-postgres-backup.yaml`):

- `pg-backup-auth` — daily 02:00 MSK (UTC-cron `0 23 * * *`)
- `pg-backup-logging` — daily 02:15 MSK (UTC-cron `15 23 * * *`)
- Том: PVC `dbos-backup-pv` (20 GiB, `k8s/101-backup-pvc.yaml`)
- Формат: `pg_dump --format=custom --compress=6 --no-owner --no-acl`
  + проверка целостности через `pg_restore --list` сразу после dump'а.
- Retention: ring of suffixes — `daily-NN` (14 слотов), `weekly-NN`
  (4 слота, rotate в понедельник UTC), `monthly-NN` (6 слотов, rotate
  1-го числа UTC). Старого файла никогда не больше N штук, имена
  детерминированы.
- `successfulJobsHistoryLimit: 3`, `failedJobsHistoryLimit: 3`.

Status / inspect:

```bash
kubectl -n dbos get cronjob
kubectl -n dbos get jobs -l app=pg-backup
kubectl -n dbos logs job/pg-backup-auth-<runid>
```

Локальный ad-hoc backup с VM (через kubectl exec):

```bash
make k8s-backup                                        # ./k8s/backups/<timestamp>/
make k8s-backup DEST=/var/backups/dbos
```

**Восстановление из CronJob-бэкапа (custom-формат):**

```bash
# 1. через одноразовый pod, монтирующий dbos-backup-pv
#    (kubectl run inspect --image=postgres:16 ... --overrides='{...}';
#     полная процедура — obsidian/infra/runbooks/Backup-DR.md):
# 2. накатить:
kubectl -n dbos exec -i deploy/auth-postgres -- \
    pg_restore -U auth_user -d auth_db --clean --if-exists < /tmp/auth.dump
```

### Observability

- `/metrics`-endpoint'ов нет (owner decision W41 — без отдельного Prometheus).
  В каждом pod-template'е `prometheus.io/scrape: "false"`, чтобы ванильный
  Prometheus не дёргал несуществующий путь.
- Operational counters читаются из payload `/api/<svc>/v1/ready` —
  `dispatch_outbox_pending_depth`, `secrets_decrypt_failures_total`,
  `*_dropped_429`, `emit_tasks_overflow_total`.
- Audit-стрим: `GET /api/logging/v1/events` (loging_service); SIEM-rule'ы
  подключаются полингом since/until.
- Worker (без HTTP) — liveness через `worker_heartbeats` + `audit_outbox`.
- Подробно по DR / counter'ам — `obsidian/infra/runbooks/Backup-DR.md` и
  `obsidian/infra/runbooks/Incident-response.md`. Отдельного Observability.md
  пока нет; правила SIEM подключаются полингом `GET /api/logging/v1/events`.

### Smoke-test после деплоя

```bash
BASE_URL=https://dbos.local ADMIN_PASS=<пароль> \
    bash scripts/k8s/smoke_test.sh
```

Проверяет `/health` + `/ready` всех HTTP-сервисов, login admin → JWT,
`/me`, и что login-event дошёл до `GET /api/logging/v1/events`.

Если `BASE_URL` не задан, smoke сам подтянет endpoint из
`k8s/.env.k8s` (`INGRESS_HOST=<dns-or-ip>` — пишет `gen_secrets.sh`),
а если файла нет — попробует `kubectl -n dbos get ingress dbos-ingress`.

### Применить изменение конфига (без пересборки)

```bash
vim k8s/40-auth-service.yaml      # например, поднять ACCESS_TOKEN_TTL_MINUTES
make k8s-deploy                   # apply + rollout до готовности
```

## Откатные/аварийные сценарии

### Pod циклически рестартится

```bash
kubectl -n dbos describe pod <name>           # причина в Events
kubectl -n dbos logs <name> --previous        # логи упавшего инстанса
```

Частые причины: миграции не прошли (БД не готова), отсутствует Secret, неверный конфиг.

### Postgres не отвечает

```bash
kubectl -n dbos exec -it deploy/auth-postgres -- psql -U auth_user -d auth_db
# \dt — посмотреть таблицы, SELECT version() — версия PG
```

### Удалить всё (включая БД!)

```bash
make k8s-destroy           # требует подтверждения 'y'
```

## Что НЕ покрыто этим сетапом

- **Полноценная HA** — одна VM = одна точка отказа. Для HA нужны минимум 3 master-узла k3s + внешняя БД.
- **Публично-доверенный TLS** — internal CA, не Let's Encrypt. Это сознательное решение под closed-network: ACME требует наружного DNS-челленджа. После импорта `dbos-ca.crt` (см. шаг 5a) admin-машины видят сертификаты как валидные. Внешние клиенты (за периметром платформы) не предусмотрены.
- **Метрики/alerting** — `kubectl top` требует metrics-server (`helm install metrics-server`), Prometheus/Grafana не входит.
- **Off-host backups** — `make k8s-backup` пишет на тот же VM. Настройте `rsync` / `borg` в S3 / на другой хост.
- **Image registry** — образы хранятся только в containerd этой VM. Если VM умрёт, пересоберёте на новой из git.

## Структура каталога

```
k8s/
├── README.md                       # этот файл
├── kustomization.yaml              # точка входа для `kubectl apply -k`
├── 00-namespace.yaml
├── 10-postgres-auth.yaml           # PVC + Deployment + Service для auth-postgres
├── 11-postgres-logging.yaml        # то же для logging-postgres
├── 12-postgres-server.yaml         # postgres для server_service
├── 12-postgres-worker.yaml         # postgres для server_worker
├── 13-postgres-secret.yaml         # postgres для secret_service
├── 13-secret-service.yaml          # ConfigMap + Deployment + Service + PDB для secret_service
├── 20-secrets.yaml                 # генерируется gen_secrets.sh (gitignored)
├── 20-secrets.yaml.example         # шаблон
├── 30-logging-service.yaml         # ConfigMap + Deployment + Service ClusterIP + PDB
├── 40-auth-service.yaml            # то же для auth_service
├── 50-server-service.yaml          # то же для server_service
├── 60-server-worker.yaml           # taskiq-worker (без HTTP)
├── 50-ingress.yaml                 # Ingress (path-based: /api/auth, /api/logging, /api/server, /api/secret, /docs)
├── 50-ingress.yaml.template        # legacy-шаблон (не используется, оставлен для истории)
├── 55-swagger-ui.yaml              # объединённый Swagger UI (dbos-swagger-ui за /docs)
├── 90-cert-manager-install.yaml    # инструкции по установке cert-manager (опционально, к Ingress'у не подключён)
├── 100-postgres-backup.yaml        # CronJob pg_dump для всех postgres-кластеров
├── 101-backup-pvc.yaml             # PVC dbos-backup-pv (20 GiB)
├── 102-master-keys-backup.yaml     # CronJob master-keys-backup (ежедневно)
├── 103-secret-full-backup.yaml     # CronJob secret-full-backup (еженедельно)
├── 104-pg-restore-drill.yaml       # CronJob pg-restore-drill (ежемесячно)
├── 120-rotation-rbac.yaml          # ServiceAccount/Role/RoleBinding rotation-runner
└── .env.k8s                        # домен для повторных запусков (gitignored)

scripts/k8s/
├── install_k3s.sh                  # установка k3s на Astra Linux SE
├── install_cert_manager.sh         # offline-установка cert-manager (опционально, для будущих фич)
├── cert-manager.yaml               # манифест cert-manager v1.15.3 (gitignored)
├── gen_secrets.sh                  # генерация 20-secrets.yaml + dbos-ingress-tls
├── rotate_master_key.sh            # ротация SERVER_ENCRYPTION_KEY
├── rotate_secret_master_key.sh     # ротация SECRET_ENCRYPTION_KEY
├── rotate_redis_stash_master_key.sh # ротация REDIS_STASH_ENCRYPTION_KEY
├── rotate_db_passwords.sh          # ротация DB-паролей (per-service)
├── rotate_redis_password.sh        # ротация REDIS_PASSWORD
├── rotate_s2s_keys.sh              # ротация S2S API-ключей
├── _rotation_helpers.sh            # общий код проверки migration_status / TTL gating
├── test_rotation_safety.sh         # orchestrator (backup → rotate → smoke → restore-on-fail)
├── backup_master_keys.sh           # backup master-keys в зашифрованный архив
├── backup_secret_full.sh           # backup всего Secret'а dbos-secrets
├── restore_master_keys.sh          # restore master-keys из архива
├── pg_restore_drill.sh             # проверка восстанавливаемости pg_dump'ов
├── backup_pg.sh                    # pg_dump custom-формат (in-cluster + host режимы)
├── build_and_import.sh             # docker build + import в k3s
├── deploy.sh                       # kubectl apply -k + rollout status
├── rollout.sh                      # rebuild + rolling update
├── rollback.sh                     # rollout undo
├── dump_openapi.sh                 # дамп OpenAPI-схем из подов
├── smoke_test.sh                   # post-deploy verification (health/login/me/audit)
├── README-MASTER-KEYS.md           # backup/restore master-keys и rotation overview
└── README-PG-RESTORE-DRILL.md      # процедура pg_restore_drill.sh
```
