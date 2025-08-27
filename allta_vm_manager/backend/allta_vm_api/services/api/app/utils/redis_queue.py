from __future__ import annotations

import json
from typing import Any, Dict, Optional
from redis import asyncio as redis

from app.utils.config import settings



async def enqueue_task(task: Dict[str, Any], queue: Optional[str] = None) -> None:
    """
    Кладёт задачу (dict) в Redis-очередь как JSON.
    Не блокирует сервер; использует redis.asyncio.
    """
    key = queue or settings.QUEUE_KEY
    client = redis.Redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)

    try:
        payload = json.dumps(task, ensure_ascii=False)
        await client.rpush(key, payload)
    finally:
        await client.aclose()
