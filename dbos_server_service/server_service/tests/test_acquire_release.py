"""Интеграционные тесты `POST /servers/{id}/busy` и `DELETE /servers/{id}/busy`.

Покрытие:
* acquire — happy path с purpose/lease_until, без тела, конкурентный acquire
  → 409 SERVER_ALREADY_BUSY (CAS), decommissioned → 409 SERVER_DECOMMISSIONED;
* release — happy path; уже free → 409 SERVER_NOT_BUSY; releasee может не
  совпадать с заходившим (роль решает);
* permissions — reader/guest → 403; cross-dept → 404; no Bearer → 401;
* `update_os_version` (`POST /os-sync`) — happy path, невалидный os_version_id
  → 422 INVALID_OS_VERSION, permissions reader → 403.

Реальный PostgreSQL через сервисный docker-compose.test.yml (см. conftest).
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from tests._helpers import auth_hdr as _hdr

BASE = "/api/server/v1/servers"


@pytest_asyncio.fixture
async def make_os_version(db):
    """Создаёт строку в os_versions (под FK для update_os_version-тестов)."""
    from src.models import OsVersion
    from src.utils.ids import _new_id

    async def _factory(name: str = "Astra Linux SE 1.8") -> OsVersion:
        osv = OsVersion(id=_new_id("osv_"), name=name)
        db.add(osv)
        await db.flush()
        return osv

    return _factory


# ── POST /busy (acquire) ─────────────────────────────────────────────────────


class TestAcquireServer:
    async def test_operator_acquires_free_server(
        self, client, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/busy",
            headers=_hdr(operator_token_a),
            json={"purpose": "stress-test-001"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == srv.id
        assert body["busy_state"] == "busy"
        assert body["busy_user_id"].startswith("usr_")
        assert body["busy_since"] is not None
        assert "stress-test-001" in (body.get("busy_note") or "")

    async def test_acquire_without_body_works(
        self, client, operator_token_a, make_server,
    ):
        """Body опционален — payload может быть пустым."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 200
        assert resp.json()["busy_state"] == "busy"

    async def test_acquire_with_lease_until_writes_note(
        self, client, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/busy",
            headers=_hdr(operator_token_a),
            json={"purpose": "smoke", "lease_until": "2030-01-01T00:00:00Z"},
        )
        assert resp.status_code == 200
        note = resp.json().get("busy_note") or ""
        assert "smoke" in note
        assert "2030-01-01" in note

    async def test_already_busy_returns_409(
        self, client, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        r1 = await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        assert r1.status_code == 200
        r2 = await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        assert r2.status_code == 409
        assert r2.json()["error_code"] == "SERVER_ALREADY_BUSY"

    async def test_decommissioned_returns_409(
        self, client, operator_token_a, make_server, db,
    ):
        from src.core.constants import ServerStatus

        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"

    async def test_decommission_race_does_not_busy_decommissioned(
        self, client, operator_token_a, make_server, db, monkeypatch,
    ):
        """Race: load_visible_server вернул FREE/ACTIVE, до CAS параллельный
        decommission успел перевести сервер в DECOMMISSIONED.

        Симулируем гонку, патча `load_visible_server` так, чтобы ПОСЛЕ
        возврата ACTIVE-объекта он же на следующем вызове `flush`'нул статус
        DECOMMISSIONED в БД (как если бы это сделал другой воркер).
        Старый код (CAS только по busy_state) переводил сервер в
        busy+decommissioned. После фикса CAS дополнительно фильтрует по
        status<>DECOMMISSIONED → rowcount=0 → re-fetch → 409
        SERVER_DECOMMISSIONED.
        """
        from src.core.constants import ServerStatus
        from src.services import server as server_svc
        from sqlalchemy import update as sa_update
        from src.models import Server

        srv = await make_server(department_id="dep_a")
        original_load = server_svc.load_visible_server

        async def racy_load(db_, identity, sid):
            obj = await original_load(db_, identity, sid)
            # Параллельный writer успел декомиссионить сервер между
            # load_visible_server и CAS. Эмулируем через отдельный UPDATE,
            # коммит делаем сразу — теперь в БД status=DECOMMISSIONED, но
            # `obj` (отдан caller'у) всё ещё держит ACTIVE.
            await db_.execute(
                sa_update(Server)
                .where(Server.id == sid)
                .values(status=ServerStatus.DECOMMISSIONED)
            )
            await db_.commit()
            return obj

        monkeypatch.setattr(server_svc, "load_visible_server", racy_load)

        resp = await client.post(
            f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a),
        )
        # Гонка должна быть пойдана: либо 409 SERVER_DECOMMISSIONED (после
        # re-fetch'а), либо мы вообще не дошли до CAS (если load увидел уже
        # decommissioned). 200 (busy+decommissioned) был бы багом.
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"

        # Проверяем итоговое состояние строки — busy НЕ должен встать.
        from sqlalchemy import select
        from src.core.constants import BusyState

        row = (
            await db.execute(select(Server).where(Server.id == srv.id))
        ).scalar_one()
        assert row.status == ServerStatus.DECOMMISSIONED
        assert row.busy_state == BusyState.FREE

    async def test_reader_cannot_acquire(
        self, client, reader_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(reader_token_a))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "PERMISSION_DENIED"

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"

    async def test_no_token_returns_401(self, client, make_server):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(f"{BASE}/{srv.id}/busy")
        assert resp.status_code == 401

    async def test_nonexistent_server_returns_404(
        self, client, operator_token_a,
    ):
        resp = await client.post(
            f"{BASE}/srv_ghost_42/busy", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404


# ── DELETE /busy (release) ───────────────────────────────────────────────────


class TestReleaseServer:
    async def test_release_busy_server(
        self, client, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        resp = await client.delete(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        assert resp.status_code == 200
        body = resp.json()
        assert body["busy_state"] == "free"
        assert body["busy_user_id"] is None
        assert body["busy_since"] is None
        assert body["busy_note"] is None

    async def test_release_free_server_returns_409(
        self, client, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.delete(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_NOT_BUSY"

    async def test_admin_can_release_someone_elses_acquisition(
        self, client, operator_token_a, admin_role_token_a, make_server,
    ):
        """Любая роль с busy_release может освободить — необязательно тот же
        пользователь, что захватывал."""
        srv = await make_server(department_id="dep_a")
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        resp = await client.delete(f"{BASE}/{srv.id}/busy", headers=_hdr(admin_role_token_a))
        assert resp.status_code == 200

    async def test_reader_cannot_release(
        self, client, reader_token_a, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        resp = await client.delete(f"{BASE}/{srv.id}/busy", headers=_hdr(reader_token_a))
        assert resp.status_code == 403

    async def test_cross_dept_returns_404(
        self, client, operator_token_a, operator_token_b, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        resp = await client.delete(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_b))
        assert resp.status_code == 404


# ── POST /os-sync (update_os_version) ────────────────────────────────────────


class TestUpdateOsVersion:
    async def test_operator_updates_os_version(
        self, client, operator_token_a, make_server, make_os_version,
    ):
        srv = await make_server(department_id="dep_a")
        osv = await make_os_version(name="Astra 1.8")
        resp = await client.post(
            f"{BASE}/{srv.id}/os-sync",
            headers=_hdr(operator_token_a),
            json={"os_version_id": osv.id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["os_version_id"] == osv.id
        assert body["os_last_synced_at"] is not None

    async def test_update_with_null_resets_version(
        self, client, operator_token_a, make_server, make_os_version,
    ):
        srv = await make_server(department_id="dep_a")
        osv = await make_os_version(name="Astra 1.8 to reset")
        await client.post(
            f"{BASE}/{srv.id}/os-sync",
            headers=_hdr(operator_token_a),
            json={"os_version_id": osv.id},
        )
        resp = await client.post(
            f"{BASE}/{srv.id}/os-sync",
            headers=_hdr(operator_token_a),
            json={"os_version_id": None},
        )
        assert resp.status_code == 200
        assert resp.json()["os_version_id"] is None

    async def test_invalid_os_version_returns_422(
        self, client, operator_token_a, make_server,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            f"{BASE}/{srv.id}/os-sync",
            headers=_hdr(operator_token_a),
            json={"os_version_id": "osv_nonexistent_ghost"},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "INVALID_OS_VERSION"

    async def test_reader_cannot_update_os_version(
        self, client, reader_token_a, make_server, make_os_version,
    ):
        srv = await make_server(department_id="dep_a")
        osv = await make_os_version()
        resp = await client.post(
            f"{BASE}/{srv.id}/os-sync",
            headers=_hdr(reader_token_a),
            json={"os_version_id": osv.id},
        )
        assert resp.status_code == 403

    async def test_cross_dept_returns_404(
        self, client, operator_token_b, make_server, make_os_version,
    ):
        srv = await make_server(department_id="dep_a")
        osv = await make_os_version()
        resp = await client.post(
            f"{BASE}/{srv.id}/os-sync",
            headers=_hdr(operator_token_b),
            json={"os_version_id": osv.id},
        )
        assert resp.status_code == 404


# ── Audit emission sanity ────────────────────────────────────────────────────


@pytest.fixture
def captured_emits(monkeypatch):
    from tests._helpers import make_emit_capture

    return make_emit_capture(
        monkeypatch,
        "src.services.server.audit_service.emit",
        "src.api.v1.endpoints.servers.audit_service.emit",
    )


def _events(captured: list[dict], action: str) -> list[dict]:
    return [e for e in captured if e["action"] == action]


class TestBusyAuditEmission:
    async def test_acquire_emits_success(
        self, client, operator_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        successes = [e for e in _events(captured_emits, "server.acquire") if e["status"] == "success"]
        assert len(successes) == 1
        assert successes[0]["target_id"] == srv.id
        assert successes[0]["details"]["department_id"] == "dep_a"

    async def test_release_emits_success(
        self, client, operator_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        await client.delete(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        successes = [e for e in _events(captured_emits, "server.release") if e["status"] == "success"]
        assert len(successes) == 1

    async def test_already_busy_emits_failure(
        self, client, operator_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        failures = [e for e in _events(captured_emits, "server.acquire") if e["status"] == "failure"]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "already_busy"

    async def test_reader_acquire_emits_permission_denied(
        self, client, reader_token_a, make_server, captured_emits,
    ):
        """Reader без `busy_acquire` пишет denied audit с reason=permission_denied.

        canon permission→visibility: на permission_denied obj ещё не загружен,
        поэтому department_id в details не приходит. Идентификация — через
        target_id (server_id).
        """
        srv = await make_server(department_id="dep_a")
        resp = await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(reader_token_a))
        assert resp.status_code == 403
        denied = [e for e in _events(captured_emits, "server.acquire") if e["status"] == "denied"]
        assert len(denied) == 1
        assert denied[0]["details"]["reason"] == "permission_denied"

    async def test_cross_dept_acquire_emits_not_found(
        self, client, operator_token_b, make_server, captured_emits,
    ):
        """Cross-dept caller — visibility ДО permission, reason=not_found_or_cross_dept."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_b))
        assert resp.status_code == 404
        failures = [e for e in _events(captured_emits, "server.acquire") if e["status"] == "failure"]
        assert len(failures) == 1
        assert failures[0]["details"]["reason"] == "not_found_or_cross_dept"

    async def test_reader_release_emits_permission_denied(
        self, client, reader_token_a, operator_token_a, make_server, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        await client.post(f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a))
        resp = await client.delete(f"{BASE}/{srv.id}/busy", headers=_hdr(reader_token_a))
        assert resp.status_code == 403
        denied = [e for e in _events(captured_emits, "server.release") if e["status"] == "denied"]
        assert len(denied) == 1
        assert denied[0]["details"]["reason"] == "permission_denied"

    async def test_reader_os_sync_emits_permission_denied(
        self, client, reader_token_a, make_server, make_os_version, captured_emits,
    ):
        srv = await make_server(department_id="dep_a")
        osv = await make_os_version()
        resp = await client.post(
            f"{BASE}/{srv.id}/os-sync",
            headers=_hdr(reader_token_a),
            json={"os_version_id": osv.id},
        )
        assert resp.status_code == 403
        denied = [e for e in _events(captured_emits, "server.update_os_version") if e["status"] == "denied"]
        assert len(denied) == 1
        assert denied[0]["details"]["reason"] == "permission_denied"


# ── acquire_server CAS-miss re-fetch: rollback() сбрасывает snapshot ────────


class TestAcquireRaceRollback:
    """`acquire_server` после rowcount==0 делает `db.rollback()` ПЕРЕД re-fetch'ем.

    Без rollback'а READ COMMITTED-снапшот текущей tx может вернуть устаревшую
    строку (pre-decommission), и причиной ошибки уезжает `already_busy` вместо
    точного `decommissioned`. После фикса rollback гарантирует, что re-fetch
    идёт в свежей tx и видит свежий commit конкурента.
    """

    async def test_decommission_committed_after_cas_miss_reports_decommissioned(
        self, client, operator_token_a, make_server, monkeypatch, db,
    ):
        """Конкурент: busy_state=BUSY + status=DECOMMISSIONED, committed ПОСЛЕ load_visible.

        Воспроизводим точный сценарий: load_visible_server увидел ACTIVE+FREE,
        потом параллельный writer закоммитил busy+decommissioned (например,
        ручной decommission в другом процессе). CAS промахнётся (busy не FREE
        и status DECOMMISSIONED), мы должны вернуть DECOMMISSIONED, а не
        ALREADY_BUSY. Если rollback() убрать — снапшот не обновится и
        re-fetch вернёт старый ACTIVE+FREE объект из identity-map.
        """
        from sqlalchemy import update as sa_update
        from src.core.constants import BusyState, ServerStatus
        from src.models import Server
        from src.services import server as server_svc

        srv = await make_server(department_id="dep_a")
        original_load = server_svc.load_visible_server

        async def racy_load(db_, identity, sid):
            obj = await original_load(db_, identity, sid)
            # Конкурент закоммитил busy+decommissioned. После load_visible
            # сессия caller'а держит snapshot со status=ACTIVE; rollback в
            # acquire_server должен сбросить snapshot, чтобы re-fetch увидел
            # свежий статус.
            await db_.execute(
                sa_update(Server)
                .where(Server.id == sid)
                .values(
                    status=ServerStatus.DECOMMISSIONED,
                    busy_state=BusyState.BUSY,
                )
            )
            await db_.commit()
            return obj

        monkeypatch.setattr(server_svc, "load_visible_server", racy_load)

        resp = await client.post(
            f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        # Главное: reason — именно decommissioned, а не already_busy.
        # Если rollback() отсутствует, re-fetch может вернуть кэшированный
        # ACTIVE-objects → ветка `current.status == DECOMMISSIONED` не сработает,
        # уйдёт в SERVER_ALREADY_BUSY.
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"

    async def test_expire_called_before_refetch(
        self, client, operator_token_a, make_server, monkeypatch,
    ):
        """Прямая проверка: до re-fetch'а в CAS-miss ветке вызывается db.expire().

        Перехватываем `AsyncSession.expire` через wrapper и фиксируем, что
        кеш текущего объекта инвалидируется минимум один раз после CAS-miss'а.
        Раньше та же роль закрывалась явным `db.rollback()`, но он ломал тестовые
        SAVEPOINT'ы. READ COMMITTED-снапшот PostgreSQL обновляется на каждый
        SELECT в новой транзакции, а commit конкурента в `racy_load` уже закрыл
        предыдущую tx caller'а — expire достаточно, чтобы SQLAlchemy сходил
        в БД и увидел свежее состояние.
        """
        from sqlalchemy import update as sa_update
        from src.core.constants import BusyState
        from src.models import Server
        from src.services import server as server_svc

        srv = await make_server(department_id="dep_a")
        expire_count = {"n": 0}

        original_load = server_svc.load_visible_server

        async def racy_load(db_, identity, sid):
            obj = await original_load(db_, identity, sid)
            # Конкурент закоммитил busy_state=BUSY.
            await db_.execute(
                sa_update(Server)
                .where(Server.id == sid)
                .values(busy_state=BusyState.BUSY)
            )
            await db_.commit()

            # Оборачиваем expire после load, чтобы не считать setup-вызовы.
            real_expire = db_.expire

            def counting_expire(instance, *args, **kwargs):
                expire_count["n"] += 1
                return real_expire(instance, *args, **kwargs)

            db_.expire = counting_expire
            return obj

        monkeypatch.setattr(server_svc, "load_visible_server", racy_load)

        resp = await client.post(
            f"{BASE}/{srv.id}/busy", headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_ALREADY_BUSY"
        assert expire_count["n"] >= 1, (
            "expected db.expire() to be called before CAS-miss re-fetch"
        )
