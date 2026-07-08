"""Точка входа: поднять aiohttp-сервер console_proxy."""

from __future__ import annotations

import logging

from aiohttp import web

from src.app import create_app
from src.config import get_settings


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    app = create_app(settings)
    web.run_app(app, host=settings.host, port=settings.port, access_log=None)


if __name__ == "__main__":
    main()
