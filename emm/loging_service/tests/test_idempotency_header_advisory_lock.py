"""loging: Idempotency-Key header path + retention advisory_lock contention.

Покрывает два gap'а:

1. **Idempotency-Key header**. POST /events принимает ключ либо из
   `Idempotency-Key` header'а (стандартный путь), либо из `payload.idempotency_key`
   (legacy). Тесты на header-path отсутствовали — header-only ingest,
   header vs body conflict, NFKC-нормализация.

2. **Retention advisory_lock contention**. `tests/test_hardening.py`
   проверяет single-holder сценарий и стабильность ключа. Когда два
   tick'а конкурируют (две разные replica'ы, или две session'ы в одной
   replic'е) — второй должен gracefully пропустить через
   `pg_try_advisory_lock → False`.
"""

from __future__ import annotations

from sqlalchemy import select, text

from src.models.audit_event import AuditEvent

from tests.conftest import make_event

EVENTS_URL = "/api/logging/v1/events"


# ── 1. Idempotency-Key header path ────────────────────────────────────────────


class TestIdempotencyKeyHeader:
    def test_header_only_ingest_creates_event(self, client, auth_headers, db):
        """Header-only path: body без `idempotency_key`, header выставлен → 201,
        ключ персистится в БД."""
        payload = make_event()
        payload.pop("idempotency_key", None)
        r = client.post(
            EVENTS_URL,
            json=payload,
            headers={**auth_headers, "Idempotency-Key": "hdr-only-001"},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["id"].startswith("log_")

        stored = db.execute(
            select(AuditEvent).where(
                AuditEvent.service == "auth_service",
                AuditEvent.idempotency_key == "hdr-only-001",
            )
        ).all()
        assert len(stored) == 1

    def test_header_vs_body_conflict_returns_422(self, client, auth_headers):
        """Header `X` и body `Y` после NFKC расходятся → 422 VALIDATION_ERROR
        с явным error_code (caller противоречит сам себе)."""
        payload = make_event(idempotency_key="body-key-Y")
        r = client.post(
            EVENTS_URL,
            json=payload,
            headers={**auth_headers, "Idempotency-Key": "header-key-X"},
        )
        assert r.status_code == 422, r.text
        body = r.json()
        assert body["error_code"] == "VALIDATION_ERROR"
        # details содержат оба значения для caller-side диагностики.
        details = body.get("details") or {}
        assert details.get("header") == "header-key-X"
        assert details.get("body") == "body-key-Y"

    def test_header_nfkc_normalized(self, client, auth_headers, db):
        """Header проходит через NFKC-нормализатор и `strip()` перед
        charset-pattern check'ом. ASCII с whitespace-обвесом — самый
        репрезентативный случай: HTTP-headers ограничены ISO-8859-1, так что
        реальные fullwidth-варианты в transport'е не доходят, но
        `unicodedata.normalize("NFKC", raw).strip()` всё равно прогоняется и
        обязан схлопывать лидирующий/трейлинг whitespace до канонической
        формы."""
        payload = make_event()
        payload.pop("idempotency_key", None)
        raw_header = "   ascii-normalised-001   "  # padding strip'ом
        r = client.post(
            EVENTS_URL,
            json=payload,
            headers={**auth_headers, "Idempotency-Key": raw_header},
        )
        assert r.status_code == 201, r.text

        # В БД лежит уже нормализованная форма без whitespace.
        stored = db.execute(
            select(AuditEvent).where(
                AuditEvent.service == "auth_service",
                AuditEvent.idempotency_key == "ascii-normalised-001",
            )
        ).all()
        assert len(stored) == 1

    def test_header_normalize_unit_nfkc(self):
        """Unit-уровень: `_idempotency_key_from_header` действительно
        прогоняет NFKC и `strip()`. Headers через HTTP закрыты ASCII'ем
        (см. предыдущий тест), но helper-функция — публичный контракт
        ingest path'а и должна корректно работать на любых unicode-входах,
        чтобы внутренние callers (если появятся) получали ту же семантику."""
        from src.api.v1.endpoints.events import _idempotency_key_from_header

        # Fullwidth → ASCII.
        assert _idempotency_key_from_header("ＡＡＡ-１１") == "AAA-11"
        # Whitespace вокруг.
        assert _idempotency_key_from_header("  hdr-1  ") == "hdr-1"
        # Пустая строка после strip'а → None (как и raw None).
        assert _idempotency_key_from_header("   ") is None
        assert _idempotency_key_from_header(None) is None


# ── 2. Retention advisory_lock contention ─────────────────────────────────────


class TestRetentionAdvisoryLockContention:
    """Два holder'а на один ключ: первый берёт lock, второй gracefully
    пропускает (`pg_try_advisory_lock → False`).

    В дев-стенде это эквивалентно двум репликам в одном minutely tick'е:
    первая снимает lock в начале и держит его до конца sweep'а, вторая
    приходит, не получает lock, фиксирует `last_run = today` и идёт в
    следующий tick. Тест держит session-A с уже взятым lock'ом и
    имитирует попытку acquire из session-B на отдельном connection'е.
    """

    def test_second_holder_gets_false_when_first_holds(self, db, TestSessionLocal):
        """Sanity contention: session B на отдельном connection'е не получает
        lock пока session A его держит; после release lock доступен."""
        from src.main import _RETENTION_ADVISORY_LOCK_KEY

        # Holder A — берёт lock на основной test-session.
        locked_a = db.execute(
            text("SELECT pg_try_advisory_lock(:k)"),
            {"k": _RETENTION_ADVISORY_LOCK_KEY},
        ).scalar()
        assert locked_a is True

        try:
            # Holder B — независимая session с собственным connection'ом.
            # `pg_try_advisory_lock` per-session, не per-transaction, так что
            # одновременное удержание из двух разных sessions блокируется.
            other = TestSessionLocal()
            try:
                locked_b = other.execute(
                    text("SELECT pg_try_advisory_lock(:k)"),
                    {"k": _RETENTION_ADVISORY_LOCK_KEY},
                ).scalar()
                assert locked_b is False, (
                    "Второй holder должен gracefully пропустить tick, "
                    "пока первый держит lock"
                )
            finally:
                other.close()
        finally:
            # Release lock A; теперь следующий acquire проходит.
            db.execute(
                text("SELECT pg_advisory_unlock(:k)"),
                {"k": _RETENTION_ADVISORY_LOCK_KEY},
            )
            db.commit()

        # После release: lock снова acquirable.
        relocked = db.execute(
            text("SELECT pg_try_advisory_lock(:k)"),
            {"k": _RETENTION_ADVISORY_LOCK_KEY},
        ).scalar()
        assert relocked is True
        db.execute(
            text("SELECT pg_advisory_unlock(:k)"),
            {"k": _RETENTION_ADVISORY_LOCK_KEY},
        )
        db.commit()
