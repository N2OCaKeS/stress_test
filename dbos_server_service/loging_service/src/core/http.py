"""HTTP-хелперы для outbound запросов loging_service.

Маленький модуль с общими header-builder'ами. Держим локально, не в shared lib:
service-to-service контракт у каждого сервиса свой, копия в 4 строки дешевле
кросс-сервисной зависимости.
"""

from __future__ import annotations


def bearer_header(token: str) -> dict[str, str]:
    """Return Authorization header dict for the given bearer token."""
    return {"Authorization": f"Bearer {token}"}
