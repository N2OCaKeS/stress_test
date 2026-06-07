"""Общие enum'ы и константы сервиса."""

SERVICE_NAME = "secret_service"

# Health/ready paths, исключаемые из rate-limit / audit / introspect.
HEALTH_PATHS: frozenset[str] = frozenset({
    "/api/secret/v1/health",
    "/api/secret/v1/ready",
})
