"""Тесты: POST /api/logging/v1/events — приём и сохранение событий аудита."""

import pytest
from tests.conftest import make_event


class TestIngestAuth:
    def test_missing_token_returns_401(self, client):
        resp = client.post("/api/logging/v1/events", json=make_event())
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self, client):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(),
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401

    def test_valid_token_accepted(self, client, auth_headers):
        resp = client.post("/api/logging/v1/events", json=make_event(), headers=auth_headers)
        assert resp.status_code == 201


class TestIngestPayload:
    def test_returns_id_and_received_at(self, client, auth_headers):
        resp = client.post("/api/logging/v1/events", json=make_event(), headers=auth_headers)
        body = resp.json()
        assert body["id"].startswith("log_")
        assert "received_at" in body

    def test_minimal_event_defaults(self, client, auth_headers):
        payload = {
            "timestamp": "2026-04-19T10:00:00Z",
            "service": "config_service",
            "action": "secret.read",
            "status": "success",
            "allowed": True,
        }
        # X-Service-Identity должна совпадать с payload.service —
        # guard SERVICE_IDENTITY_PAYLOAD_MISMATCH режет несовпадение на 403.
        headers = auth_headers | {"X-Service-Identity": "config_service"}
        resp = client.post("/api/logging/v1/events", json=payload, headers=headers)
        assert resp.status_code == 201

    def test_all_severity_levels_accepted(self, client, auth_headers):
        for severity in ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            resp = client.post(
                "/api/logging/v1/events",
                json=make_event(severity=severity),
                headers=auth_headers,
            )
            assert resp.status_code == 201, f"failed for severity={severity}"

    def test_invalid_severity_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(severity="VERBOSE"),
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_invalid_status_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(status="fail"),
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_denied_event(self, client, auth_headers):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(status="denied", allowed=False, severity="WARNING"),
            headers=auth_headers,
        )
        assert resp.status_code == 201

    def test_details_stored(self, client, auth_headers, db):
        from src.repositories.events import query as repo_query
        payload = make_event(details={"reason": "invalid_password", "attempts": 3})
        resp = client.post("/api/logging/v1/events", json=payload, headers=auth_headers)
        assert resp.status_code == 201
        event_id = resp.json()["id"]
        from src.models.audit_event import AuditEvent
        stored = db.get(AuditEvent, event_id)
        assert stored.details["reason"] == "invalid_password"
        assert stored.details["attempts"] == 3


class TestHealthProbes:
    def test_health_no_auth(self, client):
        resp = client.get("/api/logging/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ready_no_auth(self, client):
        resp = client.get("/api/logging/v1/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"


class TestIngestReservedService:
    """Внешний ingest не может писать события от имени loging_service.

    Это закрывает дыру: при компрометации SERVICE_API_KEY злоумышленник мог
    бы засеять storage событиями с ``service='loging_service'``, которые
    защищены retention-инвариантом и никогда не ротировались бы.
    """

    def test_canonical_loging_service_rejected(self, client, auth_headers):
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service="loging_service"),
            headers=auth_headers,
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_mixed_case_loging_service_rejected(self, client, auth_headers):
        """Обход case-sensitivity (LoGiNg_SeRvIcE / LOGING_SERVICE / ...) — отбой.

        Schema-charset (`[a-z_]{1,64}`) ловит uppercase ASCII ДО того, как
        запрос доходит до ingest-эндпоинта с reserved-name guard'ом. Defence
        in depth: оба слоя работают, но schema срабатывает раньше → 422
        VALIDATION_ERROR, а не 403 RESERVED_SERVICE_NAME. Важно, что
        событие не записывается ни под каким вариантом.
        """
        for variant in ("LoGiNg_SeRvIcE", "LOGING_SERVICE", "Loging_Service"):
            resp = client.post(
                "/api/logging/v1/events",
                json=make_event(service=variant),
                headers=auth_headers,
            )
            assert resp.status_code == 422, f"variant {variant!r} not blocked"
            assert resp.json()["error_code"] == "VALIDATION_ERROR"

    def test_whitespace_padded_loging_service_rejected(self, client, auth_headers):
        """Trailing / leading whitespace тоже не должно проходить (strip перед сравнением)."""
        for variant in (" loging_service", "loging_service ", "  loging_service  "):
            resp = client.post(
                "/api/logging/v1/events",
                json=make_event(service=variant),
                headers=auth_headers,
            )
            assert resp.status_code == 403, f"variant {variant!r} not blocked"
            assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_other_service_names_still_accepted(self, client, auth_headers):
        """Регрессия: легитимные сервисы продолжают писать без 403."""
        for svc in ("auth_service", "server_service", "config_service", "server_worker"):
            resp = client.post(
                "/api/logging/v1/events",
                json=make_event(service=svc),
                headers=auth_headers | {"X-Service-Identity": svc},
            )
            assert resp.status_code == 201, f"service {svc!r} unexpectedly blocked"


class TestIngestReservedServiceUnicodeBypass:
    """Обход reserved-service-guard через Unicode.

    До фикса сравнение было ``.strip().lower()`` — не убирало zero-width
    space (U+200B) и не нормализовало Unicode confusable (кириллическая
    ``о`` U+043E vs латинская ``o``). Атакующий мог писать события с
    ``service="loging_service​"`` или ``"lоging_service"``, оба
    варианта попадали в audit_events и retention-инвариант
    ``func.lower(service) != 'loging_service'`` тоже их пропускал —
    событие удалится при ротации → erasure of audit trail.

    После фикса ``normalize_service_name`` применяется в pydantic-валидаторе
    ``EventCreate.service`` (NFKC + strip invisibles + homoglyph fold +
    lower) → guard в ingest и retention-фильтр работают корректно.
    """

    def test_zero_width_space_bypass_rejected(self, client, auth_headers):
        """``loging_service`` + U+200B (zero-width space) → 403."""
        variant = "loging_service​"
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service=variant),
            headers=auth_headers,
        )
        assert resp.status_code == 403, f"variant {variant!r} not blocked"
        assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_zero_width_chars_inside_rejected(self, client, auth_headers):
        """Невидимые символы внутри строки (ZWNJ / ZWJ / WJ / BOM) тоже схлопываются."""
        variants = [
            "loging​service",     # ZWSP в середине, без подчёркивания
            "loging_‌service",    # ZWNJ
            "loging_‍service",    # ZWJ
            "loging_⁠service",    # WORD JOINER
            "﻿loging_service",    # BOM в начале
            "loging_service﻿",    # BOM в конце
            "loging­_service",    # SOFT HYPHEN
        ]
        # ZWSP без подчёркивания даёт "logingservice" — это не reserved,
        # поэтому исключаем такой кейс из reject-проверки.
        for variant in variants[1:]:
            resp = client.post(
                "/api/logging/v1/events",
                json=make_event(service=variant),
                headers=auth_headers,
            )
            assert resp.status_code == 403, f"variant {variant!r} not blocked"
            assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_cyrillic_confusable_rejected(self, client, auth_headers):
        """``lоging_service`` с кириллической ``о`` (U+043E) → 403 после NFKC + confusable-fold."""
        variant = "lоging_service"  # cyrillic 'о'
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service=variant),
            headers=auth_headers,
        )
        assert resp.status_code == 403, f"variant {variant!r} not blocked"
        assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_all_cyrillic_confusables_rejected(self, client, auth_headers):
        """Полный кириллический ``loging_service`` (все буквы заменены на похожие)."""
        # l (не путается), о→о, g (нет cyr-аналога), i→і, n (нет), _, s→ѕ, e→е, r→р, v→в, i→і, c→с, e→е
        variant = "lоging_ѕеrviсе"  # "loging_serviсе" — частично кириллица
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service=variant),
            headers=auth_headers,
        )
        assert resp.status_code == 403, f"variant {variant!r} not blocked"
        assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_greek_confusable_rejected(self, client, auth_headers):
        """Greek ``ο`` (U+03BF) тоже фолдится в ASCII ``o`` → 403."""
        variant = "lοging_service"  # greek 'ο'
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service=variant),
            headers=auth_headers,
        )
        assert resp.status_code == 403, f"variant {variant!r} not blocked"
        assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_fullwidth_letters_rejected(self, client, auth_headers):
        """NFKC схлопывает full-width ``ｌｏｇｉｎｇ_ｓｅｒｖｉｃｅ`` → ASCII."""
        variant = "ｌｏｇｉｎｇ_ｓｅｒｖｉｃｅ"
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service=variant),
            headers=auth_headers,
        )
        assert resp.status_code == 403, f"variant {variant!r} not blocked"
        assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_combined_unicode_and_case_rejected(self, client, auth_headers):
        """Комбинация: верхний регистр + кириллическая ``О`` + ZWSP — отбой.

        Аналогично mixed-case'у: schema-charset режет на 422 до того, как
        ingest-guard увидел бы reserved name. Confusable-fold + invisibles
        работают на стадии нормализации, но uppercase ASCII остаётся, и
        `[a-z_]{1,64}` его не пропускает. Запись по-прежнему не появляется.
        """
        variant = "LОGING_SERVICE​"  # 'O' = cyrillic capital, + ZWSP
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service=variant),
            headers=auth_headers,
        )
        assert resp.status_code == 422, f"variant {variant!r} not blocked"
        assert resp.json()["error_code"] == "VALIDATION_ERROR"

    def test_invisible_padding_with_whitespace_rejected(self, client, auth_headers):
        """ZWSP + ASCII whitespace по краям одновременно — оба слоя должны срезаться."""
        variant = " ​loging_service​ "
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service=variant),
            headers=auth_headers,
        )
        assert resp.status_code == 403, f"variant {variant!r} not blocked"
        assert resp.json()["error_code"] == "RESERVED_SERVICE_NAME"

    def test_unicode_normalized_form_stored_in_db(self, client, auth_headers, db):
        """Событие легитимного сервиса с full-width буквами сохраняется в каноничной ASCII-форме.

        Это критично для retention: если бы NFKC-нормализация не применялась к
        записываемой строке, ``func.lower(service)`` в retention потом не нашёл бы
        канонический ``auth_service`` и поведение стало бы непредсказуемым.
        """
        variant = "ａuth_service"  # ｕ = full-width 'a' → 'a' после NFKC
        resp = client.post(
            "/api/logging/v1/events",
            json=make_event(service=variant),
            headers=auth_headers,
        )
        assert resp.status_code == 201
        event_id = resp.json()["id"]
        from src.models.audit_event import AuditEvent
        stored = db.get(AuditEvent, event_id)
        assert stored.service == "auth_service", (
            f"expected normalized 'auth_service', got {stored.service!r}"
        )
