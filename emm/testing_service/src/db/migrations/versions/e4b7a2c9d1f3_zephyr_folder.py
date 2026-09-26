"""zephyr folder: path/run-name templates, zephyr_folders, FOLDER_TREE_ID source

Revision ID: e4b7a2c9d1f3
Revises: 89394402c071
Create Date: 2026-09-24 14:00:00.000000

До путь папки прогонов СТП и имя прогона были зашиты в
`services/stp.py` (`folder = f"/stress_test/{release}/{rc_number}"`,
`name = f"{rc_number}_{mode}_{kernel}_{stand_token}"`), а `-fti` уходил
скриптам как `none`. Теперь:

* `department_integration_settings.zephyr_folder_path_template` =
  `/stress_test/{RC_RELEASE}/{RC_NAME}` — легаси-путь папки:
  `emm/allta_app_full/libs/liballta.py:2122-2130` (`"name":
  f"/stress_test/{name}"`, `name` = `<release>/<rc>`, :2150-2166), глубина
  `release` — 3 сегмента, для UU — 5 (переменная `RC_RELEASE`, её заводит
  );
* `department_integration_settings.zephyr_run_name_template` =
  `{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}` — легаси `-tcyc`
  (`emm/allta_app_full/allta_back.py:175`:
  `f'-tcyc {release}_{mode}_{kernel}_{stand}'`), та же формула, что у
  `TEST_CYCLE_NAME` (сид `d6f2b8c4e1a9`);
* таблица `zephyr_folders` (отдел × версия ОС → `folder_tree_id`, признак
  ручной правки) — замена словаря `cycle_tree_index`, который наполнял бот
  (`emm/allta_app_full/libs/liballta.py:2141`,
  `emm/allta_app_full/allta_back.py:523,540`: `cti=cycle_tree_index()[rc]`);
* `FOLDER_TREE_ID` получает источник `zephyr_folder`
  (`{"field": "folder_tree_id"}`) — легаси `-fti {args.CTI}`
  (`emm/allta_app_full/backup_image.py:305`). Только если переменную не
  трогали руками (`source='launch_context' AND source_ref IS NULL`, тот же
  приём, что в `d6f2b8c4e1a9`). `override_value: "none"` у `-fti` уже снят
   (`f8b3d1e6a254`).

Существующим строкам настроек шаблоны проставляет `server_default` колонок.
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e4b7a2c9d1f3"
down_revision: Union[str, None] = "89394402c071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FOLDER_PATH_TEMPLATE = "/stress_test/{RC_RELEASE}/{RC_NAME}"
_RUN_NAME_TEMPLATE = "{RC_NAME}_{MODE}_{KERNEL}_{STAND_TOKEN}"

_FTI_REF = {"field": "folder_tree_id"}
_FTI_DESCRIPTION = (
    "Легаси-флаг -fti (backup_image.py:305): id папки Zephyr прогонов РЦ. "
    "Берётся из zephyr_folders по отделу стенда и версии ОС — запись заводит "
    "генерация СТП или ручная правка на странице СТП."
)
_FTI_PREVIOUS_DESCRIPTION = (
    "Легаси-флаг -cti/-fti (cycle tree index). Задаётся вызывающим при постановке в очередь."
)


def upgrade() -> None:
    op.add_column(
        "department_integration_settings",
        sa.Column(
            "zephyr_folder_path_template", sa.String(length=512), nullable=False,
            server_default=_FOLDER_PATH_TEMPLATE,
        ),
    )
    op.add_column(
        "department_integration_settings",
        sa.Column(
            "zephyr_run_name_template", sa.String(length=512), nullable=False,
            server_default=_RUN_NAME_TEMPLATE,
        ),
    )

    op.create_table(
        "zephyr_folders",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("department_id", sa.String(length=64), nullable=False),
        sa.Column("os_version_id", sa.String(length=64), nullable=False),
        sa.Column("folder_path", sa.String(length=512), nullable=False),
        sa.Column("folder_tree_id", sa.String(length=64), nullable=True),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("department_id", "os_version_id", name="uq_zephyr_folders_dept_os_version"),
    )
    op.create_index("ix_zephyr_folders_department_id", "zephyr_folders", ["department_id"])
    op.create_index("ix_zephyr_folders_os_version_id", "zephyr_folders", ["os_version_id"])

    op.get_bind().execute(
        sa.text(
            "UPDATE global_variables SET source = 'zephyr_folder', source_ref = :source_ref, "
            "description = :description, updated_at = now() "
            "WHERE code = 'FOLDER_TREE_ID' AND source = 'launch_context' AND source_ref IS NULL"
        ).bindparams(sa.bindparam("source_ref", type_=postgresql.JSONB)),
        {"source_ref": _FTI_REF, "description": _FTI_DESCRIPTION},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE global_variables SET source = 'launch_context', source_ref = NULL, "
            "description = :description, updated_at = now() "
            "WHERE code = 'FOLDER_TREE_ID' AND source = 'zephyr_folder' "
            "AND source_ref = CAST(:source_ref AS jsonb)"
        ),
        {"source_ref": json.dumps(_FTI_REF), "description": _FTI_PREVIOUS_DESCRIPTION},
    )
    op.drop_index("ix_zephyr_folders_os_version_id", table_name="zephyr_folders")
    op.drop_index("ix_zephyr_folders_department_id", table_name="zephyr_folders")
    op.drop_table("zephyr_folders")
    op.drop_column("department_integration_settings", "zephyr_run_name_template")
    op.drop_column("department_integration_settings", "zephyr_folder_path_template")
