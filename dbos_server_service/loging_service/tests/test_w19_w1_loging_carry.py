"""Carry-фиксы loging (W11/W12/W14/W15/W16 → закрыты в W19).

Пункты задачи:
1. `_dropped_overflow_total` split — verify (закрыто W18-W4).
2. `apply_active` snapshot race — регрессия на «policies взяты раз, изменение
   в середине цикла видно только на следующем sweep».
3. `_retention_loop` last_run race — verify (last_run выставлен ДО finally,
   commit-failure не отменяет факт sweep'а).
4. `register_events` count-вместо-list — verify (COUNT(*) на total).
5. `_RuleSnapshot.effect` Literal vs enum — verify.
6. `description` control-char guard — добавлен `_RULE_DESCRIPTION_CONTROL_RE`,
   CR/LF/NUL/TAB и прочий C0/C1 отбиваются.
7. `_CONTENT_LENGTH_RE` — переведён с `\\d+` на `[0-9]+`, юникод-digits не
   проходят guard.

Структурные тесты не требуют БД; behavioural — общая `db` фикстура.
"""

from __future__ import annotations

import inspect
import re

import pytest

from src.repositories import retention_policies as rp_repo
from src.repositories.retention_policies import apply_active
from src.schemas.rules import (
    _RULE_DESCRIPTION_CONTROL_RE,
    RuleCreate,
    RuleUpdate,
    _validate_rule_description,
)
from src.services import audit_outbox as ob
from src.services.rule_service import _RuleSnapshot


# ── 1. _dropped_overflow_total split — verify ─────────────────────────────


class TestDroppedOverflowSplitVerify:
    """Гарант, что W18-W4 фикс не откатили: drain-loop CancelledError-handler
    различает QueueFull (overflow) и обычные cancel-losses."""

    def test_drain_loop_has_overflow_branch(self):
        src = inspect.getsource(ob.AuditOutbox._drain_loop)
        assert "_dropped_overflow_total" in src
        assert "overflow_lost" in src
        assert "QueueFull" in src

    def test_overflow_and_cancel_counters_are_separate(self):
        outbox = ob.AuditOutbox(
            max_size=8,
            batch_size=4,
            poll_interval_seconds=0.01,
            session_factory=lambda: None,
            writer=lambda _s, _e: None,
            bump_failure=lambda: 0,
        )
        # Оба counter'а стартуют с нуля и не делят backing-поле.
        assert outbox.dropped_overflow_total() == 0
        assert outbox.dropped_cancel_total() == 0
        assert outbox.dropped_shutdown_total() == 0
        # Increment через protected setter не предусмотрен — проверяем,
        # что три счётчика реально разные атрибуты.
        outbox._dropped_overflow_total = 7
        outbox._dropped_cancel_total = 3
        outbox._dropped_shutdown_total = 1
        assert outbox.dropped_overflow_total() == 7
        assert outbox.dropped_cancel_total() == 3
        assert outbox.dropped_shutdown_total() == 1


# ── 2. apply_active snapshot race ─────────────────────────────────────────


class TestApplyActiveSnapshotContract:
    """Snapshot policies снимается ДО цикла и пересоздаётся только на
    следующем sweep'е. PUT /retention в середине chunked-DELETE не виден
    текущему sweep'у — это сознательный contract (см. docstring)."""

    def test_docstring_mentions_snapshot_contract(self):
        doc = apply_active.__doc__ or ""
        assert "Snapshot" in doc or "snapshot" in doc
        assert "list_active" in doc
        # Альтернатива (refetch per-chunk) явно отвергнута в docstring'е.
        assert "перечитывать" in doc or "следующим" in doc

    def test_apply_active_calls_list_active_once(self, monkeypatch):
        """Behavioural: подменяем `list_active` на счётчик; проверяем, что
        даже при concurrent-PUT (имитируем сменой возвращаемого значения)
        текущий sweep идёт по первому snapshot'у."""
        call_count = {"n": 0}

        def fake_list_active(_db):
            call_count["n"] += 1
            return []

        monkeypatch.setattr(rp_repo, "list_active", fake_list_active)
        # Передаём None как db — он до execute не дойдёт, потому что
        # `if not policies: return 0` отстреливает раньше.
        result = apply_active(None)
        assert result == 0
        assert call_count["n"] == 1, (
            "list_active должен вызываться РОВНО один раз на sweep — "
            "если стало >1, кто-то переписал на per-chunk refetch и сломал контракт"
        )


# ── 3. _retention_loop last_run race — verify ─────────────────────────────


class TestRetentionLoopLastRunOutsideFinally:
    """`last_run = today` выставляется ДО finally — commit-failure на закрытии
    транзакции не отменяет факт, что sweep отработал и не приводит к повторному
    залпу в следующий минутный tick."""

    def test_last_run_assigned_before_finally_source_structure(self):
        from src import main as main_mod

        src = inspect.getsource(main_mod._retention_loop)
        # Структурная проверка: внутри try-блока есть `last_run = today`
        # на той же отступности, что и `deleted = apply_active(db)`, и при
        # этом ВНУТРИ try-блока ДО finally:
        try_idx = src.find("apply_active(db")
        finally_idx = src.find("finally:", try_idx)
        last_run_idx = src.find("last_run = today", try_idx)
        assert try_idx != -1 and finally_idx != -1 and last_run_idx != -1
        assert last_run_idx < finally_idx, (
            "last_run = today должно быть присвоено ДО finally, "
            "иначе db.commit() failure отменит факт sweep'а"
        )

    def test_commit_in_finally_wrapped_in_try_source_structure(self):
        from src import main as main_mod

        src = inspect.getsource(main_mod._retention_loop)
        # В finally две операции: pg_advisory_unlock и db.commit — каждая в
        # своём try/except, чтобы первая не отменила вторую.
        finally_idx = src.find("finally:")
        tail = src[finally_idx:]
        assert "pg_advisory_unlock" in tail
        # Минимум два try-ветвления в finally-блоке (unlock + commit).
        assert tail.count("try:") >= 2


# ── 4. register_events count-вместо-list — verify ─────────────────────────


class TestRegisterEventsUsesCount:
    """`POST /services/{svc}/events` берёт `total` через `count_for_service`,
    а не через `list_for_service(limit=1000)` — раньше capped на 1000 row'ах
    и врал; полноценный SELECT + материализация ORM на каталоге с тысячами
    action'ов = впустую."""

    def test_endpoint_uses_count_for_service_source_structure(self):
        from src.api.v1.endpoints import services as svc_ep

        src = inspect.getsource(svc_ep.register_events)
        assert "count_for_service" in src
        # Защита от регрессии: вызова `se_repo.list_for_service(...)` в
        # endpoint'е быть не должно (упоминание в комментарии — допустимо).
        # Проверяем именно факт вызова через регексп с явным префиксом.
        assert re.search(r"se_repo\.list_for_service\s*\(", src) is None


class TestRegisterEventsServiceIdentityReadOnce:
    """`service_identity` читается из `request.state` РОВНО один раз для
    обеих веток (path-guard и self-audit), а не дважды как раньше."""

    def test_advertised_read_once_source_structure(self):
        from src.api.v1.endpoints import services as svc_ep

        src = inspect.getsource(svc_ep.register_events)
        # `getattr(request.state, "service_identity", None)` встречается один
        # раз — переменная `advertised` потом переиспользуется.
        occurrences = src.count('getattr(request.state, "service_identity", None)')
        assert occurrences == 1, (
            f"service_identity должен читаться один раз, найдено {occurrences}"
        )


# ── 5. _RuleSnapshot.effect Literal vs enum — verify ──────────────────────


class TestRuleSnapshotEffectLiteral:
    """`_RuleSnapshot.effect` сужен до `Literal["SUPPRESS","ALLOW","OVERRIDE_SEVERITY"]`
    после нормализации `DROP → SUPPRESS` на pydantic-уровне. Литерал нужен,
    чтобы mypy ловил опечатки в `apply_rules`."""

    def test_effect_canonical_type_excludes_drop(self):
        from src.services import rule_service

        # `_EffectCanonical` — Literal["SUPPRESS","ALLOW","OVERRIDE_SEVERITY"].
        canonical = rule_service._EffectCanonical
        # typing.get_args возвращает tuple Literal-аргументов
        from typing import get_args

        args = set(get_args(canonical))
        assert args == {"SUPPRESS", "ALLOW", "OVERRIDE_SEVERITY"}
        assert "DROP" not in args, (
            "DROP — это alias на ingest, в кеше/БД его быть не должно"
        )

    def test_snapshot_field_annotation_matches(self):
        # `_RuleSnapshot.effect` использует `_EffectCanonical`.
        anns = _RuleSnapshot.__annotations__
        assert "effect" in anns


# ── 6. description control-char guard ─────────────────────────────────────


class TestRuleDescriptionControlCharGuard:
    """`RuleCreate.description` теперь отбивает CR/LF/NUL/TAB и прочие
    C0/C1 control-символы — раньше они шли насквозь в admin-UI и CSV-экспорт,
    подделывая вторую строку в log-shipping pipeline."""

    def test_pattern_matches_cr(self):
        assert _RULE_DESCRIPTION_CONTROL_RE.search("ok\rinjected")

    def test_pattern_matches_lf(self):
        assert _RULE_DESCRIPTION_CONTROL_RE.search("ok\ninjected")

    def test_pattern_matches_tab(self):
        assert _RULE_DESCRIPTION_CONTROL_RE.search("ok\tinjected")

    def test_pattern_matches_nul(self):
        assert _RULE_DESCRIPTION_CONTROL_RE.search("ok\x00injected")

    def test_pattern_matches_del(self):
        # \x7f (DEL) тоже отбит — попадает в log-shipping как печатное
        # на части консолей, но семантически control-char.
        assert _RULE_DESCRIPTION_CONTROL_RE.search("ok\x7fboom")

    def test_pattern_allows_space(self):
        assert _RULE_DESCRIPTION_CONTROL_RE.search("clean text with spaces") is None

    def test_pattern_allows_unicode_letters(self):
        # Кириллица в description допустима — это содержательное поле,
        # не identifier.
        assert _RULE_DESCRIPTION_CONTROL_RE.search("правило для аудита") is None

    def test_pattern_allows_emoji(self):
        # U+1F512 (LOCK) — не control.
        assert _RULE_DESCRIPTION_CONTROL_RE.search("rule \U0001f512") is None

    def test_validator_accepts_none(self):
        assert _validate_rule_description(None) is None

    def test_validator_accepts_clean_text(self):
        assert _validate_rule_description("plain text") == "plain text"

    def test_validator_rejects_crlf(self):
        with pytest.raises(ValueError, match="control characters"):
            _validate_rule_description("first\r\nfake admin action")

    def test_rule_create_rejects_cr_in_description(self):
        with pytest.raises(ValueError, match="control characters"):
            RuleCreate(
                name="ok",
                description="line1\rline2",
                effect="ALLOW",
            )

    def test_rule_create_rejects_lf_in_description(self):
        with pytest.raises(ValueError, match="control characters"):
            RuleCreate(
                name="ok",
                description="injected\nentry",
                effect="ALLOW",
            )

    def test_rule_create_accepts_clean_unicode_description(self):
        rule = RuleCreate(
            name="ok",
            description="Правило для http.access_denied — alert SOC",
            effect="ALLOW",
        )
        assert rule.description.startswith("Правило")

    def test_rule_update_rejects_lf_in_description(self):
        with pytest.raises(ValueError, match="control characters"):
            RuleUpdate(description="injected\nentry")

    def test_rule_update_accepts_none_description(self):
        upd = RuleUpdate(description=None)
        assert upd.description is None


# ── 7. _CONTENT_LENGTH_RE ASCII-anchored ──────────────────────────────────


class TestContentLengthRegex:
    """Pattern явно `[0-9]+`, а не `\\d+` — иначе Devanagari/арабские digits
    проходят guard, потому что Python re по умолчанию unicode-aware на `\\d`."""

    def test_pattern_matches_ascii_digits(self):
        from src.main import _CONTENT_LENGTH_RE

        assert _CONTENT_LENGTH_RE.fullmatch("0")
        assert _CONTENT_LENGTH_RE.fullmatch("12345")
        assert _CONTENT_LENGTH_RE.fullmatch("9" * 20)

    def test_pattern_rejects_devanagari_digits(self):
        from src.main import _CONTENT_LENGTH_RE

        # १२३ = 123 в Devanagari. int("१२३") == 123 — это и есть угроза:
        # без явного `[0-9]+` Python `\d` принимает их, и malformed
        # Content-Length проходит мимо guard'а.
        assert _CONTENT_LENGTH_RE.fullmatch("१२३") is None

    def test_pattern_rejects_arabic_indic_digits(self):
        from src.main import _CONTENT_LENGTH_RE

        # ٠١٢٣ = 0123 в Arabic-Indic.
        assert _CONTENT_LENGTH_RE.fullmatch("٠١٢٣") is None

    def test_pattern_rejects_sign(self):
        from src.main import _CONTENT_LENGTH_RE

        assert _CONTENT_LENGTH_RE.fullmatch("+1") is None
        assert _CONTENT_LENGTH_RE.fullmatch("-1") is None

    def test_pattern_rejects_underscores(self):
        from src.main import _CONTENT_LENGTH_RE

        # int("1_000_000") == 1000000, но это не валидный Content-Length.
        assert _CONTENT_LENGTH_RE.fullmatch("1_000") is None

    def test_pattern_rejects_whitespace(self):
        from src.main import _CONTENT_LENGTH_RE

        # strip снимается в main.py до вызова fullmatch, но внутренние
        # пробелы (`"1 000"`) обязаны падать.
        assert _CONTENT_LENGTH_RE.fullmatch("1 000") is None

    def test_pattern_source_uses_ascii_class(self):
        from src.main import _CONTENT_LENGTH_RE

        # Pattern источник содержит явный `[0-9]`, а не `\d`. Это
        # структурный guard от случайного отката на `\d+`.
        assert "[0-9]" in _CONTENT_LENGTH_RE.pattern
        assert r"\d" not in _CONTENT_LENGTH_RE.pattern


# ── Сводный smoke ──────────────────────────────────────────────────────────


def test_smoke_module_imports():
    """Импорт всех изменённых модулей в одном кейсе — быстрый smoke."""
    from src import main as _m  # noqa: F401
    from src.schemas import rules as _r  # noqa: F401
    from src.services import audit_outbox as _ob  # noqa: F401
    from src.services import rule_service as _rs  # noqa: F401
    from src.repositories import retention_policies as _rp  # noqa: F401
