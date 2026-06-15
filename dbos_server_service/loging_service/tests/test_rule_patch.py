"""Тесты edge cases для PATCH /api/logging/v1/rules/{id}.

Покрывают переходы effect/effect_severity, валидацию match_action в PATCH,
обработку конфликта имён (409 вместо 500) и инвалидацию кеша после PATCH.
"""

from tests.conftest import make_rule, make_event_def

RULES_URL = "/api/logging/v1/rules"
SERVICES_URL = "/api/logging/v1/services"
EVENTS_URL = "/api/logging/v1/events"


# ── Переходы effect / effect_severity ────────────────────────────────────────

class TestPatchEffectSeverityTransitions:
    def test_override_to_suppress_clears_effect_severity(self, admin_client):
        """OVERRIDE_SEVERITY → SUPPRESS с явным effect_severity=null допустим."""
        created = admin_client.post(RULES_URL, json=make_rule(
            name="t1", effect="OVERRIDE_SEVERITY", effect_severity="CRITICAL",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"effect": "SUPPRESS", "effect_severity": None},
        )
        assert r.status_code == 200
        assert r.json()["effect"] == "SUPPRESS"
        assert r.json()["effect_severity"] is None

    def test_override_to_suppress_without_clearing_severity_rejected(self, admin_client):
        """OVERRIDE_SEVERITY → SUPPRESS без сброса effect_severity → 422."""
        created = admin_client.post(RULES_URL, json=make_rule(
            name="t2", effect="OVERRIDE_SEVERITY", effect_severity="CRITICAL",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"effect": "SUPPRESS"},
        )
        assert r.status_code == 422

    def test_suppress_to_override_requires_effect_severity(self, admin_client):
        """SUPPRESS → OVERRIDE_SEVERITY без effect_severity → 422."""
        created = admin_client.post(RULES_URL, json=make_rule(
            name="t3", effect="SUPPRESS",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"effect": "OVERRIDE_SEVERITY"},
        )
        assert r.status_code == 422
        assert "effect_severity" in r.json()["message"]

    def test_suppress_to_override_with_severity_ok(self, admin_client):
        created = admin_client.post(RULES_URL, json=make_rule(
            name="t4", effect="SUPPRESS",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"effect": "OVERRIDE_SEVERITY", "effect_severity": "WARNING"},
        )
        assert r.status_code == 200
        assert r.json()["effect"] == "OVERRIDE_SEVERITY"
        assert r.json()["effect_severity"] == "WARNING"

    def test_clear_effect_severity_on_override_rule_rejected(self, admin_client):
        """Для существующего OVERRIDE_SEVERITY попытка обнулить effect_severity → 422."""
        created = admin_client.post(RULES_URL, json=make_rule(
            name="t5", effect="OVERRIDE_SEVERITY", effect_severity="CRITICAL",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"effect_severity": None},
        )
        assert r.status_code == 422

    def test_change_only_effect_severity_keeps_override(self, admin_client):
        created = admin_client.post(RULES_URL, json=make_rule(
            name="t6", effect="OVERRIDE_SEVERITY", effect_severity="WARNING",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"effect_severity": "CRITICAL"},
        )
        assert r.status_code == 200
        assert r.json()["effect"] == "OVERRIDE_SEVERITY"
        assert r.json()["effect_severity"] == "CRITICAL"


# ── Валидация match_action на PATCH (гейт против реестра) ────────────────────

class TestPatchMatchActionValidation:
    def test_patch_unregistered_action_rejected_when_registry_nonempty(
        self, client, admin_client, auth_headers
    ):
        """PATCH с match_action='не_зарег' при непустом реестре → 422."""
        # Регистрируем известное событие
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        created = admin_client.post(RULES_URL, json=make_rule(
            name="patch-bad", effect="SUPPRESS",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"match_action": "user.does_not_exist"},
        )
        assert r.status_code == 422
        assert "not registered" in r.json()["message"].lower()

    def test_patch_registered_action_allowed(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        created = admin_client.post(RULES_URL, json=make_rule(
            name="patch-ok", effect="SUPPRESS",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"match_action": "user.login"},
        )
        assert r.status_code == 200
        assert r.json()["match_action"] == "user.login"

    def test_patch_glob_action_always_allowed(self, client, admin_client, auth_headers):
        client.post(f"{SERVICES_URL}/auth_service/events",
                    json={"events": [make_event_def(action="user.login")]},
                    headers=auth_headers)
        created = admin_client.post(RULES_URL, json=make_rule(
            name="patch-glob", effect="SUPPRESS",
        )).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"match_action": "user.*"},
        )
        assert r.status_code == 200


# ── Конфликт имён на PATCH → 409 (gap #11) ───────────────────────────────────

class TestPatchNameConflict:
    def test_rename_to_existing_returns_409(self, admin_client):
        """PATCH name → имя другого правила должен дать 409, не 500."""
        admin_client.post(RULES_URL, json=make_rule(name="alpha"))
        beta = admin_client.post(RULES_URL, json=make_rule(name="beta")).json()
        r = admin_client.patch(
            f"{RULES_URL}/{beta['id']}",
            json={"name": "alpha"},
        )
        assert r.status_code == 409
        assert "alpha" in r.json()["message"]

    def test_rename_to_unused_name_ok(self, admin_client):
        created = admin_client.post(RULES_URL, json=make_rule(name="orig")).json()
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"name": "renamed"},
        )
        assert r.status_code == 200
        assert r.json()["name"] == "renamed"


# ── Инвалидация кеша после PATCH ─────────────────────────────────────────────

class TestPatchInvalidatesCache:
    def test_deactivating_suppress_rule_via_patch_re_enables_ingest(
        self, client, admin_client, auth_headers
    ):
        """После PATCH is_active=false событие должно перестать подавляться."""
        from tests.conftest import make_event
        created = admin_client.post(RULES_URL, json=make_rule(
            name="cache-inv", effect="SUPPRESS", match_action="user.login",
        )).json()
        # Сначала подавляется
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login")).status_code == 204
        # Деактивируем правило
        admin_client.patch(f"{RULES_URL}/{created['id']}", json={"is_active": False})
        # Теперь должно сохраняться
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login")).status_code == 201

    def test_changing_match_action_via_patch_redirects_suppression(
        self, client, admin_client, auth_headers
    ):
        created = admin_client.post(RULES_URL, json=make_rule(
            name="switch", effect="SUPPRESS", match_action="user.login",
        )).json()
        from tests.conftest import make_event
        # До PATCH — user.logout НЕ подавляется
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.logout")).status_code == 201
        # Переключаем правило на user.logout
        admin_client.patch(f"{RULES_URL}/{created['id']}",
                           json={"match_action": "user.logout"})
        # Теперь user.logout подавляется
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.logout")).status_code == 204
        # А user.login — нет
        assert client.post(EVENTS_URL, headers=auth_headers,
                           json=make_event(action="user.login")).status_code == 201


# ── IntegrityError без `name` в payload ───────────────────────────────────


class TestPatchIntegrityErrorWithoutName:
    """PATCH без поля `name` не должен врать клиенту `RULE_NAME_CONFLICT`.

    Раньше `except IntegrityError` слепо эмитил 409 с message
    "Rule with name 'None' already exists" даже когда IntegrityError пришёл
    от NOT NULL / FK / CHECK constraint'а на другом поле — SOC видит фейковый
    конфликт имён, caller тратит время на поиск дубликата, которого нет.

    После фикса: payload.name is None → 500 INTERNAL_ERROR с честным
    error_code (caller знает, что нужно искать другую причину); payload.name
    задан → нормальный 409 NAME_CONFLICT.
    """

    def test_integrity_error_without_name_returns_500_internal(
        self, admin_client, monkeypatch
    ):
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        created = admin_client.post(
            RULES_URL, json=make_rule(name="orig"),
        ).json()

        # Эмулируем не-name constraint violation (NOT NULL / FK / CHECK).
        # `orig`-параметр у IntegrityError — `None`, чтобы конструктор не
        # дёргался за driver-specific атрибутами.
        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("simulated constraint", params=None, orig=None)

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        # PATCH без `name` — меняем только priority.
        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"priority": 50},
        )
        assert r.status_code == 500, r.text
        body = r.json()
        assert body["error_code"] == "INTERNAL_ERROR"
        # Сообщение не должно содержать вранья про конфликт имён "None".
        assert "None" not in body["message"]
        assert "name" not in body["message"].lower()

    def test_integrity_error_with_name_still_returns_409_conflict(
        self, admin_client, monkeypatch
    ):
        """Регрессия: легитимный rename-конфликт продолжает быть 409.

        Симметрично с `create_rule` — pgcode `23505` + наличие `payload.name`
        мапим в 409. Голый `orig=None` уходит в 500, чтобы не врать
        SOC'у про конфликт имён при неизвестной причине IntegrityError'а.
        """
        from sqlalchemy.exc import IntegrityError

        from src.api.v1.endpoints import rules as rules_endpoint

        created = admin_client.post(
            RULES_URL, json=make_rule(name="orig"),
        ).json()

        class _Orig:
            pgcode = "23505"  # настоящий UniqueViolation

        def _boom(db, rule, payload, *, commit=True):
            raise IntegrityError("simulated unique-name", params=None, orig=_Orig())

        monkeypatch.setattr(rules_endpoint.rule_repo, "update", _boom)

        r = admin_client.patch(
            f"{RULES_URL}/{created['id']}",
            json={"name": "renamed"},
        )
        assert r.status_code == 409
        body = r.json()
        assert body["error_code"] == "RULE_NAME_CONFLICT"
        assert "renamed" in body["message"]
