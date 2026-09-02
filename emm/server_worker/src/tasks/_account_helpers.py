"""Общие хелперы account-flow'а для SSH-task'ов.

Здесь живёт логика «достать SSH-credentials для шага под аккаунтом» —
управляемый сценарий (вход по ключу под management_user, пароль не нужен)
и self-сценарий (вход по паролю под самим аккаунтом). Раньше блок
дублировался в `inventory.py`, `users.py` и `installed_packages.py`.
"""

from __future__ import annotations

from src.core.exceptions import CredentialFetchError
from src.services import server_service_client


async def resolve_ssh_creds(
    payload: dict,
    server_id: str,
    *,
    account_id: str | None = None,
    target_dept: str | None = None,
    is_managed: bool = False,
) -> dict:
    """Достать SSH-credentials для шага под аккаунтом.

    Семантика:
      * `is_managed=True` или `account_id` пустой — вход по ключу под
        management_user; пароль аккаунта на этом шаге не нужен (его может
        вообще не быть у discovered-аккаунта). Возвращаем skeleton с
        `login` из `payload["ssh_login"]` (fallback `"root"`).
      * Иначе — fetch пароля аккаунта из server_service. Возвращаемый
        dict содержит `login` + `password` (+ другие поля от
        server_service: `host`, `ssh_port`, ...).

    Caller затем дёргает `ssh_client.apply_session_hints(creds, payload)`,
    чтобы догнать `host`/`ssh_port`/managed-флаги из payload.
    """
    if account_id and not is_managed:
        creds: dict = await server_service_client.fetch_account_password(
            server_id, account_id, target_dept,
        )
    else:
        creds = {"login": payload.get("ssh_login", "root")}
    return creds


__all__ = ["resolve_ssh_creds", "CredentialFetchError"]
