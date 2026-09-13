"""Доступность текста независимо от результата попытки."""
from sqlalchemy import select
from src.core.constants import ACTIVE_QUEUE_STATES
from src.models import TestLog


def status(item, exists):
    if exists:
        return "available"
    if item.log_rotated_at:
        return "rotated"
    return "pending" if item.state in ACTIVE_QUEUE_STATES else "missing"


async def for_items(db, items):
    if not items:
        return {}
    ids = set((await db.execute(select(TestLog.queue_item_id).where(
        TestLog.queue_item_id.in_([item.id for item in items])
    ))).scalars())
    return {item.id: status(item, item.id in ids) for item in items}
