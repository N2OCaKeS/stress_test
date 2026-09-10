#!/usr/bin/env python3
"""Dev seed: патчит postgres напрямую (без REST API).

Стек должен быть уже поднят (`make up`). Скрипт:
  - обновляет bootstrap-админа (must_change_password=false, пароль 1),
  - заводит 4 новых юзера: loging_admin1 / dep_admin1 / loging_reader1 / user1,
  - создаёт отдел «Нагрузочное тестирование»,
  - выдаёт отделу доступ ко всем платформенным сервисам,
  - сидит каталог OS-версий (`os_versions`),
  - создаёт один сервер test-server-01, указывающий на контейнер test_server
    (ssh_port=2222, привязан к debian OS-record),
  - заводит OS-аккаунт `tester` (пароль `tester1234`) на этом сервере с
    шифрованием через server_service AES-GCM,
  - досевает системную роль `admin` в каталоге `service_role_definitions`
    для отдела НТ по всем сервисам,
  - заводит три credential'а в secret_service (dev_jira_token,
    dev_postgres_password, dev_loadgen_secret).

Bot воркера (`server_worker`) в dev НЕ создаётся этим скриптом: его заводит
auth_service на старте из `WORKER_BOT_TOKEN` в системном отделе `DBOS System`
— тот же путь, что и в prod (см. `bootstrap_service.bootstrap_worker_bot`).

Пароли argon2-хешируются внутри контейнера auth_service (там лежит ровно та
библиотека, что и проверяет логин). Шифрование кред — внутри контейнера
secret_service (ему нужен мастер-ключ из ENV). Пароль server_account
шифруется внутри server_service (своим ключом + AAD по account_id).

Идемпотентность: тестовые записи удаляются до вставки, чтобы повторный запуск
поверх уже наполненной БД не падал на UNIQUE.
"""

import os
import secrets
import subprocess
import sys

import psycopg

PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("PG_USER", "app_user")
PG_PASS = os.environ.get("PG_PASS", "app_password")
SECRET_PG_HOST = os.environ.get("SECRET_PG_HOST", "localhost")
SECRET_PG_PORT = int(os.environ.get("SECRET_PG_PORT", "5435"))

AUTH_CONTAINER = os.environ.get("AUTH_CONTAINER", "emm-auth_service-1")
SECRET_CONTAINER = os.environ.get(
    "SECRET_CONTAINER", "emm-secret_service-1"
)
SERVER_CONTAINER = os.environ.get(
    "SERVER_CONTAINER", "emm-server_service-1"
)

DEV_PASSWORD = "1"

# Имя docker-сервиса с тестовым SSH-сервером (см. docker-compose.dev.yml).
TEST_SERVER_HOSTNAME = "test_server"
# `tester` / `tester1234` — bootstrap-юзер, которого заводит entrypoint
# контейнера (BOOTSTRAP_USER / BOOTSTRAP_PASSWORD); он в группе sudo. prepare
# заходит под ним по паролю и поднимает сервер в managed-режим.
TEST_SERVER_LOGIN = "tester"
TEST_SERVER_PASSWORD = "tester1234"
# Контейнер слушает 2222, снаружи проброшен 2222:2222.
TEST_SERVER_SSH_PORT = 2222
# Тестовый сервер живёт в той же docker-сети, что и наши сервисы.
# INET-колонке нужна валидная IP-строка; реальный IP контейнер получает
# из подсети, выданной docker'ом — worker внутри сети резолвит
# `test_server` по docker DNS, IP здесь — плейсхолдер для not-null.
TEST_SERVER_IP = "10.99.0.10"

# Каталог OS-версий — пока одна запись под debian-образ тестового сервера.
# Когда заведём реальную Astra/Ubuntu — добавим рядом.
OS_VERSION_NAME = "debian-stable"

# UI-имена платформенных сервисов (должны совпадать с теми, что сидятся
# через bootstrap auth_service'а — см. docker-compose env'ы и
# `core/constants` сервиса). Если platform_services пустая — заполним сами.
PLATFORM_SERVICES = [
    ("auth_service", "Аутентификация и управление аккаунтами"),
    ("server_service", "Инвентаризация и управление серверами"),
    ("loging_service", "Аудит и журналирование событий"),
    ("secret_service", "Хранилище токенов и учётных данных"),
    ("testing_service", "Каталог тестов, очередь запуска, СТП (allta_app)"),
]

WIDTH = 64


def section(title: str) -> None:
    print(f"\n{'─' * WIDTH}")
    print(f"  {title}")
    print(f"{'─' * WIDTH}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}", file=sys.stderr)


def _docker_exec(container: str, code: str) -> str:
    """Запустить python-snippet внутри указанного контейнера и вернуть stdout."""
    res = subprocess.run(
        ["docker", "exec", container, "python", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        fail(
            f"docker exec {container} вернул {res.returncode}:\n"
            f"--- stderr ---\n{res.stderr}\n--- stdout ---\n{res.stdout}"
        )
        sys.exit(1)
    return res.stdout.strip()


def hash_password(plain: str) -> str:
    """Argon2-хеш в формате, который понимает auth_service."""
    return _docker_exec(
        AUTH_CONTAINER,
        f"from src.core.security import hash_password; print(hash_password({plain!r}))",
    )


def encrypt_secret(cred_id: str, plaintext: str) -> str:
    """AES-GCM шифр для credentials.secret_encrypted (envelope `v<v>$<n>$<ct>`)."""
    code = (
        "from src.services.secrets_service import encrypt, aad_for_credential; "
        f"print(encrypt({plaintext!r}, aad=aad_for_credential({cred_id!r})))"
    )
    return _docker_exec(SECRET_CONTAINER, code)


def encrypt_account_password(account_id: str, plaintext: str) -> str:
    """AES-GCM шифр для server_accounts.password_encrypted.

    Ключ + AAD считываются внутри server_service'а — у seed'ера не должно
    быть копии мастер-ключа. `aad_for_server_account_password(account_id)`
    привязывает ciphertext к строке: подмена в другую строку = InvalidTag.
    """
    code = (
        "from src.services.secrets_service import encrypt, "
        "aad_for_server_account_password; "
        f"print(encrypt({plaintext!r}, "
        f"aad=aad_for_server_account_password({account_id!r})))"
    )
    return _docker_exec(SERVER_CONTAINER, code)


def conn(host: str, port: int, dbname: str) -> psycopg.Connection:
    return psycopg.connect(
        host=host,
        port=port,
        user=PG_USER,
        password=PG_PASS,
        dbname=dbname,
        autocommit=True,
    )


def gen_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


# ── auth_service ─────────────────────────────────────────────────────────────


def seed_auth(pwd_hash: str) -> tuple[str, dict[str, str]]:
    """Засеять отдел + 5 юзеров. Вернуть (dept_id, {username: user_id})."""
    section("auth_service: отдел, юзеры, доступ к сервисам")
    user_ids: dict[str, str] = {}
    dept_name = "Нагрузочное тестирование"
    new_usernames = ("loging_admin1", "dep_admin1", "loging_reader1", "user1")

    with conn(PG_HOST, PG_PORT, "dev_auth") as c, c.cursor() as cur:
        # Cleanup тестовых записей (без bootstrap-админа).
        cur.execute(
            "DELETE FROM users WHERE username = ANY(%s)",
            (list(new_usernames),),
        )
        cur.execute(
            "DELETE FROM departments WHERE name = %s",
            (dept_name,),
        )

        # Bootstrap-админ: обновить пароль и сбросить must_change_password.
        cur.execute(
            "UPDATE users SET password_hash=%s, must_change_password=false "
            "WHERE username='admin' RETURNING id",
            (pwd_hash,),
        )
        row = cur.fetchone()
        if not row:
            fail(
                "bootstrap-админ 'admin' не найден в БД — поднят ли auth_service "
                "с INITIAL_ADMIN_USERNAME=admin?"
            )
            sys.exit(1)
        user_ids["admin"] = row[0]
        ok(f"admin (bootstrap) → пароль обновлён, must_change=false ({row[0]})")

        # Каталог сервисов: дозаполним, если пуст.
        cur.execute("SELECT service_name FROM platform_services")
        existing_services = {r[0] for r in cur.fetchall()}
        for svc_name, desc in PLATFORM_SERVICES:
            if svc_name in existing_services:
                continue
            cur.execute(
                "INSERT INTO platform_services (service_name, description, is_active) "
                "VALUES (%s, %s, true)",
                (svc_name, desc),
            )
            ok(f"platform_service {svc_name}")

        # Отдел НТ.
        dept_id = gen_id("dep")
        cur.execute(
            "INSERT INTO departments (id, name, description, is_active) "
            "VALUES (%s, %s, %s, true)",
            (dept_id, dept_name, "Dev seed: отдел Нагрузочного тестирования"),
        )
        ok(f"отдел «{dept_name}» ({dept_id})")

        # Доступ отдела ко всем сервисам.
        for svc_name, _ in PLATFORM_SERVICES:
            cur.execute(
                "INSERT INTO department_service_access "
                "(id, department_id, service_name, is_active, granted_by) "
                "VALUES (%s, %s, %s, true, %s) ON CONFLICT DO NOTHING",
                (gen_id("dsa"), dept_id, svc_name, user_ids["admin"]),
            )
        ok(f"отделу выданы доступы к {len(PLATFORM_SERVICES)} сервисам")

        # Новые юзеры.
        new_users = [
            ("loging_admin1",  "loging_admin",      None),
            ("dep_admin1",     "department_admin",  dept_id),
            ("loging_reader1", "loging_reader",     None),
            ("user1",          None,                dept_id),
        ]
        for username, role, dept in new_users:
            uid = gen_id("usr")
            cur.execute(
                "INSERT INTO users "
                "(id, username, email, password_hash, department_id, platform_role, "
                " must_change_password, is_active, status, failed_login_attempts, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, false, true, 'active', 0, %s)",
                (
                    uid,
                    username,
                    f"{username}@dbos.local",
                    pwd_hash,
                    dept,
                    role,
                    "seed",
                ),
            )
            user_ids[username] = uid
            label = role or "regular"
            ok(f"user {username} ({label}, dept={dept or '—'}) → {uid}")

    return dept_id, user_ids


# ── server_service ───────────────────────────────────────────────────────────


def seed_os_version() -> str:
    """Сидим одну запись каталога OS под debian-образ тестового сервера.

    Делаем это до `seed_server`, чтобы сразу прицепить сервер к OS-version
    через `os_version_id`. На реальном prepare/inventory worker допишет
    остальные строки (Astra/Ubuntu), здесь — только placeholder, чтобы
    каталог не был пустым и FK сервер→OS можно было подёргать в UI.
    """
    section("server_service: каталог OS-версий")
    with conn(PG_HOST, PG_PORT, "dev_server") as c, c.cursor() as cur:
        # На реинъекции old-row держит FK от прошлого test-server'а — не
        # сносим, а UPSERT'им: id остаётся тот же, описание перетирается.
        cur.execute("SELECT id FROM os_versions WHERE name = %s", (OS_VERSION_NAME,))
        row = cur.fetchone()
        if row:
            os_id = row[0]
            cur.execute(
                "UPDATE os_versions SET description = %s, repositories = %s, "
                "updated_at = now() WHERE id = %s",
                (
                    "Dev seed: debian-образ (тестовый SSH-таргет)",
                    [],
                    os_id,
                ),
            )
        else:
            os_id = gen_id("osv")
            cur.execute(
                "INSERT INTO os_versions (id, name, description, repositories) "
                "VALUES (%s, %s, %s, %s)",
                (
                    os_id,
                    OS_VERSION_NAME,
                    "Dev seed: debian-образ (тестовый SSH-таргет)",
                    [],
                ),
            )
    ok(f"os_version {OS_VERSION_NAME} ({os_id})")
    return os_id


def seed_server(dept_id: str, created_by: str, os_version_id: str | None) -> str:
    section("server_service: один тестовый сервер")
    server_id = gen_id("srv")
    with conn(PG_HOST, PG_PORT, "dev_server") as c, c.cursor() as cur:
        cur.execute("DELETE FROM servers WHERE hostname=%s", (TEST_SERVER_HOSTNAME,))
        # is_managed=false осознанно. Управляющий пользователь + ключ
        # выкатываются на сервер только через `prepare`-flow; до него worker
        # заходит самим аккаунтом по паролю (см.
        # `_account_helpers.resolve_ssh_creds`) — это сценарий
        # tester/tester1234 на test_server'е. Управляющую SSH-пару и пароль
        # server_service генерит сам при prepare (per-server, шифрует в своей
        # БД) — глобального ключа в стенде больше нет, стартовое состояние
        # оставляем неуправляемым.
        cur.execute(
            "INSERT INTO servers "
            "(id, hostname, display_name, ip_address, ssh_port, os_version_id, "
            " department_id, status, power_state, busy_state, is_managed, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'unknown', 'unknown', 'free', false, %s)",
            (
                server_id,
                TEST_SERVER_HOSTNAME,
                "test-server-01",
                TEST_SERVER_IP,
                TEST_SERVER_SSH_PORT,
                os_version_id,
                dept_id,
                created_by,
            ),
        )
    ok(
        f"server test-server-01 ({server_id}) → {TEST_SERVER_HOSTNAME}:"
        f"{TEST_SERVER_SSH_PORT} (os={OS_VERSION_NAME})"
    )
    return server_id


def seed_server_account(server_id: str, dept_id: str, created_by: str) -> str:
    """OS-аккаунт `tester` для test-server-01.

    Пароль шифруется AES-GCM внутри server_service (docker exec), потом
    кладём строку прямо в `server_accounts` + `server_account_servers`.
    Поднимать API ради одного INSERT'а смысла нет: для create-эндпоинта
    нужна service-role с action=create, которой у админа платформы нет
    (account_admin не имеет department-scoped service-ролей), а dep_admin1
    в auth'е тоже без роли. SQL — короче и совпадает с общим стилем seed'а.
    """
    section("server_service: OS-аккаунт tester на test-server-01")
    account_id = gen_id("acc")
    link_id = gen_id("acs")
    encrypted = encrypt_account_password(account_id, TEST_SERVER_PASSWORD)
    with conn(PG_HOST, PG_PORT, "dev_server") as c, c.cursor() as cur:
        # Идемпотентность по login на сервере — снимаем join и сам аккаунт,
        # если seed запускается поверх старой БД.
        cur.execute(
            "DELETE FROM server_accounts a USING server_account_servers s "
            "WHERE a.id = s.account_id AND s.server_id = %s AND s.login = %s",
            (server_id, TEST_SERVER_LOGIN),
        )
        cur.execute(
            "INSERT INTO server_accounts "
            "(id, department_id, login, source, password_encrypted, has_sudo, "
            " unix_groups, is_active, created_by) "
            "VALUES (%s, %s, %s, 'managed', %s, true, %s, true, %s)",
            (
                account_id,
                dept_id,
                TEST_SERVER_LOGIN,
                encrypted,
                [],
                created_by,
            ),
        )
        cur.execute(
            "INSERT INTO server_account_servers "
            "(id, account_id, server_id, login, present_on_server) "
            "VALUES (%s, %s, %s, %s, true)",
            (link_id, account_id, server_id, TEST_SERVER_LOGIN),
        )
    ok(
        f"server_account tester ({account_id}) → server={server_id}, "
        "sudo=true, пароль зашифрован"
    )
    return account_id


# ── auth: service_role_definitions ──────────────────────────────────────────


def seed_service_role_assignments(dept_admin_id: str) -> None:
    """Назначить dep_admin1 `admin`-роль на каждом сервисе.

    Без этого dep_admin1 имеет только platform-роль `department_admin`,
    которая в платформенных middleware'ах считается «не account_admin»,
    но в матрице server_service action-permission'ов её нет — все
    server_account/inventory/drift POST'ы возвращают 403 PERMISSION_DENIED.

    Назначаем явно `admin` на все 4 сервиса отдела — для UI это «полный
    доступ» в рамках dept'а.
    """
    section("auth_service: dep_admin1 → admin@*")
    with conn(PG_HOST, PG_PORT, "dev_auth") as c, c.cursor() as cur:
        cur.execute(
            "DELETE FROM user_service_roles WHERE user_id = %s",
            (dept_admin_id,),
        )
        for svc_name, _ in PLATFORM_SERVICES:
            cur.execute(
                "INSERT INTO user_service_roles "
                "(id, user_id, service_name, role, is_active, assigned_by) "
                "VALUES (%s, %s, %s, 'admin', true, %s)",
                (gen_id("usr"), dept_admin_id, svc_name, dept_admin_id),
            )
            ok(f"dep_admin1 → admin@{svc_name}")


def seed_service_role_defs(dept_id: str, admin_id: str) -> None:
    """Досевание каталога `service_role_definitions` для отдела НТ.

    Штатный путь через `grant_service_access` (auth API) автоматически
    зовёт `seed_system_admin` — но мы выдаём departmental access прямой
    вставкой в `department_service_access`, минуя сервисный слой. Поэтому
    `admin` и `worker_bot` row'ы в каталоге надо завести руками.

    Сидим для всех сервисов, к которым у отдела есть доступ:
      * `admin` — системная роль (`is_system=True`), чтобы её нельзя было
        удалить через API. Любой `dep_admin` через UI сможет навешивать
        её на юзеров.

    Роль `worker_bot` здесь не заводим: воркер живёт в системном отделе
    `DBOS System`, а не в бизнес-отделе НТ — его роль сеет bootstrap
    auth_service'а вместе с ботом.
    """
    section("auth_service: системные service_role_definitions")
    with conn(PG_HOST, PG_PORT, "dev_auth") as c, c.cursor() as cur:
        for svc_name, _ in PLATFORM_SERVICES:
            # Idempotency: каждый прогон seed'а пересоздаёт roleset с нуля,
            # чтобы FK на user_service_roles не залипал на старых dept_id.
            cur.execute(
                "DELETE FROM service_role_definitions "
                "WHERE department_id = %s AND service_name = %s",
                (dept_id, svc_name),
            )
            cur.execute(
                "INSERT INTO service_role_definitions "
                "(id, department_id, service_name, role_name, description, "
                " is_active, is_system, created_by) "
                "VALUES (%s, %s, %s, 'admin', %s, true, true, %s)",
                (
                    gen_id("srd"),
                    dept_id,
                    svc_name,
                    "Full administrative access to the service",
                    admin_id,
                ),
            )
            ok(f"role admin@{svc_name} (is_system)")


# ── auth: bot + PAT для server_worker'а ─────────────────────────────────────


# ── secret_service ───────────────────────────────────────────────────────────


def seed_secrets(dept_id: str, user_ids: dict[str, str]) -> None:
    section("secret_service: 3 кредa (jira/postgres/loadgen)")
    admin_id = user_ids["admin"]
    user1_id = user_ids["user1"]

    secrets_spec = [
        # name,                    service,        scope,        owner_user, owner_dept, owner_user_dept, login,        plaintext
        ("dev_jira_token",         "jira",         "department", None,        dept_id,    None,            None,         "dev-jira-token-value-1234"),
        ("dev_postgres_password",  "postgres",     "department", None,        dept_id,    None,            "postgres",   "dev-postgres-password-1234"),
        ("dev_loadgen_secret",     "loadgen",      "personal",   user1_id,    None,       dept_id,         "user1",      "dev-loadgen-secret-1234"),
    ]

    with conn(SECRET_PG_HOST, SECRET_PG_PORT, "dev_secret") as c, c.cursor() as cur:
        # Cleanup по именам из seed.
        cur.execute(
            "DELETE FROM credentials WHERE name = ANY(%s)",
            ([s[0] for s in secrets_spec],),
        )
        for name, svc, scope, owner_user, owner_dept, owner_user_dept, login, plain in secrets_spec:
            cred_id = gen_id("cred")
            encrypted = encrypt_secret(cred_id, plain)
            cur.execute(
                "INSERT INTO credentials "
                "(id, name, service, scope, owner_user_id, owner_dept_id, owner_user_dept_id, "
                " login, secret_encrypted, status, created_by, visible_to_dept) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, false)",
                (
                    cred_id,
                    name,
                    svc,
                    scope,
                    owner_user,
                    owner_dept,
                    owner_user_dept,
                    login,
                    encrypted,
                    admin_id if scope != "personal" else user1_id,
                ),
            )
            owner = f"user={owner_user}" if owner_user else f"dept={owner_dept}"
            ok(f"credential {name} ({scope}, {owner}) → {cred_id}")


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    print()
    print("=" * WIDTH)
    print("  DBOS dev seed (direct SQL)")
    print("=" * WIDTH)

    section("argon2 hash для пароля 1")
    pwd_hash = hash_password(DEV_PASSWORD)
    ok(f"hash: {pwd_hash[:48]}…")

    dept_id, user_ids = seed_auth(pwd_hash)
    seed_service_role_defs(dept_id, admin_id=user_ids["admin"])
    seed_service_role_assignments(dept_admin_id=user_ids["dep_admin1"])
    os_version_id = seed_os_version()
    server_id = seed_server(
        dept_id, created_by=user_ids["admin"], os_version_id=os_version_id,
    )
    seed_server_account(server_id, dept_id, created_by=user_ids["admin"])
    seed_secrets(dept_id, user_ids)

    print()
    print("=" * WIDTH)
    print("  Готово. Пользователи (пароль у всех: 1):")
    print("=" * WIDTH)
    print(f"""
    {'Логин':<16}  {'Роль':<22}  Отдел
    {'─' * 16}  {'─' * 22}  {'─' * 32}
    {'admin':<16}  {'account_admin':<22}  —
    {'loging_admin1':<16}  {'loging_admin':<22}  —
    {'dep_admin1':<16}  {'department_admin':<22}  Нагрузочное тестирование
    {'loging_reader1':<16}  {'loging_reader':<22}  —
    {'user1':<16}  {'regular':<22}  Нагрузочное тестирование

  Server:     test-server-01 → {TEST_SERVER_HOSTNAME}:{TEST_SERVER_SSH_PORT} (контейнер test_server)
              OS-version: {OS_VERSION_NAME}, account: tester (sudo, пароль зашифрован)
  Worker bot: server_worker (системный отдел «DBOS System») — заведён
              auth_service'ом на старте из WORKER_BOT_TOKEN, не этим seed'ом.
  Credentials: dev_jira_token, dev_postgres_password (dept НТ),
               dev_loadgen_secret (personal user1)
""")


if __name__ == "__main__":
    main()
