"""Регресс на схему ответа list-эндпоинтов в OpenAPI.

Ловит дефект `response_model=None` (или вовсе пропущенный `response_model`)
на GET-list-эндпоинтах: при нём FastAPI отдаёт пустую `{}`-схему `200`,
хотя хендлер возвращает типизированный envelope. Тест читает `app.openapi()`
и требует, чтобы каждый list-эндпоинт ссылался на компонент-схему с полями.

`/events/export` сюда не входит: он отдаёт CSV через `StreamingResponse`,
`response_model` там осознанно не объявлен (см. `endpoints/events.py`).
"""

from src.main import app


def _resolve_ref(spec: dict, schema: dict) -> dict:
    """Разворачивает `$ref` на компонент-схему. anyOf — берёт первую ветку с ref."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return spec["components"]["schemas"][name]
    for variant in schema.get("anyOf", []):
        if "$ref" in variant:
            return _resolve_ref(spec, variant)
    return schema


# (path, ожидаемое имя поля-коллекции в схеме ответа).
_LIST_ENDPOINTS = [
    ("/api/logging/v1/events", "items"),
    ("/api/logging/v1/rules", "items"),
    ("/api/logging/v1/services", "items"),
    ("/api/logging/v1/services/{service}/events", "items"),
]


class TestListOpenApiSchema:
    def test_list_endpoints_have_non_empty_200_schema(self):
        spec = app.openapi()
        for path, collection_field in _LIST_ENDPOINTS:
            op = spec["paths"][path]["get"]
            schema = op["responses"]["200"]["content"]["application/json"]["schema"]
            resolved = _resolve_ref(spec, schema)
            props = resolved.get("properties", {})
            assert props, f"{path}: схема ответа 200 пустая (response_model потерян?)"
            assert collection_field in props, (
                f"{path}: в схеме ответа нет поля {collection_field!r}"
            )

    def test_events_list_schema_is_event_list_response(self):
        """Точечный смоук на самый нагруженный list-канал — GET /events."""
        spec = app.openapi()
        schema = spec["paths"]["/api/logging/v1/events"]["get"][
            "responses"
        ]["200"]["content"]["application/json"]["schema"]
        assert schema.get("$ref", "").endswith("EventListResponse")
        resolved = _resolve_ref(spec, schema)
        for field in ("items", "has_more", "limit", "offset"):
            assert field in resolved["properties"], f"GET /events: нет поля {field!r}"

    def test_stats_and_retention_have_non_empty_200_schema(self):
        spec = app.openapi()
        for path in ("/api/logging/v1/events/stats", "/api/logging/v1/retention"):
            schema = spec["paths"][path]["get"]["responses"]["200"]["content"][
                "application/json"
            ]["schema"]
            resolved = _resolve_ref(spec, schema)
            assert resolved.get("properties"), f"{path}: схема ответа 200 пустая"

    def test_export_keeps_csv_content_type(self):
        """`/events/export` отдаёт CSV — `text/csv` остаётся в схеме 200."""
        spec = app.openapi()
        content = spec["paths"]["/api/logging/v1/events/export"]["get"][
            "responses"
        ]["200"]["content"]
        assert "text/csv" in content
