"""Схемы для OAuth2 client management и token-flow."""

from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError


def _validate_redirect_uri(uri: str) -> str:
    """RFC 6749 §3.1.2 / 8.1 — registered redirect_uris не должны содержать
    fragment, и должны быть https (исключение — `http://localhost[:port]/...`
    для локальной разработки native/CLI-клиентов).

    Зачем:
    * `http://` (non-localhost) → MITM в кластере перехватит code/state.
    * fragment (`#...`) → fragment не отправляется на сервер; если auth-сервер
      сделает `f"{uri}?code=..."` без strip'а fragment'а — клиент получит
      странный URL, и некоторые SPA-route'ры могут отдать code как
      fragment-route. Бан полностью, чтобы не гадать.
    """
    if not isinstance(uri, str) or not uri:
        raise PydanticCustomError(
            "redirect_uri_invalid",
            "redirect_uri must be a non-empty string",
        )

    try:
        parsed = urlparse(uri)
    except Exception as exc:  # pragma: no cover — urlparse редко падает
        raise PydanticCustomError(
            "redirect_uri_invalid",
            "redirect_uri is not a valid URL",
        ) from exc

    if parsed.fragment or "#" in uri:
        # urlparse кладёт content после `#` в `parsed.fragment` — но
        # дополнительно проверяем raw uri на случай если кто-то пропустит
        # экранированный `%23` (он останется в path, не в fragment).
        raise PydanticCustomError(
            "redirect_uri_has_fragment",
            "redirect_uri must not contain a fragment (#...)",
        )

    scheme = (parsed.scheme or "").lower()
    hostname = (parsed.hostname or "").lower()

    if scheme == "https":
        return uri

    if scheme == "http":
        # localhost-исключение (RFC 8252 §7.3) — для нативных клиентов:
        # `http://localhost`, `http://localhost:8080`, `http://127.0.0.1`,
        # `http://[::1]`. Любой другой http — запрещаем.
        if hostname in {"localhost", "127.0.0.1", "::1"}:
            return uri
        raise PydanticCustomError(
            "redirect_uri_not_https",
            "redirect_uri must use https:// (http:// allowed only for localhost)",
        )

    raise PydanticCustomError(
        "redirect_uri_scheme_invalid",
        "redirect_uri must use https:// (or http:// localhost)",
    )


class OAuthClientCreate(BaseModel):
    """Тело `POST /oauth2/clients` — регистрация OAuth2-клиента."""
    name: str = Field(min_length=1, max_length=128, description="Человекочитаемое имя клиента.")
    description: str | None = Field(default=None)
    department_id: str = Field(description="Отдел, к которому привязываем клиента.")
    redirect_uris: list[str] = Field(
        default_factory=list,
        description="Whitelist redirect_uri. Только https (или http://localhost для native).",
    )
    allowed_scopes: list[str] = Field(default_factory=list, description="Scope'ы, которые клиент может запросить.")
    grant_types: list[Literal["authorization_code", "client_credentials", "refresh_token"]] = Field(
        default=["authorization_code"],
        description="Разрешённые grant'ы (`authorization_code`, `client_credentials`, `refresh_token`).",
    )
    is_public: bool = Field(
        default=False,
        description=(
            "Public-клиент (SPA / native CLI) — не может хранить client_secret. "
            "Для таких включается обязательный PKCE с S256 (plain отвергается). "
            "Default False (confidential)."
        ),
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name не может быть пустым")
        return v

    @field_validator("redirect_uris")
    @classmethod
    def _validate_redirect_uris(cls, value: list[str]) -> list[str]:
        """Каждый uri должен быть https (или localhost-http) и без fragment.

        Связано с phishing-вектором (RFC 6749 §3.1.2 / RFC 8252 §7.3):
        redirect через trusted auth.<org>-домен на http-URL под управлением
        атакующего раньше проходил pydantic как есть.
        """
        return [_validate_redirect_uri(u) for u in value]

    @model_validator(mode="after")
    def _validate_grant_consistency(self) -> "OAuthClientCreate":
        """Кросс-полевые инварианты конфигурации клиента.

        Пустой `grant_types` (явный `[]`) создаёт бесполезного клиента — ни
        один flow для него не доступен. authorization_code без redirect_uris
        тоже мёртв: некуда вернуть код, /authorize отобьёт REDIRECT_URI_MISMATCH.
        Раньше эти случаи проходили схему и отсекались только в UI.
        """
        if not self.grant_types:
            raise PydanticCustomError(
                "grant_types_empty",
                "grant_types must not be empty",
            )
        if "authorization_code" in self.grant_types and not self.redirect_uris:
            raise PydanticCustomError(
                "redirect_uris_required",
                "redirect_uris required for authorization_code grant",
            )
        return self


class OAuthClientResponse(BaseModel):
    """Клиент в ответе list/get эндпоинтов (без plaintext secret)."""
    id: str
    client_id: str
    department_id: str
    name: str
    description: str | None
    redirect_uris: list[str]
    allowed_scopes: list[str]
    grant_types: list[str]
    is_active: bool
    is_public: bool = False
    created_at: datetime


class OAuthClientCreatedResponse(OAuthClientResponse):
    """Возвращается только при создании — содержит plaintext secret (показ один раз).

    Для public-клиентов `client_secret == None` (секрет не выдаётся, identity
    доказывается PKCE-verifier'ом). Для confidential — plaintext-строка.
    """
    client_secret: str | None = Field(
        default=None,
        description=(
            "Plaintext client_secret. Сохрани сейчас — больше не покажем. "
            "У public-клиентов поле отсутствует/None (secret не выдаётся)."
        ),
    )


# ── Authorization code flow ───────────────────────────────────────────────────

class OAuthTokenRequest(BaseModel):
    """Тело `POST /oauth2/token`."""
    # `Literal` обязательный — Pydantic v2 вернёт 422 если поле отсутствует
    # или значение вне списка. Раньше `str | None` маскировало bad grant в
    # тихий fallback-путь.
    grant_type: Literal["authorization_code", "refresh_token", "client_credentials"] = Field(
        description='`authorization_code`, `refresh_token` или `client_credentials`.',
    )
    # поля authorization_code grant
    code: str | None = Field(default=None, description="Authorization code (для authorization_code grant).")
    redirect_uri: str | None = Field(default=None, description="Тот же redirect_uri, что был на /authorize.")
    client_id: str | None = None
    client_secret: str | None = None
    # RFC 7636 PKCE — обмен кода требует verifier, если на /authorize был
    # передан code_challenge. Поле общее на authorization_code; для
    # client_credentials игнорируется.
    code_verifier: str | None = Field(
        default=None,
        description="PKCE verifier. Обязателен, если code был выдан с code_challenge.",
    )
    # refresh_token grant: предъявляемый opaque refresh. client_id обязателен
    # (по нему мы валидируем принадлежность токена клиенту); client_secret —
    # для confidential, public-клиент его не шлёт.
    refresh_token: str | None = Field(
        default=None,
        description="Opaque refresh-токен (для refresh_token grant).",
    )
    # client_credentials поля (client_id/secret те же, что выше)


class OAuthTokenResponse(BaseModel):
    """Ответ token-эндпойнта — access_token и метаданные.

    `refresh_token` присутствует только когда клиент имеет grant `refresh_token`
    (authorization_code-обмен и refresh-ротация). Для client_credentials —
    `None`: m2m просто берёт новый токен по client_secret, refresh там не нужен.
    """
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = Field(description="TTL access_token в секундах.")
    scope: str = ""
    refresh_token: str | None = Field(
        default=None,
        description="Opaque refresh-токен (выдаётся клиентам с grant refresh_token).",
    )
