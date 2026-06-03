"""Regression для `validate_outbox_id` в `finalize_reencrypt_outbox_done/_failed`.

`outbox_id` приходит из server_service и подставляется в URL-path
(`f".../{outbox_id}/done"`). Без guard'а path-traversal-вставка
(`"../admin"`) или newline'ы / пробелы дали бы worker'у возможность
дёрнуть сторонний endpoint server_service по своему bearer-токену.

Соседний `test_w18_w3_worker_p3.py::TestValidateOutboxId` уже проверяет
вызов валидатора статически (через `inspect.getsource`) + alphabet
через сам `validate_outbox_id`. Этот файл добивает behavioural-стороной:
реально бросает ли `finalize_*` ValueError до HTTP-вызова на конкретных
векторах (path-traversal, newline, пробел, пустая строка, переполнение
длины), и не даёт ли по дороге зацепить httpx-клиент.
"""
from __future__ import annotations

import pytest

from src.core.exceptions import CredentialFetchError  # noqa: F401  — import-time проверка зоны
from src.services import server_service_client


@pytest.fixture
def explode_if_called(monkeypatch):
    """Любой httpx.post должен бросить — если до него дошли, валидатор не сработал."""

    class _Boom:
        async def post(self, *args, **kwargs):  # pragma: no cover — не должен зваться
            raise AssertionError(
                "validate_outbox_id должен был отбить запрос до HTTP-вызова",
            )

    monkeypatch.setattr(
        server_service_client, "get_server_service_client", lambda: _Boom(),
    )


VALID_IDS = [
    "rox_a1b2c3d4e5f6789012345678901234ab",  # каноничный rox_<32 hex>
    "42",                                    # legacy BIGINT
    "abc_DEF-123",                           # alphanumeric + `_` + `-`
    "a" * 64,                                # ровно на границе длины
]

INVALID_IDS = [
    "../admin",                              # path-traversal
    "rox_ok/done",                           # `/` сам по себе
    "rox id",                                # пробел
    "rox\tid",                               # tab
    "rox\nid",                               # newline
    "",                                      # пустая строка
    "a" * 65,                                # на 1 длиннее лимита
    "rox.id",                                # `.`
    "rox:id",                                # `:`
    "1; DROP TABLE",                         # SQLi-вставка
]


class TestFinalizeReencryptOutboxDone:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("good", VALID_IDS)
    async def test_valid_id_passes_validator(self, monkeypatch, good):
        # Подменяем httpx.client на стаб, который возвращает 200 — валидатор
        # должен пропустить good-id, и мы убеждаемся в том, что URL содержит
        # ровно его (без экранирования и срезов).
        captured: dict[str, str] = {}

        class _Stub:
            async def post(self, url, headers=None):
                captured["url"] = url

                class _R:
                    status_code = 200

                    def json(self):
                        return {"id": good, "status": "done", "skipped": False}

                return _R()

        monkeypatch.setattr(
            server_service_client, "get_server_service_client", lambda: _Stub(),
        )

        result = await server_service_client.finalize_reencrypt_outbox_done(good)
        assert result["id"] == good
        assert captured["url"].endswith(f"/{good}/done")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", INVALID_IDS)
    async def test_invalid_id_rejected_before_http(self, explode_if_called, bad):
        with pytest.raises(ValueError):
            await server_service_client.finalize_reencrypt_outbox_done(bad)


class TestFinalizeReencryptOutboxFailed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("good", VALID_IDS)
    async def test_valid_id_passes_validator(self, monkeypatch, good):
        captured: dict[str, str] = {}

        class _Stub:
            async def post(self, url, headers=None, json=None):
                captured["url"] = url
                captured["json"] = json

                class _R:
                    status_code = 200

                    def json(self):
                        return {"id": good, "status": "failed"}

                return _R()

        monkeypatch.setattr(
            server_service_client, "get_server_service_client", lambda: _Stub(),
        )

        result = await server_service_client.finalize_reencrypt_outbox_failed(
            good, error="boom",
        )
        assert result["status"] == "failed"
        assert captured["url"].endswith(f"/{good}/failed")
        assert captured["json"] == {"error": "boom"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", INVALID_IDS)
    async def test_invalid_id_rejected_before_http(self, explode_if_called, bad):
        with pytest.raises(ValueError):
            await server_service_client.finalize_reencrypt_outbox_failed(
                bad, error="ignored",
            )
