"""Сборка v1-роутера: подключаем все endpoint-файлы.

Каталог тестов/очередь/СТП/логи/отчёты (§2-9 плана миграции) добавят свои
роутеры сюда по мере реализации.
"""

from fastapi import APIRouter

from src.api.v1.endpoints.department_test_settings import router as department_test_settings_router
from src.api.v1.endpoints.global_variables import router as global_variables_router
from src.api.v1.endpoints.health import router as health_router
from src.api.v1.endpoints.test_command_args import router as test_command_args_router
from src.api.v1.endpoints.test_definitions import router as test_definitions_router
from src.api.v1.endpoints.test_stands import router as test_stands_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(global_variables_router, tags=["global-variables"])
router.include_router(test_definitions_router, tags=["test-definitions"])
router.include_router(test_command_args_router, tags=["test-definitions"])
router.include_router(test_stands_router, tags=["test-stands"])
router.include_router(department_test_settings_router, tags=["department-test-settings"])
