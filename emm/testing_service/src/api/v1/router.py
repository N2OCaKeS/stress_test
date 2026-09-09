"""Сборка v1-роутера: подключаем все endpoint-файлы.

Каталог тестов/очередь/СТП/логи/отчёты (§2-9 плана миграции) добавят свои
роутеры сюда по мере реализации.
"""

from fastapi import APIRouter

from src.api.v1.endpoints.global_variables import router as global_variables_router
from src.api.v1.endpoints.health import router as health_router

router = APIRouter()
router.include_router(health_router, tags=["health"])
router.include_router(global_variables_router, tags=["global-variables"])
