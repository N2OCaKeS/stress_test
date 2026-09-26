"""Превью запуска `POST /test-definitions/{id}/launch-preview`.

Главное свойство — превью совпадает с настоящим заданием воркеру: тест ниже
проводит item через очередь до claim и сравнивает файлы и команды с превью
для тех же стенда, РЦ, ядра и режима (с поправкой на id item'а в путях и на
маску секретов). Остальные тесты — маскировка, частичный результат при
ошибке этапа, права, отсутствие записей в БД.
"""

from __future__ import annotations

import json
import shlex

from sqlalchemy import func, select

from src.db.session import AsyncSessionLocal
from src.models import QueueItem
from src.services import queue as queue_svc
from src.services.launch_preview import PREVIEW_QUEUE_ITEM_ID
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    CALLBACK_BASE,
    LAUNCH_CTX,
    OS_VERSION_NAME,
    QUEUE_BASE,
    SERVER_SECRET,
    TESTS_BASE,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _identity,
    _server_hdr,
    configure_internal_keys,  # noqa: F401 — фикстура
    mock_git_token,  # noqa: F401 — фикстура
    mock_server_service,  # noqa: F401 — фикстура
    recorded_calls,  # noqa: F401 — зависимость mock_server_service
)

GIT_TOKEN = "git-token-value"  # из `mock_git_token`
PASSWORD_OVERRIDE = "s3cr3t"   # override_value слота TEST_PASSWORD в `_create_test_def`


def _body(stand_id: str, **over) -> dict:
    body = {"stand_id": stand_id, "os_version_id": LAUNCH_CTX["RC"], "kernel": LAUNCH_CTX["KERNEL"]}
    body.update(over)
    return body


async def _preview(client, token, test_id, body):
    return await client.post(f"{TESTS_BASE}/{test_id}/launch-preview", headers=_hdr(token), json=body)


async def _queue_count() -> int:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(func.count()).select_from(QueueItem))).scalar_one()


class TestPreviewMatchesClaim:
    async def test_files_and_commands_are_the_claim_job(
        self, client, admin_token, guest_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id, with_sensitive_arg=True)

        resp = await _preview(client, guest_token, test_id, _body(stand_id))
        assert resp.status_code == 200, resp.text
        preview = resp.json()
        assert preview["errors"] == []

        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": True, "test_username": "u",
                  "test_password": "p", "test_ssh_private_key": "-----KEY-----"},
        )
        claim = (await client.post(
            f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET),
        )).json()["item"]

        def as_preview(text: str) -> str:
            return text.replace(item.id, PREVIEW_QUEUE_ITEM_ID)

        assert preview["launch_command_masked"] == as_preview(claim["launch_command_masked"])
        assert preview["stop_command"] == as_preview(claim["stop_command"])
        assert preview["use_pty"] == claim["use_pty"]
        assert preview["cleanup_globs"] == claim["cleanup_globs"]
        assert preview["launch_profile"]["version_id"] == claim["launch_profile_version_id"]

        claim_files = claim["files"]
        assert [f["path"] for f in preview["files"]] == [as_preview(f["path"]) for f in claim_files]
        assert [f["mode"] for f in preview["files"]] == [f["mode"] for f in claim_files]
        assert [f["sensitive"] for f in preview["files"]] == [f["sensitive"] for f in claim_files]
        by_role = {f["role"]: f for f in preview["files"]}
        assert set(by_role) == {"script", "token", "dates", "testenv_marker"}
        claim_by_name = {f["path"].rsplit("/", 1)[1].split("_")[0]: f for f in claim_files}
        # starter.sh и маркер — без секретов: содержимое то же самое.
        assert by_role["script"]["content"] == as_preview(claim_by_name["starter.sh"]["content"])
        assert by_role["testenv_marker"]["content"] == claim_by_name["testenv"]["content"] == "off"
        # Токен и секреты dates замаскированы, прочие токены dates — как у claim.
        assert by_role["token"]["content"] == "***"
        assert shlex.split(claim_by_name["dates"]["content"]) == ["--run", PASSWORD_OVERRIDE]
        assert by_role["dates"]["content"] == preview["dates_content_masked"] == "--run '***'"

        # $2/$3 у starter.sh — имена файлов из путей профиля, $4 — имя версии.
        argv = shlex.split(preview["launch_command_masked"])
        assert argv[4:7] == [f"git_token_{PREVIEW_QUEUE_ITEM_ID}.conf",
                             f"dates_{PREVIEW_QUEUE_ITEM_ID}.conf", OS_VERSION_NAME]

    async def test_secrets_never_leave_in_the_response(
        self, client, admin_token, guest_token, mock_server_service, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id, with_sensitive_arg=True)

        resp = await _preview(client, guest_token, test_id, _body(stand_id))
        assert resp.status_code == 200, resp.text
        raw = resp.text
        assert GIT_TOKEN not in raw
        assert PASSWORD_OVERRIDE not in raw
        rows = {row["code"]: row for row in resp.json()["variables"]}
        # Слот TEST_PASSWORD с override_value: значение — шаблон слота,
        # маскируется по переменной слота.
        assert rows["TEST_PASSWORD"] == {
            "code": "TEST_PASSWORD", "label": rows["TEST_PASSWORD"]["label"], "source": "override",
            "value": "***", "sensitive": True, "slot_position": 1,
        }
        # Значения задания — отдельный источник, пути профиля видны.
        assert rows["QUEUE_ITEM_ID"] == {
            "code": "QUEUE_ITEM_ID", "label": None, "source": "claim",
            "value": PREVIEW_QUEUE_ITEM_ID, "sensitive": False, "slot_position": None,
        }
        assert rows["STARTER_PATH"]["value"] == "/home/u/starter.sh"
        assert rows["RC_NAME"]["value"] == OS_VERSION_NAME
        assert rows["RC_NAME"]["source"] == "os_version"


class TestPreviewOptions:
    async def test_testenv_writes_marker_on_and_command_file(
        self, client, admin_token, guest_token, mock_server_service, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        preview = (await _preview(client, guest_token, test_id, _body(stand_id, testenv=True))).json()
        by_role = {f["role"]: f for f in preview["files"]}
        assert by_role["testenv_marker"]["content"] == "on"
        assert by_role["command_file"]["content"] == preview["launch_command_masked"] + "\n"

    async def test_mode_defaults_to_test_mode_and_can_be_overridden(
        self, client, admin_token, guest_token, mock_server_service, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id, mode="smolensk")

        preview = (await _preview(client, guest_token, test_id, _body(stand_id))).json()
        assert preview["launch_context"] == {"RC": LAUNCH_CTX["RC"], "KERNEL": LAUNCH_CTX["KERNEL"], "MODE": "smolensk"}
        preview = (await _preview(client, guest_token, test_id, _body(stand_id, mode="orel"))).json()
        assert preview["launch_context"]["MODE"] == "orel"

    async def test_nothing_is_enqueued_or_written(
        self, client, admin_token, guest_token, mock_server_service, mock_git_token, recorded_calls,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        before = await _queue_count()
        recorded_calls.clear()

        resp = await _preview(client, guest_token, test_id, _body(stand_id))
        assert resp.status_code == 200, resp.text
        assert await _queue_count() == before
        # Ни брони, ни service-status, ни prepare-for-test.
        assert not [p for _, p in recorded_calls if "acquire" in p or "service-status" in p or "prepare" in p]


class TestPartialPreview:
    async def test_failed_stage_is_reported_and_the_rest_is_built(
        self, client, admin_token, guest_token, mock_server_service,
    ):
        """Без git-credential отдела claim провалил бы item — превью показывает
        причину и всё остальное задание."""
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        resp = await _preview(client, guest_token, test_id, _body(stand_id))
        assert resp.status_code == 200, resp.text
        preview = resp.json()
        assert [(e["stage"], e["error_code"]) for e in preview["errors"]] == [
            ("git_token", "GIT_CREDENTIAL_NOT_CONFIGURED"),
        ]
        by_role = {f["role"]: f for f in preview["files"]}
        assert by_role["token"]["content"] is None
        assert by_role["dates"]["content"] == "--run"
        assert preview["launch_command_masked"].startswith("sudo bash /home/u/starter.sh ")

    async def test_unresolvable_dates_keeps_commands(
        self, client, admin_token, guest_token, mock_server_service, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        variable = (await client.get(
            "/api/testing/v1/global-variables/by-code/FOLDER_TREE_ID", headers=_hdr(admin_token),
        )).json()
        await client.post(
            f"{TESTS_BASE}/{test_id}/args", headers=_hdr(admin_token),
            json={"kind": "variable", "variable_id": variable["id"]},
        )

        preview = (await _preview(client, guest_token, test_id, _body(stand_id))).json()
        [error] = preview["errors"]
        assert error["stage"] == "dates"
        assert error["error_code"] == "VARIABLE_VALUE_MISSING"
        assert preview["dates_content_masked"] is None
        assert {f["role"]: f for f in preview["files"]}["dates"]["content"] is None
        assert preview["launch_command_masked"]


class TestPreviewAccess:
    async def test_foreign_department_stand_is_403(
        self, client, admin_token, make_token, mock_server_service, mock_git_token,
    ):
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        other = make_token(department_id="dep_other", service_roles={"testing_service": ["guest"]})

        resp = await _preview(client, other, test_id, _body(stand_id))
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "DEPARTMENT_ISOLATION"

    async def test_unknown_test_and_stand_are_404(self, client, admin_token, guest_token, mock_server_service):
        mock_server_service()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)

        resp = await _preview(client, guest_token, "tdef_missing", _body(stand_id))
        assert resp.status_code == 404
        resp = await _preview(client, guest_token, test_id, _body("tst_missing"))
        assert resp.status_code == 404
        assert resp.json()["error_code"] == "TEST_STAND_NOT_FOUND"

    async def test_preview_is_audited_without_values(
        self, client, admin_token, guest_token, mock_server_service, mock_git_token, monkeypatch,
    ):
        from src.services import audit_service

        events = []
        monkeypatch.setattr(audit_service, "emit", lambda action, **kw: events.append((action, kw)))
        mock_server_service()
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id, with_sensitive_arg=True)

        await _preview(client, guest_token, test_id, _body(stand_id))
        [(_, event)] = [e for e in events if e[0] == "test_definition.launch_preview"]
        assert event["target_id"] == test_id
        assert event["details"]["stand_id"] == stand_id
        assert event["details"]["errors"] == []
        assert PASSWORD_OVERRIDE not in json.dumps(event)


class TestPreviewStandSetup:
    """шаг настройки стенда и профиль подготовки в превью."""

    async def test_setup_script_resolved_and_masked(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        resp = await client.patch(
            f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token),
            json={"stand_setup": {
                "kernel_cmdline_extra": ["audit=0"],
                "script": "echo {{TEST_USER}} {{TEST_PASSWORD}}\n",
            }},
        )
        assert resp.status_code == 200, resp.text

        body = (await _preview(client, admin_token, test_id, _body(stand_id))).json()
        setup = body["stand_setup"]
        assert setup["kernel_cmdline_extra"] == ["audit=0"]
        assert setup["script"] == "echo u ***\n"
        assert setup["script_is_sensitive"] is True
        assert "default-test-password" not in str(body)
        assert body["provisioning"]["allowed_failed_units"] == ["astra-mount-lock.service"]

    async def test_unknown_variable_is_a_stage_error(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        await client.patch(
            f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token),
            json={"stand_setup": {"script": "echo {{NO_SUCH_VAR}}\n"}},
        )
        body = (await _preview(client, admin_token, test_id, _body(stand_id))).json()
        assert body["stand_setup"] is None
        assert [e["stage"] for e in body["errors"]] == ["stand_setup"]
