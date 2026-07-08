"""Конфигурация console_proxy.

Все значения приходят из окружения (k8s Secret/ConfigMap). Ключевое —
`console_token_secret`: он общий с server_service, тем же ключом server_service
подписывает консольный JWT, а прокси его проверяет. Если ключи разъедутся —
прокси будет отвергать все токены (INVALID_SIGNATURE).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # ── Подпись/валидация токена ──────────────────────────────────────────────
    console_token_secret: str = Field(
        default="",
        validation_alias="VM_CONSOLE_TOKEN_SECRET",
        description="Общий с server_service HMAC-секрет для проверки консольного JWT (HS256).",
    )
    console_token_issuer: str = Field(
        default="dbos-server-service",
        validation_alias="VM_CONSOLE_TOKEN_ISSUER",
        description="Ожидаемый iss в токене; server_service ставит то же значение.",
    )
    console_token_leeway_seconds: int = Field(
        default=10,
        validation_alias="VM_CONSOLE_TOKEN_LEEWAY_SECONDS",
        description="Допуск на расхождение часов при проверке exp/iat.",
    )

    # ── Как прокси достаёт TCP-таргет консоли ─────────────────────────────────
    # direct — прямой TCP на hub_ip:port (libvirt слушает на mgmt-LAN);
    # ssh    — SSH-туннель на hub (VNC/SPICE слушают на 127.0.0.1 хаба),
    #          порт при необходимости резолвится через virsh.
    target_mode: str = Field(
        default="ssh",
        validation_alias="CONSOLE_TARGET_MODE",
        description="direct | ssh — стратегия доступа к порту консоли на хабе.",
    )
    ssh_connect_timeout: int = Field(
        default=15, validation_alias="CONSOLE_SSH_CONNECT_TIMEOUT",
    )
    tcp_connect_timeout: int = Field(
        default=10, validation_alias="CONSOLE_TCP_CONNECT_TIMEOUT",
    )
    idle_timeout_seconds: int = Field(
        default=1800,
        validation_alias="CONSOLE_IDLE_TIMEOUT_SECONDS",
        description="Максимальная длительность одной консольной сессии; сверху cap на утечку соединений.",
    )

    # ── Клиент internal-API server_service (для ssh-режима) ────────────────────
    # Прокси тянет управляющие креды хаба через
    # GET /api/server/v1/internal/servers/{id}/management/credentials
    # под своим service-API-ключом (тот же inbound-механизм, что у воркера).
    server_service_base_url: str = Field(
        default="http://server-service:8002",
        validation_alias="SERVER_SERVICE_BASE_URL",
    )
    server_service_api_key: str = Field(
        default="",
        validation_alias="SERVER_SERVICE_API_KEY",
        description="Ключ для inbound SERVICE_API_KEYS-map server_service (запись console_proxy).",
    )
    server_service_api_key_name: str = Field(
        default="console_proxy",
        validation_alias="SERVER_SERVICE_API_KEY_NAME",
    )
    internal_request_timeout: int = Field(
        default=10, validation_alias="CONSOLE_INTERNAL_REQUEST_TIMEOUT",
    )

    # ── HTTP-сервер ───────────────────────────────────────────────────────────
    host: str = Field(default="0.0.0.0", validation_alias="CONSOLE_PROXY_HOST")
    port: int = Field(default=8085, validation_alias="CONSOLE_PROXY_PORT")
    static_dir: str = Field(
        default="static",
        validation_alias="CONSOLE_STATIC_DIR",
        description="Каталог с self-hosted noVNC/spice-html5 и HTML-обёртками.",
    )
    # Публичный путь-префикс на ingress. Влияет только на генерацию ссылок в
    # HTML-обёртках; сам роутинг завязан на этот же префикс.
    path_prefix: str = Field(default="/vm-console", validation_alias="CONSOLE_PATH_PREFIX")


@lru_cache
def get_settings() -> Settings:
    return Settings()
