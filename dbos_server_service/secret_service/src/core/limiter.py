"""SlowAPI Limiter, вынесен из `src.main` ради разрыва цикла импортов.

Endpoint-модули вешают `@limiter.limit(...)` декораторы на свои handler'ы
(reveal/transfer/recover/delete/create/dept-grants/acl). Если limiter жил бы
в `src.main`, эти модули импортировали бы `main` ещё до того, как тот
собрал `app`: endpoints/* подтягиваются через `src.api.router`, который
импортируется в момент сборки `app`. Получился бы циклический import —
нейтральный модуль закрывает эту проблему.

Limiter держит default-лимит из `settings.slowapi_rate_limit` (глобальный
spam-щит), endpoint-декораторы накручивают более жёсткие лимиты поверх —
slowapi применяет каждый лимит независимо (пробит хотя бы один → 429).

`app.state.limiter = limiter` выставляется в `create_application()` —
slowapi-декораторы ищут limiter именно там.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.core.config import get_settings


_settings = get_settings()


limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_settings.slowapi_rate_limit],
    headers_enabled=False,
)
