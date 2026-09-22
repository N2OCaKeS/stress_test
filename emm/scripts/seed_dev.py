#!/usr/bin/env python3
"""Dev seed: патчит postgres напрямую (без REST API).

Стек должен быть уже поднят (`make up`). Полный прогон (`make seed`, без
аргументов) заново создаёт отдел и все dev-данные:
  - обновляет bootstrap-админа (must_change_password=false, пароль 1),
  - заводит 4 новых юзера: loging_admin1 / dep_admin1 / loging_reader1 / user1,
  - создаёт отдел «Нагрузочное тестирование»,
  - выдаёт отделу доступ ко всем платформенным сервисам,
  - сидит каталог OS-версий (`os_versions`): debian-плейсхолдер под
    test-server-01 + весь реальный каталог сборок Astra из allta_app
    (`allta_app_service/releases.json`, см. `seed_astra_catalog`),
  - создаёт один сервер test-server-01, указывающий на контейнер test_server
    (ssh_port=2222, привязан к debian OS-record),
  - заводит OS-аккаунт `tester` (пароль `tester1234`) на этом сервере с
    шифрованием через server_service AES-GCM,
  - при заданной переменной окружения `ACS_PASS` — включает ACS-снимки
    (`acs_settings`, реальный prod URL, пароль зашифрован тем же AES-GCM) и
    даёт доступ отделу НТ (`acs_department_access`); без `ACS_PASS` шаг
    пропускается с предупреждением, ACS остаётся выключен (см. `seed_acs`),
  - досевает системную роль `admin` в каталоге `service_role_definitions`
    для отдела НТ по всем сервисам,
  - заводит три credential'а в secret_service (dev_jira_token,
    dev_postgres_password, dev_loadgen_secret),
  - заполняет `department_integration_settings` dev-дефолтами несекретных
    полей (base URL'ы, project key, space'ы) — ссылки на реальные
    Jira/Git/Confluence credential'ы остаются NULL, их вводит владелец через
    `/home/integration-onboarding` (см. `seed_integration_settings_defaults`),
  - импортирует каталог тестов+стенд из allta_app в testing_service
    (`testing_service/scripts/import_catalog.py`, живой логин dep_admin1,
    server_id стенда подставляется реально засеянным),
  - добавляет 126 демо-попыток с логами (`scripts/seed_test_logs.py`) —
    только чтобы было что показать в интерфейсе, не результаты испытаний.

`python3 seed_dev.py --refresh` (`make seed-refresh`) — НЕДЕструктивный
повтор поверх уже наполненного стека: НЕ трогает отдел/юзеров/секреты/ссылки
на реальные credential'ы, только переимпортирует каталог тестов и демо-логи
для уже существующего отдела (см. `refresh_seed`). Нужен для обновления
каталога тестов без потери токенов Jira/Git/Confluence, которые владелец уже
ввёл через onboarding-страницу.

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

import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import psycopg

PG_HOST = os.environ.get("PG_HOST", "localhost")
PG_PORT = int(os.environ.get("PG_PORT", "5432"))
PG_USER = os.environ.get("PG_USER", "app_user")
PG_PASS = os.environ.get("PG_PASS", "app_password")
SECRET_PG_HOST = os.environ.get("SECRET_PG_HOST", "localhost")
SECRET_PG_PORT = int(os.environ.get("SECRET_PG_PORT", "5435"))
TESTING_PG_HOST = os.environ.get("TESTING_PG_HOST", "localhost")
TESTING_PG_PORT = int(os.environ.get("TESTING_PG_PORT", "5436"))

AUTH_CONTAINER = os.environ.get("AUTH_CONTAINER", "emm-auth_service-1")
SECRET_CONTAINER = os.environ.get(
    "SECRET_CONTAINER", "emm-secret_service-1"
)
SERVER_CONTAINER = os.environ.get(
    "SERVER_CONTAINER", "emm-server_service-1"
)
TESTING_CONTAINER = os.environ.get(
    "TESTING_CONTAINER", "emm-testing_service-1"
)
AUTH_BASE_URL = os.environ.get("AUTH_BASE_URL", "http://localhost:8000")

# Реальный каталог тестов+стенд, портированный из legacy allta_app — держится
# отдельным yaml, а не инлайн-данными в этом файле (см. docstring
# testing_service/scripts/import_catalog.py). server_id стенда в этом файле —
# плейсхолдер: реальный сервер каждый прогон seed'а получает новый id
# (`seed_server` ниже), поэтому import_catalog.py подменяет его аргументом
# `--override-stand-server-id` перед импортом.
IMPORT_CATALOG_YAML = "scripts/import_catalog.allta.yaml"

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
# Номер стенда — обязателен и уникален в рамках department_id (не глобально).
# Отдел каждый прогон `make seed` создаётся заново, так что 1 всегда свободен.
TEST_SERVER_NUMBER = 1

# Каталог OS-версий — одна запись под debian-образ тестового сервера
# (на неё же указывает test-server-01 через os_version_id) плюс весь реальный
# каталог Astra из allta_app (см. `seed_astra_catalog` ниже).
OS_VERSION_NAME = "debian-stable"

# Каталог сборок Astra, портированный из легаси allta_app
# (`ReleaseToRepo.generate_releases_file` в allta_app_full/libs/liballta.py) —
# ключ это build-версия (`1.8.5.46`, легаси `1.7.3.UU.1`), значение — уже
# готовые строки sources.list для неё. server_service использует тот же
# формат в проде (см. `os_version_repo_resolver.resolve_repository_urls`),
# только резолвит его на лету по сети; тут — статический снапшот на момент
# написания seed'а.
ASTRA_RELEASES_JSON = (
    Path(__file__).resolve().parent.parent / "allta_app_service" / "releases.json"
)

# Реальный ACS (clonezilla/DRBL) сервера, тот же, что зашит в легаси
# allta_app_full/alltabot.py (SERVER_ACS_IP_OR_NAME/SERVER_ACS_PORT). URL —
# не секрет, можно держать прямо в seed'е. Пароль — секрет, поэтому в код не
# идёт: seed берёт его только из ACS_PASS в окружении на момент запуска (см.
# `seed_acs`). Сервер read-only для нас, реальных обращений отсюда нет —
# только запись URL-строки в БД.
ACS_URL = "http://10.177.103.10:9999"
# Singleton PK строки acs_settings — совпадает с server_service's
# `models.acs_settings.SINGLETON_ID`; захардкожено, т.к. seed не импортирует
# исходники server_service.
ACS_SETTINGS_ID = "default"

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


def warn(msg: str) -> None:
    """Некритичное предупреждение — шаг пропущен, но seed продолжает работу."""
    print(f"  ⚠ {msg}")


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


def encrypt_acs_password(plaintext: str) -> str:
    """AES-GCM шифр для acs_settings.acs_password_encrypted (singleton `default`).

    Тот же конверт и та же зависимость от server_service, что и у
    `encrypt_account_password` — ключ и AAD (`aad_for_acs_password`)
    считываются внутри контейнера, seed'еру мастер-ключ не нужен.
    """
    code = (
        "from src.services.secrets_service import encrypt, aad_for_acs_password; "
        f"print(encrypt({plaintext!r}, aad=aad_for_acs_password({ACS_SETTINGS_ID!r})))"
    )
    return _docker_exec(SERVER_CONTAINER, code)


def login(username: str, password: str) -> str:
    """Живой логин через auth_service — нужен import_catalog.py для стендов.

    `create_test_stand` резолвит department_id пасс-through вызовом к
    server_service тем же bearer'ом, которым видит сервер вызывающий —
    подделать значение из yaml нельзя, нужен настоящий токен.
    """
    body = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(
        f"{AUTH_BASE_URL}/api/auth/v1/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())["access_token"]
    except urllib.error.URLError as exc:
        fail(f"login {username} → {AUTH_BASE_URL} не удался: {exc}")
        sys.exit(1)


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


def seed_astra_catalog() -> int:
    """Сидим весь реальный каталог сборок Astra из `ASTRA_RELEASES_JSON`.

    Рядом с placeholder'ом `debian-stable` (`seed_os_version`) — не заменяет
    его, тестовый сервер как был привязан к debian-записи, так и остаётся.
    Каждая строка `releases.json` — уже готовый список `deb https://...`
    репозиториев для этой сборки, брать их и резолвить самим не нужно.

    Идемпотентно: по UNIQUE(name) обновляет repositories у существующей
    записи (id не переиспользуется, только апдейтится), иначе вставляет новую.
    """
    section("server_service: каталог сборок Astra (allta_app)")
    with open(ASTRA_RELEASES_JSON, encoding="utf-8") as f:
        releases = json.load(f)

    with conn(PG_HOST, PG_PORT, "dev_server") as c, c.cursor() as cur:
        for build_version, repo_lines in sorted(releases.items()):
            is_urgent = "UU" in build_version.split(".")
            description = f"Astra Linux SE {build_version} (allta_app releases.json)"
            cur.execute("SELECT id FROM os_versions WHERE name = %s", (build_version,))
            row = cur.fetchone()
            if row:
                cur.execute(
                    "UPDATE os_versions SET description = %s, repositories = %s, "
                    "is_urgent_update = %s, updated_at = now() WHERE id = %s",
                    (description, repo_lines, is_urgent, row[0]),
                )
            else:
                cur.execute(
                    "INSERT INTO os_versions "
                    "(id, name, description, repositories, is_urgent_update) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (gen_id("osv"), build_version, description, repo_lines, is_urgent),
                )
    ok(f"каталог Astra: {len(releases)} версий (allta_app releases.json)")
    return len(releases)


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
            " department_id, number, status, power_state, busy_state, is_managed, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'unknown', 'unknown', 'free', false, %s)",
            (
                server_id,
                TEST_SERVER_HOSTNAME,
                "test-server-01",
                TEST_SERVER_IP,
                TEST_SERVER_SSH_PORT,
                os_version_id,
                dept_id,
                TEST_SERVER_NUMBER,
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


def seed_acs(dept_id: str, created_by: str) -> None:
    """ACS-снимки: platform singleton (`acs_settings`) + opt-in отдела НТ.

    Пароль ACS не хардкодится — читается из `ACS_PASS` в окружении seed'а.
    Если переменная не задана, шаг просто пропускается с предупреждением:
    строки `acs_settings`/`acs_department_access` не заводятся, вкладка
    «Снимки ACS» остаётся скрытой (тот же дефолт, что и без этого seed'а).
    """
    section("server_service: ACS-снимки (url + пароль из ACS_PASS)")
    acs_pass = os.environ.get("ACS_PASS")
    if not acs_pass:
        warn(
            "ACS_PASS не задан в окружении — ACS оставлен выключенным. "
            "Запустите `ACS_PASS=... python3 seed_dev.py`, чтобы включить."
        )
        return

    encrypted = encrypt_acs_password(acs_pass)
    with conn(PG_HOST, PG_PORT, "dev_server") as c, c.cursor() as cur:
        cur.execute(
            "INSERT INTO acs_settings (id, enabled, acs_url, acs_password_encrypted) "
            "VALUES (%s, true, %s, %s) "
            "ON CONFLICT (id) DO UPDATE SET "
            "enabled = true, acs_url = EXCLUDED.acs_url, "
            "acs_password_encrypted = EXCLUDED.acs_password_encrypted, "
            "updated_at = now()",
            (ACS_SETTINGS_ID, ACS_URL, encrypted),
        )
        cur.execute(
            "INSERT INTO acs_department_access (id, department_id, is_enabled, created_by) "
            "VALUES (%s, %s, true, %s) "
            "ON CONFLICT (department_id) DO UPDATE SET "
            "is_enabled = true, updated_at = now()",
            (gen_id("ada"), dept_id, created_by),
        )
    ok(f"acs_settings enabled=true, url={ACS_URL}, пароль зашифрован")
    ok(f"acs_department_access: отдел {dept_id} → is_enabled=true")


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


# ── testing_service ──────────────────────────────────────────────────────────


# Dev-дефолты несекретных полей `department_integration_settings` (G3).
# `stp_matrix_confluence_space`/`stp_matrix_confluence_root_page_title` и
# `jira_board_id`/`tempo_team_id` воспроизводят прежние платформенные
# хардкоды легаси allta_app (см. docstring
# `testing_service/src/models/department_integration_settings.py`) — до
# перевода этих полей в per-department настройку они были глобальными
# константами `DEVQA`/`'Состав тестового прогона'`/`340`/`["7"]`, так что для
# dev-окружения это не выдумка, а тот же исходный дефолт. Остальные адреса и
# space'ы нигде в репозитории не описаны как реальный dev-инстанс — это
# заведомо фиктивные, но валидные по формату placeholder'ы под `.dev.internal`,
# чтобы их нельзя было спутать с прод-доменом `astralinux.ru`.
_INTEGRATION_DEFAULTS: dict[str, str] = {
    "jira_base_url": "https://jira.dev.internal",
    "confluence_base_url": "https://confluence.dev.internal",
    "bitbucket_base_url": "https://bitbucket.dev.internal",
    "bitbucket_project_key": "NTDEV",
    "bitbucket_repo_slug": "allta-app",
    "jira_board_id": "340",
    "tempo_team_id": "7",
    "confluence_report_page_space": "NTDEV",
    "confluence_report_parent_page_title": "Отчёты по активности (dev)",
    "stp_matrix_confluence_space": "DEVQA",
    "stp_matrix_confluence_root_page_title": "Состав тестового прогона",
}


def seed_integration_settings_defaults(dept_id: str) -> None:
    """Завести `department_integration_settings` со всеми несекретными dev-полями.

    `credential_id`/`confluence_credential_id`/`bitbucket_credential_id`
    намеренно НЕ заполняются: это ссылки на реальные secret_service-записи
    Jira/Git/Confluence, угадать которые нельзя — их вводит владелец через
    `/home/integration-onboarding` (G3) уже после seed'а. `seed_auth` создаёт
    новый `dept_id` на каждый прогон, поэтому строка тут всегда свежая
    (никогда не конфликтует с предыдущим прогоном) — `ON CONFLICT` оставлен
    ради `seed-refresh`/повторных ручных вызовов, но трогает только
    перечисленные несекретные колонки, credential-ссылки не задевает.
    """
    section("testing_service: dev-дефолты интеграций (Jira/Confluence/Bitbucket)")
    row_id = gen_id("dis")
    cols = list(_INTEGRATION_DEFAULTS.keys())
    assignments = ", ".join(f"{col} = EXCLUDED.{col}" for col in cols)
    with conn(TESTING_PG_HOST, TESTING_PG_PORT, "dev_testing") as c, c.cursor() as cur:
        cur.execute(
            f"INSERT INTO department_integration_settings (id, department_id, {', '.join(cols)}) "
            f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (department_id) DO UPDATE SET {assignments}",
            (row_id, dept_id, *_INTEGRATION_DEFAULTS.values()),
        )
    ok(
        f"department_integration_settings ({dept_id}) → dev URL/space/board/team заполнены; "
        "credential_id/confluence_credential_id/bitbucket_credential_id оставлены NULL"
    )


def seed_test_catalog(server_id: str) -> None:
    """Каталог тестов+стенд из allta_app через scripts/import_catalog.py.

    Запускается внутри testing_service-контейнера (`scripts/` смонтирована
    в docker-compose.dev.yml) — тот же use case, что дёргает HTTP-API, не
    параллельный путь записи. `--override-stand-server-id` подменяет
    захардкоженный в yaml server_id на реально засеянный `seed_server`'ом
    (он новый на каждый прогон).
    """
    section("testing_service: каталог тестов allta_app")
    # server_id/department_id пересоздаются заново на каждый прогон seed'а
    # (seed_server/seed_auth генерируют новый id, не переиспользуют старый) —
    # test_stands.server_id не FK на другую БД, старая строка от прошлого
    # прогона осиротеет молча (сервер/отдел, на которые она ссылалась, уже
    # удалены), а не будет переиспользована/задедуплена импортом. Сносим её
    # явно перед реимпортом, как и остальные тестовые записи в этом файле.
    # `queue_items`/`stp_test_runs` держат RESTRICT-FK на test_stands (может
    # накопиться из demo-логов прошлого прогона, seed_demo_logs ниже, или из
    # реального использования очереди/СТП на этом стенде) — сносим их первыми,
    # иначе DELETE test_stands падает на FK. test_log_segments/blobs каскадом
    # уйдут вместе с test_logs, stp_cells — вместе с stp_test_runs.
    # `seed_demo_logs` (ниже) заводит свои test_definitions с кодом
    # `EXAMPLE-<hash отдела>-*` — хэш зависит от department_id, который
    # seed_auth пересоздаёт заново на каждый прогон, поэтому старый набор
    # не задедуплицируется по code и копится под уже удалённым отделом. Код
    # `EXAMPLE-` — код реального allta_app-теста никогда не примет (там
    # `postgresql.balance` и т.п.), поэтому фильтр безопасен.
    with conn(TESTING_PG_HOST, TESTING_PG_PORT, "dev_testing") as c, c.cursor() as cur:
        cur.execute("DELETE FROM test_logs")
        cur.execute("DELETE FROM queue_items")
        cur.execute("DELETE FROM stp_test_runs")
        cur.execute("DELETE FROM test_stands")
        cur.execute("DELETE FROM test_definitions WHERE code LIKE 'EXAMPLE-%'")
    token = login("dep_admin1", DEV_PASSWORD)
    res = subprocess.run(
        [
            "docker", "exec", "-w", "/app", TESTING_CONTAINER,
            "python", "scripts/import_catalog.py",
            IMPORT_CATALOG_YAML,
            "--bearer-token", token,
            "--override-stand-server-id", server_id,
        ],
        check=False,
    )
    if res.returncode != 0:
        fail(f"import_catalog.py вернул {res.returncode}")
        sys.exit(1)
    ok("каталог тестов allta_app импортирован")


def seed_demo_logs() -> None:
    """126 искусственных завершённых попыток (64 кампании / 62 одиночных) с
    логами и сегментами — чтобы было что показать в `/testing/logs` без
    реального прогона на железе (`scripts/seed_test_logs.py`, см.
    [[../obsidian/reports/2026-09-13-132500-log-history-and-dev-examples]]).

    Должен идти ПОСЛЕ `seed_test_catalog` — иначе создаст свой одноразовый
    отключённый стенд вместо того, чтобы переиспользовать реальный
    allta_app-стенд, и тот потом снесётся вместе с `DELETE FROM test_stands`
    в `seed_test_catalog` при следующем прогоне seed'а.
    """
    section("testing_service: демо-логи для показа интерфейса")
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_test_logs.py")
    res = subprocess.run([sys.executable, script], check=False)
    if res.returncode != 0:
        fail(f"seed_test_logs.py вернул {res.returncode}")
        sys.exit(1)
    ok("демо-попытки с логами добавлены")


# ── refresh (idempotent, поверх уже наполненного стека) ─────────────────────


def find_existing_department() -> tuple[str, str]:
    """Отдел + admin_id уже наполненного отдела НТ (по `dep_admin1`).

    Не создаёт ничего нового — `seed-refresh` не должен заводить свой отдел,
    он переиспользует то, что оставил предыдущий полный `make seed`.
    """
    with conn(PG_HOST, PG_PORT, "dev_auth") as c, c.cursor() as cur:
        cur.execute(
            "SELECT id, department_id FROM users WHERE username = 'dep_admin1'"
        )
        row = cur.fetchone()
    if not row or not row[1]:
        fail(
            "dep_admin1 не найден — сначала выполните полный `make seed`, "
            "`make seed-refresh` умеет только обновлять уже наполненный стек"
        )
        sys.exit(1)
    return row[1], row[0]


def find_existing_test_server(dept_id: str) -> str:
    """id `test-server-01` уже заведённого отдела (по hostname+department_id)."""
    with conn(PG_HOST, PG_PORT, "dev_server") as c, c.cursor() as cur:
        cur.execute(
            "SELECT id FROM servers WHERE hostname = %s AND department_id = %s",
            (TEST_SERVER_HOSTNAME, dept_id),
        )
        row = cur.fetchone()
    if not row:
        fail(
            f"сервер {TEST_SERVER_HOSTNAME} для отдела {dept_id} не найден — "
            "сначала выполните полный `make seed`"
        )
        sys.exit(1)
    return row[0]


def refresh_seed() -> None:
    """Недеструктивный повтор: только каталог тестов + демо-логи (G4).

    НЕ трогает отдел/юзеров (`seed_auth`), НЕ трогает `seed_secrets` и НЕ
    трогает `seed_integration_settings_defaults` — реальные Jira/Git/Confluence
    credential'ы, которые владелец уже ввёл через `/home/integration-onboarding`,
    остаются как есть. Годится для обновления каталога после правок
    `import_catalog.allta.yaml` без необходимости заново вводить токены.
    """
    print()
    print("=" * WIDTH)
    print("  DBOS dev seed refresh (без пересоздания отдела/кред)")
    print("=" * WIDTH)

    dept_id, _admin_id = find_existing_department()
    server_id = find_existing_test_server(dept_id)
    seed_test_catalog(server_id)
    seed_demo_logs()

    print()
    print("=" * WIDTH)
    print("  Готово. Каталог тестов и демо-логи обновлены для существующего отдела.")
    print("  Отдел, пользователи, секреты и ссылки на реальные credential'ы не тронуты.")
    print("=" * WIDTH)
    print()


# ── main ─────────────────────────────────────────────────────────────────────


def full_seed() -> None:
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
    astra_count = seed_astra_catalog()
    server_id = seed_server(
        dept_id, created_by=user_ids["admin"], os_version_id=os_version_id,
    )
    seed_server_account(server_id, dept_id, created_by=user_ids["admin"])
    seed_acs(dept_id, created_by=user_ids["admin"])
    seed_secrets(dept_id, user_ids)
    seed_integration_settings_defaults(dept_id)
    seed_test_catalog(server_id)
    seed_demo_logs()

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
  OS-каталог: {astra_count} сборок Astra (allta_app releases.json) + {OS_VERSION_NAME}
  ACS:        {"enabled=true, url=" + ACS_URL if os.environ.get("ACS_PASS") else "выключен (ACS_PASS не был задан при запуске seed)"}
  Worker bot: server_worker (системный отдел «DBOS System») — заведён
              auth_service'ом на старте из WORKER_BOT_TOKEN, не этим seed'ом.
  Credentials: dev_jira_token, dev_postgres_password (dept НТ),
               dev_loadgen_secret (personal user1)
  Testing:    каталог тестов+стенд allta_app импортированы в testing_service
              + 126 демо-попыток с логами (см. вывод seed_test_logs.py выше)
  Интеграции: department_integration_settings заполнены dev-дефолтами URL/space;
              реальные токены Jira/Git/Confluence — на /home/integration-onboarding
              (страница dep_admin1, ссылки на credential'ы намеренно не заведены)
""")


def main() -> None:
    if "--refresh" in sys.argv[1:]:
        refresh_seed()
    else:
        full_seed()


if __name__ == "__main__":
    main()
