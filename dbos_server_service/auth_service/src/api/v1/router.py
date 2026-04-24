"""Version 1 API router aggregation."""

from fastapi import APIRouter

from src.api.v1.endpoints.auth import router as auth_router
from src.api.v1.endpoints.authorization import router as authorization_router
from src.api.v1.endpoints.bots import router as bots_router
from src.api.v1.endpoints.departments import router as departments_router
from src.api.v1.endpoints.docker_registry import router as docker_router
from src.api.v1.endpoints.groups import router as groups_router
from src.api.v1.endpoints.oauth2 import router as oauth2_router
from src.api.v1.endpoints.service_roles import router as service_roles_router
from src.api.v1.endpoints.services import router as services_router
from src.api.v1.endpoints.tokens import router as tokens_router
from src.api.v1.endpoints.users import router as users_router

router = APIRouter()
router.include_router(auth_router, tags=["auth"])
router.include_router(users_router, tags=["users"])
router.include_router(departments_router, tags=["departments"])
router.include_router(services_router, tags=["services"])
router.include_router(service_roles_router, tags=["service-roles"])
router.include_router(groups_router, tags=["groups"])
router.include_router(tokens_router, tags=["tokens"])
router.include_router(bots_router, tags=["bots"])
router.include_router(authorization_router, tags=["authorization"])
router.include_router(oauth2_router, tags=["oauth2"])
router.include_router(docker_router, tags=["docker-registry"])
