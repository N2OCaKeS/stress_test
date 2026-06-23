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

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies.auth import AccountAdmin
from src.dependencies.db import get_db
from src.schemas.admin import (
    GeneratedServiceKeyResponse,
    LockoutPolicyResponse,
    LockoutPolicyUpdateRequest,
)
from src.services import audit_service, lockout_policy_service

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


@router.get(
    "/lockout-policy",
    response_model=LockoutPolicyResponse,
    summary="Текущая политика brute-force lockout (account_admin)",
    responses={
        403: {"description": "ROLE_REQUIRED — нужен account_admin."},
    },
)
async def get_lockout_policy(
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> LockoutPolicyResponse:
    """Вернуть эффективные параметры lockout'а + источник (`db`/`env`).

    `source="env"` — runtime-override не задан, действуют env-дефолты.
    `source="db"` — действует строка из таблицы `lockout_policy`.
    """
    max_attempts, minutes, source = await lockout_policy_service.get_effective_policy(db)
    return LockoutPolicyResponse(
        max_failed_attempts=max_attempts,
        lockout_minutes=minutes,
        source=source,
    )


@router.put(
    "/lockout-policy",
    response_model=LockoutPolicyResponse,
    summary="Изменить политику brute-force lockout (account_admin)",
    responses={
        403: {"description": "ROLE_REQUIRED — нужен account_admin."},
        422: {"description": "max_failed_attempts/lockout_minutes < 1."},
    },
)
async def update_lockout_policy(
    body: LockoutPolicyUpdateRequest,
    request: Request,
    identity: AccountAdmin,
    db: AsyncSession = Depends(get_db),
) -> LockoutPolicyResponse:
    """Записать runtime-override политики lockout'а (платформенный scope).

    Меняет поведение `/login` и self-change-password без рестарта. Audit:
    `lockout_policy.update` (CRITICAL) с old/new значениями.
    """
    max_attempts, minutes = await lockout_policy_service.update_policy(
        db=db,
        actor_id=identity.user_id,
        max_failed_attempts=body.max_failed_attempts,
        lockout_minutes=body.lockout_minutes,
        request_id=getattr(request.state, "request_id", None),
    )
    return LockoutPolicyResponse(
        max_failed_attempts=max_attempts,
        lockout_minutes=minutes,
        source="db",
    )
