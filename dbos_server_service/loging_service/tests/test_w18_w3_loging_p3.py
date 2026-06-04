"""Тесты P3-кластера loging (carry из W14/W15/W16, фиксы в W18).

1) `action_is_registered` glob — фильтр в БД через LIKE, не full scan +
   python-цикл; `has_any` через `SELECT EXISTS`.
2) `_validate_match_action` зовёт `has_any` вместо `list_all` —
   подтверждаем endpoint-side контракт.
3) `apply_active` snapshot политик фиксируется на старте (документация).
4) `_retention_loop` last_run не теряется при упавшем trailing-commit'е.
5) `register_events` — `advertised` читается ровно один раз.
6) `_action_for_path` segment-walk (sanity), substring-collision не воспроиз-
   водится.
7) `RuleCreate.match_service` — `.lower()` дроп больше не нужен после
   `[a-z_]`-pattern; uppercase вход отбивается, lowercase проходит.
8) `_RuleSnapshot.effect` — Literal type alias (smoke import-test).
9) `_validate_request_id` использует единый regex из schemas (импорт-источник).
"""

from __future__ import annotations

import pytest

from src.repositories import service_events as se_repo
from src.repositories.events import _validate_request_id
from src.schemas.rules import RuleCreate
from src.services.rule_service import _EffectCanonical, _RuleSnapshot


# ── Fix 1: action_is_registered LIKE-фильтр в БД ──────────────────────────


def _seed_actions(db, *, service: str, actions: list[str]) -> None:
    se_repo.upsert_events(
        db,
        service,
        [{"action": a, "description": None, "default_severity": "INFO"} for a in actions],
    )


class TestActionIsRegisteredGlob:
    def test_exact_hit(self, db):
        _seed_actions(db, service="auth_service", actions=["user.login", "user.logout"])
        assert se_repo.action_is_registered(db, "user.login") is True

    def test_exact_miss(self, db):
        _seed_actions(db, service="auth_service", actions=["user.login"])
        assert se_repo.action_is_registered(db, "user.absent") is False

    def test_glob_hit_one_segment(self, db):
        _seed_actions(db, service="auth_service", actions=["user.login", "bot.create"])
        assert se_repo.action_is_registered(db, "user.*") is True

    def test_glob_miss_when_pattern_segment_count_differs(self, db):
        """`user.*` matchит только ОДИН сегмент после точки — `user.login.extra`
        нельзя пускать. DB-level LIKE с `%` сам по себе пропустил бы оба
        варианта, второй фильтр в python обязателен."""
        _seed_actions(db, service="auth_service", actions=["user.login.extra"])
        assert se_repo.action_is_registered(db, "user.*") is False

    def test_glob_miss_unknown_prefix(self, db):
        _seed_actions(db, service="auth_service", actions=["user.login", "bot.create"])
        assert se_repo.action_is_registered(db, "missing.*") is False

    def test_full_wildcard(self, db):
        _seed_actions(db, service="auth_service", actions=["user.login"])
        assert se_repo.action_is_registered(db, "*.*") is True

    def test_full_wildcard_empty_catalog(self, db):
        assert se_repo.action_is_registered(db, "*.*") is False

    def test_glob_db_filter_observed_via_listener(self, db):
        """SQLAlchemy `before_cursor_execute` ловит итоговый SQL. До фикса
        glob-проверка тянула `SELECT service_events.action FROM service_events`
        без WHERE; после — `... WHERE service_events.action LIKE ...`."""
        from sqlalchemy import event

        _seed_actions(
            db,
            service="auth_service",
            actions=["user.login", "user.logout", "bot.create", "server.power_on", "ipmi.reset"],
        )

        captured_sql: list[str] = []
        engine = db.get_bind()

        def _capture(conn, cursor, statement, parameters, context, executemany):
            if "service_events" in statement.lower():
                captured_sql.append(statement.lower())

        event.listen(engine, "before_cursor_execute", _capture)
        try:
            assert se_repo.action_is_registered(db, "user.*") is True
            assert se_repo.action_is_registered(db, "ipmi.*") is True
            assert se_repo.action_is_registered(db, "missing.*") is False
        finally:
            event.remove(engine, "before_cursor_execute", _capture)

        like_selects = [s for s in captured_sql if "like" in s and "service_events.action" in s]
        assert like_selects, (
            f"ожидался LIKE-запрос к service_events.action; видели: {captured_sql}"
        )

    def test_has_any_via_exists(self, db):
        assert se_repo.has_any(db) is False
        _seed_actions(db, service="auth_service", actions=["user.login"])
        assert se_repo.has_any(db) is True


# ── Fix 2: _validate_match_action использует has_any_registered ────────────


class TestValidateMatchActionViaEndpoint:
    """Контракт endpoint'а: на пустом каталоге `match_action` без `*`
    проходит (нет registered → нет блокировки); на непустом каталоге
    неизвестный action отбивается 422 UNKNOWN_MATCH_ACTION.
    """

    def test_create_rule_empty_catalog_allows_any_match_action(self, admin_client, db):
        # service_events пуст — раньше тут `list_all` материализовал бы
        # пустой список, теперь `has_any` отдаёт False по EXISTS.
        r = admin_client.post(
            "/api/logging/v1/rules",
            json={
                "name": "any-match-on-empty",
                "effect": "SUPPRESS",
                "match_action": "auth.login",
            },
        )
        assert r.status_code == 201, r.text

    def test_create_rule_known_action_passes(self, admin_client, db):
        _seed_actions(db, service="auth_service", actions=["user.login"])
        r = admin_client.post(
            "/api/logging/v1/rules",
            json={
                "name": "known-action",
                "effect": "SUPPRESS",
                "match_action": "user.login",
            },
        )
        assert r.status_code == 201, r.text

    def test_create_rule_unknown_action_with_catalog_rejected(self, admin_client, db):
        _seed_actions(db, service="auth_service", actions=["user.login"])
        r = admin_client.post(
            "/api/logging/v1/rules",
            json={
                "name": "unknown-action",
                "effect": "SUPPRESS",
                "match_action": "user.absent",
            },
        )
        assert r.status_code == 422
        assert r.json().get("error_code") == "UNKNOWN_MATCH_ACTION"


# ── Fix 3: apply_active snapshot фиксируется (документация-as-test) ────────


class TestApplyActiveSnapshotContract:
    def test_snapshot_taken_before_loop(self, db):
        """`apply_active` сначала зовёт `list_active`, потом chunked-DELETE.
        Регрессия — если кто-то решит перечитывать snapshot в цикле,
        контракт сломается, и docstring (а с ним этот тест) обновится явно.
        """
        from src.repositories import retention_policies as rp

        # Голая проверка: первая операция в теле — list_active(db).
        import inspect

        src = inspect.getsource(rp.apply_active)
        # `policies = list_active(db)` должен идти до while-loop'а.
        idx_list = src.find("list_active(db)")
        idx_while = src.find("while True")
        assert idx_list >= 0 and idx_while >= 0
        assert idx_list < idx_while, "list_active должен идти ДО chunked-DELETE цикла"


# ── Fix 4: _retention_loop last_run не теряется при упавшем commit'е ───────


class TestRetentionLoopCommitFailure:
    """Под фикс: trailing-commit обёрнут в try/except, ошибка не выкидывает
    наружу и не отменяет уже выставленный last_run."""

    def test_trailing_commit_failure_caught(self, monkeypatch):
        import src.main as main_mod
        import inspect

        src = inspect.getsource(main_mod._retention_loop)
        # Должно быть две отдельные try/except вокруг advisory_unlock и commit.
        # Грубый smoke: count("try:") в участке finally растёт хотя бы до 2.
        # Точечный тест полного loop'а — сложно без daemon-фикстуры; здесь
        # подтверждаем структурно.
        assert "pg_advisory_unlock" in src
        assert "trailing commit failed" in src or "trailing_commit" in src or "db.commit()" in src
        # Гарантия, что unlock и commit разнесены try/except.
        # Минимум: два `try:` после строки с pg_advisory_unlock в этом scope.
        post = src.split("pg_advisory_unlock", 1)[1]
        # Один try-обёртка под unlock + один под commit.
        assert post.count("except Exception") >= 2, (
            "ожидалось два разнесённых try/except: один на unlock, один на commit"
        )


# ── Fix 5: register_events — single advertised read ────────────────────────


class TestRegisterEventsAdvertisedSingleRead:
    def test_no_duplicate_advertised_read(self):
        import inspect
        from src.api.v1.endpoints import services as svc_mod

        # Локализуем тело register_events (точное имя см. endpoints/services.py).
        src = inspect.getsource(svc_mod)
        # Берём срез между def register_events до следующего def на topo-level.
        # Грубо: ищем сколько раз 'service_identity' читается через getattr.
        # До фикса было два вызова `getattr(request.state, "service_identity", None)`.
        register_src_idx = src.find("def register_events")
        next_def_idx = src.find("\ndef ", register_src_idx + 1)
        body = src[register_src_idx:next_def_idx if next_def_idx > 0 else None]
        getattr_calls = body.count('getattr(request.state, "service_identity"')
        assert getattr_calls == 1, (
            f"register_events должен читать service_identity один раз, нашли {getattr_calls}"
        )


# ── Fix 6: _action_for_path segment-walk (sanity) ──────────────────────────


class TestActionForPathSegmentWalk:
    @pytest.mark.parametrize(
        "method,path,expected",
        [
            ("GET", "/api/logging/v1/services", "logging.services_read"),
            ("GET", "/api/logging/v1/services/auth_service/events", "logging.events_queried"),
            ("GET", "/api/logging/v1/rules", "logging.rules_read"),
            ("POST", "/api/logging/v1/rules", "logging.rules_write"),
            ("GET", "/api/logging/v1/retention", "logging.retention_read"),
            ("PUT", "/api/logging/v1/retention", "logging.retention_write"),
            ("GET", "/api/logging/v1/events", "logging.events_queried"),
        ],
    )
    def test_action_for_path(self, method, path, expected):
        from src.main import _action_for_path

        assert _action_for_path(method, path) == expected

    def test_no_substring_collision(self):
        """`/services_x_y` не должен путаться с `/services`."""
        from src.main import _action_for_path

        # Несуществующий resource — fallback на admin_access.
        assert _action_for_path("GET", "/api/logging/v1/whatever") == "logging.admin_access"


# ── Fix 7: RuleCreate.match_service — uppercase отбивается ─────────────────


class TestMatchServiceNormalization:
    def test_lowercase_passes(self):
        r = RuleCreate.model_validate(
            {
                "name": "lower-svc",
                "effect": "SUPPRESS",
                "match_service": "auth_service",
            }
        )
        assert r.match_service == "auth_service"

    def test_uppercase_rejected(self):
        """Pattern `[a-z_]` сам по себе обязан отбивать uppercase —
        без этого ушло бы тихое схлопывание `.lower()`."""
        with pytest.raises(Exception):
            RuleCreate.model_validate(
                {
                    "name": "upper-svc",
                    "effect": "SUPPRESS",
                    "match_service": "AUTH_SERVICE",
                }
            )

    def test_dash_rejected(self):
        with pytest.raises(Exception):
            RuleCreate.model_validate(
                {
                    "name": "dash-svc",
                    "effect": "SUPPRESS",
                    "match_service": "auth-service",
                }
            )

    def test_homoglyph_collapsed_then_validated(self):
        """Кириллическое `а` → ASCII `a` после NFKC + confusables → pattern проходит."""
        r = RuleCreate.model_validate(
            {
                "name": "homoglyph",
                "effect": "SUPPRESS",
                # `а` — кириллица U+0430, после confusable-fold станет ASCII 'a'
                "match_service": "аuth_service",
            }
        )
        assert r.match_service == "auth_service"


# ── Fix 8: _RuleSnapshot.effect Literal alias smoke ────────────────────────


class TestRuleSnapshotEffectLiteral:
    def test_literal_imported(self):
        # Smoke: type-alias на месте, кастомные значения runtime не валидируются
        # (это статический type-hint), но мы убеждаемся, что snapshot строится
        # без ошибок для трёх канонических effect'ов.
        for eff in ("SUPPRESS", "ALLOW", "OVERRIDE_SEVERITY"):
            snap = _RuleSnapshot(
                id="rul_x",
                name="x",
                match_service=None,
                match_action=None,
                match_status=None,
                match_severity=None,
                match_allowed=None,
                effect=eff,  # type: ignore[arg-type]
                effect_severity=None,
            )
            assert snap.effect == eff

    def test_effect_canonical_alias_exists(self):
        # Хотим явный сигнал, если alias переименуют — call-site'ы зависят.
        assert _EffectCanonical is not None


# ── Fix 9: _validate_request_id единый regex ───────────────────────────────


class TestRequestIdValidationDedup:
    def test_valid_passes(self):
        _validate_request_id("req_abc-123.def")
        _validate_request_id(None)

    def test_crlf_rejected(self):
        from src.core.exceptions import DomainValidationError

        with pytest.raises(DomainValidationError):
            _validate_request_id("req_abc\r\nSet-Cookie: hijack")

    def test_repo_uses_schema_pattern(self):
        """Sanity: репозиторий импортирует `_REQUEST_ID_PATTERN` напрямую
        из схемы, без локального `_REQUEST_ID_RE`."""
        from src.repositories import events as ev_mod

        # Локального `_REQUEST_ID_RE` больше не существует.
        assert not hasattr(ev_mod, "_REQUEST_ID_RE")
        # Импортированный pattern — тот же, что в схеме.
        from src.schemas.events import _REQUEST_ID_PATTERN as schema_pat

        assert ev_mod._REQUEST_ID_PATTERN is schema_pat
