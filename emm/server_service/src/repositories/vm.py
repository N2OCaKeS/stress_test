"""Vm-репозиторий — сырой CRUD против таблицы `vms` + агрегаты ёмкости hub'а."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Vm


async def get_by_id(db: AsyncSession, vm_id: str) -> Vm | None:
    """SELECT ВМ по PK."""
    return (
        await db.execute(select(Vm).where(Vm.id == vm_id))
    ).scalar_one_or_none()


async def get_by_department_number(
    db: AsyncSession, department_id: str, number: int
) -> Vm | None:
    """SELECT ВМ по номеру стенда в рамках отдела (UNIQUE per department_id)."""
    return (
        await db.execute(
            select(Vm).where(
                Vm.department_id == department_id, Vm.number == number,
            )
        )
    ).scalar_one_or_none()


async def max_number_for_department(db: AsyncSession, department_id: str) -> int:
    """MAX(vms.number) в отделе, 0 если ВМ ещё нет."""
    stmt = select(func.max(Vm.number)).where(Vm.department_id == department_id)
    return (await db.execute(stmt)).scalar() or 0


async def list_by_busy_state(db: AsyncSession, busy_state: str) -> list[Vm]:
    """SELECT все ВМ в заданном lifecycle-состоянии (busy_state).

    Reconcile упавших create'ов берёт `busy_state='creating'`: ВМ, чью
    `vm.create`-задачу воркер уже завершил ошибкой, но callback не снял
    lock (worker упал / задача застряла в failed без state-callback'а).
    Порядок по `busy_since` (старые сверху) — чтобы reconcile сначала
    разгребал самые залежавшиеся.
    """
    return list(
        (
            await db.execute(
                select(Vm)
                .where(Vm.busy_state == busy_state)
                .order_by(Vm.busy_since.asc().nulls_last())
            )
        ).scalars()
    )


async def list_all_active(db: AsyncSession, limit: int) -> list[Vm]:
    """SELECT все ВМ платформы (capped) — для статус-sweep'а.

    Зеркало серверного `list_all_active`: у ВМ нет «списанного» статуса, поэтому
    активны все строки таблицы. Порядок по `created_at` (стабильный) с cap'ом
    против шторма задач на крупной платформе; отрезанный хвост выровняется
    следующим прогоном.
    """
    stmt = (
        select(Vm)
        .order_by(Vm.created_at.desc(), Vm.id.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars())


async def count_all_active(db: AsyncSession) -> int:
    """COUNT всех ВМ платформы — для total в статус-sweep'е (детект truncation)."""
    return int((await db.execute(select(func.count(Vm.id)))).scalar_one())


async def list_in_departments(
    db: AsyncSession,
    department_ids: list[str] | None,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Vm]:
    """SELECT ВМ, опционально ограниченный набором отделов (created_at DESC).

    `department_ids=None` → все (зарезервировано); `[]` → пусто.
    """
    stmt = select(Vm).order_by(Vm.created_at.desc(), Vm.id.desc()).limit(limit).offset(offset)
    if department_ids is not None:
        if not department_ids:
            return []
        stmt = stmt.where(Vm.department_id.in_(department_ids))
    return list((await db.execute(stmt)).scalars())


async def count_in_departments(
    db: AsyncSession, department_ids: list[str] | None,
) -> int:
    """COUNT под тем же фильтром, что и list — для total в pagination."""
    stmt = select(func.count(Vm.id))
    if department_ids is not None:
        if not department_ids:
            return 0
        stmt = stmt.where(Vm.department_id.in_(department_ids))
    return int((await db.execute(stmt)).scalar_one())


async def list_by_ids(
    db: AsyncSession, ids: list[str], *, limit: int, offset: int,
) -> list[Vm]:
    """Grant-only листинг: ВМ строго из набора id (created_at DESC)."""
    if not ids:
        return []
    stmt = (
        select(Vm)
        .where(Vm.id.in_(ids))
        .order_by(Vm.created_at.desc(), Vm.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars())


async def count_by_ids(db: AsyncSession, ids: list[str]) -> int:
    """COUNT для grant-only листинга."""
    if not ids:
        return 0
    stmt = select(func.count(Vm.id)).where(Vm.id.in_(ids))
    return int((await db.execute(stmt)).scalar_one())


async def list_used_ips(
    db: AsyncSession, department_id: str, *, exclude_vm_id: str | None = None,
) -> set[str]:
    """Множество занятых IP отдела (непустые `vms.ip_address`).

    Для аллокатора IPAM: занятость ведём по карточкам ВМ. `exclude_vm_id`
    исключает саму ВМ (при смене её же IP, чтобы текущий адрес не считался
    занятым конфликтом с самой собой).
    """
    stmt = select(Vm.ip_address).where(
        Vm.department_id == department_id, Vm.ip_address.is_not(None)
    )
    if exclude_vm_id is not None:
        stmt = stmt.where(Vm.id != exclude_vm_id)
    rows = (await db.execute(stmt)).scalars()
    return {str(ip) for ip in rows if ip is not None}


async def list_for_hub(db: AsyncSession, hub_server_id: str) -> list[Vm]:
    """Все ВМ конкретного hub'а — для проверки ёмкости и вложенного list'а."""
    stmt = select(Vm).where(Vm.hub_server_id == hub_server_id).order_by(Vm.created_at)
    return list((await db.execute(stmt)).scalars())


async def list_for_hub_department(
    db: AsyncSession, hub_server_id: str, department_id: str,
) -> list[Vm]:
    """ВМ отдела на конкретном hub'е — для teardown'а hub'а (снос ВМ отдела)."""
    stmt = (
        select(Vm)
        .where(Vm.hub_server_id == hub_server_id, Vm.department_id == department_id)
        .order_by(Vm.created_at)
    )
    return list((await db.execute(stmt)).scalars())


async def exists_in_department_by_name(
    db: AsyncSession, department_id: str, name: str,
) -> bool:
    """Есть ли ВМ отдела с таким именем (любой hub) — deploy-once bridge-пресета."""
    stmt = select(Vm.id).where(
        Vm.department_id == department_id, Vm.name == name
    ).limit(1)
    return (await db.execute(stmt)).first() is not None


async def exists_on_hub_by_name(
    db: AsyncSession, hub_server_id: str, name: str,
) -> bool:
    """Есть ли ВМ с таким именем на hub'е — deploy-once nat-пресета (на сервер)."""
    stmt = select(Vm.id).where(
        Vm.hub_server_id == hub_server_id, Vm.name == name
    ).limit(1)
    return (await db.execute(stmt)).first() is not None


async def sum_resources_for_hub(db: AsyncSession, hub_server_id: str) -> dict[str, int]:
    """Σ(cpu / ram_mb / disk_gb) уже созданных ВМ на hub'е (NULL → 0)."""
    stmt = select(
        func.coalesce(func.sum(Vm.cpu), 0),
        func.coalesce(func.sum(Vm.ram_mb), 0),
        func.coalesce(func.sum(Vm.disk_gb), 0),
    ).where(Vm.hub_server_id == hub_server_id)
    row = (await db.execute(stmt)).one()
    return {"cpu": int(row[0]), "ram_mb": int(row[1]), "disk_gb": int(row[2])}


async def create(db: AsyncSession, data: dict) -> Vm:
    """INSERT новой ВМ (без commit — owner транзакции caller)."""
    obj = Vm(**data)
    db.add(obj)
    await db.flush()
    return obj


async def delete(db: AsyncSession, vm: Vm) -> None:
    """DELETE строки ВМ (без commit)."""
    await db.delete(vm)
    await db.flush()
