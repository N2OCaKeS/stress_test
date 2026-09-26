"""ASGI/HTTP middleware'ы server_service.

* ``platform_admin_guard`` — глобальный block для
  ``account_admin``/``loging_admin``, реализующий разделение
  администраторских плоскостей.
* ``https_guard.HTTPSRequiredMiddleware`` — в production/staging отбивает
  cleartext-HTTP с 403 ``HTTPS_REQUIRED``. Health/ready пропускаются.
"""
