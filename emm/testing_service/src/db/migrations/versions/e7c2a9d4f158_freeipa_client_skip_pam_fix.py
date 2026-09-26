"""FreeIPA scenarios: client stand without PAM fix

Revision ID: e7c2a9d4f158
Revises: d4b8e2f6a193
Create Date: 2026-09-26 01:00:00.000000

Легаси `emm/allta_app_full/backup_image.py:943-964` готовил ВМ-клиента
FreeIPA с `run_provision.modes = False` — без PAM-правки. Клиентский стенд
(`revert_only`) сидовых сценариев `freeipa.*` получает `skip_pam_fix = true`.
Сценарии, которые уже правил владелец (`created_by` задан), и снимки прошлых
запусков не трогаются.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7c2a9d4f158"
down_revision: Union[str, None] = "d4b8e2f6a193"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FREEIPA_TESTS = ("freeipa.auth", "freeipa.create_users", "freeipa.plugin")


def _set_client_skip_pam_fix(conn, value: bool) -> None:
    conn.execute(sa.text(
        "UPDATE scenario_stands SET skip_pam_fix = :v "
        "WHERE preparation = 'revert_only' AND skip_pam_fix IS DISTINCT FROM :v "
        "AND scenario_id IN (SELECT id FROM scenarios WHERE code = ANY(:codes) AND created_by IS NULL)"
    ), {"v": value, "codes": list(FREEIPA_TESTS)})


def upgrade() -> None:
    _set_client_skip_pam_fix(op.get_bind(), True)


def downgrade() -> None:
    _set_client_skip_pam_fix(op.get_bind(), False)
