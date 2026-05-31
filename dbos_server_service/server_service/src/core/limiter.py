"""SlowAPI Limiter, вынесен из `src.main` ради разрыва цикла.

Endpoint-модули вешают `@limiter.limit(...)` декораторы на свои handler'ы —
например, на анонимный `/os-versions` для отбивки enumeration-сканера. Если
limiter жил бы в `src.main`, эти модули импортировали бы `main` ещё до того,
как тот собрал `app`: endpoints/* подтягиваются через `src.api.router`,
который импортируется уже в момент сборки `app`. Получался бы циклический
import. Нейтральный модуль решает это раз и навсегда.

Limiter держит default-лимиты из `settings.global_rate_limit` (глобальная
slowloris-защита 401-pipeline'а), а endpoint-декораторы добавляют более
жёсткие лимиты поверх — slowapi применяет каждый лимит независимо
(пробит хотя бы один → 429).

**Start-time settings:** `_settings = get_settings()` читается ОДИН раз при
импорте модуля. Значение `global_rate_limit` фиксируется тогда же и в
дальнейшем не перечитывается — runtime-смена настроек требует перезапуска
процесса. Это сознательный выбор: декораторы `@limiter.limit(...)` на
endpoint'ах резолвят limit-expression при импорте handler'а, динамика на
горячую всё равно бы потребовала пересборки routing-таблицы.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from src.core.config import get_settings
from src.services.audit_context import extract_client_ip


_settings = get_settings()


def _resolve_client_ip(request: Request) -> str:
    """Ключ для rate-limit'а с учётом X-Forwarded-For за trusted ingress.

    За k8s-ingress'ом `request.client.host` указывает на сам ingress — без
    этой отвязки все per-IP-лимиты сваливались бы в одну корзину
    (ingress = единый источник, лимиты выгорают на любом легитимном
    трафике). Логика выбора совпадает с `audit_context.extract_client_ip`:
    если direct IP в `trusted_proxy_ips` → берём левый non-trusted из XFF
    (или X-Real-IP), иначе доверяем только `request.client.host`.

    Fallback на `get_remote_address` нужен на случай, когда у request'а
    нет `.client` (тесты с TestClient без транспорта, ASGI lifespan-фейки).
    """
    ip = extract_client_ip(request)
    if ip:
        return ip
    return get_remote_address(request)


def per_account_key(request: Request) -> str:
    """Ключ для rate-limit'а IPMI/account-rotate'а: IP + path-param идентификатор.

    Чистый per-IP лимит пробивается, если злоумышленник с одного аккаунта
    бьёт ротации через несколько exit-IP — slowapi бакеты у разных IP
    независимы. Комбинация `IP:account_or_controller_id` склеивает все
    burst-попытки против одной целевой сущности в одну корзину, при этом
    разные аккаунты не делят лимит между собой.

    Поддерживаем оба распространённых имени path-параметра в server_service
    (`account_id` и `controller_id`), плюс fallback на `server_id`. Если
    ни одного нет — возвращаем чистый IP-ключ.
    """
    base = _resolve_client_ip(request)
    params = request.path_params
    target = (
        params.get("account_id")
        or params.get("controller_id")
        or params.get("server_id")
        or "unknown"
    )
    return f"{base}:{target}"


# `headers_enabled=True` оставлено для compat с прежним поведением
# (мы возвращаем кастомный 429 и сами ставим `Retry-After: 60`, остальные
# `X-RateLimit-*` опциональны и помогают отлаживать клиенты).
limiter = Limiter(
    key_func=_resolve_client_ip,
    default_limits=[_settings.global_rate_limit],
    headers_enabled=True,
)

# Отдельный limiter для endpoint-level декораторов (`@endpoint_limiter.limit(...)`).
# Headers выключены: иначе slowapi требует `response: Response` параметр у
# каждого декорированного handler'а и инжектит в него `X-RateLimit-*`. Нам
# headers ни на /os-versions/anon, ни на /ipmi/rotate не нужны — клиент
# получит наш стандартный `Retry-After: 60` через middleware-handler `_rate_limit_exceeded_response`.
endpoint_limiter = Limiter(
    key_func=_resolve_client_ip,
    default_limits=[],
    headers_enabled=False,
)
