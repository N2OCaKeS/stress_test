"""ASGI/HTTP middleware'ы secret_service.

* ``HTTPSRequiredMiddleware`` — в production/staging отбивает cleartext-HTTP
  с 403 ``HTTPS_REQUIRED``. Health/ready пропускаются.
* ``AuditAccessMiddleware`` — на 4xx/5xx ответе эмитит ``http.*`` audit-event.
"""

from src.middleware.audit_middleware import AuditAccessMiddleware
from src.middleware.https_guard import HTTPSRequiredMiddleware

__all__ = [
    "AuditAccessMiddleware",
    "HTTPSRequiredMiddleware",
]
