"""HTTP-хелперы для outbound запросов auth_service.

Маленький модуль с общими header-builder'ами. Держим локально, не в shared lib:
service-to-service контракт у каждого сервиса свой, копия в 4 строки дешевле
кросс-сервисной зависимости.

SOURCE OF TRUTH: dbos_server_service/sdk/bearer.py — при правке `bearer_header`
обновить эталон и все четыре копии (`auth_service`, `loging_service`,
`server_service`, `server_worker`).
"""

from __future__ import annotations


# SOURCE OF TRUTH: dbos_server_service/sdk/bearer.py::bearer_header
def bearer_header(token: str) -> dict[str, str]:
    """Return Authorization header dict for the given bearer token."""
    return {"Authorization": f"Bearer {token}"}
