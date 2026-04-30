"""API v1 router."""

from fastapi import APIRouter

from src.api.v1.endpoints import auth, events, health, retention, rules, services

v1_router = APIRouter()

v1_router.include_router(health.router)
v1_router.include_router(auth.router)
v1_router.include_router(events.router, prefix="/events", tags=["events"])
v1_router.include_router(rules.router, prefix="/rules", tags=["rules"])
v1_router.include_router(services.router, prefix="/services", tags=["services"])
v1_router.include_router(retention.router, prefix="/retention", tags=["retention"])
