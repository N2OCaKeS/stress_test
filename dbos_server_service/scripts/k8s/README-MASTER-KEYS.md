# Master-keys backup & restore

Backup и восстановление мастер-ключей шифрования DBOS Server Manager из k8s Secret'а `dbos-secrets`.

## Что хранится в `dbos-secrets`

Полный набор живёт в `k8s/20-secrets.yaml`, генерится `scripts/k8s/gen_secrets.sh`. Master-ключи, без которых **восстановление зашифрованных данных невозможно**:

| Поле | Назначение | Что станет нечитаемым при потере |
|------|------------|----------------------------------|
| `SERVER_ENCRYPTION_KEY` (+ `_VERSION`, + `SERVER_ENCRYPTION_KEY__v<N>` legacy) | Envelope encryption в `server_service` (server_account credentials, IPMI). | Все `server_account.encrypted_*` и IPMI-секреты. |
| `SECRET_ENCRYPTION_KEY` (+ `_VERSION`, + legacy `__v<N>`) | Envelope encryption в `secret_service` (bot tokens, s2s API keys). | Хранилище секретов целиком. |
| `REDIS_STASH_ENCRYPTION_KEY` (+ `_VERSION`, + legacy `__v<N>`) | Шифрование Redis-стэшей (provision creds и т.п.) — если включено. | Содержимое stash'ей; обычно ephemeral, но при инциденте полезно иметь. |
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
