"""Мелкие константы testing_worker'а."""

from __future__ import annotations

# Identity, под которой testing_worker представляется testing_service на
# internal-канале очереди (`X-Service-Identity`). Должна совпадать с ключом
# в SERVICE_API_KEYS['testing_worker'] на стороне testing_service.
SERVICE_NAME = "testing_worker"
