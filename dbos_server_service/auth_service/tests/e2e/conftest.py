"""E2E-фикстуры: подключение к реальным сервисам.

Тесты этой директории обращаются к:
  - auth-service-e2e  (реальный uvicorn-процесс, E2E_AUTH_SERVICE_URL)
  - docker-registry   (контейнер registry:2, E2E_REGISTRY_HOST)

Проверяется полный протокол Docker token auth по HTTP — без Docker CLI.

Переменные окружения (устанавливаются docker-compose.test.yml или run_devcont_tests.py):
  E2E_AUTH_SERVICE_URL   например http://auth-service-e2e:8000
  E2E_REGISTRY_HOST      например docker-registry:5000
"""

import os
import time

import pytest
import requests

# ── Координаты сервисов ───────────────────────────────────────────────────────

AUTH_URL = os.environ.get("E2E_AUTH_SERVICE_URL", "http://auth-service-e2e:8000")
REGISTRY_HOST = os.environ.get("E2E_REGISTRY_HOST", "docker-registry:5000")

_E2E_ADMIN = "e2e_admin"
_E2E_ADMIN_PW = "E2eAdmin1234!"
_E2E_USER = "e2e_user"
_E2E_USER_PW = "E2eUser1234!"
_E2E_DEPT = "e2e_department"


# ── Проверка доступности сервисов ────────────────────────────────────────────

def _wait_http(url: str, timeout: int = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=3).status_code < 500:
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"Service not reachable at {url} after {timeout}s")


def _wait_registry(host: str, timeout: int = 120) -> None:
    """Ждать пока registry не вернёт 401 — это значит что он поднялся и авторизация настроена."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"http://{host}/v2/", timeout=3)
            if r.status_code in (200, 401):
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"Registry not reachable at {host} after {timeout}s")


# ── Session-фикстуры: ожидание готовности сервисов ───────────────────────────

@pytest.fixture(scope="session", autouse=True)
def _services_ready():
    _wait_http(f"{AUTH_URL}/api/auth/v1/health")
    _wait_registry(REGISTRY_HOST)


# ── Session-фикстуры: админ, отдел, включение Docker registry ────────────────

@pytest.fixture(scope="session")
def e2e_admin_token(_services_ready):
    """Создать (или переиспользовать) E2E-администратора и вернуть access-токен."""
    s = requests.Session()
    # Сначала пробуем войти — пользователь мог существовать с предыдущего запуска
    r = s.post(f"{AUTH_URL}/api/auth/v1/login",
               json={"username": _E2E_ADMIN, "password": _E2E_ADMIN_PW})
    if r.status_code == 200:
        return r.json()["access_token"]

    # Пользователь не найден — E2E-тесты будут пропущены.
    # Администратор создаётся через scripts/seed_e2e.py перед запуском тестов.
    pytest.skip(
        "E2E-администратор не найден. "
        "Убедитесь, что scripts/seed_e2e.py был выполнен перед запуском тестов."
    )


@pytest.fixture(scope="session")
def e2e_api(e2e_admin_token):
    """Requests-сессия с предустановленным Bearer-токеном администратора."""
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {e2e_admin_token}"
    return s, AUTH_URL


@pytest.fixture(scope="session")
def e2e_dept(e2e_api):
    """Создать тестовый отдел (идемпотентно). Возвращает dict с ключом `department_id`."""
    s, base = e2e_api
    r = s.post(f"{base}/api/auth/v1/departments",
               json={"name": _E2E_DEPT, "display_name": "E2E Department"})
    assert r.status_code in (201, 409), f"create dept failed: {r.text}"
    if r.status_code == 201:
        return r.json()
    # 409 = already exists; fetch it
    depts = s.get(f"{base}/api/auth/v1/departments").json()
    return next(d for d in depts if d["name"] == _E2E_DEPT)


@pytest.fixture(scope="session")
def e2e_user(e2e_api, e2e_dept):
    """Создать (или переиспользовать) обычного пользователя в e2e_dept."""
    s, base = e2e_api
    # DepartmentResponse использует ключ `department_id`, не `id`
    dept_id = e2e_dept["department_id"]
    r = s.post(f"{base}/api/auth/v1/users",
               json={"username": _E2E_USER, "password": _E2E_USER_PW,
                     "department_id": dept_id})
    assert r.status_code in (201, 409), f"create user failed: {r.text}"
    if r.status_code == 201:
        return r.json()
    users = s.get(f"{base}/api/auth/v1/users").json()
    return next(u for u in users if u["username"] == _E2E_USER)


_E2E_SERVICE = "e2e_service"


@pytest.fixture(scope="session")
def e2e_service(e2e_api, e2e_dept):
    """Создать платформенный сервис и выдать e2e_dept доступ к нему.

    PAT-create требует непустой `allowed_services`, и каждый сервис в нём
    должен быть в `dept.allowed_services`. Без этой фикстуры PAT-тесты
    в e2e падали бы на 422 / 403.
    """
    s, base = e2e_api
    # Создаём (или переиспользуем) платформенный сервис.
    r = s.post(f"{base}/api/auth/v1/services",
               json={"service_name": _E2E_SERVICE, "display_name": "E2E Service"})
    assert r.status_code in (201, 409), f"create service failed: {r.text}"
    # Выдаём отделу доступ — идемпотентно.
    dept_id = e2e_dept["department_id"]
    s.post(
        f"{base}/api/auth/v1/departments/{dept_id}/services",
        json={"service_name": _E2E_SERVICE},
    )
    return _E2E_SERVICE


@pytest.fixture(scope="session")
def e2e_user_token(e2e_user):
    r = requests.post(f"{AUTH_URL}/api/auth/v1/login",
                      json={"username": _E2E_USER, "password": _E2E_USER_PW})
    assert r.status_code == 200
    return r.json()["access_token"]


_E2E_USER_PULL_ONLY = "e2e_pull_only_user"
_E2E_USER_PULL_ONLY_PW = "E2ePullOnly1234!"


@pytest.fixture(scope="session")
def e2e_user_pull_only(e2e_api, e2e_dept):
    """Второй пользователь в e2e_dept — не входит в push_user_ids.

    Используется для проверки что push-scope даётся избирательно.
    Делать это через `e2e_admin` нельзя: account_admin не привязан к dept
    и не может получить docker_token вообще (DOCKER_ACCESS_DENIED)."""
    s, base = e2e_api
    dept_id = e2e_dept["department_id"]
    r = s.post(f"{base}/api/auth/v1/users",
               json={"username": _E2E_USER_PULL_ONLY,
                     "password": _E2E_USER_PULL_ONLY_PW,
                     "department_id": dept_id})
    assert r.status_code in (201, 409), f"create pull-only user failed: {r.text}"
    if r.status_code == 201:
        return r.json()
    users = s.get(f"{base}/api/auth/v1/users").json()
    return next(u for u in users if u["username"] == _E2E_USER_PULL_ONLY)


@pytest.fixture(scope="session")
def e2e_docker_enabled(e2e_api, e2e_dept, e2e_user):
    """Включить Docker registry для e2e_dept: pull_policy=all, пользователь в списке push."""
    s, base = e2e_api
    r = s.put(
        f"{base}/api/auth/v1/docker/registry/{e2e_dept['department_id']}",
        json={
            "pull_policy": "all",
            "pull_user_ids": [],
            "push_user_ids": [e2e_user["user_id"]],
        },
    )
    assert r.status_code == 200, f"enable docker failed: {r.text}"
    return r.json()


# ── Вспомогательные фикстуры уровня теста ────────────────────────────────────

@pytest.fixture()
def registry_host():
    return REGISTRY_HOST


@pytest.fixture()
def auth_base():
    return AUTH_URL
