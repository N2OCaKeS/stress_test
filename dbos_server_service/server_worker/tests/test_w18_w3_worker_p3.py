"""P3 carry-over fixes для server_worker — кластер из шести правок.

Зачем отдельный файл, а не дозалить ассерты в существующие сьюты:
дельта закрывает разнородные точки (UUID-валидация в HTTP-клиенте,
Python-guard для запретных home'ов в SSH, docstring redfish'а, pure-read
для breaker'а, SKIP-LOCKED-комментарий и docstring clock-skew). Один
файл проще держать в обзоре, и в индексе reports легче ссылаться.

Все тесты — unit'ные, БД не дёргают (кроме одного `select` через
inspect на pyproject), Redis — через общий FakeRedis из
`unit/_breaker_test_helpers`.
"""

from __future__ import annotations

import inspect
import re

import pytest

from src.clients import ssh as ssh_client
from src.clients import redfish as redfish_client
from src.core import identifiers
from src.repositories import task as task_repo
from src.services import _breaker_lua, audit_publisher_breaker as cb
from src.services import server_service_client
from src.tasks import _runner
from src.utils.redaction import redact_error_message  # noqa: F401  — import-time проверка зоны

from tests.unit._breaker_test_helpers import (
    FakeRedis,
    frozen_clock_fixture,
    install_fake_redis,
)


# ── item 1: validate_outbox_id в URL-path ───────────────────────────────────


class TestValidateOutboxId:
    """W17-W2 уже добавил `validate_outbox_id`; проверяем, что callers ходят через него."""

    def test_validate_outbox_id_is_imported_from_identifiers(self) -> None:
        assert (
            server_service_client.validate_outbox_id
            is identifiers.validate_outbox_id
        )

    @pytest.mark.parametrize(
        "good",
        [
            "rox_a1b2c3d4e5f6789012345678901234ab",
            "42",  # цифровой short — на случай legacy BIGINT
            "abc_DEF-123",
        ],
    )
    def test_accepts_safe_alphabet(self, good: str) -> None:
        assert identifiers.validate_outbox_id(good) == good

    @pytest.mark.parametrize(
        "bad",
        [
            "../admin",
            "rox_xx/yy",
            "rox xx",
            "rox.xx",
            "rox:xx",
            "",
            "a" * 65,  # длиннее 64 — отбиваем
            "1; DROP TABLE",
        ],
    )
    def test_rejects_unsafe_input(self, bad: str) -> None:
        with pytest.raises(ValueError):
            identifiers.validate_outbox_id(bad)

    def test_finalize_done_calls_validator(self) -> None:
        # Статический check: оба `finalize_*` пропускают outbox_id через
        # validator до подстановки в URL-template.
        src = inspect.getsource(server_service_client.finalize_reencrypt_outbox_done)
        assert "validate_outbox_id" in src
        # Проверка очерёдности: validator зовётся до построения url
        # (иначе SQLi/path-traversal сначала бы попал в f-string).
        assert src.index("validate_outbox_id") < src.index("/done")

    def test_finalize_failed_calls_validator(self) -> None:
        src = inspect.getsource(server_service_client.finalize_reencrypt_outbox_failed)
        assert "validate_outbox_id" in src
        assert src.index("validate_outbox_id") < src.index("/failed")


# ── item 2: rotate_user_password docstring переписан ─────────────────────────


class TestRotateUserPasswordDocstring:
    """Docstring `rotate_user_password` должен явно перечислить три фазы по порядку."""

    def test_docstring_lists_three_phases_in_order(self) -> None:
        doc = redfish_client.RedfishClient.rotate_user_password.__doc__
        assert doc is not None
        # Все три фазы должны быть упомянуты, и apply должен идти первым,
        # verify — между ними, submit — последним.
        # Docstring переносит "submit to\nserver_service" через newline — нормализуем whitespace.
        normalized = " ".join(doc.lower().split())
        apply_pos = normalized.find("apply on bmc")
        verify_pos = normalized.find("verify")
        submit_pos = normalized.find("submit to server_service")
        assert apply_pos != -1, "apply on BMC шаг должен быть назван явно"
        assert verify_pos != -1
        assert submit_pos != -1
        assert apply_pos < verify_pos < submit_pos, (
            "docstring должен описывать порядок apply → verify → submit"
        )

    def test_docstring_warns_about_post_to_server_service_after_verify(self) -> None:
        doc = redfish_client.RedfishClient.rotate_user_password.__doc__ or ""
        # Должна остаться явная отсылка к stash'у в Redis перед apply'ем —
        # иначе caller рискует крэшнуться между apply и submit'ом и
        # потерять plaintext.
        assert "stash" in doc.lower() or "redis" in doc.lower()


# ── item 3: _install_authorized_key target_home guard ────────────────────────


class TestInstallAuthorizedKeyHomeGuard:
    """`target_home` параметр + check'и `_FORBIDDEN_HOMES` уже wired W17-W3."""

    def test_install_authorized_key_accepts_target_home(self) -> None:
        sig = inspect.signature(ssh_client.SshClient._install_authorized_key)
        assert "target_home" in sig.parameters

    def test_forbidden_homes_include_critical_system_paths(self) -> None:
        # Реальный _FORBIDDEN_HOMES содержит non-shell login-target'ы и `/` —
        # пути типа /root/etc регулируются bash-стороной create_user'а.
        forbidden = ssh_client._FORBIDDEN_HOMES
        for path in ("/", "/bin/false", "/sbin/nologin", "/usr/sbin/nologin", "/var/empty", "/dev"):
            assert path in forbidden, f"{path} должен быть в _FORBIDDEN_HOMES"

    def test_install_authorized_key_checks_target_home(self) -> None:
        src = inspect.getsource(ssh_client.SshClient._install_authorized_key)
        # Проверка должна стоять до `await`-команд (т.е. до отправки на хост).
        assert "_FORBIDDEN_HOMES" in src
        guard_pos = src.find("_FORBIDDEN_HOMES")
        first_run_pos = src.find("await")
        assert guard_pos < first_run_pos, (
            "guard на _FORBIDDEN_HOMES должен стоять до отправки SSH-команд"
        )

    def test_provision_pubkey_passes_target_home(self) -> None:
        # Caller-сайт в provision-пути должен пробрасывать home аккаунта
        # вместо None, иначе guard остаётся пустым.
        src = inspect.getsource(ssh_client.SshClient)
        # Минимум одно явное `target_home=home_dir` (W17-W3 wire-up).
        assert "target_home=home_dir" in src


# ── item 4: cancel_timestamp clock-skew документирован ───────────────────────


class TestCancelTimestampClockSkew:
    """Docstring `_cancel_timestamp` и module-doc явно фиксируют NTP-drift trade-off."""

    def test_module_doc_mentions_clock_skew(self) -> None:
        doc = _runner.__doc__ or ""
        # Достаточно ключевых слов — формулировка может меняться.
        assert "clock" in doc.lower() or "ntp" in doc.lower(), (
            "_runner module doc должен явно упоминать clock-skew / NTP"
        )
        assert "server_service" in doc

    def test_cancel_timestamp_doc_names_server_service_clock(self) -> None:
        doc = _runner._cancel_timestamp.__doc__ or ""
        # «server_service clock» / «func.now()» — оба варианта валидны.
        assert "server_service" in doc
        # Источник истины должен быть явно назван — оператор не должен
        # гадать, что показывают дашборды.

    def test_attach_cancel_metadata_writes_both_clocks(self) -> None:
        # Контракт: в details попадают и server_service'ский, и
        # worker'ский timestamp. Без этого SIEM не сможет detect'ить
        # drift'ы.
        src = inspect.getsource(_runner._attach_cancel_metadata)
        assert "cancel_request_received_at" in src
        assert "worker_clock_now" in src


# ── item 5: list_orphaned_running — комментарий о CAS-safety ─────────────────


class TestListOrphanedRunningCASSafety:
    """SELECT без SKIP LOCKED — но в источнике должен быть явный комментарий
    про идемпотентность через CAS в `mark_failed`."""

    def test_source_documents_idempotency_through_mark_failed(self) -> None:
        src = inspect.getsource(task_repo.list_orphaned_running)
        # Достаточно ключевых якорей — текст может эволюционировать.
        assert "mark_failed" in src, (
            "комментарий должен явно ссылаться на mark_failed CAS"
        )
        assert "CAS" in src or "cas" in src.lower()

    def test_source_acknowledges_audit_dup_tradeoff(self) -> None:
        src = inspect.getsource(task_repo.list_orphaned_running)
        # Комментарий должен зафиксировать, что дубль audit-row осознан
        # и считается приемлемым (без этого читатель кода уйдёт чинить
        # «несуществующую» проблему).
        normalized = src.lower()
        assert "audit" in normalized
        assert "trade-off" in normalized or "tradeoff" in normalized or "accept" in normalized


# ── item 6: get_state — pure-read Lua ────────────────────────────────────────


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> type[FakeRedis]:
    return install_fake_redis(monkeypatch, cb)


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> dict:
    return frozen_clock_fixture(monkeypatch, cb)


class TestGetStatePureRead:
    """`get_state()` должен использовать read-only Lua и не транзитить state."""

    def test_get_state_script_constant_exists(self) -> None:
        assert hasattr(_breaker_lua, "GET_STATE_SCRIPT")
        script = _breaker_lua.GET_STATE_SCRIPT
        assert isinstance(script, str)
        # Read-only-инвариант: скрипт НЕ должен делать ни SET, ни INCR,
        # ни DEL.
        # Допустимы только GET и TTL.
        forbidden = re.findall(
            r"redis\.call\(\s*'(SET|INCR|DEL|EXPIRE|PEXPIRE|SETNX|HSET)'",
            script,
        )
        assert not forbidden, (
            f"GET_STATE_SCRIPT не должен мутировать Redis; найдено: {forbidden}"
        )

    def test_get_state_uses_get_state_script(self) -> None:
        src = inspect.getsource(cb.get_state)
        assert "_GET_STATE_SCRIPT" in src
        # И НЕ должен звать _CHECK_SCRIPT — иначе вернётся прежняя
        # мутирующая семантика.
        assert "_CHECK_SCRIPT" not in src

    async def test_closed_state_returns_closed(self, fake_redis, frozen_clock) -> None:
        state, retry = await cb.get_state()
        assert state == "closed"
        assert retry == 0.0

    async def test_open_state_returns_open_with_remaining_cooldown(
        self, fake_redis, frozen_clock,
    ) -> None:
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()
        state, retry = await cb.get_state()
        assert state == "open"
        assert 0 < retry <= cb.DEFAULT_COOLDOWN_SECONDS

    async def test_get_state_does_not_transition_open_to_half_open(
        self, fake_redis, frozen_clock,
    ) -> None:
        """Главное отличие от прежнего поведения: read-only get_state НЕ
        захватывает probe-slot и НЕ переводит open → half_open после
        истечения cooldown'а."""
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()

        # Прокручиваем clock за cooldown — cooldown истёк, но мы хотим
        # увидеть «всё ещё open, retry_after=0», а не «half_open».
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 5
        state, retry = await cb.get_state()
        assert state == "open", (
            "get_state не должен сам транзитить в half_open — это работа check()"
        )
        assert retry == 0.0

        # И raw-state в FakeRedis тоже остаётся open (никакого побочного SET).
        keys = cb._keys()
        raw_state = fake_redis._store.get(keys[1])
        assert raw_state == "open"
        # Probe-ключ НЕ должен быть захвачен.
        assert cb._probe_key() not in fake_redis._store

    async def test_get_state_does_not_consume_probe_slot(
        self, fake_redis, frozen_clock,
    ) -> None:
        """После N подряд `get_state()` — `check()` всё ещё может
        захватить probe-slot и пропустить пробный запрос."""
        for _ in range(cb.DEFAULT_FAILURE_THRESHOLD):
            await cb.record_failure()
        frozen_clock["now"] += cb.DEFAULT_COOLDOWN_SECONDS + 1

        for _ in range(5):
            state, _ = await cb.get_state()
            assert state == "open"

        # `check()` должен пропустить нас в half_open (probe-slot всё ещё свободен).
        try:
            await cb.check()
        except cb.CircuitBreakerOpenError:
            pytest.fail("check() должен был захватить probe-slot, а отбил запрос")

        # Теперь state — half_open.
        keys = cb._keys()
        assert fake_redis._store.get(keys[1]) == "half_open"

    async def test_get_state_fail_open_on_redis_error(
        self, monkeypatch, fake_redis, frozen_clock,
    ) -> None:
        """Если Redis-eval бросает — возвращаем `("closed", 0.0)`, не пробрасываем."""
        from redis.exceptions import RedisError

        async def boom_eval(*args, **kw):
            raise RedisError("redis down")

        # Подменяем eval на FakeRedis-instance, который вернётся из _get_client.
        original_get_client = cb._get_client

        async def faulty_client():
            client = await original_get_client()
            client.eval = boom_eval  # type: ignore[method-assign]
            return client

        monkeypatch.setattr(cb, "_get_client", faulty_client)

        state, retry = await cb.get_state()
        assert state == "closed"
        assert retry == 0.0


# ── item 7: SCRUBBED_SENTINEL импортируется в _unscrub ───────────────────────


class TestScrubbedSentinelImport:
    """`_unscrub` должен сверять value с импортированной константой,
    не с literal-строкой."""

    def test_users_module_imports_sentinel(self) -> None:
        from src.tasks import users
        from src.core import constants

        assert hasattr(users, "SCRUBBED_SENTINEL")
        assert users.SCRUBBED_SENTINEL is constants.SCRUBBED_SENTINEL

    def test_unscrub_compares_against_constant(self) -> None:
        from src.tasks import users
        src = inspect.getsource(users._unscrub)
        # Сравнение должно идти именно через SCRUBBED_SENTINEL,
        # а не через хардкод "<scrubbed>".
        assert "SCRUBBED_SENTINEL" in src
        # Литерал в коде допустим в комментарии, но не в `==`.
        assert '== "<scrubbed>"' not in src
        assert "== '<scrubbed>'" not in src
