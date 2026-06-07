"""HTTP-хелперы для outbound запросов secret_service."""

from __future__ import annotations


def bearer_header(token: str) -> dict[str, str]:
    """Authorization header dict для заданного bearer-токена."""
    return {"Authorization": f"Bearer {token}"}
