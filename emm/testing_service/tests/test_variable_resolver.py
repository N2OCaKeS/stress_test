"""Резолвер переменных: источники, шаблоны, циклы, маскировка, валидация.

Шаблоны и сид-формулы против легаси — в `test_launch_context.py`; здесь —
механизм: каждая ветка источника, кеш, защита от циклов, маскировка через
шаблон и `override_value`, проверки при сохранении переменной (422/409).
"""

from __future__ import annotations

import uuid

import pytest

from src.core.exceptions import DomainValidationError, NotFoundError
from src.db.session import AsyncSessionLocal
from src.models import GlobalVariable, TestDefinition, TestStand
from src.repositories import department_integration_settings as dis_repo
from src.services import secret_client, server_client
from src.services import test_command_arg as svc
from src.services import variable_resolver as vr
from tests.conftest import auth_hdr as _hdr

TESTS_BASE = "/api/testing/v1/test-definitions"
VARS_BASE = "/api/testing/v1/global-variables"
# `RC` — id карточки версии ОС (`osv_<hex>`); имя (`RC_NAME`) — из карточки.
CTX = {"RC": "osv_8a16c9e0", "KERNEL": "6.1.90-1-generic", "MODE": "orel"}

OS_VERSIONS: dict[str, server_client.OsVersionInfo] = {
    "osv_8a16c9e0": server_client.OsVersionInfo(name="1.8.1.6", is_urgent_update=False, rc_number="RC6"),
    # UU-версия с проставленным флагом и без него: легаси определял UU по имени.
    "osv_uu179120": server_client.OsVersionInfo(name="1.7.9.UU.1.2", is_urgent_update=True, rc_number="RC2"),
    "osv_uu_noflag": server_client.OsVersionInfo(name="1.7.3.UU.1.4", is_urgent_update=False, rc_number=None),
    "osv_short": server_client.OsVersionInfo(name="1.8", is_urgent_update=False, rc_number=None),
}


@pytest.fixture(autouse=True)
def os_version_calls(monkeypatch) -> list[str]:
    """`server_client.resolve_os_version_info` по каталогу `OS_VERSIONS`; список вызовов."""
    calls: list[str] = []

    async def fake_resolve(os_version_id: str) -> server_client.OsVersionInfo:
        calls.append(os_version_id)
        info = OS_VERSIONS.get(os_version_id)
        if info is None:
            raise NotFoundError(error_code="OS_VERSION_NOT_FOUND", message=os_version_id)
        return info

    monkeypatch.setattr(server_client, "resolve_os_version_info", fake_resolve)
    return calls


def _code(prefix: str = "V") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8].upper()}"


async def _create_var(client, token, **fields) -> dict:
    payload = {"code": _code(), "label": "Переменная", "source": "launch_context", **fields}
    resp = await client.post(VARS_BASE, headers=_hdr(token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_test(client, token) -> str:
    resp = await client.post(
        TESTS_BASE, headers=_hdr(token),
        json={"code": f"resolver.{uuid.uuid4().hex[:8]}", "full_name": "Резолвер переменных"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_slot(client, token, test_id, variable_id, override_value=None):
    payload = {"kind": "variable", "variable_id": variable_id}
    if override_value is not None:
        payload["override_value"] = override_value
    resp = await client.post(f"{TESTS_BASE}/{test_id}/args", headers=_hdr(token), json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _stand(department_id: str = "dep_a", legacy_token: str | None = "stand7") -> TestStand:
    return TestStand(
        id="stand_" + uuid.uuid4().hex, server_id="srv_" + uuid.uuid4().hex[:8],
        department_id=department_id, legacy_token=legacy_token, queue_enabled=True, is_active=True,
    )


def _ctx(db, *, stand=None, test=None, launch_context=None, debug=False) -> vr.ResolveContext:
    return vr.ResolveContext(
        db=db, department_id=stand.department_id if stand else None, test=test, stand=stand,
        launch_context=dict(CTX if launch_context is None else launch_context), debug=debug,
    )


# ── шаблоны ──────────────────────────────────────────────────────────────────

class TestTemplates:
    async def test_nested_templates_resolve_recursively(self, client, admin_token):
        inner = await _create_var(client, admin_token, source="template", source_ref={"template": "{MODE}-{KERNEL}"})
        outer = await _create_var(
            client, admin_token, source="template", source_ref={"template": f"[{{{inner['code']}}}] {{STAND_TOKEN}}"},
        )
        test_id = await _create_test(client, admin_token)
        await _add_slot(client, admin_token, test_id, outer["id"])
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, CTX, stand=_stand())
        assert args == ["[orel-6.1.90-1-generic] stand7"]

    async def test_non_placeholder_braces_are_kept_as_text(self, client, admin_token):
        var = await _create_var(client, admin_token, source="template", source_ref={"template": '{"a": 1} {lower} {MODE}'})
        test_id = await _create_test(client, admin_token)
        await _add_slot(client, admin_token, test_id, var["id"])
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, CTX)
        assert args == ['{"a": 1} {lower} orel']

    async def test_override_value_understands_substitutions(self, client, admin_token):
        """D5: «переопределение на тесте» — `override_value` слота тоже шаблон."""
        test_id = await _create_test(client, admin_token)
        cnp = await client.get(f"{VARS_BASE}/by-code/CONFLUENCE_NEW_PAGE", headers=_hdr(admin_token))
        await _add_slot(client, admin_token, test_id, cnp.json()["id"], override_value="custom_{RC_NAME}_{STAND_TOKEN}")
        async with AsyncSessionLocal() as db:
            args = await svc.resolve_command(db, test_id, CTX, stand=_stand())
        assert args == ["custom_1.8.1.6_stand7"]

    async def test_plain_override_is_still_literal(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        fti = await client.get(f"{VARS_BASE}/by-code/FOLDER_TREE_ID", headers=_hdr(admin_token))
        await _add_slot(client, admin_token, test_id, fti.json()["id"], override_value="none")
        async with AsyncSessionLocal() as db:
            assert await svc.resolve_command(db, test_id, {}) == ["none"]

    async def test_template_when_condition(self, client, admin_token):
        var = await _create_var(
            client, admin_token, source="template", source_ref={"template": "prod", "when": "not_debug"},
        )
        test_id = await _create_test(client, admin_token)
        await _add_slot(client, admin_token, test_id, var["id"])
        async with AsyncSessionLocal() as db:
            assert await svc.resolve_command(db, test_id, CTX, debug=False) == ["prod"]
            assert await svc.resolve_command(db, test_id, CTX, debug=True) == [""]

    async def test_cycle_is_detected_at_resolve_time(self):
        """Каталог, сохранённый в обход валидации, не вешает резолв — понятная ошибка."""
        a = GlobalVariable(code="CYC_A", source="template", source_ref={"template": "{CYC_B}"}, is_sensitive=False)
        b = GlobalVariable(code="CYC_B", source="template", source_ref={"template": "x{CYC_A}"}, is_sensitive=False)
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db)
            ctx._catalog = {"CYC_A": a, "CYC_B": b}
            with pytest.raises(DomainValidationError) as exc:
                await vr.resolve_code(ctx, "CYC_A")
        assert exc.value.error_code == "VARIABLE_TEMPLATE_CYCLE"
        assert exc.value.details["cycle"] == ["CYC_A", "CYC_B", "CYC_A"]

    async def test_values_are_cached_within_one_resolve(self, monkeypatch):
        calls: list[str] = []

        async def fake_connection(server_id: str) -> dict:
            calls.append(server_id)
            return {"host": "10.1.2.3"}

        monkeypatch.setattr(server_client, "get_connection_info", fake_connection)
        host = GlobalVariable(code="H", source="stand", source_ref={"field": "host"}, is_sensitive=False)
        both = GlobalVariable(code="BOTH", source="template", source_ref={"template": "{H}/{H}"}, is_sensitive=False)
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, stand=_stand())
            ctx._catalog = {"H": host, "BOTH": both}
            assert (await vr.resolve_code(ctx, "BOTH")).value == "10.1.2.3/10.1.2.3"
            assert (await vr.resolve_code(ctx, "H")).value == "10.1.2.3"
        assert len(calls) == 1


# ── маскировка ───────────────────────────────────────────────────────────────

class TestMasking:
    async def test_sensitive_value_inside_template_masks_whole_token(self, client, admin_token):
        secret = await _create_var(client, admin_token, is_sensitive=True)
        wrap = await _create_var(
            client, admin_token, source="template",
            source_ref={"template": f"Bearer {{{secret['code']}}} for {{MODE}}"},
        )
        test_id = await _create_test(client, admin_token)
        resp = await client.post(
            f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "-ba"},
        )
        assert resp.status_code == 201
        await _add_slot(client, admin_token, test_id, wrap["id"])
        ctx = {**CTX, secret["code"]: "s3cr3t"}
        async with AsyncSessionLocal() as db:
            raw = await svc.resolve_command(db, test_id, ctx)
            masked = await svc.resolve_command_masked(db, test_id, ctx)
            dates, dates_masked = await svc.resolve_dates(db, test_id, ctx)
            dates_masked_2 = await svc.resolve_dates_content_masked(db, test_id, ctx)
        assert raw == ["-ba", "Bearer s3cr3t for orel"]
        assert masked == ["-ba", "***"]
        assert dates == "-ba 'Bearer s3cr3t for orel'"
        assert dates_masked == dates_masked_2 == "-ba '***'"

    async def test_sensitive_value_in_override_masks_token(self, client, admin_token):
        secret = await _create_var(client, admin_token, is_sensitive=True)
        test_id = await _create_test(client, admin_token)
        testenv = await client.get(f"{VARS_BASE}/by-code/TESTENV", headers=_hdr(admin_token))
        await _add_slot(client, admin_token, test_id, testenv.json()["id"], override_value=f"x{{{secret['code']}}}")
        ctx = {**CTX, secret["code"]: "pw"}
        async with AsyncSessionLocal() as db:
            assert await svc.resolve_command(db, test_id, ctx) == ["xpw"]
            assert await svc.resolve_command_masked(db, test_id, ctx) == ["***"]

    async def test_template_without_sensitive_parts_is_not_masked(self, client, admin_token):
        cycle = await client.get(f"{VARS_BASE}/by-code/TEST_CYCLE_NAME", headers=_hdr(admin_token))
        test_id = await _create_test(client, admin_token)
        await _add_slot(client, admin_token, test_id, cycle.json()["id"])
        async with AsyncSessionLocal() as db:
            masked = await svc.resolve_command_masked(db, test_id, CTX, stand=_stand())
        assert masked == ["1.8.1.6_orel_6.1.90-1-generic_stand7"]


# ── источники ────────────────────────────────────────────────────────────────

@pytest.fixture
async def integration_settings():
    department_id = f"dep_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as db:
        await dis_repo.create(db, {
            "id": f"dis_{uuid.uuid4().hex[:8]}", "department_id": department_id,
            "jira_base_url": "https://jira.example", "confluence_credential_id": "cred_conf",
            "confluence_report_page_space": "DEVQA",
        })
        await db.commit()
    return department_id


@pytest.fixture
def fake_reveal(monkeypatch):
    calls: list[str] = []

    async def reveal(cred_id: str) -> tuple[str, str]:
        calls.append(cred_id)
        return f"login-of-{cred_id}", f"secret-of-{cred_id}"

    monkeypatch.setattr(secret_client, "reveal_credential", reveal)
    return calls


def _gv(code: str, source: str, source_ref: dict | None, *, sensitive: bool = False) -> GlobalVariable:
    return GlobalVariable(code=code, source=source, source_ref=source_ref, is_sensitive=sensitive)


class TestDepartmentIntegration:
    async def test_plain_field_and_credential_parts(self, integration_settings, fake_reveal):
        catalog = {
            "URL": _gv("URL", "department_integration", {"field": "jira_base_url"}),
            "SPACE": _gv("SPACE", "department_integration", {"field": "confluence_report_page_space"}),
            "USER": _gv("USER", "department_integration", {"field": "confluence_credential_id", "credential_part": "login"}),
            "TOKEN": _gv(
                "TOKEN", "department_integration",
                {"field": "confluence_credential_id", "credential_part": "secret"}, sensitive=True,
            ),
            "AUTH": _gv("AUTH", "template", {"template": "{USER}:{TOKEN}"}),
        }
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, stand=_stand(department_id=integration_settings))
            ctx._catalog = catalog
            assert (await vr.resolve_code(ctx, "URL")) == vr.Resolved("https://jira.example")
            assert (await vr.resolve_code(ctx, "SPACE")).value == "DEVQA"
            user = await vr.resolve_code(ctx, "USER")
            token = await vr.resolve_code(ctx, "TOKEN")
            auth = await vr.resolve_code(ctx, "AUTH")
        assert (user.value, user.sensitive) == ("login-of-cred_conf", False)
        assert (token.value, token.masked) == ("secret-of-cred_conf", "***")
        assert (auth.value, auth.masked) == ("login-of-cred_conf:secret-of-cred_conf", "***")
        # Один reveal на credential за резолв, хотя упомянут трижды.
        assert fake_reveal == ["cred_conf"]

    async def test_unconfigured_field_is_explicit_error(self, integration_settings, fake_reveal):
        catalog = {"BA": _gv("BA", "department_integration", {"field": "credential_id", "credential_part": "secret"}, sensitive=True)}
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, stand=_stand(department_id=integration_settings))
            ctx._catalog = catalog
            with pytest.raises(DomainValidationError) as exc:
                await vr.resolve_code(ctx, "BA")
        assert exc.value.error_code == "DEPARTMENT_INTEGRATION_NOT_CONFIGURED"
        assert fake_reveal == []

    async def test_department_without_settings(self, fake_reveal):
        catalog = {"URL": _gv("URL", "department_integration", {"field": "jira_base_url"})}
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, stand=_stand(department_id="dep_nothing_here"))
            ctx._catalog = catalog
            with pytest.raises(DomainValidationError) as exc:
                await vr.resolve_code(ctx, "URL")
        assert exc.value.error_code == "DEPARTMENT_INTEGRATION_NOT_CONFIGURED"

    async def test_command_uses_stand_department(self, client, admin_token, integration_settings, fake_reveal):
        """Сквозь `resolve_dates`: отдел — стенда, секрет маскируется."""
        token = await _create_var(
            client, admin_token, source="department_integration", is_sensitive=True,
            source_ref={"field": "confluence_credential_id", "credential_part": "secret"},
        )
        test_id = await _create_test(client, admin_token)
        await client.post(
            f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
            json={"kind": "literal", "literal_value": "--token"},
        )
        await _add_slot(client, admin_token, test_id, token["id"])
        async with AsyncSessionLocal() as db:
            content, masked = await svc.resolve_dates(
                db, test_id, CTX, stand=_stand(department_id=integration_settings),
            )
        assert content == "--token secret-of-cred_conf"
        assert masked == "--token '***'"


class TestOtherSources:
    async def test_stand_fields(self, monkeypatch):
        async def fake_connection(server_id: str) -> dict:
            return {"host": "10.9.9.9"}

        monkeypatch.setattr(server_client, "get_connection_info", fake_connection)
        catalog = {
            "T": _gv("T", "stand", {"field": "legacy_token"}),
            "N": _gv("N", "stand", {"field": "number"}),
            "H": _gv("H", "stand", {"field": "host"}),
        }
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, stand=_stand(legacy_token="stand14"))
            ctx._catalog = catalog
            values = [(await vr.resolve_code(ctx, c)).value for c in ("T", "N", "H")]
        assert values == ["stand14", "14", "10.9.9.9"]

    async def test_stand_source_without_stand_is_context_error(self):
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db)
            ctx._catalog = {"T": _gv("T", "stand", {"field": "legacy_token"})}
            with pytest.raises(DomainValidationError) as exc:
                await vr.resolve_code(ctx, "T")
        assert exc.value.error_code == "VARIABLE_CONTEXT_MISSING"

    async def test_test_field(self):
        test = TestDefinition(id="tdef_x", code="x.y", full_name="Full", category="kernel", changelog_component=None)
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, test=test)
            ctx._catalog = {
                "C": _gv("C", "test_field", {"field": "category"}),
                "K": _gv("K", "test_field", {"field": "code"}),
                "TOPIC": _gv("TOPIC", "test_field", {"field": "changelog_component", "fallback": "category"}),
            }
            assert [(await vr.resolve_code(ctx, c)).value for c in ("C", "K", "TOPIC")] == ["kernel", "x.y", "kernel"]


    @pytest.mark.real_test_account
    async def test_test_account_not_configured(self, client, admin_token):
        """Учётки отдела нет — понятная ошибка с путём в администрирование."""
        var = await _create_var(client, admin_token, source="test_account", source_ref={"field": "login"})
        async with AsyncSessionLocal() as db:
            with pytest.raises(DomainValidationError) as exc:
                await vr.resolve_code(_ctx(db, stand=_stand(department_id="dep_noacct")), var["code"])
        assert exc.value.error_code == "TEST_ACCOUNT_NOT_CONFIGURED"

    @pytest.mark.real_test_account
    async def test_test_account_fields(self, client, admin_token, monkeypatch):
        """`TEST_USER`/`TEST_PASSWORD`/`TEST_HOME` — из учётки отдела, credential раскрывается один раз."""
        from src.repositories import department_test_settings as dts_repo
        from src.services import test_account as test_account_svc

        department_id = f"dep_acct_{uuid.uuid4().hex[:6]}"
        secret = test_account_svc.encode_secret(password="p@ss word", private_key="PRIV", public_key="PUB")
        reveals: list[str] = []

        async def fake_reveal(cred_id: str):
            reveals.append(cred_id)
            return "tester", secret

        monkeypatch.setattr(secret_client, "reveal_credential", fake_reveal)
        async with AsyncSessionLocal() as db:
            await dts_repo.create(db, {
                "id": f"dts_{uuid.uuid4().hex[:8]}", "department_id": department_id,
                "retry_enabled": True, "test_username": "u", "activity_report_auto_generate": False,
                "test_account_credential_id": "cred_acct", "test_account_home_template": "/srv/{TEST_USER}",
            })
            await db.commit()
            ctx = _ctx(db, stand=_stand(department_id=department_id))
            user = await vr.resolve_code(ctx, "TEST_USER")
            password = await vr.resolve_code(ctx, "TEST_PASSWORD")
            home = await vr.resolve_code(ctx, "TEST_HOME")
            legacy_home = await vr.resolve_code(ctx, "HOME_DIR")
        assert (user.value, user.sensitive) == ("tester", False)
        assert (password.value, password.sensitive) == ("p@ss word", True)
        assert home.value == legacy_home.value == "/srv/tester"
        assert reveals == ["cred_acct"]

    async def test_test_account_password_requires_sensitive(self, client, admin_token):
        resp = await client.post(VARS_BASE, headers=_hdr(admin_token), json={
            "code": f"PW_{uuid.uuid4().hex[:6].upper()}", "label": "pw", "source": "test_account",
            "value_type": "string", "is_sensitive": False, "source_ref": {"field": "password"},
        })
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_SOURCE_REF_INVALID"

    async def test_static_value(self):
        async with AsyncSessionLocal() as db:
            assert (await vr.resolve_code(_ctx(db), "DEBUG_PREFIX")).value == "DEBUG_"
            assert (await vr.resolve_code(_ctx(db, debug=True), "IS_DEBUG_PREFIX")).value == "DEBUG_"
            assert (await vr.resolve_code(_ctx(db, debug=False), "IS_DEBUG_PREFIX")).value == ""


# ── os_version ───────────────────────────────────────────────────────

def _os_catalog() -> dict[str, GlobalVariable]:
    return {
        "NAME": _gv("NAME", "os_version", {"field": "name", "segments": None, "uu_segments": None}),
        "REL": _gv("REL", "os_version", {"field": "name", "segments": 3, "uu_segments": 5}),
        "BR": _gv("BR", "os_version", {"field": "name", "segments": 2}),
        "NUM": _gv("NUM", "os_version", {"field": "rc_number"}),
        "UU": _gv("UU", "os_version", {"field": "is_urgent_update"}),
        "ALL": _gv("ALL", "template", {"template": "{NAME}|{REL}|{BR}|{UU}"}),
    }


async def _resolve_os(rc: str, *codes: str) -> list[str]:
    async with AsyncSessionLocal() as db:
        ctx = _ctx(db, launch_context={**CTX, "RC": rc})
        ctx._catalog = _os_catalog()
        return [(await vr.resolve_code(ctx, code)).value for code in codes]


class TestOsVersion:
    async def test_seeded_rc_name_is_the_version_name_not_id(self):
        async with AsyncSessionLocal() as db:
            assert (await vr.resolve_code(_ctx(db), "RC_NAME")).value == "1.8.1.6"

    async def test_regular_version_fields(self):
        assert await _resolve_os("osv_8a16c9e0", "NAME", "REL", "BR", "NUM", "UU") == [
            "1.8.1.6", "1.8.1", "1.8", "RC6", "false",
        ]

    async def test_urgent_update_uses_uu_segments(self):
        """Критерий: для `1.7.9.UU.1.2` релиз — `1.7.9.UU.1` (allta_back.py:356-359)."""
        assert await _resolve_os("osv_uu179120", "NAME", "REL", "BR", "UU") == [
            "1.7.9.UU.1.2", "1.7.9.UU.1", "1.7", "true",
        ]

    async def test_uu_marker_in_name_counts_without_flag(self):
        """Легаси знал UU только по имени — непроставленный флаг не меняет правила."""
        assert await _resolve_os("osv_uu_noflag", "REL", "UU") == ["1.7.3.UU.1", "true"]

    async def test_fewer_segments_than_asked_keeps_the_name(self):
        assert await _resolve_os("osv_short", "REL", "BR") == ["1.8", "1.8"]

    async def test_seeded_derived_variables(self):
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, launch_context={**CTX, "RC": "osv_uu179120"})
            values = [(await vr.resolve_code(ctx, c)).value for c in ("RC_NAME", "RC_RELEASE", "RC_BRANCH", "RC_NUMBER")]
        assert values == ["1.7.9.UU.1.2", "1.7.9.UU.1", "1.7", "RC2"]

    async def test_missing_rc_number_is_value_missing(self):
        with pytest.raises(DomainValidationError) as exc:
            await _resolve_os("osv_short", "NUM")
        assert exc.value.error_code == "VARIABLE_VALUE_MISSING"
        assert exc.value.details["field"] == "rc_number"

    async def test_card_is_fetched_once_per_context(self, os_version_calls):
        assert await _resolve_os("osv_8a16c9e0", "ALL", "NUM") == ["1.8.1.6|1.8.1|1.8|false", "RC6"]
        assert os_version_calls == ["osv_8a16c9e0"]

    async def test_missing_rc_is_launch_context_error(self, os_version_calls):
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, launch_context={"KERNEL": "k", "MODE": "orel"})
            ctx._catalog = _os_catalog()
            with pytest.raises(DomainValidationError) as exc:
                await vr.resolve_code(ctx, "REL")
        assert exc.value.error_code == "LAUNCH_CONTEXT_VARIABLE_MISSING"
        assert exc.value.details == {"code": "RC", "needed_by": "REL"}
        assert os_version_calls == []

    async def test_unknown_version_propagates(self):
        """id вместо имени наружу не подставляется — лучше видимый провал запуска."""
        with pytest.raises(NotFoundError):
            await _resolve_os("osv_deleted", "NAME")


class TestRenderArgs:
    async def test_tokens_keep_empty_positionals(self):
        async with AsyncSessionLocal() as db:
            ctx = _ctx(db, launch_context={**CTX, "A": "x y", "EMPTY": ""})
            ctx._catalog = {
                "A": _gv("A", "launch_context", None),
                "EMPTY": _gv("EMPTY", "launch_context", None),
                "RC_NAME": _gv("RC_NAME", "os_version", {"field": "name"}),
                "SECRET": _gv("SECRET", "static", {"value": "pw"}, sensitive=True),
            }
            args = await vr.render_args(ctx, "{A}  {RC_NAME}\t{EMPTY} lit-{SECRET}")
        assert [a.value for a in args] == ["x y", "1.8.1.6", "", "lit-pw"]
        assert [a.masked for a in args] == ["x y", "1.8.1.6", "", "***"]


# ── валидация при сохранении ─────────────────────────────────────────────────

class TestSaveValidation:
    async def _post(self, client, token, **fields):
        payload = {"code": _code(), "label": "x", "source": "launch_context", **fields}
        return await client.post(VARS_BASE, headers=_hdr(token), json=payload)

    async def test_unknown_code_in_template_is_422(self, client, admin_token):
        resp = await self._post(client, admin_token, source="template", source_ref={"template": "{NO_SUCH_VAR}_{MODE}"})
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_TEMPLATE_UNKNOWN"
        assert resp.json()["details"]["unknown"] == ["NO_SUCH_VAR"]

    async def test_self_reference_is_cycle(self, client, admin_token):
        code = _code()
        resp = await client.post(VARS_BASE, headers=_hdr(admin_token), json={
            "code": code, "label": "x", "source": "template", "source_ref": {"template": f"{{{code}}}"},
        })
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_TEMPLATE_CYCLE"

    async def test_indirect_cycle_on_update_is_422(self, client, admin_token):
        a = await _create_var(client, admin_token, source="template", source_ref={"template": "{MODE}"})
        b = await _create_var(client, admin_token, source="template", source_ref={"template": f"{{{a['code']}}}"})
        resp = await client.patch(
            f"{VARS_BASE}/{a['id']}", headers=_hdr(admin_token),
            json={"source_ref": {"template": f"x{{{b['code']}}}"}},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_TEMPLATE_CYCLE"
        assert resp.json()["details"]["cycle"] == [a["code"], b["code"], a["code"]]
        # Ничего не сохранилось.
        current = await client.get(f"{VARS_BASE}/{a['id']}", headers=_hdr(admin_token))
        assert current.json()["source_ref"] == {"template": "{MODE}"}

    @pytest.mark.parametrize("source,source_ref,sensitive", [
        ("launch_context", {"field": "x"}, False),
        ("template", None, False),
        ("template", {"template": "x", "extra": 1}, False),
        ("template", {"template": "x", "when": "sometimes"}, False),
        ("test_field", {"field": "owner_password"}, False),
        ("test_field", {"field": "short_name", "fallback": "nope"}, False),
        ("stand", {"field": "ip"}, False),
        ("department_integration", {"field": "id"}, False),
        ("department_integration", {"field": "credential_id"}, True),
        ("department_integration", {"field": "credential_id", "credential_part": "secret"}, False),
        ("department_integration", {"field": "jira_base_url", "credential_part": "login"}, False),
        ("os_version", {"field": "name", "segments": 0}, False),
        ("os_version", {"field": "codename"}, False),
        ("test_account", {"field": "ssh_key"}, False),
        ("static", {"value": 5}, False),
    ])
    async def test_source_ref_must_match_source(self, client, admin_token, source, source_ref, sensitive):
        resp = await self._post(client, admin_token, source=source, source_ref=source_ref, is_sensitive=sensitive)
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_SOURCE_REF_INVALID"

    async def test_valid_variable_round_trips_source_ref(self, client, admin_token):
        var = await _create_var(
            client, admin_token, source="department_integration", is_sensitive=True,
            source_ref={"field": "credential_id", "credential_part": "secret"},
        )
        assert var["source_ref"] == {"field": "credential_id", "credential_part": "secret"}

    async def test_dropping_is_sensitive_on_secret_reveal_is_422(self, client, admin_token):
        var = await _create_var(
            client, admin_token, source="department_integration", is_sensitive=True,
            source_ref={"field": "credential_id", "credential_part": "secret"},
        )
        resp = await client.patch(f"{VARS_BASE}/{var['id']}", headers=_hdr(admin_token), json={"is_sensitive": False})
        assert resp.status_code == 422, resp.text

    async def test_switching_source_requires_matching_ref(self, client, admin_token):
        var = await _create_var(client, admin_token, source="template", source_ref={"template": "{MODE}"})
        resp = await client.patch(f"{VARS_BASE}/{var['id']}", headers=_hdr(admin_token), json={"source": "launch_context"})
        assert resp.status_code == 422, resp.text
        ok = await client.patch(
            f"{VARS_BASE}/{var['id']}", headers=_hdr(admin_token), json={"source": "launch_context", "source_ref": None},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["source_ref"] is None

    async def test_referenced_variable_cannot_be_deleted_or_renamed(self, client, admin_token):
        base = await _create_var(client, admin_token)
        await _create_var(client, admin_token, source="template", source_ref={"template": f"{{{base['code']}}}"})
        resp = await client.delete(f"{VARS_BASE}/{base['id']}", headers=_hdr(admin_token))
        assert resp.status_code == 409, resp.text
        assert resp.json()["error_code"] == "GLOBAL_VARIABLE_IN_USE"
        resp = await client.patch(f"{VARS_BASE}/{base['id']}", headers=_hdr(admin_token), json={"code": _code()})
        assert resp.status_code == 409, resp.text

    async def test_variable_referenced_by_slot_override_cannot_be_deleted(self, client, admin_token):
        base = await _create_var(client, admin_token)
        test_id = await _create_test(client, admin_token)
        testenv = await client.get(f"{VARS_BASE}/by-code/TESTENV", headers=_hdr(admin_token))
        await _add_slot(client, admin_token, test_id, testenv.json()["id"], override_value=f"{{{base['code']}}}")
        resp = await client.delete(f"{VARS_BASE}/{base['id']}", headers=_hdr(admin_token))
        assert resp.status_code == 409, resp.text
        assert resp.json()["details"]["referenced_by_tests"] == [test_id]

    async def test_slot_override_with_unknown_code_is_422(self, client, admin_token):
        test_id = await _create_test(client, admin_token)
        testenv = await client.get(f"{VARS_BASE}/by-code/TESTENV", headers=_hdr(admin_token))
        resp = await client.post(
            f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": testenv.json()["id"], "override_value": "{NOPE_NOPE}"},
        )
        assert resp.status_code == 422, resp.text
        assert resp.json()["error_code"] == "VARIABLE_TEMPLATE_UNKNOWN"


# ── миграция данных ────────────────────────────────────────────────────

def _load_rc_name_migration():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "src" / "db" / "migrations" / "versions" / "89394402c071_rc_name_variables.py"
    )
    spec = importlib.util.spec_from_file_location("rc_name_variables", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestTp05SlotMigration:
    async def test_only_tcv_and_vbox_slots_switch_to_rc_name(self, client, admin_token):
        """Существующие слоты `-tcv RC`/`-vbox RC` переезжают на `RC_NAME`, прочие `RC` — нет."""
        from sqlalchemy import create_engine, text

        from tests.conftest import TEST_DATABASE_URL

        test_id = await _create_test(client, admin_token)
        rc = (await client.get(f"{VARS_BASE}/by-code/RC", headers=_hdr(admin_token))).json()
        rc_name = (await client.get(f"{VARS_BASE}/by-code/RC_NAME", headers=_hdr(admin_token))).json()
        slot_ids = []
        for flag in ("-tcv", "-rs", "-vbox"):
            resp = await client.post(
                f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
                json={"kind": "literal", "literal_value": flag},
            )
            assert resp.status_code == 201, resp.text
            slot_ids.append((await _add_slot(client, admin_token, test_id, rc["id"]))["id"])

        migration = _load_rc_name_migration()
        engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")

        def variables() -> list[str]:
            with engine.connect() as conn:
                rows = conn.execute(
                    text("SELECT id, variable_id FROM test_command_args WHERE test_id = :t"), {"t": test_id},
                ).all()
            by_id = dict(rows)
            return [by_id[slot_id] for slot_id in slot_ids]

        try:
            with engine.connect() as conn:
                migration._switch_slots(conn, "RC", "RC_NAME")
            assert variables() == [rc_name["id"], rc["id"], rc_name["id"]]
            with engine.connect() as conn:
                migration._switch_slots(conn, "RC_NAME", "RC")
            assert variables() == [rc["id"], rc["id"], rc["id"]]
        finally:
            engine.dispose()
