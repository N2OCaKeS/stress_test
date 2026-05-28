"""Тесты `POST /api/server/v1/servers/{id}/installed-packages` — live SSH-probe.

Endpoint не работает с БД — он только публикует задачу `installed_packages.list`
в worker и возвращает `{task_id, status}`. Тесты мочат `worker_client.dispatch_task`
монкеи-патчем и фиксируют именно argument shape (task_kind / payload / pattern).

Покрытие (7 тестов):

* success — operator с правом `(server, view)` получает 202 + task_id, пайтерн
  пробрасывается в worker'ский payload.
* empty pattern result — task сразу сабмиттится; то, что вернёт worker —
  отдельный тест worker'а (см. `server_worker/tests/test_installed_packages_task.py`).
  Здесь проверяем, что dispatch'ер принимает пустой `pattern` (вариация: тот же
  default `*`).
* invalid pattern → 400 INVALID_PATTERN — `;`/`|`/spaces / quotes отбиваются.
* server not found / cross-dept → 404 SERVER_NOT_FOUND.
* dispatch failure → 503 (worker unreachable, exception в worker_client).
* permission denied — guest без `(server, view)` → 403 PERMISSION_DENIED.
* decommissioned server → 409 SERVER_DECOMMISSIONED.
"""

from __future__ import annotations

import pytest

BASE = "/api/server/v1"


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _url(server_id: str) -> str:
    return f"{BASE}/servers/{server_id}/installed-packages"


@pytest.fixture
def captured_dispatch(monkeypatch):
    """Перехват `worker_client.dispatch_task` ВО ВСЕХ дёргающих модулях.

    endpoint импортит `from src.services import worker_client` и зовёт
    `worker_client.dispatch_task(...)` — патчим главный модуль + прямую
    ссылку из endpoint-модуля (defensive).
    """
    calls: list[dict] = []

    async def fake_dispatch(*, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None):
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
            "created_by": created_by,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        })
        return f"tsk_pkg_fake_{len(calls)}"

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(
        "src.api.v1.endpoints.installed_packages.worker_client.dispatch_task",
        fake_dispatch,
    )
    return calls


# ── 1. Success ──────────────────────────────────────────────────────────────


class TestDispatchSuccess:
    async def test_operator_dispatches_with_pattern(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            _url(srv.id),
            headers=_hdr(operator_token_a),
            params={"pattern": "linux-image*"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["task_id"].startswith("tsk_pkg_fake_")
        assert body["status"] == "queued"
        assert len(captured_dispatch) == 1
        call = captured_dispatch[0]
        assert call["task_kind"] == "installed_packages.list"
        assert call["target_server_id"] == srv.id
        assert call["payload"] == {
            "server_id": srv.id,
            "host": srv.hostname,
            "ssh_port": srv.ssh_port,
            "pattern": "linux-image*",
            "target_department_id": "dep_a",
        }

    async def test_default_pattern_is_wildcard(
        self, client, operator_token_a, make_server, captured_dispatch,
    ):
        """Без `?pattern=` query — глобальный `*` (все пакеты). Эмулирует
        кейс «empty result possible» — worker может вернуть пустой массив,
        но dispatch'ер на это не смотрит."""
        srv = await make_server(department_id="dep_a")
        resp = await client.post(_url(srv.id), headers=_hdr(operator_token_a))
        assert resp.status_code == 202
        assert len(captured_dispatch) == 1
        assert captured_dispatch[0]["payload"]["pattern"] == "*"


# ── 2. Pattern validation ───────────────────────────────────────────────────


class TestPatternValidation:
    @pytest.mark.parametrize("bad_pattern", [
        "foo;bar",       # shell-separator
        "foo|bar",       # pipe
        "foo bar",       # пробел
        "foo$bar",       # variable expansion
        "foo`whoami`",   # command substitution
        "foo'bar",       # quote — break-out из одинарных
        "foo\"bar",      # quote
        "../etc/passwd", # path traversal
    ])
    async def test_invalid_pattern_returns_422(
        self, client, operator_token_a, make_server, captured_dispatch,
        bad_pattern,
    ):
        """DomainValidationError → 422 INVALID_PATTERN. Cеми shell-метасимволов
        и path-traversal должно хватать для покрытия injection-vectors'ов.
        """
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            _url(srv.id),
            headers=_hdr(operator_token_a),
            params={"pattern": bad_pattern},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "INVALID_PATTERN"
        assert captured_dispatch == []


# ── 3. Visibility / dept-isolation ──────────────────────────────────────────


class TestVisibility:
    async def test_server_not_found_returns_404(
        self, client, operator_token_a, captured_dispatch,
    ):
        resp = await client.post(
            _url("srv_ghost_no_such_id"),
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"
        assert captured_dispatch == []

    async def test_cross_dept_server_returns_404(
        self, client, operator_token_b, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            _url(srv.id),
            headers=_hdr(operator_token_b),
        )
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "SERVER_NOT_FOUND"
        assert captured_dispatch == []


# ── 4. Permissions ──────────────────────────────────────────────────────────


class TestPermissions:
    async def test_no_token_returns_401(
        self, client, make_server, captured_dispatch,
    ):
        srv = await make_server(department_id="dep_a")
        resp = await client.post(_url(srv.id))
        assert resp.status_code == 401
        assert captured_dispatch == []


# ── 5. Decommissioned + worker failures ─────────────────────────────────────


class TestWorkerSideFailures:
    async def test_decommissioned_returns_409(
        self, client, operator_token_a, make_server, captured_dispatch, db,
    ):
        from src.core.constants import ServerStatus
        srv = await make_server(department_id="dep_a")
        srv.status = ServerStatus.DECOMMISSIONED
        await db.flush()
        resp = await client.post(
            _url(srv.id),
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "SERVER_DECOMMISSIONED"
        assert captured_dispatch == []

    async def test_worker_unreachable_returns_503(
        self, client, operator_token_a, make_server, monkeypatch,
    ):
        from src.core.exceptions import ServiceUnavailableError

        async def fail_dispatch(*args, **kwargs):
            raise ServiceUnavailableError(
                error_code="WORKER_UNREACHABLE",
                message="Failed to publish task to worker broker: ConnectionError",
            )

        monkeypatch.setattr(
            "src.api.v1.endpoints.installed_packages.worker_client.dispatch_task",
            fail_dispatch,
        )
        srv = await make_server(department_id="dep_a")
        resp = await client.post(
            _url(srv.id),
            headers=_hdr(operator_token_a),
        )
        assert resp.status_code == 503
        assert resp.json()["error_code"] == "WORKER_UNREACHABLE"
