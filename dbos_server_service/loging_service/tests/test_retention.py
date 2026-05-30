"""Тесты: /api/logging/v1/retention — политика хранения логов."""

URL = "/api/logging/v1/retention"


# ── GET ───────────────────────────────────────────────────────────────────────


class TestGetPolicy:
    def test_returns_null_when_no_policy(self, admin_client):
        r = admin_client.get(URL)
        assert r.status_code == 200
        assert r.json() is None

    def test_returns_existing_policy(self, admin_client):
        admin_client.put(URL, json={"retain_days": 90, "description": "default"})
        r = admin_client.get(URL)
        assert r.status_code == 200
        body = r.json()
        assert body is not None
        assert body["retain_days"] == 90
        assert body["description"] == "default"
        assert body["is_active"] is True


# ── PUT ───────────────────────────────────────────────────────────────────────


class TestSetPolicy:
    def test_creates_new_policy(self, admin_client):
        r = admin_client.put(URL, json={"retain_days": 60, "description": "60d hold"})
        assert r.status_code == 200
        body = r.json()
        assert body["retain_days"] == 60
        assert body["description"] == "60d hold"
        assert body["is_active"] is True
        assert "id" in body
        assert "created_at" in body

    def test_replaces_existing_policy(self, admin_client, db):
        admin_client.put(URL, json={"retain_days": 30})
        second = admin_client.put(URL, json={"retain_days": 90, "description": "expanded"}).json()
        # PUT гасит прежний набор и создаёт новый: активной остаётся ровно
        # одна global-строка с новыми параметрами, старая деактивирована.
        assert second["retain_days"] == 90
        assert second["description"] == "expanded"
        from src.models.retention_policy import RetentionPolicy
        active = db.query(RetentionPolicy).filter_by(is_active=True).all()
        assert len(active) == 1
        assert active[0].id == second["id"]
        assert active[0].retain_days == 90

    def test_below_minimum_returns_422(self, admin_client):
        # retain_days < 30 (минимум по схеме)
        r = admin_client.put(URL, json={"retain_days": 7})
        assert r.status_code == 422

    def test_above_maximum_returns_422(self, admin_client):
        # retain_days > 3650 (10 лет)
        r = admin_client.put(URL, json={"retain_days": 4000})
        assert r.status_code == 422

    def test_missing_retain_days_returns_422(self, admin_client):
        r = admin_client.put(URL, json={"description": "no days"})
        assert r.status_code == 422


# ── DELETE ────────────────────────────────────────────────────────────────────


class TestDisablePolicy:
    def test_delete_returns_204(self, admin_client):
        admin_client.put(URL, json={"retain_days": 60})
        r = admin_client.delete(URL)
        assert r.status_code == 204

    def test_delete_deactivates_policy(self, admin_client):
        admin_client.put(URL, json={"retain_days": 60})
        admin_client.delete(URL)
        # После delete GET возвращает null (активной политики нет)
        assert admin_client.get(URL).json() is None

    def test_delete_with_no_policy_returns_204(self, admin_client):
        # Идемпотентность: DELETE на пустой системе тоже 204
        r = admin_client.delete(URL)
        assert r.status_code == 204

    def test_delete_deactivates_entire_cartesian_set(self, admin_client, db):
        # filtered-политика раскрывается в N×M активных строк; DELETE обязан
        # погасить ВЕСЬ набор, иначе фоновая ротация продолжит чистить события
        # по оставшимся узким предикатам.
        admin_client.put(
            URL,
            json={
                "retain_days": 45,
                "severity_filter": ["INFO", "WARNING"],
                "service_filter": ["auth_service", "server_service"],
            },
        )
        from src.models.retention_policy import RetentionPolicy
        assert db.query(RetentionPolicy).filter_by(is_active=True).count() == 4

        r = admin_client.delete(URL)
        assert r.status_code == 204
        assert db.query(RetentionPolicy).filter_by(is_active=True).count() == 0
        # GET тоже подтверждает, что активной политики не осталось.
        assert admin_client.get(URL).json() is None


# ── Авторизация: ВСЕ роуты под require_admin (router-level) ───────────────────


class TestAdminGuard:
    def test_no_token_get_returns_401(self, client):
        assert client.get(URL).status_code == 401

    def test_no_token_put_returns_401(self, client):
        assert client.put(URL, json={"retain_days": 60}).status_code == 401

    def test_no_token_delete_returns_401(self, client):
        assert client.delete(URL).status_code == 401

    def test_account_admin_cannot_manage_retention(self, client, mock_introspect):
        """account_admin не имеет loging_admin → 403 при попытке управлять retention."""
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "u",
            "username": "n", "platform_role": "account_admin",
        }):
            r = client.put(URL, headers={"Authorization": "Bearer t"}, json={"retain_days": 60})
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_department_admin_cannot_manage_retention(self, client, mock_introspect):
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "u",
            "username": "n", "platform_role": "department_admin",
            "department_id": "dep_x",
        }):
            r = client.get(URL, headers={"Authorization": "Bearer t"})
        assert r.status_code == 403

    def test_loging_reader_cannot_manage_retention(self, client, mock_introspect):
        """loging_reader даёт только чтение событий, не управление retention."""
        with mock_introspect(json_body={
            "active": True, "subject_type": "user", "sub": "u",
            "username": "n", "platform_role": "loging_reader",
            "department_id": "dep_x",
        }):
            r = client.delete(URL, headers={"Authorization": "Bearer t"})
        assert r.status_code == 403


# ── severity_filter / service_filter ─────────────────────────────────────────


class TestFilteredPolicy:
    """`RetentionPolicyCreate.severity_filter` + `service_filter`.

    DB-схема — scalar nullable; create_policy раскрывает list-фильтры в
    Cartesian rows (одна строка на пару). `apply_active` обрабатывает
    каждый row своим DELETE по соответствующему предикату.
    """

    def test_invalid_severity_returns_422(self, admin_client):
        # Pydantic Literal — `FATAL` не в whitelist'е.
        r = admin_client.put(
            URL,
            json={"retain_days": 60, "severity_filter": ["FATAL"]},
        )
        assert r.status_code == 422

    def test_invalid_service_name_returns_422(self, admin_client):
        # Charset гард в validator'е — пробел не разрешён.
        r = admin_client.put(
            URL,
            json={"retain_days": 60, "service_filter": ["bad name"]},
        )
        assert r.status_code == 422

    def test_empty_filter_treated_as_none(self, admin_client, db):
        # `[]` нормализуется в None — пишется один row NULL/NULL.
        r = admin_client.put(
            URL,
            json={"retain_days": 60, "severity_filter": [], "service_filter": []},
        )
        assert r.status_code == 200
        from src.models.retention_policy import RetentionPolicy
        rows = db.query(RetentionPolicy).filter_by(is_active=True).all()
        assert len(rows) == 1
        assert rows[0].severity is None
        assert rows[0].service is None

    def test_severity_filter_persists_in_db(self, admin_client, db):
        r = admin_client.put(
            URL,
            json={"retain_days": 90, "severity_filter": ["TRACE", "DEBUG"]},
        )
        assert r.status_code == 200
        from src.models.retention_policy import RetentionPolicy
        rows = db.query(RetentionPolicy).filter_by(is_active=True).all()
        # 2 severity × 1 service (None) = 2 rows
        assert len(rows) == 2
        severities = sorted(r.severity for r in rows)
        assert severities == ["DEBUG", "TRACE"]
        assert all(r.service is None for r in rows)
        assert all(r.retain_days == 90 for r in rows)

    def test_cartesian_product_severity_x_service(self, admin_client, db):
        r = admin_client.put(
            URL,
            json={
                "retain_days": 45,
                "severity_filter": ["INFO", "WARNING"],
                "service_filter": ["auth_service", "server_service"],
            },
        )
        assert r.status_code == 200
        from src.models.retention_policy import RetentionPolicy
        rows = db.query(RetentionPolicy).filter_by(is_active=True).all()
        # 2 × 2 = 4 rows
        assert len(rows) == 4
        pairs = sorted((r.severity, r.service) for r in rows)
        assert pairs == [
            ("INFO", "auth_service"),
            ("INFO", "server_service"),
            ("WARNING", "auth_service"),
            ("WARNING", "server_service"),
        ]

    def test_dedupe_severity_filter(self, admin_client, db):
        # Дубликаты в filter'е свёрнуты — иначе получили бы N×M строк
        # с повторами.
        r = admin_client.put(
            URL,
            json={"retain_days": 60, "severity_filter": ["INFO", "INFO", "ERROR"]},
        )
        assert r.status_code == 200
        from src.models.retention_policy import RetentionPolicy
        rows = db.query(RetentionPolicy).filter_by(is_active=True).all()
        assert len(rows) == 2

    def test_filtered_put_replaces_global_policy(self, admin_client, db):
        # PUT без filter'ов → один global row; затем PUT с filter'ом
        # деактивирует global и пишет filtered набор.
        admin_client.put(URL, json={"retain_days": 30})
        admin_client.put(
            URL,
            json={"retain_days": 60, "severity_filter": ["ERROR"]},
        )
        from src.models.retention_policy import RetentionPolicy
        active = db.query(RetentionPolicy).filter_by(is_active=True).all()
        inactive = db.query(RetentionPolicy).filter_by(is_active=False).all()
        # Старый global row деактивирован, новый ERROR-row активен.
        assert len(active) == 1
        assert active[0].severity == "ERROR"
        assert len(inactive) == 1
        assert inactive[0].severity is None

    def test_global_put_replaces_filtered_set(self, admin_client, db):
        # filtered-набор (N×M строк) → PUT без фильтров. Должна остаться РОВНО
        # одна global-строка; узкие предикаты погашены, иначе «сброс на
        # глобальную политику» не работал бы.
        admin_client.put(
            URL,
            json={
                "retain_days": 45,
                "severity_filter": ["INFO", "WARNING"],
                "service_filter": ["auth_service", "server_service"],
            },
        )
        from src.models.retention_policy import RetentionPolicy
        assert db.query(RetentionPolicy).filter_by(is_active=True).count() == 4

        body = admin_client.put(URL, json={"retain_days": 30}).json()
        assert body["retain_days"] == 30
        active = db.query(RetentionPolicy).filter_by(is_active=True).all()
        assert len(active) == 1
        assert active[0].severity is None
        assert active[0].service is None
        assert active[0].retain_days == 30
        # Прежний Cartesian-набор деактивирован, не удалён.
        assert db.query(RetentionPolicy).filter_by(is_active=False).count() == 4

    def test_filtered_put_replaces_previous_filtered_set(self, admin_client, db):
        # filtered → filtered: новый набор полностью заменяет прежний, старые
        # пары не остаются активными рядом с новыми.
        admin_client.put(
            URL,
            json={"retain_days": 45, "severity_filter": ["INFO", "WARNING"]},
        )
        from src.models.retention_policy import RetentionPolicy
        assert db.query(RetentionPolicy).filter_by(is_active=True).count() == 2

        admin_client.put(
            URL,
            json={"retain_days": 60, "severity_filter": ["ERROR"]},
        )
        active = db.query(RetentionPolicy).filter_by(is_active=True).all()
        assert len(active) == 1
        assert active[0].severity == "ERROR"
        assert active[0].retain_days == 60

    def test_apply_active_honours_severity_filter(self, db):
        """`apply_active` удаляет только rows нужного severity."""
        from datetime import datetime, timedelta, timezone
        import uuid

        from src.models.audit_event import AuditEvent
        from src.repositories.retention_policies import apply_active
        from src.repositories import retention_policies as repo
        from src.schemas.retention import RetentionPolicyCreate

        old = datetime.now(timezone.utc) - timedelta(days=100)
        for severity in ("INFO", "ERROR"):
            db.add(AuditEvent(
                id=f"log_{uuid.uuid4().hex[:16]}",
                timestamp=old, service="auth_service", action="x.y",
                actor_id=None, actor_type="service", status="success",
                allowed=True, severity=severity, details={},
            ))
        db.flush()

        # Политика: удалять только ERROR старше 60 дней.
        repo.create_policy(
            db,
            RetentionPolicyCreate(retain_days=60, severity_filter=["ERROR"]),
        )

        deleted = apply_active(db)
        assert deleted == 1
        remaining = {e.severity for e in db.query(AuditEvent).all()}
        assert remaining == {"INFO"}

    def test_apply_active_honours_service_filter(self, db):
        """`apply_active` удаляет только rows нужного service."""
        from datetime import datetime, timedelta, timezone
        import uuid

        from src.models.audit_event import AuditEvent
        from src.repositories.retention_policies import apply_active
        from src.repositories import retention_policies as repo
        from src.schemas.retention import RetentionPolicyCreate

        old = datetime.now(timezone.utc) - timedelta(days=100)
        for service in ("auth_service", "server_service"):
            db.add(AuditEvent(
                id=f"log_{uuid.uuid4().hex[:16]}",
                timestamp=old, service=service, action="x.y",
                actor_id=None, actor_type="service", status="success",
                allowed=True, severity="INFO", details={},
            ))
        db.flush()

        repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=60, service_filter=["auth_service"]
            ),
        )

        deleted = apply_active(db)
        assert deleted == 1
        remaining = {e.service for e in db.query(AuditEvent).all()}
        assert remaining == {"server_service"}

    def test_schema_rejects_loging_service_in_filter(self):
        """Schema-уровень валидирует service_filter и отвергает `loging_service`
        с 422 — политика никогда не сработала бы на защищённом сервисе.
        Закрывает «полития создаётся, но retention не чистит».
        """
        import pytest
        from pydantic import ValidationError

        from src.schemas.retention import RetentionPolicyCreate

        with pytest.raises(ValidationError) as excinfo:
            RetentionPolicyCreate(
                retain_days=60, service_filter=["loging_service"]
            )
        assert "protected" in str(excinfo.value)

    def test_apply_active_protects_loging_service_via_legacy_policy(self, db):
        """Defence-in-depth: даже если policy с `service='loging_service'` попала
        в БД мимо schema (legacy data, прямой ORM-insert), runtime-guard в
        `apply_active` всё равно не удаляет события защищённого сервиса.
        """
        from datetime import datetime, timedelta, timezone
        import uuid

        from src.models.audit_event import AuditEvent
        from src.models.retention_policy import RetentionPolicy
        from src.repositories.retention_policies import apply_active

        old = datetime.now(timezone.utc) - timedelta(days=100)
        db.add(AuditEvent(
            id=f"log_{uuid.uuid4().hex[:16]}",
            timestamp=old, service="loging_service", action="logging.admin",
            actor_id=None, actor_type="service", status="success",
            allowed=True, severity="INFO", details={},
        ))
        # Прямая вставка policy с loging_service — имитация legacy-data,
        # когда schema-guard ещё не существовал.
        now = datetime.now(timezone.utc)
        db.add(RetentionPolicy(
            retain_days=60,
            service="loging_service",
            is_active=True,
            created_at=now,
            updated_at=now,
        ))
        db.flush()

        deleted = apply_active(db)
        assert deleted == 0
        assert db.query(AuditEvent).count() == 1


class TestRetentionAuditNoDuplicate:
    """Endpoint — single source `logging.retention_write`. Middleware-эмиссия
    на success-путь сходила бы в дубль, ломала SIEM-агрегаты по action."""

    def test_put_emits_exactly_one_retention_write(self, admin_client, db):
        import time
        from src.models.audit_event import AuditEvent

        admin_client.put(URL, json={"retain_days": 90})
        # Async middleware tasks могли ещё крутиться — даём шанс отстреляться.
        time.sleep(0.2)
        db.expire_all()
        events = (
            db.query(AuditEvent)
            .filter_by(action="logging.retention_write")
            .all()
        )
        assert len(events) == 1, (
            f"PUT /retention должен писать ровно один retention_write event "
            f"(endpoint — single source), получили {len(events)}"
        )

    def test_delete_emits_exactly_one_retention_write(self, admin_client, db):
        import time
        from src.models.audit_event import AuditEvent

        admin_client.put(URL, json={"retain_days": 90})
        admin_client.delete(URL)
        time.sleep(0.2)
        db.expire_all()
        events = (
            db.query(AuditEvent)
            .filter_by(action="logging.retention_write")
            .all()
        )
        # 1 from PUT + 1 from DELETE = 2 retention_write events total
        assert len(events) == 2, (
            f"PUT+DELETE должны дать ровно 2 retention_write events, "
            f"получили {len(events)}"
        )

    def test_get_does_not_emit_retention_write(self, admin_client, db):
        import time
        from src.models.audit_event import AuditEvent

        admin_client.put(URL, json={"retain_days": 90})
        admin_client.get(URL)
        time.sleep(0.2)
        db.expire_all()
        writes = (
            db.query(AuditEvent)
            .filter_by(action="logging.retention_write")
            .count()
        )
        # PUT — 1 write, GET — 0 (read-only).
        assert writes == 1


class TestRetentionAuditSnapshot:
    """`logging.retention_write` audit-events должны нести ПОЛНЫЙ snapshot
    активного набора, а не только первую представительскую строку."""

    def test_put_audit_old_snapshot_lists_all_active_policies(
        self, admin_client, db
    ):
        from src.models.audit_event import AuditEvent

        # Подготовка: набор из 4 filtered-политик активен.
        admin_client.put(
            URL,
            json={
                "retain_days": 30,
                "severity_filter": ["INFO", "WARNING"],
                "service_filter": ["auth_service", "server_service"],
            },
        )

        # Перезаписываем — теперь old_snapshot должен описать все 4 прежних
        # (в grouped-форме: сервис-измерение схлопнуто в `services`/`count`,
        # severity остаётся per-row).
        admin_client.put(URL, json={"retain_days": 60})

        events = (
            db.query(AuditEvent)
            .filter_by(action="logging.retention_write")
            .order_by(AuditEvent.received_at.desc())
            .all()
        )
        assert events, "ожидался хотя бы один retention_write event"
        latest = events[0]
        old = latest.details.get("old", [])
        assert isinstance(old, list)
        # 2 severities × 2 services Cartesian → группировка по severity
        # схлопывает 4 строки в 2 группы (INFO/WARNING), каждая с count=2
        # и services=["auth_service","server_service"].
        assert len(old) == 2, (
            f"ожидаем 2 группы (по severity), получили {len(old)}: {old!r}"
        )
        total_count = sum(item.get("count", 0) for item in old)
        assert total_count == 4, (
            f"total count в old должен равняться 4 (исходный Cartesian), "
            f"получили {total_count}"
        )
        severities = {item["severity"] for item in old}
        assert severities == {"INFO", "WARNING"}
        for item in old:
            assert item["services"] == ["auth_service", "server_service"]
            assert item["count"] == 2
            assert item["retain_days"] == 30

    def test_delete_audit_old_snapshot_lists_all_active_policies(
        self, admin_client, db
    ):
        from src.models.audit_event import AuditEvent

        admin_client.put(
            URL,
            json={
                "retain_days": 30,
                "severity_filter": ["INFO", "ERROR"],
            },
        )
        admin_client.delete(URL)

        events = (
            db.query(AuditEvent)
            .filter_by(action="logging.retention_write")
            .order_by(AuditEvent.received_at.desc())
            .all()
        )
        # disable пишет последним событием.
        latest = events[0]
        old = latest.details.get("old", [])
        assert isinstance(old, list)
        assert len(old) == 2
        assert latest.details.get("new") is None


# ── self-audit size-cap для filtered Cartesian ──────────────────────────────


class TestRetentionSelfAuditSizeCap:
    """Filtered-PUT с полным Cartesian (6 severity × 64 сервиса = 384 строк)
    раздувал self-audit `details` за 64 KB лимит `EventCreate._details_size`
    и валил легитимную операцию 422-кой. После фикса `_snapshot_list`
    группирует по `(retain_days, severity)`, схлопывая сервис-измерение в
    `services`/`count` — детали остаются в разумных рамках, audit проходит.
    """

    def test_large_filtered_put_does_not_overflow_details(
        self, admin_client, db
    ):
        import json

        from src.models.audit_event import AuditEvent

        all_severities = ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        # 64 уникальных snake_case-сервиса (без digits — service_filter validator
        # требует [a-z_]). Кодируем индекс в двух буквах a..h × a..h = 64 шт.
        import string

        letters = string.ascii_lowercase[:8]  # a..h
        services = [f"svc_{a}{b}" for a in letters for b in letters]

        # Первый PUT — устанавливает большой Cartesian.
        r1 = admin_client.put(
            URL,
            json={
                "retain_days": 30,
                "severity_filter": all_severities,
                "service_filter": services,
            },
        )
        assert r1.status_code == 200, r1.text

        # Второй PUT — теперь old_snapshot должен описать все 384 row'и.
        # Без фикса self-audit упал бы 422 «details must not exceed 64 KB».
        r2 = admin_client.put(URL, json={"retain_days": 60})
        assert r2.status_code == 200, (
            f"PUT упал {r2.status_code} вместо 200; вероятно self-audit "
            f"раздул details за 64 KB. Body: {r2.text}"
        )

        events = (
            db.query(AuditEvent)
            .filter_by(action="logging.retention_write")
            .order_by(AuditEvent.received_at.desc())
            .all()
        )
        # Должно быть как минимум два audit-row (первый PUT + второй PUT).
        assert len(events) >= 2

        latest = events[0]
        old = latest.details.get("old", [])
        # Group-by (retain_days × severity): 6 severity → 6 групп.
        assert isinstance(old, list)
        assert len(old) == 6, f"ожидаем 6 групп по severity, получили {len(old)}"

        total_count = sum(item.get("count", 0) for item in old)
        assert total_count == 6 * 64, (
            f"сумма count должна быть 384 (полный Cartesian), "
            f"получили {total_count}"
        )

        # Каждая группа содержит все 64 сервиса.
        for item in old:
            assert item["retain_days"] == 30
            assert item["count"] == 64
            assert item["services"] == sorted(services)

        # Прямая проверка ограничения size-cap: сериализованный details
        # должен умещаться в 64 KB.
        details_json = json.dumps(latest.details, default=str)
        assert len(details_json) <= 65_536, (
            f"details сериализуется в {len(details_json)} байт, лимит 65536"
        )
