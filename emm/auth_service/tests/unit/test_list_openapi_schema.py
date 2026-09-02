"""Регресс на типизацию response-schema у GET-list эндпоинтов.

list-роуты должны нести в OpenAPI типизированную `200`-схему (массив с `items`),
а не пустую `{}`. Пустая схема возникает, если у роута стоит `response_model=None`
(или он отсутствует) при том, что хендлер реально отдаёт `list[...]` — генераторы
клиентов тогда не видят форму ответа. На момент написания auth_service уже
полностью типизирован; этот файл фиксирует инвариант, чтобы регресс не прополз.

Параллельно проверяем, что листинговые схемы токенов/секретов отдают только
метаданные: raw-значения (`token`, `client_secret`) и хэши там не светятся —
plaintext показывается ровно один раз в отдельных `*Create*`-схемах.

БД не нужна: `app.openapi()` строит спеку статически из роутов и схем.
"""

from __future__ import annotations

BASE = "/api/auth/v1"

# Полный набор GET-list эндпоинтов auth_service: путь → ожидаемое имя элемента
# (`$ref` на компонент-схему внутри `items`). Если добавится новый list-роут —
# допиши сюда, и регресс начнёт его сторожить.
_LIST_ENDPOINTS = {
    f"{BASE}/users": "UserResponse",
    f"{BASE}/departments": "DepartmentResponse",
    f"{BASE}/services": "ServiceResponse",
    f"{BASE}/tokens": "PATListItem",
    f"{BASE}/oauth2/clients": "OAuthClientResponse",
}


def _list_item_schema(spec: dict, path: str) -> dict:
    """Достать schema из `200`/json для GET-роута по пути."""
    get_op = spec["paths"][path]["get"]
    return get_op["responses"]["200"]["content"]["application/json"]["schema"]


class TestListOpenApiSchema:
    """Каждый GET-list должен нести непустую `items`-схему, а не `{}`."""

    def test_all_list_routes_are_typed_arrays(self):
        from src.main import app

        spec = app.openapi()
        for path, item_name in _LIST_ENDPOINTS.items():
            schema = _list_item_schema(spec, path)
            assert schema != {}, f"{path}: пустая 200-схема"
            assert schema.get("type") == "array", f"{path}: не array"
            ref = schema.get("items", {}).get("$ref", "")
            assert ref.endswith(f"/{item_name}"), f"{path}: items != {item_name} ({ref})"

    def test_bot_tokens_list_is_typed_array(self):
        from src.main import app

        spec = app.openapi()
        # Путь параметризован `{bot_id}` — берём как есть из спеки.
        schema = _list_item_schema(spec, f"{BASE}/bots/{{bot_id}}/tokens")
        assert schema.get("type") == "array"
        assert schema["items"]["$ref"].endswith("/BotTokenListItem")


class TestListSchemasHideSecrets:
    """Листинговые схемы токенов/секретов несут только метаданные."""

    def test_pat_list_item_has_no_raw_token(self):
        from src.main import app

        props = app.openapi()["components"]["schemas"]["PATListItem"]["properties"]
        assert "token" not in props
        assert "token_hash" not in props
        assert "token_prefix" in props
        assert {"token_id", "name", "allowed_services"} <= set(props)

    def test_bot_token_list_item_has_no_raw_token(self):
        from src.main import app

        props = app.openapi()["components"]["schemas"]["BotTokenListItem"]["properties"]
        assert "token" not in props
        assert "token_hash" not in props
        assert "token_prefix" in props

    def test_oauth_client_list_item_has_no_secret(self):
        from src.main import app

        props = app.openapi()["components"]["schemas"]["OAuthClientResponse"]["properties"]
        assert "client_secret" not in props
        assert "client_secret_hash" not in props
