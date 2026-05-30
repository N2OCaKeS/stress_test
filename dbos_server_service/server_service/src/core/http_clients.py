"""Module-level pooled httpx.AsyncClient'ы для исходящих каналов server_service.

Большая часть outbound-каналов сервиса уже идёт через свои пулы:

* `dependencies/auth.py::_introspect_client` — introspect в auth_service.
* `services/audit_service.py::_audit_client` — audit-emit в loging_service.

Здесь — пулы для остальных read-only каналов, у которых раньше httpx-клиент
создавался per-call (`async with httpx.AsyncClient(...) as c: await c.get(...)`).
Под штатной drift-нагрузкой каждый запрос на `GET /servers/{id}/drift`
открывал свежий TCP+TLS до loging_service — на N серверах в дашборде это
N handshake'ов на одно обновление.

Контракт:

* `loging_read_client` — read-канал к loging_service (`GET /events` для
  drift-summary и подобных). Не пересекается с audit-emit pool'ом: тот
  держит `Authorization` per-request, а здесь — общий header в client'е.
  Раздельные пулы держим, чтобы read-трафик не выедал FD у write-канала,
  и наоборот (drift-burst при кратной инспекции дашбордом ↔ login flood).
* Лениво в lifespan startup, закрытие в shutdown.
* Outside lifecycle (unit-тесты без TestClient) — module-level slot остаётся
  `None`, потребитель идёт по per-call fallback'у. Симметрично паттерну
  introspect/audit.
"""

from __future__ import annotations

import httpx

# Pooled read-only client к loging_service. Инициализируется в `main.lifespan`
# startup, закрывается в shutdown. `None` outside the app lifecycle.
loging_read_client: httpx.AsyncClient | None = None
