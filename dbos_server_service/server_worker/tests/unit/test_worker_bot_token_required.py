"""Fail-fast guard: пустой `WORKER_BOT_TOKEN` валит старт воркера.

Без PAT каждый internal-вызов в server_service возвращает 401, и worker
тихо отдаёт `IPMI_CREDENTIALS_UNAVAILABLE` / `ACCOUNT_PASSWORD_UNAVAILABLE`
без подсказки про причину. В dev/staging/production это материальная дыра —
все power/SSH задачи фейлятся одинаково, оператор тратит час на диагностику.

Проверяем:

* Settings-валидатор бьёт ValueError на пустом токене в dev/staging/production.
* В `local`/`test` пусто разрешено (CI/dev-стек без secret-rotation).
* `python -m src.main` с пустым токеном завершается с exit code 1 и пишет
  про `WORKER_BOT_TOKEN env required` в stderr.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str):
    """Создать свежий Settings с минимальным окружением + перекрытиями."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg://app_user:app_password@postgres:5432/server_worker_db_test",
    )
    monkeypatch.setenv("SERVER_SERVICE_URL", "https://not-used")
    monkeypatch.setenv("AUTH_SERVICE_URL", "https://not-used")
    monkeypatch.setenv("LOGGING_SERVICE_URL", "https://not-used")
    monkeypatch.setenv("WORKER_BOT_TOKEN", "dummy-test-token")
    monkeypatch.setenv("LOGGING_SERVICE_API_KEY", "dummy-test-key")
    for key, value in overrides.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    from src.core.config import Settings

    return Settings()


class TestWorkerBotTokenRequiredOutsideDevTest:
    """Validator `_require_worker_bot_token_outside_dev`."""

    @pytest.mark.parametrize("env", ["dev", "staging", "production"])
    def test_empty_token_rejected_outside_dev(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        with pytest.raises(ValueError, match="WORKER_BOT_TOKEN env required"):
            _make_settings(
                monkeypatch,
                APP_ENV=env,
                REDIS_URL="redis://:pw@redis:6379/0",
                WORKER_BOT_TOKEN="",
            )

    def test_empty_token_rejected_uppercase_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # APP_ENV=PRODUCTION (case-insensitive) тоже триггерит guard.
        with pytest.raises(ValueError, match="WORKER_BOT_TOKEN env required"):
            _make_settings(
                monkeypatch,
                APP_ENV="PRODUCTION",
                REDIS_URL="redis://:pw@redis:6379/0",
                WORKER_BOT_TOKEN="",
            )

    @pytest.mark.parametrize("env", ["local", "test"])
    def test_empty_token_allowed_in_local_and_test(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        # CI/dev-стек поднимается без secret-rotation, HTTP-моки
        # тестов не проверяют Authorization.
        s = _make_settings(
            monkeypatch,
            APP_ENV=env,
            WORKER_BOT_TOKEN="",
        )
        assert s.worker_bot_token == ""

    @pytest.mark.parametrize("env", ["dev", "staging", "production"])
    def test_non_empty_token_accepted(
        self, monkeypatch: pytest.MonkeyPatch, env: str
    ) -> None:
        s = _make_settings(
            monkeypatch,
            APP_ENV=env,
            REDIS_URL="redis://:pw@redis:6379/0",
            WORKER_BOT_TOKEN="wbt-prod-real",
        )
        assert s.worker_bot_token == "wbt-prod-real"


class TestMainProcessExitsOnEmptyToken:
    """`python -c "import src.main"` падает с exit 1 при пустом токене.

    Покрытие fail-fast на уровне процесса — что startup-валидатор
    действительно прерывает старт worker'а, а не «только тест».
    """

    def test_process_exits_with_code_1(self) -> None:
        service_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        env = {
            # PATH/PYTHONPATH/HOME оставляем из родителя, остальное чистим
            # чтобы не утянуть pollute'нутые prod-токены из shell'а оператора.
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONPATH": ".",
            "HOME": os.environ.get("HOME", "/tmp"),
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/d",
            "REDIS_URL": "redis://:strong@redis:6379/0",
            "SERVER_SERVICE_URL": "https://server.prod",
            "AUTH_SERVICE_URL": "https://auth.prod",
            "LOGGING_SERVICE_URL": "https://logging.prod",
            "LOGGING_SERVICE_API_KEY": "prod-key",
            "WORKER_BOT_TOKEN": "",
        }
        result = subprocess.run(
            [sys.executable, "-c", "import src.main"],
            cwd=service_dir,
            env=env,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 1, (
            f"expected exit 1, got {result.returncode}; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        combined = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        assert "WORKER_BOT_TOKEN env required" in combined, combined
