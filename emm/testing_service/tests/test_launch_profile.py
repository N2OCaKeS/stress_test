"""Профили запуска — API, версии, сборка задания, остановка дерева процессов."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import textwrap

import pytest

from src.db.session import AsyncSessionLocal
from src.repositories import launch_profile as lp_repo
from src.services import launch_profile as lp_svc
from src.services import queue as queue_svc
from src.services import variable_resolver
from tests.conftest import auth_hdr as _hdr
from tests.test_queue import (
    CALLBACK_BASE,
    LAUNCH_CTX,
    QUEUE_BASE,
    SERVER_SECRET,
    TESTS_BASE,
    WORKER_SECRET,
    _create_stand,
    _create_test_def,
    _identity,
    _server_hdr,
    claim_file,
    configure_internal_keys,  # noqa: F401 — фикстура
    mock_git_token,  # noqa: F401 — фикстура
    mock_server_service,  # noqa: F401 — фикстура
    recorded_calls,  # noqa: F401 — зависимость mock_server_service
)

BASE = "/api/testing/v1/launch-profiles"
LEGACY_STARTER = os.path.join(os.path.dirname(__file__), "..", "..", "allta_app_full", "starter.sh")


def _version_body(**over) -> dict:
    body = {
        "starter_script": "#!/bin/bash\necho hi {{GIT_REPO_URL}}\n",
        "clone": {"repo_url": "https://git.example/repo.git", "mode": "branch", "depth": None},
        "launch_command_template": "sudo bash {STARTER_PATH} {TEST_BRANCH}",
        "stop_command_template": "sudo pkill -f {{STARTER_PGREP_PATTERN}}",
        "stop_grace_seconds": 5,
        "use_pty": True,
    }
    body.update(over)
    return body


@pytest.fixture
def dept_admin_token(make_token, dept_a) -> str:
    return make_token(department_id=dept_a, platform_role="department_admin")


class TestDefaultProfileSeed:
    async def test_global_default_is_legacy_starter_except_the_token_line(self):
        async with AsyncSessionLocal() as db:
            profile = await lp_repo.get_default(db, None)
            version = await lp_repo.get_version(db, profile.current_version_id)
        legacy = open(LEGACY_STARTER, encoding="utf-8").read().splitlines()
        seeded = version.starter_script.splitlines()
        assert len(legacy) == len(seeded)
        diff = [(a, b) for a, b in zip(legacy, seeded) if a != b]
        # Строка клонирования и адреса infocollector/registry,
        # вынесенные в переменные.
        assert len(diff) == 3
        old, new = diff[0]
        assert 'Authorization: $2' in old
        assert '$(cat "{{GIT_TOKEN_PATH}}")' in new and "{{GIT_CLONE_ARGS}}" in new
        registry_old, registry_new = diff[1]
        assert "allta.devos.astralinux.ru:21503" in registry_old
        assert registry_new == registry_old.replace("allta.devos.astralinux.ru:21503", "{{DOCKER_REGISTRY}}")
        info_old, info_new = diff[2]
        assert info_new == info_old.replace("http://10.177.103.10:18181", "{{INFOCOLLECTOR_URL}}")
        assert "10.177.103.10" not in version.starter_script
        assert version.id == "lpv_default_2" and version.version == 2
        assert version.use_pty is True and version.stop_grace_seconds == 10
        assert version.testenv == {"on_value": "on", "off_value": "off", "cleanup_other": False}
        assert version.launch_command_template == (
            "sudo bash {STARTER_PATH} {TEST_BRANCH} {GIT_TOKEN_FILE} {DATES_FILE} {RC_NAME} {STARTER_SUFFIX}"
        )

    def test_default_clone_args_render_to_legacy(self):
        assert lp_svc.clone_args({"mode": "branch", "depth": None}) == '--branch "$1" --single-branch'
        assert lp_svc.clone_args({"mode": "branch", "depth": 1}) == '--branch "$1" --single-branch --depth 1'
        assert lp_svc.clone_args({"mode": "full", "depth": None}) == '--branch "$1"'

    def test_pgrep_pattern_does_not_match_itself(self):
        pattern = lp_svc.pgrep_pattern("/home/u/starter.sh")
        assert pattern == "[/]home/u/starter\\.sh"


class TestClaimRendersProfile:
    async def test_script_content_is_legacy_with_token_file(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": True, "test_username": "u",
                  "test_password": "p", "test_ssh_private_key": "-----KEY-----"},
        )
        resp = await client.post(f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET))
        payload = resp.json()["item"]
        script = claim_file(payload, "starter.sh")
        assert script["mode"] == "0755"
        legacy = open(LEGACY_STARTER, encoding="utf-8").read()
        expected = legacy.replace(
            'git -c http.extraHeader="Authorization: $2" clone --branch "$1" --single-branch '
            "https://git.astralinux.ru/scm/qa/stress_test.git",
            f'git -c http.extraHeader="Authorization: $(cat "/home/u/git_token_{item.id}.conf")" clone '
            '--branch "$1" --single-branch https://git.astralinux.ru/scm/qa/stress_test.git; '
            f'rm -f "/home/u/git_token_{item.id}.conf"',
        )
        assert script["content"] == expected


class TestLaunchProfileApi:
    async def test_create_version_and_history(self, client, dept_admin_token, dept_a):
        resp = await client.post(BASE, headers=_hdr(dept_admin_token), json={
            "department_id": dept_a, "name": "Отдел A", "is_default": True, "version": _version_body(),
        })
        assert resp.status_code == 201, resp.text
        profile = resp.json()
        assert profile["current_version"]["version"] == 1
        first_version_id = profile["current_version"]["id"]

        resp = await client.post(
            f"{BASE}/{profile['id']}/versions", headers=_hdr(dept_admin_token),
            json=_version_body(starter_script="#!/bin/bash\necho v2\n", comment="v2"),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["version"] == 2

        history = (await client.get(f"{BASE}/{profile['id']}/versions", headers=_hdr(dept_admin_token))).json()
        assert [v["version"] for v in history] == [2, 1]
        # Старая версия не изменилась.
        assert history[1]["id"] == first_version_id
        assert history[1]["starter_script"] == "#!/bin/bash\necho hi {{GIT_REPO_URL}}\n"

        listed = (await client.get(BASE, headers=_hdr(dept_admin_token), params={"department_id": dept_a})).json()
        names = {p["name"]: p for p in listed["items"]}
        assert names["Отдел A"]["is_default"] is True
        assert names["Легаси starter.sh"]["department_id"] is None

    async def test_department_admin_cannot_edit_global_profile(self, client, dept_admin_token):
        resp = await client.post(f"{BASE}/lp_default/versions", headers=_hdr(dept_admin_token), json=_version_body())
        assert resp.status_code == 403

    async def test_guest_cannot_create(self, client, guest_token, dept_a):
        resp = await client.post(BASE, headers=_hdr(guest_token), json={
            "department_id": dept_a, "name": "x", "version": _version_body(),
        })
        assert resp.status_code == 403

    async def test_unsafe_path_template_rejected(self, client, dept_admin_token, dept_a):
        resp = await client.post(BASE, headers=_hdr(dept_admin_token), json={
            "department_id": dept_a, "name": "x",
            "version": _version_body(paths={"script": "/home/u/start er.sh"}),
        })
        assert resp.status_code == 422

    async def test_test_uses_its_profile_and_running_item_keeps_version(
        self, client, admin_token, dept_admin_token, dept_a,
        mock_server_service, configure_internal_keys, mock_git_token,
    ):
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        created = (await client.post(BASE, headers=_hdr(dept_admin_token), json={
            "department_id": dept_a, "name": "Свой", "version": _version_body(),
        })).json()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        await client.patch(f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token), json={"department_id": dept_a})
        patched = await client.patch(
            f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token), json={"launch_profile_id": created["id"]},
        )
        assert patched.status_code == 200, patched.text
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(db, _identity(), test_id, launch_context=LAUNCH_CTX)
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": True, "test_username": "u",
                  "test_password": "p", "test_ssh_private_key": "-----KEY-----"},
        )
        payload = (await client.post(
            f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET),
        )).json()["item"]
        assert payload["launch_command"] == "sudo bash /home/u/starter.sh ''"
        assert claim_file(payload, "starter.sh")["content"] == "#!/bin/bash\necho hi https://git.example/repo.git\n"
        assert payload["stop_command"] == "sudo pkill -f [/]home/u/starter\\.sh"
        v1 = created["current_version"]["id"]
        assert payload["launch_profile_version_id"] == v1

        # Новая версия не трогает уже запущенный item.
        await client.post(
            f"{BASE}/{created['id']}/versions", headers=_hdr(dept_admin_token),
            json=_version_body(starter_script="#!/bin/bash\necho v2\n"),
        )
        from tests.test_queue import _get_item

        assert (await _get_item(item.id)).launch_profile_version_id == v1

    async def test_foreign_department_profile_rejected_on_test(
        self, client, admin_token, make_token, mock_server_service,
    ):
        mock_server_service()
        other_admin = make_token(department_id="dep_other", platform_role="department_admin")
        foreign = (await client.post(BASE, headers=_hdr(other_admin), json={
            "department_id": "dep_other", "name": "Чужой", "version": _version_body(),
        })).json()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        resp = await client.patch(
            f"{TESTS_BASE}/{test_id}", headers=_hdr(admin_token), json={"launch_profile_id": foreign["id"]},
        )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "LAUNCH_PROFILE_INVALID"


class TestTestenvInCampaign:
    async def test_retry_gets_off_even_with_prepare_only(
        self, client, admin_token, mock_server_service, configure_internal_keys, mock_git_token,
    ):
        """T2: `on` только в одиночном запуске; повтор — всегда `off`."""
        mock_server_service(host="10.9.9.9")
        await mock_git_token()
        stand_id, _ = await _create_stand(client, admin_token)
        test_id = await _create_test_def(client, admin_token, stand_id)
        async with AsyncSessionLocal() as db:
            item = await queue_svc.enqueue(
                db, _identity(), test_id, launch_context=LAUNCH_CTX, prepare_only=True,
            )
            from src.repositories import queue_item as qrepo

            loaded = await qrepo.get_by_id(db, item.id)
            loaded.is_retry = True
            await db.commit()
        await client.post(
            f"{CALLBACK_BASE}/{item.prepare_request_id}/completed",
            headers=_server_hdr("server_service", SERVER_SECRET),
            json={"correlation_id": item.id, "succeeded": True, "test_username": "u",
                  "test_password": "p", "test_ssh_private_key": "-----KEY-----"},
        )
        payload = (await client.post(
            f"{QUEUE_BASE}/claim", headers=_server_hdr("testing_worker", WORKER_SECRET),
        )).json()["item"]
        assert claim_file(payload, "testenv_")["content"] == "off"


class TestStopCommandKillsWholeTree:
    """T1: дефолтные launch/stop профиля, исполненные через bash на этой машине."""

    @staticmethod
    def _tree(root_pid: int) -> set[int]:
        out = subprocess.run(["ps", "-eo", "pid=,ppid="], capture_output=True, text=True).stdout
        children: dict[int, list[int]] = {}
        for line in out.splitlines():
            pid, ppid = map(int, line.split())
            children.setdefault(ppid, []).append(pid)
        found, stack = set(), [root_pid]
        while stack:
            pid = stack.pop()
            for child in children.get(pid, []):
                if child not in found:
                    found.add(child)
                    stack.append(child)
        return found

    @staticmethod
    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        stat = open(f"/proc/{pid}/stat").read()
        return stat.split(")")[1].split()[0] != "Z"

    async def test_no_process_of_the_tree_survives(self, tmp_path):
        if subprocess.run(["sudo", "-n", "true"]).returncode != 0:
            pytest.skip("passwordless sudo is not available")
        run_sh = tmp_path / "run.sh"
        run_sh.write_text(textwrap.dedent("""\
            python3 -c 'import time; time.sleep(300)' &
            setsid sleep 301 &
            sleep 302
        """))
        starter = tmp_path / "starter.sh"
        starter.write_text(f"bash {run_sh}\n")

        async with AsyncSessionLocal() as db:
            profile = await lp_repo.get_default(db, None)
            version = await lp_repo.get_version(db, profile.current_version_id)
            ctx = variable_resolver.ResolveContext(
                db=db, department_id="dep_a", test=None, stand=None, launch_context={},
                locals={
                    "STARTER_PATH": str(starter), "TEST_BRANCH": "b", "GIT_TOKEN_FILE": "t",
                    "DATES_FILE": "d", "RC_NAME": "1.8", "STARTER_SUFFIX": "",
                },
            )
            launch = shlex.join(a.value for a in await variable_resolver.render_args(ctx, version.launch_command_template))
            ctx.locals.update({
                "STARTER_PGREP_PATTERN": lp_svc.pgrep_pattern(str(starter)),
                "STOP_GRACE_SECONDS": "2",
            })
            stop = (await lp_svc.render_shell(ctx, version.stop_command_template)).value

        proc = subprocess.Popen(["bash", "-c", launch], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            tree: set[int] = set()
            for _ in range(50):
                tree = self._tree(proc.pid)
                if len(tree) >= 5:  # sudo→bash starter→bash run→python, setsid sleep, sleep
                    break
                await asyncio.sleep(0.1)
            assert len(tree) >= 5, tree
            setsid_pids = [p for p in tree if os.getsid(p) == p]
            assert setsid_pids, "the tree must contain a process that called setsid"

            result = subprocess.run(["bash", "-c", stop], timeout=60)
            assert result.returncode == 0
            await asyncio.sleep(0.3)
            survivors = [p for p in tree if self._alive(p)]
            assert survivors == []
        finally:
            proc.kill()
            proc.wait()
