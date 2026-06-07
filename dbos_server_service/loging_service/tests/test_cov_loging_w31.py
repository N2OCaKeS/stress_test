"""Coverage gaps loging_service — W31 P4 pass.

Восемь edge-case'ов, оставшихся непокрытыми после W26 (нормальные cov-волны
закрыли основные ветки, эти остались в углах):

  1. `_fetch_identity` runtime-ветка `INTROSPECT_KEY_NOT_CONFIGURED` —
     `introspect_service_api_key` пуст уже после старта (env-override через
     monkeypatch); endpoint должен отдать 503, а не молча подделать запрос.

  2. COUNT-side `_with_statement_timeout` с pgcode через `.value`-атрибут
     (psycopg2 enum-обёртка); SELECT-side покрыт w12, COUNT — нет.

  3. `_drain_loop` падает не-Cancel-исключением на `queue.get()` (broken
     queue); тело логирует и выходит, не пробрасывая наверх — иначе
     lifespan'у нечем перехватить и pod ронит rolling-restart.

  4. `_classify_key` matches uppercase keys через lower()-нормализацию —
     `details={"Password": "x"}` маскируется так же, как `password`.

  5. `apply_active` с двумя непересекающимися политиками (severity-only +
     service-only); total равен размеру объединения, без двойного учёта.

  6. `_action_for_path` сегмент `events` под `/services/{svc}/...` всегда
     даёт `logging.events_queried` (catalog read), даже когда resource —
     `services`; backward-compat с substring-эпохой.

  7. `_RuleCache.invalidate` сбрасывает `_last_db_max` (введён в W26 как
     основной watermark вместо `_loaded_at`); без сброса cross-worker
     UPDATE не подхватывается после явного `invalidate_cache()`.

  8. `apply_rules` с `payload.service="loging_service"` обходит rule
     engine целиком — даже при наличии SUPPRESS/OVERRIDE правил
     `match_service="loging_service"` либо `match_action="*"`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import DBAPIError

from src.services.audit_outbox import AuditOutbox


EVENTS_URL = "/api/logging/v1/events"


class _OkSession:
    def begin_nested(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def commit(self):
        pass

    def close(self):
        pass


# ── 1. INTROSPECT_KEY_NOT_CONFIGURED runtime ─────────────────────────────────


class TestIntrospectKeyNotConfiguredRuntime:
    """Если `INTROSPECT_SERVICE_API_KEY` пуст на момент запроса
    (env-override после старта), `_fetch_identity` должен вернуть 503
    `INTROSPECT_KEY_NOT_CONFIGURED`, а не уйти в introspect с пустым Bearer.
    """

    def test_empty_introspect_key_returns_503(self, client, monkeypatch):
        # Settings уже загружен с непустым ключом (conftest fixture). Подменяем
        # сам атрибут на settings-объекте, без `get_settings.cache_clear()` —
        # production-guard на старте мы не трогаем, проверяем именно runtime
        # raise.
        from src.core.config import get_settings

        s = get_settings()
        monkeypatch.setattr(s, "introspect_service_api_key", "")

        r = client.get(EVENTS_URL, headers={"Authorization": "Bearer token-anything"})
        assert r.status_code == 503, r.text
        assert r.json()["error_code"] == "INTROSPECT_KEY_NOT_CONFIGURED"


# ── 2. COUNT 57014 через pgcode.value (psycopg2 enum) ────────────────────────


class TestCountStatementTimeoutViaValueAttr:
    """`_with_statement_timeout` на COUNT-ветке должен ловить 57014 и через
    `getattr(orig.pgcode, "value", None)`. SELECT-side покрыт w12, COUNT
    нужен симметричный тест.
    """

    def _insert_event(self, db):
        from src.models.audit_event import AuditEvent
        from src.utils.ids import audit_event_id

        row = AuditEvent(
            id=audit_event_id(),
            timestamp=datetime.now(timezone.utc),
            service="auth_service",
            action="user.login",
            actor_type="user",
            status="success",
            allowed=True,
            severity="INFO",
        )
        db.add(row)
        db.commit()

    def test_count_57014_via_value_attr_returns_total_none(self, db, monkeypatch):
        from src.core.config import get_settings
        from src.repositories import events as events_repo

        monkeypatch.setenv("AUDIT_COUNT_STATEMENT_TIMEOUT_MS", "5000")
        get_settings.cache_clear()
        try:
            self._insert_event(db)

            original_execute = db.execute
            state = {"count_calls": 0}

            def patched_execute(clause, *args, **kwargs):
                sql_text = str(getattr(clause, "text", clause))
                sql_lower = sql_text.lower()
                # Перехватываем именно plain COUNT, не SET LOCAL и не SHOW.
                if (
                    "count" in sql_lower
                    and "set local" not in sql_lower
                    and "show" not in sql_lower
                    and state["count_calls"] == 0
                ):
                    state["count_calls"] += 1
                    inner = MagicMock()
                    # Без str-pgcode, только enum-обёртка с `.value` — это
                    # путь, которым psycopg2 кладёт SQLSTATE.
                    inner.pgcode = MagicMock()
                    inner.pgcode.value = "57014"
                    raise DBAPIError(
                        statement="SELECT count(*)",
                        params={},
                        orig=inner,
                    )
                return original_execute(clause, *args, **kwargs)

            monkeypatch.setattr(db, "execute", patched_execute)

            events_list, total, has_more = events_repo.query(db, include_total=True)
            assert total is None
            assert len(events_list) >= 1
        finally:
            get_settings.cache_clear()


# ── 3. _drain_loop fatal Exception на queue.get() ───────────────────────────


class TestDrainLoopFatalException:
    """Если очередь сломалась (например, `_queue.get` бросает RuntimeError —
    GC/closed loop), `_drain_loop` логирует и выходит, не пробрасывая наверх.
    Lifespan ждёт graceful shutdown; необработанное исключение из drain-task
    превращалось бы в `asyncio.exceptions.InvalidStateError` на await stop().
    """

    def test_broken_queue_logs_and_exits(self, caplog):
        async def run():
            outbox = AuditOutbox(
                max_size=2,
                batch_size=1,
                poll_interval_seconds=0.01,
                session_factory=_OkSession,
                writer=lambda db, env: None,
                bump_failure=lambda: 0,
            )
            outbox._loop = asyncio.get_running_loop()

            class _BrokenQueue:
                def qsize(self):
                    return 0

                async def get(self):
                    raise RuntimeError("queue is closed")

                def get_nowait(self):
                    raise asyncio.QueueEmpty

                def put_nowait(self, _):
                    raise asyncio.QueueFull

            outbox._queue = _BrokenQueue()  # type: ignore[assignment]

            with caplog.at_level(logging.ERROR, logger="src.services.audit_outbox"):
                task = asyncio.get_running_loop().create_task(outbox._drain_loop())
                # Loop сразу свалится на первом get() → except Exception
                # логирует и выходит. Даём пару tick'ов на завершение.
                for _ in range(20):
                    if task.done():
                        break
                    await asyncio.sleep(0.01)
                assert task.done(), "drain_loop должен сам завершиться на RuntimeError из queue"
                # exception должно остаться внутри (не пробрасывается).
                assert task.exception() is None

            return [r.message for r in caplog.records]

        messages = asyncio.run(run())
        assert any("drain loop crashed" in m for m in messages), (
            f"expected 'drain loop crashed' log, got {messages}"
        )


# ── 4. _classify_key uppercase normalization ─────────────────────────────────


class TestClassifyKeyUppercase:
    """`_classify_key` зовёт `key.lower()` ДО lookup'а — `Password`/`TOKEN`/
    `Secret` маскируются так же, как канонические нижне-регистровые имена.
    Без этой ветки атакующий мог бы прислать `{"Password": "..."}` и
    обойти secret-маскировку (а потом — log-shipping в SIEM выведет
    plaintext).
    """

    def test_uppercase_password_key_redacted(self):
        from src.utils.redaction import redact

        out = redact({"Password": "hunter2"})
        assert out == {"Password": "<PASSWORD>"}

    def test_mixed_case_token_key_redacted(self):
        from src.utils.redaction import redact

        out = redact({"Bearer_Token": "eyJ..."})
        assert out == {"Bearer_Token": "<TOKEN>"}

    def test_uppercase_secret_key_redacted(self):
        from src.utils.redaction import redact

        # `SECRET` верхним регистром (env-var convention).
        out = redact({"API_SECRET": "abc"})
        assert out == {"API_SECRET": "<SECRET>"}


# ── 5. apply_active с двумя непересекающимися политиками ─────────────────────


class TestApplyActiveMultiPolicyOr:
    """`apply_active` собирает все активные политики в одно OR-выражение —
    одно событие, попавшее под несколько политик, удаляется один раз, а
    счётчик `total` отражает уникальный размер набора, не сумму per-policy
    rowcount'ов. Регрессия N-pass подхода (W14): два прохода считали
    одно событие дважды, в SIEM ехало завышенное число.
    """

    def _add_event(self, db, *, service: str, severity: str, days_ago: int):
        from src.models.audit_event import AuditEvent
        from datetime import timedelta
        from src.utils.ids import audit_event_id

        row = AuditEvent(
            id=audit_event_id(),
            timestamp=datetime.now(timezone.utc) - timedelta(days=days_ago),
            service=service,
            action="x.y",
            actor_type="user",
            status="success",
            allowed=True,
            severity=severity,
        )
        db.add(row)
        db.commit()
        return row

    def test_disjoint_policies_count_unique_rows(self, db):
        from src.models.retention_policy import RetentionPolicy
        from src.repositories.retention_policies import apply_active

        # Две независимые политики:
        #   P1: retain_days=10, severity=CRITICAL (все сервисы)
        #   P2: retain_days=10, service=auth_service (все severity)
        # События:
        #   E1: auth_service / CRITICAL / 30 дней назад — матчит обе политики
        #   E2: auth_service / INFO     / 30 дней назад — только P2
        #   E3: server_service / CRITICAL / 30 дней назад — только P1
        #   E4: server_service / INFO  / 5 дней назад — ни одну (свежий)
        now = datetime.now(timezone.utc)
        p1 = RetentionPolicy(
            severity="CRITICAL", service=None, retain_days=10,
            description=None, is_active=True, created_at=now, updated_at=now,
        )
        p2 = RetentionPolicy(
            severity=None, service="auth_service", retain_days=10,
            description=None, is_active=True, created_at=now, updated_at=now,
        )
        db.add_all([p1, p2])
        db.commit()

        self._add_event(db, service="auth_service", severity="CRITICAL", days_ago=30)
        self._add_event(db, service="auth_service", severity="INFO", days_ago=30)
        self._add_event(db, service="server_service", severity="CRITICAL", days_ago=30)
        self._add_event(db, service="server_service", severity="INFO", days_ago=5)

        deleted = apply_active(db)
        # E1, E2, E3 — три уникальных события под объединением политик.
        # E4 не попадает (5 < 10 дней). E1 матчит обе — но считаем один раз.
        assert deleted == 3


# ── 6. _action_for_path: финальный сегмент events под /services/.../events ──


class TestActionForPathServicesEventsSuffix:
    """`_action_for_path` мапит `/services/{svc}/events` в отдельный action
    `logging.service_events_browsed` — это каталог зарегистрированных
    action'ов сервиса, не чтение audit-журнала. SOC-фильтр по
    `logging.events_queried` ловит только journal-чтения.
    """

    def test_services_subresource_events_returns_service_events_browsed(self):
        from src.main import _action_for_path

        assert (
            _action_for_path("GET", "/api/logging/v1/services/auth_service/events")
            == "logging.service_events_browsed"
        )

    def test_services_subresource_events_with_trailing_slash(self):
        from src.main import _action_for_path

        # `/events/` — финальный пустой сегмент отфильтрован, реальный
        # последний — `events`.
        assert (
            _action_for_path("GET", "/api/logging/v1/services/auth_service/events/")
            == "logging.service_events_browsed"
        )

    def test_top_level_services_without_events_returns_services_read(self):
        from src.main import _action_for_path

        # Лист services без /events суффикса — это листинг каталога.
        assert (
            _action_for_path("GET", "/api/logging/v1/services") == "logging.services_read"
        )


# ── 7. _RuleCache.invalidate clears _last_db_max ─────────────────────────────


class TestRuleCacheInvalidateClearsLastDbMax:
    """W26 ввёл `_last_db_max` — основной watermark изменений вместо
    `_loaded_at` (NTP-skew fix). `invalidate()` обязан сбросить ЕГО ТОЖЕ,
    иначе после явного `invalidate_cache()` ветка `changed` сравнит свежий
    `db_updated_at` с замороженным `_last_db_max` и может не подтянуть
    cross-worker UPDATE, прилетевший прямо перед invalidate'ом.
    """

    def test_invalidate_resets_last_db_max(self):
        from src.services.rule_service import _RuleCache

        cache = _RuleCache(ttl_seconds=30)
        # Имитируем prosperous состояние: cache читал из БД, MAX зафиксирован.
        cache._last_db_max = datetime.now(timezone.utc)
        cache._loaded_at = datetime.now(timezone.utc)
        cache._loaded_monotonic = 0.0

        cache.invalidate()

        assert cache._last_db_max is None, (
            "invalidate() обязан сбросить _last_db_max — иначе следующий "
            "get() сравнит fresh db_updated_at с устаревшим watermark'ом и "
            "может пропустить cross-worker UPDATE"
        )
        assert cache._loaded_at is None
        assert cache._loaded_monotonic is None


# ── 8. apply_rules self-audit bypass для loging_service ──────────────────────


class TestApplyRulesLogingServiceBypass:
    """`apply_rules(payload)` с `payload.service == "loging_service"` возвращает
    payload без обращения к rule engine — defence-in-depth от «admin создал
    SUPPRESS match_service='loging_service' и отключил собственный аудит».
    Тест проверяет, что даже SUPPRESS правило не подавляет self-audit, и
    severity self-audit'а не переписывается OVERRIDE'ом.
    """

    def test_suppress_match_service_loging_service_does_not_drop(self, monkeypatch):
        from src.services import rule_service
        from src.schemas.events import EventCreate

        # Подменяем cache на список «вечно подавлять loging_service».
        snap = type(
            "Snap",
            (),
            {
                "effect": "SUPPRESS",
                "effect_severity": None,
                "match_service": "loging_service",
                "match_action": None,
                "match_status": None,
                "match_severity": None,
                "match_allowed": None,
                "priority": 100,
            },
        )()

        class _StubCache:
            def get(self, _db):
                return [snap]

        monkeypatch.setattr(rule_service, "_cache", _StubCache())

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging.events_queried",
            actor_type="user",
            status="success",
            allowed=True,
            severity="INFO",
        )

        out = rule_service.apply_rules(None, payload)
        # Self-audit НЕ подавлен SUPPRESS-правилом.
        assert out is payload, (
            "self-audit для loging_service должен возвращаться без обращения "
            "к rule engine — SUPPRESS не имеет права обнулять audit-канал"
        )

    def test_override_severity_does_not_rewrite_self_audit(self, monkeypatch):
        from src.services import rule_service
        from src.schemas.events import EventCreate

        snap = type(
            "Snap",
            (),
            {
                "effect": "OVERRIDE_SEVERITY",
                "effect_severity": "TRACE",  # понизить чувствительность self-audit
                "match_service": "loging_service",
                "match_action": None,
                "match_status": None,
                "match_severity": None,
                "match_allowed": None,
                "priority": 100,
            },
        )()

        class _StubCache:
            def get(self, _db):
                return [snap]

        monkeypatch.setattr(rule_service, "_cache", _StubCache())

        payload = EventCreate(
            timestamp=datetime.now(timezone.utc),
            service="loging_service",
            action="logging.rules_write",
            actor_type="user",
            status="success",
            allowed=True,
            severity="CRITICAL",
        )

        out = rule_service.apply_rules(None, payload)
        assert out is payload
        assert out.severity == "CRITICAL", (
            "OVERRIDE_SEVERITY не имеет права понижать severity self-audit'а — "
            "иначе admin прячет собственные действия в TRACE-канале"
        )
