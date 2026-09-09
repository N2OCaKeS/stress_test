"""Общие enum'ы и константы сервиса.

Минимальная заготовка для каркасной волны. `EntityType`/действия для матрицы
RBAC (§10 плана миграции) появятся вместе с доменными сущностями (волна 3+).
"""

SERVICE_NAME = "testing_service"

# Health/ready paths, исключаемые из rate-limit / audit / introspect.
HEALTH_PATHS: frozenset[str] = frozenset({
    "/api/testing/v1/health",
    "/api/testing/v1/ready",
})
