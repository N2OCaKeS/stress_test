"""dtqc-emm display_name префиксы для платформенных сервисов

Revision ID: h5i6j7k8l9m0
Revises: g4h5i6j7k8l9
Create Date: 2026-06-08 00:00:00.000000

Выставляет display_name = `DTQC-EMM <name>` для auth/loging/server/worker.
Для loging_service и server_service делает UPDATE (записи уже посеяны
скриптом). Для auth_service и server_worker делает INSERT — эти сервисы
исторически не регистрировались в platform_services, но осмыслены как
ноды UI (роли/доступы).

secret_service и config_service не трогаем — у них доменные display_name'ы.
internal `service_name` business key'ом и не меняется: URL'ы, audit-события,
FK на service_name остаются как были.

Idempotent: повторный UPDATE по тем же значениям — no-op; INSERT с
ON CONFLICT DO NOTHING переживает повторный апгрейд.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "h5i6j7k8l9m0"
down_revision: Union[str, None] = "g4h5i6j7k8l9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (service_name, new_display_name, prev_display_name_for_downgrade)
# prev_display_name — для UPDATE-кейсов; INSERT-only сервисы помечены None.
_RENAMES: tuple[tuple[str, str, str | None], ...] = (
    ("auth_service",   "DTQC-EMM auth",   None),
    ("loging_service", "DTQC-EMM loging", "Аудит и логирование"),
    ("server_service", "DTQC-EMM server", "Управление серверами"),
    ("server_worker",  "DTQC-EMM worker", None),
)


def upgrade() -> None:
    for service_name, new_display, _ in _RENAMES:
        # INSERT ... ON CONFLICT DO UPDATE: накатывает rename на существующую
        # запись (loging/server) и сразу регистрирует отсутствующих
        # (auth_service / server_worker). description оставляем NULL для
        # вновь созданных — seed_dev.py перетрёт описанием при следующем
        # прогоне.
        op.execute(
            f"""
            INSERT INTO platform_services (service_name, display_name, is_active)
            VALUES ('{service_name}', '{new_display}', TRUE)
            ON CONFLICT (service_name) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    updated_at   = now()
            """
        )


def downgrade() -> None:
    for service_name, _, prev_display in _RENAMES:
        if prev_display is not None:
            # Откатываем display_name к прежнему доменному имени.
            op.execute(
                f"""
                UPDATE platform_services
                   SET display_name = '{prev_display}',
                       updated_at   = now()
                 WHERE service_name = '{service_name}'
                """
            )
        else:
            # auth_service / server_worker не существовали до апгрейда —
            # удаляем. Если успели завязать на них роли/доступы — cascade
            # снесёт их (предупреждение в reports/).
            op.execute(
                f"DELETE FROM platform_services WHERE service_name = '{service_name}'"
            )
