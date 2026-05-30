"""Канонический список audit-событий, которые эмитит auth_service.

При старте отсылается в loging_service через `register_events()`. Новые
события, добавленные сюда, автоматически зарегистрируются при следующем
рестарте.
"""

from src.core.config import get_settings

import logging
import httpx

logger = logging.getLogger("audit")

# Запись: action, human description, default_severity (для success; для failure
# loging_service переопределит на WARNING/CRITICAL через свои правила)
SERVICE_EVENTS = [
    # Startup
    {"action": "service.started", "description": "Service started up", "default_severity": "INFO"},
    # HTTP middleware
    {"action": "http.access_denied", "description": "HTTP 401/403 response", "default_severity": "CRITICAL"},
    {"action": "http.client_error", "description": "HTTP 4xx response (except 401/403)", "default_severity": "WARNING"},
    {"action": "http.server_error", "description": "HTTP 5xx response", "default_severity": "CRITICAL"},
    # Authentication & sessions
    {"action": "user.login", "description": "User authentication attempt", "default_severity": "INFO"},
    {"action": "user.refresh", "description": "Access token refresh", "default_severity": "INFO"},
    {"action": "user.logout", "description": "User session termination", "default_severity": "INFO"},
    {"action": "user.me", "description": "Get current user identity", "default_severity": "INFO"},
    {"action": "token.refresh_reuse", "description": "Refresh token reuse detected (possible theft)", "default_severity": "CRITICAL"},
    {"action": "token.refresh_race", "description": "Concurrent refresh-token rotation lost CAS (benign race, retry expected)", "default_severity": "INFO"},
    # Users
    {"action": "user.create", "description": "New user account created", "default_severity": "INFO"},
    {"action": "user.list", "description": "User list retrieved (global or per-department)", "default_severity": "INFO"},
    {"action": "user.update", "description": "User profile updated", "default_severity": "INFO"},
    {"action": "user.roles_assign", "description": "Service roles assigned to user", "default_severity": "INFO"},
    {"action": "user.password_reset", "description": "User password reset", "default_severity": "CRITICAL"},
    {"action": "user.self_password_reset", "description": "User changed own password via /users/me/password", "default_severity": "CRITICAL"},
    {"action": "user.ban", "description": "User account banned", "default_severity": "CRITICAL"},
    {"action": "user.unban", "description": "User account unbanned", "default_severity": "CRITICAL"},
    {"action": "user.ban_deactivated_via_status_change", "description": "Active ban deactivated as side-effect of PATCH /users/{id}/status", "default_severity": "WARNING"},
    {"action": "user.permissions_view", "description": "User permissions snapshot retrieved (GET /users/{id}/permissions)", "default_severity": "INFO"},
    {"action": "user.roles_purged_on_transfer", "description": "User service-roles purged after department transfer", "default_severity": "WARNING"},
    {"action": "user.groups_purged_on_transfer", "description": "User group memberships of the old department purged after transfer", "default_severity": "WARNING"},
    {"action": "user.sessions_listed", "description": "User listed own active sessions (GET /users/me/sessions)", "default_severity": "INFO"},
    {"action": "user.sessions_revoked_all", "description": "User revoked all own sessions (POST /users/me/sessions/revoke)", "default_severity": "CRITICAL"},
    {"action": "user.session_revoked_one", "description": "User revoked one own session (DELETE /users/me/sessions/{id})", "default_severity": "WARNING"},
    # Departments
    {"action": "department.create", "description": "New department created", "default_severity": "CRITICAL"},
    {"action": "department.list", "description": "Department list retrieved", "default_severity": "INFO"},
    {"action": "department.service_grant", "description": "Service access granted to department", "default_severity": "CRITICAL"},
    {"action": "department.service_revoke", "description": "Service access revoked from department", "default_severity": "CRITICAL"},
    # Groups
    {"action": "group.create", "description": "New group created", "default_severity": "INFO"},
    {"action": "group.update", "description": "Group updated", "default_severity": "INFO"},
    {"action": "group.delete", "description": "Group deleted", "default_severity": "CRITICAL"},
    {"action": "group.member_add", "description": "Member added to group", "default_severity": "WARNING"},
    {"action": "group.member_remove", "description": "Member removed from group", "default_severity": "WARNING"},
    {"action": "group.bot_member_add", "description": "Bot added to group", "default_severity": "WARNING"},
    {"action": "group.bot_member_remove", "description": "Bot removed from group", "default_severity": "WARNING"},
    {"action": "group.service_grant", "description": "Service access granted to group", "default_severity": "CRITICAL"},
    {"action": "group.service_revoke", "description": "Service access revoked from group", "default_severity": "CRITICAL"},
    {"action": "group.roles_assign", "description": "Roles assigned to group", "default_severity": "CRITICAL"},
    {"action": "group.roles_revoke", "description": "Roles revoked from group", "default_severity": "CRITICAL"},
    # Platform services
    {"action": "service.create", "description": "Platform service registered", "default_severity": "CRITICAL"},
    {"action": "service.delete", "description": "Platform service deleted", "default_severity": "CRITICAL"},
    {"action": "service.list", "description": "Platform service list retrieved", "default_severity": "INFO"},
    {"action": "service.access_check", "description": "Service access check (introspect)", "default_severity": "INFO"},
    # Service roles
    {"action": "service_role.create", "description": "Service role created", "default_severity": "INFO"},
    {"action": "service_role.update", "description": "Service role updated", "default_severity": "INFO"},
    {"action": "service_role.delete", "description": "Service role deleted", "default_severity": "CRITICAL"},
    {"action": "service_role.bulk_assign", "description": "Bulk role assignment", "default_severity": "INFO"},
    {"action": "service_role.bulk_revoke", "description": "Bulk role revocation", "default_severity": "INFO"},
    # Personal Access Tokens
    {"action": "pat.create", "description": "Personal access token created", "default_severity": "INFO"},
    {"action": "pat.list", "description": "Personal access token list retrieved", "default_severity": "INFO"},
    {"action": "pat.revoke", "description": "Personal access token revoked", "default_severity": "WARNING"},
    # Bots
    {"action": "bot.create", "description": "Bot account created", "default_severity": "WARNING"},
    {"action": "bot.list", "description": "Bot list retrieved", "default_severity": "INFO"},
    {"action": "bot.update", "description": "Bot updated", "default_severity": "WARNING"},
    {"action": "bot.token_create", "description": "Bot token created", "default_severity": "WARNING"},
    {"action": "bot.token_list", "description": "Bot token list retrieved", "default_severity": "INFO"},
    {"action": "bot.token_revoke", "description": "Bot token revoked", "default_severity": "WARNING"},
    {"action": "bot.token_expired", "description": "Expired bot token rejected at introspect", "default_severity": "WARNING"},
    {"action": "bot.roles_assign", "description": "Bot service-roles assigned", "default_severity": "WARNING"},
    {"action": "bot.roles_list", "description": "Bot service-roles retrieved", "default_severity": "INFO"},
    {"action": "bot.roles_revoke", "description": "Bot service-roles revoked", "default_severity": "WARNING"},
    {"action": "bot.roles_purged_on_services_narrowed", "description": "Bot service-roles purged because allowed_services was narrowed", "default_severity": "WARNING"},
    {"action": "bot.suspicious_multi_ip", "description": "Bot token used from multiple distinct IPs within short window", "default_severity": "CRITICAL"},
    # OAuth2 clients
    {"action": "oauth_client.create", "description": "OAuth2 client registered", "default_severity": "CRITICAL"},
    {"action": "oauth_client.list", "description": "OAuth2 client list retrieved", "default_severity": "INFO"},
    {"action": "oauth_client.delete", "description": "OAuth2 client deleted", "default_severity": "CRITICAL"},
    {"action": "oauth.authorization_code_issued", "description": "OAuth2 authorization code issued", "default_severity": "INFO"},
    {"action": "oauth.code_exchanged", "description": "OAuth2 authorization code exchanged for token", "default_severity": "INFO"},
    {"action": "oauth.client_credentials_token", "description": "OAuth2 client credentials token issued", "default_severity": "INFO"},
    # Token introspection
    {"action": "token.introspect", "description": "Token introspection request", "default_severity": "INFO"},
    # Docker Registry
    {"action": "docker_registry.configure", "description": "Docker registry configured", "default_severity": "CRITICAL"},
    {"action": "docker_registry.update", "description": "Docker registry configuration updated", "default_severity": "CRITICAL"},
    {"action": "docker_registry.get_config", "description": "Docker registry configuration retrieved", "default_severity": "INFO"},
    {"action": "docker_registry.disable", "description": "Docker registry disabled", "default_severity": "CRITICAL"},
    {"action": "docker.token_issued", "description": "Docker registry JWT token issued", "default_severity": "INFO"},
    {"action": "docker.push_denied", "description": "Docker registry push action denied", "default_severity": "WARNING"},
    {"action": "docker.pull_denied", "description": "Docker registry pull action denied", "default_severity": "INFO"},
]


def register_events() -> None:
    """POST'ит полный список событий в loging_service.

    Вызывается на startup в background-потоке. Если `LOGGING_SERVICE_URL` не
    задан — пропускаем (dev-сценарий без logging-сервиса). HTTP-ошибки
    логгируются как WARNING, но не валят процесс.
    """
    settings = get_settings()
    logging_url = getattr(settings, "logging_service_url", None)
    api_key = getattr(settings, "logging_service_api_key", None)
    if not logging_url or not api_key:
        logger.debug("audit: skipping event registration — LOGGING_SERVICE_URL not configured")
        return
    try:
        resp = httpx.post(
            f"{logging_url}/api/logging/v1/services/auth_service/events",
            json={"events": SERVICE_EVENTS},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            logger.info(
                "audit: registered %d events (added=%d updated=%d)",
                data.get("total"), data.get("added"), data.get("updated"),
            )
        else:
            logger.warning("audit: event registration failed: %s %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("audit: event registration error: %s", exc)
