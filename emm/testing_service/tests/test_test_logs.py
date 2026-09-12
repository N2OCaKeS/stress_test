"""Тесты хранения логов прогонов (§2.6, §8 плана миграции — волна 6, часть 1).

`log-chunk`/`log-segment` — internal-контракт для `testing_worker` (следующая
волна строит поверх него потоковое SSH-исполнение — форма тела фиксирована,
не меняется в одностороннем порядке). Переиспользуем инфраструктуру мока
`server_service`/фикстуры очереди из `test_queue.py`, чтобы получить
настоящий `queue_item` — сам домен логов не занимается подготовкой стенда.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import update as sa_update

from src.db.session import AsyncSessionLocal
from src.models import TestLog as LogModel  # алиас без префикса "Test" — иначе pytest пытается собрать класс как тестовый
from src.repositories import test_log as test_log_repo
from src.services import log_rotation
from src.services import queue as queue_svc
from src.utils.ids import test_log_id as new_test_log_id  # алиас — иначе pytest коллекционирует как test_*
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (  # noqa: F401 — фикстуры переиспользуются pytest'ом по имени
    LAUNCH_CTX,
    SERVER_SECRET,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _identity,
    _server_hdr,
    configure_internal_keys,
    mock_server_service,
    recorded_calls,
)

LOG_BASE = "/internal/queue"
QUEUE_ITEMS_BASE = "/api/testing/v1/queue-items"


async def _make_queue_item(client, admin_token, mock_server_service, *, launch_context=None):
    """Стенд + тест + enqueue — приём лога не требует claim/complete, только сам queue_item."""
    mock_server_service()
    stand_id, _ = await _create_stand(client, admin_token)
    test_id = await _create_test_def(client, admin_token, stand_id)
    ctx = dict(launch_context if launch_context is not None else LAUNCH_CTX)
    async with AsyncSessionLocal() as db:
        item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=ctx)
    return item, test_id, stand_id


class TestLogChunk:
    async def test_first_chunk_creates_log_lazily_and_accumulates(
        self, client, admin_token, mock_server_service, configure_internal_keys,
    ):
        item, _test_id, _stand_id = await _make_queue_item(client, admin_token, mock_server_service)

        resp = await client.post(
            f"{LOG_BASE}/{item.id}/log-chunk",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"text": "hello "},
        )
        assert resp.status_code == 200, resp.text
        log_id = resp.json()["log_id"]

        resp2 = await client.post(
            f"{LOG_BASE}/{item.id}/log-chunk",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"text": "world"},
        )
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["log_id"] == log_id

        full = await client.get(f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token))
        assert full.status_code == 200, full.text
        assert full.text == "hello world"

        async with AsyncSessionLocal() as db:
            log = await test_log_repo.get_by_id(db, log_id)
        assert log.size_bytes == len("hello world")

    async def test_wrong_identity_rejected(self, client, configure_internal_keys):
        resp = await client.post(
            f"{LOG_BASE}/qi_whatever/log-chunk",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"text": "x"},
        )
        assert resp.status_code == 401, resp.text

    async def test_unknown_queue_item_404(self, client, configure_internal_keys):
        resp = await client.post(
            f"{LOG_BASE}/qi_does_not_exist/log-chunk",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"text": "x"},
        )
        assert resp.status_code == 404, resp.text


class TestLogSegment:
    async def test_segment_format_matches_legacy_template(
        self, client, admin_token, mock_server_service, configure_internal_keys,
    ):
        item, *_ = await _make_queue_item(client, admin_token, mock_server_service)

        resp = await client.post(
            f"{LOG_BASE}/{item.id}/log-segment",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={
                "kind": "command", "label": "run.py", "status": "OK",
                "command_text_masked": "run.py --flag ***",
                "output": "все хорошо",
                "host": "10.0.0.5",
                "started_at": "2026-01-02T09:04:05+00:00",
                "finished_at": "2026-01-02T09:04:06+00:00",
            },
        )
        assert resp.status_code == 200, resp.text

        full = await client.get(f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token))
        assert full.status_code == 200, full.text
        border = "*" * 66
        expected = (
            f"{border}\n"
            "TASK [run.py: 10.0.0.5]\n"
            "[ 12:04:05 02-01-2026 ]\n"
            "STATUS [OK]\n"
            "COMMAND: run.py --flag ***\n\n"
            "CONCLUSION: все хорошо\n"
            f"{border}\n\n\n"
        )
        assert full.text == expected

    async def test_fatal_status_uses_hash_border(
        self, client, admin_token, mock_server_service, configure_internal_keys,
    ):
        item, *_ = await _make_queue_item(client, admin_token, mock_server_service)
        resp = await client.post(
            f"{LOG_BASE}/{item.id}/log-segment",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={
                "kind": "checkpoint", "label": "restore", "status": "FATAL",
                "command_text_masked": None, "output": "boom",
                "host": "10.0.0.5",
                "started_at": "2026-01-02T09:04:05+00:00",
                "finished_at": "2026-01-02T09:04:06+00:00",
            },
        )
        assert resp.status_code == 200, resp.text
        full = await client.get(f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token))
        assert full.text.startswith("#" * 66 + "\n")
        assert "COMMAND: \n\n" in full.text

    async def test_offsets_accumulate_across_segments(
        self, client, admin_token, mock_server_service, configure_internal_keys,
    ):
        item, *_ = await _make_queue_item(client, admin_token, mock_server_service)
        for i in range(2):
            resp = await client.post(
                f"{LOG_BASE}/{item.id}/log-segment",
                headers=_server_hdr("testing_worker", WORKER_SECRET),
                json={
                    "kind": "command", "label": f"step{i}", "status": "OK",
                    "command_text_masked": "cmd", "output": f"out{i}",
                    "host": "h", "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:01Z",
                },
            )
            assert resp.status_code == 200, resp.text

        segs = await client.get(f"{QUEUE_ITEMS_BASE}/{item.id}/log/segments", headers=_hdr(admin_token))
        assert segs.status_code == 200, segs.text
        items = segs.json()["items"]
        assert [s["position"] for s in items] == [0, 1]
        assert items[0]["byte_offset_start"] == 0
        assert items[0]["byte_offset_end"] == items[1]["byte_offset_start"]

        full = await client.get(f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token))
        content = full.text
        assert "step0" in content[items[0]["byte_offset_start"]:items[0]["byte_offset_end"]]
        assert "step1" in content[items[1]["byte_offset_start"]:items[1]["byte_offset_end"]]
        assert "step0" not in content[items[1]["byte_offset_start"]:items[1]["byte_offset_end"]]

    async def test_status_filter(self, client, admin_token, mock_server_service, configure_internal_keys):
        item, *_ = await _make_queue_item(client, admin_token, mock_server_service)
        for status in ("OK", "FATAL", "OK"):
            resp = await client.post(
                f"{LOG_BASE}/{item.id}/log-segment",
                headers=_server_hdr("testing_worker", WORKER_SECRET),
                json={
                    "kind": "command", "label": "s", "status": status,
                    "command_text_masked": None, "output": "o",
                    "host": "h", "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:01Z",
                },
            )
            assert resp.status_code == 200, resp.text

        resp = await client.get(
            f"{QUEUE_ITEMS_BASE}/{item.id}/log/segments",
            headers=_hdr(admin_token), params={"status": "FATAL"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["status"] == "FATAL"


class TestGetLog:
    async def test_full_text_and_filename(self, client, admin_token, mock_server_service, configure_internal_keys):
        item, _test_id, stand_id = await _make_queue_item(
            client, admin_token, mock_server_service,
            launch_context={**LAUNCH_CTX, "OS_VERSION_MAJOR": "1.8"},
        )
        await client.post(
            f"{LOG_BASE}/{item.id}/log-chunk",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"text": "abc"},
        )
        resp = await client.get(f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.text == "abc"
        disposition = resp.headers["content-disposition"]
        assert disposition.startswith("attachment; filename=")
        assert stand_id in disposition
        assert LAUNCH_CTX["RC"] in disposition
        assert disposition.rstrip('"').endswith(".log")

    async def test_range_valid(self, client, admin_token, mock_server_service, configure_internal_keys):
        item, *_ = await _make_queue_item(client, admin_token, mock_server_service)
        await client.post(
            f"{LOG_BASE}/{item.id}/log-chunk",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"text": "0123456789"},
        )
        resp = await client.get(
            f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token), params={"from": 2, "to": 5},
        )
        assert resp.status_code == 200, resp.text
        assert resp.text == "234"

    async def test_range_invalid_returns_422(self, client, admin_token, mock_server_service, configure_internal_keys):
        item, *_ = await _make_queue_item(client, admin_token, mock_server_service)
        await client.post(
            f"{LOG_BASE}/{item.id}/log-chunk",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"text": "short"},
        )
        resp = await client.get(
            f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token), params={"from": 3, "to": 1},
        )
        assert resp.status_code == 422, resp.text

        resp2 = await client.get(
            f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token), params={"from": 0, "to": 999},
        )
        assert resp2.status_code == 422, resp2.text

    async def test_404_when_no_log_yet(self, client, admin_token, mock_server_service, configure_internal_keys):
        item, *_ = await _make_queue_item(client, admin_token, mock_server_service)
        resp = await client.get(f"{QUEUE_ITEMS_BASE}/{item.id}/log", headers=_hdr(admin_token))
        assert resp.status_code == 404, resp.text

        resp2 = await client.get(f"{QUEUE_ITEMS_BASE}/{item.id}/log/segments", headers=_hdr(admin_token))
        assert resp2.status_code == 404, resp2.text

    async def test_requires_auth(self, client):
        resp = await client.get(f"{QUEUE_ITEMS_BASE}/qi_x/log")
        assert resp.status_code == 401, resp.text


class TestRotation:
    async def _make_log(
        self, *, os_version_major: str, rc: str, kernel: str, test_id: str = "tdef_x", created_at=None,
    ) -> str:
        async with AsyncSessionLocal() as db:
            log = await test_log_repo.create(db, {
                "id": new_test_log_id(),
                "queue_item_id": None,
                "stand_id": "stand_x",
                "test_id": test_id,
                "os_version_major": os_version_major,
                "rc": rc,
                "kernel": kernel,
                "internal_path": "logs/dep/x/x/stand_x/qi_x.log",
                "size_bytes": 0,
                "protected": False,
            })
            await db.commit()
            log_id = log.id
        if created_at is not None:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    sa_update(LogModel).where(LogModel.id == log_id).values(created_at=created_at)
                )
                await db.commit()
        return log_id

    async def test_top_two_rc_protected(self):
        branch = f"branch_{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        old_id = await self._make_log(
            os_version_major=branch, rc="rc1", kernel="k", created_at=now - timedelta(days=3),
        )
        mid_id = await self._make_log(
            os_version_major=branch, rc="rc2", kernel="k", created_at=now - timedelta(days=2),
        )
        new_id = await self._make_log(
            os_version_major=branch, rc="rc3", kernel="k", created_at=now - timedelta(days=1),
        )

        async with AsyncSessionLocal() as db:
            await log_rotation.recompute_protection_for_branch(db, branch)
            await db.commit()

        async with AsyncSessionLocal() as db:
            old = await test_log_repo.get_by_id(db, old_id)
            mid = await test_log_repo.get_by_id(db, mid_id)
            new = await test_log_repo.get_by_id(db, new_id)
        assert old.protected is False
        assert mid.protected is True
        assert new.protected is True

    async def test_monthly_retention_deletes_only_stale_unprotected(self):
        branch = f"branch_{uuid.uuid4().hex[:6]}"
        now = datetime.now(timezone.utc)
        stale_unprotected = await self._make_log(
            os_version_major=branch, rc="rcA", kernel="k", created_at=now - timedelta(days=40),
        )
        stale_protected = await self._make_log(
            os_version_major=branch, rc="rcB", kernel="k", created_at=now - timedelta(days=40),
        )
        fresh_unprotected = await self._make_log(
            os_version_major=branch, rc="rcC", kernel="k", created_at=now - timedelta(days=1),
        )
        async with AsyncSessionLocal() as db:
            await db.execute(sa_update(LogModel).where(LogModel.id == stale_protected).values(protected=True))
            await db.commit()

        async with AsyncSessionLocal() as db:
            deleted = await log_rotation.enforce_monthly_retention(db, retention_days=30)
        assert deleted == 1

        async with AsyncSessionLocal() as db:
            assert await test_log_repo.get_by_id(db, stale_unprotected) is None
            assert await test_log_repo.get_by_id(db, stale_protected) is not None
            assert await test_log_repo.get_by_id(db, fresh_unprotected) is not None

    async def test_relaunch_preserves_previous_attempt_log(
        self, client, admin_token, mock_server_service, configure_internal_keys,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            first = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{LOG_BASE}/{first.id}/log-chunk",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"text": "first run"},
        )
        async with AsyncSessionLocal() as db:
            first_log = await test_log_repo.get_by_queue_item_id(db, first.id)
        assert first_log is not None

        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        async with AsyncSessionLocal() as db:
            gone = await test_log_repo.get_by_id(db, first_log.id)
        assert gone is not None
        assert gone.id == first_log.id
        response = await client.get(f"{QUEUE_ITEMS_BASE}/{first.id}/log", headers=_hdr(admin_token))
        assert response.status_code == 200
        assert "first run" in response.text

    async def test_relaunch_preserves_protected_log(
        self, client, admin_token, mock_server_service, configure_internal_keys,
    ):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        async with AsyncSessionLocal() as db:
            first = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{LOG_BASE}/{first.id}/log-chunk",
            headers=_server_hdr("testing_worker", WORKER_SECRET),
            json={"text": "first run"},
        )
        async with AsyncSessionLocal() as db:
            first_log = await test_log_repo.get_by_queue_item_id(db, first.id)
            first_log_id = first_log.id
            await db.execute(sa_update(LogModel).where(LogModel.id == first_log_id).values(protected=True))
            await db.commit()

        async with AsyncSessionLocal() as db:
            await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)

        async with AsyncSessionLocal() as db:
            still_there = await test_log_repo.get_by_id(db, first_log_id)
        assert still_there is not None
