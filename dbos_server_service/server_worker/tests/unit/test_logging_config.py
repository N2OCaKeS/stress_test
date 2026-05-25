"""Регрессия — `src.main` обязан подавлять verbose-логирование
HTTP-клиентов, иначе при `WORKER_LOG_LEVEL=DEBUG` `httpx._client` логирует полные
запросы вместе с `Authorization: Bearer <worker_bot_token>` в stdout → journald.

После импорта `src.main` уровень логгеров `httpx`, `httpcore`, `hpack` должен быть
не ниже WARNING.
"""

from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _reset_noisy_loggers():
    """Каждый тест получает свежие настройки шумных логгеров.

    `src.main` импортируется один раз на процесс, и `setLevel(WARNING)` уже
    был вызван к моменту запуска тестов (conftest подгрузил окружение, тесты
    выше по дереву могли импортировать `src.*`). Чтобы тест измерял именно
    эффект логики `main.py`, перед каждым кейсом сбрасываем уровни в NOTSET и
    переимпортируем модуль через `importlib.reload`.
    """
    for name in ("httpx", "httpcore", "hpack"):
        logging.getLogger(name).setLevel(logging.NOTSET)
    yield


@pytest.mark.parametrize("logger_name", ["httpx", "httpcore", "hpack"])
def test_noisy_http_loggers_capped_at_warning(logger_name):
    """После загрузки `src.main` уровень шумного логгера не ниже WARNING."""
    import importlib

    import src.main

    importlib.reload(src.main)

    level = logging.getLogger(logger_name).level
    assert level == logging.WARNING, (
        f"logger {logger_name!r} level={logging.getLevelName(level)}; "
        "expected WARNING — иначе DEBUG раскроет Authorization-заголовки"
    )


def test_effective_level_blocks_debug_records():
    """Прямая проверка инварианта: запись DEBUG-уровня не попадёт в handler.

    Даже если корневой логгер настроен на DEBUG (как при WORKER_LOG_LEVEL=DEBUG),
    `httpx.isEnabledFor(DEBUG)` обязан вернуть False.
    """
    import importlib

    import src.main

    importlib.reload(src.main)

    logging.getLogger().setLevel(logging.DEBUG)
    try:
        for name in ("httpx", "httpcore", "hpack"):
            logger = logging.getLogger(name)
            assert not logger.isEnabledFor(logging.DEBUG), (
                f"{name}: DEBUG-записи не должны проходить — это утечка Bearer-токена в stdout"
            )
            assert not logger.isEnabledFor(logging.INFO), (
                f"{name}: INFO-уровень httpx тоже логирует request/response — нужно WARNING"
            )
            assert logger.isEnabledFor(logging.WARNING), (
                f"{name}: WARNING должен оставаться видимым — иначе скроем настоящие ошибки"
            )
    finally:
        logging.getLogger().setLevel(logging.WARNING)
