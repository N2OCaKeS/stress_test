"""Tests for `require_user_context` guard in server_service.

Симметрично `auth_service.require_user_context` и
`secret_service.require_user_context`: m2m identity (OAuth `client_credentials`,
subject_type='oauth_client') не пускается на user-facing бизнес-endpoint'ы.

Покрытие:

* Сам guard: oauth_client → 403 USER_CONTEXT_REQUIRED; user/bot → pass-through.
* Через ASGI: bearer-токен с `subject_type='oauth_client'` отбивается с 403
  на `/servers`, /server-accounts, /ipmi-controllers; user-token проходит.
* `/internal/*` (worker→server_service) НЕ должен ловить этот guard — там
  identity = worker_bot (subject_type='bot'), а ручка использует свою матрицу
  permissions.
"""

from __future__ import annotations

import pytest

from src.core.exceptions import AuthorizationError
from src.dependencies.auth import require_user_context
from src.schemas.identity import IdentityContext
from tests._helpers import assert_error, auth_hdr
from tests.conftest import _identity_body, register_token

BASE = "/api/server/v1"


def _make_identity(subject_type: str | None) -> IdentityContext:
    return IdentityContext(
        user_id="u_1",
        username="tester",
        department_id="dep_1",
        department_name="Test",
        allowed_services=["server_service"],
        service_roles={"server_service": ["operator"]},
        is_banned=False,
        platform_role=None,
        subject_type=subject_type,
    )


class TestRequireUserContextUnit:
    """Прямые unit-тесты гарда — без ASGI."""

    def test_user_passes(self):
        identity = _make_identity("user")
        assert require_user_context(identity) is identity

    def test_bot_passes(self):
        """worker_bot не m2m в OAuth-смысле — у него subject_type='bot' и
        нормальное department/role binding. Guard его пропускает (но в
        реальности bot не должен ходить в user-facing эндпоинты, это
        отбьётся матрицей permissions ниже)."""
        identity = _make_identity("bot")
        assert require_user_context(identity) is identity

    def test_pat_passes(self):
        """PAT auth_service маппит на subject_type='user', но даже если
        пришёл явный 'pat' — это всё ещё человеческий контекст, пропускаем."""
        identity = _make_identity("pat")
        assert require_user_context(identity) is identity

    def test_none_passes(self):
        """Старые токены без subject_type (legacy) пропускаются — поломать
        существующий деплой ужесточением гарда нельзя."""
        identity = _make_identity(None)
        assert require_user_context(identity) is identity

    def test_oauth_client_rejected(self):
        identity = _make_identity("oauth_client")
        with pytest.raises(AuthorizationError) as exc:
            require_user_context(identity)
        assert exc.value.error_code == "USER_CONTEXT_REQUIRED"


class TestUserFacingRoutersBlockOAuth:
    """Через ASGI: m2m token попадает на user-facing ручки → 403."""

    @pytest.fixture
    def m2m_token(self):
        token = "dbos_oauth_test_m2m_token_xxxxxxxxxxxxxxxxxxxxx"
        # OAuth client identity: user_id формата `cli_*`, нет department_id,
        # subject_type='oauth_client'. allowed_services включаем — guard
        # должен срабатывать ДО проверки сервиса.
        register_token(
            token,
            _identity_body(
                user_id="cli_test_client",
                username="m2m-client",
                department_id=None,
                allowed_services=["server_service"],
                subject_type="oauth_client",
            ),
        )
        return token

    @pytest.fixture
    def user_token(self):
        token = "dbos_pat_test_user_xxxxxxxxxxxxxxxxxxxxxxxxxxx"
        register_token(
            token,
            _identity_body(
                user_id="usr_1",
                username="alice",
                department_id="dep_1",
                allowed_services=["server_service"],
                service_roles={"server_service": ["operator"]},
                subject_type="user",
            ),
        )
        return token

    async def test_servers_list_blocks_m2m(self, client, m2m_token):
        resp = await client.get(f"{BASE}/servers", headers=auth_hdr(m2m_token))
        assert_error(resp, 403, "USER_CONTEXT_REQUIRED")

    async def test_server_accounts_blocks_m2m(self, client, m2m_token):
        # /server-accounts is a root collection; any GET against the router
        # triggers require_user_context before the DB layer is touched.
        resp = await client.get(
            f"{BASE}/server-accounts/acc_test_nonexistent",
            headers=auth_hdr(m2m_token),
        )
        assert_error(resp, 403, "USER_CONTEXT_REQUIRED")

    async def test_ipmi_list_blocks_m2m(self, client, m2m_token):
        resp = await client.get(
            f"{BASE}/ipmi-controllers", headers=auth_hdr(m2m_token),
        )
        assert_error(resp, 403, "USER_CONTEXT_REQUIRED")

    async def test_os_versions_post_blocks_m2m(self, client, m2m_token):
        """`/os-versions` GET — public (без авторизации), POST требует user.
        m2m получает 403 USER_CONTEXT_REQUIRED именно на authenticated-ручках."""
        resp = await client.post(
            f"{BASE}/os-versions",
            headers=auth_hdr(m2m_token),
            json={"name": "test-os-1.0"},
        )
        assert_error(resp, 403, "USER_CONTEXT_REQUIRED")

    async def test_permissions_blocks_m2m(self, client, m2m_token):
        resp = await client.get(
            f"{BASE}/permissions/me", headers=auth_hdr(m2m_token),
        )
        # endpoint может вернуть 404 / 403 / 200 — нас интересует только то,
        # что m2m отбит ДО endpoint'а через require_user_context (403 с этим
        # кодом). Если /permissions/me не существует — пропускаем кейс.
        if resp.status_code == 404:
            pytest.skip("/permissions/me endpoint not present in this build")
        assert_error(resp, 403, "USER_CONTEXT_REQUIRED")

    async def test_user_token_passes_servers_list(self, client, user_token):
        """User-token не цепляется require_user_context'ом — endpoint доходит."""
        resp = await client.get(f"{BASE}/servers", headers=auth_hdr(user_token))
        # 200 (пустой список) либо 403/200 от матрицы permissions — не 403
        # USER_CONTEXT_REQUIRED. Проверяем только отсутствие нашего кода.
        if resp.status_code == 403:
            body = resp.json()
            assert body.get("error_code") != "USER_CONTEXT_REQUIRED", (
                f"User token must not be rejected by require_user_context, got: {body}"
            )

    async def test_internal_routes_do_not_use_guard(self, client, m2m_token):
        """`/internal/*` не цепляет require_user_context.

        На /internal/* m2m-токен дойдёт до endpoint-business-логики; там его
        либо разрешат (worker_bot subject_type='bot' с правильными правами),
        либо отобьют PermissionDenied/AuthorizationError по матрице. Нашего
        USER_CONTEXT_REQUIRED тут НЕ должно быть — внутренние ручки используют
        свою scope-семантику.
        """
        resp = await client.get(
            f"{BASE}/internal/secrets/migration_status",
            headers=auth_hdr(m2m_token),
        )
        if resp.status_code == 404:
            pytest.skip("internal endpoint not mounted in this test build")
        body = resp.json() if resp.headers.get("content-type", "").startswith(
            "application/json"
        ) else {}
        assert body.get("error_code") != "USER_CONTEXT_REQUIRED", (
            "Internal endpoints must not be guarded by require_user_context; "
            f"got body: {body}"
        )
