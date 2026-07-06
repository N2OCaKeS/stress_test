"""Бизнес-логика настраиваемой кнопки левой панели web-UI.

Единственная строка конфига (`nav_link`) задаёт подпись, внешний URL и правило
видимости — либо «все отделы», либо явный список `department_ids`. Обычный
пользователь получает кнопку только если она включена, имеет URL и его отдел
попадает под правило видимости. account_admin настраивает конфиг через
`/admin/nav-links`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import DomainValidationError
from src.models.nav_link import DEFAULT_LABEL
from src.repositories.nav_links import NavLinkRepository
from src.schemas.nav_links import (
    NavLinkConfig,
    NavLinkConfigUpdate,
    NavLinkItem,
)
from src.services import audit_service


async def get_config(db: AsyncSession) -> NavLinkConfig:
    """Полная конфигурация кнопки. Если строки нет — дефолт (выключена)."""
    row = await NavLinkRepository(db).get()
    if row is None:
        return NavLinkConfig(
            enabled=False,
            label=DEFAULT_LABEL,
            url=None,
            all_departments=False,
            department_ids=[],
        )
    return NavLinkConfig(
        enabled=row.enabled,
        label=row.label,
        url=row.url,
        all_departments=row.all_departments,
        department_ids=list(row.department_ids or []),
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


async def list_visible(
    db: AsyncSession,
    department_id: str | None,
) -> list[NavLinkItem]:
    """Кнопки, видимые отделу этой identity.

    Кнопка отдаётся только если она включена, у неё задан URL и выполнено
    правило видимости: `all_departments=True` — виден всем аутентифицированным;
    иначе — только если `department_id` есть в списке разрешённых. Персоны без
    отдела (account_admin / платформенные) видят кнопку лишь по правилу
    `all_departments`.
    """
    row = await NavLinkRepository(db).get()
    if row is None or not row.enabled or not row.url:
        return []
    visible = row.all_departments or (
        department_id is not None and department_id in (row.department_ids or [])
    )
    if not visible:
        return []
    return [NavLinkItem(label=row.label, url=row.url)]


async def update_config(
    db: AsyncSession,
    actor_id: str,
    actor_username: str | None,
    body: NavLinkConfigUpdate,
    request_id: str | None = None,
) -> NavLinkConfig:
    """Записать конфиг кнопки (полная замена). Возвращает новое состояние.

    Если кнопка включена — URL обязателен (иначе клиенту нечего открывать).
    Правило `all_departments=False` без непустого `department_ids` оставляет
    кнопку невидимой для всех — это допустимо (эффективно выключено), отдельной
    ошибкой не режем.
    """
    if body.enabled and not body.url:
        raise DomainValidationError(
            error_code="NAV_LINK_URL_REQUIRED",
            message="url is required when the nav link is enabled",
        )

    repo = NavLinkRepository(db)
    # Нормализуем список отделов: убираем дубликаты и пустые, сохраняя порядок.
    dept_ids: list[str] = []
    for d in body.department_ids:
        d = d.strip()
        if d and d not in dept_ids:
            dept_ids.append(d)

    await repo.upsert(
        enabled=body.enabled,
        label=body.label,
        url=body.url,
        all_departments=body.all_departments,
        department_ids=dept_ids,
        updated_by=actor_id,
    )
    await db.commit()

    audit_service.emit(
        "nav_link.update",
        actor_id,
        target_id=None,
        target_type="nav_link",
        username=actor_username,
        details={
            "enabled": body.enabled,
            "label": body.label,
            "url": body.url,
            "all_departments": body.all_departments,
            "department_count": len(dept_ids),
        },
        request_id=request_id,
    )
    return await get_config(db)
