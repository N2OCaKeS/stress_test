"""Регрессия: `run_publisher_loop` не пишет в stdout без redaction.

История: утечка в `audit_outbox.last_error` (worker-DB) была закрыта
через `redact_error_message`. Тот же exception в `run_publisher_loop`'s
`except`-ветке шёл в stdout через `logger.error("...: %s", exc)` — на
k8s-deploy'е stdout агрегируется fluentd/Loki, и секреты из URL
credentials / Bearer-токенов утекали в централизованные логи.

Этот тест-модуль проверяет ровно один инвариант: **message-аргументы
logger-вызовов в `audit_outbox_publisher` и `_runner` не содержат
plaintext-секретов**, даже если exception, которое их породило, имеет
секрет в `str(exc)`.

Замечания по покрытию:

* `_runner.py` на момент написания теста **не имеет** собственных
  `logger.*(exc...)` вызовов. Безопасность его error-path обеспечена
  тем, что текст ошибки попадает только в `task.last_error` и audit
  `details.error` — и оба прогоняются через `redact_error_message`
  (см. `tests/unit/test_redaction.py::TestRunnerRedactsSecrets`).
  Здесь мы фиксируем это инвариантом: «модуль не делает stdout-логов
  с raw exception-объектом». Если в будущем кто-то добавит
  `logger.warning("task failed: %s", exc)` в `_runner.py` — этот тест
  упадёт первым.
* `_publish_one` для своего `logger.warning` передаёт только
  `type(exc).__name__` (без значения) — message-аргумент уже безопасен,
  тесту менять там нечего.
* Фокус — `run_publisher_loop`'s outer `except Exception` ветка
  (`flush_outbox` упал целиком, не одна строка).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest

from src.services import audit_outbox_publisher


# ── 1. Прямая проверка инвариант'а у helper'а ────────────────────────────────


class TestRedactionHelperApplied:
    """Smoke: `redact_error_message` импортирован и виден в модуле."""

    def test_redaction_helper_imported_in_publisher_module(self):
        assert hasattr(audit_outbox_publisher, "redact_error_message"), (
            "audit_outbox_publisher должен использовать redact_error_message — "
            "иначе stdout-логи утекут URL credentials / Bearer токены"
        )


# ── 2. run_publisher_loop: exception → stdout, плагины redact ────────────────


class _StopLoop(Exception):
    """Помечаем «достаточно итераций» из теста — выходим из бесконечного loop'а."""


async def _run_one_iteration(
    *, exception_message: str, caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    """Запустить ровно одну итерацию `run_publisher_loop`.

    Мокаем `flush_outbox` так, чтобы при первом вызове он кинул
    `Exception(exception_message)`, а `asyncio.sleep` — чтобы он
    выкидывал `_StopLoop` и обрывал loop. Возвращаем все
    лог-записи `audit_outbox_publisher` уровня WARNING+.
    """

    call_state = {"flush_calls": 0, "sleep_calls": 0}

    async def failing_flush(*, limit: int = 50) -> tuple[int, int]:  # noqa: ARG001
        call_state["flush_calls"] += 1
        # Симулируем реальный httpx-style текст ошибки.
        raise RuntimeError(exception_message)

    async def aborting_sleep(_seconds: float) -> None:
        # `run_publisher_loop` спит после каждого прохода; ловим момент
        # после первой попытки и обрываем loop.
        call_state["sleep_calls"] += 1
        raise _StopLoop()

    caplog.set_level(logging.WARNING, logger=audit_outbox_publisher.__name__)

    # Loop вызывает `_flush_outbox_once` напрямую (circuit breaker
    # рефакторинг). Mockаем именно его — публичный `flush_outbox` зовётся
    # только inline из `_runner`, не из loop'а.
    audit_outbox_publisher._reset_breaker_state()
    with patch.object(audit_outbox_publisher, "_flush_outbox_once", failing_flush), \
         patch.object(audit_outbox_publisher.asyncio, "sleep", aborting_sleep):
        with pytest.raises(_StopLoop):
            await audit_outbox_publisher.run_publisher_loop(interval_seconds=0.01)

    assert call_state["flush_calls"] == 1, (
        "_flush_outbox_once должен быть вызван ровно один раз — иначе "
        "тест мерит не ту итерацию"
    )
    assert call_state["sleep_calls"] == 1, (
        "asyncio.sleep должен быть вызван после except — обрыв loop'а через "
        "sleep гарантирует, что except-ветка успела выполнить logger.*"
    )
    # Возвращаем только записи нашего модуля (caplog ловит всю иерархию).
    return [
        r for r in caplog.records
        if r.name == audit_outbox_publisher.__name__
    ]


class TestRunPublisherLoopStdoutRedaction:
    """Главная цель файла: stdout-лог `run_publisher_loop` не утекает
    plaintext-секреты, когда exception из `flush_outbox` имеет URL
    credentials / Bearer / `password=...` в repr."""

    async def test_url_credentials_redacted(self, caplog):
        """httpx.RequestError-style: текст ошибки содержит
        `https://user:pass@host/...`."""
        secret_password = "Calvin"
        leaky_url = f"https://root:{secret_password}@idrac.example/redfish/v1"
        exc_msg = f"Connection refused for {leaky_url} after 3 retries"

        records = await _run_one_iteration(
            exception_message=exc_msg, caplog=caplog,
        )

        assert len(records) == 1, (
            f"ожидался ровно один WARNING/ERROR-record, получено {len(records)}: "
            f"{[r.getMessage() for r in records]}"
        )
        rendered = records[0].getMessage()

        # Plaintext-пароль не должен попасть в stdout.
        assert secret_password not in rendered, (
            f"`{secret_password}` обнаружен в логе: {rendered!r}"
        )
        # URL credentials заменены на placeholder.
        assert "<PASSWORD>" in rendered, (
            f"placeholder <PASSWORD> отсутствует — redact не отработал: {rendered!r}"
        )
        # Тип исключения сохранён для диагностики.
        assert "RuntimeError" in rendered

    async def test_bearer_token_redacted(self, caplog):
        """Authorization-заголовок утекает в repr(): `Bearer eyJ...`."""
        secret_token = "eyJhbGciOiJIUzI1NiJ9.payload.signaturedata"
        exc_msg = f"HTTP 401 on Authorization: Bearer {secret_token}"

        records = await _run_one_iteration(
            exception_message=exc_msg, caplog=caplog,
        )

        assert len(records) == 1
        rendered = records[0].getMessage()

        assert secret_token not in rendered
        assert "Bearer <TOKEN>" in rendered

    async def test_password_kv_form_redacted(self, caplog):
        """`password=...` в произвольном тексте exception'а."""
        secret_password = "hunter2plain"
        exc_msg = f"AuthError: password={secret_password} rejected by server"

        records = await _run_one_iteration(
            exception_message=exc_msg, caplog=caplog,
        )

        assert len(records) == 1
        rendered = records[0].getMessage()

        assert secret_password not in rendered
        assert "password=<PASSWORD>" in rendered

    async def test_dbos_opaque_token_redacted(self, caplog):
        """`dbos_pat_*` / `dbos_bot_*` опаковые токены системы."""
        secret_token = "dbos_bot_xx1234567890ZZabcdef"
        exc_msg = f"bot auth failed for {secret_token} on /events"

        records = await _run_one_iteration(
            exception_message=exc_msg, caplog=caplog,
        )

        assert len(records) == 1
        rendered = records[0].getMessage()

        assert secret_token not in rendered
        assert "<TOKEN>" in rendered

    async def test_plain_message_passes_through_unchanged(self, caplog):
        """Регрессия: без секретов текст диагностики сохраняется
        вербатим. Чрезмерно агрессивный redact = потерянная диагностика."""
        exc_msg = "ConnectionError: timeout after 30s connecting to loging.svc"

        records = await _run_one_iteration(
            exception_message=exc_msg, caplog=caplog,
        )

        assert len(records) == 1
        rendered = records[0].getMessage()

        assert "timeout after 30s" in rendered
        assert "loging.svc" in rendered
        assert "ConnectionError" in rendered

    async def test_log_level_is_warning_not_error(self, caplog):
        """`run_publisher_loop`'s iteration failure — это retry-able event
        (следующий проход подхватит unpublished rows). По project-конвенции
        retry-able failures логируются WARNING, не ERROR, чтобы не
        зашумлять alert'ы. См. TODO «stdout-logger без redact»."""
        records = await _run_one_iteration(
            exception_message="anything", caplog=caplog,
        )

        assert len(records) == 1
        assert records[0].levelno == logging.WARNING, (
            f"уровень {records[0].levelname} != WARNING — поднимет шум в "
            "alert-pipeline'е, retry handle'ится loop'ом"
        )


# ── 3. _runner.py: инвариант «нет logger.*(exc...)» ──────────────────────────


class TestRunnerHasNoRawExceptionLogging:
    """Регрессия: `_runner.py` не должен логировать exception в stdout
    БЕЗ предварительного `redact_error_message`. Текст ошибки идёт в
    `task.last_error` + audit `details.error` (оба прогнаны через
    `redact_error_message`) — а если ещё и в stdout, то обязательно
    через тот же redact.

    `_runner.py` имеет module-level `logger` для операционных сообщений
    (CAS rejection, retry-kick, shutdown-cancel). Эти log'и **не**
    содержат raw exception — либо без exception вовсе, либо exception
    прогнан через redact.
    """

    def test_runner_logger_only_logs_redacted_exceptions(self):
        """Любой `logger.X(...)` вызов в `_runner.py`, в котором
        встречается `exc` или `error_message` без `redact_error_message`,
        — это потенциальная утечка creds (URL basic-auth, Bearer, etc).

        Проверяем source-text эвристически: грубо ищем pattern'ы вида
        ``logger.<level>(..., exc)`` и требуем, чтобы они НЕ
        встречались. Допустимо ``logger.warning("... %s", redacted)``
        где `redacted = redact_error_message(...)`.
        """
        import inspect
        import re

        from src.tasks import _runner

        src = inspect.getsource(_runner)

        # 1. Все вызовы logger'а. Грубое regex'ом — поиск `logger.<word>(`.
        logger_call_pattern = re.compile(r"logger\.(error|warning|exception|info|debug)\s*\(")
        positions = [m.start() for m in logger_call_pattern.finditer(src)]
        assert positions, (
            "Tест-инвариант: ожидался хотя бы один logger.X(...) вызов "
            "в _runner.py (CAS-rejection / retry / shutdown). Если убрал — "
            "перепиши/удали этот тест."
        )

        # 2. Для каждого вызова: проверяем только тело самого вызова
        # `logger.X(...)`. Балансируем скобки чтобы извлечь именно
        # arguments, не лезть в комментарии «except Exception as exc»
        # на следующей строке (раньше тест на этом подпрыгивал, что
        # давало ложные срабатывания).
        for pos in positions:
            # Найти открывающую скобку logger.X(
            open_paren = src.index("(", pos)
            depth = 1
            i = open_paren + 1
            while i < len(src) and depth > 0:
                if src[i] == "(":
                    depth += 1
                elif src[i] == ")":
                    depth -= 1
                i += 1
            call_body = src[open_paren + 1 : i - 1]

            # Запрещаем `, exc)` `, exc,` `% exc` `exc!r` — попадание
            # raw exception в format-args. Comment-pattern'ы (например
            # `except Exception as exc:` на следующей строке) уже не
            # попадают в call_body.
            uses_raw_exc = (
                re.search(r",\s*exc\s*[,)]", call_body)
                or re.search(r"%\s*exc\b", call_body)
                or re.search(r"\bexc\!", call_body)
            )
            if uses_raw_exc:
                # Найден raw-exc — должен быть redact'нут где-то выше
                # (в той же функции). Берём 600 chars ДО вызова и
                # ищем redact_error_message.
                preamble = src[max(0, pos - 600) : pos]
                assert "redact_error_message" in preamble, (
                    f"logger вызов на позиции {pos} логирует raw `exc` без "
                    "предварительного redact_error_message. См. "
                    "audit_outbox_publisher.py как образец паттерна "
                    "`redacted = redact_error_message(...); logger.X(... redacted)`."
                )

    def test_runner_does_not_use_logging_module_directly(self):
        """`logging.error(...)`/`logging.warning(...)` без module-level
        logger — нестандартно и легко пропустить при ревью. Запрещаем."""
        import inspect

        from src.tasks import _runner

        src = inspect.getsource(_runner)
        for forbidden in (
            "logging.error",
            "logging.warning",
            "logging.exception",
        ):
            assert forbidden not in src, (
                f"_runner.py использует `{forbidden}` напрямую — "
                "пользуйся `logger = logging.getLogger(__name__)` + "
                "проверь, что exception-аргументы прогоняются через "
                "redact_error_message."
            )


# ── 4. Pytest-asyncio mode ───────────────────────────────────────────────────

# Все coroutine-тесты выше — async def. Маркер `pytest.mark.asyncio` не
# нужен, потому что проект использует `asyncio_mode = "auto"` (см.
# `pyproject.toml` server_worker). Если в будущем mode поменяется на
# strict — добавить декоратор на классы.
