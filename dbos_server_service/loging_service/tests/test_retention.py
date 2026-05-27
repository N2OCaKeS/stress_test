"""Тесты: /api/logging/v1/retention — политика хранения логов."""

from unittest.mock import MagicMock, patch

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

    def test_account_admin_cannot_manage_retention(self, client):
        """account_admin не имеет loging_admin → 403 при попытке управлять retention."""
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"active": True, "subject_type": "user", "sub": "u",
                              "username": "n", "platform_role": "account_admin"},
            )
            r = client.put(URL, headers={"Authorization": "Bearer t"}, json={"retain_days": 60})
        assert r.status_code == 403
        assert r.json()["error_code"] == "INSUFFICIENT_ROLE"

    def test_department_admin_cannot_manage_retention(self, client):
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"active": True, "subject_type": "user", "sub": "u",
                              "username": "n", "platform_role": "department_admin",
                              "department_id": "dep_x"},
            )
            r = client.get(URL, headers={"Authorization": "Bearer t"})
        assert r.status_code == 403

    def test_loging_reader_cannot_manage_retention(self, client):
        """loging_reader даёт только чтение событий, не управление retention."""
        with patch("src.dependencies.auth.httpx.post") as mock_get:
            mock_get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"active": True, "subject_type": "user", "sub": "u",
                              "username": "n", "platform_role": "loging_reader",
                              "department_id": "dep_x"},
            )
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

    def test_apply_active_protects_loging_service_even_with_filter(self, db):
        """Filter на `loging_service` НЕ может удалить его события — инвариант."""
        from datetime import datetime, timedelta, timezone
        import uuid

        from src.models.audit_event import AuditEvent
        from src.repositories.retention_policies import apply_active
        from src.repositories import retention_policies as repo
        from src.schemas.retention import RetentionPolicyCreate

        old = datetime.now(timezone.utc) - timedelta(days=100)
        db.add(AuditEvent(
            id=f"log_{uuid.uuid4().hex[:16]}",
            timestamp=old, service="loging_service", action="logging.admin",
            actor_id=None, actor_type="service", status="success",
            allowed=True, severity="INFO", details={},
        ))
        db.flush()

        # Пытаемся явно нацелиться на loging_service.
        repo.create_policy(
            db,
            RetentionPolicyCreate(
                retain_days=60, service_filter=["loging_service"]
            ),
        )

        deleted = apply_active(db)
        assert deleted == 0
        assert db.query(AuditEvent).count() == 1
