#!/usr/bin/env python3
"""Добавить искусственную историю логов в локальный dev-стек без запуска тестов.

Повторный вызов не изменяет существующие записи. Нужны миграции testing_service
и стандартный dev-пользователь dep_admin1. Все попытки уже завершены.
"""
import hashlib
import os
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.types.json import Jsonb


def connect(database, port):
    return psycopg.connect(
        host=os.environ.get("PG_HOST", "localhost"), port=port,
        user=os.environ.get("PG_USER", "app_user"),
        password=os.environ.get("PG_PASS", "app_password"), dbname=database,
    )


def main():
    with connect("dev_auth", int(os.environ.get("PG_PORT", "5432"))) as auth:
        row = auth.execute("SELECT id, department_id FROM users WHERE username = 'dep_admin1'").fetchone()
        if not row or not row[1]:
            raise SystemExit("Сначала создайте стандартного dev-пользователя dep_admin1")
        actor, department = row
    with connect("dev_server", int(os.environ.get("PG_PORT", "5432"))) as server:
        row = server.execute("SELECT id FROM servers WHERE department_id = %s ORDER BY created_at LIMIT 1", (department,)).fetchone()
        if not row:
            raise SystemExit("В dev-отделе нет сервера для примеров")
        server_id = row[0]
    prefix = "example_logs_" + hashlib.sha256(department.encode()).hexdigest()[:8]
    base_time = datetime.now(timezone.utc) - timedelta(hours=3)
    with connect("dev_testing", int(os.environ.get("TESTING_PG_PORT", "5436"))) as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (prefix,))
        stand = db.execute("SELECT id FROM test_stands WHERE server_id = %s AND department_id = %s", (server_id, department)).fetchone()
        stand_id = stand[0] if stand else f"{prefix}_stand"
        if not stand:
            db.execute("INSERT INTO test_stands (id, server_id, department_id, queue_enabled, is_active, created_by) VALUES (%s,%s,%s,false,false,%s)", (stand_id, server_id, department, actor))
        names = ["Пример: проверка файловой системы", "Пример: нагрузка PostgreSQL", "Пример: сетевой обмен"]
        for i, name in enumerate(names):
            db.execute("INSERT INTO test_definitions (id, code, full_name, readiness, department_id, created_by) VALUES (%s,%s,%s,'development',%s,%s) ON CONFLICT (id) DO NOTHING", (f"{prefix}_test_{i}", f"EXAMPLE-{prefix[-8:]}-{['FS','DB','NET'][i]}", name, department, actor))
        run_id = f"{prefix}_run"
        db.execute("INSERT INTO test_runs (id, os_version_id, mode, kernel, department_id, test_run_stands, status, final, created_by, created_at) VALUES (%s,'1.7.1.44','orel','6.1.50-1-generic',%s,%s,'failed',false,%s,%s) ON CONFLICT (id) DO NOTHING", (run_id, department, Jsonb([stand_id]), actor, base_time))
        added = 0
        for i in range(126):
            item_id = f"{prefix}_{i:03}"
            test_index = 0 if i == 1 else i % 3
            test_id = f"{prefix}_test_{test_index}"
            failed = i % 7 == 0
            started = base_time + timedelta(minutes=i)
            finished = started + timedelta(seconds=45)
            debug = i >= 64 and i % 2 == 0
            context = {"RC": "1.7.1.44", "KERNEL": "6.1.50-1-generic", "MODE": "orel"}
            inserted = db.execute("""INSERT INTO queue_items
                (id, stand_id, test_id, launch_context, state, position, is_retry, retry_of_id,
                 debug_mode, test_run_id, created_by, created_at, started_at, finished_at, error)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING RETURNING id""", (
                item_id, stand_id, test_id, Jsonb(context), "failed" if failed else "succeeded", i,
                i == 1, f"{prefix}_000" if i == 1 else None, debug, run_id if i < 64 else None,
                actor, started, started, finished,
                "Искусственный пример: контрольная сумма не совпала" if failed else None,
            )).fetchone()
            if not inserted:
                continue
            added += 1
            blocks = [
                ("Подготовка окружения", "OK", "uname -r", "6.1.50-1-generic\nОкружение готово ✅"),
                ("Настройка теста", "CHANGED", "export TEST_TOKEN=***", "Параметры: duration=30s, workers=4\nСекрет замаскирован: ***"),
                (names[test_index], "FATAL" if failed else "OK", "python3 run.py --duration 30", "[00:00:10] Выполнено 320 операций\n[00:00:20] Проверка результата\n" + ("AssertionError: checksum mismatch\nОжидалось: 7e95c4; получено: 1b3a00\nRESULT: FAILED" if failed else "[00:00:30] Выполнено 960 операций\nОшибок: 0\nRESULT: PASSED")),
                ("Очистка окружения", "OK", "cleanup.py", "Временные данные удалены.\nЭто искусственный лог для просмотра страницы EMM."),
            ]
            content = ""
            segments = []
            for position, (label, status, command, output) in enumerate(blocks):
                start = len(content)
                content += f"{'*' * 66}\nTASK [{label}: dev-example]\nSTATUS [{status}]\nCOMMAND: {command}\n\nCONCLUSION: {output}\n{'*' * 66}\n\n"
                segments.append((position, label, status, command, start, len(content)))
            log_id = f"log_{item_id}"
            db.execute("INSERT INTO test_logs (id, queue_item_id, stand_id, test_id, os_version_major, rc, kernel, internal_path, size_bytes, created_at, protected) VALUES (%s,%s,%s,%s,'1.7','1.7.1.44','6.1.50-1-generic',%s,%s,%s,false)", (log_id, item_id, stand_id, test_id, f"examples/{item_id}.log", len(content), started))
            db.execute("INSERT INTO test_log_blobs (log_id, content) VALUES (%s,%s)", (log_id, content))
            for pos, label, status, command, start, end in segments:
                db.execute("INSERT INTO test_log_segments (id, log_id, position, kind, label, command_text_masked, status, started_at, finished_at, byte_offset_start, byte_offset_end) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", (f"seg_{item_id}_{pos}", log_id, pos, "checkpoint" if pos == 0 else "command", label, command, status, started, finished, start, end))
    print(f"Добавлено {added} искусственных попыток; всего в наборе 126 (64 в прогоне, 62 одиночных).")
    print(f"Прогон: {run_id}")
    print(f"http://localhost:5173/testing/logs?kind=campaign&test_run_id={run_id}")


if __name__ == "__main__":
    main()
