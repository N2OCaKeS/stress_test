"""IpmiController-репозиторий — CRUD + rotate_credentials.

`get_by_server_id` остаётся основной точкой доступа: связь servers↔ipmi
1:1 (UNIQUE на server_id), поэтому все эндпоинты идут через server_id.
`get_by_id` — для редких случаев lookup'а по PK (внутренние утилиты).
"""

from datetime import datetime, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import IpmiController, Server


async def get_by_server_id(db: AsyncSession, server_id: str) -> IpmiController | None:
    """SELECT IPMI-controller по server_id (UNIQUE)."""
    stmt = select(IpmiController).where(IpmiController.server_id == server_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_id(db: AsyncSession, controller_id: str) -> IpmiController | None:
    """SELECT по PK. Visibility-check делает service-layer."""
    stmt = select(IpmiController).where(IpmiController.id == controller_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def list_in_departments(
    db: AsyncSession,
    department_ids: list[str] | None,
    limit: int = 100,
    offset: int = 0,
) -> list[IpmiController]:
    """SELECT IPMI-контроллеров через JOIN с servers по dept-фильтру.

    `department_ids=None` → все (зарезервировано). `[]` → пусто (caller без
    department). JOIN нужен потому, что department_id живёт на servers, а
    не на ipmi_controllers — иначе пришлось бы дублировать колонку.
    """
    stmt = (
        select(IpmiController)
        .join(Server, Server.id == IpmiController.server_id)
        .order_by(IpmiController.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if department_ids is not None:
        if not department_ids:
            return []
        stmt = stmt.where(Server.department_id.in_(department_ids))
    return list((await db.execute(stmt)).scalars())


async def list_in_departments_after(
    db: AsyncSession,
    department_ids: list[str] | None,
    *,
    limit: int,
    after_created_at: datetime | None,
    after_id: str | None,
) -> list[IpmiController]:
    """Keyset-страница IPMI-контроллеров по `(created_at DESC, id DESC)`.

    JOIN с `servers` чтобы фильтровать по department'у — dept-колонки на
    самом контроллере нет.
    """
    if department_ids is not None and not department_ids:
        return []
    stmt = (
        select(IpmiController)
        .join(Server, Server.id == IpmiController.server_id)
        .order_by(IpmiController.created_at.desc(), IpmiController.id.desc())
        .limit(limit)
    )
    if department_ids is not None:
        stmt = stmt.where(Server.department_id.in_(department_ids))
    if after_created_at is not None and after_id is not None:
        stmt = stmt.where(
            or_(
                IpmiController.created_at < after_created_at,
                and_(
                    IpmiController.created_at == after_created_at,
                    IpmiController.id < after_id,
                ),
            )
        )
    return list((await db.execute(stmt)).scalars())


async def count_in_departments(
    db: AsyncSession,
    department_ids: list[str] | None,
) -> int:
    """COUNT под тем же фильтром, что и list — для total в pagination."""
    stmt = (
        select(func.count(IpmiController.id))
        .join(Server, Server.id == IpmiController.server_id)
    )
    if department_ids is not None:
        if not department_ids:
            return 0
        stmt = stmt.where(Server.department_id.in_(department_ids))
    return int((await db.execute(stmt)).scalar_one())


async def create(db: AsyncSession, data: dict) -> IpmiController:
    """INSERT новой строки. commit — на caller'е."""
    obj = IpmiController(**data)
    db.add(obj)
    await db.flush()
    return obj


async def update(db: AsyncSession, obj: IpmiController, changes: dict) -> IpmiController:
    """In-place setattr + flush. commit — на caller'е."""
    for key, value in changes.items():
        setattr(obj, key, value)
    await db.flush()
    return obj


async def delete(db: AsyncSession, obj: IpmiController) -> None:
    """DELETE объекта. commit — на caller'е."""
    await db.delete(obj)
    await db.flush()


async def update_password(
    db: AsyncSession, controller: IpmiController, password_encrypted: str
) -> IpmiController:
    """Сменить пароль + проставить `password_rotated_at = now (UTC)`. commit — на caller'е."""
    controller.password_encrypted = password_encrypted
    controller.password_rotated_at = datetime.now(timezone.utc)
    await db.flush()
    return controller
