import httpx
from urllib.parse import quote

from fastapi import HTTPException, status

from app.api.v1.schemas.server import (
    PhysicalServer,
    PhysicalServerStatusOnly,
    SnapshotPasswordRead,
)
from app.utils.config import settings

MANAGE_API_BASE = settings.SERVER_API_BASE


def _response_detail(resp: httpx.Response) -> str:
    try:
        payload = resp.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()

    text = (resp.text or "").strip()
    if text:
        return text
    return f"HTTP {resp.status_code}"


async def get_physical_server_from_remote(
    server_id: int,
    token: str,
    *,
    base_url: str = MANAGE_API_BASE,
    timeout_sec: float = 5.0,
) -> PhysicalServer:
    url = f"{base_url.rstrip('/')}/v1/manage/{server_id}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.get(url, headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Remote API unavailable: {e.__class__.__name__}",
        )

    if resp.status_code == 200:
        try:
            return PhysicalServer.model_validate(resp.json())
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Invalid payload from remote API: {e}",
            )

    if resp.status_code in (401, 403, 404):
        raise HTTPException(status_code=resp.status_code, detail=_response_detail(resp))

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Remote API error {resp.status_code}: {_response_detail(resp)[:200]}",
    )


async def set_server_status(
    server_id: int,
    status_value: str,
    token: str,
    *,
    base_url: str = MANAGE_API_BASE,
    timeout_sec: float = 5.0,
) -> PhysicalServerStatusOnly:
    url = f"{base_url.rstrip('/')}/v1/manage/{server_id}/status"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {"status": status_value}

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(url, headers=headers, json=payload)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Remote API unavailable: {e.__class__.__name__}",
        )

    if resp.status_code == 200:
        try:
            return PhysicalServerStatusOnly.model_validate(resp.json())
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Invalid payload from remote API: {e}",
            )

    if resp.status_code in (401, 403, 404):
        raise HTTPException(status_code=resp.status_code, detail=_response_detail(resp))

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Remote API error {resp.status_code}: {_response_detail(resp)[:200]}",
    )


async def clear_server_status(
    server_id: int,
    token: str,
    *,
    base_url: str = MANAGE_API_BASE,
    timeout_sec: float = 5.0,
) -> PhysicalServer:
    url = f"{base_url.rstrip('/')}/v1/manage/{server_id}/release"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(url, headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Remote API unavailable: {e.__class__.__name__}",
        )

    if resp.status_code == 200:
        try:
            return PhysicalServer.model_validate(resp.json())
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Invalid payload from remote API: {e}",
            )

    if resp.status_code in (401, 403, 404):
        raise HTTPException(status_code=resp.status_code, detail=_response_detail(resp))

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Remote API error {resp.status_code}: {_response_detail(resp)[:200]}",
    )


async def get_os_versions(
    token: str,
    *,
    base_url: str = settings.SERVER_API_BASE,
    timeout_sec: float = 5.0,
) -> list[dict]:
    url = f"{base_url.rstrip('/')}/v1/os-versions/?skip=0&limit=100"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.get(url, headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Remote API unavailable: {e.__class__.__name__}",
        )

    if resp.status_code == 200:
        try:
            return resp.json()
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Invalid payload from remote API: {e}",
            )

    if resp.status_code in (401, 403, 404):
        raise HTTPException(status_code=resp.status_code, detail=_response_detail(resp))

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Remote API error {resp.status_code}: {_response_detail(resp)[:200]}",
    )


async def get_snapshot_password_by_os_version(
    os_version_name: str,
    token: str,
    *,
    base_url: str = settings.SERVER_API_BASE,
    timeout_sec: float = 5.0,
) -> SnapshotPasswordRead:
    normalized_os_version_name = os_version_name.strip()
    if not normalized_os_version_name:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Remote server payload has empty os_version",
        )

    encoded_name = quote(normalized_os_version_name, safe="")
    url = f"{base_url.rstrip('/')}/v1/passwords/{encoded_name}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.get(url, headers=headers)
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Remote API unavailable: {e.__class__.__name__}",
        )

    if resp.status_code == 200:
        try:
            return SnapshotPasswordRead.model_validate(resp.json())
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Invalid snapshot password payload: {e}",
            )

    if resp.status_code == 404:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Snapshot password not found for os_version "
                f"'{normalized_os_version_name}'"
            ),
        )

    if resp.status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Cannot read snapshot passwords from server API "
                f"(HTTP {resp.status_code})"
            ),
        )

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Remote API error {resp.status_code}: {_response_detail(resp)[:200]}",
    )
