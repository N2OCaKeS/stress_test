# client.py
import httpx
from fastapi import HTTPException, status
from app.api.v1.schemas.server import PhysicalServer, PhysicalServerStatusOnly
from app.utils.config import settings

MANAGE_API_BASE = settings.SERVER_API_BASE


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
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Remote API error {resp.status_code}: {resp.text[:200]}",
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
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Remote API error {resp.status_code}: {resp.text[:200]}",
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
        raise HTTPException(status_code=resp.status_code, detail=resp.text)

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Remote API error {resp.status_code}: {resp.text[:200]}",
    )
