"""Гейт деструктивных операций над сервером.

Деструктив — операция, которая мутирует состояние входа на бокс: смена
пароля аккаунта (`account.rotate_password`), ротация пароля IPMI-контроллера
(`ipmi.rotate_password`), удаление OS-пользователя (`account.deprovision`),
cutover-rename management-учётки (`management_user_sync`).
Если такую операцию запустить, пока на ТОМ ЖЕ сервере выполняется другая
задача (inventory.sync под SSH, ещё одна провизия, консольная сессия,
завязанная на тот же аккаунт), параллельный chpasswd / userdel может оборвать
её сессию или оставить бокс в полу-применённом состоянии.

Гейт зовётся ВНУТРИ `impl`, ДО первого side-effect'а на хосте. Сама задача к
этому моменту уже в `running` (mark_running сделан `_runner`), поэтому
проверка считает только ДРУГИЕ running-задачи на сервере (исключая себя). Если
их нет — гейт молча пропускает. Если есть хотя бы одна — поднимает
`DestructiveGateDeferred`; `_runner` шедулит durable reschedule без расхода
`max_attempts`, и задача дождётся момента, когда бокс освободится.
"""

from __future__ import annotations

import logging

from src.core.exceptions import DestructiveGateDeferred
from src.db.session import AsyncSessionLocal
from src.repositories import task as task_repo

logger = logging.getLogger(__name__)


async def ensure_no_other_running_on_server(task_id: str, server_id: str) -> None:
    """Отбить деструктив, если на `server_id` крутится другая задача.

    Атомарность: один `SELECT count(*) WHERE status='running' AND
    target_server_id=:sid AND id != :self` — снимок согласован на уровне
    PostgreSQL read-committed. Гонка «две деструктивные задачи на один сервер
    стартовали одновременно и обе не видят друг друга» закрыта тем, что
    mark_running коммитится в `_runner` ДО входа в impl: к моменту гейта обе
    уже `running` и видят друг друга, обе откладываются и расходятся по
    разному backoff'у при reschedule.

    `server_id` пустой/None — пропускаем (нечего гейтить, такой задачи в
    норме не бывает у деструктива, но защищаемся от падения на None).
    """
    if not server_id:
        return
    async with AsyncSessionLocal() as session:
        running = await task_repo.count_other_running_on_server(
            session, server_id=server_id, exclude_task_id=task_id,
        )
    if running > 0:
        logger.info(
            "destructive gate: deferring task_id=%s — server %s has %s other "
            "running task(s)",
            task_id, server_id, running,
        )
        raise DestructiveGateDeferred(
            error_code="DESTRUCTIVE_DEFERRED_SERVER_BUSY",
            message=(
                "Destructive operation deferred: server has other running "
                "tasks; will retry once they complete."
            ),
            details={"server_id": server_id, "running_tasks": running},
        )


__all__ = ["ensure_no_other_running_on_server"]
