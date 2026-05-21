# DBOS Server Manager · деплой в k3s на одной VM

Этот каталог содержит k8s-манифесты для single-node k3s-деплоя на Astra Linux SE.
Сценарий: одна VM, она же master и worker. k3s даёт self-healing, rolling updates и откаты.

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
              │           :443 + TLS (Secret dbos-tls)│
              └───────────────────┬──────────────────┘
                                  ▼
                  https://<domain>/api/auth/...
                  https://<domain>/api/logging/...
```

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

### 2. Генерация prod-секретов + TLS-сертификата (один раз)

```bash
make k8s-secrets                              # спросит домен интерактивно
# или сразу:
bash scripts/k8s/gen_secrets.sh dbos.example.com
```

Создаст `k8s/20-secrets.yaml` и `k8s/50-ingress.yaml` (оба gitignored) со случайными:

- паролями БД (auth_user, logging_user)
- `SECRET_KEY` для подписи JWT
- `DOCKER_RSA_PRIVATE_KEY` (RSA 2048) для Docker registry token-flow
- `LOGGING_SERVICE_API_KEY` (shared между auth и logging)
- `INITIAL_ADMIN_PASSWORD` (16 символов)
- **TLS cert + key** для Ingress (self-signed, RSA 2048, валиден 365 дней)
- **Ingress** с host=`<ваш-домен>` и роутингом `/api/auth/` → auth-service, `/api/logging/` → logging-service

**Сохраните admin-пароль из вывода скрипта** — после первого bootstrap его не восстановить.

**Перевыпуск сертификата** (когда подойдёт срок):

```bash
bash scripts/k8s/gen_secrets.sh                 # тот же домен (из .env.k8s)
make k8s-deploy                                 # подтянет новый Secret
```

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

### 5. Доступ к домену с клиента

Если домен не зарегистрирован в DNS (или вы за корпоративным DNS), пропишите в `/etc/hosts` на клиенте:

```bash
echo "<VM_IP>  <domain>" | sudo tee -a /etc/hosts
```

### 6. Проверка работоспособности

```bash
# -k потому что self-signed cert
curl -ks https://<domain>/api/auth/v1/health
curl -ks https://<domain>/api/logging/v1/health

# Логин:
curl -ks -X POST https://<domain>/api/auth/v1/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"<пароль из make k8s-secrets>"}'

# Открыть Swagger UI в браузере (один раз принять предупреждение о self-signed):
#   https://<domain>/api/auth/v1/docs
#   https://<domain>/api/logging/v1/docs
```

**HTTP→HTTPS редирект:** `http://<domain>/...` отвечает 301 на `https://...`. Это настроено отдельным Ingress + Traefik middleware.

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

```bash
make k8s-backup                                        # ./k8s/backups/<timestamp>/
make k8s-backup DEST=/var/backups/dbos                 # альтернативный путь

# Cron на VM (каждую ночь в 02:00 MSK):
echo "0 23 * * * cd /opt/dbos_server_service && make k8s-backup DEST=/var/backups/dbos" \
    | sudo tee /etc/cron.d/dbos-backup
```

Старше 30 дней удаляются автоматически.

**Восстановление из бэкапа:**

```bash
gunzip -c backups/<ts>/auth-postgres.sql.gz \
    | kubectl -n dbos exec -i deploy/auth-postgres -- psql -U auth_user -d auth_db
```

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
- **Доверенный TLS** — self-signed cert, браузер ругается. Для prod: либо bring-your-own cert (замените content `dbos-tls` Secret), либо подключите cert-manager + Let's Encrypt (нужны публичный DNS и доступ наружу с VM).
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
├── 50-ingress.yaml              # генерируется gen_secrets.sh (gitignored)
├── 50-ingress.yaml.template     # шаблон с __INGRESS_HOST__
└── .env.k8s                     # домен для повторных запусков (gitignored)

scripts/k8s/
├── install_k3s.sh               # установка k3s на Astra Linux SE
├── gen_secrets.sh               # генерация 20-secrets.yaml
├── build_and_import.sh          # docker build + import в k3s
├── deploy.sh                    # kubectl apply -k + rollout status
├── rollout.sh                   # rebuild + rolling update
├── rollback.sh                  # rollout undo
└── backup_pg.sh                 # pg_dump обеих БД
```
