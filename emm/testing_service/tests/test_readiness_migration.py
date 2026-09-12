"""Переход старого каталога к четырём статусам без автоматического допуска."""

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from tests.conftest import TEST_DATABASE_URL


def test_legacy_readiness_migration():
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
    migrate("downgrade", "b6d2f9a1c735")
    try:
        values = [
            ("ready", "ready"),
            ("draft", "development"),
            ("blocked", "broken"),
            (None, "review"),
            ("deprecated", "review"),
            ("typo", "review"),
        ]
        with engine.begin() as conn:
            for index, (old, _) in enumerate(values):
                conn.execute(
                    text(
                        "INSERT INTO test_definitions (id, code, full_name, readiness, created_by) VALUES (:id, :id, :id, :status, 'usr_migration_test')"
                    ),
                    {"id": f"migration_{index}", "status": old},
                )
        migrate("upgrade", "head")
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT readiness FROM test_definitions WHERE created_by = 'usr_migration_test' ORDER BY id"
                    )
                )
                .scalars()
                .all()
            )
        assert rows == [new for _, new in values]
    finally:
        migrate("upgrade", "head")
        engine.dispose()
