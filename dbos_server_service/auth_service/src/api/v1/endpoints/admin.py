"""Платформенные admin-ручки, не привязанные к конкретному ресурсу.

Сейчас здесь — генератор мастер-ключей шифрования для рантайм-ротации
в server_service / secret_service. auth_service только **генерит** свежий
ключ (32 байта, base64) и отдаёт его account_admin'у для распространения;
сам ключ нигде не хранит — это снимает с auth_service роль key-custodian'а,
крипто-зоны сервисов остаются раздельными (см. memory: server_service
шифрует пароли, secret_service — токены; у каждого свой master).
"""

from __future__ import annotations

import base64
import secrets as _secrets

from fastapi import APIRouter

from src.dependencies.auth import AccountAdmin
from src.schemas.admin import GeneratedServiceKeyResponse
from src.services import audit_service

router = APIRouter(prefix="/admin")

_AES_KEY_BYTES = 32  # AES-256


@router.post(
    "/service-keys/generate",
    response_model=GeneratedServiceKeyResponse,
    summary="Сгенерировать свежий мастер-ключ шифрования (account_admin)",
    responses={
        200: {"description": "Свежий ключ (base64) для распространения в keystore сервиса."},
        401: {"description": "INVALID_TOKEN — нет/битый bearer."},
        403: {"description": "ROLE_REQUIRED — нужен account_admin."},
    },
)
async def generate_service_key(
    identity: AccountAdmin,
) -> GeneratedServiceKeyResponse:
    """Вернуть свежий AES-256 ключ (32 байта, base64) для ротации.

    Ключ генерится через `secrets.token_bytes` (CSPRNG), кодируется
    стандартным base64 и **не сохраняется** — auth_service видит его ровно
    один раз в этом ответе. account_admin относит его в rotate-эндпоинт
    нужного сервиса (server_service / secret_service
    `POST /internal/encryption/rotate`).

    Audit: `service_key.generate` (CRITICAL) — фиксируем факт генерации
    (без самого ключа в details).
    """
    raw = _secrets.token_bytes(_AES_KEY_BYTES)
    key_b64 = base64.b64encode(raw).decode("ascii")
    audit_service.emit(
        "service_key.generate",
        actor_id=identity.user_id,
        target_id=None,
        target_type="service_key",
        status="success",
        allowed=True,
        details={"key_bytes": _AES_KEY_BYTES, "encoding": "base64"},
    )
    return GeneratedServiceKeyResponse(
        key_b64=key_b64,
        key_bytes=_AES_KEY_BYTES,
        algorithm="AES-256-GCM",
    )
