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
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import get_settings


_settings = get_settings()

# `headers_enabled=True` оставлено для compat с прежним поведением
# (мы возвращаем кастомный 429 и сами ставим `Retry-After: 60`, остальные
# `X-RateLimit-*` опциональны и помогают отлаживать клиенты).
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_settings.global_rate_limit],
    headers_enabled=True,
)

# Отдельный limiter для endpoint-level декораторов (`@endpoint_limiter.limit(...)`).
# Headers выключены: иначе slowapi требует `response: Response` параметр у
# каждого декорированного handler'а и инжектит в него `X-RateLimit-*`. Нам
# headers ни на /os-versions/anon, ни на /ipmi/rotate не нужны — клиент
# получит наш стандартный `Retry-After: 60` через middleware-handler `_rate_limit_exceeded_response`.
endpoint_limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    headers_enabled=False,
)
