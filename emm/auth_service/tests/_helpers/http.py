"""Общие helper-функции для HTTP-вызовов в тестах.

До этого `_basic`, `_login`, `_create_bot` дублировались по 5-13 раз в разных
test-модулях. Здесь — одна каноническая реализация. Старые модули импортируют
их (alias на уровне модуля) для обратной совместимости.
"""

from __future__ import annotations

import base64


LOGIN_URL = "/api/auth/v1/login"
BOTS_URL = "/api/auth/v1/bots"


def basic_auth_header(username: str, password: str) -> dict[str, str]:
    """Authorization: Basic <b64(username:password)> header dict."""
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


# Backward-compat aliases — старые тесты импортируют под этими именами.
_basic = basic_auth_header
_basic_auth = basic_auth_header
_basic_hdr = basic_auth_header


async def login(client, username: str = "t_admin", password: str = "Admin12345678!") -> dict:
    """POST /login — возвращает JSON-ответ (access_token, refresh_token, identity)."""
    r = await client.post(LOGIN_URL, json={"username": username, "password": password})
    assert r.status_code == 200, f"login failed for {username}: {r.text}"
    return r.json()


async def login_token(client, username: str = "t_admin", password: str = "Admin12345678!") -> str:
    """POST /login — возвращает только access_token."""
    data = await login(client, username, password)
    return data["access_token"]


_login = login


async def create_bot(client, token: str, dept_id: str, name: str = "test_bot",
                     services: list[str] | None = None):
    """POST /bots — Bearer-авторизация, dept_id, allowed_services."""
    return await client.post(
        BOTS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": name,
            "department_id": dept_id,
            "allowed_services": services or [],
        },
    )


_create_bot = create_bot
