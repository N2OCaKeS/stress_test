#!/usr/bin/env python3
"""Dev seed: патчит postgres напрямую (без REST API).

Стек должен быть уже поднят (`make up`). Скрипт:
  - обновляет bootstrap-админа (must_change_password=false, пароль 1234),
  - заводит 4 новых юзера: loging_admin1 / dep_admin1 / loging_reader1 / user1,
  - создаёт отдел «Нагрузочное тестирование»,
  - выдаёт отделу доступ ко всем платформенным сервисам,
  - сидит каталог OS-версий (`os_versions`),
  - создаёт один сервер test-server-01, указывающий на контейнер test_server
    (ssh_port=2222, привязан к openssh-server OS-record),
  - заводит OS-аккаунт `tester` (пароль `tester1234`) на этом сервере с
    шифрованием через server_service AES-GCM,
  - досевает системные роли (`admin`, `worker_bot`) в каталоге
    `service_role_definitions` для отдела НТ × `server_service`,
  - заводит bot'а `worker_bot_nt` с ролью `worker_bot` и выдаёт ему PAT,
    пишет в `.dev/.worker_pat` для server_worker'а (после seed нужно
    `docker compose restart server_worker`),
  - заводит три credential'а в secret_service (dev_jira_token,
    dev_postgres_password, dev_loadgen_secret).

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
import time

import httpx
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
SERVER_CONTAINER = os.environ.get(
    "SERVER_CONTAINER", "dbos_server_service-server_service-1"
)
WORKER_CONTAINER = os.environ.get(
    "WORKER_CONTAINER", "dbos_server_service-server_worker-1"
)

DEV_PASSWORD = "1234"
AUTH_BASE_URL = os.environ.get("AUTH_BASE_URL", "http://localhost:8000")

# Имя docker-сервиса с openssh-server (см. docker-compose.dev.yml).
TEST_SERVER_HOSTNAME = "test_server"
# `tester` / `tester1234` — это креды, которые принимает linuxserver/openssh-server
# по env'ам USER_NAME / USER_PASSWORD; sudo есть (SUDO_ACCESS=true).
TEST_SERVER_LOGIN = "tester"
TEST_SERVER_PASSWORD = "tester1234"
# Контейнер слушает 2222 (linuxserver image), снаружи проброшен 2222:2222.
TEST_SERVER_SSH_PORT = 2222
# Тестовый сервер живёт в той же docker-сети, что и наши сервисы.
# INET-колонке нужна валидная IP-строка; реальный IP контейнер получает
# из подсети, выданной docker'ом — worker внутри сети резолвит
# `test_server` по docker DNS, IP здесь — плейсхолдер для not-null.
TEST_SERVER_IP = "10.99.0.10"

# Каталог OS-версий — пока одна запись под образ linuxserver/openssh-server.
# Когда заведём реальную Astra/Ubuntu — добавим рядом.
OS_VERSION_NAME = "openssh-server-latest"

# Путь к PAT-файлу для server_worker'а — docker-compose.dev.yml
# монтирует `./.dev` как `/shared`. Файл читается worker'ом при старте
# и кладётся в env WORKER_BOT_TOKEN.
WORKER_PAT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".dev")
WORKER_PAT_FILE = os.path.join(WORKER_PAT_DIR, ".worker_pat")
WORKER_BOT_NAME = "worker_bot_nt"

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
        # Стереть seed-бота — иначе он держит FK на dept, и DELETE
        # departments падает FK violation'ом. Его токены/роли уйдут
        # каскадом по FK ondelete=CASCADE.
        cur.execute(
            "DELETE FROM bot_accounts WHERE name = %s",
            (WORKER_BOT_NAME,),
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
    """Сидим одну запись каталога OS под образ linuxserver/openssh-server.

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
                    "Dev seed: образ linuxserver/openssh-server (тестовый SSH-таргет)",
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
                    "Dev seed: образ linuxserver/openssh-server (тестовый SSH-таргет)",
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
        # выкатываются только через `prepare`-flow, у worker'а в dev-стеке
        # ssh_management_private_key_path не настроен — managed-сессия упала
        # бы в SSH_MANAGEMENT_KEY_MISSING. Под `false` worker заходит самим
        # аккаунтом по паролю (см. `_account_helpers.resolve_ssh_creds`), что
        # как раз сценарий tester/tester1234 на test_server'е.
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
      * `worker_bot` — только для server_service: нужна для bot'а воркера
        (см. `seed_worker_bot` ниже).
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
        # worker_bot нужен только для server_service — за пределами этого
        # сервиса entity_permissions для роли пустой, давать её бесполезно.
        cur.execute(
            "INSERT INTO service_role_definitions "
            "(id, department_id, service_name, role_name, description, "
            " is_active, is_system, created_by) "
            "VALUES (%s, %s, 'server_service', 'worker_bot', %s, "
            " true, true, %s)",
            (
                gen_id("srd"),
                dept_id,
                "Worker bot least-privilege role (internal callbacks)",
                admin_id,
            ),
        )
        ok("role worker_bot@server_service (is_system)")


# ── auth: bot + PAT для server_worker'а ─────────────────────────────────────


# httpx по умолчанию забирает прокси из env (HTTP_PROXY/HTTPS_PROXY) —
# у разработчика на хосте часто стоит локальный SOCKS/HTTP-прокси (например
# clash на 127.0.0.1:7897), который на запросы в localhost:8000 отдаёт 502.
# Дев-стек поднят в той же машине; явно сбрасываем прокси для seed-клиентов.
_HTTPX_KW = {"trust_env": False}


def _http_get(url: str, **kw: object) -> httpx.Response:
    return httpx.get(url, **_HTTPX_KW, **kw)  # type: ignore[arg-type]


def _http_post(url: str, **kw: object) -> httpx.Response:
    return httpx.post(url, **_HTTPX_KW, **kw)  # type: ignore[arg-type]


def _http_client(**kw: object) -> httpx.Client:
    return httpx.Client(**_HTTPX_KW, **kw)  # type: ignore[arg-type]


def _wait_auth_healthy(retries: int = 30) -> None:
    """Дождаться, пока auth_service ответит 200 на /health.

    Внутри `make seed` стек только что поднят — health-проверка docker'а
    уже его проверила, но если seed запускается отдельно, добавим
    короткий retry чтобы не падать на networking-flake'е первого вызова.
    """
    for _ in range(retries):
        try:
            r = _http_get(f"{AUTH_BASE_URL}/api/auth/v1/health", timeout=3)
            if r.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)
    fail(f"auth_service не отвечает на {AUTH_BASE_URL}/api/auth/v1/health")
    sys.exit(1)


def _admin_login() -> str:
    r = _http_post(
        f"{AUTH_BASE_URL}/api/auth/v1/login",
        json={"username": "admin", "password": DEV_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200:
        fail(f"admin login failed: {r.status_code} {r.text}")
        sys.exit(1)
    return r.json()["access_token"]


def seed_worker_bot(dept_id: str) -> None:
    """Завести bot'а воркера, выдать ему `worker_bot`-роль и записать PAT.

    После seed'а нужно `docker compose restart server_worker`, потому что
    worker читает `/shared/.worker_pat` ровно один раз — на старте (см.
    docker-compose.dev.yml). seed это не делает, чтобы не зависеть от
    docker compose CLI в окружении и не дёргать рантайм за seed'ом.

    Идемпотентность: bot с таким именем удаляется до создания (вместе с
    его токенами/ролями через FK ondelete=CASCADE).
    """
    section("auth_service: bot worker_bot_nt + PAT для server_worker'а")
    _wait_auth_healthy()
    token = _admin_login()
    client = _http_client(
        base_url=AUTH_BASE_URL,
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )

    # Снести bot'а с этим именем, если он остался от предыдущего прогона.
    # /bots возвращает либо list, либо envelope с items — нормализуем.
    # Имена ботов уникальны глобально, поэтому ищем без фильтра по dept'у:
    # прошлый прогон seed'а мог остаться с bot'ом в уже не существующем
    # dept_id, и фильтр по новому dept_id его не найдёт — POST упёрся бы
    # в BOT_NAME_TAKEN.
    existing_id: str | None = None
    r = client.get("/api/auth/v1/bots")
    if r.status_code == 200:
        data = r.json()
        items = data.get("items") if isinstance(data, dict) else data
        for b in items or []:
            if b.get("name") == WORKER_BOT_NAME:
                existing_id = b.get("bot_id") or b.get("id")
                break
    if existing_id:
        r = client.delete(f"/api/auth/v1/bots/{existing_id}")
        if r.status_code not in (200, 204, 404):
            fail(f"failed to delete existing bot {existing_id}: {r.status_code} {r.text}")
            sys.exit(1)
        ok(f"старый bot {WORKER_BOT_NAME} ({existing_id}) удалён")

    r = client.post(
        "/api/auth/v1/bots",
        json={
            "name": WORKER_BOT_NAME,
            "department_id": dept_id,
            "allowed_services": ["server_service"],
            "description": "Dev seed: PAT для server_worker'а",
        },
    )
    if r.status_code not in (200, 201):
        fail(f"create bot failed: {r.status_code} {r.text}")
        sys.exit(1)
    bot_id = r.json()["bot_id"]
    ok(f"bot {WORKER_BOT_NAME} ({bot_id})")

    r = client.post(
        f"/api/auth/v1/bots/{bot_id}/roles",
        json={"service_name": "server_service", "roles": ["worker_bot"]},
    )
    if r.status_code not in (200, 201, 204):
        fail(f"assign worker_bot role failed: {r.status_code} {r.text}")
        sys.exit(1)
    ok("роль worker_bot@server_service назначена")

    # Политика auth'а требует expires_at ≤ 6 месяцев. Берём ровно 6 мес
    # (180 дней) — для dev-стека хватит надолго, для prod'а PAT всё равно
    # выписывается отдельно.
    from datetime import datetime, timedelta, timezone
    expires_at = (datetime.now(timezone.utc) + timedelta(days=180)).isoformat()
    r = client.post(
        f"/api/auth/v1/bots/{bot_id}/tokens",
        json={
            "name": f"dev_worker_pat_{int(time.time())}",
            "expires_at": expires_at,
        },
    )
    if r.status_code not in (200, 201):
        fail(f"issue PAT failed: {r.status_code} {r.text}")
        sys.exit(1)
    pat = r.json().get("token")
    if not pat:
        fail(f"PAT response без поля 'token': {r.json()}")
        sys.exit(1)

    os.makedirs(WORKER_PAT_DIR, exist_ok=True)
    # Старый PAT мог быть оставлен с владельцем root (если предыдущая
    # версия seed писала из-под container'а). Открыть его на запись
    # под текущим юзером не получится. Unlink работает по правам
    # директории — её владеет mfilippenko, поэтому unlink проходит,
    # после чего создаём свежий файл с 0600.
    try:
        os.unlink(WORKER_PAT_FILE)
    except FileNotFoundError:
        pass
    fd = os.open(WORKER_PAT_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, pat.encode("ascii"))
    finally:
        os.close(fd)
    ok(f"PAT записан в {WORKER_PAT_FILE} ({pat[:24]}…)")


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
    seed_service_role_defs(dept_id, admin_id=user_ids["admin"])
    seed_service_role_assignments(dept_admin_id=user_ids["dep_admin1"])
    os_version_id = seed_os_version()
    server_id = seed_server(
        dept_id, created_by=user_ids["admin"], os_version_id=os_version_id,
    )
    seed_server_account(server_id, dept_id, created_by=user_ids["admin"])
    seed_worker_bot(dept_id)
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

  Server:     test-server-01 → {TEST_SERVER_HOSTNAME}:{TEST_SERVER_SSH_PORT} (контейнер test_server)
              OS-version: {OS_VERSION_NAME}, account: tester (sudo, пароль зашифрован)
  Worker bot: {WORKER_BOT_NAME} → PAT в .dev/.worker_pat
              ⚠ перезапусти server_worker: `docker compose -f docker-compose.dev.yml restart server_worker`
  Credentials: dev_jira_token, dev_postgres_password (dept НТ),
               dev_loadgen_secret (personal user1)
""")


if __name__ == "__main__":
    main()
