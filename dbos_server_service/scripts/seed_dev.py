#!/usr/bin/env python3
"""Dev seed: патчит postgres напрямую (без REST API).

Стек должен быть уже поднят (`make up`). Скрипт:
  - обновляет bootstrap-админа (must_change_password=false, пароль 1234),
  - заводит 4 новых юзера: loging_admin1 / dep_admin1 / loging_reader1 / user1,
  - создаёт отдел «Нагрузочное тестирование»,
  - выдаёт отделу доступ ко всем платформенным сервисам,
  - создаёт один сервер test-server-01, указывающий на контейнер test_server,
  - заводит три credential'а в secret_service (dev_jira_token, dev_postgres_password,
    dev_loadgen_secret).

Пароли argon2-хешируются внутри контейнера auth_service (там лежит ровно та
библиотека, что и проверяет логин). Шифрование кред — внутри контейнера
secret_service (ему нужен мастер-ключ из ENV).

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

AUTH_CONTAINER = os.environ.get("AUTH_CONTAINER", "dbos_server_service-auth_service-1")
SECRET_CONTAINER = os.environ.get(
    "SECRET_CONTAINER", "dbos_server_service-secret_service-1"
)

DEV_PASSWORD = "1234"

# Имя docker-сервиса с openssh-server (см. docker-compose.dev.yml).
TEST_SERVER_HOSTNAME = "test_server"
# Тестовый сервер живёт в той же docker-сети, что и наши сервисы.
# INET-колонке нужна валидная IP-строка; реальный IP контейнер получает
# из подсети, выданной docker'ом — для seed-данных подойдёт любой валидный
# адрес-плейсхолдер (worker сюда никуда ходить не будет).
TEST_SERVER_IP = "10.99.0.10"

# UI-имена платформенных сервисов (должны совпадать с теми, что сидятся
# через bootstrap auth_service'а — см. docker-compose env'ы и
# `core/constants` сервиса). Если platform_services пустая — заполним сами.
PLATFORM_SERVICES = [
    ("auth_service", "Аутентификация и управление аккаунтами"),
    ("server_service", "Инвентаризация и управление серверами"),
    ("loging_service", "Аудит и журналирование событий"),
    ("secret_service", "Хранилище токенов и учётных данных"),
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


def seed_server(dept_id: str, created_by: str) -> str:
    section("server_service: один тестовый сервер")
    server_id = gen_id("srv")
    with conn(PG_HOST, PG_PORT, "dev_server") as c, c.cursor() as cur:
        cur.execute("DELETE FROM servers WHERE hostname=%s", (TEST_SERVER_HOSTNAME,))
        cur.execute(
            "INSERT INTO servers "
            "(id, hostname, display_name, ip_address, ssh_port, department_id, "
            " status, power_state, busy_state, is_managed, created_by) "
            "VALUES (%s, %s, %s, %s, 22, %s, 'unknown', 'unknown', 'free', false, %s)",
            (
                server_id,
                TEST_SERVER_HOSTNAME,
                "test-server-01",
                TEST_SERVER_IP,
                dept_id,
                created_by,
            ),
        )
    ok(f"server test-server-01 ({server_id}) → {TEST_SERVER_HOSTNAME}:{TEST_SERVER_IP}")
    return server_id


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

    section("argon2 hash для пароля 1234")
    pwd_hash = hash_password(DEV_PASSWORD)
    ok(f"hash: {pwd_hash[:48]}…")

    dept_id, user_ids = seed_auth(pwd_hash)
    seed_server(dept_id, created_by=user_ids["admin"])
    seed_secrets(dept_id, user_ids)

    print()
    print("=" * WIDTH)
    print("  Готово. Пользователи (пароль у всех: 1234):")
    print("=" * WIDTH)
    print(f"""
    {'Логин':<16}  {'Роль':<22}  Отдел
    {'─' * 16}  {'─' * 22}  {'─' * 32}
    {'admin':<16}  {'account_admin':<22}  —
    {'loging_admin1':<16}  {'loging_admin':<22}  —
    {'dep_admin1':<16}  {'department_admin':<22}  Нагрузочное тестирование
    {'loging_reader1':<16}  {'loging_reader':<22}  —
    {'user1':<16}  {'regular':<22}  Нагрузочное тестирование

  Server:     test-server-01 → {TEST_SERVER_HOSTNAME}:22 (контейнер test_server)
  Credentials: dev_jira_token, dev_postgres_password (dept НТ),
               dev_loadgen_secret (personal user1)
""")


if __name__ == "__main__":
    main()
