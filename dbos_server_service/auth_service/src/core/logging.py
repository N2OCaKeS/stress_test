"""Logging configuration and audit helpers."""

import logging


def configure_logging(log_level: str = "INFO") -> None:
    """Configure a simple application-wide logging format."""

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
