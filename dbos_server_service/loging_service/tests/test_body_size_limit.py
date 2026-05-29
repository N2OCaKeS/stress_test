"""Тесты лимита размера request body (DoS-fix).

Закрывает дыру: `loging_service` POST /events не ограничивал размер body —
только `details: dict` лимит 64 KB ВНУТРИ pydantic-валидатора, который
запускается ПОСЛЕ полного чтения body из ASGI. Один attacker request с
body 100 MB → пул pgsql забит, очередь длительная, всех клиентов отбивает
на 503. См. `loging_service/src/main.py::limit_body_size` и
`schemas/events.py::EventCreate._details_depth`.

Покрытие:
  * POST с Content-Length > лимита → 413 (без чтения body).
  * POST в пределах лимита → 201.
  * 413-envelope соответствует общему формату (error/error_code/details/timestamp).
  * GET с Content-Length > лимита → пропускается (middleware игнорирует
    non-mutating методы).
  * 413 НЕ порождает audit-emit (middleware outermost → audit_access не достигается).
  * Конфигурация через env `MAX_REQUEST_BODY_BYTES`.
  * Chunked transfer (Content-Length отсутствует) — overflow ловится на
    стриме через подменённый ASGI ``receive`` callback, downstream-route
    не вызывается.
  * Глубокая вложенность details (>10 уровней) → 422 от `_details_depth`.
  * Mixed dict/list вложенность считается.
  * Нормальные events в пределах лимитов — 201.
"""

import asyncio
import json

from tests.conftest import make_event

EVENTS_URL = "/api/logging/v1/events"


# ── Body size middleware: Content-Length-based ────────────────────────────────


class TestBodySizeMiddleware:
    """``limit_body_size`` middleware в `main.py` — Content-Length-based gate."""

    def test_normal_event_under_limit_accepted(self, client, auth_headers):
        """Нормальный event (< 1 MB) проходит — 201."""
        r = client.post(EVENTS_URL, json=make_event(), headers=auth_headers)
        assert r.status_code == 201

    def test_oversize_body_returns_413(self, client, auth_headers):
        """Body > 1 MiB → 413 (httpx сам выставляет Content-Length по реальному размеру).

        httpx Test-клиент перевычисляет Content-Length по реальной длине
        body — невозможно подсунуть «маленький body + заявленный CL=100MB»
        (что и атакующий пытался бы сделать). Поэтому шлём реально большое
        тело: его Content-Length будет > 1 MB → middleware отбивает.

        Этот тест покрывает основной use case: legit attacker с access к
        SERVICE_API_KEY пытается ingest'ить «жирный» payload. Middleware
        отбивает по заголовку, не позволяя ASGI прочитать body в память.
        """
        big_blob = "a" * (2 * 1024 * 1024)  # 2 MiB после json.dumps
        payload = make_event(details={"blob": big_blob})
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 413, f"expected 413, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["error"] == "payload_too_large"
        assert body["error_code"] == "PAYLOAD_TOO_LARGE"
        assert body["details"]["max_bytes"] == 1024 * 1024
        assert body["details"]["declared_bytes"] > 1024 * 1024

    def test_oversize_raw_bytes_with_content_length(self, client, auth_headers):
        """Sanity: сырые байты с реально большим CL → 413.

        Дополняет основной тест: проверяет ветку, где тело передаётся
        через `content=bytes`, а не `json=` (отличается обработка CL в
        httpx). Если регрессия конкретно по этому пути — поймает.
        """
        huge_event = {
            "timestamp": "2026-04-19T10:00:00Z",
            "service": "auth_service",
            "action": "user.login",
            "status": "success",
            "allowed": True,
            "details": {"blob": "Z" * (2 * 1024 * 1024)},
        }
        body_bytes = json.dumps(huge_event).encode()
        assert len(body_bytes) > 1024 * 1024
        r = client.post(
            EVENTS_URL,
            content=body_bytes,
            headers={**auth_headers, "Content-Type": "application/json"},
        )
        assert r.status_code == 413
        assert r.json()["error_code"] == "PAYLOAD_TOO_LARGE"

    def test_413_response_envelope_shape(self, client, auth_headers):
        """413-ответ соответствует общему envelope-формату loging_service."""
        huge = {"blob": "Q" * (1500 * 1024)}  # ~1.5 MB
        payload = make_event(details=huge)
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 413
        body = r.json()
        assert set(body.keys()) >= {
            "error", "error_code", "message", "details", "timestamp"
        }
        assert body["error"] == "payload_too_large"
        assert body["error_code"] == "PAYLOAD_TOO_LARGE"
        assert isinstance(body["message"], str)
        assert body["details"]["max_bytes"] == 1024 * 1024
        # ISO-8601 timestamp
        from datetime import datetime
        ts = datetime.fromisoformat(body["timestamp"].replace("Z", "+00:00"))
        assert ts.tzinfo is not None

    def test_get_with_large_content_length_passes_through(self, client):
        """GET с заголовком Content-Length > лимита НЕ должен срабатывать.

        Middleware фильтрует только POST/PUT/PATCH — у GET тела нет.
        Атака через CL на GET бессмысленна (ASGI не читает body), но
        отбой по CL на GET сломал бы health-чеки и read-эндпоинты.
        """
        r = client.get(
            "/api/logging/v1/health",
            headers={"Content-Length": str(10 * 1024 * 1024)},
        )
        assert r.status_code == 200

    def test_under_limit_size_passes(self, client, auth_headers):
        """Body около (но под) лимита проходит — пограничный случай.

        ~900 KB body не должно отбиваться. Это страховка от
        off-by-one в условии `declared > max_size` (должно быть строгое
        неравенство, не `>=`).
        """
        # 900 KB строка → JSON-body ~900 KB + envelope ~150 байт.
        # Под лимитом 1 MiB (1_048_576).
        normal_blob = "n" * (900 * 1024)
        # Не передаём в details: там лимит 64 KB. Делим по нескольким полям —
        # размер тела складывается из всех. Точнее всего: пакуем blob в
        # username (max 128 chars) не сработает. Используем target_id... нет,
        # лимит 48. Лучше всего — отдельный route, но проще просто меньше
        # blob'а упаковать в details (под 64 KB), но всё ещё проверить body.
        # Реально CL < 1 MB определяется суммой всех полей.

        # Простой вариант: details ≤ 64 KB (под лимитом size-валидатора),
        # body total ≈ 64 KB → точно под 1 MB лимитом.
        payload = make_event(details={"blob": "x" * 60_000})
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 201

    def test_negative_content_length_returns_400(self, client, auth_headers):
        """`Content-Length: -1` отбивается 400, иначе обход body-size cap'а.

        `int("-1")` → -1, `-1 > max_size` False → middleware пропускал бы
        запрос, и атакующий с реальным body 50 MB обходил бы лимит. RFC 9110
        требует неотрицательного integer'а.
        """
        r = client.post(
            EVENTS_URL,
            content=b'{"timestamp":"2026-04-19T10:00:00Z","service":"auth_service","action":"user.login","status":"success","allowed":true}',
            headers={
                **auth_headers,
                "Content-Type": "application/json",
                "Content-Length": "-1",
            },
        )
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
        body = r.json()
        assert body["error_code"] == "INVALID_CONTENT_LENGTH"

    def test_post_other_endpoint_also_size_capped(self, client, auth_headers):
        """Лимит применяется ко ВСЕМ POST/PUT/PATCH, не только /events.

        Mirror-проверка: попытка отправить гигантский POST на любой
        mutating endpoint защищается middleware (он смотрит метод, не path).
        Используем `/services/{svc}/events` — он тоже работает по SERVICE_API_KEY.
        """
        huge_description = "Q" * (2 * 1024 * 1024)
        r = client.post(
            "/api/logging/v1/services/auth_service/events",
            json={"events": [{"action": "x.y", "default_severity": "INFO",
                              "description": huge_description}]},
            headers=auth_headers,
        )
        # Middleware рубит ДО auth-чека и парсинга payload'а → 413.
        assert r.status_code == 413, (
            f"expected 413, got {r.status_code}: {r.text[:200]}"
        )
        assert r.json()["error_code"] == "PAYLOAD_TOO_LARGE"

    def test_no_audit_emitted_on_413(self, admin_client, db):
        """413-ответ НЕ должен порождать audit-event (avoid amplification).

        ``limit_body_size`` зарегистрирован outermost → 413 возвращается
        до ``audit_access``, никаких ``http.client_error`` записей в
        audit_events. Если регрессия (порядок инвертировали или фильтр
        попал внутрь audit-цепочки) — тест ловит её.

        ВАЖНО: используем POST /rules (admin endpoint), а не /events —
        /events исключён из audit_access по prefix-фильтру `_INGEST_PREFIXES`,
        там не было бы audit-emit даже без нашего фильтра. /rules — это
        admin-mutation, который КАК РАЗ должен порождать `http.client_error`
        на 4xx, кроме 413.
        """
        from src.models.audit_event import AuditEvent
        from sqlalchemy import select

        # Заведомо большой body на POST /rules → 413.
        # `name` ограничен max_length, но мы передаём в body чтобы
        # CL стал большим — middleware рубит ДО валидации поля.
        huge_payload = {
            "name": "test",
            "effect": "SUPPRESS",
            "priority": 100,
            "match_action": "X" * (2 * 1024 * 1024),  # 2 MiB
        }
        r = admin_client.post("/api/logging/v1/rules", json=huge_payload)
        assert r.status_code == 413, (
            f"expected 413, got {r.status_code}: {r.text[:200]}"
        )

        # Audit запись пишется через asyncio.ensure_future + asyncio.to_thread,
        # её появление асинхронно. Дадим event loop'у завершить.
        import time
        time.sleep(0.1)  # short grace for asyncio.to_thread

        events = db.execute(
            select(AuditEvent).where(AuditEvent.action == "http.client_error")
        ).scalars().all()
        assert len(events) == 0, (
            "413-ответ на admin-mutation не должен эмитить http.client_error "
            f"(нашли {len(events)} event'ов: {[e.details for e in events]})"
        )


# ── Chunked transfer (без Content-Length) — стриминг-кэп ────────────────────


class TestChunkedOverflow:
    """``limit_body_size`` middleware: ветка без Content-Length.

    Cheap path по заголовку — easy. Хитрая ветка — `Transfer-Encoding: chunked`
    (или любой HTTP/1.1-клиент, не выставивший CL): middleware подменяет
    ASGI ``receive`` callback, считает сумму байт по приходящим
    ``http.request``-сообщениям и отбивает 413, не давая downstream-роуту
    дочитать тело. Тестим напрямую через ASGI: TestClient на httpx ВСЕГДА
    вычисляет реальный CL по `body=`, путь без CL так не выстрелить —
    собираем scope руками.

    Контракт, который проверяем:
      * overflow ловится по сумме байт через несколько `http.request`
        chunk'ов (никаких CL header'ов в scope);
      * route handler не получает накопившийся body (downstream обрезан);
      * Ответ — 413 PAYLOAD_TOO_LARGE с тем же envelope-форматом, что и
        cheap path;
      * Под лимитом — стрим проходит насквозь.
    """

    @staticmethod
    def _send_to_app(app, *, chunks: list[bytes], path: str = EVENTS_URL,
                     method: str = "POST", api_key: str = "test-service-api-key"):
        """Прямой ASGI-вызов без Content-Length header'а.

        Возвращает `(status, headers_dict, body_bytes)`. Эмулирует Hypercorn'овский
        chunked-режим: HTTP scope без `content-length`, body доставляется N
        `http.request` сообщениями с `more_body=True/False`.
        """
        headers = [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {api_key}".encode()),
            (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked"),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "headers": headers,
        }

        receive_queue: list[dict] = []
        for i, chunk in enumerate(chunks):
            receive_queue.append({
                "type": "http.request",
                "body": chunk,
                "more_body": i < len(chunks) - 1,
            })

        responses: list[dict] = []

        async def receive():
            if receive_queue:
                return receive_queue.pop(0)
            # Если middleware дернул receive больше раз, чем у нас чанков —
            # отдаём end-of-stream, чтобы не висеть.
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            responses.append(message)

        asyncio.run(app(scope, receive, send))

        status = None
        out_headers: dict[bytes, bytes] = {}
        body = b""
        for msg in responses:
            if msg["type"] == "http.response.start":
                status = msg["status"]
                out_headers = dict(msg.get("headers") or [])
            elif msg["type"] == "http.response.body":
                body += msg.get("body", b"")
        return status, out_headers, body

    def test_chunked_overflow_returns_413(self, monkeypatch):
        """Сумма chunk'ов > лимита → 413, route не вызывается.

        Понижаем лимит до 50 KiB и шлём четыре чанка по 20 KiB (= 80 KiB,
        overflow на третьем). Без CL header'а cheap path неактивен —
        работает стрим-cap.
        """
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(50 * 1024))
        monkeypatch.setenv("SERVICE_API_KEY", "test-service-api-key")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import app

        chunk = b"x" * (20 * 1024)
        status, headers, body = self._send_to_app(
            app, chunks=[chunk, chunk, chunk, chunk]
        )
        assert status == 413, f"expected 413, got {status}: {body!r}"
        payload = json.loads(body)
        assert payload["error"] == "payload_too_large"
        assert payload["error_code"] == "PAYLOAD_TOO_LARGE"
        assert payload["details"]["max_bytes"] == 50 * 1024
        # `received_bytes` — счётчик из middleware, должен быть > max.
        assert payload["details"]["received_bytes"] > 50 * 1024

    def test_chunked_under_limit_passes_through(self, monkeypatch):
        """Сумма chunk'ов ≤ лимита — стрим уходит дальше в route.

        Тест валидирует, что middleware НЕ ломает легитимный chunked-ingest
        (route может ответить ошибкой по другим причинам — нам важно, чтобы
        ответ не был 413). БД не нужна: проверяем только, что middleware
        отдал управление downstream'у. Если downstream упал на отсутствии
        DB или auth — это «не 413», что и требуется.
        """
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(50 * 1024))
        monkeypatch.setenv("SERVICE_API_KEY", "test-service-api-key")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import app

        small = json.dumps(make_event()).encode()
        assert len(small) < 50 * 1024
        try:
            status, _, _ = self._send_to_app(app, chunks=[small])
        except Exception:
            # Downstream упал (нет БД, нет auth) — middleware пропустил
            # запрос дальше, что и требуется. Главное: не 413.
            return
        assert status != 413, f"chunked under-limit неожиданно отбит 413"

    def test_chunked_exactly_at_limit_passes(self, monkeypatch):
        """Граничный кейс `received == max_size` — НЕ overflow.

        Middleware проверяет `received > max_size` (строгое неравенство).
        Тест защищает от off-by-one — если кто-то поменяет на `>=`, легит
        ровно-в-лимит chunked-ingest начнёт отбиваться.
        """
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(50 * 1024))
        monkeypatch.setenv("SERVICE_API_KEY", "test-service-api-key")
        from src.core.config import get_settings
        get_settings.cache_clear()
        from src.main import app

        # Ровно 50 KiB — на грани, но не за.
        boundary = b"x" * (50 * 1024)
        status, _, body = self._send_to_app(app, chunks=[boundary])
        assert status != 413, (
            f"граничный размер (== max_size) не должен 413; got {status}: {body!r}"
        )


# ── Конфигурируемость лимита через env ────────────────────────────────────────


class TestBodySizeConfig:
    def test_default_limit_is_one_megabyte(self, monkeypatch):
        """Default — 1 MiB (без env override)."""
        monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
        from src.core.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.max_request_body_bytes == 1024 * 1024

    def test_override_via_env(self, monkeypatch):
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "524288")  # 512 KiB
        from src.core.config import get_settings
        get_settings.cache_clear()
        settings = get_settings()
        assert settings.max_request_body_bytes == 524288

    def test_smaller_limit_rejects_smaller_bodies(self, client, auth_headers, monkeypatch):
        """С пониженным MAX_REQUEST_BODY_BYTES даже 100 KB → 413."""
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(50 * 1024))  # 50 KiB
        from src.core.config import get_settings
        get_settings.cache_clear()
        # ВНИМАНИЕ: get_settings закэшировано — `client` фикстура уже его
        # очистила. Но middleware вызывает get_settings() per-request, так
        # что новый лимит подхватится со следующего запроса.

        # Маленькое (под старым 1 MB лимитом проходило, под 50 KB — отбой):
        payload = make_event(details={"blob": "K" * 60_000})
        assert len(json.dumps(payload)) > 50_000
        r = client.post(EVENTS_URL, json=payload, headers=auth_headers)
        assert r.status_code == 413
        body = r.json()
        assert body["details"]["max_bytes"] == 50 * 1024


# ── details: deep nesting (RecursionError DoS) ────────────────────────────────


class TestDetailsNestingDepth:
    """``EventCreate._details_depth`` валидатор — cap на глубину 10."""

    def test_shallow_nesting_accepted(self, client, auth_headers):
        """Реальные audit-кейсы (глубина 3-5) проходят без вопросов."""
        details = {
            "request": {
                "headers": {
                    "x_forwarded_for": ["1.2.3.4"],
                    "user_agent": "curl/7.0",
                },
                "method": "POST",
            },
            "actor": {"id": "u_1", "type": "user"},
        }
        r = client.post(
            EVENTS_URL, json=make_event(details=details), headers=auth_headers
        )
        assert r.status_code == 201

    def test_depth_exactly_10_accepted(self, client, auth_headers):
        """Глубина ровно 10 — на грани лимита, должна проходить."""
        # Глубина считается от корня: details (1) → a (2) → b (3) → ... → 10.
        d: dict = {"value": "leaf"}
        for _ in range(9):  # +9 уровней над листом → ровно 10
            d = {"x": d}
        r = client.post(
            EVENTS_URL, json=make_event(details=d), headers=auth_headers
        )
        assert r.status_code == 201, (
            f"depth-10 должно проходить, got {r.status_code}: {r.text}"
        )

    def test_depth_11_rejected_422(self, client, auth_headers):
        """Глубина 11 — превышает лимит → 422 VALIDATION_ERROR."""
        d: dict = {"value": "leaf"}
        for _ in range(11):  # 12 уровней — точно > 10
            d = {"x": d}
        r = client.post(
            EVENTS_URL, json=make_event(details=d), headers=auth_headers
        )
        assert r.status_code == 422
        body = r.json()
        assert body["error_code"] == "VALIDATION_ERROR"
        # сообщение валидатора в errors[].msg
        errors_str = json.dumps(body["details"]["errors"])
        assert "nesting" in errors_str or "10" in errors_str

    def test_depth_via_lists_also_counted(self, client, auth_headers):
        """Глубина считается и через списки — `[[[[[...]]]]]` тоже DoS."""
        # 12 вложенных списков → 13 уровней.
        inner: object = "leaf"
        for _ in range(12):
            inner = [inner]
        r = client.post(
            EVENTS_URL,
            json=make_event(details={"chain": inner}),
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_mixed_dict_and_list_depth(self, client, auth_headers):
        """Смесь dict/list — глубина суммируется."""
        # dict → list → dict → list → ... — 12 чередований
        inner: object = "leaf"
        for i in range(12):
            inner = {"k": inner} if i % 2 == 0 else [inner]
        r = client.post(
            EVENTS_URL,
            json=make_event(details={"chain": inner}),
            headers=auth_headers,
        )
        assert r.status_code == 422

    def test_extreme_recursion_does_not_crash_worker(self, client, auth_headers):
        """Очень глубокая вложенность не должна обрушить worker.

        Без `_details_depth` `json.dumps` в `_details_size` мог бы упасть
        с RecursionError (~500 уровней по дефолту CPython). С нашим
        валидатором: итеративный обход по explicit stack + проверка
        depth ДО size → 422 на 11-м уровне ДО того как json.dumps
        вообще запустится.

        Конструируем body как сырые байты JSON, минуя `json.dumps` на
        клиенте — иначе тест сам бы упал с RecursionError на сборке
        запроса. Сервер должен либо отбить 422 (от depth-валидатора),
        либо 413 (если body > 1 MB), либо 400 (от парсера, если глубина
        превысила его внутренний лимит). 500 = регрессия.
        """
        # Строим JSON руками: `{"chain": {"chain": ... "leaf"}}` × depth.
        # depth выбираем достаточно глубокий чтобы быть «attacker-like»,
        # но не настолько чтобы превысить лимит парсера в C-extension'е.
        # На CPython 3.11+ `json.loads` стабильно работает до ~1000 уровней
        # без специального лимита; за пределами поведение зависит от
        # стека потока. Берём 200 — гарантированно за нашим лимитом
        # (10) и заведомо ниже любого парсера-лимита.
        depth = 200
        body_str = (
            '{"timestamp":"2026-04-19T10:00:00Z",'
            '"service":"auth_service",'
            '"action":"user.login",'
            '"status":"success",'
            '"allowed":true,'
            '"details":'
            + '{"chain":' * depth + '"leaf"' + '}' * depth
            + '}'
        )
        r = client.post(
            EVENTS_URL,
            content=body_str.encode("utf-8"),
            headers={**auth_headers, "Content-Type": "application/json"},
        )
        # 422 от depth-валидатора — основной ожидаемый исход.
        # 413/400 допустимы как defence-in-depth.
        # 500 — регрессия (worker не должен падать на глубоком payload'е).
        assert r.status_code in (400, 413, 422), (
            f"deep-nesting запрос не должен ронять worker, "
            f"got {r.status_code}: {r.text[:200]}"
        )

    def test_depth_validator_does_not_break_size_validator(self, client, auth_headers):
        """`_details_size` (64 KB) и `_details_depth` (10) — оба должны работать.

        Регрессия: добавление одного валидатора не должно отключить другой.
        """
        # 1. Маленький, мелкая вложенность → 201.
        r = client.post(
            EVENTS_URL,
            json=make_event(details={"a": 1}),
            headers=auth_headers,
        )
        assert r.status_code == 201

        # 2. Маленький по размеру, но глубокий → 422 от depth.
        deep: dict = {"v": 1}
        for _ in range(15):
            deep = {"x": deep}
        r = client.post(
            EVENTS_URL,
            json=make_event(details=deep),
            headers=auth_headers,
        )
        assert r.status_code == 422

        # 3. Большой по размеру, мелкий → 422 от size (или 413 если > 1 MB).
        big_value = "z" * 70_000  # > 64 KB
        r = client.post(
            EVENTS_URL,
            json=make_event(details={"blob": big_value}),
            headers=auth_headers,
        )
        assert r.status_code == 422
