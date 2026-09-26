"""Public legacy compat /rest/api (allowed networks, default department), service address variables

Revision ID: d2b6e8f41a73
Revises: d7e2a9c4f186
Create Date: 2026-09-24 18:00:00.000000

Решение D17 плана паритета: скрипты на стендах ходят на легаси-хост
ALLTA без авторизации (`http://allta.devos.astralinux.ru/rest/api/...`);
после переноса DNS эти пути обслуживает `api/legacy_public.py`.

* `compat_allowed_networks` — подсети, из которых `/rest/api/*` доступен без
  авторизации. Сид — `10.177.103.0/24`: все стенды легаси
  (`emm/allta_app_full/allta_image_conf.py:34-49`, `stands_ip`:
  `10.177.103.101`…`10.177.103.210`) и ВМ на них
  (`emm/allta_app_full/make/vm_prepare/libvirt_vm.py:13-96`, `ip_bridge`).
  Легаси пускал всех — подсеть стендов ближе всего к тому, кто им реально
  пользовался.
* `legacy_compat_settings` — singleton с отделом по умолчанию для
  `get-jira-url`/`get-confluence-url`. Легаси отдавал одну глобальную строку
  (`allta_image_conf.py:81-82`), отдела у неё не было — поэтому сид `NULL`
  (отдел выбирает администратор в UI; до этого запрос с незнакомого IP
  получает 404 `LEGACY_COMPAT_DEPARTMENT_UNKNOWN`, а IP стенда — URL его
  отдела).
* Права `legacy_compat:view/update` — системной роли `admin`.

Адреса внешних сервисов легаси-хоста — static-переменные, чтобы шаблоны
профиля запуска и скриптов не содержали IP (D17: «FTP/devpi/infocollector —
отдельные сервисы, их адреса доступны скриптам как переменные»):

* `INFOCOLLECTOR_URL` = `http://10.177.103.10:18181` —
  `emm/allta_app_full/starter.sh:71`;
* `FTP_URL` = `ftp://10.177.103.10` —
  `emm/allta_app_full/make/vm_prepare/vms_prepare.sh:18`,
  `emm/allta_app_full/box-config.json`;
* `DEVPI_URL` = `http://10.177.103.10:3141/root/release` —
  `emm/allta_app_full/make/vm_prepare/vms_prepare.sh:27`;
* `DOCKER_REGISTRY` = `allta.devos.astralinux.ru:21503` —
  `emm/allta_app_full/starter.sh:65` (insecure-registries).

Новые строки — `ON CONFLICT (code) DO NOTHING`: переменную, заведённую руками
с тем же кодом, миграция не перезаписывает.

Общий профиль запуска `lp_default` получает версию 2: тот же `starter.sh`,
но адреса infocollector и registry — `{{INFOCOLLECTOR_URL}}` и
`{{DOCKER_REGISTRY}}`. С переменными по умолчанию скрипт на стенде
получается байт-в-байт прежним. Версия добавляется, только если действующая
— сидовая `lpv_default_1` (профиль не правили руками) и в ней есть оба адреса.
"""
from typing import Sequence, Union
from uuid import uuid4

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d2b6e8f41a73"
down_revision: Union[str, None] = "d7e2a9c4f186"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SEED_NETWORK_ID = "cnet_stands_legacy"
_SEED_NETWORK_CIDR = "10.177.103.0/24"

_PROFILE_ID = "lp_default"
_V1_ID = "lpv_default_1"
_V2_ID = "lpv_default_2"

_INFOCOLLECTOR = "http://10.177.103.10:18181"
_REGISTRY = "allta.devos.astralinux.ru:21503"

# (code, label, value, description)
_VARIABLES: list[tuple[str, str, str, str]] = [
    (
        "INFOCOLLECTOR_URL", "Адрес infocollector", _INFOCOLLECTOR,
        "Дашборд нагрузки allta_infocollector (легаси starter.sh:71: "
        "`curl http://10.177.103.10:18181/rest/api/dashboard/$localhost/full`). "
        "Подставляется в starter.sh профиля запуска как {{INFOCOLLECTOR_URL}}.",
    ),
    (
        "FTP_URL", "Адрес FTP с пакетами и боксами", "ftp://10.177.103.10",
        "FTP легаси-хоста: python, modules, postgresql, boxes "
        "(make/vm_prepare/vms_prepare.sh:18, box-config.json). Им же "
        "подменяется адрес в /rest/api/get-box-config.",
    ),
    (
        "DEVPI_URL", "Адрес devpi (пакет allta)", "http://10.177.103.10:3141/root/release",
        "Индекс devpi с пакетом allta (make/vm_prepare/vms_prepare.sh:27). "
        "Ветки берут его из /etc/pip.conf стенда.",
    ),
    (
        "DOCKER_REGISTRY", "Docker registry стендов", _REGISTRY,
        "insecure-registry, который starter.sh прописывает в "
        "/etc/docker/daemon.json (легаси starter.sh:61-68). Подставляется как "
        "{{DOCKER_REGISTRY}}. После переноса DNS allta.devos.astralinux.ru на "
        "платформу укажите здесь адрес, где registry остался.",
    ),
]


def _jsonb(name: str) -> sa.sql.expression.BindParameter:
    return sa.bindparam(name, type_=postgresql.JSONB)


def upgrade() -> None:
    op.create_table(
        "compat_allowed_networks",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("cidr", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=256), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("cidr", name="uq_compat_allowed_networks_cidr"),
    )
    op.create_table(
        "legacy_compat_settings",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("default_department_id", sa.String(length=64), nullable=True),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    bind = op.get_bind()
    bind.execute(sa.text(
        "INSERT INTO compat_allowed_networks (id, cidr, description, enabled) "
        "VALUES (:id, :cidr, :description, true)"
    ), {
        "id": _SEED_NETWORK_ID, "cidr": _SEED_NETWORK_CIDR,
        "description": "Подсеть стендов и ВМ легаси (allta_image_conf.py:34-49)",
    })
    bind.execute(sa.text(
        "INSERT INTO legacy_compat_settings (id, default_department_id) VALUES ('default', NULL)"
    ))

    permissions = sa.table(
        "entity_permissions",
        sa.column("id", sa.String), sa.column("entity_type", sa.String),
        sa.column("role", sa.String), sa.column("action", sa.String),
    )
    op.bulk_insert(permissions, [
        {"id": f"prm_{uuid4().hex}", "entity_type": "legacy_compat", "role": "admin", "action": action}
        for action in ("view", "update")
    ])

    insert = sa.text(
        "INSERT INTO global_variables "
        "(id, code, label, source, value_type, choices_source, is_sensitive, description, source_ref) "
        "VALUES (:id, :code, :label, 'static', 'string', NULL, false, :description, :source_ref) "
        "ON CONFLICT (code) DO NOTHING"
    ).bindparams(_jsonb("source_ref"))
    for code, label, value, description in _VARIABLES:
        bind.execute(insert, {
            "id": f"gvar_{code.lower()}", "code": code, "label": label,
            "description": description, "source_ref": {"value": value},
        })

    _add_profile_version(bind)


def _add_profile_version(bind) -> None:
    current = bind.execute(sa.text(
        "SELECT current_version_id FROM launch_profiles WHERE id = :id"
    ), {"id": _PROFILE_ID}).scalar()
    if current != _V1_ID:
        return
    v1 = bind.execute(sa.text(
        "SELECT starter_script, clone, paths, launch_command_template, stop_command_template, "
        "stop_grace_seconds, use_pty, testenv FROM launch_profile_versions WHERE id = :id"
    ), {"id": _V1_ID}).mappings().first()
    if v1 is None or _INFOCOLLECTOR not in v1["starter_script"] or _REGISTRY not in v1["starter_script"]:
        return
    script = (
        v1["starter_script"]
        .replace(_INFOCOLLECTOR, "{{INFOCOLLECTOR_URL}}")
        .replace(_REGISTRY, "{{DOCKER_REGISTRY}}")
    )
    versions = sa.table(
        "launch_profile_versions",
        sa.column("id", sa.String), sa.column("profile_id", sa.String), sa.column("version", sa.Integer),
        sa.column("comment", sa.String), sa.column("starter_script", sa.Text),
        sa.column("clone", postgresql.JSONB), sa.column("paths", postgresql.JSONB),
        sa.column("launch_command_template", sa.Text), sa.column("stop_command_template", sa.Text),
        sa.column("stop_grace_seconds", sa.Integer), sa.column("use_pty", sa.Boolean),
        sa.column("testenv", postgresql.JSONB),
    )
    op.bulk_insert(versions, [{
        "id": _V2_ID, "profile_id": _PROFILE_ID, "version": 2,
        "comment": "адреса infocollector и registry — переменные INFOCOLLECTOR_URL, DOCKER_REGISTRY",
        "starter_script": script, "clone": v1["clone"], "paths": v1["paths"],
        "launch_command_template": v1["launch_command_template"],
        "stop_command_template": v1["stop_command_template"],
        "stop_grace_seconds": v1["stop_grace_seconds"], "use_pty": v1["use_pty"],
        "testenv": v1["testenv"],
    }])
    bind.execute(sa.text(
        "UPDATE launch_profiles SET current_version_id = :v2, updated_at = now() WHERE id = :id"
    ), {"v2": _V2_ID, "id": _PROFILE_ID})


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(
        "UPDATE launch_profiles SET current_version_id = :v1, updated_at = now() "
        "WHERE id = :id AND current_version_id = :v2"
    ), {"v1": _V1_ID, "v2": _V2_ID, "id": _PROFILE_ID})
    bind.execute(sa.text("DELETE FROM launch_profile_versions WHERE id = :v2"), {"v2": _V2_ID})
    bind.execute(
        sa.text(
            "DELETE FROM global_variables WHERE code IN :codes AND created_by IS NULL "
            "AND id NOT IN (SELECT variable_id FROM test_command_args WHERE variable_id IS NOT NULL)"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"codes": [code for code, *_ in _VARIABLES]},
    )
    bind.execute(sa.text("DELETE FROM entity_permissions WHERE entity_type = 'legacy_compat'"))
    op.drop_table("legacy_compat_settings")
    op.drop_table("compat_allowed_networks")
