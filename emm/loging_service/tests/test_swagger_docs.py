"""Self-host Swagger UI.

При заданном SWAGGER_UI_ASSETS_BASE дефолтный docs_url выключается, /docs
отдаётся со своего маршрута и тянет бандл с локального адреса (а не с CDN
jsdelivr, недоступного в корп-сети). CSP на /docs ослабляется ровно под
бандл; на остальных путях остаётся строгим.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

_ASSETS_BASE = "http://localhost:8088"


def _build_app(monkeypatch, assets_base: str | None):
    if assets_base:
        monkeypatch.setenv("SWAGGER_UI_ASSETS_BASE", assets_base)
    else:
        monkeypatch.delenv("SWAGGER_UI_ASSETS_BASE", raising=False)
    from src.core.config import get_settings
    from src.main import create_application

    get_settings.cache_clear()  # type: ignore[attr-defined]
    try:
        return create_application()
    finally:
        get_settings.cache_clear()  # type: ignore[attr-defined]


def test_docs_served_from_local_bundle(monkeypatch):
    app = _build_app(monkeypatch, _ASSETS_BASE)
    assert app.docs_url is None
    client = TestClient(app)
    resp = client.get("/docs")
    assert resp.status_code == 200
    body = resp.text
    assert f"{_ASSETS_BASE}/swagger-ui-bundle.js" in body
    assert f"{_ASSETS_BASE}/swagger-ui.css" in body
    assert "cdn.jsdelivr.net" not in body


def test_docs_csp_allows_local_assets(monkeypatch):
    app = _build_app(monkeypatch, _ASSETS_BASE)
    client = TestClient(app)
    resp = client.get("/docs")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert f"script-src 'self' 'unsafe-inline' {_ASSETS_BASE}" in csp
    assert f"style-src 'self' 'unsafe-inline' {_ASSETS_BASE}" in csp
    assert "frame-ancestors 'none'" in csp
    assert "default-src 'none'" in csp


def test_api_path_keeps_strict_csp(monkeypatch):
    app = _build_app(monkeypatch, _ASSETS_BASE)
    client = TestClient(app)
    resp = client.get("/__no_such_path__")
    assert resp.status_code == 404
    csp = resp.headers.get("Content-Security-Policy", "")
    assert csp == "default-src 'none'; frame-ancestors 'none'"


def test_default_cdn_docs_when_base_unset(monkeypatch):
    app = _build_app(monkeypatch, None)
    # Без base оставляем штатный docs_url — поведение как раньше.
    assert app.docs_url == "/docs"
