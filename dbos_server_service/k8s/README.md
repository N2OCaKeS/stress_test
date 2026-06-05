# DBOS Server Manager · деплой в k3s на одной VM

Этот каталог содержит k8s-манифесты для single-node k3s-деплоя на Astra Linux SE.
Сценарий: одна VM, она же master и worker. k3s даёт self-healing, rolling updates и откаты.

> **Полный end-to-end runbook** — `obsidian/infra/Production deploy runbook.md`.
> **Чек-листы** (pre-/post-deploy, SLO/SLI) — `obsidian/infra/Production checklist.md`.
> Этот README остаётся как краткая справка про конкретные манифесты и устройство каталога.

## TL;DR

```bash
make k8s-install-k3s                 # один раз, на чистой VM (sudo)
make k8s-install-cert-manager        # один раз, после k3s
make k8s-build                       # после каждого изменения кода
make k8s-secrets DOMAIN=dbos.local   # один раз, заполнить k8s/20-secrets.yaml
make prod-deploy                     # build → check → deploy → migrate → smoke
```

## Архитектура

```text
              VM (Astra Linux SE) + k3s
              ┌──────────────────────────────────────┐
              │  namespace: dbos                     │
              │                                      │
              │  ┌─auth-postgres──┐  ┌─logging-pg──┐ │
              │  │ Deployment×1   │  │ Deployment×1│ │
              │  │ PVC 5 Gi       │  │ PVC 5 Gi    │ │
              │  └────────────────┘  └─────────────┘ │
              │         ▲                  ▲         │
              │         │                  │         │
              │  ┌─auth-service──┐  ┌─logging-service┐
              │  │ Deployment×2  │  │ Deployment×2  ││
              │  │ ClusterIP     │  │ ClusterIP     ││
              │  └───────┬───────┘  └───────┬───────┘│
              │          │                  │        │
              │          ▼                  ▼        │
              │     /api/auth/*       /api/logging/* │
              │          └────────┬─────────┘        │
              │                   ▼                  │
              │           Traefik (Ingress)          │
              │           :80 → 301 → :443           │
              │           :443 + TLS (cert-manager)  │
              └───────────────────┬──────────────────┘
                                  ▼
              https://auth.dbos.local/api/auth/...
              https://loging.dbos.local/api/logging/...
              https://server.dbos.local/api/server/...
```

## TLS-стратегия: internal CA + cert-manager

Платформа разворачивается в closed network — Let's Encrypt и ACME недоступны.
TLS-сертификаты выпускает cert-manager изнутри кластера, корневой CA — собственный.

```
   self-signed Issuer (bootstrap)
            │
            ▼
   Certificate "DBOS Server Manager Internal CA"   ← 10 лет, isCA: true
            │  (приватный ключ в Secret dbos-ca-key-pair)
            ▼
   CA Issuer (dbos-ca-issuer)
            │
            ├──► Certificate auth-service-tls       ← 1 год, renew за 30 дней
            ├──► Certificate logging-service-tls    ← 1 год, renew за 30 дней
            └──► Certificate server-service-tls     ← 1 год, renew за 30 дней
                       │
                       ▼
                 Ingress TLS (Traefik)
```

**Автоматическая ротация** — cert-manager Renewal Controller сам пересчитывает
`renewBefore` и за 30 дней до истечения leaf'а перевыпускает Secret;
Traefik подхватывает новый TLS-секрет без рестарта. Вмешательство админа не
требуется. Корневой CA живёт 10 лет (за год до истечения тоже автоперевыпуск,
но это означает смену CA — придётся переимпортировать на admin-машинах).

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
```

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

> ⚠ Повторный запуск `gen_secrets.sh` перегенерирует ВСЕ секреты, включая `SERVER_ENCRYPTION_KEY` — все существующие зашифрованные строки в `server_db` станут недешифруемыми. Для смены **только** мастер-ключа используй `scripts/k8s/rotate_master_key.sh` — см. раздел «Ротация мастер-ключа» ниже.

TLS-сертификаты для Ingress больше не генерирует этот скрипт — их выпускает
cert-manager (см. шаг 2a).

### Ротация мастер-ключа шифрования

`SERVER_ENCRYPTION_KEY` — корень envelope encryption паролей `server_account`/IPMI. Wire-format ciphertext'а версионирован (`v<N>$<nonce>$<ct>`), KDF — HKDF-SHA256. Ротация без re-encrypt'а возможна: старый ключ остаётся в Secret под `SERVER_ENCRYPTION_KEY__v<old>`, новый шифрует новые строки, фоновый `secrets.reencrypt_lazy` в server-worker перешивает существующие.

Полный runbook — `obsidian/infra/Secrets management.md`. Краткая последовательность:

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

**Compromise мастер-ключа** (утёк в git / чат / dump pod env) — см. секцию «Incident response» в `obsidian/infra/Secrets management.md`. Кратко: ротация + параллельно ротировать сами пароли в системах назначения через `/server-accounts/<id>/rotate-password` и `/ipmi-controllers/<id>/rotate-credentials`.

### 2a. Установка cert-manager + выпуск TLS (один раз)

```bash
# на машине с интернетом — скачать манифест и docker-образы (см. инструкции
# в шапке k8s/90-cert-manager-install.yaml), перенести на VM.
sudo bash scripts/k8s/install_cert_manager.sh
```

Скрипт:

- `kubectl apply -f scripts/k8s/cert-manager.yaml` — ставит cert-manager v1.15.3
- Ждёт rollout cert-manager-controller / cainjector / webhook
- Применяет `k8s/91-ca-issuer.yaml` — self-signed bootstrap, CA Certificate
  (CN `DBOS Server Manager Internal CA`, 10 лет), CA Issuer
- Применяет `k8s/92-certificates.yaml` — leaf-сертификаты для
  `auth.dbos.local`, `loging.dbos.local`, `server.dbos.local`
  (1 год, renew за 30 дней)
- Дожидается готовности всех сертификатов

После шага в namespace `dbos` появятся Secret'ы `auth-service-tls`,
`logging-service-tls`, `server-service-tls` — Ingress подхватит их автоматически.

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

### 5. Доступ к hostnames с клиента

Если `*.dbos.local` не разрешается через корпоративный DNS — пропишите три записи в `/etc/hosts` на клиенте:

```bash
sudo tee -a /etc/hosts <<EOF
<VM_IP>  auth.dbos.local
<VM_IP>  loging.dbos.local
<VM_IP>  server.dbos.local
EOF
```

### 5a. Импорт CA на admin-машины

Чтобы браузер и curl доверяли сертификатам без `-k`, нужно один раз импортировать
корневой CA-сертификат (выпущенный cert-manager'ом) как trusted.

**Экспорт CA с VM:**

```bash
kubectl -n dbos get secret dbos-ca-key-pair \
    -o jsonpath='{.data.ca\.crt}' | base64 -d > dbos-ca.crt
```

**Импорт на Linux (Debian/Astra/Ubuntu):**

```bash
sudo cp dbos-ca.crt /usr/local/share/ca-certificates/dbos-ca.crt
sudo update-ca-certificates
# Проверка:
curl https://auth.dbos.local/api/auth/v1/health
```

**Импорт на Linux (RHEL/CentOS/Fedora):**

```bash
sudo cp dbos-ca.crt /etc/pki/ca-trust/source/anchors/dbos-ca.crt
sudo update-ca-trust
```

**Импорт в Firefox** — Settings → Privacy & Security → View Certificates →
Authorities → Import → выбрать `dbos-ca.crt` → отметить "Trust this CA to
identify websites".

**Импорт в Chrome / Chromium** — использует системный trust store на Linux,
команды выше достаточно. На Windows — `certmgr.msc` → Trusted Root
Certification Authorities → Import.

**Импорт в Windows (PowerShell, admin):**

```powershell
Import-Certificate -FilePath dbos-ca.crt -CertStoreLocation Cert:\LocalMachine\Root
```

### 6. Проверка работоспособности

```bash
# Без -k — браузер и curl доверяют CA после импорта
curl https://auth.dbos.local/api/auth/v1/health
curl https://loging.dbos.local/api/logging/v1/health
curl https://server.dbos.local/api/server/v1/health

# Логин:
curl -X POST https://auth.dbos.local/api/auth/v1/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"<пароль из make k8s-secrets>"}'

# Swagger UI:
#   https://auth.dbos.local/api/auth/v1/docs
#   https://loging.dbos.local/api/logging/v1/docs
#   https://server.dbos.local/api/server/v1/docs
```

**HTTP→HTTPS редирект:** `http://<host>/...` отвечает 301 на `https://...`. Это настроено отдельным Ingress + Traefik middleware.

### Авто-ротация сертификатов

Никаких действий админа не требуется:

- **Leaf-сертификаты** (1 год). За 30 дней до истечения cert-manager Renewal
  Controller перевыпускает Secret. Traefik watch'ит Secret и переключается на
  новый ключ без рестарта подов. Проверить статус:

  ```bash
  kubectl -n dbos get certificate
  kubectl -n dbos describe certificate auth-service-tls
  ```

- **Корневой CA** (10 лет). За год до истечения cert-manager перевыпустит
  CA Certificate, но это означает смену CA-ключа — на admin-машинах нужно
  будет переимпортировать `dbos-ca.crt`. Заранее запланируйте, лучше за
  пару месяцев до истечения вручную ротировать (создать новый CA, переключить
  Issuer, дать leaf'ам перевыпуститься, потом удалить старый).

**Принудительный перевыпуск leaf'а** (если, например, скомпрометировался ключ):

```bash
kubectl -n dbos delete secret auth-service-tls
# cert-manager увидит, что Certificate указывает на отсутствующий Secret,
# и сразу его пересоздаст.
```

или через cmctl:

```bash
cmctl renew -n dbos auth-service-tls
```

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
# 1. через одноразовый pod, монтирующий dbos-backup-pv:
#    kubectl run inspect --image=postgres:16 ... --overrides='{...}' (см. Observability.md)
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
- Подробно: `obsidian/infra/Observability.md` (counter'ы, SIEM-rules,
  Loki-friendly logging contract).

### Smoke-test после деплоя

```bash
BASE_URL=https://dbos.local ADMIN_PASS=<пароль> \
    bash scripts/k8s/smoke_test.sh
```

Проверяет `/health` + `/ready` всех HTTP-сервисов, login admin → JWT,
`/me`, и что login-event дошёл до `GET /api/logging/v1/events`.

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
├── README.md                    # этот файл
├── kustomization.yaml           # точка входа для `kubectl apply -k`
├── 00-namespace.yaml
├── 10-postgres-auth.yaml        # PVC + Deployment + Service для auth-postgres
├── 11-postgres-logging.yaml     # то же для logging-postgres
├── 20-secrets.yaml              # генерируется gen_secrets.sh (gitignored)
├── 20-secrets.yaml.example      # шаблон
├── 30-logging-service.yaml      # ConfigMap + Deployment + Service ClusterIP + PDB
├── 40-auth-service.yaml         # то же для auth_service
├── 50-server-service.yaml       # то же для server_service
├── 60-server-worker.yaml        # taskiq-worker (без HTTP)
├── 50-ingress.yaml              # Ingress для auth/loging/server.dbos.local
├── 50-ingress.yaml.template     # legacy-шаблон (не используется, оставлен для истории)
├── 90-cert-manager-install.yaml # инструкции по установке cert-manager (offline)
├── 91-ca-issuer.yaml            # bootstrap Issuer + CA Certificate + CA Issuer
├── 92-certificates.yaml         # leaf Certificate'ы для трёх hostname'ов
├── 100-postgres-backup.yaml     # CronJob pg_dump для auth/logging кластеров
├── 101-backup-pvc.yaml          # PVC dbos-backup-pv (20 GiB)
└── .env.k8s                     # домен для повторных запусков (gitignored)

scripts/k8s/
├── install_k3s.sh               # установка k3s на Astra Linux SE
├── install_cert_manager.sh      # offline-установка cert-manager + CA + leaf'ы
├── cert-manager.yaml            # манифест cert-manager v1.15.3 (gitignored, скачать вручную)
├── gen_secrets.sh               # генерация 20-secrets.yaml (полный набор секретов)
├── rotate_master_key.sh         # ротация SERVER_ENCRYPTION_KEY (интерактивно)
├── build_and_import.sh          # docker build + import в k3s
├── deploy.sh                    # kubectl apply -k + rollout status
├── rollout.sh                   # rebuild + rolling update
├── rollback.sh                  # rollout undo
├── backup_pg.sh                 # pg_dump custom-формат (in-cluster + host режимы)
└── smoke_test.sh                # post-deploy verification (health/login/me/audit)
```
