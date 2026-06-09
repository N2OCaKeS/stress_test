"""Финальное закрытие непокрытых веток docker-registry token flow.

После предыдущих волн оставались мелкие, но регрессионно-значимые ветки:
push на disabled registry с не-legacy scope (симметрия с pull-веткой),
unit-покрытие `_resolve_actions` по всем 4 переходам, и точная shape-проверка
NO_CFG/DISABLED audit-деталей (`username`, `service`, `requested_scope`),
до этого assert'ился только `reason`.
"""

from types import SimpleNamespace

import jwt as _jwt
import pytest_asyncio

from src.models.department_docker_registry import (
    PULL_POLICY_ALL,
    PULL_POLICY_RESTRICTED,
    DepartmentDockerRegistry,
)
from src.utils.ids import _new_id
from tests._helpers.http import _basic  # noqa: F401 — общий helper

TOKEN_URL = "/api/auth/v1/docker/token"


def _decode_access(token: str) -> list[dict]:
    return _jwt.decode(token, options={"verify_signature": False}).get("access", [])


def _capture_audit(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    import src.services.audit_service as audit_mod
    original = audit_mod.emit

    def _spy(action, actor_id=None, **kw):
        captured.append({"action": action, "actor_id": actor_id, **kw})
        return original(action, actor_id, **kw)

    monkeypatch.setattr(audit_mod, "emit", _spy)
    return captured


@pytest_asyncio.fixture()
async def registry_a_pull_all(db, dept_a, user_a):
    cfg = DepartmentDockerRegistry(
        id=_new_id("ddr_"),
        department_id=dept_a.id,
        is_enabled=True,
        pull_policy="all",
        pull_user_ids=[],
        push_user_ids=[user_a.id],
    )
    db.add(cfg)
    await db.flush()
    return cfg


# ── push на disabled registry с не-legacy scope ──────────────────────────────


class TestPushDisabledNonLegacyScope:
    async def test_push_on_disabled_registry_emits_disabled_audit(
        self, client, user_a, dept_a, db, monkeypatch,
    ):
        """user_a в dept_a, у того же отдела docker config с `is_enabled=False`,
        scope с `<dept>/<image>` (не legacy) → legacy_only_scope guard не
        срабатывает, доходим до push-handler'а; cfg есть, но disabled →
        `docker.push_denied reason=REGISTRY_DISABLED`. Симметрия с уже покрытым
        pull-кейсом (`test_pull_on_disabled_registry_emits_disabled_audit`)."""
        cfg = DepartmentDockerRegistry(
            id=_new_id("ddr_"),
            department_id=dept_a.id,
            is_enabled=False,
            pull_policy="all",
            pull_user_ids=[],
            push_user_ids=[user_a.id],
        )
        db.add(cfg)
        await db.flush()

        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={
                "service": "registry.test",
                "scope": f"repository:{dept_a.name}/image:push",
            },
        )
        # Soft-omit: 200 с пустым access; push выкинут, REGISTRY_DISABLED audit.
        assert resp.status_code == 200, resp.text
        access = _decode_access(resp.json()["access_token"])
        assert all("push" not in e.get("actions", []) for e in access)

        push_denied = [e for e in captured if e["action"] == "docker.push_denied"]
        assert push_denied, captured
        ev = push_denied[-1]
        assert ev["details"]["reason"] == "REGISTRY_DISABLED"
        assert ev["details"]["registry_name"] == dept_a.name


# ── NO_CFG / DISABLED audit shape: полные details (username/service/scope) ───


class TestLegacyGuardAuditShape:
    async def test_no_cfg_audit_includes_username_service_and_scope(
        self, client, user_a, dept_a, monkeypatch,
    ):
        """У caller'а нет docker config, legacy scope → DOCKER_ACCESS_DENIED +
        audit с полным набором деталей. Существующий тест проверяет только
        `reason=NO_CFG`; SOC ещё ищет `username`/`service`/`requested_scope`
        для корреляции попыток конфигурации registry'я по логам."""
        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={
                "service": "registry.test",
                "scope": "repository:legacy_image:pull",
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "DOCKER_ACCESS_DENIED"

        failures = [
            e for e in captured
            if e["action"] == "docker.token_issued" and e.get("status") == "failure"
        ]
        assert failures, captured
        details = failures[-1]["details"]
        assert details["reason"] == "NO_CFG"
        assert details["username"] == "t_user_a"
        assert details["service"] == "registry.test"
        assert details["requested_scope"] == "repository:legacy_image:pull"

    async def test_disabled_legacy_audit_includes_username_service_and_scope(
        self, client, user_a, dept_a, db, monkeypatch,
    ):
        """Docker config у caller-отдела есть, но `is_enabled=False`, scope
        legacy → DOCKER_ACCESS_DENIED + audit с `reason=DISABLED` и тем же
        набором деталей. Покрывает вторую половину легаси-guard'а
        (`else DISABLED`) с полной shape-проверкой."""
        cfg = DepartmentDockerRegistry(
            id=_new_id("ddr_"),
            department_id=dept_a.id,
            is_enabled=False,
            pull_policy="all",
            pull_user_ids=[],
            push_user_ids=[],
        )
        db.add(cfg)
        await db.flush()

        captured = _capture_audit(monkeypatch)
        resp = await client.get(
            TOKEN_URL,
            headers=_basic("t_user_a", "User12345678!"),
            params={
                "service": "registry.test",
                "scope": "repository:legacy_disabled:pull",
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "DOCKER_ACCESS_DENIED"

        failures = [
            e for e in captured
            if e["action"] == "docker.token_issued" and e.get("status") == "failure"
        ]
        assert failures, captured
        details = failures[-1]["details"]
        assert details["reason"] == "DISABLED"
        assert details["username"] == "t_user_a"
        assert details["service"] == "registry.test"
        assert details["requested_scope"] == "repository:legacy_disabled:pull"


# ── _resolve_actions: unit-покрытие всех 4 переходов ────────────────────────


class TestResolveActionsBranches:
    """`_resolve_actions(cfg, subject_id, requested_actions)` — 4 ветви:
    pull-all, pull-restricted-в-списке, pull-restricted-не-в-списке (косвенно),
    push-в-списке. Проверяем единый helper напрямую, без HTTP-обвязки.
    """

    def _cfg(self, *, pull_policy: str, pull_user_ids=None, push_user_ids=None):
        return SimpleNamespace(
            pull_policy=pull_policy,
            pull_user_ids=pull_user_ids or [],
            push_user_ids=push_user_ids or [],
        )

    def test_pull_all_grants_pull_regardless_of_pull_user_ids(self):
        from src.services.docker_registry_service import _resolve_actions

        cfg = self._cfg(pull_policy=PULL_POLICY_ALL)
        # pull_user_ids пуст, но pull_policy=all → pull выдаётся всем.
        assert _resolve_actions(cfg, "usr_anyone", ["pull"]) == ["pull"]

    def test_pull_restricted_listed_user_gets_pull(self):
        from src.services.docker_registry_service import _resolve_actions

        cfg = self._cfg(
            pull_policy=PULL_POLICY_RESTRICTED, pull_user_ids=["usr_alpha"],
        )
        assert _resolve_actions(cfg, "usr_alpha", ["pull"]) == ["pull"]

    def test_pull_restricted_unlisted_user_no_pull(self):
        from src.services.docker_registry_service import _resolve_actions

        cfg = self._cfg(
            pull_policy=PULL_POLICY_RESTRICTED, pull_user_ids=["usr_alpha"],
        )
        # usr_beta не в pull_user_ids, policy=restricted → pull не даётся.
        assert _resolve_actions(cfg, "usr_beta", ["pull"]) == []

    def test_push_listed_user_gets_push(self):
        from src.services.docker_registry_service import _resolve_actions

        cfg = self._cfg(
            pull_policy=PULL_POLICY_ALL, push_user_ids=["usr_pusher"],
        )
        assert _resolve_actions(cfg, "usr_pusher", ["push"]) == ["push"]

    def test_push_unlisted_user_no_push_even_with_pull_all(self):
        from src.services.docker_registry_service import _resolve_actions

        cfg = self._cfg(
            pull_policy=PULL_POLICY_ALL, push_user_ids=["usr_alpha"],
        )
        # pull_policy=all не влияет на push; usr_beta не в push_user_ids.
        assert _resolve_actions(cfg, "usr_beta", ["push"]) == []

    def test_combined_pull_push_returns_only_granted(self):
        from src.services.docker_registry_service import _resolve_actions

        cfg = self._cfg(
            pull_policy=PULL_POLICY_ALL, push_user_ids=["usr_pusher"],
        )
        # usr_pusher имеет и pull (через all), и push (через list).
        result = _resolve_actions(cfg, "usr_pusher", ["pull", "push"])
        assert set(result) == {"pull", "push"}
