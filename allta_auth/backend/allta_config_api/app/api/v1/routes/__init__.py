from fastapi import APIRouter

from app.api.v1.routes.files import router as files_router
from app.api.v1.routes.service_credentials import router as service_credentials_router
from app.api.v1.routes.tokens import router as tokens_router

router = APIRouter()
router.include_router(files_router, tags=["Config Files"])
router.include_router(tokens_router, tags=["Config Tokens"])
router.include_router(service_credentials_router, tags=["Service Credentials"])

