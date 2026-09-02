"""Корневой API-роутер. Всё под `/api/logging/v1/`."""

from fastapi import APIRouter

from src.api.v1.router import v1_router

api_router = APIRouter()

api_router.include_router(v1_router, prefix="/logging/v1")
