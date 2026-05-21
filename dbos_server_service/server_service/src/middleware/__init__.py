"""ASGI/HTTP middleware'ы server_service.

Сейчас здесь живёт ``platform_admin_guard`` — глобальный block для
``account_admin``/``loging_admin``/``loging_reader``, реализующий §7–8
security-модели («разделение администраторских плоскостей»).
"""
