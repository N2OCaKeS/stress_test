"""Хелперы для повторяющегося аудит-паттерна `denied on permission failure`.

Десятки call-site'ов в сервисах и endpoints оборачивают
`permissions.require_action(...)` в одинаковую конструкцию:

```python
try:
    await permissions.require_action(...)
except AuthorizationError:
    audit_service.emit(action, ..., status="denied", allowed=False,
                       details={"reason": "permission_denied", ...})
    raise
```

Контекст-менеджер :func:`emit_denied_on_authz_error` сворачивает
boilerplate, сохраняя exact shape события (action, target_id, target_type,
status="denied", allowed=False, details["reason"]="permission_denied").
Дополнительные ключи в details передаются через ``extra_details`` — туда
обычно кладут ``server_id``, ``department_id``, ``role_name`` и т.п.,
которые в существующих call-site'ах живут рядом с reason.

Помогает только для **чистого** случая, когда:
- ловится ровно `AuthorizationError`,
- reason жёстко `permission_denied`,
- блок не делает ничего, кроме emit + re-raise.

Для более хитрых веток (несколько reason'ов через `isinstance(exc, ...)`,
custom reason типа `grant_sudo_denied`, нестандартный e.error_code branching)
лучше оставлять ручной try/except — попытка склеить всё в одну абстракцию
превращает простой код в API с десятком kwargs.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from src.core.exceptions import AuthorizationError
from src.schemas.identity import IdentityContext
from src.services import audit_service


@contextmanager
def emit_denied_on_authz_error(
    action: str,
    *,
    target_type: str,
    target_id: str | None = None,
    extra_details: dict | None = None,
    identity: IdentityContext | None = None,
) -> Iterator[None]:
    """Перехватить AuthorizationError, эмитнуть denied-audit и пробросить дальше.

    Эмит идёт с теми же полями, что и ручной аналог:

    ```python
    audit_service.emit(
        action,
        target_id=target_id,
        target_type=target_type,
        status="denied",
        allowed=False,
        details={"reason": "permission_denied", **(extra_details or {})},
    )
    ```

    `identity` опционален: если задан — в details попадает `subject_type`
    (user / bot / pat / oauth_client). Помогает SIEM'у фильтровать denied'ы
    по типу caller'а (например, искать аномалии в bot-traffic). Без identity
    остаёмся обратно-совместимыми с call-site'ами, которые его не пробрасывают.

    Контекст-менеджер sync, потому что `audit_service.emit` сам sync (он
    fire-and-forget'ит httpx-task внутри). Тело `with` может быть `await`
    или sync — нам важно только перехватить исключение.
    """
    try:
        yield
    except AuthorizationError:
        # reason="permission_denied" — фиксированный для этого паттерна, поэтому
        # пишется ПОСЛЕ extra_details, чтобы пользовательский spread не смог его
        # случайно переопределить. Совпадает с исходным `{**audit_details, "reason": ...}`
        # в permission_service.
        details: dict = dict(extra_details) if extra_details else {}
        details["reason"] = "permission_denied"
        if identity is not None and identity.subject_type is not None:
            # Не перетираем явный subject_type в extra_details (если call-site уже
            # положил его руками — он имеет приоритет).
            details.setdefault("subject_type", identity.subject_type)
        audit_service.emit(
            action,
            target_id=target_id,
            target_type=target_type,
            status="denied",
            allowed=False,
            details=details,
        )
        raise
