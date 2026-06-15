"""default severity rules in DB (is_default + seed_state)

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-06-15 00:00:00.000000

Дефолтные severity-правила переезжают из хардкод-таблицы `_DEFAULT_SEVERITY`
в `audit_rules` как видимые/редактируемые/удаляемые row'и (`is_default=true`).

* `audit_rules.is_default` — флаг авто-сидируемого дефолта.
* `seed_state` — маркер «дефолты засеяны»; удалённый дефолт не воскресает.

Сид выполняется здесь (для `make seed` / prod, где гоняются миграции) и
дублируется идемпотентным `rule_service.seed_default_rules` на старте сервиса
(для тестов на `create_all` и как self-healing, если миграция не сеяла).
Оба пути проверяют маркер в `seed_state` — двойного сида не будет.
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "n4o5p6q7r8s9"
down_revision: Union[str, None] = "m3n4o5p6q7r8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "audit_rules",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.create_table(
        "seed_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("seeded_at", sa.DateTime(timezone=True), nullable=False),
    )

    # Сид дефолтов. Импортируем данные сидера из rule_service — единый
    # источник пар `(action, status) → severity` для миграции и для
    # startup-сидера. Если маркер уже стоит (повторный прогон upgrade на
    # частично мигрированной БД) — не сеем.
    from src.services.rule_service import (
        DEFAULT_RULES_SEED_KEY,
        _DEFAULT_RULE_PRIORITY,
        _DEFAULT_SEVERITY,
        default_rule_name,
    )
    from src.utils.ids import audit_rule_id

    bind = op.get_bind()
    already = bind.execute(
        sa.text("SELECT 1 FROM seed_state WHERE key = :k"),
        {"k": DEFAULT_RULES_SEED_KEY},
    ).scalar()
    if already:
        return

    now = datetime.now(timezone.utc)
    audit_rules = sa.table(
        "audit_rules",
        sa.column("id", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_active", sa.Boolean),
        sa.column("is_default", sa.Boolean),
        sa.column("priority", sa.Integer),
        sa.column("match_action", sa.String),
        sa.column("match_status", sa.String),
        sa.column("effect", sa.String),
        sa.column("effect_severity", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    rows = [
        {
            "id": audit_rule_id(),
            "name": default_rule_name(action, status),
            "description": "auto-seeded default severity rule",
            "is_active": True,
            "is_default": True,
            "priority": _DEFAULT_RULE_PRIORITY,
            "match_action": action,
            "match_status": status,
            "effect": "OVERRIDE_SEVERITY",
            "effect_severity": severity,
            "created_at": now,
            "updated_at": now,
        }
        for (action, status), severity in _DEFAULT_SEVERITY.items()
    ]
    if rows:
        op.bulk_insert(audit_rules, rows)

    seed_state = sa.table(
        "seed_state",
        sa.column("key", sa.String),
        sa.column("seeded_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        seed_state,
        [{"key": DEFAULT_RULES_SEED_KEY, "seeded_at": now}],
    )


def downgrade() -> None:
    # Сносим засеянные дефолты вместе с флагом. Managed-правила
    # (`is_default=false`) остаются.
    op.execute("DELETE FROM audit_rules WHERE is_default = true")
    op.drop_table("seed_state")
    op.drop_column("audit_rules", "is_default")
