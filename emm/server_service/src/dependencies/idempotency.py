"""HTTP `Idempotency-Key` header — чтение и валидация длины.

Worker-БД (`dev_server_worker.tasks`) держит колонку `idempotency_key` как
`VARCHAR(128)` с UNIQUE. Server-side fan-out (например, массовая ротация
паролей в `worker_dispatch.fanout_*`) дописывает к клиентскому ключу
суффикс `":<server.id>"`, где `server.id` — `srv_<uuid4.hex>` длиной 36
символов. Колоночный лимит 128, минус 1 байт двоеточия и максимальный
запас под server.id (64 символа в схеме `models/server.py`) даёт верхний
лимит для клиентского ключа.

Без guard'а длинный клиентский ключ ронял INSERT в worker-БД с DataError
на per-server строке — fan-out возвращал 503 `WORKER_UNREACHABLE` для
всего массового вызова. Теперь длинный ключ режется на входе с понятным
400 `IDEMPOTENCY_KEY_TOO_LONG`.
"""

from __future__ import annotations

from fastapi import Request

from src.core.exceptions import BadRequestError


# Жёсткий потолок длины клиентского `Idempotency-Key`. Запас под суффикс
# `:srv_<uuid4.hex>` (37 байт: ":" + 4-байтовый префикс + 32 hex-символа),
# плюс несколько байт хвоста на случай изменения формата server.id.
# 128 (column) - 41 = 87, ровно покрывает UUID-style ключи (32 / 36 chars).
IDEMPOTENCY_KEY_MAX_LEN = 87


def read_idempotency_key(request: Request) -> str | None:
    """Прочитать заголовок `Idempotency-Key`, валидировать длину.

    * Возвращает `None`, если клиент header не прислал или прислал пустую
      строку — endpoint трактует это как «без дедупликации».
    * Поднимает `BadRequestError(IDEMPOTENCY_KEY_TOO_LONG)`, если длина
      превышает `IDEMPOTENCY_KEY_MAX_LEN`. Per-server fan-out (`mass`
      rotate / `fanout_update_on_host`) приписывает `:<server.id>` к
      этому ключу; без guard'а concat вылезает за `VARCHAR(128)` и
      INSERT в worker-БД падает с DataError.

    Зовётся напрямую endpoint'ами (`request.headers` остаётся доступным
    через FastAPI `Request`), отдельный `Depends`-биндинг не нужен —
    хелпер дешёвый и точечный.
    """
    raw = request.headers.get("Idempotency-Key")
    if not raw:
        return None
    if len(raw) > IDEMPOTENCY_KEY_MAX_LEN:
        raise BadRequestError(
            error_code="IDEMPOTENCY_KEY_TOO_LONG",
            message=(
                f"Idempotency-Key header is too long "
                f"(max {IDEMPOTENCY_KEY_MAX_LEN} chars, got {len(raw)})"
            ),
            details={"max_length": IDEMPOTENCY_KEY_MAX_LEN, "got": len(raw)},
        )
    return raw
