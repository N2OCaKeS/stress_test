"""Корневой API-router. Подключает все v1-эндпоинты под `/auth/v1`."""

from fastapi import APIRouter

from src.api.v1.router import router as v1_router

api_router = APIRouter()
api_router.include_router(v1_router, prefix="/auth/v1")
