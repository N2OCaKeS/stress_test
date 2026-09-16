"""Тесты фонового пересчёта статистики (§2.7, §9.3 плана миграции).

`schedule_recalc` мокает `secret_client.reveal_credential` и
`statistics_client.trigger_all_statistics` (monkeypatch модульных функций,
тот же приём, что `tests/test_run_summary.py`) — ни одного реального
сетевого вызова. Фоновая задача шедулится через `asyncio.get_running_loop()
.create_task(...)`; `schedule_recalc`/`trigger_manual` возвращают саму
задачу — тесты `await` её напрямую вместо sleep-поллинга. Для HTTP-триггеров
(эндпоинт `/statistics/recalculate`, событийный хук из `services/queue.py`)
задача осядет в модульном `statistics_recalc._pending_recalc_tasks` — тесты
дожидаются её оттуда.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.constants import StatisticsRecalcStatus
from src.db.session import AsyncSessionLocal
from src.dependencies.auth import Identity
from src.repositories import department_integration_settings as dis_repo
from src.repositories import statistics_recalc as recalc_repo
from src.repositories import statistics_settings as settings_repo
from src.services import secret_client, statistics_client, statistics_recalc as recalc_svc
from src.utils.ids import department_integration_settings_id
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    CALLBACK_BASE,  # noqa: F401 — реэкспорт для TestQueueTrigger
    QUEUE_BASE,  # noqa: F401
    SERVER_SECRET,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _server_hdr,  # noqa: F401
    configure_internal_keys,
    mock_server_service,
    recorded_calls,  # noqa: F401 — mock_server_service объявляет её как свою зависимость
)
from tests.test_run_summary import _seed_test_run
from tests.test_test_runs import _drive_to_success, _get_item, _payload

SETTINGS_BASE = "/api/testing/v1/statistics/settings"
STATUS_BASE = "/api/testing/v1/statistics/status"
RECALC_BASE = "/api/testing/v1/statistics/recalculate"
RUNS_BASE = "/api/testing/v1/test-runs"


@pytest.fixture(autouse=True)
async def _reset_statistics_singletons():
    """Обе singleton-таблицы — общие на весь тестовый прогон, не per-department.

    Сбрасываем перед каждым тестом этого файла, иначе порядок тестов внутри
    класса/файла определял бы, видит ли следующий тест дефолты или состояние,
    оставленное предыдущим (тот же приём нужен только для настоящих
    singleton-строк — per-department таблицы такой проблемы не имеют, там
    каждый тест использует свой уникальный department_id).
    """
    async with AsyncSessionLocal() as db:
        settings_row = await settings_repo.get_singleton(db)
        if settings_row is not None:
            await db.delete(settings_row)
        status_row = await recalc_repo.get_singleton(db)
        if status_row is not None:
            await db.delete(status_row)
        await db.commit()
    yield


async def _configure_statistics(*, enabled: bool = True, base_url: str = "http://stats.example:7777") -> None:
    async with AsyncSessionLocal() as db:
        await settings_repo.upsert(db, {"enabled": enabled, "base_url": base_url})
        await db.commit()


async def _seed_integration_settings(
    department_id: str, *, credential_id: str | None = "cred_x", bitbucket_credential_id: str | None = None,
) -> None:
    """Idempotent — `department_id` может повторяться между тестами этого файла (`dep_a`)."""
    async with AsyncSessionLocal() as db:
        existing = await dis_repo.get_by_department(db, department_id)
        changes = {
            "credential_id": credential_id,
            "bitbucket_credential_id": bitbucket_credential_id,
        }
        if existing is None:
            await dis_repo.create(db, {
                "id": department_integration_settings_id(),
                "department_id": department_id,
                "jira_base_url": None,
                "confluence_base_url": None,
                **changes,
            })
        else:
            await dis_repo.update(db, existing, changes)
        await db.commit()


@pytest.fixture
def mock_secret_client(monkeypatch):
    store: dict[str, tuple[str, str]] = {}

    async def fake_reveal(cred_id: str):
        if cred_id not in store:
            from src.core.exceptions import NotFoundError

            raise NotFoundError(error_code="CREDENTIAL_NOT_FOUND", message="not found")
        return store[cred_id]

    monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
    return store


@pytest.fixture
def mock_statistics_client(monkeypatch):
    """`state["fail"]` — смоделировать сбой внешнего сервиса статистики."""
    calls: list[dict] = []
    state: dict = {"fail": False}

    async def fake_trigger(*, base_url, username, token, timeout):
        calls.append({"base_url": base_url, "username": username, "token": token})
        if state["fail"]:
            from src.core.exceptions import ServiceUnavailableError

            raise ServiceUnavailableError(error_code="STATISTICS_SERVICE_ERROR", message="boom")

    monkeypatch.setattr(statistics_client, "trigger_all_statistics", fake_trigger)
    return calls, state


async def _drain_pending_tasks() -> None:
    """Дождаться всех текущих fire-and-forget задач пересчёта (HTTP-триггеры)."""
    pending = [t for t in recalc_svc._pending_recalc_tasks if not t.done()]
    if pending:
        await asyncio.gather(*pending)


# ── schedule_recalc — прямые вызовы сервиса ─────────────────────────────────


class TestScheduleRecalcSkips:
    async def test_settings_missing_skips(self):
        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "manual", department_id="dep_x")
        assert task is None

    async def test_settings_disabled_skips(self):
        await _configure_statistics(enabled=False)
        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "manual", department_id="dep_x")
        assert task is None

    async def test_no_base_url_skips(self):
        await _configure_statistics(enabled=True, base_url="")
        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "manual", department_id="dep_x")
        assert task is None

    async def test_unknown_test_run_skips(self):
        await _configure_statistics()
        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "test_run", test_run_id="run_missing")
        assert task is None

    async def test_no_integration_settings_skips(self):
        await _configure_statistics()
        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "manual", department_id="dep_unconfigured")
        assert task is None

    async def test_no_credential_id_skips(self):
        await _configure_statistics()
        await _seed_integration_settings("dep_nocred", credential_id=None)
        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "manual", department_id="dep_nocred")
        assert task is None

    async def test_reveal_failure_skips(self, mock_secret_client):
        await _configure_statistics()
        await _seed_integration_settings("dep_badcred", credential_id="cred_missing")
        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "manual", department_id="dep_badcred")
        assert task is None


class TestScheduleRecalcRuns:
    async def test_success_marks_succeeded(self, mock_secret_client, mock_statistics_client):
        calls, _state = mock_statistics_client
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _configure_statistics(base_url="http://stats.example:7777")
        await _seed_integration_settings("dep_ok")

        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "manual", department_id="dep_ok")
        assert task is not None
        await task

        assert calls == [{"base_url": "http://stats.example:7777", "username": "bot", "token": "tok123"}]
        async with AsyncSessionLocal() as db:
            status = await recalc_svc.get_status(db)
        assert status["status"] == StatisticsRecalcStatus.SUCCEEDED
        assert status["triggered_by"] == "manual"
        assert status["error"] is None
        assert status["started_at"] is not None
        assert status["finished_at"] is not None

    async def test_client_failure_marks_failed(self, mock_secret_client, mock_statistics_client):
        _calls, state = mock_statistics_client
        state["fail"] = True
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _configure_statistics()
        await _seed_integration_settings("dep_fail")

        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "manual", department_id="dep_fail")
        await task

        async with AsyncSessionLocal() as db:
            status = await recalc_svc.get_status(db)
        assert status["status"] == StatisticsRecalcStatus.FAILED
        assert status["error"]

    async def test_test_run_trigger_resolves_department_from_run(
        self, mock_secret_client, mock_statistics_client,
    ):
        calls, _state = mock_statistics_client
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _configure_statistics()
        await _seed_integration_settings("dep_run")
        run_id = await _seed_test_run(department_id="dep_run")

        async with AsyncSessionLocal() as db:
            task = await recalc_svc.schedule_recalc(db, "test_run", test_run_id=run_id)
        await task

        assert len(calls) == 1
        async with AsyncSessionLocal() as db:
            status = await recalc_svc.get_status(db)
        assert status["status"] == StatisticsRecalcStatus.SUCCEEDED
        assert status["triggered_by"] == "test_run"
        assert status["test_run_id"] == run_id


# ── trigger_manual — permission + department resolution ────────────────────


class TestTriggerManualService:
    async def test_no_department_anywhere_raises(self):
        from src.core.exceptions import DomainValidationError

        identity = Identity(
            user_id="usr_x", username="x", actor_type="user", department_id=None,
            allowed_services=["testing_service"], service_roles={"testing_service": ["admin"]},
            is_banned=False, platform_role=None,
        )
        await _configure_statistics()
        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError):
                await recalc_svc.trigger_manual(db, identity, None)


# ── HTTP: /statistics/settings ──────────────────────────────────────────────


class TestSettingsEndpoint:
    async def test_defaults_when_unset(self, client, admin_token):
        resp = await client.get(SETTINGS_BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"enabled": False, "base_url": None}

    async def test_open_to_any_authenticated_role(self, client, guest_token):
        resp = await client.get(SETTINGS_BASE, headers=_hdr(guest_token))
        assert resp.status_code == 200, resp.text

    async def test_anonymous_rejected(self, client):
        resp = await client.get(SETTINGS_BASE)
        assert resp.status_code == 401, resp.text

    async def test_put_requires_update_permission(self, client, guest_token):
        resp = await client.put(SETTINGS_BASE, headers=_hdr(guest_token), json={"enabled": True})
        assert resp.status_code == 403, resp.text

    async def test_put_updates_settings(self, client, admin_token):
        resp = await client.put(
            SETTINGS_BASE, headers=_hdr(admin_token),
            json={"enabled": True, "base_url": "http://allta.devos.astralinux.ru:7777"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["enabled"] is True
        assert body["base_url"] == "http://allta.devos.astralinux.ru:7777"

        again = await client.get(SETTINGS_BASE, headers=_hdr(admin_token))
        assert again.json() == body

    async def test_put_empty_base_url_clears(self, client, admin_token):
        await client.put(
            SETTINGS_BASE, headers=_hdr(admin_token),
            json={"enabled": True, "base_url": "http://stats.example"},
        )
        resp = await client.put(SETTINGS_BASE, headers=_hdr(admin_token), json={"base_url": ""})
        assert resp.status_code == 200, resp.text
        assert resp.json()["base_url"] is None
        assert resp.json()["enabled"] is True  # непереданное поле не трогается


# ── HTTP: /statistics/status ────────────────────────────────────────────────


class TestStatusEndpoint:
    async def test_defaults_when_unset(self, client, admin_token):
        resp = await client.get(STATUS_BASE, headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "idle"
        assert body["triggered_by"] is None

    async def test_open_to_any_authenticated_role(self, client, guest_token):
        resp = await client.get(STATUS_BASE, headers=_hdr(guest_token))
        assert resp.status_code == 200, resp.text

    async def test_anonymous_rejected(self, client):
        resp = await client.get(STATUS_BASE)
        assert resp.status_code == 401, resp.text


# ── HTTP: /statistics/recalculate ───────────────────────────────────────────


class TestRecalculateEndpoint:
    async def test_requires_update_permission(self, client, guest_token):
        resp = await client.post(RECALC_BASE, headers=_hdr(guest_token), json={})
        assert resp.status_code == 403, resp.text

    async def test_manual_trigger_reflects_in_status(
        self, client, admin_token, mock_secret_client, mock_statistics_client,
    ):
        calls, _state = mock_statistics_client
        mock_secret_client["cred_x"] = ("bot", "tok123")
        await _configure_statistics()
        await _seed_integration_settings("dep_a")  # admin_token/dept_a fixture

        resp = await client.post(RECALC_BASE, headers=_hdr(admin_token), json={})
        assert resp.status_code == 202, resp.text

        await _drain_pending_tasks()

        status = await client.get(STATUS_BASE, headers=_hdr(admin_token))
        body = status.json()
        assert body["status"] == "succeeded"
        assert body["triggered_by"] == "manual"
        assert len(calls) == 1

    async def test_manual_trigger_explicit_department_overrides_caller(
        self, client, admin_token, mock_secret_client, mock_statistics_client,
    ):
        """Каллер из `dep_a`, но явно просит поднять креды другого отдела."""
        calls, _state = mock_statistics_client
        mock_secret_client["cred_other"] = ("other-bot", "other-tok")
        await _configure_statistics()
        await _seed_integration_settings("dep_other", credential_id="cred_other")

        resp = await client.post(
            RECALC_BASE, headers=_hdr(admin_token), json={"department_id": "dep_other"},
        )
        assert resp.status_code == 202, resp.text
        await _drain_pending_tasks()

        assert calls == [{"base_url": "http://stats.example:7777", "username": "other-bot", "token": "other-tok"}]


# ── Хук из services/queue.py на терминальном переходе кампании (§9.3) ──────


class TestQueueTrigger:
    async def test_campaign_success_triggers_recalc(
        self, client, admin_token, mock_server_service, configure_internal_keys,
        mock_secret_client, mock_statistics_client,
    ):
        calls, _state = mock_statistics_client
        mock_secret_client["cred_x"] = ("bot", "tok123")
        mock_secret_client["cred_bitbucket"] = ("git-bot", "git-token")
        mock_server_service()
        await _configure_statistics()
        await _seed_integration_settings("dep_a", bitbucket_credential_id="cred_bitbucket")

        stand_id, _ = await _create_stand(client, admin_token, department_id="dep_a")
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(RUNS_BASE, headers=_hdr(admin_token), json=_payload([stand_id], debug=True))
        assert resp.status_code == 201, resp.text
        run_id = resp.json()["id"]
        detail = await client.get(f"{RUNS_BASE}/{run_id}", headers=_hdr(admin_token))
        item_id = detail.json()["queue_items"][0]["queue_item_id"]
        item = await _get_item(item_id)

        await _drive_to_success(client, item)
        await _drain_pending_tasks()

        status = await client.get(STATUS_BASE, headers=_hdr(admin_token))
        body = status.json()
        assert body["status"] == "succeeded"
        assert body["triggered_by"] == "test_run"
        assert body["test_run_id"] == run_id
        assert len(calls) == 1

    async def test_non_terminal_transition_does_not_trigger(
        self, client, admin_token, mock_server_service, configure_internal_keys,
        mock_secret_client, mock_statistics_client,
    ):
        calls, _state = mock_statistics_client
        mock_secret_client["cred_x"] = ("bot", "tok123")
        mock_server_service()
        await _configure_statistics()
        await _seed_integration_settings("dep_a")

        stand_id, _ = await _create_stand(client, admin_token, department_id="dep_a")
        await _create_test_def(client, admin_token, stand_id)

        resp = await client.post(RUNS_BASE, headers=_hdr(admin_token), json=_payload([stand_id]))
        run_id = resp.json()["id"]

        await _drain_pending_tasks()
        status = await client.get(STATUS_BASE, headers=_hdr(admin_token))
        assert status.json()["status"] == "idle"
        assert calls == []
