"""Shared helpers for server_service tests.

Полезные мелочи, повторяющиеся в 20+ файлах:

* :func:`auth_hdr` — `_hdr` из каждого файла, один import избавляет от копий.
* :func:`make_emit_capture` — фабрика monkeypatch'а `audit_service.emit` сразу
  по списку модулей-importer'ов (раньше тот же блок копировали в `captured_emits`
  фикстуру в каждом тесте, отличался только список patch-сайтов).
* :func:`make_dispatch_capture` — типовой `worker_client.dispatch_task` /
  `dispatch_task_with_hit` capture; не покрывает специфичные case'ы (boom-
  patch с конкретным исключением), но 16+ файлов с happy-path patch'ем сюда
  ложатся напрямую.
* :func:`assert_error` — единая проверка `(status_code, error_code)` по
  envelope-схеме `src/schemas/common.py::ErrorResponse`. Заменяет голый
  `assert resp.status_code == 4xx` в test_internal_endpoints.py и аналогах.
"""

from __future__ import annotations

import base64
from typing import Any


def b64(plaintext: str) -> str:
    """`base64.b64encode(plaintext)` — write-эндпоинты принимают пароли так."""
    return base64.b64encode(plaintext.encode("utf-8")).decode("ascii")


def auth_hdr(token: str, dept: str | None = None) -> dict[str, str]:
    """Шорткат для `Authorization: Bearer <token>` (+ опционально `X-Target-Department-Id`).

    Часть тестов кросс-департамент-кейсов передаёт department override через
    заголовок `X-Target-Department-Id`. Если `dept` задан — добавляется в headers.
    """
    headers = {"Authorization": f"Bearer {token}"}
    if dept is not None:
        headers["X-Target-Department-Id"] = dept
    return headers


# Alias под имя из task-спеки: семантически то же самое, что `auth_hdr` без dept.
bearer_header = auth_hdr


def assert_error(resp: Any, status: int, error_code: str | None = None) -> dict:
    """Проверить, что `resp` — error-envelope с ожидаемыми `status` и `error_code`.

    Возвращает распарсенный JSON-body для дальнейших ассертов на `details`/
    `request_id`. Если `error_code` не задан — проверяется только status.

    Использовать вместо голого `assert resp.status_code == 4xx`, чтобы
    зафиксировать стабильный контракт `error_code` (каталог в
    ``API_ENDPOINTS.md``).
    """
    assert resp.status_code == status, (
        f"expected status {status}, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    if error_code is not None:
        assert body.get("error_code") == error_code, (
            f"expected error_code={error_code!r}, got {body.get('error_code')!r}: {body}"
        )
    return body


def make_emit_capture(
    monkeypatch,
    *extra_patch_paths: str,
    include_default: bool = True,
) -> list[dict]:
    """Перехватить `audit_service.emit` сразу в нескольких точках вызова.

    Возвращает список словарей `{action, actor_id, **kwargs}`. Список
    дополняется in-place — caller хранит ссылку и читает после теста.

    `include_default=True` всегда патчит общий модуль
    ``src.services.audit_service.emit``. Дополнительные пути (например
    ``src.api.v1.endpoints.worker_dispatch.audit_service.emit``) передаются
    через ``*extra_patch_paths`` — patch'аются той же фейк-функцией.

    Если extra-путь резолвится в несуществующий модуль/атрибут (импортёр был
    удалён или ещё не дотащил `import audit_service`), он молча скипается:
    локальные фикстуры исторически прятали эту резолюцию в try/except, и
    общий helper повторяет ту же семантику, иначе любая чистка импортов
    ломала десятки тестов.
    """
    captured: list[dict] = []

    def fake_emit(action: str, actor_id: Any = None, **kwargs: Any) -> None:
        captured.append({"action": action, "actor_id": actor_id, **kwargs})

    if include_default:
        import src.services.audit_service as audit_mod
        monkeypatch.setattr(audit_mod, "emit", fake_emit)
    for path in extra_patch_paths:
        try:
            monkeypatch.setattr(path, fake_emit)
        except (AttributeError, ImportError):
            pass
    return captured


def make_dispatch_capture(
    monkeypatch,
    *extra_patch_paths: str,
) -> list[dict]:
    """Перехватить `worker_client.dispatch_task[_with_hit]` happy-path'ом.

    Возвращает список dict'ов с записанными call-kwargs. Кейсы с boom-патчем
    (`raise ServiceUnavailableError`) этот хелпер не покрывают — caller сам
    конструирует raising-замыкание и патчит через monkeypatch.setattr напрямую.

    `extra_patch_paths` — endpoint-модули, импортнувшие `worker_client`
    в своё пространство имён.
    """
    calls: list[dict] = []
    _by_key: dict[str, str] = {}

    async def fake_dispatch(*, db=None, task_kind, target_server_id, payload,
                            created_by, request_id,
                            target_resource_id=None, idempotency_key=None,
                            return_hit=False):
        if idempotency_key is not None and idempotency_key in _by_key:
            existing = _by_key[idempotency_key]
            return (existing, True) if return_hit else existing
        calls.append({
            "task_kind": task_kind,
            "target_server_id": target_server_id,
            "target_resource_id": target_resource_id,
            "payload": payload,
            "created_by": created_by,
            "request_id": request_id,
            "idempotency_key": idempotency_key,
        })
        new_id = f"tsk_{task_kind.replace('.', '_')}_fake_{len(calls)}"
        if idempotency_key is not None:
            _by_key[idempotency_key] = new_id
        return (new_id, False) if return_hit else new_id

    async def fake_dispatch_with_hit(**kwargs):
        kwargs["return_hit"] = True
        return await fake_dispatch(**kwargs)

    import src.services.worker_client as worker_mod
    monkeypatch.setattr(worker_mod, "dispatch_task", fake_dispatch)
    monkeypatch.setattr(worker_mod, "dispatch_task_with_hit", fake_dispatch_with_hit)
    for path in extra_patch_paths:
        if path.endswith(".dispatch_task"):
            monkeypatch.setattr(path, fake_dispatch)
        elif path.endswith(".dispatch_task_with_hit"):
            monkeypatch.setattr(path, fake_dispatch_with_hit)
        else:
            raise ValueError(
                f"unsupported dispatch patch path: {path!r} "
                "(must end with .dispatch_task or .dispatch_task_with_hit)"
            )
    return calls
