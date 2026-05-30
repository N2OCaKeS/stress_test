#!/bin/bash
# Полное локальное сканирование платформы (запускать вручную).
#
# CI/CD в репозитории намеренно отсутствует — единственный регулярный
# прогон тестов и линтеров делает владелец этой командой (или через
# `make scan`). Скрипт собирает:
#   - юнит-тесты всех 4 backend-сервисов (auth, logging, server, worker);
#   - кросс-сервисные интеграционные тесты;
#   - ruff на src/ каждого сервиса;
#   - опционально pip-audit и safety (если установлены).
#
# Любой невыполненный шаг (тесты/линтер) прерывает скан через set -e.
# pip-audit и safety не считаются критичными: их отсутствие в системе
# не валит скан, но в выводе явно отмечается, что проверка пропущена.
set -e

echo "== test-auth ==" && make test-auth
echo "== test-logging ==" && make test-logging
echo "== test-server ==" && make test-server
echo "== test-worker ==" && make test-worker
echo "== test-integration ==" && make test-integration

echo "== ruff check =="
for s in auth_service loging_service server_service server_worker; do
    (cd "$s" && ruff check src/)
done

echo "== pip-audit =="
if command -v pip-audit >/dev/null 2>&1; then
    pip-audit || true
else
    echo "(pip-audit не установлен — пропускаем)"
fi

echo "== safety check =="
if command -v safety >/dev/null 2>&1; then
    safety check || true
else
    echo "(safety не установлен — пропускаем)"
fi

echo "Full scan complete"
