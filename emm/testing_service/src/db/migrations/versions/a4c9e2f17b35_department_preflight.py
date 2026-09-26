"""department_test_settings.preflight

Revision ID: a4c9e2f17b35
Revises: e4b7a2c9d1f3
Create Date: 2026-09-24 12:00:00.000000

Проверка внешних сервисов перед запуском теста (T4) переезжает из env
воркера (`testing_worker/src/core/config.py`, `PREFLIGHT_*`) в настройки
тестов отдела: JSON-колонка `preflight` в форме CONTRACTS.md C3,
`schemas/department_test_settings.py::PreflightSettings`.

Сид — легаси `available_astra_services_checker`
(`emm/allta_app_full/libs/liballta.py:1784-1836`):

* HTTP — `https://{JIRA_URL|CONFLUENCE_URL|GIT_URL|RELEASES_URL}`
  (`emm/allta_app_full/allta_image_conf.py:81-84`, запросы —
  `liballta.py:1794-1797`), доступен — строго `200` (`liballta.py:1810`,
  `jira == 200 and life == 200...`). Воркер до считал доступным
  любой ответ `< 500`; теперь это настраиваемый `ok_status: "lt500"`.
* DNS — `ASTRA_DNS` (`allta_image_conf.py:91`), достаточно одного
  (`liballta.py:1790`, `if 0 in available_dns.values()`). Легаси слало
  ICMP-ping; воркер проверяет TCP на `dns_port` 53.
* `poll_interval_seconds` 180 — `requests_frequency = 180` (`liballta.py:1807`).
* `timeout_seconds` 7200 — `wait_time = 120` минут (`liballta.py:1806`).
* `probe_timeout_seconds` 15 — прежний env-дефолт воркера; у легаси
  `requests.get` таймаута не было.

Существующие строки получают этот сид явно. У новых строк, созданных PUT без
`preflight`, колонка `NULL` — сервис подставляет те же значения (дефолты
`PreflightSettings`).
"""
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4c9e2f17b35"
down_revision: Union[str, None] = "e4b7a2c9d1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_LEGACY_PREFLIGHT = {
    "enabled": True,
    "http": [
        {"url": "https://jira.astralinux.ru", "ok_status": "200"},
        {"url": "https://life.astralinux.ru", "ok_status": "200"},
        {"url": "https://git.astralinux.ru", "ok_status": "200"},
        {"url": "https://releases.devos.astralinux.ru", "ok_status": "200"},
    ],
    "dns_hosts": ["10.177.128.198", "10.177.180.246", "10.177.181.142"],
    "dns_port": 53,
    "poll_interval_seconds": 180,
    "timeout_seconds": 7200,
    "probe_timeout_seconds": 15,
}


def upgrade() -> None:
    op.add_column(
        "department_test_settings",
        sa.Column("preflight", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE department_test_settings SET preflight = CAST(:value AS JSONB) "
            "WHERE preflight IS NULL"
        ).bindparams(value=json.dumps(_LEGACY_PREFLIGHT))
    )


def downgrade() -> None:
    op.drop_column("department_test_settings", "preflight")
