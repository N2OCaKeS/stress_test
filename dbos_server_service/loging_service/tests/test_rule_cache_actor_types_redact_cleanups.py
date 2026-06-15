"""Тесты под пять точечных чисток loging_service.

Покрывают:

1. `_RuleCache` не делает лишний SELECT active_sorted, когда БД пустая
   на двух последовательных tick'ах после истечения TTL.
2. `service_events.upsert_events(..., commit=False)` не вызывает
   `db.commit()` — выравнивает поведение с `events.insert` / `rules.create`
   / `retention_policies.create_policy`.
3. `VALID_ACTOR_TYPES` — единый frozenset в `src/core/constants.py`,
   используется и `main`, и `audit_outbox`, согласован с `EventCreate.actor_type`.
4. `redact()` — top-level скаляр-строка возвращается как есть (документация).
5. `_TOKEN_KEYS` / `_SECRET_KEYS` — `bearer` живёт только в `_TOKEN_KEYS`,
   `_classify_key("bearer")` стабильно возвращает `<TOKEN>`.
"""

from __future__ import annotations

from typing import get_args

from src.repositories import rules as rule_repo
from src.repositories import service_events as se_repo
from src.schemas.rules import RuleCreate
from src.services import rule_service
from src.services.rule_service import _RuleCache


# ── 1. _RuleCache: пустая БД не тянет SELECT active_sorted каждый tick ───────


class TestRuleCacheEmptyDbNoExtraReload:
    def test_empty_db_second_tick_skips_active_select(self, db, monkeypatch):
        """На пустой БД второй tick (после TTL) не должен дёргать
        `get_active_sorted` — MAX(updated_at) уже был NULL на прошлом tick'е,
        состояние не изменилось.
        """
        cache = _RuleCache(ttl_seconds=0)
        # Первый load: фиксируем `_db_empty = True`.
        assert cache.get(db) == []

        active_calls: list[int] = []
        original_active = rule_repo.get_active_sorted
        monkeypatch.setattr(
            rule_repo,
            "get_active_sorted",
            lambda d: (active_calls.append(1), original_active(d))[1],
        )

        # TTL=0 + любая ненулевая monotonic-дельта → cache.get форсит
        # refresh-попытку. monotonic() имеет наносекундное разрешение в
        # CPython — отдельный `time.sleep` не нужен, два последовательных
        # `cache.get(db)` гарантированно идут по slow-path с MAX-touch'ем.
        # Второй tick — БД всё ещё пустая, MAX = NULL.
        assert cache.get(db) == []
        # И третий — то же самое.
        assert cache.get(db) == []

        assert active_calls == [], (
            "На стабильной пустой БД get_active_sorted не должен вызываться повторно, "
            f"было: {active_calls}"
        )

    def test_first_rule_after_empty_triggers_reload(self, db):
        """Как только в пустую БД добавили правило — кеш подтягивает его."""
        cache = _RuleCache(ttl_seconds=0)
        assert cache.get(db) == []

        # `rule_repo.create` коммитит — DB-side NOW() для `updated_at` фиксируется
        # в момент INSERT'а и доступен следующему cache.get без sleep'а: cache
        # читает MAX(updated_at), сравнивает с `_last_db_max=None`, идёт по
        # slow-path get_active_sorted.
        payload = RuleCreate(name="r1", effect="SUPPRESS", priority=100)
        rule_repo.create(db, payload)

        rules = cache.get(db)
        assert [r.name for r in rules] == ["r1"]


# ── 2. service_events.upsert_events: commit=False не коммитит ────────────────


class TestUpsertEventsCommitFlag:
    def test_commit_false_does_not_commit(self, db, TestSessionLocal):
        """С `commit=False` транзакция должна остаться открытой —
        rollback в той же сессии откатывает upsert."""
        added, updated = se_repo.upsert_events(
            db,
            "upsert_commit_svc",
            [{"action": "x.y", "description": "d", "default_severity": "INFO"}],
            commit=False,
        )
        assert added == 1
        assert updated == 0

        # Rollback — событие не должно сохраниться.
        db.rollback()

        # Проверяем в свежей сессии (своя транзакция).
        s2 = TestSessionLocal()
        try:
            rows, total = se_repo.list_for_service(s2, "upsert_commit_svc")
            assert total == 0, "commit=False + rollback должно убрать запись"
        finally:
            s2.close()

    def test_commit_true_persists(self, db, TestSessionLocal):
        """Без аргумента (default `commit=True`) — запись сохраняется."""
        se_repo.upsert_events(
            db,
            "upsert_persist_svc",
            [{"action": "a.b", "description": "d", "default_severity": "INFO"}],
        )
        s2 = TestSessionLocal()
        try:
            _, total = se_repo.list_for_service(s2, "upsert_persist_svc")
            assert total == 1
        finally:
            s2.close()


# ── 3. VALID_ACTOR_TYPES в core.constants, без дубля ─────────────────────────


class TestValidActorTypesConstant:
    def test_constants_module_exports_frozenset(self):
        from src.core.constants import VALID_ACTOR_TYPES

        assert isinstance(VALID_ACTOR_TYPES, frozenset)
        assert VALID_ACTOR_TYPES == frozenset(
            {"user", "bot", "service", "anonymous", "oauth_client"}
        )

    def test_main_uses_constant(self):
        """`main.py` не держит локального дубля whitelist'а: резолв `actor_type`
        делегирован `audit_outbox._resolve_actor_type`, который сам читает
        `VALID_ACTOR_TYPES` из constants. Раньше `main` импортировал константу
        и повторял if/else руками — теперь это один helper в одном месте.
        """
        from src import main as main_mod

        # Локальный дубль whitelist'а в main.py запрещён.
        assert not hasattr(main_mod, "_VALID_ACTOR_TYPES")
        # `_emit_audit` ходит через единый helper.
        assert main_mod._resolve_actor_type is not None
        # Сам helper читает источник истины из constants.
        from src.core.constants import VALID_ACTOR_TYPES
        from src.services import audit_outbox
        assert audit_outbox.VALID_ACTOR_TYPES is VALID_ACTOR_TYPES

    def test_audit_outbox_uses_constant(self):
        """`audit_outbox` тоже идёт через единый константный набор."""
        from src.core.constants import VALID_ACTOR_TYPES
        from src.services import audit_outbox

        assert audit_outbox.VALID_ACTOR_TYPES is VALID_ACTOR_TYPES
        assert not hasattr(audit_outbox, "_VALID_ACTOR_TYPES")

    def test_constant_matches_event_create_literal(self):
        """`EventCreate.actor_type` Literal — источник истины для Pydantic-
        валидации. Frozenset обязан совпадать байт-в-байт."""
        from src.schemas.events import EventCreate
        from src.core.constants import VALID_ACTOR_TYPES

        literal_args = set(get_args(EventCreate.model_fields["actor_type"].annotation))
        assert set(VALID_ACTOR_TYPES) == literal_args


# ── 4. redact: top-level scalar passthrough — задокументировано ──────────────


class TestRedactTopLevelScalarPassthrough:
    def test_top_level_string_returned_as_is(self):
        """`redact("Bearer abc.def.xyz")` возвращает строку без изменений —
        контракт `redact` объявлен только для контейнеров (dict/list)."""
        from src.utils.redaction import redact

        s = "Bearer abc12345.def67890.xyz09876"
        assert redact(s) == s

    def test_top_level_int_returned_as_is(self):
        from src.utils.redaction import redact

        assert redact(42) == 42

    def test_inside_dict_value_is_still_masked(self):
        """Внутри dict masking работает — это и есть use-case."""
        from src.utils.redaction import redact

        out = redact({"token": "abcdef"})
        assert out == {"token": "<TOKEN>"}


# ── 5. bearer только в _TOKEN_KEYS ───────────────────────────────────────────


class TestBearerSingleClassification:
    def test_bearer_not_in_secret_keys(self):
        from src.utils.redaction import _SECRET_KEYS, _TOKEN_KEYS

        assert "bearer" in _TOKEN_KEYS
        assert "bearer" not in _SECRET_KEYS, (
            "bearer был дублирован в _SECRET_KEYS — мёртвая ветка, "
            "_classify_key всегда отдавал <TOKEN> по приоритету"
        )

    def test_bearer_classifies_as_token(self):
        from src.utils.redaction import _classify_key

        assert _classify_key("bearer") == "<TOKEN>"
        assert _classify_key("Bearer") == "<TOKEN>"

    def test_bearer_in_dict_masked_as_token(self):
        from src.utils.redaction import redact

        assert redact({"bearer": "anything"}) == {"bearer": "<TOKEN>"}
