# Postgres baseline для DBOS k8s

Все пять postgres-инстансов (`auth`, `logging`, `server`, `worker`, `secret`)
скопированы из одного шаблона. Файлы:

- `10-postgres-auth.yaml`
- `10-postgres-server.yaml`
- `10-postgres-worker.yaml`
- `11-postgres-logging.yaml`
- `13-postgres-secret.yaml`

Изолированные базы по сервисам — сознательный выбор:

- каждый сервис владеет своей БД, не разделяет данные с соседом;
- падение Postgres'а одного сервиса не выносит остальные;
- backup/restore drill можно гонять на одной БД, не трогая остальные.

Kustomize-overlay сюда **сознательно не внедрён** — для пяти 100-строчных
манифестов overhead на изучение и поддержку шаблона выше, чем выигрыш на
дедупликации. Если в будущем количество баз вырастет до десятка — переходим
на Kustomize/Helm/cloudnative-pg.

## Что общее (must match across all 5)

Если меняешь одно из перечисленного — обнови во **всех пяти** файлах:

### PersistentVolumeClaim

- `accessModes: ["ReadWriteOnce"]`
- `storage: 5Gi`
- `storageClassName: local-path`
- `annotations.helm.sh/resource-policy: keep` — guard на Retain reclaim policy
  у k3s local-path provisioner.

### Deployment / template

- `replicas: 1` — Postgres не работает в двух копиях на одном PVC.
- `strategy.type: Recreate`
- `terminationGracePeriodSeconds: 60`
- `automountServiceAccountToken: false`
- pod-level `securityContext`:
  - `runAsNonRoot: true`
  - `runAsUser: 999` / `runAsGroup: 999` / `fsGroup: 999`
    (UID/GID postgres из официального image)

### Container

- `image: postgres:16`
- `imagePullPolicy: IfNotPresent`
- container-level `securityContext`:
  - `allowPrivilegeEscalation: false`
  - `capabilities.drop: ["ALL"]`
  - `readOnlyRootFilesystem: false` — официальный postgres image пишет в
    `/var/run/postgresql`, `/tmp`, `/etc` (init); полноценный read-only FS
    требует десяток emptyDir mount'ов либо смены на cloudnative-pg.
- `env.PGDATA: /var/lib/postgresql/data/pgdata` — поддиректория, чтобы
  lost+found на корне PVC не мешал init'у.
- `volumeMounts[name=data].mountPath: /var/lib/postgresql/data`
- probes:
  - readiness: `pg_isready -U <user> -d <db>` initial 5s / period 5s /
    timeout 3s / failureThreshold 6 (= 30s окно ready)
  - liveness: `pg_isready` initial 30s / period 30s / timeout 5s /
    failureThreshold 3
- resources: requests 100m/256Mi, limits 1000m/1Gi

### Service

- `type: ClusterIP`
- порт 5432 → targetPort 5432

## Что варьируется (per-instance)

- имена объектов (`<svc>-postgres`, `<svc>-postgres-data`);
- метки `app: <svc>-postgres`;
- `POSTGRES_DB` (`auth_db`, `logging_db`, `server_db`, `worker_db`, `secret_db`);
- `POSTGRES_USER` / `POSTGRES_PASSWORD` — из Secret'а `dbos-secrets` под ключи
  `<SVC>_DB_USER` / `<SVC>_DB_PASSWORD`;
- значение `-U <user>` в readiness-probe (имя пользователя совпадает с
  `<svc>_user`).

## PodDisruptionBudget

Postgres'ы идут с `replicas: 1` — PDB для них не делается (PDB с
`minAvailable: 1` на single-replica deployment блокировал бы любой voluntary
drain). См. `70-pdb.yaml` — PDB только для сервисов с replicas≥2.

## Backup

- ежедневный pg_dump через CronJob'ы в `100-postgres-backup.yaml`
  (auth 02:00 / logging 02:15 / secret 02:30 / server 02:45 / worker 03:00 MSK);
- restore drill — `104-pg-restore-drill.yaml` (еженедельно);
- backup target — `dbos-backup-pv` PVC из `101-backup-pvc.yaml`.
