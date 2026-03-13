# utils/auth.py
from __future__ import annotations
import json
import os
import time
from typing import Optional

import requests

from allta_cli.utils import ui
from allta_cli.utils.config import API_BASE_URL, SESSION_FILE, TOKEN_TTL_HOURS_DEFAULT

SESSION_AUTH_TYPE_PASSWORD = "password"
SESSION_AUTH_TYPE_API_TOKEN = "api_token"

# ==== Исключения (читабельные) ====
class AuthError(RuntimeError):
    """Общая ошибка авторизации/хранилища."""

class NotAuthenticatedError(AuthError):
    """Нет локальной сессии — требуется вход."""

class TokenExpiredError(AuthError):
    """Локальный токен протух по TTL."""

# ==== Внутренняя утилита логирования (единый стиль) ====
def _echo(msg: str, *, ok: bool | None = None, verbose: bool = False):
    """
    Единая обёртка для «болтливого» режима.
    ok=True  -> зелёный ✓ через ui.ok
    ok=False -> красный ✗ через ui.err
    ok=None  -> обычный ui.echo
    """
    if not verbose:
        return
    if ok is True:
        ui.ok(msg)
    elif ok is False:
        ui.err(msg)
    else:
        ui.echo(msg)

# ==== Вспомогательные функции для хранилища ====
def _ensure_dirs_and_perms():
    dir_ = SESSION_FILE.parent
    dir_.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dir_, 0o700)
    except Exception:
        pass

def _write_session(
    token: str,
    login: str | None = None,
    auth_type: str = SESSION_AUTH_TYPE_PASSWORD,
):
    _ensure_dirs_and_perms()
    payload = {"token": token, "iat": int(time.time()), "auth_type": auth_type}
    if login:
        payload["login"] = login
    SESSION_FILE.write_text(json.dumps(payload), encoding="utf-8")
    try:
        os.chmod(SESSION_FILE, 0o600)
    except Exception:
        pass

def _read_session() -> dict:
    if not SESSION_FILE.exists():
        raise NotAuthenticatedError("Не авторизованы. Выполните вход: allta login <login>.")
    try:
        return json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        raise AuthError("Файл сессии повреждён. Выполните вход заново.")

def _delete_session_silent():
    try:
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
    except Exception:
        pass

# ==== Публичные функции ====
def login(
    *,
    login: str,
    password: str,
    api_base_url: Optional[str] = None,
    verbose: bool = False,
) -> str:
    """
    Входит через /login, сохраняет файл сессии (token + iat) и возвращает токен.
    Бросает AuthError/NotAuthenticatedError при ошибках.
    """
    base = (api_base_url or API_BASE_URL).rstrip("/")
    url = f"{base}:21500/api/auth/login"
    data = {"username": login, "password": password, "grant_type": "password"}

    if verbose:
        ui.http(f"POST {url}")

    try:
        r = requests.post(url, data=data, timeout=20)
    except requests.RequestException as e:
        raise AuthError(f"Не удалось подключиться к серверу авторизации: {e}") from e

    if r.status_code == 401:
        raise AuthError("Неверный логин или пароль.")
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise AuthError(f"Ошибка авторизации: HTTP {r.status_code}") from e

    try:
        js = r.json()
        token = js.get("access_token")
    except ValueError:
        raise AuthError("Сервер вернул не-JSON ответ при входе.")
    if not token:
        raise AuthError("Сервер не вернул access_token.")

    _write_session(token, login=login, auth_type=SESSION_AUTH_TYPE_PASSWORD)
    _echo("Успешный вход. Токен сохранён локально.", ok=True, verbose=verbose)
    return token


def login_with_api_token(
    *,
    login: str,
    api_token: str,
    api_base_url: Optional[str] = None,
    verbose: bool = False,
) -> str:
    """
    Проверяет API token через /verify, сохраняет его локально и возвращает токен.
    """
    base = (api_base_url or API_BASE_URL).rstrip("/")
    url = f"{base}:21500/api/auth/verify"
    headers = {"Authorization": f"Bearer {api_token}"}

    if verbose:
        ui.http(f"GET {url}")

    try:
        r = requests.get(url, headers=headers, timeout=20)
    except requests.RequestException as e:
        raise AuthError(f"Не удалось подключиться к серверу авторизации: {e}") from e

    if r.status_code == 401:
        raise AuthError("Неверный или отозванный API token.")
    try:
        r.raise_for_status()
    except requests.HTTPError as e:
        raise AuthError(f"Ошибка проверки API token: HTTP {r.status_code}") from e

    try:
        js = r.json()
    except ValueError:
        raise AuthError("Сервер вернул не-JSON ответ при проверке API token.")

    token_login = js.get("login")
    if not token_login:
        raise AuthError("Сервер не вернул login при проверке API token.")
    if str(token_login) != login:
        raise AuthError(f"API token принадлежит пользователю '{token_login}', а указан '{login}'.")

    _write_session(api_token, login=login, auth_type=SESSION_AUTH_TYPE_API_TOKEN)
    _echo("Успешный вход по API token. Токен сохранён локально.", ok=True, verbose=verbose)
    return api_token


def logout(
    *,
    api_base_url: Optional[str] = None,
    verbose: bool = False,
) -> None:
    """
    Отзывает текущий токен на сервере (/logout) и удаляет локальный файл сессии.
    Не падает, если токен уже невалиден, но сообщит об этом при verbose=True.
    """
    base = (api_base_url or API_BASE_URL).rstrip("/")
    url = f"{base}/api/auth/logout"

    token = None
    try:
        data = _read_session()
        token = data.get("token")
    except AuthError:
        _delete_session_silent()
        _echo("Нет локальной сессии, ничего удалять.", verbose=verbose)
        return

    auth_type = str(data.get("auth_type") or SESSION_AUTH_TYPE_PASSWORD)

    if auth_type == SESSION_AUTH_TYPE_API_TOKEN:
        _delete_session_silent()
        _echo(
            "Локальная сессия, созданная через API token, удалена. Сам API token на сервере не отзывался.",
            ok=True,
            verbose=verbose,
        )
        return

    headers = {"Authorization": f"Bearer {token}"} if token else {}

    if verbose:
        ui.http(f"POST {url} (revoke)")

    try:
        r = requests.post(url, headers=headers, timeout=15)
        if r.status_code not in (200, 204, 401):
            r.raise_for_status()
    except requests.RequestException as e:
        _delete_session_silent()
        raise AuthError(f"Ошибка при отзыве токена: {e}") from e

    _delete_session_silent()
    if r.status_code == 401:
        _echo("Локальная сессия удалена. Сервер считал токен недействительным (401).", ok=True, verbose=verbose)
    else:
        _echo("Токен отозван на сервере и локально удалён.", ok=True, verbose=verbose)


def load_token(
    *,
    verbose: bool = False,
) -> str:
    """
    Загружает токен из файла и проверяет TTL (по умолчанию 10 часов).
    Возвращает токен или бросает TokenExpiredError/NotAuthenticatedError/AuthError.
    """
    ttl_h = TOKEN_TTL_HOURS_DEFAULT
    data = _read_session()
    token = data.get("token")
    iat = int(data.get("iat", 0))
    auth_type = str(data.get("auth_type") or SESSION_AUTH_TYPE_PASSWORD)

    if not token or not iat:
        raise AuthError("Сессия неполная. Авторизуйтесь заново.")

    if auth_type == SESSION_AUTH_TYPE_API_TOKEN:
        _echo("API token загружен из локальной сессии.", ok=True, verbose=verbose)
        return str(token)

    age_sec = time.time() - iat
    if age_sec > ttl_h * 3600:
        raise TokenExpiredError(f"Срок действия токена истёк: более {ttl_h} ч с момента входа.")

    _echo(f"Токен валиден локально (возраст ~{int(age_sec/60)} мин).", ok=True, verbose=verbose)
    return token


def current_login() -> str:
    """
    Возвращает логин, сохранённый при входе. Бросает NotAuthenticatedError/AuthError при проблемах.
    """
    data = _read_session()
    login = data.get("login")
    if not login:
        raise AuthError("В сессии не найден логин. Выполните вход заново.")
    return str(login)
