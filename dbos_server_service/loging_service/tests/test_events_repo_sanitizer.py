"""Repo-уровень defence-in-depth для `request_id`.

`EventCreate` уже валидирует charset на Pydantic-слое, но
`repositories.events.insert` могут позвать в обход схемы —
из миграции, фоновой задачи или другого сервиса. Регекспный
гард внутри репо страхует от CR/LF и прочих опасных байтов
в `request_id`, который middleware рефлектит в `X-Request-ID`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.core.exceptions import DomainValidationError
from src.repositories import events as events_repo
from src.schemas.events import EventCreate


def _payload(request_id: str | None) -> EventCreate:
    # model_construct обходит field_validator'ы EventCreate — имитирует
    # caller'а, который зовёт репо напрямую с уже собранной dataclass-кой.
    return EventCreate.model_construct(
        timestamp=datetime.now(timezone.utc),
        service="auth_service",
        action="user.login",
        status="success",
        allowed=True,
        request_id=request_id,
        details={},
        idempotency_key=None,
    )


class TestRepoRequestIdSanitizer:
    @pytest.mark.parametrize("bad", [
        "abc\r\nSet-Cookie: hijack",
        "req\nfoo",
        "req\r",
        "req with space",
        "req/slash",
        "req.dot",  # точка разрешена схемой, но repo-гард строже — repo-charset
        "req\x00null",
        "req\x1bescape",
        "a" * 65,
        "",
    ])
    def test_invalid_request_id_rejected(self, db, bad: str):
        with pytest.raises(DomainValidationError) as exc:
            events_repo.insert(db, _payload(bad))
        assert exc.value.error_code == "INVALID_REQUEST_ID"

    @pytest.mark.parametrize("good", [
        "req_123",
        "REQ-abc",
        "abc_DEF-456",
        "a",
        "a" * 64,
    ])
    def test_valid_request_id_accepted(self, db, good: str):
        ev = events_repo.insert(db, _payload(good))
        assert ev.request_id == good

    def test_none_request_id_accepted(self, db):
        ev = events_repo.insert(db, _payload(None))
        assert ev.request_id is None
