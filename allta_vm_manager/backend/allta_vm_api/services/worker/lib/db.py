# lib/db.py
from __future__ import annotations

import os
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from utils.config import settings

# Процесс-локальные singletons
_engine = None
_sessionmaker: Optional[sessionmaker] = None
_engine_pid: Optional[int] = None


def _ensure_engine():
    """Создаёт engine/sessionmaker после fork, один раз на процесс."""
    global _engine, _sessionmaker, _engine_pid
    pid = os.getpid()
    if _engine is not None and _engine_pid == pid:
        return

    # Если мы тут, либо первый старт, либо мы в новом процессе после fork
    _engine_pid = pid
    _engine = create_async_engine(
        settings.DATABASE_URL_ASYNC,
        echo=False,
        future=True,
        # важно: не шарим пул между процессами/лупами
        poolclass=NullPool,
    )
    _sessionmaker = sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


def get_async_sessionmaker() -> sessionmaker:
    """Возвращает процесс-локальный sessionmaker (ленивая инициализация)."""
    _ensure_engine()
    assert _sessionmaker is not None
    return _sessionmaker
