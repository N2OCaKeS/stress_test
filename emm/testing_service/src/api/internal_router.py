"""Внутренние (`/internal/...`) роутеры — без `/api/testing/v1` namespace.

Подключаются напрямую к `app` в `main.py`, а не через `api_router`: эти пути
зашиты в код каллеров буквально (server_service'овский callback,
`testing_worker`'ский поллинг) — версионировать их вместе с публичным API
смысла нет, контракт и так фиксирован на уровне кода обеих сторон.
"""

from fastapi import APIRouter

from src.api.v1.endpoints.internal_log import router as log_router
from src.api.v1.endpoints.internal_prepare_for_test import router as prepare_for_test_router
from src.api.v1.endpoints.internal_queue import router as queue_router
from src.api.v1.endpoints.internal_stand_setup import router as stand_setup_router

internal_router = APIRouter()
internal_router.include_router(prepare_for_test_router)
internal_router.include_router(queue_router)
internal_router.include_router(log_router)
internal_router.include_router(stand_setup_router)
