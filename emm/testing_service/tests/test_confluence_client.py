"""Тесты транспорта `services/confluence_client.py`.

Инстанс отдела — Confluence Server/Data Center (`life.astralinux.ru`), у него
база REST — `/rest/api/...`. Префикс `/wiki` есть только у Atlassian Cloud, и
на Server/DC он даёт 404 на каждом вызове. Легаси ходит туда же через
`atlassian.Confluence(url='https://life.astralinux.ru')`, то есть без `/wiki`
— эти тесты фиксируют ровно это, чтобы префикс не вернулся обратно.

Никакой БД: `build_client` подменяется на `httpx.MockTransport`.
"""

from __future__ import annotations

import httpx
import pytest

from src.services import confluence_client

BASE_URL = "http://confluence.example"


class _StubSettings:
    confluence_request_timeout_seconds = 2.0


@pytest.fixture
def recorded(monkeypatch):
    """Перехватывает каждый исходящий запрос. Возвращает список `httpx.Request`."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/child/comment"):
            return httpx.Response(200, json={"results": [{"id": "cmt_1", "version": {"number": 2}}]})
        if request.method == "GET" and request.url.params.get("expand") == "version":
            return httpx.Response(200, json={"version": {"number": 7}})
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"id": "pg_1", "title": "STRESS_report ⬝ 1.8.5"}]})
        if request.method == "POST":
            return httpx.Response(201, json={"id": "new_1"})
        return httpx.Response(200, json={})

    monkeypatch.setattr(confluence_client, "get_settings", lambda: _StubSettings())
    monkeypatch.setattr(
        confluence_client, "build_client",
        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return requests


class TestServerDcContentPath:
    def test_content_path_has_no_wiki_prefix(self):
        assert confluence_client._CONTENT_PATH == "/rest/api/content"
        assert "/wiki" not in confluence_client._CONTENT_PATH

    async def test_find_page_id_hits_server_dc_path(self, recorded):
        page_id = await confluence_client.find_page_id(
            base_url=BASE_URL, bearer_token="tok", space="AL", title="STRESS_report ⬝ 1.8.5",
        )
        assert page_id == "pg_1"
        assert recorded[0].url.path == "/rest/api/content"

    async def test_find_blogpost_id_hits_server_dc_path(self, recorded):
        await confluence_client.find_blogpost_id(
            base_url=BASE_URL, bearer_token="tok", space="AL", title="нет такого",
        )
        assert recorded[0].url.path == "/rest/api/content"

    async def test_get_comments_hits_server_dc_path(self, recorded):
        comments = await confluence_client.get_comments(
            base_url=BASE_URL, bearer_token="tok", content_id="blog_1",
        )
        assert comments == [{"id": "cmt_1", "version": {"number": 2}}]
        assert recorded[0].url.path == "/rest/api/content/blog_1/child/comment"

    async def test_add_comment_hits_server_dc_path(self, recorded):
        await confluence_client.add_comment(
            base_url=BASE_URL, bearer_token="tok", content_id="blog_1", body_html="<p>x</p>",
        )
        assert recorded[0].url.path == "/rest/api/content"

    async def test_update_comment_hits_server_dc_path(self, recorded):
        await confluence_client.update_comment(
            base_url=BASE_URL, bearer_token="tok", comment_id="cmt_1", body_html="<p>x</p>", version=2,
        )
        assert recorded[0].url.path == "/rest/api/content/cmt_1"

    async def test_get_page_version_hits_server_dc_path(self, recorded):
        assert await confluence_client.get_page_version(
            base_url=BASE_URL, bearer_token="tok", page_id="pg_1",
        ) == 7
        assert recorded[0].url.path == "/rest/api/content/pg_1"

    async def test_create_page_hits_server_dc_path(self, recorded):
        await confluence_client.create_page(
            base_url=BASE_URL, bearer_token="tok", space="AL", title="t",
            parent_id=None, body_html="<p>x</p>",
        )
        assert recorded[0].url.path == "/rest/api/content"

    async def test_update_page_hits_server_dc_path(self, recorded):
        await confluence_client.update_page(
            base_url=BASE_URL, bearer_token="tok", page_id="pg_1", title="t",
            body_html="<p>x</p>", version=7,
        )
        assert recorded[0].url.path == "/rest/api/content/pg_1"

    async def test_no_operation_emits_a_wiki_prefixed_url(self, recorded):
        """Сводная проверка: ни одна из девяти операций не должна уйти в `/wiki`."""
        await confluence_client.find_page_id(
            base_url=BASE_URL, bearer_token="tok", space="AL", title="STRESS_report ⬝ 1.8.5",
        )
        await confluence_client.find_blogpost_id(
            base_url=BASE_URL, bearer_token="tok", space="AL", title="нет такого",
        )
        await confluence_client.get_comments(base_url=BASE_URL, bearer_token="tok", content_id="blog_1")
        await confluence_client.add_comment(
            base_url=BASE_URL, bearer_token="tok", content_id="blog_1", body_html="<p>x</p>",
        )
        await confluence_client.update_comment(
            base_url=BASE_URL, bearer_token="tok", comment_id="cmt_1", body_html="<p>x</p>", version=2,
        )
        await confluence_client.get_page_version(base_url=BASE_URL, bearer_token="tok", page_id="pg_1")
        await confluence_client.create_page(
            base_url=BASE_URL, bearer_token="tok", space="AL", title="t",
            parent_id="pg_0", body_html="<p>x</p>",
        )
        await confluence_client.update_page(
            base_url=BASE_URL, bearer_token="tok", page_id="pg_1", title="t",
            body_html="<p>x</p>", version=7,
        )
        assert len(recorded) == 8
        for request in recorded:
            assert request.url.path.startswith("/rest/api/content"), request.url


class TestAuthScheme:
    """Server/DC берёт PAT как `Authorization: Bearer <token>` — тот же
    заголовок ставит `atlassian-python-api` при `token=`, которым пользуется
    легаси. Cloud-схема (email + API-token в Basic) здесь неприменима."""

    async def test_sends_bearer_authorization(self, recorded):
        await confluence_client.find_page_id(
            base_url=BASE_URL, bearer_token="pat123", space="AL", title="t",
        )
        assert recorded[0].headers["Authorization"] == "Bearer pat123"
        assert "Basic " not in recorded[0].headers["Authorization"]
