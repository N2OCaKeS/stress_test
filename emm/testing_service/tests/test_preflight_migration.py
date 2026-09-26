"""Миграция `department_test_settings.preflight` сидит легаси-значения в существующие строки."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from src.schemas.department_test_settings import PreflightSettings
from tests.conftest import TEST_DATABASE_URL

_REVISION = "a4c9e2f17b35"


def test_existing_rows_get_legacy_preflight():
    service_dir = Path(__file__).resolve().parents[1]

    def migrate(*args):
        subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=service_dir,
            env={**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": "."},
            check=True,
            capture_output=True,
        )

    engine = create_engine(TEST_DATABASE_URL)
    migrate("downgrade", f"{_REVISION}-1")
    try:
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO department_test_settings (id, department_id, retry_enabled, test_username) "
                "VALUES ('dts_tp19_migration', 'dep_tp19_migration', true, 'u')"
            ))
        migrate("upgrade", "head")
        with engine.connect() as conn:
            value = conn.execute(text(
                "SELECT preflight FROM department_test_settings WHERE id = 'dts_tp19_migration'"
            )).scalar_one()
        # Сид миграции и дефолты схемы — одни и те же легаси-значения
        # (`liballta.py:1784-1836`, `allta_image_conf.py:81-91`).
        assert value == PreflightSettings().model_dump()
        assert {probe["ok_status"] for probe in value["http"]} == {"200"}
        assert value["poll_interval_seconds"] == 180
        assert value["timeout_seconds"] == 7200
    finally:
        migrate("upgrade", "head")
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM department_test_settings WHERE id = 'dts_tp19_migration'"))
        engine.dispose()
