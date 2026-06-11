#!/usr/bin/env python3
"""Dev environment seed script — запускается каждый раз на чистую БД.

Что создаётся:
  Пользователи:  admin, loging_admin, nt_admin, nt_developer, nt_viewer
  Отдел:         НТ — Нагрузочное тестирование
  Сервисы:       config_service, server_service
  Роли:          reader / operator / admin для каждого сервиса
  Бот:           nt-deploy-bot
  Правила логов: 5 правил из коробки
  Серверы:       4 dev-сервера в отделе НТ (с ssh/ipmi/hostname) — добавляется,
                 если SERVER_URL доступен (по умолчанию — да).
  Аккаунты:      3 server_account с зашифрованными паролями.
  IPMI:          2 ipmi_controller (Redfish/iDRAC) с зашифрованными credentials.

Скрипт идемпотентен: 409-конфликты (duplicate) на серверной части молча
переводятся в OK, чтобы повторный запуск на наполненной БД не падал.
"""

import os
import sys
import time
import httpx

AUTH_URL    = os.environ.get("AUTH_URL", "http://localhost:8000")
LOG_URL     = os.environ.get("LOG_URL", "http://localhost:8001")
SERVER_URL  = os.environ.get("SERVER_URL", "http://localhost:8002")
SECRET_URL  = os.environ.get("SECRET_URL", "http://localhost:8003")
ADMIN_USER  = "admin"
ADMIN_PASS  = "AdminPass1234!"
LOG_API_KEY = "dev-logging-api-key"
DEV_PASS    = "DevPass1234!"

WIDTH = 60


# ── helpers ───────────────────────────────────────────────────────────────────

def _wait(url: str, path: str, label: str, retries: int = 30) -> None:
    for i in range(retries):
        try:
            if httpx.get(f"{url}{path}", timeout=3).status_code == 200:
                return
        except Exception:
            pass
        if i == 0:
            print(f"  ожидаем {label}...", end="", flush=True)
        print(".", end="", flush=True)
        time.sleep(1)
    print()
    sys.exit(f"  ✗ {label} не ответил за {retries}с")


def ok(msg: str)   -> None: print(f"  ✓ {msg}")
def fail(msg: str) -> None: print(f"  ✗ {msg}", file=sys.stderr)

def section(title: str) -> None:
    print(f"\n{'─' * WIDTH}")
    print(f"  {title}")
    print(f"{'─' * WIDTH}")

def post(c: httpx.Client, path: str, body: dict) -> tuple[int, dict]:
    r = c.post(path, json=body)
    return r.status_code, r.json() if r.content else {}

def get(c: httpx.Client, path: str) -> list | dict:
    r = c.get(path)
    r.raise_for_status()
    return r.json()

def must(status: int, body: dict, label: str) -> dict:
    if status not in (200, 201):
        fail(f"{label}: {body}")
        sys.exit(1)
    ok(label)
    return body


# ── server_service seeding ────────────────────────────────────────────────────

def _post_idempotent(c: httpx.Client, path: str, body: dict, label: str) -> dict | None:
    """POST с обработкой 409 как «уже есть». Возвращает body на успехе, None на 409."""
    r = c.post(path, json=body)
    if r.status_code in (200, 201):
        ok(label)
        return r.json() if r.content else {}
    if r.status_code == 409:
        ok(f"{label} (уже есть)")
        return None
    fail(f"{label}: HTTP {r.status_code} — {r.text}")
    sys.exit(1)


def _find_existing_server(c: httpx.Client, hostname: str) -> dict | None:
    """Поиск сервера по hostname среди списка (для resolve id после 409)."""
    r = c.get("/api/server/v1/servers", params={"limit": 500})
    if r.status_code != 200:
        return None
    for item in r.json().get("items", []):
        if item.get("hostname") == hostname:
            return item
    return None


def _seed_server_service(dept_id: str) -> None:
    """Залить набор dev-серверов / аккаунтов / IPMI под отдел НТ.

    Поднимается под токеном `nt_admin` (admin service-роль в server_service,
    выдана при создании пользователя). Падать в OK на 409 — основа
    идемпотентности при повторном `seed_dev.py` без чистки БД.
    """
    section("server_service: серверы / аккаунты / IPMI")

    r = httpx.post(f"{AUTH_URL}/api/auth/v1/login",
                   json={"username": "nt_admin", "password": DEV_PASS})
    if r.status_code != 200:
        fail(f"Не удалось залогиниться как nt_admin: {r.text}")
        sys.exit(1)
    nt = httpx.Client(
        base_url=SERVER_URL,
        headers={"Authorization": f"Bearer {r.json()['access_token']}"},
        timeout=10,
    )

    # ── Серверы ──
    servers = [
        {
            "hostname": "nt-load-01.dev.local",
            "display_name": "NT Load Generator 01",
            "ip_address": "10.20.1.11",
            "mgmt_ip_address": "10.20.101.11",
            "ssh_port": 22,
            "serial_number": "NT-LOAD-01-SN",
            "location": "DC1 / R12 / U17",
        },
        {
            "hostname": "nt-load-02.dev.local",
            "display_name": "NT Load Generator 02",
            "ip_address": "10.20.1.12",
            "mgmt_ip_address": "10.20.101.12",
            "ssh_port": 22,
            "serial_number": "NT-LOAD-02-SN",
            "location": "DC1 / R12 / U18",
        },
        {
            "hostname": "nt-db-01.dev.local",
            "display_name": "NT Postgres Target 01",
            "ip_address": "10.20.2.21",
            "mgmt_ip_address": "10.20.102.21",
            "ssh_port": 22,
            "serial_number": "NT-DB-01-SN",
            "location": "DC1 / R14 / U03",
        },
        {
            "hostname": "nt-bench-01.dev.local",
            "display_name": "NT Sysbench Box",
            "ip_address": "10.20.3.31",
            "ssh_port": 2222,
            "serial_number": "NT-BENCH-01-SN",
            "location": "DC2 / R02 / U09",
        },
    ]

    server_ids: dict[str, str] = {}
    for s in servers:
        payload = dict(s, department_id=dept_id)
        body = _post_idempotent(nt, "/api/server/v1/servers", payload,
                                f"server {s['hostname']}")
        if body and body.get("id"):
            server_ids[s["hostname"]] = body["id"]
        else:
            # 409 — достанем id через list-эндпоинт чтобы можно было создать аккаунты.
            existing = _find_existing_server(nt, s["hostname"])
            if existing:
                server_ids[s["hostname"]] = existing["id"]

    # ── server_account (зашифрованные пароли — server_service шифрует сам) ──
    accounts = [
        {
            "server_hostname": "nt-load-01.dev.local",
            "login": "loadgen",
            "password": "dev-loadgen-secret-1",
            "has_sudo": True,
            "unix_groups": ["loadtest"],
            "shell": "/bin/bash",
            "home_dir": "/home/loadgen",
        },
        {
            "server_hostname": "nt-load-02.dev.local",
            "login": "loadgen",
            "password": "dev-loadgen-secret-2",
            "has_sudo": False,
            "unix_groups": ["loadtest"],
            "shell": "/bin/bash",
            "home_dir": "/home/loadgen",
        },
        {
            "server_hostname": "nt-db-01.dev.local",
            "login": "postgres",
            "password": "dev-postgres-secret",
            "has_sudo": False,
            "unix_groups": ["postgres"],
            "shell": "/bin/bash",
            "home_dir": "/var/lib/postgresql",
        },
    ]
    for acc in accounts:
        srv_id = server_ids.get(acc.pop("server_hostname"))
        if not srv_id:
            continue
        _post_idempotent(
            nt,
            "/api/server/v1/server-accounts",
            dict(acc, server_id=srv_id),
            f"account {acc['login']} на {srv_id[:12]}…",
        )

    # ── IPMI контроллеры (Redfish/iDRAC, пароли шифруются сервисом) ──
    ipmis = [
        {
            "server_hostname": "nt-load-01.dev.local",
            "kind": "redfish",
            "endpoint_url": "https://10.20.101.11/redfish/v1",
            "username": "admin",
            "password": "dev-bmc-secret-load-01",
        },
        {
            "server_hostname": "nt-db-01.dev.local",
            "kind": "idrac",
            "endpoint_url": "https://10.20.102.21",
            "username": "root",
            "password": "dev-bmc-secret-db-01",
        },
    ]
    for ipmi in ipmis:
        srv_id = server_ids.get(ipmi.pop("server_hostname"))
        if not srv_id:
            continue
        _post_idempotent(
            nt,
            f"/api/server/v1/servers/{srv_id}/ipmi",
            ipmi,
            f"ipmi {ipmi['kind']} на {srv_id[:12]}…",
        )


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * WIDTH)
    print("  DBOS Server Manager — наполнение dev-окружения")
    print("=" * WIDTH)

    _wait(LOG_URL,    "/api/logging/v1/health", "loging_service"); print()
    _wait(AUTH_URL,   "/api/auth/v1/health",    "auth_service");   print()
    _wait(SERVER_URL, "/api/server/v1/health",  "server_service"); print()
    # secret_service может стартовать позже остальных — best-effort, не fatal
    try:
        _wait(SECRET_URL, "/api/secret/v1/health", "secret_service", retries=15)
        print()
    except SystemExit:
        print("  (secret_service пропущен — сидинг ролей будет недоступен)")

    # Логин как account_admin
    r = httpx.post(f"{AUTH_URL}/api/auth/v1/login",
                   json={"username": ADMIN_USER, "password": ADMIN_PASS})
    if r.status_code != 200:
        sys.exit(f"  ✗ Не удалось залогиниться как '{ADMIN_USER}': {r.text}")
    token = r.json()["access_token"]
    auth = httpx.Client(base_url=AUTH_URL,
                        headers={"Authorization": f"Bearer {token}"}, timeout=10)
    log_svc = httpx.Client(base_url=LOG_URL,
                           headers={"Authorization": f"Bearer {LOG_API_KEY}"}, timeout=10)

    # ── Платформенные сервисы ─────────────────────────────────────────────────
    # `service_name` — единственное имя сервиса; UI показывает его напрямую.
    section("Платформенные сервисы")
    for svc in [
        {"service_name": "auth_service",
         "description": "Аутентификация, авторизация и управление аккаунтами"},
        {"service_name": "loging_service",
         "description": "Централизованный сервис аудита. reader-роль даёт доступ к просмотру логов"},
        {"service_name": "server_service",
         "description": "Инвентаризация и управление тестовыми серверами"},
        {"service_name": "server_worker",
         "description": "Фоновые операции по серверам (power, ssh, ipmi) под worker_bot"},
        {"service_name": "config_service",
         "description": "Хранение и раздача конфигурации приложений"},
        {"service_name": "secret_service",
         "description": "Безопасное хранение токенов и учётных данных для внешних систем (Jira, Confluence, Git и т.п.)"},
    ]:
        _post_idempotent(auth, "/api/auth/v1/services", svc, svc["service_name"])

    # ── Отдел НТ ─────────────────────────────────────────────────────────────
    # Department-доступ к сервисам должен быть выдан ДО создания ролей —
    # каталог ролей теперь per-(department, service), а системная роль `admin`
    # сидится автоматически при grant_access.
    section("Отдел НТ")
    s, b = post(auth, "/api/auth/v1/departments",
                {"name": "НТ — Нагрузочное тестирование"})
    if s in (200, 201):
        ok("отдел НТ")
        dept_id = b["department_id"]
    elif s == 409:
        ok("отдел НТ (уже есть)")
        # ищем существующий отдел через list
        depts = httpx.get(
            f"{AUTH_URL}/api/auth/v1/departments",
            headers={"Authorization": auth.headers["Authorization"]},
            timeout=5,
        ).json()
        dept_id = None
        for d in depts if isinstance(depts, list) else depts.get("items", []):
            if d.get("name") == "НТ — Нагрузочное тестирование":
                dept_id = d.get("id") or d.get("department_id")
                break
        if not dept_id:
            fail("не удалось найти dept_id для НТ")
            sys.exit(1)
    else:
        fail(f"отдел НТ: {b}")
        sys.exit(1)

    for svc_name in ["config_service", "server_service", "loging_service", "secret_service"]:
        _post_idempotent(
            auth,
            f"/api/auth/v1/departments/{dept_id}/services",
            {"service_name": svc_name},
            f"НТ → доступ к {svc_name}",
        )

    # ── Роли сервисов (per-department) ────────────────────────────────────────
    # `admin` создаётся как is_system при выдаче доступа департаменту — здесь
    # добавляем только остальные роли.
    #
    # `worker_bot` — least-privilege scope для server_worker. Гранты сидятся
    # миграцией `43cf9cfef9e1_seed_worker_bot_entity_permissions.py` в
    # server_service: только view_password/rotate_password (server_account)
    # и view_credentials/rotate_credentials (ipmi_controller). Никаких
    # power.{on,off,reboot}, server.delete, permission.grant и т.п.
    section("Роли сервисов (отдел НТ)")
    for svc_name, roles in {
        "config_service": [
            ("reader",   "Только чтение конфигов"),
            ("operator", "Чтение + обновление конфигов"),
        ],
        "server_service": [
            ("reader",     "Просмотр списка серверов"),
            ("operator",   "Управление серверами"),
            ("worker_bot", "Least-privilege для server_worker: доступ к зашифрованным паролям/IPMI-credentials и их ротация. Не имеет power/CRUD/permission_grant."),
        ],
        "loging_service": [
            ("reader",   "Просмотр логов отдела"),
        ],
        # secret_service: `admin` сеется автоматически при grant_service_access
        # (через `seed_system_admin` → ServiceRoleDefinitionRepository) с
        # is_system=True. Здесь добавляем только остальные роли.
        "secret_service": [
            ("guest",    "Просмотр документации сервиса"),
            ("reader",   "Просмотр кред и reveal с can_read"),
            ("operator", "Создание/изменение кред в своей зоне"),
        ],
    }.items():
        for role_name, desc in roles:
            _post_idempotent(
                auth,
                f"/api/auth/v1/departments/{dept_id}/services/{svc_name}/roles",
                {"role_name": role_name, "description": desc},
                f"{svc_name}:{role_name}",
            )

    # ── Пользователи ─────────────────────────────────────────────────────────
    section("Пользователи")
    users = [
        {"username": "loging_admin",  "password": DEV_PASS,
         "platform_role": "loging_admin",
         "_label": "loging_admin (управление логированием)"},
        {"username": "nt_admin",      "password": DEV_PASS,
         "department_id": dept_id, "platform_role": "department_admin",
         # admin в server_service нужен чтобы department_admin мог реально
         # CRUD'ить сервера/аккаунты/IPMI через API — без service-роли
         # entity_permissions матрица не пустит даже department_admin'а.
         # Используем для seed'инга dev-серверов ниже.
         "initial_roles": [
             {"service_name": "server_service", "roles": ["admin"]},
         ],
         "_label": "nt_admin (администратор отдела НТ + admin в server_service)"},
        {"username": "nt_developer",  "password": DEV_PASS,
         "department_id": dept_id,
         "_label": "nt_developer (разработчик НТ)"},
        {"username": "nt_viewer",     "password": DEV_PASS,
         "department_id": dept_id,
         "_label": "nt_viewer (наблюдатель НТ)"},
        {"username": "nt_senior",     "password": DEV_PASS,
         "department_id": dept_id,
         "_label": "nt_senior (старший инженер НТ — получит доступ к логам через роль)"},
        {"username": "loging_reader", "password": DEV_PASS,
         "platform_role": "loging_reader",
         "department_id": dept_id,
         "_label": "loging_reader (читатель логов НТ через platform_role)"},
        # service_admin — cross-dept админ secret_service: read_for_audit,
        # admin_override_delete, transfer_ownership, recover. Department=NULL
        # (как loging_admin); права действуют во ВСЕХ depts.
        {"username": "service_admin", "password": DEV_PASS,
         "platform_role": "service_admin",
         "_label": "service_admin (cross-dept secret_service: audit/override/transfer/recover)"},
        {"username": "user", "password": DEV_PASS,
         "department_id": dept_id,
         "initial_roles": [
             {"service_name": "config_service", "roles": ["reader"]},
         ],
         "_label": "user (обычный пользователь НТ, reader в config_service)"},
    ]
    created_users = {}
    for u in users:
        label = u.pop("_label")
        s, b = post(auth, "/api/auth/v1/users", u)
        if s in (200, 201):
            ok(label)
            created_users[u["username"]] = b.get("user_id")
        elif s == 409:
            ok(f"{label} (уже есть)")
            # достанем user_id через список — для роль-операций ниже
            try:
                users_list = httpx.get(
                    f"{AUTH_URL}/api/auth/v1/users",
                    headers={"Authorization": auth.headers["Authorization"]},
                    params={"username": u["username"]},
                    timeout=5,
                ).json()
                items = users_list.get("items") if isinstance(users_list, dict) else users_list
                for it in items or []:
                    if it.get("username") == u["username"]:
                        created_users[u["username"]] = it.get("user_id") or it.get("id")
                        break
            except Exception:
                pass
        else:
            fail(f"{label}: {b}")
            # validation/enum-ошибки не валим скрипт целиком — продолжаем со следующего юзера
            continue

    # ── Роль loging.reader для nt_senior ─────────────────────────────────────
    section("Роли в loging_service")
    nt_senior_id = created_users.get("nt_senior")
    if nt_senior_id:
        status_r, body_r = post(
            auth,
            f"/api/auth/v1/departments/{dept_id}/services/loging_service/roles/reader/assign",
            {"user_ids": [nt_senior_id]},
        )
        if status_r in (200, 201):
            ok("nt_senior → reader в loging_service")
        elif status_r == 409:
            ok("nt_senior → reader в loging_service (уже есть)")
        else:
            print(f"  ! Не удалось назначить роль: {body_r}")

    # ── Бот ──────────────────────────────────────────────────────────────────
    section("Боты")
    s, b = post(auth, "/api/auth/v1/bots", {
        "name": "nt-deploy-bot",
        "department_id": dept_id,
        "allowed_services": ["config_service", "server_service"],
        "description": "Бот автоматизации деплоя для отдела НТ",
    })
    bot = must(s, b, "nt-deploy-bot")
    s2, b2 = post(auth, f"/api/auth/v1/bots/{bot['bot_id']}/tokens", {"name": "dev-token"})
    if s2 == 201:
        ok(f"  токен: {b2.get('token', '')[:40]}…")

    # ── server_worker service user + PAT ─────────────────────────────────────
    # Worker аутентифицируется PAT'ом обычного пользователя: токен живёт
    # пока не отозван, что удобно для долгоиграющего процесса.
    #
    # Роль `worker_bot` (а НЕ `admin`!) — least-privilege scope: только
    # view_password/rotate_password на server_account и
    # view_credentials/rotate_credentials на ipmi_controller. Никаких
    # power.{on,off,reboot}, server.delete, permission_grant, role_create.
    # Гранты сидятся миграцией `43cf9cfef9e1` в server_service.
    section("Сервисный аккаунт server_worker")
    s, b = post(auth, "/api/auth/v1/users", {
        "username": "server_worker_user",
        "password": DEV_PASS,
        "department_id": dept_id,
        "initial_roles": [
            {"service_name": "server_service", "roles": ["worker_bot"]},
        ],
    })
    must(s, b, "server_worker_user (worker_bot least-privilege в server_service)")

    r_login = httpx.post(f"{AUTH_URL}/api/auth/v1/login",
                        json={"username": "server_worker_user", "password": DEV_PASS})
    if r_login.status_code != 200:
        fail(f"Не удалось залогиниться как server_worker_user: {r_login.text}")
        sys.exit(1)
    worker_user_token = r_login.json()["access_token"]
    worker_auth = httpx.Client(base_url=AUTH_URL,
                              headers={"Authorization": f"Bearer {worker_user_token}"}, timeout=10)
    s, b = post(worker_auth, "/api/auth/v1/tokens", {
        "name": "server_worker_pat",
        "allowed_services": ["server_service", "loging_service"],
    })
    worker_pat = b.get("token", "")
    if s == 201 and worker_pat:
        ok("  PAT для server_worker (используется автоматически при make up):")
        print(f"\n    {worker_pat}\n")
        try:
            pat_path = "/shared/.worker_pat"
            with open(pat_path, "w") as f:
                f.write(worker_pat)
            # 0600: PAT'у на shared-volume оставляем доступ только владельцу.
            # На bind-mount'ах (.dev/ в host'е) uid в контейнере и на хосте
            # могут не совпадать — поэтому chmod best-effort, без падения.
            try:
                os.chmod(pat_path, 0o600)
            except OSError:
                pass
            ok("  PAT записан в .dev/.worker_pat (mount /shared у seeder и worker)")
        except OSError as exc:
            fail(f"  не удалось записать /shared/.worker_pat: {exc}")

    # ── server_service: dev-сервера + аккаунты + IPMI ────────────────────────
    # Сидим типовой парк через REST (паролы шифрует server_service внутри —
    # ключ encryption-секрета в seeder контейнер не пробрасывается). Поэтому
    # действуем как обычный пользователь под токеном nt_admin (admin в
    # server_service). Идемпотентность — через перехват 409.
    _seed_server_service(dept_id)

    # ── Правила логирования ───────────────────────────────────────────────────
    section("Правила логирования")
    r2 = httpx.post(f"{AUTH_URL}/api/auth/v1/login",
                    json={"username": "loging_admin", "password": DEV_PASS})
    if r2.status_code != 200:
        fail(f"Не удалось залогиниться как loging_admin: {r2.text}")
        sys.exit(1)
    log_admin = httpx.Client(
        base_url=LOG_URL,
        headers={"Authorization": f"Bearer {r2.json()['access_token']}"},
        timeout=10,
    )
    for rule in [
        {"name": "escalate-all-denied",
         "description": "Все отказы в доступе → CRITICAL",
         "effect": "OVERRIDE_SEVERITY", "match_status": "denied",
         "effect_severity": "CRITICAL", "priority": 900},
        {"name": "escalate-auth-failures",
         "description": "Ошибки аутентификации auth_service → CRITICAL",
         "effect": "OVERRIDE_SEVERITY", "match_service": "auth_service",
         "match_status": "failure", "effect_severity": "CRITICAL", "priority": 800},
        {"name": "escalate-user-bans",
         "description": "Блокировки пользователей → CRITICAL",
         "effect": "OVERRIDE_SEVERITY", "match_action": "user.ban",
         "effect_severity": "CRITICAL", "priority": 700},
        {"name": "escalate-password-resets",
         "description": "Сбросы паролей → CRITICAL",
         "effect": "OVERRIDE_SEVERITY", "match_action": "user.password_reset",
         "effect_severity": "CRITICAL", "priority": 700},
        {"name": "suppress-health-checks",
         "description": "Подавить http.client_error на /health (выключено по умолчанию)",
         "effect": "SUPPRESS", "match_action": "http.client_error",
         "match_service": "auth_service", "priority": 50, "is_active": False},

        # ── Заготовки ротации (выключены) ────────────────────────────────────
        {"name": "rotate-info-config-service",
         "description": "Ротация: отбросить все INFO события config_service (только чтение конфигов)",
         "effect": "SUPPRESS", "match_service": "config_service",
         "match_severity": "INFO", "priority": 10, "is_active": False},
        {"name": "rotate-list-operations",
         "description": "Ротация: подавить все успешные операции чтения (*.list) от всех сервисов",
         "effect": "SUPPRESS", "match_action": "*.list",
         "match_status": "success", "priority": 10, "is_active": False},
        {"name": "rotate-me-calls",
         "description": "Ротация: подавить user.me (частые токен-проверки)",
         "effect": "SUPPRESS", "match_action": "user.me",
         "match_status": "success", "priority": 10, "is_active": False},
    ]:
        s, b = post(log_admin, "/api/logging/v1/rules", rule)
        state = "" if rule.get("is_active", True) else " (выключено)"
        must(s, b, f"«{rule['name']}» prio={rule['priority']}{state}")

    # ── Политика ротации (retention) ──────────────────────────────────────────
    section("Политика ротации логов")
    s, b = log_admin.put("/api/logging/v1/retention", json={
        "retain_days": 90,
        "description": "Хранить все события 90 дней (минимум 30). Логи loging_service хранятся вечно.",
        "is_active": True,
    }).status_code, {}
    if s == 200:
        ok("Глобальная ротация: 90 дней для всех событий")
    else:
        print(f"  ! Ротация: {s}")

    # ── Итог ──────────────────────────────────────────────────────────────────
    print()
    print("=" * WIDTH)
    print("  Окружение готово!")
    print("=" * WIDTH)
    print(f"""
  Пользователи (все пароли: {DEV_PASS}):

    {'Логин':<16}  {'Роль':<26}  Доступ к логам
    {'─' * 16}  {'─' * 26}  {'─' * 30}
    {'admin':<16}  {'account_admin':<26}  —
    {'loging_admin':<16}  {'loging_admin':<26}  Все логи + правила + ротация
    {'loging_reader':<16}  {'loging_reader':<26}  Все логи НТ (только чтение)
    {'service_admin':<16}  {'service_admin':<26}  — (secret_service cross-dept)
    {'nt_admin':<16}  {'department_admin (НТ)':<26}  Логи НТ (только чтение)
    {'nt_senior':<16}  {'reader в loging_service':<26}  Логи НТ (только чтение)
    {'nt_developer':<16}  {'пользователь НТ':<26}  —
    {'nt_viewer':<16}  {'пользователь НТ':<26}  —
    {'user':<16}  {'reader в config_service':<26}  —

  Документация API:

    auth_service    →  http://localhost:8000/docs
    loging_service  →  http://localhost:8001/docs
    server_service  →  http://localhost:8002/docs    (nt_admin / {DEV_PASS})

  server_service-данные:
    4 сервера НТ (nt-load-01/02, nt-db-01, nt-bench-01), 3 server_account
    (loadgen ×2 + postgres) с шифрованными паролями, 2 IPMI-контроллера
    (Redfish/iDRAC).

  Authorize → OAuth2Password:
    auth_service   : admin / {DEV_PASS}
    loging_service : loging_admin / {DEV_PASS}   (полный доступ)
                   : nt_senior / {DEV_PASS}       (читатель логов НТ)
    server_service : nt_admin / {DEV_PASS}        (admin в server_service)

  Ротация логов:
    Все события → 90 дней (настраивается через PUT /api/logging/v1/retention)
    loging_service события защищены от ротации всегда.

  Остановить: make dev-stop
""")


if __name__ == "__main__":
    main()
