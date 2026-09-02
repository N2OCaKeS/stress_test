"""Тесты конкуррентных сценариев.

upsert_events теперь использует `INSERT ... ON CONFLICT (service, action) DO
UPDATE` — параллельные регистрации одного (service, action) не падают
`IntegrityError`, оба завершаются успешно (один INSERT, второй UPDATE).
"""

import threading

import pytest

from src.repositories import service_events as se_repo


class TestUpsertEventsHappyPath:
    def test_sequential_upsert_two_services_same_action(self, db, TestSessionLocal):
        """В разных сервисах action='user.login' хранится независимо."""
        s1 = TestSessionLocal()
        s2 = TestSessionLocal()
        try:
            se_repo.upsert_events(s1, "auth_service",
                                  [{"action": "user.login", "description": "auth"}])
            se_repo.upsert_events(s2, "config_service",
                                  [{"action": "user.login", "description": "config"}])
        finally:
            s1.close()
            s2.close()

        s3 = TestSessionLocal()
        try:
            auth_events, _ = se_repo.list_for_service(s3, "auth_service")
            config_events, _ = se_repo.list_for_service(s3, "config_service")
            assert len(auth_events) == 1
            assert len(config_events) == 1
        finally:
            s3.close()

    def test_sequential_upsert_same_service_same_action_updates(self, db, TestSessionLocal):
        """Повторный upsert одного и того же (service, action) обновляет, не дублирует."""
        s = TestSessionLocal()
        try:
            added1, updated1 = se_repo.upsert_events(s, "auth_service",
                                                    [{"action": "user.login",
                                                      "description": "v1"}])
            assert added1 == 1 and updated1 == 0
            added2, updated2 = se_repo.upsert_events(s, "auth_service",
                                                    [{"action": "user.login",
                                                      "description": "v2"}])
            assert added2 == 0 and updated2 == 1
            events, total = se_repo.list_for_service(s, "auth_service")
            assert total == 1
            assert events[0].description == "v2"
        finally:
            s.close()


class TestConcurrentUpsertRace:
    def test_concurrent_upsert_same_action_no_integrity_error(self, db, TestSessionLocal):
        """Два параллельных upsert на один (service, action) — оба проходят.

        ON CONFLICT DO UPDATE превращает race на одной (service, action) в
        idempotent operation: первый — INSERT, второй — UPDATE на том же row.
        """
        errors: list[Exception] = []
        barrier = threading.Barrier(2)

        def worker():
            s = TestSessionLocal()
            try:
                barrier.wait()
                se_repo.upsert_events(
                    s, "auth_service",
                    [{"action": "user.race", "description": "concurrent"}],
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                s.close()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"unexpected upsert race errors: {errors!r}"

        # В таблице должен лежать ровно один row.
        s = TestSessionLocal()
        try:
            rows, total = se_repo.list_for_service(s, "auth_service")
            assert total == 1
            assert rows[0].action == "user.race"
        finally:
            s.close()
