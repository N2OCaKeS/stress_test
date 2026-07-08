"""Клиент internal-API server_service.

Нужен только в ssh-режиме: VNC/SPICE-сокет qemu по умолчанию слушает на
127.0.0.1 хаба, снаружи его не видно. Чтобы пробросить порт, прокси открывает
SSH-туннель на хаб под управляющей учёткой сервера — эти креды server_service
отдаёт по internal-эндпоинту (тот же worker_bot-грант `view_management_credentials`,
что использует server_worker). Ключ доступа прокси кладётся в inbound
SERVICE_API_KEYS-map server_service отдельной записью `console_proxy`.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class HubCredentials:
    management_user: str
    ssh_private_key: str | None
    password: str | None


class InternalClientError(Exception):
    pass


class InternalClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        api_key_name: str,
        timeout: int = 10,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._api_key_name = api_key_name
        self._timeout = timeout

    async def fetch_hub_credentials(
        self, server_id: str, *, target_department_id: str | None = None
    ) -> HubCredentials:
        """GET /internal/servers/{id}/management/credentials."""
        url = f"{self._base_url}/api/server/v1/internal/servers/{server_id}/management/credentials"
        headers = {
            "X-Service-Name": self._api_key_name,
            "X-API-Key": self._api_key,
        }
        if target_department_id:
            headers["X-Target-Department-Id"] = target_department_id
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise InternalClientError(f"server_service unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise InternalClientError(
                f"management credentials fetch failed: {resp.status_code}"
            )
        data = resp.json()
        return HubCredentials(
            management_user=data["management_user"],
            ssh_private_key=data.get("ssh_private_key"),
            password=data.get("password"),
        )
