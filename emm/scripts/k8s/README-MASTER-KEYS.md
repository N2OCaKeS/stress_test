# Master-keys backup & restore

Backup и восстановление мастер-ключей шифрования DBOS Server Manager из k8s Secret'а `dbos-secrets`.

## Что хранится в `dbos-secrets`

Полный набор живёт в `k8s/20-secrets.yaml`, генерится `scripts/k8s/gen_secrets.sh`. Master-ключи, без которых **восстановление зашифрованных данных невозможно**:

| Поле | Назначение | Что станет нечитаемым при потере |
|------|------------|----------------------------------|
| `SERVER_ENCRYPTION_KEY` (+ `_VERSION`, + `SERVER_ENCRYPTION_KEY__v<N>` legacy) | Envelope encryption в `server_service` (server_account credentials, IPMI). | Все `server_account.encrypted_*` и IPMI-секреты. |
| `SECRET_ENCRYPTION_KEY` (+ `_VERSION`, + legacy `__v<N>`) | Envelope encryption в `secret_service` (bot tokens, s2s API keys). | Хранилище секретов целиком. |
| `REDIS_STASH_ENCRYPTION_KEY` (+ `_VERSION`, + legacy `__v<N>`) | Шифрование Redis-стэшей (provision creds и т.п.) — если включено. | Содержимое stash'ей; обычно ephemeral, но при инциденте полезно иметь. |
| `CREDS_STASH_ENCRYPTION_KEY` (+ `_VERSION`, + legacy `__v<N>`) | Шифрование Redis-стэша кред тестового пользователя в `testing_service` (пароль, SSH-ключ между prepare-for-test и claim'ом). В production без него сервис не стартует. | Записи живут ≤10 минут; потеря ключа = элементы в подготовке уйдут в retry. Ротации-скрипта нет: сменить значение + версию, прежний ключ в `CREDS_STASH_ENCRYPTION_KEY__v<N-1>`. |
| `HKDF_SALT_HEX` | Salt для HKDF-SHA256 KDF, общий для server/secret/stash. | Без него KDF-материал не воспроизводится → не расшифруешь даже с master-key. |
| `AUTH_SECRET_KEY` | Подпись JWT в `auth_service`. | Все выданные access/refresh-токены превратятся в тыкву, юзеры перелогинятся. |
| `DOCKER_RSA_PRIVATE_KEY` (опц.) | Token-flow для Docker registry. | Сломается push/pull в private registry, пока не сгенеришь новый и не обновишь registry config. |

> **Важно.** DB-пароли, REDIS_PASSWORD, s2s API-keys, INITIAL_ADMIN_PASSWORD в этот backup **не попадают**: их можно сменить ротацией без потери данных. Master-keys можно сменить только через двухфазный re-encrypt (см. `scripts/k8s/rotate_master_key.sh`).

## Что произойдёт, если master-key потерян

Это **катастрофический** сценарий: восстановить зашифрованные данные нельзя ничем, кроме backup'а master-key. В частности:

- `SERVER_ENCRYPTION_KEY` потерян → все `server_account.encrypted_*` колонки превращаются в random bytes. Парольные данные серверов придётся вводить заново.
- `SECRET_ENCRYPTION_KEY` потерян → все bot-токены, s2s-keys, OAuth-секреты в `secret_service` нечитаемы; нужно ре-issue.
- `HKDF_SALT_HEX` потерян → даже с master-key KDF не выдаст тот же material. **Salt критичен ровно так же, как сам ключ.**
- `AUTH_SECRET_KEY` потерян → новые JWT можно подписать новым ключом, но старые токены отвалятся. Это «мягкая» потеря (есть выход).

## Backup procedure

### Что использовать

```bash
scripts/k8s/backup_master_keys.sh
```

Дефолтное поведение:
- Source: `kubectl -n dbos get secret dbos-secrets`.
- Target dir: `/backup/master-keys/` (chmod 700), переопределяется `BACKUP_TARGET_DIR`.
- Шифрование (по приоритету): `age` → `gpg --symmetric AES256` → `openssl enc -aes-256-gcm -pbkdf2`.
- Passphrase: `BACKUP_PASSPHRASE` env (для cron), иначе интерактивно (read -s, два раза, min 12 chars).
- Для `age` с publickey-получателями: `BACKUP_AGE_RECIPIENTS="age1xxx,age1yyy"` (без passphrase).

### Окружение

| Variable | Default | Что |
|----------|---------|-----|
| `DBOS_NAMESPACE` | `dbos` | k8s namespace, где лежит Secret. |
| `DBOS_SECRET_NAME` | `dbos-secrets` | Имя Secret'а. |
| `BACKUP_TARGET_DIR` | `/backup/master-keys` | Куда положить итоговый `.age`/`.gpg`/`.enc`. |
| `BACKUP_PASSPHRASE` | — | Passphrase (опасно: будет в env пайплайна). |
| `BACKUP_AGE_RECIPIENTS` | — | Через запятую, для age publickey-режима. |

### Что получается на выходе

```
/backup/master-keys/
  dbos-master-keys-20260608T143000Z.tar.gz.gpg       (chmod 600)
  dbos-master-keys-20260608T143000Z.tar.gz.gpg.meta  (chmod 600, sha256 + размер)
```

`.meta` — sidecar c `sha256_cipher` / `sha256_plain` / timestamp / encryptor. Используется `restore_master_keys.sh` для верификации.

### Cadence

| Событие | Когда |
|---------|-------|
| Регулярный backup | **раз в месяц минимум**. Master-ключи редко меняются — частый backup не нужен. |
| Внеплановый | сразу после `rotate_master_key.sh` (изменилась активная версия + появился legacy `__v<N>`). |
| Внеплановый | перед любым `gen_secrets.sh` (перегенерация snесёт мастер-ключи). |
| Внеплановый | перед обновлением кластера / миграцией Secret'а. |

### Хранение архива

- **Offsite**: не на той же машине, что и k3s-кластер. Иначе пожар/диск-фейл = одновременная потеря и Secret'а, и backup'а.
- **На отдельном носителе**: оптимально — USB-токен или encrypted USB-drive в физическом сейфе.
- **Минимум две копии**: разные носители, разные локации. Стандартная схема 3-2-1: 3 копии, 2 среды, 1 offsite.
- **Passphrase / age identity** — **не** на том же носителе, что архив. Иначе компрометация носителя = компрометация ключей. Passphrase — в password manager оператора (1Password / KeePassXC / Yubikey + GPG-key).

## Recovery flow при потере master-key

Сценарий: кластер пересоздан / Secret снесли / `gen_secrets.sh` отработал случайно. Encrypted данные в БД остались, master-key пропал.

1. Восстанови архив с offsite-носителя.
2. Проверь sha256 архива против `.meta` (это сделает restore-скрипт автоматически, либо вручную `sha256sum`).
3. Сделай dry-run preview:
   ```bash
   scripts/k8s/restore_master_keys.sh /path/to/dbos-master-keys-*.gpg
   ```
   Скрипт выведет `kubectl apply --dry-run=server` и команду для реального применения.
4. Если preview ок — применяй:
   ```bash
   scripts/k8s/restore_master_keys.sh /path/to/dbos-master-keys-*.gpg --apply
   ```
5. Restart pods, чтобы они перечитали Secret:
   ```bash
   kubectl -n dbos rollout restart deploy/server-service deploy/server-worker
   kubectl -n dbos rollout restart deploy/secret-service deploy/auth-service
   ```
6. Sanity-чек: попробуй прочитать любой server_account (GET через API) — расшифровка должна вернуть исходное значение, а не «invalid ciphertext».

### Recovery при полностью потерянной БД

Если потеряны и Secret, и postgres — backup master-key бесполезен (нечего расшифровывать). Нужен `pg_dump`-backup (`scripts/k8s/backup_pg.sh`). Эти процедуры **дополняют** друг друга: master-keys backup без БД-backup'а ничего не спасает, и наоборот.

### Recovery если потерян ТОЛЬКО `AUTH_SECRET_KEY`

Менее катастрофично:
1. Восстанови `AUTH_SECRET_KEY` из backup'а — все ранее выданные JWT станут валидны снова.
2. Если backup'а нет — сгенерируй новый, juzzy перелогинятся (рефреш-токены тоже сломаются).

## Smoke-test

Проверить, что backup/restore round-trip не теряет данные:

```bash
tests/k8s/test_master_keys_roundtrip.sh
```

Скрипт делает `backup → restore --apply` против тестового namespace, потом сверяет содержимое Secret'а по полям.

## Связанное: проверка pg_dump-бэкапов

Master-keys backup без БД-backup'а бесполезен (нечего расшифровывать). Проверка, что сами pg_dump'ы действительно восстанавливаются, делается отдельным manual-only скриптом — см. `scripts/k8s/README-PG-RESTORE-DRILL.md` и `scripts/k8s/pg_restore_drill.sh`. Запускать quarterly + после правок backup-pipeline.

## Автоматизация: backup-CronJob'ы

Помимо manual-only `backup_master_keys.sh` теперь развёрнуты CronJob'ы:

| CronJob | Schedule (UTC) | Что делает | Manifest |
|---------|----------------|-----------|----------|
| `master-keys-backup`     | `0 4 * * *`   ежедневно        | прогоняет `backup_master_keys.sh`, кладёт `.gpg/.age` в `/backup/master-keys/`, sidecar `offsite-sync` пушит rsync'ом на `OFFSITE_RSYNC_DEST` | `k8s/102-master-keys-backup.yaml` |
| `secret-full-backup`     | `0 5 * * 0`   воскресенье     | полный snapshot Secret'а `dbos-secrets` (DB-пароли + S2S + TLS + master-keys) | `k8s/103-secret-full-backup.yaml` |
| `pg-restore-drill`       | `0 6 1 * *`   1-е число месяца | прогоняет `pg_restore_drill.sh` по 5 БД в throwaway-namespace | `k8s/104-pg-restore-drill.yaml` |

Все три CronJob'а используют один backup-PV (`dbos-backup-pv`, см. `k8s/101-backup-pvc.yaml`) и `serviceAccountName: rotation-runner` (RBAC — `k8s/120-rotation-rbac.yaml`).

### CA backup

`dbos-ca-key-pair` — это **отдельный** Secret cert-manager'а с приватным ключом
внутреннего CA. Без него после DR cert-manager не сможет пере-выпустить
TLS-сертификаты сервисам. `backup_master_keys.sh` забирает его автоматически в
подкаталог `ca/` внутри tar.gz-архива. Имя Secret'а переопределяется через
`DBOS_CA_SECRET_NAME` (default `dbos-ca-key-pair`). При restore через
`restore_master_keys.sh` CA-Secret раскатывается отдельным `kubectl apply -f`.

### Rotation scripts

В DBOS живёт 6 rotation-скриптов + auto-finalize'еры. После каждой ротации
backup нужно обновить — старая legacy-версия ключа должна попасть в архив,
иначе ciphertext'ы, шифрованные под старым ключом, не расшифруются после DR.

| Script | Что ротирует | Двухфазно (lazy re-encrypt + finalize) | `--auto-finalize` поддерживается |
|--------|-------------|-------------|----------------------------------|
| `rotate_master_key.sh`             | `SERVER_ENCRYPTION_KEY` (server_service) | да, через `secrets.reencrypt_lazy` | да |
| `rotate_secret_master_key.sh`      | `SECRET_ENCRYPTION_KEY` (secret_service) | да, через `secrets.reencrypt_lazy` | да |
| `rotate_redis_stash_master_key.sh` | `REDIS_STASH_ENCRYPTION_KEY` (provision creds stash) | да, TTL-gated (≤1ч) | да |
| `rotate_db_passwords.sh`           | DB-пароли (`*_DB_PASSWORD`, per-service `SERVICE=auth\|logging\|server\|worker\|secret`) | — (синхронный ALTER USER + rolling restart) | — |
| `rotate_s2s_keys.sh`               | `SERVICE_API_KEYS`, `INTROSPECT_SERVICE_API_KEY`, `WORKER_BOT_TOKEN` | grace-period (новый + старый, finalize по подтверждению) | — |
| `rotate_redis_password.sh`         | `REDIS_PASSWORD` (общий broker) | — (CONFIG SET + rolling restart) | — |

Полный flow каждого, troubleshooting, `--status`/`--finalize`/`--force-finalize`, identity `rotation_runner`, проверки migration_status — `obsidian/infra/runbooks/Rotation.md`.

Helper-скрипт `_rotation_helpers.sh` (sourced остальными) содержит общий код для проверки `migration_status` и TTL-gating'а. `test_rotation_safety.sh` — orchestrator для CronJob'а `rotation-scheduler` (backup → rotate → smoke → restore-on-fail).

После любой из ротаций **триггернуть** `master-keys-backup` вручную:

```bash
kubectl -n dbos create job --from=cronjob/master-keys-backup \
    master-keys-backup-after-rotation-$(date +%s)
```

Schedule `0 4 * * *` подхватит на следующий день автоматически — manual trigger нужен только если ротация была за несколько часов до catastrophe.

### Passphrase setup

CronJob'ы `master-keys-backup` и `secret-full-backup` читают
`BACKUP_PASSPHRASE` из k8s Secret'а `dbos-backup-passphrase`. Если Secret
отсутствует — backup пишется plaintext'ом с warning'ом.

Создание (после первого деплоя):

```bash
PASS="$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)"
kubectl -n dbos create secret generic dbos-backup-passphrase \
    --from-literal=BACKUP_PASSPHRASE="$PASS"
# Сохрани $PASS в offline-сейфе ДО того, как удалишь переменную.
```

Шаблон: `k8s/106-backup-passphrase.yaml.example`. Полный flow (ротация,
recovery при утере passphrase) — `obsidian/infra/runbooks/Backup-DR.md`,
раздел «Настройка passphrase для шифрованных backup'ов».

Ad-hoc запуск `backup_master_keys.sh` / `backup_secret_full.sh` с
оператор-хоста (вне CronJob'а) теперь сам подтягивает passphrase из
Secret'а — `kubectl` достаточно. Env-override `BACKUP_PASSPHRASE` имеет
приоритет над Secret'ом.
