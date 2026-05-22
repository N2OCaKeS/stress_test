"""Конфиг логирования."""

import logging


def configure_logging(log_level: str = "INFO") -> None:
    """Простой application-wide формат логов. Зовём один раз при старте."""

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
