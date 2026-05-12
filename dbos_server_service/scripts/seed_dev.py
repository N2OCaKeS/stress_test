#!/usr/bin/env python3
"""Dev environment seed script — запускается каждый раз на чистую БД.

Что создаётся:
  Пользователи:  admin, loging_admin, nt_admin, nt_developer, nt_viewer
  Отдел:         НТ — Нагрузочное тестирование
  Сервисы:       config_service, server_service
  Роли:          reader / operator / admin для каждого сервиса
  Бот:           nt-deploy-bot
  Правила логов: 5 правил из коробки
"""

import os
import sys
import time
import httpx

AUTH_URL    = os.environ.get("AUTH_URL", "http://localhost:8000")
LOG_URL     = os.environ.get("LOG_URL", "http://localhost:8001")
ADMIN_USER  = "admin"
ADMIN_PASS  = "1234"
LOG_API_KEY = "dev-logging-api-key"
DEV_PASS    = "1234"

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


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * WIDTH)
    print("  DBOS Server Manager — наполнение dev-окружения")
    print("=" * WIDTH)

    _wait(LOG_URL, "/api/logging/v1/health", "loging_service"); print()
    _wait(AUTH_URL, "/api/auth/v1/health",   "auth_service");   print()

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
    section("Платформенные сервисы")
    for svc in [
        {"service_name": "config_service",  "display_name": "Конфигурация",
         "description": "Хранение и раздача конфигурации приложений"},
        {"service_name": "server_service",  "display_name": "Управление серверами",
         "description": "Инвентаризация и управление тестовыми серверами"},
        {"service_name": "loging_service",  "display_name": "Аудит и логирование",
         "description": "Централизованный сервис аудита. reader-роль даёт доступ к просмотру логов"},
    ]:
        s, b = post(auth, "/api/auth/v1/services", svc)
        must(s, b, f"{svc['service_name']} ({svc['display_name']})")

    # ── Роли сервисов ─────────────────────────────────────────────────────────
    section("Роли сервисов")
    # admin создаётся автоматически при создании сервиса
    for svc_name, roles in {
        "config_service": [
            ("reader",   "Читатель", "Только чтение конфигов"),
            ("operator", "Оператор", "Чтение + обновление конфигов"),
        ],
        "server_service": [
            ("reader",   "Читатель", "Просмотр списка серверов"),
            ("operator", "Оператор", "Управление серверами"),
        ],
    }.items():
        for role_name, display, desc in roles:
            s, b = post(auth, f"/api/auth/v1/services/{svc_name}/roles",
                        {"role_name": role_name, "display_name": display, "description": desc})
            must(s, b, f"{svc_name}:{role_name}")

    # ── Отдел НТ ─────────────────────────────────────────────────────────────
    section("Отдел НТ")
    s, b = post(auth, "/api/auth/v1/departments",
                {"name": "nt", "display_name": "НТ — Нагрузочное тестирование"})
    dept = must(s, b, "отдел НТ")
    dept_id = dept["department_id"]

    for svc_name in ["config_service", "server_service"]:
        s, b = post(auth, f"/api/auth/v1/departments/{dept_id}/services",
                    {"service_name": svc_name})
        must(s, b, f"НТ → доступ к {svc_name}")

    # ── Пользователи ─────────────────────────────────────────────────────────
    section("Пользователи")
    users = [
        {"username": "loging_admin",  "password": DEV_PASS,
         "platform_role": "loging_admin",
         "_label": "loging_admin (управление логированием)"},
        {"username": "nt_admin",      "password": DEV_PASS,
         "department_id": dept_id, "platform_role": "department_admin",
         "_label": "nt_admin (администратор отдела НТ)"},
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
    ]
    created_users = {}
    for u in users:
        label = u.pop("_label")
        s, b = post(auth, "/api/auth/v1/users", u)
        must(s, b, label)
        created_users[u["username"]] = b.get("user_id")

    # ── Роль loging.reader для nt_senior ─────────────────────────────────────
    section("Роли в loging_service")
    # Назначаем nt_senior роль reader в loging_service
    nt_senior_id = created_users.get("nt_senior")
    if nt_senior_id:
        status_r, body_r = post(auth, f"/api/auth/v1/services/loging_service/roles/reader/assign",
                                {"user_id": nt_senior_id})
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
    {'nt_admin':<16}  {'department_admin (НТ)':<26}  Логи НТ (только чтение)
    {'nt_senior':<16}  {'reader в loging_service':<26}  Логи НТ (только чтение)
    {'nt_developer':<16}  {'пользователь НТ':<26}  —
    {'nt_viewer':<16}  {'пользователь НТ':<26}  —

  Документация API:

    auth_service    →  http://localhost:8000/docs
    loging_service  →  http://localhost:8001/docs

  Authorize → OAuth2Password:
    auth_service   : admin / {DEV_PASS}
    loging_service : loging_admin / {DEV_PASS}   (полный доступ)
                   : nt_senior / {DEV_PASS}       (читатель логов НТ)

  Ротация логов:
    Все события → 90 дней (настраивается через PUT /api/logging/v1/retention)
    loging_service события защищены от ротации всегда.

  Остановить: make dev-stop
""")


if __name__ == "__main__":
    main()
