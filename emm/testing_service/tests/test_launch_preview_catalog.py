""", критерий приёмки: превью теста настоящего каталога allta_app.

`file_systems.xfs` на стенде `stand3`, РЦ `1.8.1.6`: в `dates.conf`
`--confluence-new-page` — короткое имя теста и имя версии,
значение экранировано по `dates_quoting=shell`; учётные данные из
интеграций отдела замаскированы.
"""

from __future__ import annotations

import shlex

from src.db.session import AsyncSessionLocal
from src.repositories import department_integration_settings as dis_repo
from src.repositories import test_definition as test_definition_repo
from tests.conftest import auth_hdr as _hdr
from tests.test_launch_context import (  # noqa: F401 — фикстуры каталога
    CATALOG_FOLDER_TREE_ID,
    imported_allta_catalog,
    integration_for_catalog,
    mock_server_service,
    os_version_catalog,
    zephyr_folder_for_catalog,
)

TESTS_BASE = "/api/testing/v1/test-definitions"
KERNEL = "6.1.90-1-generic"
GIT_HEADER = "Bearer git-header-secret"


async def _with_git_credential(creds: dict) -> None:
    """git-токен для starter.sh — иначе превью покажет `GIT_CREDENTIAL_NOT_CONFIGURED`."""
    creds["cred_git"] = ("git-bot", GIT_HEADER)
    async with AsyncSessionLocal() as db:
        settings = await dis_repo.get_by_department(db, "dep_a")
        await dis_repo.update(db, settings, {"git_credential_id": "cred_git"})
        await db.commit()


class TestXfsPreview:
    async def test_confluence_new_page_is_short_name_and_version_name(
        self, client, guest_token, imported_allta_catalog, integration_for_catalog,
    ):
        await _with_git_credential(integration_for_catalog)
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, "file_systems.xfs")

        resp = await client.post(
            f"{TESTS_BASE}/{test.id}/launch-preview", headers=_hdr(guest_token),
            json={"stand_id": imported_allta_catalog.id, "os_version_id": "osv_8a16c9e0", "kernel": KERNEL},
        )
        assert resp.status_code == 200, resp.text
        preview = resp.json()
        assert preview["errors"] == []

        page = f"XFS_1.8.1.6_orel_{KERNEL}_stand3"
        dates = preview["dates_content_masked"]
        assert f"--confluence-new-page {shlex.quote(page)}" in dates
        argv = shlex.split(dates)
        assert argv[argv.index("--confluence-new-page") + 1] == page
        assert argv[argv.index("-fti") + 1] == CATALOG_FOLDER_TREE_ID
        # Учётные данные отдела: логин виден, токены — нет.
        assert argv[argv.index("--username") + 1] == "conf-bot"
        assert argv[argv.index("--token") + 1] == "***"
        for secret in ("conf-token", "jira basic auth", GIT_HEADER):
            assert secret not in resp.text

        rows = {row["code"]: row for row in preview["variables"]}
        assert rows["CONFLUENCE_NEW_PAGE"]["value"] == page
        assert rows["CONFLUENCE_NEW_PAGE"]["source"] == "template"
        assert rows["TEST_SHORT_NAME"]["value"] == "XFS"
        assert rows["CONFLUENCE_TOKEN"] == {
            "code": "CONFLUENCE_TOKEN", "label": rows["CONFLUENCE_TOKEN"]["label"],
            "source": "department_integration", "value": "***", "sensitive": True, "slot_position": None,
        }

        # starter.sh: ветка теста, имена файлов, имя версии.
        argv = shlex.split(preview["launch_command_masked"])
        assert argv[:3] == ["sudo", "bash", "/home/u/starter.sh"]
        assert argv[3:7] == ["file_systems", "git_token_qi_preview.conf", "dates_qi_preview.conf", "1.8.1.6"]
        files = {f["role"]: f for f in preview["files"]}
        assert files["dates"]["content"] == dates and files["dates"]["sensitive"] is True
        assert files["token"]["content"] == "***"

    async def test_value_with_space_is_quoted(
        self, client, guest_token, imported_allta_catalog, integration_for_catalog,
    ):
        """`shlex.quote` берёт в кавычки только небезопасные токены: у
        `XFS parsec` в имени пробел — страница Confluence уходит одним аргументом."""
        await _with_git_credential(integration_for_catalog)
        async with AsyncSessionLocal() as db:
            test = await test_definition_repo.get_by_code(db, "file_systems.xfs_parsec")

        preview = (await client.post(
            f"{TESTS_BASE}/{test.id}/launch-preview", headers=_hdr(guest_token),
            json={"stand_id": imported_allta_catalog.id, "os_version_id": "osv_8a16c9e0", "kernel": KERNEL},
        )).json()
        assert preview["errors"] == []
        assert f"--confluence-new-page 'XFS parsec_1.8.1.6_orel_{KERNEL}_stand3'" in preview["dates_content_masked"]
